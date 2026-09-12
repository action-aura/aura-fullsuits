"""Licence gate on the admin/employee-management mutation surface --
onboarding_routes.py.

AUDIT: `/api/admin/employees` and its siblings (status/branch-scope/role/
pin/clinic-role/permissions, plus the company-settings POST branch) carried
`@mt_login_required` ONLY -- no licence check anywhere in this file, unlike
every OTHER mutation surface in both products (retail_api.py's
`require_license_capability(..., restricted_mode_allowlist=
RETAIL_RESTRICTED_ALLOWLIST)`, clinic_api.py's identical pattern). An
install whose licence was EXPIRED, SUSPENDED, REVOKED, or never activated
could still mint staff accounts, change roles, re-scope branches and grant
permissions. This file pins the fix: every admin-mutation route in
onboarding_routes.py now requires an ACTIVE licence state
(`identity.employee.manage` / `identity.company.settings.manage`, evaluated
against an intentionally EMPTY restricted-mode allowlist -- see
onboarding_routes.py's own block comment for why none of this is a
continuity-of-care/business-continuity exception), while the first-admin /
onboarding / login-recovery chain (create-admin, verify-email, resend-
verification, forgot/reset-password, complete-onboarding, employee-setup)
stays completely UNGATED -- gating any of those would lock an install out of
ever reaching a state where a licence could even be activated.

Both halves matter equally here (see ENGINEERING.md's "prove both directions
of anything that both denies and allows"):
  DENY  -- a non-ACTIVE licence state refuses every gated route with 403 AND
           leaves the database byte-for-byte unchanged (not merely "still
           returns a row" -- the exact row, compared field for field).
  ALLOW -- an ACTIVE licence state leaves every gated route working exactly
           as it did before this fix. If this half is wrong, a shop that has
           genuinely paid cannot manage its own staff, which is worse than
           the defect being fixed.
  LOCK-OUT GUARD -- a fresh install with NO licence configured at all can
           still complete first-admin setup and reach a live session. This
           is the test that stops someone later "tightening" the gate onto
           a product nobody can install.

Standalone, same bootstrap idiom as test_employee_admin_routes.py /
test_employee_delegation.py in this package (`registry_db.DB_PATH`
redirected at a temp file, the real schema built through
`init_registry_db()`) -- see that file's own docstring for why. UNLIKE those
two files, this one isolates `AURA_APP_DATA` to a private temp directory
BEFORE importing onboarding_routes.py (mirroring products/retail/tests/
retail_route_capability_matrix_test.py's own module-level pattern), so the
licence check this file exercises goes through the REAL
`LicenseStateRepository`/`LicenseStateRecord` machinery against a REAL,
isolated sqlite `licensing.db` -- no monkeypatching of flask_guard internals
here, and no risk of writing into the repo's own working tree the way an
unisolated import would (onboarding_routes.py's `require_license_capability`
closure is built once, at ITS OWN import time, over whatever `AURA_APP_DATA`
resolves to then).

Run:
    pytest commercial_runtime/identity/tests/test_admin_mutation_license_gate.py -v
"""
import os
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path

import pytest

