"""Phase 5 wave B2 stage 3 -- apply-side hardening for `SyncService.
_apply_event`'s `user_permission` branch. See docs/launch-readiness/
phase5-waveb2-user-sync.md ("Decision 5 -- user_permissions is in scope")
and commercial_runtime/sync/sync_service.py's `user_permission` branch for
the full design.

Same technique as commercial_runtime/sync/tests/test_registry_user_sync_
apply.py (stage 2a, this file's sibling): every test here drives
`SyncService._apply_event`/`apply_pull_result` directly against a REAL,
isolated registry.db (built through the real `init_registry_db()` path),
rather than mocking sqlite. `SyncService.__init__`'s `handled_entity_types`
MUST be given `REGISTRY_SYNC_ENTITY_TYPES` (or a set containing
"user_permission") in every test here.

THE core proof this file exists for: `user_permissions.user_id` is a LOCAL
`users.id` the receiver mints fresh for every user it applies -- never the
wire identity. A `user_permission` payload therefore carries the owning
user's `uid`, and this branch resolves it to THIS device's own local
`users.id` before writing anything. Resolving to the WRONG local row would
be a silent privilege change -- proven below with TWO users present on the
receiver, so a resolve-to-the-wrong-row bug cannot hide behind there being
only one candidate.

Run (ONE FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest commercial_runtime/sync/tests/test_registry_user_permission_sync_apply.py -v
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from commercial_runtime.identity import registry_db
from commercial_runtime.sync.sync_service import REGISTRY_SYNC_ENTITY_TYPES, SyncService

RECEIVER_COMPANY_ID = "company-x"


@pytest.fixture
def registry_env(tmp_path, monkeypatch):
    """A real registry.db, built by the real `init_registry_db()` path, at
    the CURRENT REGISTRY_SCHEMA_VERSION -- identical fixture shape to
    test_registry_user_sync_apply.py's own `registry_env`."""
    db_dir = tmp_path / "database"
    db_path = db_dir / "registry.db"
    monkeypatch.setattr(registry_db, "_db_dir", str(db_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(db_path))
    registry_db.init_registry_db()
    return registry_db.get_conn


def _seed_local_user(get_conn, **overrides):
    """Inserts one user row directly (bypassing sync entirely) with sane
    defaults, then returns its uid. `overrides` replaces any column by
    name -- identical shape to test_registry_user_sync_apply.py's own
    helper of the same name."""
    row = {
        "id": str(uuid.uuid4()),
        "company_id": RECEIVER_COMPANY_ID,
        "employee_id": f"E-{uuid.uuid4().hex[:8]}",
        "email": f"{uuid.uuid4().hex[:8]}@x.com",
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
        "pin_hash": None,
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


def _local_id(get_conn, uid):
    conn = get_conn()
    try:
        row = conn.execute("SELECT id FROM users WHERE uid=?", (uid,)).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def _seed_local_permission(get_conn, user_id, subsystem, access_level):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), user_id, subsystem, access_level),
        )
        conn.commit()
    finally:
        conn.close()


def _fetch_permission(get_conn, user_id, subsystem):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT access_level FROM user_permissions WHERE user_id=? AND subsystem=?",
            (user_id, subsystem),
        ).fetchone()
        return row["access_level"] if row else None
    finally:
        conn.close()


def _perm_event(user_uid, subsystem, event_type="update", access_level="full", **overrides):
    payload = {"user_uid": user_uid, "subsystem": subsystem}
    if event_type != "delete":
        payload["access_level"] = access_level
    payload.update(overrides)
    return {
        "entity_type": "user_permission", "entity_id": str(uuid.uuid4()),
        "event_type": event_type, "payload": payload,
    }


def _user_event(uid, event_type="create", **overrides):
    """Minimal `user` entity event -- used only to simulate "the owning
    user's own create/update event arrives", for the quarantine-drain test
    below. Deliberately NOT imported from test_registry_user_sync_apply.py
    -- this file is self-contained, matching this codebase's convention
    (test_user_sync_emit_two_device_roundtrip.py duplicates its own device-
    harness helpers rather than importing them, for the same reason)."""
    payload = {
        "uid": uid, "employee_id": f"E-{uid[:6]}", "email": f"{uid[:6]}@x.com",
        "role": "cashier", "status": "active", "require_password_change": 0,
        "language": "en", "password_hash": "H", "pin_hash": None,
        "row_version": 1, "updated_at_utc": "2026-01-01T00:00:00+00:00",
        "session_version": 1,
    }
    payload.update(overrides)
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


