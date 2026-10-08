"""
Aura Retail -- crash reports on an install with NO Owner (the default).

Proves, against the real app: capture is on and purely local, nothing is built
that could send, the browser endpoint is guarded, and the front-end hook is
actually in app-shell.js. The mirror image (an Owner IS configured) is
retail_crash_reports_owner_wiring_test.py.

Run:
    pytest products/retail/tests/retail_crash_reports_test.py -v
"""
import json
import os
import re
import shutil
import sqlite3
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_crash_reports_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
for _name in ("AURA_DEV", "AURA_SYNC_RELAY_URL", "AURA_OWNER_LICENSING_URL", "AURA_RETAIL_DEMO_MODE"):
    os.environ.pop(_name, None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.app

# A probe route, added BEFORE init_app()/the first request (Flask refuses new
# routes afterwards). It raises with exactly the kinds of text a real bug puts
# in an exception message, so the scrub is proved against the real app.
SECRET = "boom for 'Ahmad Al-Khalidi' total 12345.678 ahmad@shop.example"


@app.route('/__crash_probe/<int:n>')
def _crash_probe(n):
    raise ValueError(SECRET)


app = _app_module.init_app()
app.config["TESTING"] = False
app.config["PROPAGATE_EXCEPTIONS"] = False     # production behaviour: a 500 page, not a re-raise

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402

STORE_PATH = DATA / "database" / "subsystems" / "crash_reports.db"


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _rows():
    conn = sqlite3.connect(str(STORE_PATH))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM crash_outbox ORDER BY id")]
    finally:
        conn.close()


def _login(role):
    email = f"crash-{role}-{uuid.uuid4().hex[:8]}@test.local"
    conn = registry_conn()
    user_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, str(uuid.uuid4()), "EMP-1", email, hash_password("CrashReportsPW1"), role, "active"),
    )
    conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
                 (str(uuid.uuid4()), user_id, "retail", "full"))
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    conn.commit()
    conn.close()
    client = app.test_client()
    assert client.post('/api/auth/login', json={'email': email, 'password': 'CrashReportsPW1'}).status_code == 200
    return client


def test_capture_is_on_locally_and_nothing_can_send_without_an_owner():
    assert _app_module._crash_store is not None
    assert STORE_PATH.exists()
    assert _app_module._crash_sender is None, "no Owner URL: no sender may exist"
    # its own file under subsystems/, never retail.db
    assert STORE_PATH.name == "crash_reports.db" and STORE_PATH.parent.name == "subsystems"


def test_a_crashing_route_is_recorded_scrubbed_and_the_user_still_gets_a_plain_500():
    before = len(_rows())
    client = app.test_client()
    response = client.get('/__crash_probe/4242')
    assert response.status_code == 500
    assert b"Ahmad" not in response.get_data() and b"12345" not in response.get_data()
    rows = _rows()
    assert len(rows) == before + 1, "the crash must have been recorded exactly once"
    row = rows[-1]
    assert row["kind"] == "server" and row["exception_type"] == "ValueError"
    assert row["route"] == "/__crash_probe/<int:n>"
    assert row["app_version"] == _app_module.APP_VERSION
    assert row["message"] == "boom for '…' total <n>.678 <email>"
    assert "4242" not in json.dumps(row)


def test_client_endpoint_needs_a_session_and_records_a_scrubbed_report():
    anonymous = app.test_client()
    assert anonymous.post('/api/crash-reports/client', json={'message': 'x'}).status_code == 401
    before = len(_rows())
    cashier = _login('cashier')
    r = cashier.post('/api/crash-reports/client', json={
        'message': "oops 'Ahmad' 0791234567", 'screen': 'sales',
        'stack': "TypeError: x\n    at f (http://127.0.0.1:5000/static/subsystem-retail.js:9:9)"})
    assert r.status_code == 204
    rows = _rows()
    assert len(rows) == before + 1
    assert rows[-1]["kind"] == "client" and rows[-1]["route"] == "sales"
    assert "Ahmad" not in rows[-1]["message"] and "0791234567" not in rows[-1]["message"]


def test_summary_is_admin_only_and_says_sending_is_off():
    assert _login('cashier').get('/api/crash-reports/summary').status_code == 403
    body = _login('admin').get('/api/crash-reports/summary').get_json()
    assert body["enabled"] is False
    assert body["pending"] + body["sent"] == len(_rows())


def test_the_browser_hook_is_in_the_shell_and_calls_the_exact_endpoint():
    shell = (PRODUCT_DIR / 'frontend' / 'app-shell.js').read_text(encoding='utf-8')
    assert "'/api/crash-reports/client'" in shell
    assert "window.addEventListener('error'" in shell and "window.addEventListener('unhandledrejection'" in shell
    # the hook sends the section id, never the page address
    hook = shell[shell.index("Crash reports: the browser's half"):]
    assert "location.href" not in hook
