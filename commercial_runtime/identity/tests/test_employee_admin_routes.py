"""Admin employee-management HTTP surface -- tests for the role-change and
till-PIN routes in commercial_runtime/identity/onboarding_routes.py.

Why these routes exist: registry v3 widened `users.role` to
{admin, manager, cashier} and added `pin_hash`, and `user_accounts` grew
`set_user_pin`/`clear_user_pin` -- but nothing could CHANGE a role after
creation and nothing anywhere called the PIN functions. Both halves were
written and unreachable. These tests pin the behaviour the Android and desktop
employee screens depend on.

Standalone, in the same spirit as test_device_routes.py in this package:
`registry_db.DB_PATH` is redirected at a temp file and the real schema is
built through `init_registry_db()`, so this runs against the ACTUAL v3
registry -- widened role domain, capability seeding, PIN column and all --
without needing product boot state. `onboarding_routes` binds `get_conn` by
name at import (`from ...registry_db import get_conn`), so the fixture patches
the name on that module, not only on registry_db.

Run:
    pytest commercial_runtime/identity/tests/test_employee_admin_routes.py -v
"""
import sqlite3
import uuid

import pytest
from flask import Flask

from commercial_runtime.identity import mt_auth, onboarding_routes, registry_db, user_accounts
from commercial_runtime.identity.onboarding_routes import onboarding_bp


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
    # AURA_APP_DATA at call time -- keep that inside the tmp dir so a test run
    # never touches a developer's real install.
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path))
    # Every route in this blueprint now carries @mt_login_required, whose own
    # DB connection is resolved separately, through mt_auth._get_registry_conn()
    # reading the module-level mt_auth.REGISTRY_DB global (fixed at mt_auth's
    # first import, from whatever AURA_APP_DATA was at that time) -- NOT
    # through onboarding_routes.get_conn patched above. Without repointing it
    # here too, mt_login_required's own read 404s against a stale/nonexistent
    # path and every request gets refused regardless of session validity (see
    # test_device_routes.py's docstring for the same pattern).
    monkeypatch.setattr(mt_auth, "REGISTRY_DB", str(db_path))

    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"
    flask_app.testing = True
    flask_app.register_blueprint(onboarding_bp)
    return flask_app


@pytest.fixture
def admin(app):
    """A logged-in owner. Goes through the real create-admin route rather than
    stuffing a session dict, so `company_id`, the ADMIN-0001 id and the v3
    columns are exactly what production would have written."""
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner@test.local', 'password': 'OwnerPW11',
    })
    assert r.status_code == 200, r.get_json()
    return client


def _make_employee(admin, role=None):
    email = f'emp-{uuid.uuid4().hex[:8]}@test.local'
    body = {'email': email}
    if role:
        body['role'] = role
    r = admin.post('/api/admin/employees', json=body)
    assert r.status_code == 200, r.get_json()
    employees = admin.get('/api/admin/employees').get_json()['employees']
    row = next(e for e in employees if e['email'] == email)
    return row['id'], email


def _caps(db_path, user_id):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return {r['subsystem']: r['access_level'] for r in conn.execute(
            "SELECT subsystem, access_level FROM user_permissions WHERE user_id=?", (user_id,))}
    finally:
        conn.close()


def _user(db_path, user_id):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    finally:
        conn.close()


# ── The list ─────────────────────────────────────────────────────────────────

def test_the_list_reports_pin_presence_and_never_the_hash(admin, db_path):
    """A PBKDF2 digest of a four-digit secret is a 10,000-entry dictionary away
    from being the PIN itself. The client is told only whether one is set."""
    user_id, _ = _make_employee(admin)

    body = admin.get('/api/admin/employees').get_json()
    row = next(e for e in body['employees'] if e['id'] == user_id)
    assert row['has_pin'] is False
    assert 'pin_hash' not in row

    assert admin.put(f'/api/admin/employees/{user_id}/pin', json={'pin': '1234'}).status_code == 200
    row = next(e for e in admin.get('/api/admin/employees').get_json()['employees']
               if e['id'] == user_id)
    assert row['has_pin'] is True
    assert 'pin_hash' not in row


