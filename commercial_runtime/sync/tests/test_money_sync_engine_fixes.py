"""Money re-verifier (vYM2) findings in the sync engine, F3a.

One file, one section per finding.  Each section states which direction the
test proves; the ALLOW half of every guard is tested next to the DENY half.
Mutation proofs are recorded in the crew progress notes: the guard is broken,
the DENY test goes red, the guard is restored byte-exact.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import test_branch_alias_till_allocation_apply as T  # noqa: E402
import test_parked_children_apply as P  # noqa: E402
import test_registry_v9_apply as R  # noqa: E402
from commercial_runtime.sync.sync_service import (  # noqa: E402
    REGISTRY_SYNC_ENTITY_TYPES,
    SyncService,
)

install = P.install            # retail install fixture (init_retail + lazy credit schema)
registry_env = R.registry_env  # registry.db fixture
till_install = T.install       # same fixture family as P, for till_allocation tests


# ---------------------------------------------------------------- 1. duplicate reversal

def _reversal_ev(original_uid, uid=None):
    uid = uid or str(uuid.uuid4())
    return {
        "entity_type": "gl_journal", "event_type": "create", "entity_id": uid,
        "payload": {
            "uid": uid, "business_date": "2026-09-25", "reference": "VOID",
            "memo": None, "source_type": "reversal", "source_uid": original_uid,
            "branch_uid": None, "currency": "JOD", "reversed_journal_uid": original_uid,
            "posted_by": "System", "posted_at": "2026-09-25 10:00:00",
            "lines": [
                {"account_code": "1000", "debit": 0.0, "credit": 10.0, "memo": None},
                {"account_code": "4000", "debit": 10.0, "credit": 0.0, "memo": None},
            ],
            "accounts": [],
        },
    }


def _seed_original_journal(install):
    conn = install()
    try:
        from core.retail.general_ledger import ensure_default_chart
        ensure_default_chart(conn, P.CID)
        conn.commit()
        orig = P._journal_ev("sale", str(uuid.uuid4()))
        P._apply(P._svc(install), conn, orig)
        return orig["payload"]["uid"]
    finally:
        conn.close()


def _reversal_count(install, original_uid):
    conn = install()
    try:
        return conn.execute(
            "SELECT COUNT(*) n FROM gl_journals WHERE reversed_journal_id="
            "(SELECT id FROM gl_journals WHERE uid=?)", (original_uid,)).fetchone()["n"]
    finally:
        conn.close()


def _parked(install, entity_type=None):
    conn = install()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT entity_type, entity_id, event_type, reason FROM sync_apply_quarantine").fetchall()
                if entity_type is None or r["entity_type"] == entity_type]
    finally:
        conn.close()


def _apply(install, ev):
    conn = install()
    try:
        return P._apply(P._svc(install), conn, ev)
    finally:
        conn.close()


def test_second_reversal_of_the_same_journal_with_a_different_id_is_parked(install):
    """DENY half: two offline computers each voided the same payment; the second
    reversal (a different uid) must NOT land -- the money would go back twice."""
    orig = _seed_original_journal(install)
    first, second = _reversal_ev(orig), _reversal_ev(orig)
    assert _apply(install, first) is True
    _apply(install, second)
    assert _reversal_count(install, orig) == 1, "the journal was reversed twice"
    parked = _parked(install, "gl_journal")
    assert [p["reason"] for p in parked] == ["gl_duplicate_reversal"]
    assert parked[0]["entity_id"] == second["payload"]["uid"]


def test_first_reversal_applies_and_a_replay_of_the_same_uid_is_a_no_op(install):
    """ALLOW half: the first reversal lands, and the very same event again (a
    replayed batch, a hub echo) is idempotent -- neither parked nor doubled."""
    orig = _seed_original_journal(install)
    rev = _reversal_ev(orig)
    assert _apply(install, rev) is True
    assert _reversal_count(install, orig) == 1
    assert _apply(install, rev) is True
    assert _reversal_count(install, orig) == 1
    assert _parked(install, "gl_journal") == []
    conn = install()
    try:
        n = conn.execute("SELECT COUNT(*) n FROM gl_journal_lines WHERE journal_id="
                         "(SELECT id FROM gl_journals WHERE uid=?)",
                         (rev["payload"]["uid"],)).fetchone()["n"]
    finally:
        conn.close()
    assert n == 2, "replay doubled the lines"


def test_reversals_of_two_different_journals_both_apply(install):
    """ALLOW half: the guard is per reversed journal, not per company."""
    a = _seed_original_journal(install)
    conn = install()
    try:
        other = P._journal_ev("sale", str(uuid.uuid4()))
        P._apply(P._svc(install), conn, other)
    finally:
        conn.close()
    b = other["payload"]["uid"]
    assert _apply(install, _reversal_ev(a)) is True
    assert _apply(install, _reversal_ev(b)) is True
    assert _reversal_count(install, a) == 1 and _reversal_count(install, b) == 1
    assert _parked(install, "gl_journal") == []


# ---------------------------------------------------------------- 2. a synced void moves the account balance once

def _party(install, table, balance):
    # customers/suppliers ids are TEXT uuids in the migrated schema.
    pid = str(uuid.uuid4())
    conn = install()
    try:
        conn.execute(f"INSERT INTO {table} (id, company_id, name, credit_balance) VALUES (?,?,?,?)",
                     (pid, P.CID, "Party", balance))
        conn.commit()
        return pid
    finally:
        conn.close()


def _balance(install, table, pid):
    conn = install()
    try:
        return conn.execute(f"SELECT credit_balance b FROM {table} WHERE id=?", (pid,)).fetchone()["b"]
    finally:
        conn.close()


def _payment_ev(party_type, party_id, direction, amount, related_type=None, uid=None):
    uid = uid or str(uuid.uuid4())
    return {"entity_type": "payment", "event_type": "create", "entity_id": uid,
            "payload": {"uid": uid, "reference": "RCPT-" + uid[:6], "party_type": party_type,
                        "party_id": party_id, "direction": direction, "amount": amount,
                        "currency": "JOD", "method": "cash", "related_type": related_type,
                        "related_id": None, "sale_uid": None, "notes": "", "status": "active",
                        "created_by": "u", "device": None, "created_at": "2026-09-25 09:00:00"}}


def _void_ev(uid):
    return {"entity_type": "payment", "event_type": "update", "entity_id": uid,
            "payload": {"uid": uid, "status": "voided"}}


def test_synced_void_of_a_customer_payment_puts_the_amount_back_once(install):
    """The origin computer took a 30 payment (balance 80 -> 50). A peer's void
    arrives: 50 -> 80. Delivered twice, still 80."""
    cust = _party(install, "customers", 50.0)
    pay = _payment_ev("customer", cust, "in", 30.0)
    assert _apply(install, pay) is True
    assert _balance(install, "customers", cust) == 50.0, "creating the payment row must not move it"
    assert _apply(install, _void_ev(pay["payload"]["uid"])) is True
    assert _balance(install, "customers", cust) == 80.0
    assert _apply(install, _void_ev(pay["payload"]["uid"])) is True
    assert _balance(install, "customers", cust) == 80.0, "a second delivery moved the balance again"


def test_synced_void_of_a_supplier_payment_puts_the_amount_back(install):
    """Supplier side: paying a supplier out lowered what we owe them, the void
    restores it (same arithmetic as the local route)."""
    sup = _party(install, "suppliers", 70.0)
    pay = _payment_ev("supplier", sup, "out", 30.0)
    assert _apply(install, pay) is True
    assert _apply(install, _void_ev(pay["payload"]["uid"])) is True
    assert _balance(install, "suppliers", sup) == 100.0


def test_synced_void_keeps_the_currency_precision(install):
    """JOD has three decimal places: 12.345 must come back as 12.345."""
    cust = _party(install, "customers", 0.0)
    pay = _payment_ev("customer", cust, "in", 12.345)
    _apply(install, pay)
    _apply(install, _void_ev(pay["payload"]["uid"]))
    assert _balance(install, "customers", cust) == 12.345


def test_a_payment_already_voided_here_is_not_adjusted_again(install):
    """ALLOW/no-op half: this computer voided it itself (status already voided,
    balance already corrected by the route). The peer's void event for the same
    payment must not move the balance a second time."""
    cust = _party(install, "customers", 50.0)
    pay = _payment_ev("customer", cust, "in", 30.0)
    _apply(install, pay)
    conn = install()
    try:
        conn.execute("UPDATE payments SET status='voided' WHERE uid=?", (pay["payload"]["uid"],))
        conn.execute("UPDATE customers SET credit_balance=80 WHERE id=?", (cust,))
        conn.commit()
    finally:
        conn.close()
    assert _apply(install, _void_ev(pay["payload"]["uid"])) is True
    assert _balance(install, "customers", cust) == 80.0
    assert _parked(install, "payment") == []


def test_a_void_for_a_cheque_tied_payment_flips_status_but_moves_no_balance(install):
    """The route refuses to void a sale/cheque-tied receipt, so a void event for
    one carries no balance change; the status flip is unchanged."""
    cust = _party(install, "customers", 10.0)
    pay = _payment_ev("customer", cust, "in", 5.0, related_type="cheque")
    _apply(install, pay)
    _apply(install, _void_ev(pay["payload"]["uid"]))
    assert _balance(install, "customers", cust) == 10.0
    conn = install()
    try:
        st = conn.execute("SELECT status FROM payments WHERE uid=?", (pay["payload"]["uid"],)).fetchone()["status"]
    finally:
        conn.close()
    assert st == "voided"


def test_a_void_before_its_payment_still_parks_missing_parent(install):
    _apply(install, _void_ev(str(uuid.uuid4())))
    assert [p["reason"] for p in _parked(install, "payment")] == ["missing_parent:payment"]


def test_a_void_for_a_party_that_has_not_arrived_is_a_quiet_no_op(install):
    pay = _payment_ev("customer", 987654, "in", 30.0)
    _apply(install, pay)
    assert _apply(install, _void_ev(pay["payload"]["uid"])) is True
    assert _parked(install, "payment") == []


# ---------------------------------------------------------------- 3. malformed user create parks, never wedges

def _reg_svc(get_conn):
    return SyncService(client_factory=lambda: None, get_conn=get_conn,
                       local_company_id_provider=lambda: R.COMPANY,
                       handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES)


def _pull_users(registry_env, events, cursor=3):
    conn = registry_env()
    try:
        _reg_svc(registry_env).apply_pull_result(conn, {"events": events, "cursor": cursor})
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("over", [{"password_hash": None}, {"employee_id": None}, {"email": None}])
def test_new_user_with_a_null_required_field_parks_and_the_stream_keeps_going(registry_env, over):
    """DENY half: [bad, good] -- the good user behind it applies (row_version 2)
    and there is exactly ONE parked row, labelled malformed_user (a null email
    must not be called a duplicate_email)."""
    good_uid, _ = R._seed_user(registry_env)
    bad_uid = str(uuid.uuid4())
    fields = {"email": f"{bad_uid[:6]}@x.com", "employee_id": "E-" + bad_uid[:4]}
    fields.update(over)
    bad = R._user_ev(bad_uid, **fields)
    bad["event_type"] = "create"
    _pull_users(registry_env, [bad, R._user_ev(good_uid)])
    rv = R._q(registry_env, "SELECT row_version FROM users WHERE uid=?", (good_uid,))[0]["row_version"]
    assert rv == 2, "the user behind the malformed one never applied"
    q = R._q(registry_env, "SELECT entity_type, reason, detail FROM sync_apply_quarantine")
    assert [(r["entity_type"], r["reason"]) for r in q] == [("user", "malformed_user")]
    assert "H2" not in (q[0]["detail"] or ""), "a credential hash leaked into the parked detail"
    assert R._q(registry_env, "SELECT 1 FROM users WHERE uid=?", (bad_uid,)) == []


def test_a_well_formed_new_user_still_applies(registry_env):
    """ALLOW half: the malformed check does not swallow ordinary creates."""
    uid = str(uuid.uuid4())
    ev = R._user_ev(uid, email="new@x.com", employee_id="E-NEW")
    ev["event_type"] = "create"
    _pull_users(registry_env, [ev])
    assert R._q(registry_env, "SELECT email FROM users WHERE uid=?", (uid,))[0]["email"] == "new@x.com"
    assert R._q(registry_env, "SELECT 1 FROM sync_apply_quarantine") == []


def test_a_real_duplicate_email_is_still_parked_as_duplicate_email(registry_env):
    """ALLOW half of the label fix: a genuine UNIQUE(email) collision keeps its
    own reason -- only the NOT NULL case moved to malformed_user."""
    R._seed_user(registry_env, email="dup@x.com", employee_id="E-A")
    uid = str(uuid.uuid4())
    ev = R._user_ev(uid, email="dup@x.com", employee_id="E-B")
    ev["event_type"] = "create"
    _pull_users(registry_env, [ev])
    q = R._q(registry_env, "SELECT reason FROM sync_apply_quarantine")
    assert [r["reason"] for r in q] == ["duplicate_email"]


# ---------------------------------------------------------------- 4. till_allocation status only moves forward

MAIN = "term-main"


def _statuses(till_install):
    return {k: v["status"] for k, v in T._tills(till_install).items()}


def test_an_update_cannot_bring_a_released_allocation_back_to_active(till_install):
    """DENY half: Y released, its number handed to X. A forged update naming Y
    with a huge row_version and status active must be parked, and X keeps
    the number."""
    T._set_main(MAIN, 1)
    assert T._apply(till_install, T._till_ev("Y", number=1, terminal="T1", epoch=1, by=MAIN,
                                            created="2026-09-27T10:00:00Z")) is True
    assert T._apply(till_install, T._till_ev("Y", number=1, terminal="T1", epoch=1, by=MAIN, rv=2,
                                            status="released", event="update",
                                            created="2026-09-27T10:00:00Z")) is True
    assert T._apply(till_install, T._till_ev("X", number=1, terminal="T3", epoch=1, by=MAIN,
                                            created="2026-09-28T10:00:00Z")) is True
    back = T._till_ev("Y", number=1, terminal="T1", epoch=1, by="anyone", rv=99, status="active",
                      event="update", created="2026-09-27T10:00:00Z")
    T._apply(till_install, back)
    assert _statuses(till_install) == {"X": "active", "Y": "released"}
    assert T._quarantined(till_install, "till_allocation") == [{"reason": "till_allocation_status_backwards"}]
    assert T._tills(till_install)["Y"]["row_version"] == 2, "the parked update still moved the row"


def test_an_update_cannot_bring_a_superseded_allocation_back_to_active(till_install):
    """DENY half, superseded: two active claims on one number, the loser is
    stored superseded. An update claiming active for the loser is parked."""
    T._set_main(MAIN, 1)
    T._apply(till_install, T._till_ev("A1", number=1, terminal="T1", epoch=1, by=MAIN,
                                     created="2026-09-27T10:00:00Z"))
    T._apply(till_install, T._till_ev("A2", number=1, terminal="T2", epoch=1, by=MAIN,
                                     created="2026-09-28T10:00:00Z"))
    st = _statuses(till_install)
    assert sorted(st.values()) == ["active", "superseded"], st
    loser = next(k for k, v in st.items() if v == "superseded")
    T._apply(till_install, T._till_ev(loser, number=1, terminal="T1" if loser == "A1" else "T2",
                                     epoch=1, by=MAIN, rv=99, status="active", event="update",
                                     created="2026-09-27T10:00:00Z"))
    assert _statuses(till_install) == st
    assert T._quarantined(till_install, "till_allocation") == [{"reason": "till_allocation_status_backwards"}]


def test_forward_updates_still_apply(till_install):
    """ALLOW half: active -> released applies; an active row's own newer
    active update (no status change) applies; a stale replay of an old active
    update onto a released row is the silent no-op it always was (not parked)."""
    T._set_main(MAIN, 1)
    base = dict(number=1, terminal="T1", epoch=1, by=MAIN, created="2026-09-27T10:00:00Z")
    T._apply(till_install, T._till_ev("Y", **base))
    assert T._apply(till_install, T._till_ev("Y", rv=2, status="active", event="update", **base)) is True
    assert T._tills(till_install)["Y"]["row_version"] == 2
    assert T._apply(till_install, T._till_ev("Y", rv=3, status="released", event="update", **base)) is True
    assert _statuses(till_install) == {"Y": "released"}
    assert T._apply(till_install, T._till_ev("Y", rv=2, status="active", event="update", **base)) is True
    assert _statuses(till_install) == {"Y": "released"}
    assert T._quarantined(till_install, "till_allocation") == []


# ---------------------------------------------------------------- 5. odd event shapes never wedge a batch

SUR = "\udc80"  # what json.loads makes of the JSON text "\udc80"


def _branch_ev(uid, name="X", eid=None):
    return {"entity_type": "branch", "event_type": "create", "entity_id": uid if eid is None else eid,
            "payload": {"uid": uid, "name": name, "address": "", "phone": "", "status": "active"}}


def _good_branch():
    u = str(uuid.uuid4())
    return u, _branch_ev(u, name="After Odd")


_ODD = {
    "entity_type_list": lambda: {"entity_type": ["branch"], "event_type": "create", "entity_id": "e1",
                                 "payload": {}},
    "entity_type_dict": lambda: {"entity_type": {"a": 1}, "event_type": "create", "entity_id": "e2",
                                 "payload": {}},
    "event_type_list": lambda: {"entity_type": "branch", "event_type": ["create"], "entity_id": "e3",
                                "payload": {"uid": "e3", "name": "n"}},
    "surrogate_entity_id_and_uid": lambda: _branch_ev("b-" + SUR),
    "surrogate_uid_only": lambda: _branch_ev("b-" + SUR, eid="plain-id"),
    "surrogate_name": lambda: _branch_ev(str(uuid.uuid4()), name="N" + SUR),
    "surrogate_till_id": lambda: T._till_ev(alloc_id="t" + SUR),
    "surrogate_setting_key": lambda: {"entity_type": "retail_setting", "event_type": "create",
                                      "entity_id": "k" + SUR, "payload": {"skey": "k" + SUR, "svalue": "v"}},
    "surrogate_entity_type": lambda: {"entity_type": "branch" + SUR, "event_type": "create",
                                      "entity_id": "e4", "payload": {}},
    "event_none": lambda: None,
}


@pytest.mark.parametrize("case", sorted(_ODD))
def test_an_odd_event_never_wedges_the_batch(till_install, case):
    """DENY half: a hostile / broken event followed by a good branch. The batch
    must not raise (a raise rolls the whole pull back and the cursor never
    moves) and the good branch must land. The hub transport already rejects
    these shapes; this pins the engine's own defence in depth."""
    good_uid, good = _good_branch()
    conn = till_install()
    try:
        T._svc(till_install).apply_pull_result(conn, {"events": [_ODD[case](), good], "cursor": 7})
        conn.commit()
        landed = conn.execute("SELECT COUNT(*) n FROM branches WHERE uid=?", (good_uid,)).fetchone()["n"]
    finally:
        conn.close()
    assert landed == 1


