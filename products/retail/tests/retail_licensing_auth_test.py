"""
Aura Retail -- P0-1 licensing HTTP-surface authentication regression suite.

AUDIT P0-1 (see docs/audit or commercial_runtime/licensing_contracts/routes.py's
make_licensing_blueprint auth_required docstring): before this fix,
/api/licensing/activate, /check-in, and /deactivate took NO authentication at
all, and /deactivate in particular read no request body either -- combined
with no CSRFProtect anywhere in this codebase and CORS only ever blocking
*reading* a cross-origin response (never blocking a form-encoded POST from
being *sent*), any website the user's browser merely visited while this app
was running could silently kill the license via a hidden
<form method="POST" action="http://127.0.0.1:5000/api/licensing/deactivate">
-- a genuine drive-by remote license kill, zero user interaction required.

The fix threads Retail's own mt_login_required session decorator into
make_licensing_blueprint's new auth_required parameter (products/retail/
backend/app.py) and adds an independent, auth-agnostic Content-Type check
(commercial_runtime/licensing_contracts/routes.py) that rejects any
non-JSON POST outright -- a plain HTML <form> can never set
Content-Type: application/json, so this closes the same hole even if auth
were ever accidentally relaxed later.

Mirrors retail_capability_guard_test.py's real-app bootstrap pattern (spins
up the actual products/retail/backend/app.py Flask app against a throwaway
temp AURA_APP_DATA directory) rather than constructing an isolated Flask app
by hand, specifically so this proves the REAL wiring in app.py -- that
mt_login_required is actually threaded through -- not just the shared
routes.py factory in isolation (that isolation-level coverage already lives
in commercial_runtime/licensing_contracts/tests/test_routes.py).

Run:
    pytest products/retail/tests/retail_licensing_auth_test.py -v
"""
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_licensing_auth_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
# Deliberately do NOT seed_active_license here -- these tests care about the
# auth/content-type gate itself, which must apply the same way regardless of
# licensing state (NOT_CONFIGURED is the real default: OWNER_LICENSING_BASE_URL
# is empty unless AURA_OWNER_LICENSING_URL is set, see config.py).
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin_client():
    email = f'lic-auth-{uuid.uuid4().hex[:8]}@test.local'
    password = 'LicensingAuthPW1'
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return c


# -- /deactivate: the single most exploitable route in the audit -----------

def test_unauthenticated_deactivate_is_401():
    anon_client = app.test_client()
    resp = anon_client.post('/api/licensing/deactivate')
    assert resp.status_code == 401


def test_authenticated_form_encoded_deactivate_is_415():
    # Even WITH a real session, a form-encoded body (the one shape a plain
    # HTML <form> can actually produce) must still be rejected -- this is
    # the defense-in-depth layer that holds even if auth is ever
    # accidentally relaxed later.
    c = _make_admin_client()
    resp = c.post('/api/licensing/deactivate', data={'anything': 'here'})
    assert resp.status_code == 415


def test_authenticated_json_deactivate_succeeds_past_auth_gate():
    # No active license was seeded, so this is a real no-op deactivation --
    # the point is proving it's no longer blocked at 401/415 for a genuine
    # logged-in session sending an actual JSON body.
    c = _make_admin_client()
    resp = c.post('/api/licensing/deactivate', json={})
    assert resp.status_code == 200
    assert resp.get_json()['state'] == 'NOT_CONFIGURED'


# -- /activate and /check-in: same auth_required gate -----------------------

def test_unauthenticated_activate_is_401():
    anon_client = app.test_client()
    resp = anon_client.post('/api/licensing/activate', json={'license_key': 'AURA-RETAIL-XXXX'})
    assert resp.status_code == 401


def test_unauthenticated_checkin_is_401():
    anon_client = app.test_client()
    resp = anon_client.post('/api/licensing/check-in')
    assert resp.status_code == 401


def test_authenticated_checkin_succeeds_past_auth_gate():
    c = _make_admin_client()
    resp = c.post('/api/licensing/check-in', json={})
    assert resp.status_code == 200
    # No Owner URL configured in this test environment -- NOT_CONFIGURED is
    # the correct, already-tested-elsewhere shape; the point here is only
    # that it is reachable (200, not 401/415) once authenticated.
    assert resp.get_json()['current_state'] == 'NOT_CONFIGURED'


# -- /status: the deliberate exception -- must stay reachable with NO auth --

def test_status_remains_reachable_without_auth():
    anon_client = app.test_client()
    resp = anon_client.get('/api/licensing/status')
    assert resp.status_code == 200
    assert resp.get_json()['current_state'] == 'NOT_CONFIGURED'


# -- /_internal/* -- untouched by this change; Retail never registers them
# on Windows (no AURA_INTERNAL_SHARED_SECRET set), so they simply don't
# exist as routes at all. The shared-module behavior of those routes
# (unaffected by auth_required, still gated only by their own
# hmac.compare_digest shared-secret check) is covered directly by
# commercial_runtime/licensing_contracts/tests/test_internal_sync_routes.py,
# which passes unchanged by this fix.

def test_internal_routes_not_registered_on_retail_windows():
    anon_client = app.test_client()
    resp = anon_client.post('/api/licensing/_internal/reevaluate')
    assert resp.status_code == 404


# -- the licensing UI itself still functions from an authenticated session --

def test_licensing_ui_static_files_still_served():
    # licensing.html/licensing.js are served as ordinary static files under
    # this app's static_url_path='/static' (see app.py's Flask(...) call) --
    # no auth gate on the static asset itself, only the API calls it makes
    # are now gated. This just confirms the P0-1 change didn't collaterally
    # break serving the page a logged-in user would navigate to.
    c = _make_admin_client()
    html_resp = c.get('/static/licensing.html')
    assert html_resp.status_code == 200
    js_resp = c.get('/static/licensing.js')
    assert js_resp.status_code == 200
    assert b'/api/licensing/status' in js_resp.data
