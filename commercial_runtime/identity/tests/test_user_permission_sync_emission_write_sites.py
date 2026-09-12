"""Phase 5 wave B2 stage 3 -- the EMIT side for `user_permission`. See
docs/launch-readiness/phase5-waveb2-user-sync.md ("Decision 5 -- user_
permissions is in scope") and commercial_runtime/identity/user_accounts.py's
`_queue_user_permission_sync_event`/`seed_capabilities_for_user` docstrings
for the full design.

Sibling to commercial_runtime/identity/tests/test_user_sync_emission_write_
sites.py (stage 2b, the `user` entity's own emit-side suite) -- same
technique: drives the REAL Flask routes end to end against a real, isolated
registry.db built through the real `init_registry_db()` path, then inspects
`sync_outbox` directly to see what each site actually queued.

The exhaustive write-site table (task B):

  onboarding_routes.py:162  (create_admin re-onboard wipe)     -- EMITS delete
  onboarding_routes.py:191  (create_admin seed)                -- EMITS create
  onboarding_routes.py:688  (create_employee explicit perms)   -- EMITS create
  onboarding_routes.py:696  (create_employee seed)             -- EMITS create
  onboarding_routes.py:898  (update_role capability reset)     -- EMITS delete
  onboarding_routes.py:901  (update_role reseed)                -- EMITS create
  onboarding_routes.py:1090 (update_perms delete)               -- EMITS delete
  onboarding_routes.py:1091 (update_perms insert)                -- EMITS update
  account_schema.py's _seed_capability_rows (v3 migration step) -- NEVER emits

Run (ONE FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest commercial_runtime/identity/tests/test_user_permission_sync_emission_write_sites.py -v
"""
from __future__ import annotations

import json
import sqlite3
import uuid

import pytest
from flask import Flask

from commercial_runtime.identity import (
    auth_routes,
    mt_auth,
    onboarding_routes,
    registry_db,
    user_accounts,
)
from commercial_runtime.identity.auth_routes import auth_bp
from commercial_runtime.identity.onboarding_routes import onboarding_bp
from commercial_runtime.licensing_contracts import flask_guard
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRecord


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(path))
    monkeypatch.setattr(registry_db, "_db_dir", str(tmp_path))
    registry_db.init_registry_db()
    return path


@pytest.fixture
def app(db_path, tmp_path, monkeypatch):
    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(onboarding_routes, "get_conn", _get_conn)
    monkeypatch.setattr(registry_db, "get_conn", _get_conn)
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path))
    monkeypatch.setattr(mt_auth, "REGISTRY_DB", str(db_path))
    # The emit sites this file exercises (create_employee/update_role/
    # update_perms) now carry a licence gate too (AUDIT: account
    # administration had none). Same monkeypatch, same reasoning, as
    # test_employee_admin_routes.py's own `app` fixture.
    monkeypatch.setattr(flask_guard.LicenseStateRepository, "__init__", lambda self, db_path: None)
    monkeypatch.setattr(
        flask_guard.LicenseStateRepository, "load",
        lambda self: LicenseStateRecord(
            licensing_schema_version=1, product_code="AURA_TEST", platform="WINDOWS",
            current_state="ACTIVE_ONLINE",
        ),
    )

    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"
    flask_app.testing = True
    flask_app.register_blueprint(onboarding_bp)
    flask_app.register_blueprint(auth_bp)
    return flask_app


@pytest.fixture
def admin(app):
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner@test.local', 'password': 'OwnerPW11',
    })
    assert r.status_code == 200, r.get_json()
    return client


def _raw_conn(db_path):
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    return c


def _outbox_rows(db_path):
    conn = _raw_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT id, entity_type, entity_id, event_type, payload, created_at "
            "FROM sync_outbox ORDER BY rowid"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out
    finally:
        conn.close()


def _user_row(db_path, user_id):
    conn = _raw_conn(db_path)
    try:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _make_employee(admin, role=None):
    email = f'emp-{uuid.uuid4().hex[:8]}@test.local'
    body = {'email': email}
    if role:
        body['role'] = role
    r = admin.post('/api/admin/employees', json=body)
    assert r.status_code == 200, r.get_json()
    employees = admin.get('/api/admin/employees').get_json()['employees']
    row = next(e for e in employees if e['email'] == email)
    return row['id'], email, r.get_json()['setup_link']


def _perm_events(rows):
    return [r for r in rows if r["entity_type"] == "user_permission"]