def test_the_list_reports_the_effective_role_without_rewriting_the_stored_one(admin, db_path):
    """Clinic renders this same response and its rows still say 'employee', so
    `role` must survive byte-for-byte. `effective_role` is the widened-domain
    reading every capability decision is actually made against -- a UI that
    showed only the raw value would label a cashier 'employee'."""
    user_id, _ = _make_employee(admin)  # no role => legacy default

    row = next(e for e in admin.get('/api/admin/employees').get_json()['employees']
               if e['id'] == user_id)
    assert row['role'] == user_accounts.LEGACY_ROLE_EMPLOYEE
    assert row['effective_role'] == user_accounts.ROLE_CASHIER
    assert _user(db_path, user_id)['role'] == user_accounts.LEGACY_ROLE_EMPLOYEE


# ── Role change ──────────────────────────────────────────────────────────────

#: The codes that separate the two assignable roles, derived from
#: `ROLE_CAPABILITIES` at run time rather than listed here.
#:
#: Which codes a manager holds and a cashier does not is a POLICY decision that
#: lives in user_accounts.py and gets tuned (it already has: refund moved to a
#: cashier default once someone checked that `create_return` is sale-bound).
#: Hard-coding the split into this file would mean a legitimate policy change
#: breaks a test about role CHANGES, and whoever tuned it would have to relitigate
#: the argument in the wrong file. Deriving it keeps this suite testing the
#: mechanism -- "does a role change actually re-derive the grid, in both
#: directions" -- which is the thing that was broken and is not a matter of taste.
_MANAGER_ONLY = (user_accounts.capabilities_for_role(user_accounts.ROLE_MANAGER)
                 - user_accounts.capabilities_for_role(user_accounts.ROLE_CASHIER))


def test_the_two_assignable_roles_actually_differ():
    """Guard for the two tests below. If manager and cashier ever collapse to
    the same grid, those tests would pass while asserting nothing -- this makes
    that state fail loudly here instead of hiding there."""
    assert _MANAGER_ONLY, "manager and cashier grant the same capabilities; the role-change tests below are vacuous"


def test_promoting_a_cashier_to_manager_actually_grants_manager_capabilities(admin, db_path):
    """The bug this test exists for: `seed_capabilities_for_user` is
    INSERT OR IGNORE, and every account already has all eight rows from
    creation. A role change that only re-seeded would move the role string and
    grant nothing -- a promotion that promotes no one."""
    user_id, _ = _make_employee(admin, 'cashier')
    before = _caps(db_path, user_id)
    assert all(before[code] == user_accounts.ACCESS_NONE for code in _MANAGER_ONLY), before

    r = admin.put(f'/api/admin/employees/{user_id}/role', json={'role': 'manager'})
    assert r.status_code == 200, r.get_json()

    after = _caps(db_path, user_id)
    assert _user(db_path, user_id)['role'] == 'manager'
    assert all(after[code] == user_accounts.ACCESS_FULL for code in _MANAGER_ONLY), after
    # ...and a manager still never gets employee management (design §3): the
    # one code the owner keeps no matter what, so it is named explicitly.
    assert after[user_accounts.CAP_EMPLOYEES] == user_accounts.ACCESS_NONE


def test_demoting_a_manager_actually_removes_the_manager_capabilities(admin, db_path):
    """The direction that matters for security: if the reset only ever added
    grants, a demotion would be cosmetic and the person would keep everything
    their old role gave them."""
    user_id, _ = _make_employee(admin, 'manager')
    assert all(_caps(db_path, user_id)[code] == user_accounts.ACCESS_FULL
               for code in _MANAGER_ONLY)

    assert admin.put(f'/api/admin/employees/{user_id}/role',
                     json={'role': 'cashier'}).status_code == 200

    after = _caps(db_path, user_id)
    assert all(after[code] == user_accounts.ACCESS_NONE for code in _MANAGER_ONLY), after
    # The cashier's own grid survives the demotion -- a demotion is not a
    # revocation of the ability to work a till.
    for code in user_accounts.capabilities_for_role(user_accounts.ROLE_CASHIER):
        assert after[code] == user_accounts.ACCESS_FULL


