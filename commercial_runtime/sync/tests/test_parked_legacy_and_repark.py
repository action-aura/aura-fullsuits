"""P2S F2 L1 + L2 -- two small truth-in-parking defects found by the money verifier.

L1. A LEGACY `sale_out` / `return_in` movement (sent before `source_uid`
    existed) names its source only by `reference` = the document number.
    `_parked_document_by_number` parked it whenever ANY parked document
    carried the same (number, terminal_id) -- including when both terminals
    were blank (None == None) and including when the WINNER of that number was
    present and applied. The winner's own stock movement was then parked
    behind the loser's clash: the sale existed, its stock never moved.

    The rule now: park by number only if the terminal is known (non-blank) on
    both sides AND no PRESENT local document carries that number on that
    terminal (a present document is the movement's parent; the parked one is
    only its would-be twin).

L2. Re-parking the SAME still-blocked event refreshed nothing: `INSERT OR
    IGNORE` kept the first reason and detail, so a row that was first parked
    for one reason and is now blocked for another kept telling the first
    story. Reason, detail and payload now follow the latest attempt;
    `quarantined_at` stays at the FIRST time it was seen ("how long has this
    been stuck"), which is also what the retry loop's oldest-first order needs.

Run one file per process.
"""
from __future__ import annotations

import uuid

from commercial_runtime.sync.sync_service import SyncService
from commercial_runtime.sync.tests import test_parked_children_apply as P

install = P.install

OLD_STAMP = "2020-01-01 00:00:00"


def _return_ev(sale_uid, number, terminal=P.TERMINAL_A, uid=None):
    uid = uid or str(uuid.uuid4())
    return {"entity_type": "return", "event_type": "create", "entity_id": uid,
            "payload": {"uid": uid, "return_number": number, "sale_uid": sale_uid, "branch_uid": None,
                        "total_refund": 10, "refund_method": "cash", "reason": "", "status": "completed",
                        "terminal_id": terminal, "tender_refund_amount": 10, "ar_forgiven_amount": 0,
                        "store_credit_amount": 0, "created_at": "2026-09-24 10:00:00"}}


def _park_return(conn, ret_ev):
    p = ret_ev["payload"]
    conn.execute(
        "INSERT INTO sync_apply_quarantine (entity_id, entity_type, event_type, payload, reason, detail) "
        "VALUES (?,?,?,?,?,?)",
        (p["uid"], "return", "create", P.json.dumps(p), "doc_number_clash", "test"))
    conn.commit()


# ------------------------------------------------------------------ L1

def test_legacy_movement_of_the_winner_is_not_parked_when_terminals_are_blank(install):
    """The verifier's case: winner S-N present, loser S-N parked, both with a
    blank terminal. The winner's own legacy movement must apply."""
    conn = install()
    try:
        pid = P._seed_product(conn)
        svc = P._svc(install)
        winner = P._sale_ev("S-N", terminal=None)
        assert P._apply(svc, conn, winner) is True
        P._park_sale(conn, P._sale_ev("S-N", terminal=None), reason="doc_number_clash")
        mv = P._movement_ev(pid, qty=-2, reference="S-N", terminal=None)
        assert P._apply(svc, conn, mv) is True
        assert P._movement_count(conn, mv["entity_id"]) == 1
        assert conn.execute("SELECT quantity_on_hand q FROM inventory_balances WHERE product_id=?",
                            (pid,)).fetchone()["q"] == -2
    finally:
        conn.close()


def test_legacy_movement_of_the_winner_is_not_parked_when_terminals_are_known(install):
    """Same, with a real terminal on both -- a present sale (N, A) is the
    movement's parent even though a parked twin (N, A) exists."""
    conn = install()
    try:
        pid = P._seed_product(conn)
        svc = P._svc(install)
        assert P._apply(svc, conn, P._sale_ev("S-N", terminal=P.TERMINAL_A)) is True
        P._park_sale(conn, P._sale_ev("S-N", terminal=P.TERMINAL_A))
        mv = P._movement_ev(pid, qty=-2, reference="S-N", terminal=P.TERMINAL_A)
        assert P._apply(svc, conn, mv) is True
        assert P._movement_count(conn, mv["entity_id"]) == 1
    finally:
        conn.close()