# ── create_admin's OWN seed (onboarding_routes.py:191) -- EMITS create ─────

def test_create_admin_seeds_and_emits_capability_rows_for_the_owner(admin, db_path):
    session_user = admin.get('/api/auth/session').get_json()['user']
    admin_row = _user_row(db_path, session_user['id'])

    perm_events = _perm_events(_outbox_rows(db_path))
    assert len(perm_events) == 8, f"expected one event per capability code, got {perm_events}"
    assert {e["payload"]["subsystem"] for e in perm_events} == set(user_accounts.CAPABILITY_CODES)
    for e in perm_events:
        assert e["event_type"] == "create"
        assert e["payload"]["user_uid"] == admin_row["uid"], "payload must carry uid, never the local user_id"
        assert "user_id" not in e["payload"], "the LOCAL user_id must never appear in the payload (THE TRAP)"
        assert e["payload"]["access_level"] == "full", "the owner is granted every capability"
        assert "password_hash" not in e["payload"] and "pin_hash" not in e["payload"]


# ── create_admin's RE-ONBOARD wipe (onboarding_routes.py:162) -- EMITS
# delete for the OLD admin's grants ─────────────────────────────────────────

def test_create_admin_reonboard_wipe_emits_deletes_for_the_old_admins_grants(admin, db_path):
    """The re-onboarding branch (create-admin called again while an
    existing admin's password_hash is still the PENDING placeholder)
    hard-deletes the old admin's `user_permissions` rows. Task D4 requires
    each deleted grant to reach any device that already synced it, keyed
    by (old admin's uid, subsystem) -- proven here by reading the emitted
    delete events' payloads back."""
    session_user = admin.get('/api/auth/session').get_json()['user']
    old_row = _user_row(db_path, session_user['id'])
    old_uid = old_row['uid']

    # `create_admin`'s own INSERT never writes this sentinel itself -- some
    # other/legacy path is what could leave an admin in this state. Direct
    # SQL is the correct technique to reach a precondition no live route in
    # this phase produces, matching this codebase's own
    # retail_registry_v3_accounts_test.py convention of hand-building
    # fixture state that a migration/route must still handle correctly.
    conn = _raw_conn(db_path)
    try:
        conn.execute("UPDATE users SET password_hash='PENDING' WHERE id=?", (session_user['id'],))
        conn.commit()
    finally:
        conn.close()

    before_count = len(_outbox_rows(db_path))
    r = admin.post('/api/onboarding/create-admin', json={
        'name': 'New Owner', 'email': 'newowner@test.local', 'password': 'NewOwnerPW1',
    })
    assert r.status_code == 200, r.get_json()

    new_rows = _outbox_rows(db_path)[before_count:]
    delete_events = [
        e for e in new_rows
        if e["entity_type"] == "user_permission" and e["event_type"] == "delete"
        and e["payload"].get("user_uid") == old_uid
    ]
    assert len(delete_events) == 8, f"expected one delete per capability code for the OLD admin, got {new_rows}"
    assert {e["payload"]["subsystem"] for e in delete_events} == set(user_accounts.CAPABILITY_CODES)
    for e in delete_events:
        assert "access_level" not in e["payload"], "a delete payload must not carry a stale access_level"
        assert "user_id" not in e["payload"]

    conn = _raw_conn(db_path)
    try:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM user_permissions WHERE user_id=?", (session_user['id'],)
        ).fetchone()[0]
    finally:
        conn.close()
    assert remaining == 0, "fixture sanity: the old admin's permission rows must genuinely be gone locally"


# ── create_employee's explicit perms loop (:688) + its own seed (:696) --
# EMIT create for each ───────────────────────────────────────────────────