# ── Isolate AURA_APP_DATA BEFORE onboarding_routes.py is ever imported ─────
#
# onboarding_routes.py resolves `AURA_APP_DATA` (falling back to this
# package's own parent directory) exactly once, at ITS OWN import time, to
# build `require_license_capability = make_capability_guard(...)`. Setting
# this here, before the import below, is what makes the closure's licensing
# database live in a private temp directory rather than the real repo's
# `commercial_runtime/database/subsystems/licensing.db` (gitignored, but
# still real, unwanted disk I/O -- see test_employee_admin_routes.py's own
# `app` fixture comment for the sibling-file version of this same trap).
_LICENSING_APP_DATA = Path(tempfile.mkdtemp(prefix="aura_onboarding_license_gate_test_"))
(_LICENSING_APP_DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ["AURA_APP_DATA"] = str(_LICENSING_APP_DATA)

from flask import Flask  # noqa: E402

from commercial_runtime.identity import mt_auth, onboarding_routes, registry_db, user_accounts  # noqa: E402
from commercial_runtime.identity.onboarding_routes import onboarding_bp  # noqa: E402
from commercial_runtime.licensing_contracts.state_repository import (  # noqa: E402
    LicenseStateRecord,
    LicenseStateRepository,
)


def teardown_module(module):
    shutil.rmtree(_LICENSING_APP_DATA, ignore_errors=True)


# ── Licence-state helpers (real repository, real sqlite file) ──────────────

def _licensing_db_path() -> Path:
    """Same `database/subsystems/licensing.db` layout
    flask_guard.make_capability_guard's own `db_path` formula uses."""
    return _LICENSING_APP_DATA / "database" / "subsystems" / "licensing.db"


def _set_license_state(state: str, **extra):
    LicenseStateRepository(_licensing_db_path()).save(
        LicenseStateRecord(
            licensing_schema_version=1, product_code="AURA_TEST", platform="WINDOWS",
            current_state=state, **extra,
        )
    )


def _clear_license_state():
    """The real 'no licence configured at all' shape: no saved row, so
    `LicenseStateRepository.load()` returns `None` and flask_guard resolves
    that to `NOT_CONFIGURED` -- NOT a state literally named NOT_CONFIGURED
    written by `_set_license_state`. Both routes end up denied identically
    (see `evaluate_capability`), but this is the shape a genuinely fresh
    install has, so the lock-out guard test uses this, not the string."""
    LicenseStateRepository(_licensing_db_path()).reset()


#: A representative spread of non-ACTIVE states -- not just one. RESTRICTED
#: is what an owner sees mid-grace-period; SUSPENDED/REVOKED are Owner-side
#: actions; EXPIRED is a lapsed subscription; NOT_CONFIGURED (via
#: `_clear_license_state`, exercised separately) is a fresh/never-activated
#: install. All four are in DATA_PRESERVED_FAMILY, none in ACTIVE_FAMILY
#: (state_machine.py) -- every one of them must deny every gated route here,
#: since `_ACCOUNT_ADMIN_RESTRICTED_ALLOWLIST` in onboarding_routes.py is
#: deliberately empty.
NON_ACTIVE_STATES = ["RESTRICTED", "SUSPENDED", "REVOKED", "EXPIRED"]


# ── Bootstrap (same idiom as test_employee_admin_routes.py) ────────────────

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
    # config.json (create_admin/company_settings) is resolved from
    # AURA_APP_DATA at CALL time (`_config_path()`), unlike the licence
    # gate's closure above, which is fixed at IMPORT time -- so this
    # per-test override is safe: it changes only where config.json lands,
    # never which licensing.db the gate reads.
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path))
    monkeypatch.setattr(mt_auth, "REGISTRY_DB", str(db_path))

    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"
    flask_app.testing = True
    flask_app.register_blueprint(onboarding_bp)
    return flask_app


@pytest.fixture
def admin(app):
    """A logged-in owner, created via the REAL create-admin route -- which
    must succeed regardless of licence state (it is the first exempt route),
    so this fixture deliberately does NOT set any licence state first. Tests
    that need an ACTIVE licence to create/modify an employee set it
    explicitly, at the point they need it."""
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner@test.local', 'password': 'OwnerPW11',
    })
    assert r.status_code == 200, r.get_json()
    return client


def _make_employee(admin, role=None):
    """Requires an ACTIVE licence -- caller must have set one first."""
    email = f'emp-{uuid.uuid4().hex[:8]}@test.local'
    body = {'email': email}
    if role:
        body['role'] = role
    r = admin.post('/api/admin/employees', json=body)
    assert r.status_code == 200, r.get_json()
    employees = admin.get('/api/admin/employees').get_json()['employees']
    row = next(e for e in employees if e['email'] == email)
    return row['id'], email


def _user_row(db_path, user_id):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _perm_rows(db_path, user_id):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return {r['subsystem']: r['access_level'] for r in conn.execute(
            "SELECT subsystem, access_level FROM user_permissions WHERE user_id=?", (user_id,))}
    finally:
        conn.close()


