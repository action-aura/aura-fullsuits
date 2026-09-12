"""Phase 5 wave B2 stage 2b -- the EMIT side. See docs/launch-readiness/
phase5-waveb2-user-sync.md and commercial_runtime/identity/user_accounts.py's
`_queue_user_sync_event`/`_touch_user` docstrings for the full design.

Stage 2a (commercial_runtime/sync/tests/test_registry_user_sync_apply.py)
proved the RECEIVING half. This file proves the SENDING half: every write
site enumerated in the stage 2b task report that changes an allowlisted
field queues exactly one `user` event, in the SAME transaction as the row
write, carrying the bumped `row_version`/`updated_at_utc` -- and every site
that must NOT emit (device-local security counters, a login-triggered hash
re-encoding of the SAME password, a non-allowlisted column) stays silent.

Drives the REAL Flask routes end to end (create-admin, create-employee,
role/status/clinic-role/permission changes, PIN set/clear, password
reset/setup, language change, login) against a real, isolated registry.db
built through the real `init_registry_db()` path -- same technique
test_employee_admin_routes.py already uses, extended with direct reads of
`sync_outbox` to inspect what each site actually queued.

Run (ONE FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest commercial_runtime/identity/tests/test_user_sync_emission_write_sites.py -v
"""
from __future__ import annotations

import hashlib
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
    verification,
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

    # onboarding_routes/auth_routes both bind `get_conn` (or import it
    # inline) by name -- patch the name each module actually resolves,
    # exactly like test_employee_admin_routes.py's own fixture.
    monkeypatch.setattr(onboarding_routes, "get_conn", _get_conn)
    monkeypatch.setattr(registry_db, "get_conn", _get_conn)
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path))
    monkeypatch.setattr(mt_auth, "REGISTRY_DB", str(db_path))
    # create-employee/role/status/clinic-role/permission/PIN routes this
    # file drives now carry a licence gate too (AUDIT: account
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


# ── Task C.1 / C.2 -- creation sites: emit `create`, row_version==1 ────────

def test_create_admin_emits_a_create_event(admin, db_path):
    """Phase 5 wave B2 stage 3 note on this test's shape: create-admin now
    ALSO seeds and emits the owner's own eight `user_permission` rows
    (`seed_capabilities_for_user(..., emit_sync=True)`), so the outbox no
    longer holds exactly one row -- it holds one `user` event plus eight
    `user_permission` events. The assertion below is narrowed to select the
    `user`-typed row specifically (what it always meant to pin) rather than
    assume it is the only row in the table; the `user_permission` half of
    this same call is proven separately by
    test_create_admin_seeds_and_emits_capability_rows_for_the_owner in
    commercial_runtime/identity/tests/
    test_user_permission_sync_emission_write_sites.py, so nothing this test
    used to catch is lost."""
    rows = _outbox_rows(db_path)
    user_events = [r for r in rows if r["entity_type"] == "user"]
    assert len(user_events) == 1, f"expected exactly one `user` outbox row after create-admin, got {rows}"
    ev = user_events[0]
    assert ev["event_type"] == "create"
    admin_row = _user_row(db_path, admin.get('/api/auth/session').get_json()['user']['id'])
    assert ev["entity_id"] == admin_row["uid"], "entity_id must be the wire uid, never users.id"
    assert ev["payload"]["email"] == "owner@test.local"
    assert ev["payload"]["row_version"] == 1
    assert ev["payload"]["role"] == "admin"


def test_create_employee_emits_a_create_event(admin, db_path):
    """Narrowed to the `user`-typed row for the same reason as
    test_create_admin_emits_a_create_event above -- create-employee also
    seeds and emits eight `user_permission` rows for the new hire now (see
    test_user_permission_sync_emission_write_sites.py)."""
    before = len(_outbox_rows(db_path))
    user_id, email, _ = _make_employee(admin, role='manager')
    rows = _outbox_rows(db_path)
    new_rows = rows[before:]
    user_events = [r for r in new_rows if r["entity_type"] == "user"]
    assert len(user_events) == 1, f"expected exactly one new `user` outbox row, got {new_rows}"
    ev = user_events[0]
    assert ev["event_type"] == "create"
    assert ev["payload"]["email"] == email
    assert ev["payload"]["role"] == "manager"
    assert ev["payload"]["row_version"] == 1
    row = _user_row(db_path, user_id)
    assert ev["entity_id"] == row["uid"]


# ── Task C.1 / C.2 -- update sites: emit `update`, bumped row_version,
# payload carries the NEW value (the headline row_version proof) ───────────

