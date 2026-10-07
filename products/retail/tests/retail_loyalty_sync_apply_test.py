"""W2a (schema v48) on the sync APPLY side: loyalty points shared between computers.

Before W2a a `loyalty_ledger` row never left the computer that wrote it: points
earned on A did not exist on B, a return on B of A's sale found no earn/redeem
rows to claw back, `sale_id` / `return_id` were local integers, and each
computer's own v27 'opening' row for one customer would double the balance the
moment rows synced. This file pins what the RECEIVING side must do with

  loyalty_entry  create  {uid, customer_id, points_delta, entry_type, sale_uid,
                          return_uid, created_by, created_at}

  * insert ONE immutable fact per uid, under the RECEIVER's company, with the
    RECEIVER's own local sale_id / return_id;
  * park -- never raise, never drop -- an entry whose customer / sale / return
    has not arrived, and apply it when it does;
  * keep `customers.loyalty_points` equal to SUM(points_delta), recomputed
    (never incremented), so a replayed row cannot double it;
  * collapse the two computers' pre-v48 'opening' rows to the SMALLER value and
    say so once; flag an overspent redemption once, but not a claw-back;
  * NEVER post books and NEVER queue anything: a receiver must not re-send what
    it just pulled, and the 5000 expense already travels as a `gl_journal`.
  * the sale apply stores `points_earned`.

Same fixture technique as retail_transfer_sync_apply_test.py: a REAL
`init_retail()` database, `SyncService._apply_event` driven directly. One file
per process.

Run:  <venv python> -m pytest -q -p no:cacheprovider -p no:warnings products/retail/tests/retail_loyalty_sync_apply_test.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

for _k in ("AURA_SYNC_RELAY_URL", "AURA_OWNER_LICENSING_URL"):
    os.environ.pop(_k, None)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RETAIL_BACKEND_DIR = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_RETAIL_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import database.schema as schema  # noqa: E402
import api.retail_api as _retail_api_module  # noqa: E402
from api.retail_api import _ensure_credit_schema  # noqa: E402
from commercial_runtime.identity import registry_db  # noqa: E402
from commercial_runtime.sync import sync_service as ss  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402

CID = "company-loyalty-apply-test"
CUST = str(uuid.uuid4())
CUST2 = str(uuid.uuid4())
SALE_UID = str(uuid.uuid4())
RET_UID = str(uuid.uuid4())
T1 = "2026-10-07 09:00:00"
T2 = "2026-10-07 10:00:00"
T3 = "2026-10-07 11:00:00"


@pytest.fixture
def get_conn(tmp_path, monkeypatch):
    reg_dir = tmp_path / "registry_db"
    monkeypatch.setattr(registry_db, "_db_dir", str(reg_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(reg_dir / "registry.db"))
    registry_db.init_registry_db()
    root = tmp_path / "install"
    subsys = root / "subsystems"
    subsys.mkdir(parents=True, exist_ok=True)
    original = (schema.BASE_DIR, schema.SUBSYS_DIR)
    schema.BASE_DIR, schema.SUBSYS_DIR = str(root), str(subsys)
    try:
        schema.init_retail()
    finally:
        schema.BASE_DIR, schema.SUBSYS_DIR = original
    db_path = subsys / "retail.db"

    def _get():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    previous = _retail_api_module._CREDIT_SCHEMA_READY
    _retail_api_module._CREDIT_SCHEMA_READY = False
    try:
        c = _get()
        try:
            _ensure_credit_schema(c)
            for cid in (CUST, CUST2):
                c.execute("INSERT INTO customers (id, company_id, name, loyalty_points) "
                          "VALUES (?,?,?,0)", (cid, CID, "Customer " + cid[:4]))
            c.commit()
        finally:
            c.close()
    finally:
        _retail_api_module._CREDIT_SCHEMA_READY = previous
    return _get


def _svc(get_conn):
    return SyncService(client_factory=lambda: None, get_conn=get_conn,
                       local_company_id_provider=lambda: CID)


def _apply(get_conn, ev, sink=None):
    conn = get_conn()
    try:
        ok = _svc(get_conn)._apply_event(conn, ev, local_company_id=CID, conflict_sink=sink)
        conn.commit()
        return ok
    finally:
        conn.close()


def _retry(get_conn):
    conn = get_conn()
    try:
        _svc(get_conn)._retry_quarantined_events(conn, local_company_id=CID)
        conn.commit()
    finally:
        conn.close()


def _rows(get_conn, sql, params=()):
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _exec(get_conn, sql, params=()):
    conn = get_conn()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _entry(uid=None, customer_id=CUST, points_delta=10.0, entry_type="earn",
           sale_uid=None, return_uid=None, created_at=T1, **over):
    uid = uid or str(uuid.uuid4())
    p = {"uid": uid, "customer_id": customer_id, "points_delta": points_delta,
         "entry_type": entry_type, "sale_uid": sale_uid, "return_uid": return_uid,
         "created_by": "alice", "created_at": created_at,
         # A hostile/buggy sender's tenant key must never be trusted.
         "company_id": "the-senders-company"}
    p.update(over)
    return {"entity_type": "loyalty_entry", "event_type": "create", "entity_id": uid, "payload": p}


def _add_sale(get_conn, uid=SALE_UID):
    _exec(get_conn, "INSERT INTO sales (company_id, uid, sale_number, total) VALUES (?,?,?,?)",
          (CID, uid, "S-" + uid[:6], 100))
    return _rows(get_conn, "SELECT id FROM sales WHERE uid=?", (uid,))[0]["id"]


def _add_return(get_conn, sale_local_id, uid=RET_UID):
    _exec(get_conn, "INSERT INTO returns (company_id, uid, sale_id) VALUES (?,?,?)",
          (CID, uid, sale_local_id))
    return _rows(get_conn, "SELECT id FROM returns WHERE uid=?", (uid,))[0]["id"]


def _ledger(get_conn, customer_id=CUST):
    return _rows(get_conn, "SELECT * FROM loyalty_ledger WHERE customer_id=? ORDER BY id",
                 (customer_id,))


def _points(get_conn, customer_id=CUST):
    return _rows(get_conn, "SELECT loyalty_points FROM customers WHERE id=?",
                 (customer_id,))[0]["loyalty_points"]


def _sum(get_conn, customer_id=CUST):
    return _rows(get_conn, "SELECT COALESCE(SUM(points_delta),0) AS s FROM loyalty_ledger "
                           "WHERE customer_id=?", (customer_id,))[0]["s"]


def _conflicts(get_conn, customer_id=CUST):
    return sorted(json.loads(r["incoming_payload"])["reason"] for r in _rows(
        get_conn, "SELECT incoming_payload FROM sync_conflicts WHERE entity_type='loyalty_entry' "
                  "AND entity_id=?", (customer_id,)))


def _parked(get_conn):
    return {(r["entity_type"], r["event_type"], r["reason"]) for r in _rows(
        get_conn, "SELECT entity_type, event_type, reason FROM sync_apply_quarantine")}


def _outbox_count(get_conn):
    return _rows(get_conn, "SELECT COUNT(*) AS n FROM sync_outbox")[0]["n"]


# ── the type is handled ──────────────────────────────────────────────────────

def test_loyalty_entry_is_a_retail_sync_entity_type_that_carries_a_company():
    assert "loyalty_entry" in ss.RETAIL_SYNC_ENTITY_TYPES
    # The receiver stamps ITS OWN company: the type must not be on the list of
    # company-less children, or the eager company fetch would skip it.
    assert "loyalty_entry" not in ss._ENTITY_TYPES_WITHOUT_COMPANY_ID


# ── apply ────────────────────────────────────────────────────────────────────

def test_entry_inserts_under_the_receivers_company_with_local_sale_id(get_conn):
    sale_local = _add_sale(get_conn)
    ev = _entry(entry_type="earn", points_delta=12.0, sale_uid=SALE_UID)
    assert _apply(get_conn, ev) is True
    rows = _ledger(get_conn)
    assert len(rows) == 1
    r = rows[0]
    assert r["uid"] == ev["entity_id"]
    assert str(r["company_id"]) == CID          # never the payload's company
    assert r["sale_id"] == sale_local           # the RECEIVER's integer
    assert r["return_id"] is None
    assert r["points_delta"] == 12.0 and r["entry_type"] == "earn"
    assert r["created_by"] == "alice" and r["created_at"] == T1
    assert r["sync_backfill_pending"] == 0      # a receiver never re-queues a pulled row


def test_return_uid_maps_to_the_receivers_local_return_id(get_conn):
    sale_local = _add_sale(get_conn)
    ret_local = _add_return(get_conn, sale_local)
    ev = _entry(entry_type="adjust", points_delta=-12.0, sale_uid=SALE_UID, return_uid=RET_UID)
    assert _apply(get_conn, ev) is True
    r = _ledger(get_conn)[0]
    assert r["sale_id"] == sale_local and r["return_id"] == ret_local


def test_entry_without_references_applies(get_conn):
    assert _apply(get_conn, _entry(entry_type="adjust", points_delta=5.0)) is True
    r = _ledger(get_conn)[0]
    assert r["sale_id"] is None and r["return_id"] is None


def test_apply_queues_nothing_and_posts_no_books(get_conn):
    _add_sale(get_conn)
    before_outbox = _outbox_count(get_conn)
    before_gl = _rows(get_conn, "SELECT COUNT(*) AS n FROM gl_journals")[0]["n"]
    _apply(get_conn, _entry(entry_type="redeem", points_delta=-4.0, sale_uid=SALE_UID))
    _apply(get_conn, _entry(entry_type="earn", points_delta=9.0, sale_uid=SALE_UID))
    assert _outbox_count(get_conn) == before_outbox
    assert _rows(get_conn, "SELECT COUNT(*) AS n FROM gl_journals")[0]["n"] == before_gl


def test_cache_is_recomputed_from_the_sum_and_the_customer_row_is_untouched(get_conn):
    before = _rows(get_conn, "SELECT row_version FROM customers WHERE id=?", (CUST,))[0]
    _apply(get_conn, _entry(points_delta=10.0, created_at=T1))
    _apply(get_conn, _entry(points_delta=-3.0, entry_type="redeem", created_at=T2))
    assert _sum(get_conn) == 7.0
    assert _points(get_conn) == 7.0
    after = _rows(get_conn, "SELECT row_version FROM customers WHERE id=?", (CUST,))[0]
    # The cache is DERIVED: never bump row_version for it, never queue a customer event.
    assert after["row_version"] == before["row_version"]
    assert _rows(get_conn, "SELECT COUNT(*) AS n FROM sync_outbox "
                           "WHERE entity_type='customer'")[0]["n"] == 0


def test_replayed_loyalty_rows_do_not_double_points(get_conn):
    ev = _entry(points_delta=10.0)
    for _ in range(3):
        _apply(get_conn, ev)
    assert len(_ledger(get_conn)) == 1
    assert _points(get_conn) == 10.0
    # A stale cache (an older build's raw increment) is healed by a replay, not doubled.
    _exec(get_conn, "UPDATE customers SET loyalty_points=999 WHERE id=?", (CUST,))
    _apply(get_conn, ev)
    assert _points(get_conn) == 10.0


def test_entry_for_another_companys_customer_is_parked_not_attached(get_conn):
    other = str(uuid.uuid4())
    _exec(get_conn, "INSERT INTO customers (id, company_id, name, loyalty_points) VALUES (?,?,?,0)",
          (other, "some-other-company", "Stranger"))
    assert _apply(get_conn, _entry(customer_id=other)) is False
    assert _rows(get_conn, "SELECT COUNT(*) AS n FROM loyalty_ledger WHERE customer_id=?",
                 (other,))[0]["n"] == 0
    assert ("loyalty_entry", "create", "missing_parent:customer") in _parked(get_conn)


# ── parking and replay ───────────────────────────────────────────────────────

def test_missing_customer_is_parked_then_applies_when_the_customer_arrives(get_conn):
    late = str(uuid.uuid4())
    ev = _entry(customer_id=late, points_delta=8.0)
    assert _apply(get_conn, ev) is False
    assert ("loyalty_entry", "create", "missing_parent:customer") in _parked(get_conn)
    assert _rows(get_conn, "SELECT COUNT(*) AS n FROM loyalty_ledger WHERE customer_id=?",
                 (late,))[0]["n"] == 0
    _exec(get_conn, "INSERT INTO customers (id, company_id, name, loyalty_points) VALUES (?,?,?,0)",
          (late, CID, "Late"))
    _retry(get_conn)
    assert len(_ledger(get_conn, late)) == 1
    assert _points(get_conn, late) == 8.0
    assert ("loyalty_entry", "create", "missing_parent:customer") not in _parked(get_conn)


def test_missing_sale_is_parked_then_applies_when_the_sale_arrives(get_conn):
    ev = _entry(entry_type="earn", points_delta=5.0, sale_uid=SALE_UID)
    assert _apply(get_conn, ev) is False
    assert ("loyalty_entry", "create", "missing_parent:sale") in _parked(get_conn)
    assert _ledger(get_conn) == []
    assert _points(get_conn) == 0
    sale_local = _add_sale(get_conn)
    _retry(get_conn)
    rows = _ledger(get_conn)
    assert len(rows) == 1 and rows[0]["sale_id"] == sale_local
    assert _points(get_conn) == 5.0


def test_missing_return_is_parked_then_applies_when_the_return_arrives(get_conn):
    sale_local = _add_sale(get_conn)
    ev = _entry(entry_type="adjust", points_delta=-5.0, sale_uid=SALE_UID, return_uid=RET_UID)
    assert _apply(get_conn, ev) is False
    assert ("loyalty_entry", "create", "missing_parent:return") in _parked(get_conn)
    assert _ledger(get_conn) == []
    ret_local = _add_return(get_conn, sale_local)
    _retry(get_conn)
    rows = _ledger(get_conn)
    assert len(rows) == 1 and rows[0]["return_id"] == ret_local


# ── the 'opening' row ────────────────────────────────────────────────────────

def _opening_uid(customer_id=CUST):
    return SyncService._loyalty_opening_uid(customer_id)


def test_opening_uid_is_a_fixed_uuid5_of_the_customer():
    # Pinned literally so the namespace can never drift: two computers that
    # both carried the same customer must derive the SAME uid or the opening
    # balance would be counted twice. (The twin test below pins it to core.)
    assert str(ss._LOYALTY_OPENING_NAMESPACE) == _EXPECTED_NAMESPACE
    assert _opening_uid("c-1") == str(uuid.uuid5(ss._LOYALTY_OPENING_NAMESPACE, "c-1:opening"))


_EXPECTED_NAMESPACE = "b3a7d1c4-5e92-4f08-9a61-0c2d8e4f7b13"


def test_opening_the_smaller_value_wins_whichever_arrives_second(get_conn):
    ouid = _opening_uid()
    # Local opening is the LARGER; the incoming smaller one replaces it.
    _exec(get_conn, "INSERT INTO loyalty_ledger (uid, company_id, customer_id, points_delta, "
                    "entry_type) VALUES (?,?,?,?,'opening')", (ouid, CID, CUST, 100.0))
    _apply(get_conn, _entry(uid=ouid, entry_type="opening", points_delta=60.0))
    rows = _ledger(get_conn)
    assert len(rows) == 1 and rows[0]["points_delta"] == 60.0
    assert _points(get_conn) == 60.0
    # An incoming LARGER one leaves the smaller local row alone.
    _apply(get_conn, _entry(uid=ouid, entry_type="opening", points_delta=90.0))
    assert _ledger(get_conn)[0]["points_delta"] == 60.0
    assert _points(get_conn) == 60.0


def test_opening_difference_is_recorded_once_per_customer(get_conn):
    ouid = _opening_uid()
    _exec(get_conn, "INSERT INTO loyalty_ledger (uid, company_id, customer_id, points_delta, "
                    "entry_type) VALUES (?,?,?,?,'opening')", (ouid, CID, CUST, 100.0))
    for delta in (60.0, 90.0, 60.0, 70.0):
        _apply(get_conn, _entry(uid=ouid, entry_type="opening", points_delta=delta))
    assert _conflicts(get_conn) == ["loyalty_opening_differs"]


def test_opening_that_agrees_but_comes_from_another_computer_still_asks_a_person(get_conn):
    # W2a verify F3: this used to assert "records nothing" -- which is only right
    # if both computers were counting the SAME points, and nothing on the row
    # says so. It can no longer catch "equal separate balances collapse
    # silently"; that silence IS the defect, so it is now the thing asserted
    # against. The values agree, the balance is unchanged, and a person is asked.
    ouid = _opening_uid()
    _exec(get_conn, "INSERT INTO loyalty_ledger (uid, company_id, customer_id, points_delta, "
                    "entry_type, created_by, created_at) VALUES (?,?,?,?,'opening','alice',?)",
          (ouid, CID, CUST, 40.0, T2))
    _apply(get_conn, _entry(uid=ouid, entry_type="opening", points_delta=40.0, created_at=T1))
    assert _conflicts(get_conn) == ["loyalty_opening_differs"]
    rows = _ledger(get_conn)
    assert len(rows) == 1 and rows[0]["points_delta"] == 40.0 and _points(get_conn) == 40.0
    # Both computers must end holding the SAME row: the earlier created_at wins.
    assert rows[0]["created_at"] == T1


def test_the_opening_rows_own_event_coming_back_records_nothing(get_conn):
    # The relay hands a computer its own events; a replay re-delivers one. The
    # same delta, created_at and created_by IS the local row, not a second
    # computer's, and must not ask a person about it.
    ouid = _opening_uid()
    _exec(get_conn, "INSERT INTO loyalty_ledger (uid, company_id, customer_id, points_delta, "
                    "entry_type, created_by, created_at) VALUES (?,?,?,?,'opening','alice',?)",
          (ouid, CID, CUST, 40.0, T1))
    _apply(get_conn, _entry(uid=ouid, entry_type="opening", points_delta=40.0, created_at=T1))
    assert _conflicts(get_conn) == []
    assert len(_ledger(get_conn)) == 1


def test_opening_arriving_first_then_locally_larger_converges_to_smaller(get_conn):
    # Computer B holds 40, computer A's 100 arrives: B keeps 40 (and A, applying
    # B's 40, drops to 40) -- both land on the same number.
    ouid = _opening_uid()
    _apply(get_conn, _entry(uid=ouid, entry_type="opening", points_delta=40.0))
    _apply(get_conn, _entry(uid=ouid, entry_type="opening", points_delta=100.0))
    assert [r["points_delta"] for r in _ledger(get_conn)] == [40.0]
    assert _points(get_conn) == 40.0


# ── overspent ────────────────────────────────────────────────────────────────

def test_overspent_redemption_is_flagged_once(get_conn):
    _apply(get_conn, _entry(points_delta=10.0, entry_type="earn", created_at=T1))
    _apply(get_conn, _entry(points_delta=-8.0, entry_type="redeem", created_at=T2))
    assert _conflicts(get_conn) == []                    # 2 left: fine
    _apply(get_conn, _entry(points_delta=-5.0, entry_type="redeem", created_at=T3))
    assert _sum(get_conn) == -3.0 and _points(get_conn) == -3.0
    assert _conflicts(get_conn) == ["loyalty_overspent"]
    # Another row on the same negative customer, and a replay: still once.
    _apply(get_conn, _entry(points_delta=-1.0, entry_type="redeem", created_at="2026-10-07 12:00:00"))
    assert _conflicts(get_conn) == ["loyalty_overspent"]


def test_negative_from_a_claw_back_is_not_flagged(get_conn):
    _apply(get_conn, _entry(points_delta=10.0, entry_type="earn", created_at=T1))
    _apply(get_conn, _entry(points_delta=-6.0, entry_type="redeem", created_at=T2))
    # The return claws back the 10 earned: the customer is at -6 but the row
    # that crossed zero is an 'adjust', which the shop allows today.
    _apply(get_conn, _entry(points_delta=-10.0, entry_type="adjust", created_at=T3))
    assert _sum(get_conn) == -6.0
    assert _conflicts(get_conn) == []


def test_overspent_is_judged_by_the_order_of_the_facts_not_their_arrival(get_conn):
    # The redeem is OLDER than the claw-back but arrives LAST. The claw-back is
    # what crossed zero in time order, so every computer must reach the same
    # (no-flag) answer however the rows arrive.
    _apply(get_conn, _entry(points_delta=10.0, entry_type="earn", created_at=T1))
    _apply(get_conn, _entry(points_delta=-10.0, entry_type="adjust", created_at=T3))
    _apply(get_conn, _entry(points_delta=-6.0, entry_type="redeem", created_at=T2))
    # Time order: +10, -6 (4), -10 (-6): the claw-back crossed zero.
    assert _sum(get_conn) == -6.0
    assert _conflicts(get_conn) == []


# ── W2a verify F2: the overspend verdict is a function of the rows held ─────

def _overspend_rows():
    """Probe `flag_order`: 100 points, then A redeems 100 (t2), B redeems 100
    (t3, offline), then A earns 200 (t4). In TIME order the second redeem takes
    the balance 0 -> -100: overspent, whatever order the rows arrive in."""
    u = {k: str(uuid.uuid5(uuid.NAMESPACE_DNS, "w2a-f2-" + k)) for k in "0abc"}
    return u, {
        "0": _entry(uid=u["0"], points_delta=100.0, entry_type="earn", created_at="2026-10-07 08:00:00"),
        "a": _entry(uid=u["a"], points_delta=-100.0, entry_type="redeem", created_at="2026-10-07 09:00:00"),
        "b": _entry(uid=u["b"], points_delta=-100.0, entry_type="redeem", created_at="2026-10-07 10:00:00"),
        "c": _entry(uid=u["c"], points_delta=200.0, entry_type="earn", created_at="2026-10-07 11:00:00"),
    }


@pytest.mark.parametrize("order", ["0abc", "0acb", "0cab", "c0ab", "b0ac", "cba0", "abc0"])
def test_overspend_verdict_is_the_same_whatever_order_the_rows_arrive_in(get_conn, order):
    uids, evs = _overspend_rows()
    for k in order:
        _apply(get_conn, evs[k])
    # Final balance 100-100-100+200 = 100; the verdict depends on the rows, not
    # on which computer saw the later earn before the second redeem.
    assert _sum(get_conn) == 100.0
    assert _conflicts(get_conn) == ["loyalty_overspent"], order
    payload = json.loads(_rows(get_conn, "SELECT incoming_payload FROM sync_conflicts")[0]["incoming_payload"])
    assert payload["redeem_uid"] == uids["b"], "the redeem that overdrew is the same one on every computer"


def test_a_second_overspend_after_recovery_is_recorded_again(get_conn):
    # W2a verify F4: sync_conflicts has no resolved column, so "once while
    # unresolved" is "once per overspend episode" -- one record per overdrawing
    # redeem row. A replay of the same row adds nothing.
    first = _entry(points_delta=-15.0, entry_type="redeem", created_at=T2)
    _apply(get_conn, _entry(points_delta=10.0, entry_type="earn", created_at=T1))
    _apply(get_conn, first)
    assert _conflicts(get_conn) == ["loyalty_overspent"]
    _apply(get_conn, _entry(points_delta=30.0, entry_type="earn", created_at=T3))     # recovers to 25
    assert _conflicts(get_conn) == ["loyalty_overspent"]
    _apply(get_conn, _entry(points_delta=-40.0, entry_type="redeem", created_at="2026-10-07 12:00:00"))
    assert _conflicts(get_conn) == ["loyalty_overspent", "loyalty_overspent"]
    _apply(get_conn, first)                                                           # replay
    assert _conflicts(get_conn) == ["loyalty_overspent", "loyalty_overspent"]


# ── W2a verify F1: one points file imported on two computers ─────────────────

_IMPORT_DAY = "2026-10-07"


def _import_uid(target, day=_IMPORT_DAY, customer=CUST):
    return SyncService._loyalty_import_uid(customer, target, day)


def test_the_same_import_uid_with_the_same_delta_collapses_silently(get_conn):
    iuid = _import_uid(200)
    _apply(get_conn, _entry(points_delta=150.0, entry_type="earn", created_at=T1))
    _apply(get_conn, _entry(uid=iuid, points_delta=50.0, entry_type="adjust", created_at=T2))
    _apply(get_conn, _entry(uid=iuid, points_delta=50.0, entry_type="adjust", created_at=T3, created_by="bob"))
    assert _sum(get_conn) == 200.0 and _points(get_conn) == 200.0       # NOT 250
    assert _conflicts(get_conn) == []


@pytest.mark.parametrize("local_first", [50.0, 30.0])
def test_the_same_import_uid_with_a_different_delta_keeps_the_smaller_and_asks(get_conn, local_first):
    iuid = _import_uid(200)
    incoming = 30.0 if local_first == 50.0 else 50.0
    _apply(get_conn, _entry(uid=iuid, points_delta=local_first, entry_type="adjust", created_at=T2))
    _apply(get_conn, _entry(uid=iuid, points_delta=incoming, entry_type="adjust", created_at=T3))
    rows = _ledger(get_conn)
    assert len(rows) == 1 and rows[0]["points_delta"] == 30.0           # smaller, either arrival order
    assert _points(get_conn) == 30.0
    assert _conflicts(get_conn) == ["loyalty_import_differs"]
    _apply(get_conn, _entry(uid=iuid, points_delta=45.0, entry_type="adjust", created_at=T3))
    assert _conflicts(get_conn) == ["loyalty_import_differs"], "once per import"


def test_a_return_adjust_with_a_clashing_uid_is_not_treated_as_an_import(get_conn):
    # Only an adjust that names no sale and no return is an import row.
    sale_id = _add_sale(get_conn)
    iuid = _import_uid(200)
    _apply(get_conn, _entry(uid=iuid, points_delta=-5.0, entry_type="adjust", sale_uid=SALE_UID, created_at=T1))
    _apply(get_conn, _entry(uid=iuid, points_delta=-9.0, entry_type="adjust", sale_uid=SALE_UID, created_at=T2))
    assert _conflicts(get_conn) == []
    assert _ledger(get_conn)[0]["points_delta"] == -5.0 and sale_id


def test_an_opening_and_an_adjust_importing_the_same_figure_are_reported(get_conn):
    # Probe `import_offline_both`: A had no history for the customer (it wrote an
    # 'opening' of 150), B had some (it wrote an 'adjust' of 120 under the import
    # uid for target 150). Different uids -> both count; a person is asked.
    _apply(get_conn, _entry(uid=_opening_uid(), points_delta=150.0, entry_type="opening", created_at=T1))
    assert _conflicts(get_conn) == []
    _apply(get_conn, _entry(uid=_import_uid(150), points_delta=120.0, entry_type="adjust", created_at=T2))
    assert _sum(get_conn) == 270.0
    assert _conflicts(get_conn) == ["loyalty_import_differs"]


@pytest.mark.parametrize("target,day", [(200, _IMPORT_DAY), (150, "2026-10-12")])
def test_an_ordinary_re_import_is_not_reported(get_conn, target, day):
    # A later correction (a different figure, or a later day) derives a
    # different uid and is just a delta -- the legitimate case this must not flag.
    _apply(get_conn, _entry(uid=_opening_uid(), points_delta=150.0, entry_type="opening", created_at=T1))
    _apply(get_conn, _entry(uid=_import_uid(target, day), points_delta=50.0, entry_type="adjust", created_at=T2))
    assert _conflicts(get_conn) == []


def test_import_uid_namespace_and_function_equal_the_core_module():
    from core.retail import loyalty as core
    assert str(ss._LOYALTY_IMPORT_NAMESPACE) == "4d9e2f7a-81c3-4b56-9e0d-6a3f1c8b5e72"   # never changes
    assert ss._LOYALTY_IMPORT_NAMESPACE == core._IMPORT_NAMESPACE
    for target in (150, 150.0, "150.00", 0, 0.5, -3, 1e3):
        for day in ("2026-10-07", "2027-01-01"):
            assert SyncService._loyalty_import_uid(CUST, target, day) == core.import_uid(CUST, target, day)
    # The same figure spelled three ways is one uid.
    assert core.import_uid(CUST, 150, "d") == core.import_uid(CUST, 150.0, "d") == core.import_uid(CUST, "150.00", "d")
    assert core.import_uid(CUST, 150, "d") != core.import_uid(CUST, 151, "d")
    assert core.import_uid(CUST, 150, "d") != core.import_uid(CUST, 150, "e")
    assert core.import_uid(CUST, 150, "d") != core.opening_uid(CUST)


# ── the sale apply carries points_earned ─────────────────────────────────────

def test_sale_apply_stores_points_earned(get_conn):
    uid = str(uuid.uuid4())
    ev = {"entity_type": "sale", "event_type": "create", "entity_id": uid,
          "payload": {"uid": uid, "sale_number": "S-W2A-1", "customer_id": CUST,
                      "subtotal": 100, "total": 100, "amount_paid": 100,
                      "points_earned": 10.0, "created_at": T1}}
    assert _apply(get_conn, ev) is True
    r = _rows(get_conn, "SELECT points_earned FROM sales WHERE uid=?", (uid,))[0]
    assert r["points_earned"] == 10.0
    # The sale apply itself writes NO ledger rows (they arrive as loyalty_entry).
    assert _ledger(get_conn) == []
    assert _points(get_conn) == 0


def test_sale_apply_without_points_earned_leaves_it_null(get_conn):
    uid = str(uuid.uuid4())
    ev = {"entity_type": "sale", "event_type": "create", "entity_id": uid,
          "payload": {"uid": uid, "sale_number": "S-W2A-2", "customer_id": CUST,
                      "subtotal": 100, "total": 100, "amount_paid": 100, "created_at": T1}}
    assert _apply(get_conn, ev) is True
    r = _rows(get_conn, "SELECT points_earned FROM sales WHERE uid=?", (uid,))[0]
    assert r["points_earned"] is None


# ── the twins stay equal to the core module ──────────────────────────────────

def test_opening_namespace_and_uid_equal_the_core_module():
    from core.retail import loyalty as core
    assert ss._LOYALTY_OPENING_NAMESPACE == core._OPENING_NAMESPACE
    for cust in (CUST, "c-1", "", "x" * 40):
        assert SyncService._loyalty_opening_uid(cust) == core.opening_uid(cust)


def test_cache_refresh_equals_the_core_refresh(get_conn):
    from core.retail import loyalty as core
    # Two customers with rows of mixed sign; run the core refresh on one and the
    # sync service's twin on the other against identical ledgers, then compare.
    for cust in (CUST, CUST2):
        for delta, kind in ((10.0, "earn"), (-3.5, "redeem"), (7.25, "adjust"), (-0.75, "adjust")):
            _exec(get_conn, "INSERT INTO loyalty_ledger (uid, company_id, customer_id, "
                            "points_delta, entry_type) VALUES (?,?,?,?,?)",
                  (str(uuid.uuid4()), CID, cust, delta, kind))
        # W2a verify F5: a SECOND company holds ledger rows under the same
        # customer id. Neither refresh may count them -- the single-company
        # version of this test could not catch a twin that dropped the
        # company_id filter, and the two statements drifting is how that
        # happens unnoticed.
        for delta, kind in ((1000.0, "earn"), (-1.0, "redeem")):
            _exec(get_conn, "INSERT INTO loyalty_ledger (uid, company_id, customer_id, "
                            "points_delta, entry_type) VALUES (?,?,?,?,?)",
                  (str(uuid.uuid4()), "another-company", cust, delta, kind))
        _exec(get_conn, "UPDATE customers SET loyalty_points=-1 WHERE id=?", (cust,))
    conn = get_conn()
    try:
        core.refresh_points(conn, CID, CUST)
        SyncService._refresh_loyalty_points(conn, CID, CUST2)
        conn.commit()
    finally:
        conn.close()
    assert _points(get_conn, CUST) == _points(get_conn, CUST2) == 13.0
    # And a customer with NO rows reads 0 through both, never NULL.
    conn = get_conn()
    try:
        conn.execute("INSERT INTO customers (id, company_id, name, loyalty_points) VALUES ('empty-a',?,'E',5)", (CID,))
        conn.execute("INSERT INTO customers (id, company_id, name, loyalty_points) VALUES ('empty-b',?,'E',5)", (CID,))
        core.refresh_points(conn, CID, "empty-a")
        SyncService._refresh_loyalty_points(conn, CID, "empty-b")
        conn.commit()
    finally:
        conn.close()
    assert _points(get_conn, "empty-a") == _points(get_conn, "empty-b") == 0
