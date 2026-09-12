"""AUDIT -- 11 admin routes in onboarding_routes.py bypass authentication
entirely.

`grep -c mt_login_required commercial_runtime/identity/onboarding_routes.py`
returns ZERO. Eleven routes instead gate on a bare
`if session.get('mt_role') != 'admin'` read straight from the cookie
session -- never re-checked against the database row. A previous fix in this
same wave made `mt_login_required` enforce account status and
`session_version` (the revocation channel, bumped on password reset, role
change and status change). None of that reaches these eleven routes: an
admin whose account was DISABLED or whose session was REVOKED still
administers the shop through them -- creates employees, changes roles,
changes permissions, reads the audit log, edits company settings.

This suite drives the REAL onboarding_bp blueprint through a REAL Flask test
client (same spirit as test_employee_admin_routes.py in this package), builds
a genuine admin session via `/api/onboarding/create-admin`, proves the route
answers a healthy session first (the precondition -- a route that already
refused would prove nothing), then either disables the admin's own row or
bumps its `session_version` directly in the database (never through a route,
since the disable route itself now refuses to disable the owner -- see
A2/test_employee_admin_routes.py), and asserts the route now refuses.

Table-driven on purpose: a future admin route added to this file without
`@mt_login_required` fails here automatically, the same way
retail_route_capability_matrix_test.py catches a route that forgot its
capability gate.

Run:
    pytest commercial_runtime/identity/tests/test_admin_routes_require_login.py -v
"""
import sqlite3
import uuid

import pytest
from flask import Flask

from commercial_runtime.identity import mt_auth, onboarding_routes, registry_db
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
    # create_admin writes config.json through _config_path(), which reads
    # AURA_APP_DATA at call time -- keep that inside the tmp dir so a test
    # run never touches a developer's real install.
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path))
    # @mt_login_required resolves its OWN DB connection separately, through
    # mt_auth._get_registry_conn() reading the module-level mt_auth.REGISTRY_DB
    # global -- not through onboarding_routes.get_conn patched above. Without
    # repointing it here too, the decorator's own read fails against a
    # stale/nonexistent path and every request is refused regardless of
    # session validity (see test_device_routes.py's docstring).
    monkeypatch.setattr(mt_auth, "REGISTRY_DB", str(db_path))
    # This suite tests the LOGIN/session gate (A2), not licensing -- the
    # employee-management routes it drives (create_employee et al.) now also
    # carry a licence gate (AUDIT: account administration had none), so it
    # needs a licensed install to reach them at all. Same monkeypatch, same
    # reasoning, as test_employee_admin_routes.py's own `app` fixture.
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
    return flask_app


@pytest.fixture
def admin(app):
    """A logged-in owner, minted through the real create-admin route so
    `company_id`, the ADMIN-0001 id and the v3 columns are exactly what
    production would have written."""
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner@test.local', 'password': 'OwnerPW11',
    })
    assert r.status_code == 200, r.get_json()
    return client