def test_update_status_emits_an_update_event_with_the_bumped_row_version(admin, db_path):
    user_id, _, _ = _make_employee(admin)
    before_row = _user_row(db_path, user_id)
    before_count = len(_outbox_rows(db_path))

    r = admin.put(f'/api/admin/employees/{user_id}/status', json={'status': 'disabled'})
    assert r.status_code == 200, r.get_json()

    rows = _outbox_rows(db_path)
    assert len(rows) == before_count + 1
    ev = rows[-1]
    assert ev["event_type"] == "update"
    assert ev["payload"]["status"] == "disabled"
    assert ev["payload"]["row_version"] == before_row["row_version"] + 1, (
        "payload must carry the NEW row_version, not the value from before the write"
    )
    after_row = _user_row(db_path, user_id)
    assert ev["payload"]["row_version"] == after_row["row_version"]


def test_update_role_emits_an_update_event_with_the_bumped_row_version(admin, db_path):
    """Narrowed to the LAST `user`-typed row, not `rows[-1]`: update_role now
    ALSO deletes and re-seeds eight `user_permission` rows for the new role
    (stage 3, Decision 4), which are queued AFTER this route's own `user`
    update event in code order (see onboarding_routes.update_role), so the
    truly-last outbox row is one of those, not this one. The
    `user_permission` half of this exact call is proven separately in
    test_user_permission_sync_emission_write_sites.py."""
    user_id, _, _ = _make_employee(admin)
    before_row = _user_row(db_path, user_id)

    r = admin.put(f'/api/admin/employees/{user_id}/role', json={'role': 'manager'})
    assert r.status_code == 200, r.get_json()

    user_events = [r for r in _outbox_rows(db_path) if r["entity_type"] == "user"]
    ev = user_events[-1]
    assert ev["event_type"] == "update"
    assert ev["payload"]["role"] == "manager"
    assert ev["payload"]["row_version"] == before_row["row_version"] + 1


def test_update_clinic_role_emits_even_though_clinic_role_itself_never_leaves_the_device(admin, db_path):
    """Decision 3 / the task's MUST-emit list: `clinic_role` is not
    allowlisted, but this route deliberately bumps `session_version` to
    force re-login elsewhere -- that revocation only reaches another device
    if an event is actually queued."""
    user_id, _, _ = _make_employee(admin)
    before_row = _user_row(db_path, user_id)

    r = admin.put(f'/api/admin/employees/{user_id}/clinic-role', json={'clinic_role': 'doctor'})
    assert r.status_code == 200, r.get_json()

    ev = _outbox_rows(db_path)[-1]
    assert ev["event_type"] == "update"
    assert "clinic_role" not in ev["payload"], "clinic_role must never appear in a queued payload"
    assert ev["payload"]["row_version"] == before_row["row_version"] + 1
    assert ev["payload"]["session_version"] == before_row["session_version"] + 1
    # every allowlisted field is present, unchanged
    assert ev["payload"]["email"] == before_row["email"]
    assert ev["payload"]["role"] == before_row["role"]


def test_update_perms_emits_a_user_update_event_too(admin, db_path):
    """UPDATED for stage 3: `user_permission` now DOES sync (this test's
    name and docstring used to say the opposite, back when it was stage
    3's own documented gap). update_perms queues its `user_permission`
    delete+update pair BEFORE this route's own `user`/session_version-bump
    event in code order (onboarding_routes.update_perms), so `rows[-1]` is
    still this `user` event, unchanged from before stage 3 -- proven here
    the same way it always was. The `user_permission` half of this exact
    call (the delete-then-recreate pair, and the actual access_level that
    travels) is proven separately in
    test_user_permission_sync_emission_write_sites.py. The session_version
    bump still matters in its own right regardless: it is what forces a
    device that already had this user's OLD permission set logged in to
    re-authenticate, independent of whether the permission payload itself
    arrives in the same batch."""
    user_id, _, _ = _make_employee(admin)
    before_row = _user_row(db_path, user_id)

    r = admin.post(f'/api/admin/employees/{user_id}/permissions',
                    json={'subsystem': 'retail.discount', 'access_level': 'full'})
    assert r.status_code == 200, r.get_json()

    ev = _outbox_rows(db_path)[-1]
    assert ev["entity_type"] == "user"
    assert ev["event_type"] == "update"
    assert ev["payload"]["row_version"] == before_row["row_version"] + 1
    assert ev["payload"]["session_version"] == before_row["session_version"] + 1