def test_a_role_change_does_not_delete_the_legacy_subsystem_grant(admin, db_path):
    """The capability reset is bounded to the eight namespaced codes on
    purpose. `mt_require_subsystem` reads the legacy `subsystem='retail'` row
    on ~80 routes TODAY; an unbounded DELETE would take it out and lock the
    employee out of the entire retail app as a side effect of a job-title
    change."""
    user_id, _ = _make_employee(admin, 'cashier')
    assert admin.post(f'/api/admin/employees/{user_id}/permissions',
                      json={'subsystem': 'retail', 'access_level': 'full'}).status_code == 200

    assert admin.put(f'/api/admin/employees/{user_id}/role',
                     json={'role': 'manager'}).status_code == 200

    assert _caps(db_path, user_id)['retail'] == 'full'


def test_a_role_change_revokes_live_sessions_and_marks_the_row_dirty(admin, db_path):
    """`session_version` is the revocation channel design §4 gives `users`; a
    demotion that left a live session alone would leave a manager holding
    manager access until they happened to log out. `row_version` is the
    reject-stale marker the sync apply path compares."""
    user_id, _ = _make_employee(admin, 'manager')
    before = _user(db_path, user_id)

    assert admin.put(f'/api/admin/employees/{user_id}/role',
                     json={'role': 'cashier'}).status_code == 200

    after = _user(db_path, user_id)
    assert after['session_version'] == before['session_version'] + 1
    assert after['row_version'] == before['row_version'] + 1
    assert after['updated_at_utc'] > before['updated_at_utc']


def test_the_owner_role_cannot_be_changed(admin, db_path):
    """One admin per install and no way to mint a second (`create_admin` is
    gated on "no valid admin exists"), so a demotion here is an irreversible
    lockout of the entire shop."""
    owner_id = next(e['id'] for e in admin.get('/api/admin/employees').get_json()['employees']
                    if e['effective_role'] == 'admin')

    r = admin.put(f'/api/admin/employees/{owner_id}/role', json={'role': 'manager'})
    assert r.status_code == 409, r.get_json()
    assert _user(db_path, owner_id)['role'] == 'admin'


def test_an_unassignable_role_is_refused_with_the_translated_literal(admin, db_path):
    """'admin' would be a side door to a second owner account; anything else is
    outside the widened domain. The message is the FIXED sentence both locale
    catalogs carry -- a runtime-assembled one would have no key and would stay
    English on an Arabic till."""
    user_id, _ = _make_employee(admin, 'cashier')

    for bad in ('admin', 'wizard', '', 'EMPLOYEE'):
        r = admin.put(f'/api/admin/employees/{user_id}/role', json={'role': bad})
        assert r.status_code == 400, (bad, r.get_json())
        assert r.get_json()['error'] == 'Role must be manager or cashier.'
    assert _user(db_path, user_id)['role'] == 'cashier'


def test_role_change_is_admin_only(app, admin, db_path):
    """401, not 403: an anonymous caller carries no session at all, so
    `@mt_login_required` (added ahead of the bare role check -- see
    test_admin_routes_require_login.py) refuses before the role check ever
    runs. 403 stays reserved for a real, logged-in, non-admin session."""
    user_id, _ = _make_employee(admin, 'cashier')
    anonymous = app.test_client()
    r = anonymous.put(f'/api/admin/employees/{user_id}/role', json={'role': 'manager'})
    assert r.status_code == 401
    assert _user(db_path, user_id)['role'] == 'cashier'


def test_role_change_cannot_reach_another_tenants_row(app, admin, db_path):
    """Scoped by company_id like every sibling route, so a guessed id from
    another tenant is indistinguishable from one that does not exist."""
    conn = sqlite3.connect(str(db_path))
    other = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status) "
        "VALUES (?, 'other-company', 'EMP-9999', 'other@elsewhere.local', 'x', 'cashier', 'active')",
        (other,))
    conn.commit()
    conn.close()

    r = admin.put(f'/api/admin/employees/{other}/role', json={'role': 'manager'})
    assert r.status_code == 404
    assert _user(db_path, other)['role'] == 'cashier'