def test_a_surrogate_entity_id_is_parked_as_a_readable_escaped_row(till_install):
    """The parked row must exist and be readable, not just 'nothing raised':
    the operator has to be able to see WHAT was set aside."""
    good_uid, good = _good_branch()
    conn = till_install()
    try:
        T._svc(till_install).apply_pull_result(
            conn, {"events": [_branch_ev("b-" + SUR), good], "cursor": 7})
        conn.commit()
        rows = [dict(r) for r in conn.execute(
            "SELECT entity_id, reason FROM sync_apply_quarantine WHERE entity_type='branch'")]
    finally:
        conn.close()
    assert len(rows) == 1, rows
    assert rows[0]["entity_id"] == "b-\\udc80"
    assert rows[0]["reason"] == "malformed_branch"


def test_the_reason_and_detail_columns_survive_a_surrogate_too(till_install):
    """The guard builds `reason` from the event type and `detail` from an
    exception message; either can quote a raw surrogate. Called directly,
    because no handled entity type carries a surrogate in its name."""
    conn = till_install()
    try:
        SyncService._quarantine_apply_event(
            conn, {"entity_type": "branch", "event_type": "create", "entity_id": "d1", "payload": {}},
            reason="malformed_" + SUR, detail="ValueError: bad value 'x" + SUR + "'")
        conn.commit()
        row = dict(conn.execute(
            "SELECT reason, detail FROM sync_apply_quarantine WHERE entity_id='d1'").fetchone())
    finally:
        conn.close()
    assert row["reason"] == "malformed_\\udc80"
    assert row["detail"] == "ValueError: bad value 'x\\udc80'"