def test_pin_set_and_clear_each_emit_an_update_event_with_a_hashed_never_raw_value(admin, db_path):
    user_id, _, _ = _make_employee(admin)
    before_row = _user_row(db_path, user_id)
    before_count = len(_outbox_rows(db_path))

    r = admin.put(f'/api/admin/employees/{user_id}/pin', json={'pin': '1234'})
    assert r.status_code == 200, r.get_json()
    rows = _outbox_rows(db_path)
    assert len(rows) == before_count + 1
    set_ev = rows[-1]
    assert set_ev["event_type"] == "update"
    assert set_ev["payload"]["row_version"] == before_row["row_version"] + 1
    assert set_ev["payload"]["pin_hash"] != "1234"
    assert set_ev["payload"]["pin_hash"].startswith("pbkdf2_sha256$")

    mid_row = _user_row(db_path, user_id)
    r = admin.delete(f'/api/admin/employees/{user_id}/pin')
    assert r.status_code == 200, r.get_json()
    rows = _outbox_rows(db_path)
    assert len(rows) == before_count + 2
    clear_ev = rows[-1]
    assert clear_ev["event_type"] == "update"
    assert clear_ev["payload"]["row_version"] == mid_row["row_version"] + 1
    assert clear_ev["payload"]["pin_hash"] is None
    # session_version must NOT move for a PIN change (attribution, not
    # authorization -- update_pin's own docstring).
    assert clear_ev["payload"]["session_version"] == before_row["session_version"]


def test_set_language_now_bumps_row_version_and_emits(admin, db_path):
    """Closes the documented gap: user_accounts._touch_user's docstring used
    to name this exact site as unbumped/unemitted. Wave B2's own field
    allowlist settles that `language` travels now."""
    session_user = admin.get('/api/auth/session').get_json()['user']
    before_row = _user_row(db_path, session_user['id'])
    before_count = len(_outbox_rows(db_path))

    r = admin.post('/api/auth/language', json={'language': 'ar'})
    assert r.status_code == 200, r.get_json()

    rows = _outbox_rows(db_path)
    assert len(rows) == before_count + 1, "set_language must now queue a sync event"
    ev = rows[-1]
    assert ev["event_type"] == "update"
    assert ev["payload"]["language"] == "ar"
    assert ev["payload"]["row_version"] == before_row["row_version"] + 1, (
        "set_language must bump row_version in the SAME statement as the language change"
    )
    after_row = _user_row(db_path, session_user['id'])
    assert after_row["row_version"] == before_row["row_version"] + 1


def test_reset_password_emits_an_update_event_carrying_the_new_hash(admin, db_path):
    user_id, email, _ = _make_employee(admin)
    # Move the employee out of pending_setup with a real password first, so
    # the reset below is a genuine "change an existing password" case.
    admin.put(f'/api/admin/employees/{user_id}/status', json={'status': 'active'})
    before_row = _user_row(db_path, user_id)
    before_count = len(_outbox_rows(db_path))

    conn = _raw_conn(db_path)
    try:
        raw_token = verification._create_link(
            conn, company_id=before_row["company_id"], email=email,
            purpose="password_reset", ttl_hours=1,
        )
        conn.commit()
    finally:
        conn.close()

    client = admin  # anonymous route, but reuse the same test client
    r = client.post('/api/auth/reset-password', json={'token': raw_token, 'password': 'BrandNewPW1'})
    assert r.status_code == 200, r.get_json()

    rows = _outbox_rows(db_path)
    assert len(rows) == before_count + 1
    ev = rows[-1]
    assert ev["event_type"] == "update"
    assert ev["payload"]["row_version"] == before_row["row_version"] + 1
    assert ev["payload"]["password_hash"] != before_row["password_hash"]
    assert ev["payload"]["password_hash"].startswith("pbkdf2_sha256$")
    assert "BrandNewPW1" not in json.dumps(ev["payload"]), "plaintext password leaked into the payload"


def test_employee_setup_emits_an_update_event(admin, db_path):
    user_id, email, setup_link = _make_employee(admin)
    before_row = _user_row(db_path, user_id)
    before_count = len(_outbox_rows(db_path))
    raw_token = setup_link.rsplit('/', 1)[-1]

    r = admin.post('/api/auth/employee/setup', json={'token': raw_token, 'password': 'FirstRealPW1'})
    assert r.status_code == 200, r.get_json()

    rows = _outbox_rows(db_path)
    assert len(rows) == before_count + 1
    ev = rows[-1]
    assert ev["event_type"] == "update"
    assert ev["payload"]["status"] == "active"
    assert ev["payload"]["require_password_change"] == 0
    assert ev["payload"]["row_version"] == before_row["row_version"] + 1


