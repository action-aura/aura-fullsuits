"""
Aura Retail -- the update channel when an Owner IS configured.

The mirror image of retail_updates_routes_test.py (which proves the no-Owner
install is untouched): with an Owner URL and an activated licence, app.py must
build the service from the SAME licensing identity, arm the daily poll at boot
as a daemon timer, and report the channel as enabled. Nothing here ticks the
timer or calls out: the timer is armed for 120 s and stopped in teardown.

Run:
    pytest products/retail/tests/retail_updates_wiring_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_updates_wiring_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
                  AURA_OWNER_LICENSING_URL="https://owner.invalid/api/licensing/v1")
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_SYNC_RELAY_URL", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    if _app_module._update_scheduler is not None:
        _app_module._update_scheduler.stop()
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture(scope='module')
def admin():
    email = f"upd-wiring-{uuid.uuid4().hex[:8]}@test.local"
    conn = registry_conn()
    user_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, str(uuid.uuid4()), "EMP-1", email, hash_password("UpdatesWiringPW1"), "admin", "active"),
    )
    conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
                 (str(uuid.uuid4()), user_id, "retail", "full"))
    user_accounts.seed_capabilities_for_user(conn, user_id, "admin")
    conn.commit()
    conn.close()
    client = app.test_client()
    assert client.post('/api/auth/login', json={'email': email, 'password': 'UpdatesWiringPW1'}).status_code == 200
    return client


def test_service_and_scheduler_are_built_and_armed_at_boot():
    assert _app_module._update_service is not None
    scheduler = _app_module._update_scheduler
    assert scheduler is not None
    timer = scheduler._timer
    assert timer is not None and timer.daemon is True        # armed, and never keeps the app alive
    assert timer.interval == 120.0                           # first check 120 s after boot, not at once


def test_the_service_runs_startup_so_the_updates_folder_exists_at_boot():
    assert (DATA / 'updates').is_dir()


def test_status_is_enabled_for_an_activated_install(admin):
    body = admin.get('/api/updates/status').get_json()
    assert body['enabled'] is True and body['reason_disabled'] is None
    assert body['phase'] == 'idle' and body['current_version'] == _app_module.APP_VERSION

