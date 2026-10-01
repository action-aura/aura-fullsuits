"""AUDIT-033 B2b, retail stream -- the signed re-announce of till allocations and
numbering books, and the supersede cleanup against retail.db's quarantine.

Same fixture technique as test_sync_authority_retail.py (a REAL `init_retail()`
database, a REAL registry.db, REAL signatures). The registry-stream half is in
test_sync_reannounce.py.

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/sync/tests/test_sync_reannounce_retail.py -q
"""
from __future__ import annotations

import json

import pytest

from commercial_runtime.sync.tests.test_sync_authority_retail import (  # noqa: F401
    CID, Shop, _own_book, _series_ev, _till_ev, shop)
import api.retail_api as retail_api


def _apply(shop, ev, signer=None):
    """One event through the sync loop's own entry point (savepoint + supersede)."""
    if signer is not None:
        ev = shop.world.sign(ev, signer)
    conn = shop.get_conn()
    try:
        handled = shop.svc._apply_event_guarded(conn, ev, local_company_id=CID)
        conn.commit()
        return handled
    finally:
        conn.close()


def _park(shop, entity_type, entity_id, event_type, payload, reason):
    conn = shop.get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO sync_apply_quarantine (entity_id, entity_type, event_type, payload, reason, detail) "
            "VALUES (?,?,?,?,?,?)", (entity_id, entity_type, event_type, json.dumps(payload), reason, "test"))
        conn.commit()
    finally:
        conn.close()


def _parked(shop):
    return {(r["entity_type"], r["entity_id"], r["event_type"]): r["reason"] for r in shop.rows(
        "SELECT entity_type, entity_id, event_type, reason FROM sync_apply_quarantine")}


def _seed_history(shop):
    """What a shop has by the time its main computer upgrades."""
    assert _apply(shop, _till_ev("al-1", number=2, terminal="dev-a"), shop.M) is True
    assert _apply(shop, _till_ev("al-2", number=3, terminal="dev-b", status="released", rv=2), shop.M) is True
    # A real clash: two computers given the SAME till number. The engine keeps one
    # active and stores the other as `superseded` -- a local verdict, never re-emitted.
    assert _apply(shop, _till_ev("al-3", number=2, terminal="dev-c"), shop.M) is True
    assert sorted(r["status"] for r in shop.rows("SELECT status FROM till_allocations WHERE id IN ('al-1','al-3')")) == [
        "active", "superseded"]
    assert _apply(shop, _series_ev("ds-main", "MMM"), shop.M) is True
    assert _apply(shop, _own_book("own-a", "dev-a", "AAA2"), shop.A) is True
    assert _apply(shop, _own_book("own-b", "dev-b", "BBB3"), shop.B) is True