# ── PIN ──────────────────────────────────────────────────────────────────────

def test_a_pin_is_stored_hashed_and_verifies(admin, db_path):
    user_id, _ = _make_employee(admin)
    assert admin.put(f'/api/admin/employees/{user_id}/pin',
                     json={'pin': '4271'}).status_code == 200

    stored = _user(db_path, user_id)['pin_hash']
    assert stored and '4271' not in stored, "a PIN must never be recoverable from the row"

    conn = sqlite3.connect(str(db_path))
    try:
        assert user_accounts.verify_user_pin(conn, user_id, '4271') is True
        assert user_accounts.verify_user_pin(conn, user_id, '4272') is False
    finally:
        conn.close()


def test_an_arabic_indic_pin_is_accepted_and_verifies_from_an_ascii_keypad(admin, db_path):
    """The failure this guards: an Arabic soft keyboard emits U+0660..U+0669.
    Stored unfolded, a PIN set on an Arabic keypad could never be verified from
    an ASCII one -- input accepted, account broken, no error raised. The route
    hashes the folded form, so the two spellings are one PIN."""
    user_id, _ = _make_employee(admin)
    arabic_1234 = '١٢٣٤'

    assert admin.put(f'/api/admin/employees/{user_id}/pin',
                     json={'pin': arabic_1234}).status_code == 200

    conn = sqlite3.connect(str(db_path))
    try:
        assert user_accounts.verify_user_pin(conn, user_id, '1234') is True
        assert user_accounts.verify_user_pin(conn, user_id, arabic_1234) is True
    finally:
        conn.close()


def test_a_malformed_pin_is_refused_with_the_translated_literal(admin, db_path):
    user_id, _ = _make_employee(admin)
    for bad in ('123', '12345', '12a4', '', None, '12²4'):
        r = admin.put(f'/api/admin/employees/{user_id}/pin', json={'pin': bad})
        assert r.status_code == 400, (bad, r.get_json())
        assert r.get_json()['error'] == 'PIN must be exactly 4 digits.'
    assert _user(db_path, user_id)['pin_hash'] is None


def test_clearing_a_pin_leaves_the_account_otherwise_untouched(admin, db_path):
    """A PIN is not a credential -- removing it must not disable the account or
    revoke anything."""
    user_id, _ = _make_employee(admin, 'manager')
    admin.put(f'/api/admin/employees/{user_id}/pin', json={'pin': '9182'})
    before = _user(db_path, user_id)

    r = admin.delete(f'/api/admin/employees/{user_id}/pin')
    assert r.status_code == 200 and r.get_json()['has_pin'] is False

    after = _user(db_path, user_id)
    assert after['pin_hash'] is None
    assert after['status'] == before['status']
    assert after['role'] == before['role']
    assert after['password_hash'] == before['password_hash']


def test_setting_a_pin_does_not_revoke_the_persons_session(admin, db_path):
    """Deliberate asymmetry with every other write in this file. A PIN changes
    only which user id gets stamped on rows; bumping session_version here would
    sign a cashier out of a till mid-sale to propagate a change that alters
    none of their permissions. The row still has to be marked dirty for sync."""
    user_id, _ = _make_employee(admin)
    before = _user(db_path, user_id)

    assert admin.put(f'/api/admin/employees/{user_id}/pin',
                     json={'pin': '5150'}).status_code == 200

    after = _user(db_path, user_id)
    assert after['session_version'] == before['session_version'], \
        "a PIN is attribution, not authorization -- it must not log anybody out"
    assert after['row_version'] == before['row_version'] + 1


def test_the_pin_never_reaches_the_audit_log(admin, db_path):
    """An audit row that carried the PIN would turn the audit trail into a
    credential store readable by anyone with the reports screen."""
    user_id, _ = _make_employee(admin)
    admin.put(f'/api/admin/employees/{user_id}/pin', json={'pin': '3691'})

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT action, new_value_json FROM audit_logs WHERE entity_id=?", (user_id,)
        ).fetchall()
    finally:
        conn.close()

    assert any(r[0] == 'SET_PIN' for r in rows)
    assert all('3691' not in (r[1] or '') for r in rows)