def test_legacy_movement_with_blank_terminal_is_not_parked_by_a_parked_sale(install):
    """No present sale, a parked sale with a blank terminal: blank == blank
    identifies nothing, so the movement is not held hostage by it."""
    conn = install()
    try:
        pid = P._seed_product(conn)
        svc = P._svc(install)
        P._park_sale(conn, P._sale_ev("S-N", terminal=None))
        mv = P._movement_ev(pid, reference="S-N", terminal=None)
        assert P._apply(svc, conn, mv) is True
        assert P._movement_count(conn, mv["entity_id"]) == 1
    finally:
        conn.close()


def test_legacy_sale_movement_of_a_parked_sale_with_no_present_twin_is_still_parked(install):
    """Deny half: the original protection must survive."""
    conn = install()
    try:
        pid = P._seed_product(conn)
        svc = P._svc(install)
        P._park_sale(conn, P._sale_ev("S-N", terminal=P.TERMINAL_A))
        mv = P._movement_ev(pid, reference="S-N", terminal=P.TERMINAL_A)
        assert P._apply(svc, conn, mv) is False
        assert P._movement_count(conn, mv["entity_id"]) == 0
        assert P._quarantine_reason(conn, mv["entity_id"]) == "missing_parent:sale"
    finally:
        conn.close()


def test_a_present_sale_on_another_terminal_does_not_release_the_parked_ones_movement(install):
    """A present (N, B) is not the parent of a movement for (N, A)."""
    conn = install()
    try:
        pid = P._seed_product(conn)
        svc = P._svc(install)
        assert P._apply(svc, conn, P._sale_ev("S-N", terminal=P.TERMINAL_B)) is True
        P._park_sale(conn, P._sale_ev("S-N", terminal=P.TERMINAL_A))
        mv = P._movement_ev(pid, reference="S-N", terminal=P.TERMINAL_A)
        assert P._apply(svc, conn, mv) is False
        assert P._quarantine_reason(conn, mv["entity_id"]) == "missing_parent:sale"
    finally:
        conn.close()


def test_legacy_return_movement_of_a_parked_return_with_no_present_twin_is_still_parked(install):
    conn = install()
    try:
        pid = P._seed_product(conn)
        svc = P._svc(install)
        _park_return(conn, _return_ev(str(uuid.uuid4()), "R-N", P.TERMINAL_A))
        mv = P._movement_ev(pid, qty=2, movement_type="return_in", reference="R-N",
                            terminal=P.TERMINAL_A)
        assert P._apply(svc, conn, mv) is False
        assert P._quarantine_reason(conn, mv["entity_id"]) == "missing_parent:return"
    finally:
        conn.close()


def test_legacy_return_movement_of_the_present_return_applies_despite_a_parked_twin(install):
    conn = install()
    try:
        pid = P._seed_product(conn)
        svc = P._svc(install)
        sale = P._sale_ev("S-R", terminal=P.TERMINAL_A)
        assert P._apply(svc, conn, sale) is True
        ret = _return_ev(sale["payload"]["uid"], "R-N", P.TERMINAL_A)
        assert P._apply(svc, conn, ret) is True
        _park_return(conn, _return_ev(sale["payload"]["uid"], "R-N", P.TERMINAL_A))
        mv = P._movement_ev(pid, qty=2, movement_type="return_in", reference="R-N",
                            terminal=P.TERMINAL_A)
        assert P._apply(svc, conn, mv) is True
        assert P._movement_count(conn, mv["entity_id"]) == 1
    finally:
        conn.close()


def test_a_parked_row_with_a_non_object_payload_does_not_break_the_legacy_match(install):
    """A hand-written / corrupt parked payload (a JSON list, a bare number)
    must be skipped, not raise out of the movement's own apply."""
    conn = install()
    try:
        pid = P._seed_product(conn)
        svc = P._svc(install)
        for n, body in enumerate(("[1, 2]", "7", "null", '"x"')):
            conn.execute(
                "INSERT INTO sync_apply_quarantine (entity_id, entity_type, event_type, payload, reason) "
                "VALUES (?,?,?,?,?)", (f"odd-{n}", "sale", "create", body, "x"))
        conn.commit()
        mv = P._movement_ev(pid, reference="S-N", terminal=P.TERMINAL_A)
        assert P._apply(svc, conn, mv) is True
    finally:
        conn.close()