def _emit(shop, *, is_main, terminal):
    conn = shop.get_conn()
    try:
        conn.execute("DELETE FROM sync_outbox")
        n = retail_api.reannounce_authority_events(conn, CID, is_main=is_main, terminal_uid=terminal)
        rows = conn.execute(
            "SELECT id, entity_type, entity_id, event_type, payload, created_at FROM sync_outbox ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()
    events = [{"id": r[0], "entity_type": r[1], "entity_id": r[2], "event_type": r[3],
               "payload": json.loads(r[4]), "created_at": r[5]} for r in rows]
    assert n == len(events)
    return events


def _state(shop):
    return {"alloc": shop.rows("SELECT id, status, row_version, till_number, terminal_uid FROM till_allocations ORDER BY id"),
            "series": shop.rows("SELECT id, status, row_version, allocator_terminal_uid, claimed_at_utc, label "
                                "FROM doc_series ORDER BY id")}


# ═══ what is queued ═════════════════════════════════════════════════════════

def test_the_main_restates_every_book_and_every_allocation_that_is_not_a_clash_verdict(shop):
    _seed_history(shop)
    before = _state(shop)
    events = _emit(shop, is_main=True, terminal="dev-main")
    assert sorted(e["entity_id"] for e in events if e["entity_type"] == "doc_series") == ["ds-main", "own-a", "own-b"]
    loser = shop.rows("SELECT id FROM till_allocations WHERE status='superseded'")[0]["id"]
    assert loser in ("al-1", "al-3")
    assert sorted(e["entity_id"] for e in events if e["entity_type"] == "till_allocation") == sorted(
        ["al-1", "al-2", "al-3"][i] for i in range(3) if ["al-1", "al-2", "al-3"][i] != loser), (
        "active and released ride; a superseded row is a local verdict and must not")
    assert _state(shop) == before, "a re-announce must not move any version"
    for e in events:
        stored = next(r for r in before["alloc"] + before["series"] if r["id"] == e["entity_id"])
        assert e["payload"]["row_version"] == stored["row_version"]
        assert e["event_type"] == "update"


def test_an_ordinary_till_restates_only_its_own_books(shop):
    _seed_history(shop)
    events = _emit(shop, is_main=False, terminal="dev-a")
    assert [(e["entity_type"], e["entity_id"]) for e in events] == [("doc_series", "own-a")]


# ═══ what a receiver does with it ═══════════════════════════════════════════

def test_an_equal_version_restatement_is_a_silent_noop_where_the_rows_exist(shop):
    _seed_history(shop)
    before = _state(shop)
    signed = [shop.world.sign(e, shop.M) for e in _emit(shop, is_main=True, terminal="dev-main")]
    shop.pull(signed)
    assert _state(shop) == before
    assert shop.parked() == []
    assert shop.rows("SELECT COUNT(*) AS n FROM sync_conflicts")[0]["n"] == 0, (
        "an identical copy is an echo, not a conflict")


def test_a_genuine_same_version_disagreement_still_records_a_conflict(shop):
    """The other half of the echo guard: only an IDENTICAL copy is silent."""
    assert _apply(shop, _own_book("own-a", "dev-a", "AAA2"), shop.A) is True
    different = _own_book("own-a", "dev-a", "AAA2", label="a different label")
    different["event_type"] = "update"
    assert _apply(shop, different, shop.A) is True
    assert shop.rows("SELECT COUNT(*) AS n FROM sync_conflicts")[0]["n"] == 1


def test_a_restatement_applies_on_a_computer_that_has_none_of_it(tmp_path, monkeypatch):
    (tmp_path / "main").mkdir()
    main = Shop(tmp_path / "main", monkeypatch)
    _seed_history(main)
    want = _state(main)
    events = _emit(main, is_main=True, terminal="dev-main")

    (tmp_path / "fresh").mkdir()
    fresh = Shop(tmp_path / "fresh", monkeypatch)
    assert _state(fresh) == {"alloc": [], "series": []}
    fresh.pull([fresh.world.sign(e, fresh.M) for e in events])
    got = _state(fresh)
    assert fresh.parked() == []
    # The clash loser (a local verdict) was never emitted, so the fresh computer lacks it.
    assert got["alloc"] == [r for r in want["alloc"] if r["status"] != "superseded"]
    assert got["series"] == want["series"]


# ═══ supersede cleanup (retail quarantine) ══════════════════════════════════

def test_a_signed_allocation_clears_the_unsigned_copy_it_answers_and_only_that(shop):
    old = _till_ev("al-9", number=5, terminal="dev-a", rv=1)["payload"]
    newer = _till_ev("al-9", number=5, terminal="dev-a", rv=4)["payload"]
    _park(shop, "till_allocation", "al-9", "create", old, "unsigned_authority_event")
    _park(shop, "till_allocation", "al-9", "update", newer, "unsigned_authority_event")
    _park(shop, "till_allocation", "al-8", "create", old, "unsigned_authority_event")   # another entity
    assert _apply(shop, _till_ev("al-9", number=5, terminal="dev-a", rv=1), shop.M) is True
    assert _parked(shop) == {("till_allocation", "al-9", "update"): "unsigned_authority_event",
                             ("till_allocation", "al-8", "create"): "unsigned_authority_event"}


@pytest.mark.parametrize("reason", ["needs_main_computer", "bad_signature", "signer_revoked",
                                    "allocation_epoch_unknown"])
def test_a_signed_allocation_never_clears_a_forgery_signal_or_an_unrelated_reason(shop, reason):
    payload = _till_ev("al-9", number=5, terminal="dev-a", rv=1)["payload"]
    _park(shop, "till_allocation", "al-9", "create", payload, reason)
    _apply(shop, _till_ev("al-9", number=5, terminal="dev-a", rv=1), shop.M)
    assert _parked(shop) == {("till_allocation", "al-9", "create"): reason}


def test_a_signed_book_clears_its_unsigned_copies_by_version(shop):
    book = lambda rv: _own_book("own-z", "dev-a", "ZZZ9", row_version=rv)["payload"]
    _park(shop, "doc_series", "own-z", "create", book(1), "unsigned_authority_event")
    _park(shop, "doc_series", "own-z", "update", book(6), "unsigned_authority_event")
    assert _apply(shop, _own_book("own-z", "dev-a", "ZZZ9", row_version=2), shop.A) is True
    assert _parked(shop) == {("doc_series", "own-z", "update"): "unsigned_authority_event"}
