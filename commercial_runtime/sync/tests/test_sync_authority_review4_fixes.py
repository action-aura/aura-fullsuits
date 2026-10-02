"""AUDIT-033 S-AUTH -- fix round 3 for review V4r (receiver half): the retry budget,
the permanent/recoverable split of the flood bounds, and the per-signer evidence cap.
(The reviewer's own probes are in test_sync_authority_review4.py, verbatim.)

Run from the worktree root, ONE FILE PER PYTEST PROCESS.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest

from commercial_runtime.sync import authority
from commercial_runtime.sync import sync_service as ss
from commercial_runtime.sync.tests.test_sync_authority_rules import Shop, COMPANY, OWNER


@pytest.fixture
def shop(tmp_path, monkeypatch):
    ss._FLOOD_WARNED.clear()
    authority._LEASE_CACHE.clear()
    return Shop(tmp_path, monkeypatch)


def _insert_rows(shop, n, reason, prefix="r"):
    conn = shop.get_conn()
    try:
        conn.executemany(
            "INSERT INTO sync_apply_quarantine (entity_id, entity_type, event_type, payload, reason, detail, auth_key) "
            "VALUES (?,?,?,?,?,?,?)",
            [(f"{prefix}-{i}", "user", "update",
              json.dumps({"uid": f"{prefix}-{i}", "_auth": {"v": 1, "sig": "x"}}), reason, "d", f"k-{prefix}-{i}")
             for i in range(n)])
        conn.commit()
    finally:
        conn.close()


def _count_retries(shop, monkeypatch):
    seen = []
    real = shop.svc._apply_event_guarded

    def spy(conn, ev, *a, **k):
        seen.append(ev["entity_id"])
        return real(conn, ev, *a, **k)
    monkeypatch.setattr(shop.svc, "_apply_event_guarded", spy)
    return seen


def test_one_retry_pass_over_5000_parked_rows_is_bounded_and_every_row_is_reached(shop, monkeypatch):
    _insert_rows(shop, 5000, authority.REASON_MAIN_UNKNOWN)
    seen = _count_retries(shop, monkeypatch)
    t = time.perf_counter()
    shop.pull([])
    first = time.perf_counter() - t
    assert first < 3.0, f"one pass took {first:.2f}s"
    assert 0 < len(seen) <= authority.RETRY_PASS_MAX_ROWS
    visited = set(seen)
    for _ in range(60):
        before = len(seen)
        shop.pull([])
        assert len(seen) - before <= authority.RETRY_PASS_MAX_ROWS
        visited.update(seen[before:])
        if len(visited) == 5000:
            break
    assert len(visited) == 5000, f"only {len(visited)} of 5000 rows were ever retried"


def test_a_queue_that_fits_one_pass_is_retried_in_full_every_pull(shop, monkeypatch):
    _insert_rows(shop, 30, authority.REASON_MAIN_UNKNOWN)
    seen = _count_retries(shop, monkeypatch)
    shop.pull([])
    shop.pull([])
    assert len(seen) == 60


@pytest.mark.parametrize("reason", sorted(authority.PERMANENT_PARK_REASONS))
def test_permanent_junk_is_never_retried_but_stays_for_review(shop, monkeypatch, reason):
    _insert_rows(shop, 10, reason, prefix="perm")
    _insert_rows(shop, 3, authority.REASON_MAIN_UNKNOWN, prefix="live")      # control: this kind IS retried
    seen = _count_retries(shop, monkeypatch)
    shop.pull([])
    assert sorted(seen) == ["live-0", "live-1", "live-2"]
    assert shop.q("SELECT COUNT(*) AS n FROM sync_apply_quarantine WHERE reason=? AND entity_id LIKE 'perm-%'", (reason,))[0]["n"] == 10


def _park(shop, entity_id, reason, **ev_extra):
    conn = shop.get_conn()
    try:
        ev = dict({"entity_id": entity_id, "entity_type": "user", "event_type": "update",
                   "payload": {"uid": entity_id, "n": uuid.uuid4().hex, "_auth": {"v": 1, "sig": "s"}}},
                  **ev_extra)
        shop.svc._quarantine_apply_event(conn, ev, reason, "d")
        conn.commit()
    finally:
        conn.close()


def _rows(shop, entity_id=None):
    sql = "SELECT reason FROM sync_apply_quarantine" + (" WHERE entity_id=?" if entity_id else "") + " ORDER BY rowid"
    return [r["reason"] for r in shop.q(sql, (entity_id,) if entity_id else ())]


def test_a_full_entity_bound_evicts_the_oldest_junk_instead_of_refusing_a_recoverable_failure(shop, monkeypatch):
    monkeypatch.setattr(authority, "MAX_PARKED_VERIFICATION_FAILURES_PER_ENTITY", 3)
    for _ in range(3):
        _park(shop, "e1", authority.REASON_BAD_SIGNATURE)
    _park(shop, "e1", authority.REASON_UNTRUSTED_LEASE)
    assert _rows(shop, "e1") == ["bad_signature", "bad_signature", "untrusted_lease"]


def test_recoverable_failures_are_never_dropped_even_when_no_junk_can_make_room(shop, monkeypatch):
    monkeypatch.setattr(authority, "MAX_PARKED_VERIFICATION_FAILURES_PER_ENTITY", 2)
    for reason in (authority.REASON_UNTRUSTED_LEASE, authority.REASON_DEV_NOT_ATTESTED,
                   authority.REASON_UNTRUSTED_LEASE, authority.REASON_DEV_NOT_ATTESTED):
        _park(shop, "e2", reason)
    assert len(_rows(shop, "e2")) == 4                                  # allow-half: all four kept
    _park(shop, "e2", authority.REASON_BAD_SIGNATURE)                   # deny-half: junk with no junk to evict
    assert len(_rows(shop, "e2")) == 4


def test_the_global_junk_cap_evicts_the_oldest_junk_and_keeps_recoverable_rows(shop, monkeypatch):
    monkeypatch.setattr(authority, "MAX_PARKED_PERMANENT_JUNK_TOTAL", 3)
    _park(shop, "keep", authority.REASON_UNTRUSTED_LEASE)
    for i in range(6):
        _park(shop, f"j{i}", authority.REASON_BAD_SIGNATURE)
    left = shop.q("SELECT entity_id FROM sync_apply_quarantine WHERE reason='bad_signature' ORDER BY rowid")
    assert [r["entity_id"] for r in left] == ["j3", "j4", "j5"]       # the NEWEST three survive
    assert "untrusted_lease" in _rows(shop)


def test_the_global_verification_failure_cap_evicts_junk_first(shop, monkeypatch):
    monkeypatch.setattr(authority, "MAX_PARKED_VERIFICATION_FAILURES_TOTAL", 4)
    for i in range(4):
        _park(shop, f"t{i}", authority.REASON_BAD_SIGNATURE)
    _park(shop, "legit", authority.REASON_UNTRUSTED_LEASE)
    reasons = _rows(shop)
    assert len(reasons) == 4 and reasons.count("untrusted_lease") == 1


def test_evidence_rows_are_capped_per_signer_installation_and_other_signers_are_unaffected(shop, monkeypatch):
    monkeypatch.setattr(authority, "MAX_PARKED_EVIDENCE_PER_SIGNER", 5)
    for i in range(8):                                                   # A: member changing the owner
        shop.apply(shop.user_event(OWNER, password_hash=f"EVIL-A-{i}"), shop.A)
    mine = shop.q("SELECT COUNT(*) AS n FROM sync_apply_quarantine WHERE reason='owner_change_needs_main' "
                  "AND json_extract(payload,'$._auth.inst')=?", (shop.A.inst,))[0]["n"]
    assert mine == 5
    shop.apply(shop.user_event(OWNER, password_hash="EVIL-B"), shop.B)   # allow-half: another signer
    other = shop.q("SELECT COUNT(*) AS n FROM sync_apply_quarantine WHERE json_extract(payload,'$._auth.inst')=?",
                   (shop.B.inst,))[0]["n"]
    assert other == 1


# ── N1(a): the park's own read-back must not grow with the pile ──────────────
# The retry budget alone makes the reviewer's quadratic probe pass, so this pins
# the read-back ITSELF: a fixed number of park calls costs the same whether one
# entity already has 100 or 800 parked rows.

def _seed_signed_rows(shop, n):
    conn = shop.get_conn()
    try:
        conn.executemany(
            "INSERT INTO sync_apply_quarantine (entity_id, entity_type, event_type, payload, reason, detail, auth_key) "
            "VALUES (?,?,?,?,?,?,?)",
            [("one-entity", "user", "update",
              json.dumps({"uid": "one-entity", "n": i, "pad": "x" * 400,
                          "_auth": {"v": 1, "sig": f"s{i}", "inst": "inst-x"}}),
              authority.REASON_OWNER_CHANGE_NEEDS_MAIN, "d", f"k{i}") for i in range(n)])
        conn.commit()
    finally:
        conn.close()


def _time_parks(shop, payload, reason, calls=200):
    conn = shop.get_conn()
    try:
        ev = {"entity_id": "one-entity", "entity_type": "user", "event_type": "update", "payload": payload}
        t = time.perf_counter()
        for _ in range(calls):
            shop.svc._quarantine_apply_event(conn, dict(ev), reason, "d")
        elapsed = time.perf_counter() - t
        conn.rollback()
        return elapsed
    finally:
        conn.close()


@pytest.mark.parametrize("shape", ["re_park_of_a_signed_row", "unsigned_copy_beside_signed_rows"])
def test_park_cost_does_not_grow_with_the_rows_already_parked_for_the_entity(tmp_path, monkeypatch, shape):
    ss._FLOOD_WARNED.clear()
    times = {}
    for n in (100, 800):
        s = Shop(tmp_path / f"n{n}", monkeypatch)
        _seed_signed_rows(s, n)
        if shape == "re_park_of_a_signed_row":
            payload = {"uid": "one-entity", "n": 5, "pad": "x" * 400,
                       "_auth": {"v": 1, "sig": "s5", "inst": "inst-x"}}
            times[n] = _time_parks(s, payload, authority.REASON_OWNER_CHANGE_NEEDS_MAIN)
        else:
            times[n] = _time_parks(s, {"uid": "one-entity", "role": "admin"}, authority.REASON_UNSIGNED)
    assert times[800] < 3 * times[100] + 0.05, f"100 rows: {times[100]:.3f}s, 800 rows: {times[800]:.3f}s"