def _company_settings_row(db_path, company_id):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM company_settings WHERE company_id=?", (company_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════════════
# (a) DENY -- non-ACTIVE licence refuses every gated route, DB unchanged
# ═════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("state", NON_ACTIVE_STATES)
def test_create_employee_denied_under_a_non_active_licence_and_creates_nothing(app, admin, db_path, state):
    _set_license_state("ACTIVE_ONLINE")
    before_count = sqlite3.connect(str(db_path)).execute("SELECT COUNT(*) FROM users").fetchone()[0]

    _set_license_state(state)
    r = admin.post('/api/admin/employees', json={'email': f'denied-{state.lower()}@test.local'})
    assert r.status_code == 403, r.get_json()
    body = r.get_json()
    assert body.get('reason_code') == 'LICENSE_INACTIVE', body

    after_count = sqlite3.connect(str(db_path)).execute("SELECT COUNT(*) FROM users").fetchone()[0]
    assert after_count == before_count, "a denied create must not insert a row"


@pytest.mark.parametrize("state", NON_ACTIVE_STATES)
def test_update_status_denied_under_a_non_active_licence_and_changes_nothing(app, admin, db_path, state):
    _set_license_state("ACTIVE_ONLINE")
    user_id, _ = _make_employee(admin)
    before = _user_row(db_path, user_id)

    _set_license_state(state)
    r = admin.put(f'/api/admin/employees/{user_id}/status', json={'status': 'disabled'})
    assert r.status_code == 403, r.get_json()

    after = _user_row(db_path, user_id)
    assert after == before, "a denied status change must leave the row byte-for-byte unchanged"


@pytest.mark.parametrize("state", NON_ACTIVE_STATES)
def test_update_role_denied_under_a_non_active_licence_and_changes_nothing(app, admin, db_path, state):
    _set_license_state("ACTIVE_ONLINE")
    user_id, _ = _make_employee(admin, 'cashier')
    before = _user_row(db_path, user_id)
    before_perms = _perm_rows(db_path, user_id)

    _set_license_state(state)
    r = admin.put(f'/api/admin/employees/{user_id}/role', json={'role': 'manager'})
    assert r.status_code == 403, r.get_json()

    assert _user_row(db_path, user_id) == before
    assert _perm_rows(db_path, user_id) == before_perms, \
        "a denied role change must not reset the capability grid either"


@pytest.mark.parametrize("state", NON_ACTIVE_STATES)
def test_update_branch_scope_denied_under_a_non_active_licence_and_changes_nothing(app, admin, db_path, state):
    _set_license_state("ACTIVE_ONLINE")
    user_id, _ = _make_employee(admin, 'manager')
    before = _user_row(db_path, user_id)

    _set_license_state(state)
    r = admin.put(f'/api/admin/employees/{user_id}/branch-scope', json={'branch_scope_uid': 'branch-x'})
    assert r.status_code == 403, r.get_json()

    assert _user_row(db_path, user_id) == before


@pytest.mark.parametrize("state", NON_ACTIVE_STATES)
def test_update_pin_denied_under_a_non_active_licence_and_changes_nothing(app, admin, db_path, state):
    _set_license_state("ACTIVE_ONLINE")
    user_id, _ = _make_employee(admin)
    before = _user_row(db_path, user_id)
    assert before['pin_hash'] is None

    _set_license_state(state)
    r = admin.put(f'/api/admin/employees/{user_id}/pin', json={'pin': '1234'})
    assert r.status_code == 403, r.get_json()

    after = _user_row(db_path, user_id)
    assert after == before
    assert after['pin_hash'] is None, "a denied PIN set must not write a hash"


@pytest.mark.parametrize("state", NON_ACTIVE_STATES)
def test_update_clinic_role_denied_under_a_non_active_licence_and_changes_nothing(app, admin, db_path, state):
    _set_license_state("ACTIVE_ONLINE")
    user_id, _ = _make_employee(admin)
    before = _user_row(db_path, user_id)
    assert before['clinic_role'] in (None, '')

    _set_license_state(state)
    r = admin.put(f'/api/admin/employees/{user_id}/clinic-role', json={'clinic_role': 'doctor'})
    assert r.status_code == 403, r.get_json()

    assert _user_row(db_path, user_id) == before