def test_pin_routes_are_admin_only(app, admin, db_path):
    """401, not 403 -- see test_role_change_is_admin_only's comment: an
    anonymous caller is refused by `@mt_login_required` before the route's
    own role check ever runs."""
    user_id, _ = _make_employee(admin)
    anonymous = app.test_client()
    assert anonymous.put(f'/api/admin/employees/{user_id}/pin',
                         json={'pin': '1111'}).status_code == 401
    assert anonymous.delete(f'/api/admin/employees/{user_id}/pin').status_code == 401
    assert _user(db_path, user_id)['pin_hash'] is None


# ── Status change (AUDIT-032-adjacent gap: update_status had none of ────────
# ── update_role's guards) ────────────────────────────────────────────────────

def test_status_round_trips_between_active_and_disabled(admin, db_path):
    user_id, _ = _make_employee(admin)
    assert admin.put(f'/api/admin/employees/{user_id}/status',
                     json={'status': 'disabled'}).status_code == 200
    assert _user(db_path, user_id)['status'] == 'disabled'

    assert admin.put(f'/api/admin/employees/{user_id}/status',
                     json={'status': 'active'}).status_code == 200
    assert _user(db_path, user_id)['status'] == 'active'


def test_an_unrecognized_status_value_is_refused_with_the_exact_literal(admin, db_path):
    """The real domain the frontend's own `_setStatus` control ever sends
    (employees.js) is exactly {'active', 'disabled'} -- 'pending_setup' is a
    state an account starts in and leaves only through the invite/setup flow,
    never through this admin toggle."""
    user_id, _ = _make_employee(admin)
    for bad in ('banana', '', None, 'PENDING_SETUP', 'ACTIVE', 'pending_setup'):
        r = admin.put(f'/api/admin/employees/{user_id}/status', json={'status': bad})
        assert r.status_code == 400, (bad, r.get_json())
        assert r.get_json()['error'] == 'Status must be active or disabled.'
    assert _user(db_path, user_id)['status'] == 'pending_setup', "a refused change must change nothing"


def test_status_change_404s_for_an_unknown_user(admin, db_path):
    ghost = str(uuid.uuid4())
    r = admin.put(f'/api/admin/employees/{ghost}/status', json={'status': 'disabled'})
    assert r.status_code == 404


def test_status_change_cannot_reach_another_tenants_row(app, admin, db_path):
    conn = sqlite3.connect(str(db_path))
    other = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status) "
        "VALUES (?, 'other-company', 'EMP-9999', 'other-status@elsewhere.local', 'x', 'cashier', 'active')",
        (other,))
    conn.commit()
    conn.close()

    r = admin.put(f'/api/admin/employees/{other}/status', json={'status': 'disabled'})
    assert r.status_code == 404
    assert _user(db_path, other)['status'] == 'active'


def test_the_owner_account_cannot_be_disabled(admin, db_path):
    """Disabling the sole admin is UNRECOVERABLE: the disabled admin still
    has a real password_hash, so `onboarding_status` keeps answering
    needs_setup=false and create-admin keeps answering 409, while
    `authenticate_registry_user` refuses the login -- and every route that
    could undo it sits behind the admin session nobody can obtain any more."""
    owner_id = next(e['id'] for e in admin.get('/api/admin/employees').get_json()['employees']
                    if e['effective_role'] == 'admin')

    r = admin.put(f'/api/admin/employees/{owner_id}/status', json={'status': 'disabled'})
    assert r.status_code == 409, r.get_json()
    assert r.get_json()['error'] == 'You cannot disable the owner account.'
    assert _user(db_path, owner_id)['status'] == 'active'


def test_reactivating_the_owner_account_is_still_allowed(admin, db_path):
    """The owner bar guards DISABLING the owner, not every status write to
    that row -- setting an already-active owner to 'active' is a harmless
    no-op and must not be swept up by the same refusal."""
    owner_id = next(e['id'] for e in admin.get('/api/admin/employees').get_json()['employees']
                    if e['effective_role'] == 'admin')

    r = admin.put(f'/api/admin/employees/{owner_id}/status', json={'status': 'active'})
    assert r.status_code == 200, r.get_json()