# ------------------------------------------------------------------ tidy: one epoch bound

def test_the_engines_epoch_bound_is_identitys_and_is_the_documented_value():
    """The engine's pre-check and identity's own bound were two literals that
    disagreed on one value. Both must now be the same object, and the value
    is pinned to the documented 2**31 - 1 (last legal epoch) -- a literal on
    purpose, so drifting the shared constant cannot silently pass."""
    from commercial_runtime.identity import device_registry
    from commercial_runtime.sync import sync_service
    assert sync_service._MAX_SYNCED_ADMIN_EPOCH == device_registry.MAX_ADMIN_EPOCH == 2 ** 31 - 1


# ------------------------------------------------------------------ L2

def _quarantine_row(conn, entity_id):
    return conn.execute(
        "SELECT reason, detail, payload, quarantined_at FROM sync_apply_quarantine WHERE entity_id=?",
        (entity_id,)).fetchall()


def test_reparking_the_same_event_updates_reason_and_detail_but_keeps_the_first_time(install):
    conn = install()
    try:
        ev = {"entity_type": "sale", "event_type": "create", "entity_id": "E-1",
              "payload": {"uid": "E-1", "v": 1}}
        SyncService._quarantine_apply_event(conn, ev, reason="first_reason", detail="first detail")
        conn.execute("UPDATE sync_apply_quarantine SET quarantined_at=? WHERE entity_id='E-1'", (OLD_STAMP,))
        conn.commit()

        ev2 = dict(ev, payload={"uid": "E-1", "v": 2})
        assert SyncService._quarantine_apply_event(
            conn, ev2, reason="second_reason", detail="second detail") is False
        conn.commit()

        rows = _quarantine_row(conn, "E-1")
        assert len(rows) == 1, "re-parking must not add a duplicate row"
        assert rows[0]["reason"] == "second_reason"
        assert rows[0]["detail"] == "second detail"
        assert '"v": 2' in rows[0]["payload"]
        assert rows[0]["quarantined_at"] == OLD_STAMP, "first-seen time must survive a re-park"
    finally:
        conn.close()


def test_reparking_a_different_event_type_of_the_same_entity_is_a_separate_row(install):
    """The key is (entity_id, event_type): an upsert must not fold two
    different blocked events of one entity into one."""
    conn = install()
    try:
        for et in ("create", "update"):
            SyncService._quarantine_apply_event(
                conn, {"entity_type": "sale", "event_type": et, "entity_id": "E-2", "payload": {}},
                reason=f"r-{et}", detail=et)
        conn.commit()
        rows = conn.execute("SELECT event_type, reason FROM sync_apply_quarantine WHERE entity_id='E-2' "
                            "ORDER BY event_type").fetchall()
        assert [(r["event_type"], r["reason"]) for r in rows] == [("create", "r-create"), ("update", "r-update")]
    finally:
        conn.close()


def test_a_still_blocked_event_takes_its_new_reason_across_a_real_retry(install):
    """End to end through the retry loop: a sale first parked for one reason,
    still blocked (same number+terminal held by a present sale) on the retry,
    ends with the reason the RETRY gave -- and the first-seen time intact."""
    conn = install()
    try:
        svc = P._svc(install)
        assert P._apply(svc, conn, P._sale_ev("S-CLASH", terminal=P.TERMINAL_A)) is True
        loser = P._sale_ev("S-CLASH", terminal=P.TERMINAL_A)
        P._park_sale(conn, loser, reason="stale_first_reason")
        conn.execute("UPDATE sync_apply_quarantine SET quarantined_at=?, detail='stale' WHERE entity_id=?",
                     (OLD_STAMP, loser["entity_id"]))
        conn.commit()

        svc._retry_quarantined_events(conn, local_company_id=P.CID)
        conn.commit()

        rows = _quarantine_row(conn, loser["entity_id"])
        assert len(rows) == 1
        assert rows[0]["reason"] != "stale_first_reason", "retry re-parked but kept the stale reason"
        assert rows[0]["detail"] != "stale"
        assert rows[0]["quarantined_at"] == OLD_STAMP
    finally:
        conn.close()