# ── C.1 -- THE core trap: two users present, a permission for A never
# lands on B ─────────────────────────────────────────────────────────────

def test_two_users_present_a_permission_for_user_a_never_touches_user_b(registry_env):
    """Proven with TWO users present so a resolve-to-the-wrong-row bug
    cannot hide behind there being only one candidate. See this task's own
    report for the verbatim mutation-proof output: temporarily replacing
    `_local_id_by_uid(conn, "users", user_uid)` in sync_service.py's
    `user_permission` branch with a query that returns ANY local user
    (`SELECT id FROM users LIMIT 1`) turns this test RED -- user B's row
    gains the grant meant only for user A."""
    uid_a = _seed_local_user(registry_env, email="a@x.com", employee_id="E-A")
    uid_b = _seed_local_user(registry_env, email="b@x.com", employee_id="E-B")
    service = _registry_service(registry_env)
    local_id_a = _local_id(registry_env, uid_a)
    local_id_b = _local_id(registry_env, uid_b)

    # BOTH directions, deliberately -- a resolver that always picks
    # "whichever user happens to be first in an unordered SELECT" would
    # pass a single one-user-only assertion by coincidence of insertion
    # order (it would just always resolve to A). Addressing an event to B
    # SECOND is what makes a "resolves to a fixed row regardless of the
    # payload's own uid" bug observable, not merely an "always picks the
    # last-seeded row" one.
    ev_a = _perm_event(uid_a, "retail.discount", event_type="create", access_level="full")
    ev_b = _perm_event(uid_b, "retail.reports", event_type="create", access_level="full")
    conn = registry_env()
    try:
        handled_a = service._apply_event(conn, ev_a, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
        handled_b = service._apply_event(conn, ev_b, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()

    assert handled_a is True and handled_b is True
    assert _fetch_permission(registry_env, local_id_a, "retail.discount") == "full", \
        "the permission never landed on the CORRECT user"
    assert _fetch_permission(registry_env, local_id_b, "retail.discount") is None, \
        "the permission meant for user A landed on user B instead -- a silent privilege change"
    assert _fetch_permission(registry_env, local_id_b, "retail.reports") == "full", \
        "the permission never landed on the CORRECT user"
    assert _fetch_permission(registry_env, local_id_a, "retail.reports") is None, \
        "the permission meant for user B landed on user A instead -- a silent privilege change"


# ── C.2 -- an unresolvable user uid quarantines; cursor advances; unrelated
# events in the batch still land; it drains once the user arrives ──────────

def test_unresolvable_user_uid_quarantines(registry_env):
    service = _registry_service(registry_env)
    orphan_uid = str(uuid.uuid4())
    orphan_ev = _perm_event(orphan_uid, "retail.sell", event_type="create", access_level="full")

    conn = registry_env()
    try:
        handled = service._apply_event(conn, orphan_ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()

    assert handled is False, "_apply_event must report the event as NOT applied (parked)"
    rows = _quarantine_rows(registry_env)
    assert len(rows) == 1
    assert rows[0]["entity_type"] == "user_permission"
    assert rows[0]["reason"] == "missing_parent:user"
    assert orphan_uid in rows[0]["detail"], "the detail must name the unresolved uid for an operator to act on"


def test_quarantine_does_not_wedge_the_cursor_and_unrelated_events_still_land(registry_env):
    service = _registry_service(registry_env)
    orphan_uid = str(uuid.uuid4())
    orphan_ev = _perm_event(orphan_uid, "retail.sell", event_type="create", access_level="full")

    valid_uid = _seed_local_user(registry_env, email="valid@x.com", employee_id="E-V")
    valid_ev = _perm_event(valid_uid, "retail.sell", event_type="create", access_level="full")

    conn = registry_env()
    try:
        service.apply_pull_result(conn, {"events": [orphan_ev, valid_ev], "cursor": 42})
        conn.commit()
    finally:
        conn.close()

    assert service.read_cursor(registry_env()) == 42, "the cursor must advance past the poison event"
    local_id_valid = _local_id(registry_env, valid_uid)
    assert _fetch_permission(registry_env, local_id_valid, "retail.sell") == "full", \
        "the unrelated valid event in the SAME batch must still land"
    assert len(_quarantine_rows(registry_env)) == 1


def test_quarantined_permission_drains_once_the_owning_user_arrives(registry_env):
    """The owning user's OWN `user` create event arrives in the SAME batch
    as the retry pass -- `apply_pull_result` retries quarantined events
    AFTER the batch's own events (see that method's own comment), so this
    also proves the parked permission resolves in the SAME tick its parent
    finally shows up, not just eventually."""
    service = _registry_service(registry_env)
    late_uid = str(uuid.uuid4())
    perm_ev = _perm_event(late_uid, "retail.reports", event_type="create", access_level="full")

    conn = registry_env()
    try:
        service.apply_pull_result(conn, {"events": [perm_ev], "cursor": 1})
        conn.commit()
    finally:
        conn.close()
    assert len(_quarantine_rows(registry_env)) == 1

    conn = registry_env()
    try:
        service.apply_pull_result(conn, {"events": [_user_event(late_uid)], "cursor": 2})
        conn.commit()
    finally:
        conn.close()

    assert _quarantine_rows(registry_env) == [], "the resolved permission was left sitting in quarantine"
    local_id = _local_id(registry_env, late_uid)
    assert _fetch_permission(registry_env, local_id, "retail.reports") == "full", \
        "the resolved permission was never actually applied"


# ── The allow half, proven separately from the deny half: a genuinely
# resolvable create/update actually applies ─────────────────────────────────

def test_create_event_grants_the_correct_access_level(registry_env):
    uid = _seed_local_user(registry_env)
    service = _registry_service(registry_env)
    ev = _perm_event(uid, "retail.stock.adjust", event_type="create", access_level="full")
    conn = registry_env()
    try:
        service._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()
    local_id = _local_id(registry_env, uid)
    assert _fetch_permission(registry_env, local_id, "retail.stock.adjust") == "full"


def test_update_event_changes_an_existing_grants_access_level(registry_env):
    uid = _seed_local_user(registry_env)
    local_id = _local_id(registry_env, uid)
    _seed_local_permission(registry_env, local_id, "retail.discount", "none")
    service = _registry_service(registry_env)
    ev = _perm_event(uid, "retail.discount", event_type="update", access_level="full")
    conn = registry_env()
    try:
        service._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()
    assert _fetch_permission(registry_env, local_id, "retail.discount") == "full"


def test_update_event_can_also_revoke_down_to_none(registry_env):
    """The mirror-image direction of the test above -- an upsert that only
    ever moved 'none' -> 'full' would be exactly as broken as one that only
    ever moved 'full' -> 'none'; both directions are proven."""
    uid = _seed_local_user(registry_env)
    local_id = _local_id(registry_env, uid)
    _seed_local_permission(registry_env, local_id, "retail.discount", "full")
    service = _registry_service(registry_env)
    ev = _perm_event(uid, "retail.discount", event_type="update", access_level="none")
    conn = registry_env()
    try:
        service._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()
    assert _fetch_permission(registry_env, local_id, "retail.discount") == "none"


# ── C.5 -- deletes: an existing grant is removed; an absent row is a
# no-op, never an error ──────────────────────────────────────────────────

def test_delete_event_removes_an_existing_local_grant(registry_env):
    uid = _seed_local_user(registry_env)
    local_id = _local_id(registry_env, uid)
    _seed_local_permission(registry_env, local_id, "retail.cash.close", "full")
    service = _registry_service(registry_env)
    ev = _perm_event(uid, "retail.cash.close", event_type="delete")
    conn = registry_env()
    try:
        handled = service._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()
    assert handled is True
    assert _fetch_permission(registry_env, local_id, "retail.cash.close") is None


def test_delete_event_for_an_absent_row_is_a_no_op_not_an_error(registry_env):
    uid = _seed_local_user(registry_env)
    local_id = _local_id(registry_env, uid)
    service = _registry_service(registry_env)
    ev = _perm_event(uid, "retail.cash.close", event_type="delete")  # never existed locally
    conn = registry_env()
    try:
        handled = service._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)  # must not raise
        conn.commit()
    finally:
        conn.close()
    assert handled is True
    assert _fetch_permission(registry_env, local_id, "retail.cash.close") is None


# ── Malformed / unrecognised shapes never raise and wedge the batch ────────

def test_unrecognised_event_type_on_user_permission_is_ignored_not_an_error(registry_env):
    uid = _seed_local_user(registry_env)
    service = _registry_service(registry_env)
    ev = _perm_event(uid, "retail.sell", event_type="rename", access_level="full")  # bogus
    conn = registry_env()
    try:
        handled = service._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
        conn.commit()
    finally:
        conn.close()
    assert handled is True
    assert _fetch_permission(registry_env, _local_id(registry_env, uid), "retail.sell") is None


def test_missing_subsystem_is_skipped_not_raised(registry_env):
    """`user_permissions.subsystem` is NOT NULL -- a payload with no
    subsystem at all (unreachable from any real write site) must be
    skipped, never allowed to raise IntegrityError and wedge the batch."""
    uid = _seed_local_user(registry_env)
    service = _registry_service(registry_env)
    ev = _perm_event(uid, None, event_type="create", access_level="full")
    conn = registry_env()
    try:
        handled = service._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)  # must not raise
        conn.commit()
    finally:
        conn.close()
    assert handled is True


# ── Cross-stream isolation: identical shape to `user`'s own proof in
# test_registry_user_sync_apply.py -- a `user_permission` event is ignored
# by a retail-configured instance, applied by a registry-configured one,
# and the cursor advances on BOTH ────────────────────────────────────────

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
# Deliberately NO `users`/`user_permissions` table at all -- retail.db
# genuinely has neither. If the cross-stream guard ever failed, applying a
# `user_permission` event here would raise `sqlite3.OperationalError: no
# such table: users` (this branch resolves `users` FIRST), exactly wave A's
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


def test_user_permission_event_is_ignored_by_a_retail_configured_instance(retail_like_conn_factory):
    from commercial_runtime.sync.sync_service import RETAIL_SYNC_ENTITY_TYPES

    ev = _perm_event(str(uuid.uuid4()), "retail.sell", event_type="create", access_level="full")
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


def test_the_same_user_permission_event_is_applied_by_a_registry_configured_instance(registry_env):
    uid = _seed_local_user(registry_env)
    ev = _perm_event(uid, "retail.sell", event_type="create", access_level="full")
    client = _FakeRelayClient({"events": [ev], "cursor": 9})
    service = SyncService(
        client_factory=lambda: client, get_conn=registry_env,
        local_company_id_provider=lambda: RECEIVER_COMPANY_ID,
        handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES,
    )

    service.pull_once()

    local_id = _local_id(registry_env, uid)
    assert _fetch_permission(registry_env, local_id, "retail.sell") == "full", \
        "the registry instance must have applied its own event"
    conn = registry_env()
    try:
        cursor = conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()[0]
    finally:
        conn.close()
    assert cursor == 9


# ── D5 -- the residual last-writer-wins risk, DOCUMENTED as a known,
# accepted limitation, not asserted as desired behaviour ────────────────────

def test_residual_last_writer_wins_is_a_known_documented_limitation(registry_env):
    """This test does NOT assert desired behaviour -- it pins the actual,
    ACCEPTED resolution rule (design Decision 5) so the risk stays visible
    rather than being silently discovered later.

    `user_permissions` carries no version column (unlike `users`' own
    `row_version`), so two edits to the SAME (user, subsystem) pair
    resolve to WHICHEVER ARRIVES LAST LOCALLY -- never to "whichever was
    made later by wall-clock time", because there is no wall-clock (or any
    other) tiebreaker available on this table to arbitrate that. Accepted
    because: (1) this product's model is a single admin account, so two
    admins concurrently editing the SAME person's SAME subsystem is not a
    realistic operating shape; (2) permission edits are rare and
    deliberate, never a high-frequency path; (3) every permission change
    already bumps the owning user's `session_version`, forcing a re-login
    through which the admin sees the actual resulting state.

    Proven in BOTH arrival orders below, so this is genuinely "whichever
    arrives last", not an accidental artefact of one particular ordering."""
    uid = _seed_local_user(registry_env)
    local_id = _local_id(registry_env, uid)
    service = _registry_service(registry_env)

    grant_then_revoke = [
        _perm_event(uid, "retail.discount", event_type="update", access_level="full"),
        _perm_event(uid, "retail.discount", event_type="update", access_level="none"),
    ]
    conn = registry_env()
    try:
        for ev in grant_then_revoke:
            service._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
            conn.commit()
    finally:
        conn.close()
    assert _fetch_permission(registry_env, local_id, "retail.discount") == "none", (
        "whichever event ARRIVES LAST wins -- there is no version column to arbitrate otherwise"
    )

    revoke_then_grant = [
        _perm_event(uid, "retail.discount", event_type="update", access_level="none"),
        _perm_event(uid, "retail.discount", event_type="update", access_level="full"),
    ]
    conn = registry_env()
    try:
        for ev in revoke_then_grant:
            service._apply_event(conn, ev, local_company_id=RECEIVER_COMPANY_ID)
            conn.commit()
    finally:
        conn.close()
    assert _fetch_permission(registry_env, local_id, "retail.discount") == "full", (
        "reversing arrival order reverses the winner -- exactly the residual risk Decision 5 accepts"
    )
