"""P2S fix crew F2, item H3 -- ONE malformed event must never stop a whole pull.

The finding (money verifier vYM, "odd payloads" probe): `apply_pull_result`
has no per-event isolation on the RETAIL stream, and the registry stream's
`_apply_event_guarded` only covered four entity types and a narrow exception
tuple. A peer (or a buggy build) that sends ONE event whose payload is the
wrong SHAPE -- a list where an object belongs, a list or dict where a status
string belongs -- raised out of the loop, the batch rolled back, the cursor
never advanced, and the same event was re-pulled forever: every later event,
sales included, stuck behind it. Measured before the fix: all 6 retail shapes
below wedged the batch (`TypeError: unhashable type: 'list'`,
`AttributeError: 'list' object has no attribute 'get'`), as did 3 of the 4
registry types (12 of the 14 tests in this file failed).

The rule pinned here, in BOTH directions:

  DENY   an odd event is parked as exactly ONE `sync_apply_quarantine` row and
         the events AFTER it in the same batch still apply;
  ALLOW  the write an odd event managed to make BEFORE it blew up is undone
         (its own SAVEPOINT), while a locked/corrupt DATABASE
         (`sqlite3.OperationalError`) is still NOT swallowed -- that is not a
         property of the payload and must still fail the batch.

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/sync/tests/test_apply_isolation.py -q
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from commercial_runtime.sync import sync_service as ss
from commercial_runtime.sync.sync_service import REGISTRY_SYNC_ENTITY_TYPES, SyncService
from commercial_runtime.sync.tests import test_branch_alias_till_allocation_apply as T
from commercial_runtime.sync.tests import test_registry_v9_apply as R

install = T.install
registry_env = R.registry_env


def _good_branch():
    uid = str(uuid.uuid4())
    return uid, {"entity_type": "branch", "event_type": "create", "entity_id": uid,
                 "payload": {"uid": uid, "name": "After Odd", "address": "", "phone": "",
                             "status": "active"}}


def _quarantine_rows(get_conn):
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT entity_type, event_type, reason FROM sync_apply_quarantine").fetchall()]
    finally:
        conn.close()


def _run_batch(get_conn, svc, events, cursor=7):
    conn = get_conn()
    try:
        svc.apply_pull_result(conn, {"events": events, "cursor": cursor})
        conn.commit()
    finally:
        conn.close()


# name -> (entity_type, payload factory). Every one of these raised out of the
# loop before the fix (or, for the payload-is-a-list shapes, could not even be
# read as an object).
ODD_RETAIL = {
    "till_status_list": ("till_allocation", lambda: T._till_ev(status=["active"])["payload"]),
    "till_status_dict": ("till_allocation", lambda: T._till_ev(status={"a": 1})["payload"]),
    "till_payload_list": ("till_allocation", lambda: ["x"]),
    "alias_payload_list": ("branch_alias", lambda: ["x"]),
    "sale_payload_list": ("sale", lambda: ["x"]),
    "branch_payload_list": ("branch", lambda: ["x"]),
}


@pytest.mark.parametrize("case", sorted(ODD_RETAIL))
def test_retail_odd_event_is_parked_once_and_the_next_event_still_applies(install, case):
    etype, make = ODD_RETAIL[case]
    good_uid, good = _good_branch()
    odd = {"entity_type": etype, "event_type": "create", "entity_id": str(uuid.uuid4()),
           "payload": make()}

    _run_batch(install, T._svc(install), [odd, good])

    conn = install()
    try:
        landed = conn.execute("SELECT COUNT(*) FROM branches WHERE uid=?", (good_uid,)).fetchone()[0]
    finally:
        conn.close()
    assert landed == 1, "the event AFTER the odd one never applied: the batch was wedged"
    rows = _quarantine_rows(install)
    assert len(rows) == 1 and rows[0]["entity_type"] == etype, rows
    assert rows[0]["reason"] in ("malformed_payload", f"malformed_{etype}"), rows


def test_odd_event_stays_one_row_across_repeated_pulls(install):
    """The retry loop replays a parked row every pull; it must stay ONE row."""
    good_uid, good = _good_branch()
    odd = {"entity_type": "till_allocation", "event_type": "create", "entity_id": "odd-1",
           "payload": ["x"]}
    svc = T._svc(install)
    _run_batch(install, svc, [odd, good], cursor=1)
    _run_batch(install, svc, [], cursor=2)
    _run_batch(install, svc, [], cursor=3)
    assert len(_quarantine_rows(install)) == 1


def test_a_write_made_before_the_event_blew_up_is_rolled_back(install, monkeypatch):
    """ALLOW-half of the savepoint: the alias row is INSERTed and THEN the
    follow-up normalisation raises. Only THAT event's write may be undone --
    the earlier good event in the batch stays, the later one still applies."""
    first_uid, first = _good_branch()
    later_uid, later = _good_branch()
    ev = T._alias_ev()

    def boom(conn, cid):
        raise ValueError("payload-shaped failure after the alias row was written")

    monkeypatch.setattr(ss, "_normalise_branch_uid_refs", boom)
    # canonical must be this licence's default uid for the alias to be written
    monkeypatch.setattr(ss, "_owner_licence_id", lambda: T.LID)

    _run_batch(install, T._svc(install), [first, ev, later])

    assert T._aliases(install) == [], "the half-written alias row survived the failed event"
    conn = install()
    try:
        n = conn.execute("SELECT COUNT(*) FROM branches WHERE uid IN (?,?)",
                         (first_uid, later_uid)).fetchone()[0]
    finally:
        conn.close()
    assert n == 2, "an event before and an event after the failure must both have applied"
    rows = _quarantine_rows(install)
    assert [(r["entity_type"], r["reason"]) for r in rows] == [("branch_alias", "malformed_branch_alias")]


@pytest.mark.parametrize("exc_type", [KeyError, AttributeError, TypeError, ValueError,
                                      sqlite3.IntegrityError])
def test_any_exception_a_handler_raises_parks_one_row_and_the_batch_continues(
        install, monkeypatch, exc_type):
    """The isolation itself, separate from the shape checks above (those pass
    by type-checking alone): whatever a handler raises on its own payload --
    including the AttributeError/KeyError the old narrow tuple missed --
    becomes exactly one `malformed_<type>` row and the next event applies."""
    good_uid, good = _good_branch()
    odd = {"entity_type": "till_allocation", "event_type": "create", "entity_id": "boom-1",
           "payload": T._till_ev()["payload"]}

    def boom(self, conn, ev, p, event_type, local_company_id, conflict_sink):
        raise exc_type("handler exploded on this payload")

    monkeypatch.setattr(SyncService, "_apply_till_allocation", boom)
    _run_batch(install, T._svc(install), [odd, good])

    conn = install()
    try:
        assert conn.execute("SELECT COUNT(*) FROM branches WHERE uid=?", (good_uid,)).fetchone()[0] == 1
    finally:
        conn.close()
    rows = _quarantine_rows(install)
    assert [(r["entity_type"], r["reason"]) for r in rows] == [("till_allocation", "malformed_till_allocation")]


def test_registry_handler_exception_parks_one_row_and_the_next_user_applies(registry_env, monkeypatch):
    bad_uid, _ = R._seed_user(registry_env, employee_id="E-BAD", email="bad@x.com")
    good_uid, _ = R._seed_user(registry_env, employee_id="E-GOOD", email="good@x.com")
    real = SyncService._resolve_employee_code

    def boom(conn, company_id, uid, incoming):
        if uid == bad_uid:
            raise KeyError("boom")
        return real(conn, company_id, uid, incoming)

    monkeypatch.setattr(SyncService, "_resolve_employee_code", staticmethod(boom))
    _run_batch(registry_env, _registry_service(registry_env),
               [R._user_ev(bad_uid), R._user_ev(good_uid)])

    versions = {r["uid"]: r["row_version"]
                for r in R._q(registry_env, "SELECT uid, row_version FROM users")}
    assert versions[good_uid] == 2, "the user AFTER the failing one never applied"
    assert versions[bad_uid] == 1, "the failing user's write was not undone"
    rows = _quarantine_rows(registry_env)
    assert [(r["entity_type"], r["reason"]) for r in rows] == [("user", "malformed_user")]


def test_a_locked_database_still_fails_the_batch(install, monkeypatch):
    """DENY must not become 'swallow everything': OperationalError is not a
    property of the payload, so it must still propagate (cursor stays put)."""
    def locked(conn, cid):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ss, "_normalise_branch_uid_refs", locked)
    monkeypatch.setattr(ss, "_owner_licence_id", lambda: T.LID)
    with pytest.raises(sqlite3.OperationalError):
        _run_batch(install, T._svc(install), [T._alias_ev()])
    assert _quarantine_rows(install) == []


def test_a_non_object_event_is_skipped_not_raised(install):
    good_uid, good = _good_branch()
    _run_batch(install, T._svc(install), ["not-an-event", None, good])
    conn = install()
    try:
        assert conn.execute("SELECT COUNT(*) FROM branches WHERE uid=?", (good_uid,)).fetchone()[0] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# registry stream: the wrapper already existed; the shapes it missed
# ---------------------------------------------------------------------------

def _registry_service(get_conn):
    return SyncService(client_factory=lambda: None, get_conn=get_conn,
                       local_company_id_provider=lambda: R.COMPANY,
                       handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES)


@pytest.mark.parametrize("etype", ["user", "user_permission", "device", "admin_device"])
def test_registry_list_payload_is_parked_once_and_the_next_event_applies(registry_env, etype):
    uid, _ = R._seed_user(registry_env)
    odd = {"entity_type": etype, "event_type": "update", "entity_id": str(uuid.uuid4()),
           "payload": ["x"]}
    _run_batch(registry_env, _registry_service(registry_env), [odd, R._user_ev(uid)])

    row = R._q(registry_env, "SELECT row_version FROM users WHERE uid=?", (uid,))[0]
    assert row["row_version"] == 2, "the user update AFTER the odd event never applied"
    rows = _quarantine_rows(registry_env)
    assert len(rows) == 1 and rows[0]["entity_type"] == etype, rows
