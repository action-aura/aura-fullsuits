"""
Aura Retail -- a cashier created from the real Employees screen must be able
to use the retail app it was created for.

This file stands on the REAL creation route (`POST /api/admin/employees`)
with the EXACT body the Employees screen sends (`{email, role}`, nothing
else) and the real `seed_capabilities_for_user` seeding, deliberately never
hand-inserting a `user_permissions` row of its own. That distinction matters:
27 retail test fixtures (e.g. retail_route_capability_matrix_test.py's
`_make_user`) hand-insert a legacy `('retail', 'full')` row before every
request, manufacturing exactly the row production never writes -- and that is
precisely what let a real lockout ship and stay green through every one of
those suites. `mt_require_subsystem('retail')` used to demand that bare row;
nothing the product creates ever wrote it, so every cashier and manager made
through the Employees screen was refused every retail route with "Access
denied to retail. Contact your Admin." on both a real phone and a real
desktop till, while holding `retail.sell=full` (observed 2026-09-05).

Run:
    pytest products/retail/tests/retail_employee_created_via_screen_can_work_test.py -v
"""
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
FRONTEND_DIR = PRODUCT_DIR / 'frontend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_emp_can_work_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402

OWNER_EMAIL = 'emp-can-work-owner@test.local'
OWNER_PASSWORD = 'OwnerCanWorkPW1'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _owner():
    """The one admin this install allows. `create-admin` is gated on "no valid
    admin exists", so the first caller creates and every later caller logs in
    -- same shape as retail_employee_management_test.py's `_owner`."""
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': OWNER_EMAIL, 'password': OWNER_PASSWORD,
    })
    if r.status_code == 409:
        r = client.post('/api/auth/login', json={'email': OWNER_EMAIL, 'password': OWNER_PASSWORD})
    assert r.status_code == 200, r.get_json()
    return client


def _new_employee(owner, role='cashier'):
    """Through the REAL creation route with the EXACT body the Employees
    screen sends -- `{email, role}` and nothing else (employees.js:611,
    Android AuraApi.kt:315). No `permissions` key, ever: that is the whole
    point of this file."""
    email = f'emp-{uuid.uuid4().hex[:8]}@test.local'
    r = owner.post('/api/admin/employees', json={'email': email, 'role': role})
    assert r.status_code == 200, r.get_json()
    conn = registry_conn()
    try:
        user_id = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()['id']
    finally:
        conn.close()
    return user_id, email, r.get_json()


def _caps(user_id):
    conn = registry_conn()
    try:
        return {
            r['subsystem']: r['access_level']
            for r in conn.execute("SELECT subsystem, access_level FROM user_permissions WHERE user_id=?",
                                  (user_id,)).fetchall()
        }
    finally:
        conn.close()


def _activate(user_id, password):
    """Give an invited (`pending_setup`, password_hash='PENDING') account a
    real password so it can log in, without going through the emailed invite
    -- the invite flow has its own coverage; these tests need a live session
    belonging to a non-owner."""
    conn = registry_conn()
    conn.execute("UPDATE users SET password_hash=?, status='active' WHERE id=?",
                 (hash_password(password), user_id))
    conn.commit()
    conn.close()


def _login(email, password):
    """A NEW client, logged in as `email` -- a permission or role change bumps
    `session_version`, so any test that changes permissions after creation
    must log in AFTER the change, not reuse a session opened before it."""
    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client


# ═════════════════════════════════════════════════════════════════════════════
# The regression: a screen-created cashier can actually use the app
# ═════════════════════════════════════════════════════════════════════════════

def test_a_cashier_created_from_the_employees_screen_can_use_the_retail_app():
    owner = _owner()
    user_id, email, _payload = _new_employee(owner, 'cashier')

    caps = _caps(user_id)
    # This is the production shape: seed_capabilities_for_user writes ONLY
    # the eight namespaced codes. If this assertion ever fails, the fixture
    # has started manufacturing the legacy row that hid the lockout, and the
    # test below would stop being a regression test for anything real.
    assert 'retail' not in caps, caps
    assert caps[user_accounts.CAP_SELL] == user_accounts.ACCESS_FULL

    _activate(user_id, 'CashierPW11')
    client = _login(email, 'CashierPW11')

    r = client.get('/api/sub/retail/customers')
    assert r.status_code == 200, r.get_json()

    r = client.post('/api/sub/retail/customers', json={
        'name': 'Screen Cashier Customer', 'phone': '0790000001',
    })
    assert r.status_code in (200, 201), r.get_json()
    body = r.get_json() or {}
    assert body.get('error') != 'Access denied to retail. Contact your Admin.', body


def test_an_explicit_legacy_revocation_still_locks_the_cashier_out():
    """The fix must not make the coarse gate un-revokable: an admin flipping
    the legacy 'retail' row to 'none' through update_perms is a deliberate
    decision and must still win over every granted capability code."""
    owner = _owner()
    user_id, email, _payload = _new_employee(owner, 'cashier')
    _activate(user_id, 'CashierPW12')

    r = owner.post(f'/api/admin/employees/{user_id}/permissions', json={
        'subsystem': 'retail', 'access_level': 'none',
    })
    assert r.status_code == 200, r.get_json()

    # Log in AFTER the permission change -- it bumps session_version, and a
    # session opened before it would be refused for the WRONG reason (a
    # stale session), masking whether the subsystem gate itself works.
    client = _login(email, 'CashierPW12')

    r = client.get('/api/sub/retail/customers')
    assert r.status_code == 403, r.get_json()
    assert r.get_json()['error'] == 'Access denied to retail. Contact your Admin.'


def test_a_cashier_denied_every_capability_is_refused():
    """No legacy row, and every namespaced code explicitly revoked: the
    derived gate has nothing left to grant it, so it must deny -- same
    reasoning as user_holds_subsystem's own 'no rows at all' case, just
    reached by revoking every code instead of writing none of them."""
    owner = _owner()
    user_id, email, _payload = _new_employee(owner, 'cashier')
    _activate(user_id, 'CashierPW13')

    for code in user_accounts.capabilities_for_role('cashier'):
        r = owner.post(f'/api/admin/employees/{user_id}/permissions', json={
            'subsystem': code, 'access_level': 'none',
        })
        assert r.status_code == 200, r.get_json()

    client = _login(email, 'CashierPW13')

    r = client.get('/api/sub/retail/customers')
    assert r.status_code == 403, r.get_json()
    assert r.get_json()['error'] == 'Access denied to retail. Contact your Admin.'
