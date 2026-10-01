"""AUDIT-033 (S-AUTH, D5): the owner's own sign-in changes at the main computer only.

Every route that changes the owner's status, PIN, permissions, clinic role or
password is exercised in three states of the shop:

  * `other`: a main computer IS assigned and it is NOT this computer -> 409
    OWNER_CHANGE_NEEDS_MAIN_COMPUTER, and NOTHING moved: not the users row, not
    the permission rows, not one sync_outbox row (counted, not assumed).
  * `main`: this computer is the main computer -> the change goes through.
  * `none`: nobody has claimed main yet (a fresh single install) -> goes through.

Display name and language stay allowed on a non-main computer.

Same fixtures as test_fix_round_accounts.py (the REAL registry from
`init_registry_db()`, the REAL create-admin route). Each guard is
mutation-proved: delete the `_owner_change_refusal` call in the route, watch the
`other` case go red, restore. The proof runs are quoted in the crew hand-back.

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/identity/tests/test_owner_change_needs_main.py -v
"""
import sqlite3

import pytest
from flask import Flask

from commercial_runtime.identity import device_context, device_registry, mt_auth, onboarding_routes, registry_db
from commercial_runtime.identity import verification
from commercial_runtime.identity.auth_routes import auth_bp
from commercial_runtime.identity.onboarding_routes import onboarding_bp
from commercial_runtime.identity.owner_change_guard import (
    OWNER_CHANGE_NEEDS_MAIN_COMPUTER, OWNER_CHANGE_NEEDS_MAIN_MESSAGE)
from commercial_runtime.licensing_contracts import flask_guard
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRecord

OTHER_MAIN = 'dev-the-real-main-computer'


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(path))
    monkeypatch.setattr(registry_db, "_db_dir", str(tmp_path))
    registry_db.init_registry_db()
    return path


def _connect(db_path):
    c = sqlite3.connect(str(db_path), timeout=30)
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture
def app(db_path, tmp_path, monkeypatch):
    monkeypatch.setattr(onboarding_routes, "get_conn", lambda: _connect(db_path))
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path))
    monkeypatch.setattr(mt_auth, "REGISTRY_DB", str(db_path))
    monkeypatch.setattr(flask_guard.LicenseStateRepository, "__init__", lambda self, db_path: None)
    monkeypatch.setattr(
        flask_guard.LicenseStateRepository, "load",
        lambda self: LicenseStateRecord(
            licensing_schema_version=1, product_code="AURA_TEST", platform="WINDOWS",
            current_state="ACTIVE_ONLINE"))
    device_context._cached_device = None
    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"
    flask_app.testing = True
    flask_app.register_blueprint(onboarding_bp)
    flask_app.register_blueprint(auth_bp)
    yield flask_app
    device_context._cached_device = None


@pytest.fixture
def admin(app):
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner@test.local', 'password': 'OwnerPW11'})
    assert r.status_code == 200, r.get_json()
    return client


def _q(db_path, sql, args=()):
    c = _connect(db_path)
    try:
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    finally:
        c.close()


def _owner(db_path):
    return _q(db_path, "SELECT id, company_id FROM users WHERE role='admin'")[0]


def _set_main(db_path, mode):
    """none: nobody is main. main: THIS computer is. other: another computer is."""
    cid = _owner(db_path)['company_id']
    c = _connect(db_path)
    try:
        c.execute("DELETE FROM admin_device_assignment")
        c.execute("UPDATE devices SET is_admin_device=0")
        local = device_context.local_device_uuid()
        device_registry.upsert_local_device(c, local, cid)
        if mode == 'main':
            assert device_registry.claim_admin_device_with_assignment(c, cid, local)
        elif mode == 'other':
            device_registry.upsert_local_device(c, OTHER_MAIN, cid)
            assert device_registry.claim_admin_device_with_assignment(c, cid, OTHER_MAIN)
        c.commit()
    finally:
        c.close()
    device_context._cached_device = None
    # What the claim itself queued is not what the routes under test queue.
    c = _connect(db_path)
    c.execute("DELETE FROM sync_outbox")
    c.commit()
    c.close()


def _snapshot(db_path, owner_id):
    return {
        'user': _q(db_path, "SELECT * FROM users WHERE id=?", (owner_id,)),
        'perms': sorted((r['subsystem'], r['access_level']) for r in
                        _q(db_path, "SELECT subsystem, access_level FROM user_permissions WHERE user_id=?",
                           (owner_id,))),
        'outbox': _q(db_path, "SELECT COUNT(*) AS n FROM sync_outbox")[0]['n'],
        'links_used': _q(db_path, "SELECT COUNT(*) AS n FROM secure_links WHERE is_used=1")[0]['n'],
    }


# ── one callable per guarded route: (client, db_path, owner_id) -> response ──

def _status(c, db, oid):
    return c.put(f'/api/admin/employees/{oid}/status', json={'status': 'active'})


def _pin_set(c, db, oid):
    return c.put(f'/api/admin/employees/{oid}/pin', json={'pin': '4321'})