@pytest.mark.parametrize("state", NON_ACTIVE_STATES)
def test_update_perms_denied_under_a_non_active_licence_and_changes_nothing(app, admin, db_path, state):
    _set_license_state("ACTIVE_ONLINE")
    user_id, _ = _make_employee(admin, 'cashier')
    before_perms = _perm_rows(db_path, user_id)
    before_user = _user_row(db_path, user_id)

    _set_license_state(state)
    r = admin.post(f'/api/admin/employees/{user_id}/permissions',
                   json={'subsystem': user_accounts.CAP_SELL, 'access_level': 'full'})
    assert r.status_code == 403, r.get_json()

    assert _perm_rows(db_path, user_id) == before_perms
    assert _user_row(db_path, user_id) == before_user, \
        "a denied grant must not bump session_version/row_version either"


@pytest.mark.parametrize("state", NON_ACTIVE_STATES)
def test_company_settings_post_denied_under_a_non_active_licence_and_changes_nothing(app, admin, db_path, state):
    _set_license_state("ACTIVE_ONLINE")
    # Read the admin's own company_id straight from the DB -- the session
    # dict does not expose it via /api/auth/session's response body.
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    company_id = conn.execute("SELECT company_id FROM users WHERE role='admin'").fetchone()['company_id']
    conn.close()
    before = _company_settings_row(db_path, company_id)

    _set_license_state(state)
    r = admin.post('/api/admin/company/settings', json={'country': 'JO'})
    assert r.status_code == 403, r.get_json()

    assert _company_settings_row(db_path, company_id) == before

    # The GET branch of the SAME route must stay reachable regardless --
    # only the mutation is licence-gated (design requirement: "GET/read
    # routes stay ungated").
    r_get = admin.get('/api/admin/company/settings')
    assert r_get.status_code == 200, r_get.get_json()


def test_denied_under_not_configured_ie_no_licence_row_at_all(app, admin, db_path):
    """NOT_CONFIGURED via a genuinely absent record (`_clear_license_state`),
    not the state string -- the shape a fresh, never-activated install
    actually has. Distinct test from the parametrized sweep above because
    the setup (`reset()`, not `save(current_state='NOT_CONFIGURED')`) is
    different, and this is the state every route in this file is in before
    a licence has ever been issued -- the single most common real case."""
    _set_license_state("ACTIVE_ONLINE")
    user_id, _ = _make_employee(admin)
    before = _user_row(db_path, user_id)

    _clear_license_state()
    r = admin.put(f'/api/admin/employees/{user_id}/status', json={'status': 'disabled'})
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('reason_code') == 'LICENSE_INACTIVE'
    assert _user_row(db_path, user_id) == before


# ═════════════════════════════════════════════════════════════════════════
# (b) ALLOW -- an ACTIVE licence leaves every gated route working
# ═════════════════════════════════════════════════════════════════════════

def test_every_gated_route_still_works_under_an_active_licence(app, admin, db_path):
    """The half that protects the paying shop. Exercises every one of the
    eight routes onboarding_routes.py now gates, in sequence, all under
    ACTIVE_ONLINE, and asserts each one actually took effect in the
    database -- not just that the HTTP status was 200."""
    _set_license_state("ACTIVE_ONLINE")

    # create_employee
    user_id, email = _make_employee(admin, 'cashier')
    assert _user_row(db_path, user_id) is not None

    # update_role
    r = admin.put(f'/api/admin/employees/{user_id}/role', json={'role': 'manager'})
    assert r.status_code == 200, r.get_json()
    assert _user_row(db_path, user_id)['role'] == 'manager'

    # update_branch_scope
    r = admin.put(f'/api/admin/employees/{user_id}/branch-scope', json={'branch_scope_uid': 'branch-allow'})
    assert r.status_code == 200, r.get_json()
    assert _user_row(db_path, user_id)['branch_scope_uid'] == 'branch-allow'

    # update_pin
    r = admin.put(f'/api/admin/employees/{user_id}/pin', json={'pin': '5150'})
    assert r.status_code == 200, r.get_json()
    assert _user_row(db_path, user_id)['pin_hash'] is not None

    # update_clinic_role
    r = admin.put(f'/api/admin/employees/{user_id}/clinic-role', json={'clinic_role': 'secretary'})
    assert r.status_code == 200, r.get_json()
    assert _user_row(db_path, user_id)['clinic_role'] == 'secretary'

    # update_perms
    r = admin.post(f'/api/admin/employees/{user_id}/permissions',
                   json={'subsystem': user_accounts.CAP_SELL, 'access_level': 'full'})
    assert r.status_code == 200, r.get_json()
    assert _perm_rows(db_path, user_id)[user_accounts.CAP_SELL] == 'full'

    # update_status
    r = admin.put(f'/api/admin/employees/{user_id}/status', json={'status': 'disabled'})
    assert r.status_code == 200, r.get_json()
    assert _user_row(db_path, user_id)['status'] == 'disabled'

    # company_settings (POST)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    company_id = conn.execute("SELECT company_id FROM users WHERE role='admin'").fetchone()['company_id']
    conn.close()
    r = admin.post('/api/admin/company/settings', json={'country': 'JO', 'currency': 'JOD'})
    assert r.status_code == 200, r.get_json()
    settings_row = _company_settings_row(db_path, company_id)
    assert settings_row['country'] == 'JO'
    assert settings_row['currency'] == 'JOD'