def test_create_employee_explicit_grant_and_seed_gap_fill_both_emit_create(admin, db_path):
    before = len(_outbox_rows(db_path))
    email = f'emp-{uuid.uuid4().hex[:8]}@test.local'
    r = admin.post('/api/admin/employees', json={
        'email': email, 'role': 'cashier', 'permissions': {'retail.discount': 'full'},
    })
    assert r.status_code == 200, r.get_json()
    employees = admin.get('/api/admin/employees').get_json()['employees']
    user_id = next(e for e in employees if e['email'] == email)['id']
    row = _user_row(db_path, user_id)

    new_rows = _outbox_rows(db_path)[before:]
    perm_events = _perm_events(new_rows)
    # The explicit grant (line 688's loop) and the seed's gap-fill (line
    # 696) target the SAME row for retail.discount -- INSERT then INSERT OR
    # IGNORE -- so this is exactly 8, never 9: one event per capability
    # code, not one per statement that touched it.
    assert len(perm_events) == 8, f"expected exactly 8 user_permission events, got {perm_events}"
    assert {e["payload"]["subsystem"] for e in perm_events} == set(user_accounts.CAPABILITY_CODES)
    discount_ev = next(e for e in perm_events if e["payload"]["subsystem"] == "retail.discount")
    assert discount_ev["event_type"] == "create"
    assert discount_ev["payload"]["access_level"] == "full", "the explicit grant is what must actually travel"
    # cashier's own default for retail.discount is 'none' -- if this read
    # 'none' it would mean the seed's gap-fill clobbered the caller's
    # explicit grant instead of skipping a row that already existed.
    for e in perm_events:
        assert e["payload"]["user_uid"] == row["uid"]
        assert "user_id" not in e["payload"]


# ── update_role's capability reset (:898) + reseed (:901) -- EMIT delete
# then create for all eight codes ────────────────────────────────────────

def test_update_role_emits_delete_then_create_for_every_capability_code(admin, db_path):
    user_id, _, _ = _make_employee(admin)
    before = len(_outbox_rows(db_path))

    r = admin.put(f'/api/admin/employees/{user_id}/role', json={'role': 'manager'})
    assert r.status_code == 200, r.get_json()

    new_rows = _outbox_rows(db_path)[before:]
    perm_events = _perm_events(new_rows)
    deletes = [e for e in perm_events if e["event_type"] == "delete"]
    creates = [e for e in perm_events if e["event_type"] == "create"]
    assert len(deletes) == 8, f"expected one delete per capability code, got {perm_events}"
    assert {e["payload"]["subsystem"] for e in deletes} == set(user_accounts.CAPABILITY_CODES)
    for e in deletes:
        assert "access_level" not in e["payload"]

    assert len(creates) == 8, f"expected one reseed create per capability code, got {perm_events}"
    manager_caps = user_accounts.capabilities_for_role('manager')
    for e in creates:
        expected = 'full' if e["payload"]["subsystem"] in manager_caps else 'none'
        assert e["payload"]["access_level"] == expected, (
            f"{e['payload']['subsystem']} should be {expected} for a manager"
        )

    # deletes precede creates in code order (the DELETE statement runs
    # before seed_capabilities_for_user), which is what a receiver applying
    # this batch in order actually sees.
    delete_positions = [i for i, e in enumerate(perm_events) if e["event_type"] == "delete"]
    create_positions = [i for i, e in enumerate(perm_events) if e["event_type"] == "create"]
    assert max(delete_positions) < min(create_positions)


# ── update_perms' delete (:1090) + insert (:1091) -- EMIT delete then
# update with the NEW access_level ──────────────────────────────────────

def test_update_perms_emits_delete_then_update_with_the_new_access_level(admin, db_path):
    user_id, _, _ = _make_employee(admin)
    row = _user_row(db_path, user_id)
    before = len(_outbox_rows(db_path))

    r = admin.post(f'/api/admin/employees/{user_id}/permissions', json={
        'subsystem': 'retail.cash.approve', 'access_level': 'full',
    })
    assert r.status_code == 200, r.get_json()

    new_rows = _outbox_rows(db_path)[before:]
    perm_events = _perm_events(new_rows)
    assert len(perm_events) == 2, f"expected exactly a delete+update pair, got {new_rows}"
    assert perm_events[0]["event_type"] == "delete"
    assert perm_events[0]["payload"]["subsystem"] == "retail.cash.approve"
    assert "access_level" not in perm_events[0]["payload"]
    assert perm_events[1]["event_type"] == "update"
    assert perm_events[1]["payload"]["subsystem"] == "retail.cash.approve"
    assert perm_events[1]["payload"]["access_level"] == "full"
    for e in perm_events:
        assert e["payload"]["user_uid"] == row["uid"]
        assert "user_id" not in e["payload"]

    # the user/update event (session_version bump) is queued AFTER both of
    # these, per onboarding_routes.update_perms's own code order.
    user_events = [r for r in new_rows if r["entity_type"] == "user"]
    assert len(user_events) == 1
    assert new_rows.index(user_events[0]) > new_rows.index(perm_events[1])