def test_status_change_is_admin_only(app, admin, db_path):
    user_id, _ = _make_employee(admin)
    anonymous = app.test_client()
    r = anonymous.put(f'/api/admin/employees/{user_id}/status', json={'status': 'disabled'})
    assert r.status_code == 401
    assert _user(db_path, user_id)['status'] == 'pending_setup'


# ── Permissions (update_perms is not tenant-scoped) ─────────────────────────

def test_permissions_change_cannot_reach_another_tenants_user(app, admin, db_path):
    """In the shared-registry design, without this an admin of company A
    could rewrite permissions for a user of company B."""
    conn = sqlite3.connect(str(db_path))
    other = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status) "
        "VALUES (?, 'other-company', 'EMP-9999', 'other-perm@elsewhere.local', 'x', 'cashier', 'active')",
        (other,))
    conn.commit()
    conn.close()

    r = admin.post(f'/api/admin/employees/{other}/permissions',
                   json={'subsystem': user_accounts.CAP_SELL, 'access_level': 'full'})
    assert r.status_code == 404
    assert _caps(db_path, other).get(user_accounts.CAP_SELL) != user_accounts.ACCESS_FULL


def test_permissions_change_404s_for_an_unknown_user(admin, db_path):
    ghost = str(uuid.uuid4())
    r = admin.post(f'/api/admin/employees/{ghost}/permissions',
                   json={'subsystem': user_accounts.CAP_SELL, 'access_level': 'full'})
    assert r.status_code == 404


def test_an_unrecognized_access_level_is_refused(admin, db_path):
    user_id, _ = _make_employee(admin)
    r = admin.post(f'/api/admin/employees/{user_id}/permissions',
                   json={'subsystem': user_accounts.CAP_SELL, 'access_level': 'partial'})
    assert r.status_code == 400
    assert r.get_json()['error'] == 'access_level must be full or none.'
    assert user_accounts.CAP_SELL not in _caps(db_path, user_id) or \
        _caps(db_path, user_id)[user_accounts.CAP_SELL] != 'partial'


def test_an_unrecognized_subsystem_is_refused(admin, db_path):
    user_id, _ = _make_employee(admin)
    r = admin.post(f'/api/admin/employees/{user_id}/permissions',
                   json={'subsystem': 'not-a-real-subsystem', 'access_level': 'full'})
    assert r.status_code == 400
    assert r.get_json()['error'] == 'subsystem is not recognized.'
    assert 'not-a-real-subsystem' not in _caps(db_path, user_id)


def test_the_legacy_subsystem_values_are_still_accepted(admin, db_path):
    """`mt_require_subsystem` reads the legacy subsystem='retail' row on ~85
    Retail routes and subsystem='clinic' on Clinic's -- the new validation
    must not break either."""
    user_id, _ = _make_employee(admin)
    for legacy in ('retail', 'clinic'):
        r = admin.post(f'/api/admin/employees/{user_id}/permissions',
                       json={'subsystem': legacy, 'access_level': 'full'})
        assert r.status_code == 200, (legacy, r.get_json())
    caps = _caps(db_path, user_id)
    assert caps['retail'] == 'full'
    assert caps['clinic'] == 'full'


def test_every_namespaced_capability_code_is_accepted(admin, db_path):
    user_id, _ = _make_employee(admin)
    for code in user_accounts.CAPABILITY_CODES:
        r = admin.post(f'/api/admin/employees/{user_id}/permissions',
                       json={'subsystem': code, 'access_level': 'none'})
        assert r.status_code == 200, (code, r.get_json())


def test_permissions_change_is_admin_only(app, admin, db_path):
    user_id, _ = _make_employee(admin)
    anonymous = app.test_client()
    r = anonymous.post(f'/api/admin/employees/{user_id}/permissions',
                       json={'subsystem': user_accounts.CAP_SELL, 'access_level': 'full'})
    assert r.status_code == 401