# ═════════════════════════════════════════════════════════════════════════
# (c) LOCK-OUT GUARD -- no licence at all must not block first-admin setup
# ═════════════════════════════════════════════════════════════════════════

def test_lockout_guard_fresh_install_can_still_create_the_first_admin_and_reach_a_live_session(app):
    """THE test that stops someone later 'tightening' this gate into a
    product nobody can install. No licence state is set anywhere in this
    test -- `_clear_license_state` is called explicitly to make that
    intent unmistakable, even though it is also simply the fixture's
    default. create-admin, and the session read straight after it, are the
    two calls a fresh install makes before any licence can exist at all."""
    _clear_license_state()

    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Fresh Owner', 'email': 'fresh-owner@test.local', 'password': 'OwnerPW11',
    })
    assert r.status_code == 200, r.get_json()

    session_resp = client.get('/api/auth/session')
    assert session_resp.status_code == 200
    body = session_resp.get_json()
    assert body['authenticated'] is True
    assert body['user']['role'] == 'admin'


def test_lockout_guard_onboarding_completion_works_with_no_licence(app, admin):
    """`complete_onboarding` is the final step of the setup wizard an admin
    walks through immediately after create-admin -- plausibly BEFORE a
    licence is entered at all. Must not be gated."""
    _clear_license_state()
    r = admin.post('/api/onboarding/complete')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['success'] is True


def test_lockout_guard_an_already_invited_employee_can_still_complete_setup_after_the_licence_lapses(app, admin, db_path):
    """The edge case most likely to be got wrong (design doc's own warning):
    `create_employee` mints a 7-day, single-use setup link and IS gated --
    it can only run under an ACTIVE licence. But the licence can genuinely
    lapse in the days between that invite being sent and the employee
    actually opening it. `employee_setup` (the route that consumes the
    token and sets the employee's own first password) must still work in
    that window -- it grants no NEW privilege the admin did not already
    decide under an active licence; it only lets an already-authorized hire
    reach the login this whole chain exists to protect. If this were gated
    too, a licence lapsing for even a day could strand every pending invite
    permanently (the link is single-use and time-limited, not re-issuable
    by the employee themselves)."""
    _set_license_state("ACTIVE_ONLINE")
    email = f'invited-{uuid.uuid4().hex[:8]}@test.local'
    r = admin.post('/api/admin/employees', json={'email': email})
    assert r.status_code == 200, r.get_json()
    raw_token = r.get_json()['setup_link'].rsplit('/', 1)[-1]

    # The licence lapses before the employee ever opens the invite.
    _clear_license_state()

    client = app.test_client()
    r = client.post('/api/auth/employee/setup', json={'token': raw_token, 'password': 'FirstPW11'})
    assert r.status_code == 200, r.get_json()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT status FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    assert row['status'] == 'active', "the invited employee must actually be able to log in after this"


def test_lockout_guard_a_second_admin_attempt_still_gets_the_ordinary_409_not_a_licence_403(app, admin):
    """Proves create-admin's exemption is real even past the "no admin yet"
    window -- the SECOND call still reaches the handler's own business logic
    (409, an admin already exists) rather than being intercepted by a
    licence gate that was never there to begin with."""
    _clear_license_state()
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Someone Else', 'email': 'someone-else@test.local', 'password': 'OwnerPW11',
    })
    assert r.status_code == 409, r.get_json()
    assert 'licen' not in (r.get_json().get('error') or '').lower()