def test_update_perms_can_also_revoke_a_previously_full_grant(admin, db_path):
    """The allow half was proven above (none -> full via the default
    cashier grant); this proves the deny half separately -- a grant that
    already reads 'full' can be walked back down to 'none' too, and it is
    what actually travels, not silently skipped because a row already
    existed."""
    user_id, _, _ = _make_employee(admin, role='manager')  # manager defaults retail.discount=full
    row = _user_row(db_path, user_id)
    assert user_accounts.capabilities_for_role('manager') and 'retail.discount' in \
        user_accounts.capabilities_for_role('manager')
    before = len(_outbox_rows(db_path))

    r = admin.post(f'/api/admin/employees/{user_id}/permissions', json={
        'subsystem': 'retail.discount', 'access_level': 'none',
    })
    assert r.status_code == 200, r.get_json()

    perm_events = _perm_events(_outbox_rows(db_path)[before:])
    update_ev = next(e for e in perm_events if e["event_type"] == "update")
    assert update_ev["payload"]["access_level"] == "none"
    assert update_ev["payload"]["user_uid"] == row["uid"]


# ── Migration-context seeding (account_schema.py's v3 step) -- NEVER
# emits (task C.7) ────────────────────────────────────────────────────────

_V2_SCHEMA = """
CREATE TABLE users (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL, employee_id TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'employee', status TEXT DEFAULT 'active',
    require_password_change INTEGER DEFAULT 1, session_version INTEGER DEFAULT 1,
    language TEXT DEFAULT 'en', clinic_role TEXT DEFAULT '',
    failed_login_count INTEGER DEFAULT 0, locked_until TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company_id, employee_id)
);
CREATE TABLE user_permissions (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, subsystem TEXT NOT NULL,
    access_level TEXT DEFAULT 'none', UNIQUE(user_id, subsystem)
);
"""


def test_migration_context_seeding_emits_no_user_permission_events(tmp_path, monkeypatch):
    """The v3 migration step (`account_schema._seed_capability_rows`)
    backfills capability rows for accounts that ALREADY exist on THIS
    device -- it must never queue a sync event for them (task B, and
    account_schema.py's own explicit `emit_sync=False`). Exercised through
    the REAL `init_registry_db()` migration chain end to end -- a
    pre-existing v2-shaped database with a pre-existing user, upgraded to
    the current REGISTRY_SCHEMA_VERSION in one call, the same path every
    real upgrading install takes -- rather than calling the migration
    function directly, so this also proves the wiring, not just the
    function in isolation."""
    db_dir = tmp_path / "database"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "registry.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_V2_SCHEMA)
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role) VALUES (?,?,?,?,?,?)",
        ("u-existing", "c1", "EMP-0001", "existing@x.com", "hash", "employee"),
    )
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()

    monkeypatch.setattr(registry_db, "_db_dir", str(db_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(db_path))
    registry_db.init_registry_db()  # runs the FULL migration chain, v2 -> current

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        perm_count = conn.execute(
            "SELECT COUNT(*) FROM user_permissions WHERE user_id='u-existing'"
        ).fetchone()[0]
        assert perm_count == 8, "fixture sanity: the migration must still have SEEDED the rows locally"
        outbox_perm_events = conn.execute(
            "SELECT COUNT(*) FROM sync_outbox WHERE entity_type='user_permission'"
        ).fetchone()[0]
        assert outbox_perm_events == 0, "the v3 migration step must never queue a user_permission sync event"
        outbox_total = conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]
        assert outbox_total == 0, "the migration must not have queued ANY sync event for a pre-existing user"
    finally:
        conn.close()


def test_seed_capabilities_for_user_direct_call_defaults_to_no_emission(tmp_path):
    """`emit_sync` defaults to False -- the safe, backward-compatible
    posture for the many existing direct callers across `products/retail/
    tests` and `commercial_runtime/identity/tests` that build minimal
    hand-rolled schemas with no `sync_outbox`/`users` table at all and call
    this function purely for its capability-seeding side effect (e.g.
    retail_registry_v3_accounts_test.py's test_a_manager_gets_neither_
    owner_capability, which seeds against an in-memory db holding ONLY a
    `user_permissions` table). Proven directly here against that exact
    shape -- if the default were `True`, this call would raise
    `sqlite3.OperationalError: no such table: users` the moment it tried to
    read the owning user's uid."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE user_permissions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
        "subsystem TEXT NOT NULL, access_level TEXT DEFAULT 'none', UNIQUE(user_id, subsystem))"
    )
    # No `users` table and no `sync_outbox` table at all -- must not raise.
    inserted = user_accounts.seed_capabilities_for_user(conn, 'm1', 'manager')
    assert inserted == 8
    conn.close()