# ── /api/auth/session -- capabilities (rendering advice only) ───────────────
#
# The retail frontend fetches a subsystem-gated dashboard route unconditionally
# on load; a cashier's first screen after login used to be a 403 because
# nothing told the client what the signed-in user can actually reach. This
# adds a `capabilities` list to the session-status response so the frontend
# can hide what the server would refuse anyway -- ADVICE for rendering, never
# a substitute for each route's own gate.

def _login_as(app, db_path, user_id):
    """Build a session for `user_id` the way a real login would leave it,
    without needing a password round trip -- mirrors test_device_routes.py's
    own `_login` helper in this package."""
    company_id = _user(db_path, user_id)['company_id']
    email = _user(db_path, user_id)['email']
    role = _user(db_path, user_id)['role']
    session_version = _user(db_path, user_id)['session_version']
    client = app.test_client()
    with client.session_transaction() as s:
        s['mt_user_id'] = user_id
        s['company_id'] = company_id
        s['mt_role'] = role
        s['email'] = email
        s['mt_session_version'] = session_version
    return client


def test_the_owners_session_reports_all_eight_capabilities(admin):
    body = admin.get('/api/auth/session').get_json()
    assert body['authenticated'] is True
    assert body['capabilities'] == sorted(user_accounts.CAPABILITY_CODES)


def test_a_cashiers_session_reports_exactly_the_cashier_role_defaults(app, admin, db_path):
    user_id, _ = _make_employee(admin, 'cashier')
    staff = _login_as(app, db_path, user_id)

    body = staff.get('/api/auth/session').get_json()
    assert body['authenticated'] is True
    assert body['capabilities'] == sorted(user_accounts.capabilities_for_role('cashier'))


def test_session_capabilities_reflect_a_per_user_override_not_the_role_default(app, admin, db_path):
    """Per-user overrides are the whole point of the user_permissions table --
    sending the role default here would make the UI disagree with the server
    the moment an owner turns one code off for one person."""
    user_id, _ = _make_employee(admin, 'cashier')
    assert admin.post(f'/api/admin/employees/{user_id}/permissions',
                      json={'subsystem': user_accounts.CAP_SELL, 'access_level': 'none'}).status_code == 200

    staff = _login_as(app, db_path, user_id)
    caps = staff.get('/api/auth/session').get_json()['capabilities']
    assert user_accounts.CAP_SELL not in caps, \
        "an explicit per-user denial must not be masked by the role default"
    assert user_accounts.CAP_REFUND in caps, "the rest of the role default must survive"


def test_session_capabilities_never_leak_the_legacy_subsystem_row(app, admin, db_path):
    user_id, _ = _make_employee(admin, 'cashier')
    assert admin.post(f'/api/admin/employees/{user_id}/permissions',
                      json={'subsystem': 'retail', 'access_level': 'full'}).status_code == 200

    staff = _login_as(app, db_path, user_id)
    caps = staff.get('/api/auth/session').get_json()['capabilities']
    assert 'retail' not in caps


def test_session_capabilities_are_sorted(admin):
    body = admin.get('/api/auth/session').get_json()
    assert body['capabilities'] == sorted(body['capabilities'])


def test_session_endpoint_requires_a_live_session(app):
    """The decision this phase made for this route: it is the mechanism a
    fresh client uses to LEARN whether it is authenticated, so an anonymous
    call must not 500 or leak stale data -- but once a session cookie exists
    it is now re-validated against the database like every other route in
    this file, rather than trusted verbatim from the cookie."""
    anonymous = app.test_client()
    r = anonymous.get('/api/auth/session')
    assert r.status_code == 401


def test_session_endpoint_refuses_a_disabled_accounts_stale_cookie(app, admin, db_path):
    user_id = next(e['id'] for e in admin.get('/api/admin/employees').get_json()['employees']
                   if e['effective_role'] == 'admin')
    staff = _login_as(app, db_path, user_id)
    assert staff.get('/api/auth/session').get_json()['authenticated'] is True

    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE users SET status='disabled' WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

    r = staff.get('/api/auth/session')
    assert r.status_code == 401, \
        "a disabled account's browser must not keep believing it is authenticated"