def _pin_clear(c, db, oid):
    return c.delete(f'/api/admin/employees/{oid}/pin')


def _perms(c, db, oid):
    return c.post(f'/api/admin/employees/{oid}/permissions',
                  json={'subsystem': 'retail', 'access_level': 'none'})


def _clinic_role(c, db, oid):
    return c.put(f'/api/admin/employees/{oid}/clinic-role', json={'clinic_role': 'doctor'})


def _link(db, purpose):
    o = _owner(db)
    conn = _connect(db)
    try:
        token = verification._create_link(conn, company_id=o['company_id'], email='owner@test.local',
                                          purpose=purpose, ttl_hours=1)
        conn.commit()
        return token
    finally:
        conn.close()


def _reset_password(c, db, oid):
    # an anonymous request: the reset link is the credential
    token = _link(db, 'password_reset')
    return c.application.test_client().post(
        '/api/auth/reset-password', json={'token': token, 'password': 'BrandNewPW9'})


def _employee_setup(c, db, oid):
    token = _link(db, 'employee_setup')
    return c.application.test_client().post(
        '/api/auth/employee/setup', json={'token': token, 'password': 'BrandNewPW9'})


ROUTES = [
    pytest.param(_status, id='status'),
    pytest.param(_pin_set, id='pin-set'),
    pytest.param(_pin_clear, id='pin-clear'),
    pytest.param(_perms, id='permissions'),
    pytest.param(_clinic_role, id='clinic-role'),
    pytest.param(_reset_password, id='reset-password'),
    pytest.param(_employee_setup, id='employee-setup'),
]


@pytest.mark.parametrize('route', ROUTES)
def test_owner_change_on_a_non_main_computer_is_refused_and_nothing_moves(admin, db_path, route):
    _set_main(db_path, 'other')
    oid = _owner(db_path)['id']
    before = _snapshot(db_path, oid)
    resp = route(admin, db_path, oid)
    body = resp.get_json()
    assert resp.status_code == 409, body
    assert body['code'] == OWNER_CHANGE_NEEDS_MAIN_COMPUTER
    assert body['error'] == OWNER_CHANGE_NEEDS_MAIN_MESSAGE
    after = _snapshot(db_path, oid)
    assert after['user'] == before['user'], 'the owner users row moved on a refusal'
    assert after['perms'] == before['perms']
    assert after['outbox'] == before['outbox'] == 0, 'a refusal queued a sync event'
    assert after['links_used'] == 0, 'a refused reset/setup link was burned'


@pytest.mark.parametrize('route', ROUTES)
@pytest.mark.parametrize('mode', ['main', 'none'])
def test_owner_change_is_allowed_on_the_main_computer_and_on_a_fresh_install(admin, db_path, route, mode):
    _set_main(db_path, mode)
    oid = _owner(db_path)['id']
    before = _snapshot(db_path, oid)
    resp = route(admin, db_path, oid)
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body.get('code') != OWNER_CHANGE_NEEDS_MAIN_COMPUTER
    after = _snapshot(db_path, oid)
    # the allow-half must really DO the thing, not merely return 200
    assert (after['user'], after['perms'], after['outbox']) != (before['user'], before['perms'], before['outbox'])


def test_display_name_and_language_stay_allowed_on_a_non_main_computer(admin, db_path):
    _set_main(db_path, 'other')
    oid = _owner(db_path)['id']
    r = admin.put(f'/api/admin/employees/{oid}/name', json={'name': 'Owner Renamed'})
    assert r.status_code == 200, r.get_json()
    r = admin.post('/api/auth/language', json={'language': 'ar'})
    assert r.status_code == 200, r.get_json()
    row = _q(db_path, "SELECT display_name, language FROM users WHERE id=?", (oid,))[0]
    assert row["display_name"] == "Owner Renamed" and row['language'] == 'ar'


def test_a_non_owner_is_never_refused_by_the_owner_guard(admin, db_path):
    """The deny-half must not spill onto staff: a cashier's PIN changes on any computer."""
    r = admin.post('/api/admin/employees', json={
        'name': 'Cash', 'email': 'cash@test.local', 'role': 'cashier'})
    assert r.status_code == 200, r.get_json()
    cid = _q(db_path, "SELECT id FROM users WHERE email='cash@test.local'")[0]['id']
    _set_main(db_path, 'other')
    r = admin.put(f'/api/admin/employees/{cid}/pin', json={'pin': '4321'})
    assert r.status_code == 200, r.get_json()


def test_a_refused_reset_link_still_works_on_the_main_computer(admin, db_path):
    """The refusal rolls the link's 'used' flag back, so the owner can open the
    SAME emailed link at the main computer instead of asking for a new one."""
    token = _link(db_path, 'password_reset')
    anon = admin.application.test_client()
    _set_main(db_path, 'other')
    r = anon.post('/api/auth/reset-password', json={'token': token, 'password': 'BrandNewPW9'})
    assert r.status_code == 409
    _set_main(db_path, 'main')
    r = anon.post('/api/auth/reset-password', json={'token': token, 'password': 'BrandNewPW9'})
    assert r.status_code == 200, r.get_json()
