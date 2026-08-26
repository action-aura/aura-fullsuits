"""Phase 5 wave B2 stage 2a -- apply-side hardening for `SyncService.
_apply_event`'s `user` branch. See docs/launch-readiness/
phase5-waveb2-user-sync.md for the full design and
commercial_runtime/sync/sync_service.py's module docstring ("Two-stream
design") + that branch's own comment for the exact reasoning each test below
pins down.

Same technique as products/retail/tests/retail_stock_sync_apply_hardening_
test.py: every test here drives `SyncService._apply_event`/`apply_pull_
result` directly against a REAL, isolated registry.db (built through the
real `init_registry_db()` path, same fixture technique
commercial_runtime/identity/tests/test_registry_v6_quarantine_schema.py
uses), rather than mocking sqlite. `SyncService.__init__`'s
`handled_entity_types` MUST be given `REGISTRY_SYNC_ENTITY_TYPES` (or a set
containing "user") in every test here -- the module DEFAULT is
`RETAIL_SYNC_ENTITY_TYPES`, which does not include "user" at all, so a
service built without it would silently SKIP every event below without ever
reaching the branch under test (see `_apply_event`'s own gate).

Run (ONE FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest commercial_runtime/sync/tests/test_registry_user_sync_apply.py -v
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid

import pytest

from commercial_runtime.identity import registry_db
from commercial_runtime.sync.sync_service import REGISTRY_SYNC_ENTITY_TYPES, SyncService

RECEIVER_COMPANY_ID = "company-x"
HOSTILE_COMPANY_ID = "hostile-attacker-company"


@pytest.fixture
def registry_env(tmp_path, monkeypatch):
    """A real registry.db, built by the real `init_registry_db()` path, at
    the CURRENT (unpinned) REGISTRY_SCHEMA_VERSION -- unlike the v5/v6
    migration-proof fixtures, this file wants the full real v6 shape
    (`users`, `sync_apply_quarantine`, `sync_outbox`, `sync_cursor` all
    present), not a pinned earlier version. `_db_dir`/`DB_PATH` are patched
    directly rather than via AURA_APP_DATA -- registry_db.py resolves both
    from the environment ONCE, at import time, already past by the time
    this fixture runs (identical reasoning to test_registry_v5_sync_schema.
    py's own fixture).

    Returns `registry_db.get_conn` itself -- the REAL production connection
    factory (WAL, busy_timeout, foreign_keys=ON), now bound to this
    isolated tmp_path database by the monkeypatched module attributes."""
    db_dir = tmp_path / "database"
    db_path = db_dir / "registry.db"
    monkeypatch.setattr(registry_db, "_db_dir", str(db_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(db_path))
    registry_db.init_registry_db()
    return registry_db.get_conn


def _seed_local_user(get_conn, **overrides):
    """Inserts one user row directly (bypassing sync entirely) with sane
    defaults matching a real onboarding-created row, then returns its uid.
    `overrides` replaces any column by name."""
    row = {
        "id": str(uuid.uuid4()),
        "company_id": RECEIVER_COMPANY_ID,
        "employee_id": "E1",
        "email": "original@x.com",
        "password_hash": "ORIGINAL_HASH",
        "role": "cashier",
        "status": "active",
        "require_password_change": 1,
        "session_version": 1,
        "language": "en",
        "clinic_role": "",
        "failed_login_count": 0,
        "locked_until": None,
        "uid": str(uuid.uuid4()),
        "pin_hash": "ORIGINAL_PIN",
        "row_version": 1,
        "updated_at_utc": "2026-01-01T00:00:00+00:00",
    }
    row.update(overrides)
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
            "require_password_change, session_version, language, clinic_role, failed_login_count, "
            "locked_until, uid, pin_hash, row_version, updated_at_utc) "
            "VALUES (:id,:company_id,:employee_id,:email,:password_hash,:role,:status,"
            ":require_password_change,:session_version,:language,:clinic_role,:failed_login_count,"
            ":locked_until,:uid,:pin_hash,:row_version,:updated_at_utc)",
            row,
        )
        conn.commit()
    finally:
        conn.close()
    return row["uid"]


def _fetch_user(get_conn, uid):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _user_event(uid, event_type="update", **payload_overrides):
    payload = {
        "uid": uid, "employee_id": "E1", "email": "updated@x.com", "role": "manager",
        "status": "active", "require_password_change": 0, "language": "en",
        "password_hash": "UPDATED_HASH", "pin_hash": "UPDATED_PIN",
        "row_version": 2, "updated_at_utc": "2026-02-01T00:00:00+00:00",
        "session_version": 2,
    }
    payload.update(payload_overrides)
    return {"entity_type": "user", "entity_id": uid, "event_type": event_type, "payload": payload}


def _registry_service(get_conn, company_id=RECEIVER_COMPANY_ID):
    return SyncService(
        client_factory=lambda: None, get_conn=get_conn,
        local_company_id_provider=lambda: company_id,
        handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES,
    )


def _quarantine_rows(get_conn):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT entity_id, entity_type, event_type, payload, reason, detail FROM sync_apply_quarantine"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Task D.1 -- the DENY half: a stale row_version is discarded ────────────

def test_lower_incoming_row_version_is_discarded_byte_unchanged(registry_env):
    """MUTATION-PROVEN (see this task's own report for the verbatim before/
    after pytest output): the `WHERE excluded.row_version > users.row_version`
    clause on the `user` branch's DO UPDATE is what makes this discard
    happen -- removing it turns this test RED (the stale row applies)."""
    uid = _seed_local_user(registry_env, row_version=5, email="original@x.com")
    before = _fetch_user(registry_env, uid)

    service = _registry_service(registry_env)
    stale_ev = _user_event(uid, event_type="update", row_version=3, email="STALE@x.com")
    conn = registry_env()
    try:
        service._apply_event(conn, stale_ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()

    after = _fetch_user(registry_env, uid)
    assert after == before, f"a LOWER row_version event changed the local row: {before} -> {after}"


def test_equal_incoming_row_version_is_discarded_byte_unchanged(registry_env):
    """The tie case, proven SEPARATELY from strictly-lower -- Decision 2 is
    explicit that a tie is discarded, not merely "not obviously newer"."""
    uid = _seed_local_user(registry_env, row_version=5, email="original@x.com")
    before = _fetch_user(registry_env, uid)

    service = _registry_service(registry_env)
    tie_ev = _user_event(uid, event_type="update", row_version=5, email="TIE@x.com")
    conn = registry_env()
    try:
        service._apply_event(conn, tie_ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()

    after = _fetch_user(registry_env, uid)
    assert after == before, f"an EQUAL row_version event changed the local row: {before} -> {after}"


# ── Task D.2 -- the ALLOW half, proven separately (this project has shipped
# a guard that denied everything and passed every deny test once already) ──

def test_genuinely_newer_row_version_applies_every_allowlisted_field(registry_env):
    """MUTATION-PROVEN as the counterpart to the deny-half test above: this
    is what would go RED if the guard were mutated to `WHERE 0` (deny
    everything) -- a mutation that leaves both deny-half tests GREEN."""
    uid = _seed_local_user(
        registry_env, row_version=5, email="original@x.com", employee_id="E1",
        role="cashier", status="active", require_password_change=1, language="en",
        password_hash="ORIGINAL_HASH", pin_hash="ORIGINAL_PIN",
        updated_at_utc="2026-01-01T00:00:00+00:00",
    )

    service = _registry_service(registry_env)
    newer_ev = _user_event(
        uid, event_type="update", row_version=6, email="new@x.com", employee_id="E1-new",
        role="manager", status="suspended", require_password_change=1, language="ar",
        password_hash="NEW_HASH", pin_hash="NEW_PIN", updated_at_utc="2026-03-01T00:00:00+00:00",
    )
    conn = registry_env()
    try:
        service._apply_event(conn, newer_ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()

    after = _fetch_user(registry_env, uid)
    assert after["email"] == "new@x.com"
    assert after["employee_id"] == "E1-new"
    assert after["role"] == "manager"
    assert after["status"] == "suspended"
    assert after["language"] == "ar"
    assert after["password_hash"] == "NEW_HASH"
    assert after["pin_hash"] == "NEW_PIN"
    assert after["row_version"] == 6
    assert after["updated_at_utc"] == "2026-03-01T00:00:00+00:00"


def test_create_event_for_a_brand_new_uid_inserts_a_local_row(registry_env):
    """The INSERT half -- a uid never seen on this device before. Gets a
    FRESH local `id` (never the sending device's own), and every allowlisted
    field lands exactly as sent."""
    service = _registry_service(registry_env)
    new_uid = str(uuid.uuid4())
    ev = _user_event(new_uid, event_type="create", row_version=1, session_version=1)
    conn = registry_env()
    try:
        service._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()

    row = _fetch_user(registry_env, new_uid)
    assert row is not None
    assert row["id"], "a brand-new local id must have been minted"
    assert row["email"] == "updated@x.com"
    assert row["company_id"] == RECEIVER_COMPANY_ID
    assert row["row_version"] == 1


# ── Task D.3 -- session_version: MAX(local, incoming), never downward ──────

def test_session_version_increases_when_incoming_is_higher(registry_env):
    uid = _seed_local_user(registry_env, row_version=5, session_version=3)
    service = _registry_service(registry_env)
    ev = _user_event(uid, event_type="update", row_version=6, session_version=10)
    conn = registry_env()
    try:
        service._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()
    assert _fetch_user(registry_env, uid)["session_version"] == 10


def test_session_version_never_decreases_even_on_an_accepted_newer_row(registry_env):
    """MUTATION-PROVEN: the decisive case. The row_version gate accepts this
    update (6 > 5) so every OTHER allowlisted field moves -- but the
    payload's OWN session_version (2) is LOWER than what is already stored
    locally (8). A plain `session_version=excluded.session_version` (instead
    of the MAX(...) expression) turns this test RED: session_version would
    drop to 2, silently un-revoking whatever forced the higher value in the
    first place."""
    uid = _seed_local_user(registry_env, row_version=5, session_version=8)
    service = _registry_service(registry_env)
    ev = _user_event(uid, event_type="update", row_version=6, session_version=2)
    conn = registry_env()
    try:
        service._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()
    after = _fetch_user(registry_env, uid)
    assert after["row_version"] == 6, "the row_version-accepted fields must still have moved"
    assert after["session_version"] == 8, (
        f"session_version moved DOWNWARD from 8 to {after['session_version']} on an accepted update"
    )


def test_session_version_replayed_identical_event_does_not_change_it(registry_env):
    uid = _seed_local_user(registry_env, row_version=5, session_version=3)
    service = _registry_service(registry_env)
    ev = _user_event(uid, event_type="update", row_version=6, session_version=4)
    conn = registry_env()
    try:
        service._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
        assert _fetch_user(registry_env, uid)["session_version"] == 4
        # Replayed verbatim -- row_version is now EQUAL to local (6), so the
        # whole update (including session_version) is a no-op, not merely
        # "session_version happens to end up the same value again".
        service._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()
    assert _fetch_user(registry_env, uid)["session_version"] == 4


def test_session_version_reversed_delivery_order_never_dips_below_its_starting_value(registry_env):
    """Two DIFFERENT events for the same uid (rv=6/sv=4 and rv=7/sv=2),
    applied in a REVERSED order (the higher row_version arrives FIRST). The
    second (now-stale, rv=6) event is correctly discarded whole by the
    row_version gate -- so session_version never reaches the batch's overall
    max (4) on this ordering, but it must NEVER fall below its value at the
    start of the batch (3) at any point, on ANY delivery order."""
    uid = _seed_local_user(registry_env, row_version=5, session_version=3)
    service = _registry_service(registry_env)
    ev_a = _user_event(uid, event_type="update", row_version=6, session_version=4)
    ev_b = _user_event(uid, event_type="update", row_version=7, session_version=2)

    conn = registry_env()
    try:
        # Reversed: higher row_version first, then the now-stale lower one.
        service._apply_event(conn, ev_b, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
        mid = _fetch_user(registry_env, uid)
        assert mid["session_version"] >= 3, "session_version dipped below its pre-batch value"

        service._apply_event(conn, ev_a, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()

    final = _fetch_user(registry_env, uid)
    assert final["session_version"] >= 3, (
        f"session_version ended BELOW its pre-batch value: {final['session_version']}"
    )
    assert final["row_version"] == 7, "the stale (lower) row_version event must not have applied at all"


def test_session_version_normal_delivery_order_reaches_the_batch_max(registry_env):
    """The SAME two events as the reversed-order test above, delivered in
    their natural (ascending row_version) order -- both apply, and
    session_version reaches the true batch maximum (4)."""
    uid = _seed_local_user(registry_env, row_version=5, session_version=3)
    service = _registry_service(registry_env)
    ev_a = _user_event(uid, event_type="update", row_version=6, session_version=4)
    ev_b = _user_event(uid, event_type="update", row_version=7, session_version=2)

    conn = registry_env()
    try:
        service._apply_event(conn, ev_a, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
        service._apply_event(conn, ev_b, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()

    final = _fetch_user(registry_env, uid)
    assert final["row_version"] == 7
    assert final["session_version"] == 4, "session_version must reach MAX(3,4,2)=4 in ascending order"


# ── Task D.4 -- clinic_role / failed_login_count / locked_until are NEVER
# written by this branch, in either direction, even when the payload tries ──

def test_clinic_role_is_byte_unchanged_after_a_full_sync_round(registry_env):
    uid = _seed_local_user(registry_env, row_version=5, clinic_role="doctor")
    service = _registry_service(registry_env)
    # Hostile/malformed payload carrying a DIFFERENT clinic_role -- must be
    # ignored outright; this branch never even reads the key.
    ev = _user_event(uid, event_type="update", row_version=6, clinic_role="nurse")
    conn = registry_env()
    try:
        service._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()
    assert _fetch_user(registry_env, uid)["clinic_role"] == "doctor"


def test_a_live_lockout_is_not_cleared_by_an_unrelated_sync_update(registry_env):
    """MUTATION-PROVEN: the exact scenario Decision 3 names -- an idle till
    syncing this user's OTHER fields (role/email/etc.) must not clear a
    lockout a DIFFERENT, currently-under-attack till just set. Including
    `failed_login_count`/`locked_until` in the branch's column list (even
    with 'sensible' values) turns this test RED."""
    uid = _seed_local_user(
        registry_env, row_version=5, failed_login_count=5,
        locked_until="2099-01-01T00:00:00+00:00",
    )
    service = _registry_service(registry_env)
    # The payload explicitly tries to clear the lockout -- as a hostile
    # payload, or as what a naive "sync everything" implementation would
    # send from a device that never saw the lockout happen.
    ev = _user_event(
        uid, event_type="update", row_version=6,
        failed_login_count=0, locked_until=None,
    )
    conn = registry_env()
    try:
        service._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()
    after = _fetch_user(registry_env, uid)
    assert after["row_version"] == 6, "the row_version-accepted fields must still have moved"
    assert after["failed_login_count"] == 5, "a live lockout's failed_login_count was cleared by sync"
    assert after["locked_until"] == "2099-01-01T00:00:00+00:00", "a live lockout was cleared by sync"


# ── Task D.5 -- the two non-wire UNIQUE constraints are quarantined, never
# allowed to raise and wedge the batch (wave A's defect #1, reproduced) ────

def test_duplicate_email_from_a_second_device_is_quarantined(registry_env):
    existing_uid = _seed_local_user(registry_env, email="shared@x.com", employee_id="E1")
    service = _registry_service(registry_env)
    new_uid = str(uuid.uuid4())
    dup_ev = _user_event(
        new_uid, event_type="create", row_version=1,
        email="shared@x.com", employee_id="E2",
    )
    conn = registry_env()
    try:
        handled = service._apply_event(conn, dup_ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()

    assert handled is False, "_apply_event must report the event as NOT applied (parked)"
    assert _fetch_user(registry_env, new_uid) is None, "the duplicate must not have been inserted"
    rows = _quarantine_rows(registry_env)
    assert len(rows) == 1
    assert rows[0]["entity_id"] == new_uid
    assert rows[0]["entity_type"] == "user"
    assert rows[0]["reason"] == "duplicate_email"


def test_duplicate_employee_id_from_a_second_device_is_quarantined(registry_env):
    existing_uid = _seed_local_user(registry_env, email="a@x.com", employee_id="SHARED-EMP")
    service = _registry_service(registry_env)
    new_uid = str(uuid.uuid4())
    dup_ev = _user_event(
        new_uid, event_type="create", row_version=1,
        email="b@x.com", employee_id="SHARED-EMP",
    )
    conn = registry_env()
    try:
        handled = service._apply_event(conn, dup_ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()

    assert handled is False
    assert _fetch_user(registry_env, new_uid) is None
    rows = _quarantine_rows(registry_env)
    assert len(rows) == 1
    assert rows[0]["reason"] == "duplicate_employee_id"


def test_quarantine_does_not_wedge_the_cursor_and_unrelated_events_still_land(registry_env):
    """The whole point of quarantining rather than raising: via
    `apply_pull_result` (the real batch-apply entry point, not a single
    `_apply_event` call), a batch containing ONE poison (duplicate-email)
    event alongside a perfectly valid, unrelated one must still advance the
    cursor to the batch's returned value, and the valid event must still
    land -- proving the duplicate does not wedge anything behind it."""
    existing_uid = _seed_local_user(registry_env, email="shared@x.com", employee_id="E1")
    service = _registry_service(registry_env)

    poison_uid = str(uuid.uuid4())
    poison_ev = _user_event(
        poison_uid, event_type="create", row_version=1,
        email="shared@x.com", employee_id="E2",
    )
    valid_uid = str(uuid.uuid4())
    valid_ev = _user_event(
        valid_uid, event_type="create", row_version=1,
        email="brand-new@x.com", employee_id="E3",
    )

    conn = registry_env()
    try:
        service.apply_pull_result(conn, {"events": [poison_ev, valid_ev], "cursor": 55})
        conn.commit()
    finally:
        conn.close()

    assert service.read_cursor(registry_env()) == 55, "the cursor must advance past the poison event"
    assert _fetch_user(registry_env, valid_uid) is not None, "the unrelated valid event must still land"
    assert len(_quarantine_rows(registry_env)) == 1


def test_quarantined_duplicate_email_drains_once_the_conflict_is_resolved(registry_env):
    """The poison event stays parked until the underlying conflict is
    actually resolved (here: the operator renames the conflicting local
    user's email), then the NEXT batch's retry pass picks it up and applies
    it -- proving the parked row is genuinely replayable, not merely
    inert."""
    existing_uid = _seed_local_user(registry_env, email="shared@x.com", employee_id="E1")
    service = _registry_service(registry_env)

    poison_uid = str(uuid.uuid4())
    poison_ev = _user_event(
        poison_uid, event_type="create", row_version=1,
        email="shared@x.com", employee_id="E2",
    )
    conn = registry_env()
    try:
        service.apply_pull_result(conn, {"events": [poison_ev], "cursor": 1})
        conn.commit()
    finally:
        conn.close()
    assert len(_quarantine_rows(registry_env)) == 1
    assert _fetch_user(registry_env, poison_uid) is None

    # Resolve the conflict: the operator (or a later sync) frees up the
    # email the quarantined event wants.
    conn = registry_env()
    try:
        conn.execute("UPDATE users SET email='renamed@x.com' WHERE uid=?", (existing_uid,))
        conn.commit()
    finally:
        conn.close()

    # Next pull cycle -- an empty batch is enough; _retry_quarantined_events
    # runs on every apply_pull_result call regardless of batch content.
    conn = registry_env()
    try:
        service.apply_pull_result(conn, {"events": [], "cursor": 2})
        conn.commit()
    finally:
        conn.close()

    assert _quarantine_rows(registry_env) == [], "the resolved event was left sitting in quarantine"
    assert _fetch_user(registry_env, poison_uid) is not None, "the resolved event was never actually applied"


# ── Task D.6 -- company_id is always the RECEIVER's own ────────────────────

def test_hostile_payload_company_id_is_ignored(registry_env):
    service = _registry_service(registry_env)
    new_uid = str(uuid.uuid4())
    hostile_ev = _user_event(
        new_uid, event_type="create", row_version=1,
        company_id=HOSTILE_COMPANY_ID,
    )
    conn = registry_env()
    try:
        service._apply_event(conn, hostile_ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()
    row = _fetch_user(registry_env, new_uid)
    assert row is not None
    assert row["company_id"] == RECEIVER_COMPANY_ID, (
        f"row was stamped with the payload's hostile company_id: {row['company_id']!r}"
    )


# ── Task D.7 -- cross-stream isolation: a `user` event is ignored by a
# retail-configured instance, applied by a registry-configured one, cursor
# advances on BOTH ─────────────────────────────────────────────────────────

_RETAIL_LIKE_SCHEMA = """
CREATE TABLE sync_outbox (
    id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL, payload TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE sync_cursor (
    id INTEGER PRIMARY KEY CHECK (id = 1), last_seq INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO sync_cursor (id, last_seq) VALUES (1, 0);
CREATE TABLE sync_apply_quarantine (
    entity_id TEXT NOT NULL, entity_type TEXT NOT NULL, event_type TEXT NOT NULL,
    payload TEXT NOT NULL, reason TEXT NOT NULL, detail TEXT,
    quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entity_id, event_type)
);
"""
# Deliberately NO `users` table at all -- retail.db genuinely has none. If
# the cross-stream guard ever failed, applying a `user` event here would
# raise `sqlite3.OperationalError: no such table: users`, exactly wave A's
# defect #1 reproduced by construction.


@pytest.fixture
def retail_like_conn_factory(tmp_path):
    db_path = tmp_path / "retail_like.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_RETAIL_LIKE_SCHEMA)
    conn.commit()
    conn.close()

    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    return _get_conn


class _FakeRelayClient:
    def __init__(self, pull_response):
        self._pull_response = pull_response

    def pull(self, since):
        return self._pull_response


def test_user_event_is_ignored_by_a_retail_configured_instance_cursor_still_advances(
    retail_like_conn_factory,
):
    from commercial_runtime.sync.sync_service import RETAIL_SYNC_ENTITY_TYPES

    ev = _user_event(str(uuid.uuid4()), event_type="create", row_version=1)
    client = _FakeRelayClient({"events": [ev], "cursor": 9})
    service = SyncService(
        client_factory=lambda: client, get_conn=retail_like_conn_factory,
        local_company_id_provider=lambda: RECEIVER_COMPANY_ID,
        handled_entity_types=RETAIL_SYNC_ENTITY_TYPES,
    )

    service.pull_once()  # must NOT raise "no such table: users"

    conn = retail_like_conn_factory()
    try:
        cursor = conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()[0]
        quarantined = conn.execute("SELECT COUNT(*) FROM sync_apply_quarantine").fetchone()[0]
    finally:
        conn.close()
    assert cursor == 9, "the cursor must still advance past a foreign-stream event"
    assert quarantined == 0, "a foreign-stream event must never be parked -- it simply belongs elsewhere"


def test_the_same_user_event_is_applied_by_a_registry_configured_instance(registry_env):
    uid = str(uuid.uuid4())
    ev = _user_event(uid, event_type="create", row_version=1)
    client = _FakeRelayClient({"events": [ev], "cursor": 9})
    service = SyncService(
        client_factory=lambda: client, get_conn=registry_env,
        local_company_id_provider=lambda: RECEIVER_COMPANY_ID,
        handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES,
    )

    service.pull_once()

    assert _fetch_user(registry_env, uid) is not None, "the registry instance must have applied its own event"
    conn = registry_env()
    try:
        cursor = conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()[0]
    finally:
        conn.close()
    assert cursor == 9


# ── Task D.8 -- no credential ever appears in a quarantine row, a log
# record, or an exception message ───────────────────────────────────────────

def test_no_credential_appears_in_the_quarantine_detail_or_reason(registry_env):
    _seed_local_user(registry_env, email="shared@x.com", employee_id="E1")
    service = _registry_service(registry_env)
    new_uid = str(uuid.uuid4())
    dup_ev = _user_event(
        new_uid, event_type="create", row_version=1,
        email="shared@x.com", employee_id="E2",
        password_hash="SECRET_PASSWORD_HASH_VALUE", pin_hash="SECRET_PIN_HASH_VALUE",
    )
    conn = registry_env()
    try:
        service._apply_event(conn, dup_ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()

    rows = _quarantine_rows(registry_env)
    assert len(rows) == 1
    row = rows[0]
    for field_name in ("reason", "detail"):
        value = row[field_name] or ""
        assert "SECRET_PASSWORD_HASH_VALUE" not in value, f"password_hash leaked into quarantine {field_name}"
        assert "SECRET_PIN_HASH_VALUE" not in value, f"pin_hash leaked into quarantine {field_name}"


def test_no_credential_appears_in_a_log_record_during_the_quarantine_path(registry_env, caplog):
    _seed_local_user(registry_env, email="shared@x.com", employee_id="E1")
    service = _registry_service(registry_env)
    new_uid = str(uuid.uuid4())
    dup_ev = _user_event(
        new_uid, event_type="create", row_version=1,
        email="shared@x.com", employee_id="E2",
        password_hash="SECRET_PASSWORD_HASH_VALUE", pin_hash="SECRET_PIN_HASH_VALUE",
    )
    conn = registry_env()
    try:
        with caplog.at_level(logging.DEBUG):
            service._apply_event(conn, dup_ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()

    for record in caplog.records:
        assert "SECRET_PASSWORD_HASH_VALUE" not in record.getMessage()
        assert "SECRET_PIN_HASH_VALUE" not in record.getMessage()


def test_no_credential_appears_in_the_raw_integrity_error_message(registry_env):
    """Confirms directly (not merely assumed from SQLite documentation) that
    the caught `sqlite3.IntegrityError` -- the exact exception this branch's
    `except` block inspects by name -- never embeds a data value, only
    column names. This is what makes it safe for the branch to read
    `str(exc)` at all without redacting it first."""
    _seed_local_user(registry_env, email="shared@x.com", employee_id="E1")
    conn = registry_env()
    try:
        try:
            conn.execute(
                "INSERT INTO users (id, company_id, uid, employee_id, email, password_hash, row_version) "
                "VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), RECEIVER_COMPANY_ID, str(uuid.uuid4()), "E2", "shared@x.com",
                 "SECRET_PASSWORD_HASH_VALUE", 1),
            )
            raised = None
        except sqlite3.IntegrityError as exc:
            raised = str(exc)
    finally:
        conn.close()
    assert raised is not None, "fixture bug: expected a UNIQUE constraint violation"
    assert "SECRET_PASSWORD_HASH_VALUE" not in raised
    assert "users.email" in raised