def test_a_normal_quarantine_row_and_a_normal_event_are_unchanged(till_install):
    """ALLOW half: escaping touches only strings a database cannot hold. A
    plain (and a non-ASCII but valid) id is stored byte-for-byte, and a
    normal event still applies."""
    good_uid, good = _good_branch()
    T._set_main(MAIN, 1)
    # valid Arabic id from a device that is not the main computer -> parks
    ev = T._till_ev(alloc_id="مس-1", by="not-the-main", epoch=1)
    conn = till_install()
    try:
        T._svc(till_install).apply_pull_result(conn, {"events": [ev, good], "cursor": 7})
        conn.commit()
        parked = [r["entity_id"] for r in conn.execute(
            "SELECT entity_id FROM sync_apply_quarantine WHERE entity_type='till_allocation'")]
        landed = conn.execute("SELECT COUNT(*) n FROM branches WHERE uid=?", (good_uid,)).fetchone()["n"]
    finally:
        conn.close()
    assert parked == ["مس-1"]
    assert landed == 1


# AUDIT-033 / S-AUTH: authority events are refused unless signed by someone
# allowed to send them. This file's assertions are about the APPLIERS, not about
# who sent the event, so each event is signed by a legit author right before it
# is applied (see _signing.install_legit_author_signing for exactly what that
# keeps and what it can no longer catch). Authorship itself is tested, with the
# wrong people signing on purpose, in test_sync_authority*.py.
@pytest.fixture(autouse=True)
def _legit_author_signing(tmp_path, monkeypatch):
    from commercial_runtime.sync.tests._signing import install_legit_author_signing
    return install_legit_author_signing(monkeypatch, tmp_path)
