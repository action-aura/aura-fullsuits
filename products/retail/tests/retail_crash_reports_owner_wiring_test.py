"""
Aura Retail -- crash reports when an Owner IS configured and the till is activated.

The mirror image of retail_crash_reports_test.py: with an Owner URL, app.py must
build the sender from the SAME licensing identity, start it at boot as a daemon
thread (first run minutes away -- nothing here waits for it), and report sending
as enabled. The send itself is driven by calling run_once() with a stand-in
client, so no network call is ever made.

Run:
    pytest products/retail/tests/retail_crash_reports_owner_wiring_test.py -v
"""
import json
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_crash_owner_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
                  AURA_OWNER_LICENSING_URL="https://owner.invalid/api/licensing/v1")
for _name in ("AURA_DEV", "AURA_SYNC_RELAY_URL", "AURA_RETAIL_DEMO_MODE"):
    os.environ.pop(_name, None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    if _app_module._crash_sender is not None:
        _app_module._crash_sender.stop()
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture(scope='module')
def admin():
    email = f"crash-wiring-{uuid.uuid4().hex[:8]}@test.local"
    conn = registry_conn()
    user_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, str(uuid.uuid4()), "EMP-1", email, hash_password("CrashWiringPW1"), "admin", "active"),
    )
    conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
                 (str(uuid.uuid4()), user_id, "retail", "full"))
    user_accounts.seed_capabilities_for_user(conn, user_id, "admin")
    conn.commit()
    conn.close()
    client = app.test_client()
    assert client.post('/api/auth/login', json={'email': email, 'password': 'CrashWiringPW1'}).status_code == 200
    return client


def test_the_sender_is_built_started_as_a_daemon_and_reported_as_enabled(admin):
    sender = _app_module._crash_sender
    assert sender is not None
    assert sender._thread is not None and sender._thread.daemon is True and sender._thread.is_alive()
    assert sender._first_delay == 300 and sender._base_interval == 1800    # nothing is sent at boot
    assert admin.get('/api/crash-reports/summary').get_json()["enabled"] is True


def test_the_sender_signs_as_the_installation_the_licence_already_knows():
    sender = _app_module._crash_sender
    store = _app_module._crash_store
    store.record({
        "fingerprint": "d" * 32, "kind": "thread", "exception_type": "KeyError", "message": "m", "frames": [],
        "route": None, "app_version": _app_module.APP_VERSION, "platform": "WINDOWS"})

    calls = []

    class StandIn:
        def send_crash_reports(self, **kwargs):
            calls.append(kwargs)
            return {"result": "ACCEPTED", "accepted": len(kwargs["reports"])}

    sender._client_factory = lambda: StandIn()
    sender._signer_factory = lambda: object()          # the real identity is proved by the licence state below
    assert sender.run_once() == "SENT"
    assert len(calls) == 1
    # read from the SAME licensing db the licence blueprint uses
    assert calls[0]["installation_id"] == "test-fixture-installation"
    assert calls[0]["platform"] == "WINDOWS" and calls[0]["app_version"] == _app_module.APP_VERSION
    assert store.summary()["pending"] == 0
