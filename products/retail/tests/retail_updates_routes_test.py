"""
Aura Retail -- the update channel as the REAL app serves it, with no Owner.

The default install has no Owner URL. For that install the update channel must
be invisible: /api/updates/status says it is off, the actions refuse with 409,
no scheduler exists, and no network call is possible. This boots the real
app.py (not a bare Flask app) so what is proved is the registered blueprint and
the module-level wiring, not a unit double.

Also proved here, against the real session/login stack:
  * a cashier may read the status but is refused every action (403), and an
    administrator is let through to the (disabled) 409 -- both directions;
  * the blueprint is reachable in a RESTRICTED/EXPIRED licence state: an
    expired shop must still be able to install the build that may fix it.

Self-contained bootstrap, matching retail_printer_routes_test.py (no shared
conftest.py exists here).

Run:
    pytest products/retail/tests/retail_updates_routes_test.py -v
"""
import dataclasses
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_updates_routes_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
# The shell this runs in may export an Owner URL; this file is about the install
# that has none, so it must not inherit one.
os.environ.pop("AURA_OWNER_LICENSING_URL", None)
os.environ.pop("AURA_SYNC_RELAY_URL", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_user(role):
    email = f"updates-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "UpdatesRoutesPW1"
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), role, "active"),
    )
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    conn.commit()
    conn.close()
    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client


@pytest.fixture(scope='module')
def admin():
    return _make_user('admin')


@pytest.fixture(scope='module')
def cashier():
    return _make_user('cashier')


def test_the_default_install_has_no_update_service_and_no_scheduler():
    # The wiring itself, not just the HTTP answer: no Owner URL -> nothing is
    # built, so there is no timer that could ever wake up and call out.
    assert _app_module.OWNER_LICENSING_BASE_URL == ''
    assert _app_module._update_service is None
    assert _app_module._update_scheduler is None


def test_status_says_updates_are_off_and_why(admin):
    r = admin.get('/api/updates/status')
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body['enabled'] is False
    assert body['reason_disabled'] == 'OWNER_NOT_CONFIGURED'
    assert body['current_version'] == _app_module.APP_VERSION
    assert body['available'] is None and body['can_install'] is False


def test_the_update_channel_wrote_nothing_to_disk():
    assert not (DATA / 'updates').exists()


def test_status_needs_a_signed_in_user():
    assert app.test_client().get('/api/updates/status').status_code == 401


def test_a_cashier_can_read_status(cashier):
    assert cashier.get('/api/updates/status').status_code == 200


@pytest.mark.parametrize('action', ['check', 'download', 'install'])
def test_a_cashier_is_refused_every_action(cashier, action):
    assert cashier.post('/api/updates/' + action).status_code == 403


@pytest.mark.parametrize('action', ['check', 'download', 'install'])
def test_an_administrator_gets_through_the_guard_to_disabled(admin, action):
    r = admin.post('/api/updates/' + action)
    assert r.status_code == 409
    assert r.get_json() == {'error_code': 'UPDATES_DISABLED', 'reason_disabled': 'OWNER_NOT_CONFIGURED'}


def test_an_unauthenticated_caller_is_refused_every_action():
    c = app.test_client()
    for action in ('check', 'download', 'install'):
        assert c.post('/api/updates/' + action).status_code == 401


@pytest.mark.parametrize('state', ['EXPIRED', 'RESTRICTED'])
def test_the_channel_is_not_blocked_by_a_restricted_or_expired_licence(admin, state):
    db = DATA / "database" / "subsystems" / "licensing.db"
    repo = LicenseStateRepository(db)
    original = repo.load()
    try:
        repo.save(dataclasses.replace(original, current_state=state))
        assert repo.load().current_state == state            # the state really changed
        # CONTROL: a genuinely licence-guarded retail route IS refused in this
        # state (the guard fires before the route's own 400 for an empty body).
        # Without this, "updates still answers" could be true only because the
        # licence guard was never in effect for this process at all.
        control = admin.post('/api/sub/retail/categories', json={})
        assert control.status_code != 400, control.get_json()
        r = admin.get('/api/updates/status')
        assert r.status_code == 200
        assert r.get_json()['reason_disabled'] == 'OWNER_NOT_CONFIGURED'   # its own answer, not a licence refusal
        assert admin.post('/api/updates/check').get_json()['error_code'] == 'UPDATES_DISABLED'
    finally:
        repo.save(original)