# ── Task C.4 -- deliberately excluded sites stay silent ────────────────────

def test_verify_email_does_not_emit(admin, db_path):
    session_user = admin.get('/api/auth/session').get_json()['user']
    before_row = _user_row(db_path, session_user['id'])
    before_count = len(_outbox_rows(db_path))

    conn = _raw_conn(db_path)
    try:
        raw_token = verification._create_link(
            conn, company_id=before_row["company_id"], email='owner@test.local',
            purpose="email_verification", ttl_hours=24,
        )
        conn.commit()
    finally:
        conn.close()

    r = admin.post('/api/auth/verify-email', json={'token': raw_token})
    assert r.status_code == 200, r.get_json()

    assert len(_outbox_rows(db_path)) == before_count, "email_verified_at is not allowlisted -- must not emit"
    after_row = _user_row(db_path, session_user['id'])
    assert after_row["email_verified_at"] is not None, "fixture sanity: the verify itself must have taken effect"
    assert after_row["row_version"] == before_row["row_version"], "verify-email must not bump row_version either"


def _seed_login_user(db_path, *, email, password_hash, **overrides):
    row = {
        "id": str(uuid.uuid4()), "company_id": "company-x", "employee_id": "E1",
        "email": email, "password_hash": password_hash, "role": "cashier",
        "status": "active", "require_password_change": 0, "session_version": 1,
        "language": "en", "clinic_role": "", "failed_login_count": 0,
        "locked_until": None, "uid": str(uuid.uuid4()), "pin_hash": None,
        "row_version": 1, "updated_at_utc": "2026-01-01T00:00:00+00:00",
    }
    row.update(overrides)
    conn = _raw_conn(db_path)
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
    return row


def test_failed_login_does_not_emit(app, db_path):
    from commercial_runtime.security.passwords import hash_password
    row = _seed_login_user(db_path, email="cashier2@x.com", password_hash=hash_password("RealPW123"))
    before_count = len(_outbox_rows(db_path))

    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': 'cashier2@x.com', 'password': 'WRONG-PASSWORD'})
    assert r.status_code == 401

    assert len(_outbox_rows(db_path)) == before_count, "a failed login must never queue a user event"


def test_lockout_does_not_emit(app, db_path):
    from commercial_runtime.security.passwords import hash_password
    row = _seed_login_user(db_path, email="cashier3@x.com", password_hash=hash_password("RealPW123"))
    client = app.test_client()
    for _ in range(mt_auth.MAX_FAILED_ATTEMPTS):
        client.post('/api/auth/login', json={'email': 'cashier3@x.com', 'password': 'WRONG'})
    before_count = len(_outbox_rows(db_path))
    assert before_count == 0, "the lockout itself must not have emitted anything"

    after_row = _user_row(db_path, row["id"])
    assert after_row["locked_until"] is not None, "fixture sanity: the account must actually be locked"
    assert len(_outbox_rows(db_path)) == 0


def test_successful_login_with_a_modern_hash_does_not_emit(app, db_path):
    from commercial_runtime.security.passwords import hash_password
    row = _seed_login_user(db_path, email="cashier4@x.com", password_hash=hash_password("RealPW123"))
    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': 'cashier4@x.com', 'password': 'RealPW123'})
    assert r.status_code == 200, r.get_json()
    assert len(_outbox_rows(db_path)) == 0


def test_hash_upgrade_on_login_does_not_emit_but_the_hash_still_changes_locally(app, db_path):
    """The mt_auth.py judgment call, proven: a legacy SHA-256 hash upgrades
    transparently on a correct login, changing `password_hash` locally, but
    must NOT bump row_version or queue an event (see mt_auth.py's own
    comment at the upgrade site for the full churn-vs-divergence reasoning)."""
    legacy_hash = hashlib.sha256(b"RealPW123").hexdigest()
    row = _seed_login_user(db_path, email="cashier5@x.com", password_hash=legacy_hash)
    client = app.test_client()

    r = client.post('/api/auth/login', json={'email': 'cashier5@x.com', 'password': 'RealPW123'})
    assert r.status_code == 200, r.get_json()

    after_row = _user_row(db_path, row["id"])
    assert after_row["password_hash"] != legacy_hash, "fixture sanity: the hash must have been upgraded"
    assert after_row["password_hash"].startswith("pbkdf2_sha256$")
    assert after_row["row_version"] == row["row_version"], "a hash re-encoding of the SAME password must not bump row_version"
    assert len(_outbox_rows(db_path)) == 0, "a hash re-encoding of the SAME password must not emit"