def _admin_user_id(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT id FROM users WHERE role='admin'").fetchone()['id']
    finally:
        conn.close()


def _target_employee_id(admin):
    """A second account for the routes that need an `<id>` in their path --
    never the admin's own row, which A2 now protects from being disabled."""
    email = f'target-{uuid.uuid4().hex[:8]}@test.local'
    r = admin.post('/api/admin/employees', json={'email': email})
    assert r.status_code == 200, r.get_json()
    employees = admin.get('/api/admin/employees').get_json()['employees']
    return next(e['id'] for e in employees if e['email'] == email)


def _call(client, method, path, body):
    fn = getattr(client, method.lower())
    return fn(path, json=body) if body is not None else fn(path)


#: (name, http method, path-builder(target_user_id), body-builder(target_user_id)).
#: Body is a callable too (not a fixed dict) so `create_employee`'s email is
#: fresh on every call -- reusing one literal dict would collide with itself
#: between the precondition call and the post-mutation call within a single
#: test, muddying what actually made the second call fail.
ADMIN_ROUTES = [
    ('complete_onboarding',
     'POST', lambda uid: '/api/onboarding/complete', lambda uid: {}),
    ('get_employees',
     'GET', lambda uid: '/api/admin/employees', lambda uid: None),
    ('create_employee',
     'POST', lambda uid: '/api/admin/employees',
     lambda uid: {'email': f'new-{uuid.uuid4().hex[:8]}@test.local'}),
    ('update_status',
     'PUT', lambda uid: f'/api/admin/employees/{uid}/status', lambda uid: {'status': 'active'}),
    ('update_role',
     'PUT', lambda uid: f'/api/admin/employees/{uid}/role', lambda uid: {'role': 'manager'}),
    ('update_pin_put',
     'PUT', lambda uid: f'/api/admin/employees/{uid}/pin', lambda uid: {'pin': '1234'}),
    ('update_pin_delete',
     'DELETE', lambda uid: f'/api/admin/employees/{uid}/pin', lambda uid: None),
    ('update_clinic_role',
     'PUT', lambda uid: f'/api/admin/employees/{uid}/clinic-role', lambda uid: {'clinic_role': 'doctor'}),
    ('update_perms',
     'POST', lambda uid: f'/api/admin/employees/{uid}/permissions',
     lambda uid: {'subsystem': 'retail.sell', 'access_level': 'full'}),
    ('get_audit',
     'GET', lambda uid: '/api/admin/audit', lambda uid: None),
    ('get_admin_stats',
     'GET', lambda uid: '/api/admin/stats', lambda uid: None),
    ('company_settings_get',
     'GET', lambda uid: '/api/admin/company/settings', lambda uid: None),
]

_IDS = [row[0] for row in ADMIN_ROUTES]


@pytest.mark.parametrize('name,method,path_fn,body_fn', ADMIN_ROUTES, ids=_IDS)
def test_route_refuses_a_disabled_admins_session(name, method, path_fn, body_fn, admin, db_path):
    target = _target_employee_id(admin)
    path = path_fn(target)

    precondition = _call(admin, method, path, body_fn(target))
    assert precondition.status_code == 200, (
        f'{name}: precondition failed -- a healthy admin session was not served '
        f'(got {precondition.status_code}: {precondition.get_json()}); this test '
        f'would prove nothing about the disabled-account path'
    )

    admin_id = _admin_user_id(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE users SET status='disabled' WHERE id=?", (admin_id,))
    conn.commit()
    conn.close()

    r = _call(admin, method, path, body_fn(target))
    assert r.status_code == 401, (
        f'{name}: a DISABLED admin session must be refused, got {r.status_code}: {r.get_json()}'
    )


@pytest.mark.parametrize('name,method,path_fn,body_fn', ADMIN_ROUTES, ids=_IDS)
def test_route_refuses_a_revoked_admin_session(name, method, path_fn, body_fn, admin, db_path):
    target = _target_employee_id(admin)
    path = path_fn(target)

    precondition = _call(admin, method, path, body_fn(target))
    assert precondition.status_code == 200, (
        f'{name}: precondition failed -- a healthy admin session was not served '
        f'(got {precondition.status_code}: {precondition.get_json()}); this test '
        f'would prove nothing about the revoked-session path'
    )

    admin_id = _admin_user_id(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE users SET session_version=session_version+1 WHERE id=?", (admin_id,))
    conn.commit()
    conn.close()

    r = _call(admin, method, path, body_fn(target))
    assert r.status_code == 401, (
        f'{name}: a REVOKED admin session (session_version bumped) must be refused, '
        f'got {r.status_code}: {r.get_json()}'
    )


def test_the_route_table_covers_all_eleven_known_bare_role_check_routes():
    """A guard for the guard: if this table shrinks below eleven, the two
    tests above silently cover fewer routes than the audit found."""
    assert len(ADMIN_ROUTES) >= 11, (
        f'expected coverage for all 11 routes named in the audit, table has {len(ADMIN_ROUTES)}'
    )
