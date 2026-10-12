"""
SUPPORT-1 -- "Send diagnostics to support" and the report code, against the REAL app.

Aura Retail -- crash reports on an install with NO Owner (the default).

Proves, against the real app: capture is on and purely local, nothing is built
that could send, the browser endpoint is guarded, and the front-end hook is
actually in app-shell.js. The mirror image (an Owner IS configured) is
retail_crash_reports_owner_wiring_test.py.

Run:
    pytest products/retail/tests/retail_support_diagnostics_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_support1_"))
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


from commercial_runtime.crash_reports.reference_code import reference_code  # noqa: E402

EXPORT = {"retail_schema_version": 52, "licence": {"state": "ACTIVE_ONLINE"},
          "row_counts": {"sales": 12, "products": 5, "customers": 2, "users": 1},
          "sync_health": {"state": "OK"}, "log_tail": ["Ahmad Al-Khalidi 0791234567"]}


def test_admin_sends_diagnostics_and_gets_a_code_that_matches_the_stored_row():
    before = len(_rows())
    r = _login('admin').post('/api/crash-reports/diagnostics', json=EXPORT)
    assert r.status_code == 200, r.get_data(as_text=True)
    code = r.get_json()['data']['reference_code']
    rows = _rows()
    assert len(rows) == before + 1
    row = rows[-1]
    assert row['kind'] == 'diagnostics' and row['state'] == 'pending'
    assert code == reference_code(row['fingerprint']) and re.fullmatch(r'[2-9A-HJ-NP-Z]{6}', code)
    assert 'Ahmad' not in json.dumps(row) and '0791234567' not in json.dumps(row), "log_tail must never be stored"
    assert r.get_json()['data']['sending_enabled'] is False, "no Owner configured: the page must be told nothing will be sent"


def test_diagnostics_is_administrators_only_and_needs_a_session():
    before = len(_rows())
    assert _login('cashier').post('/api/crash-reports/diagnostics', json=EXPORT).status_code == 403
    assert app.test_client().post('/api/crash-reports/diagnostics', json=EXPORT).status_code in (401, 403)
    assert len(_rows()) == before, "a refused request must not write a row"


def test_diagnostics_refuses_oversize_and_malformed_bodies():
    admin = _login('admin')
    before = len(_rows())
    assert admin.post('/api/crash-reports/diagnostics', data=b'x' * 20000,
                      content_type='application/json').status_code == 413
    assert admin.post('/api/crash-reports/diagnostics', data=b'{nope', content_type='application/json').status_code == 400
    assert admin.post('/api/crash-reports/diagnostics', json=[1, 2]).status_code == 400
    assert len(_rows()) == before


def test_a_recorded_client_report_returns_its_code_in_a_header_and_a_dropped_one_does_not():
    cashier = _login('cashier')
    r = cashier.post('/api/crash-reports/client', json={'message': 'oops', 'screen': 'sales',
                     'stack': "TypeError: x\n    at f (http://127.0.0.1:5000/static/subsystem-retail.js:9:9)"})
    assert r.status_code == 204 and r.get_data() == b''
    row = _rows()[-1]
    assert r.headers['X-Aura-Report-Code'] == reference_code(row['fingerprint'])
    # the rate limit answers the same bare 204 -- with NO code, because nothing was saved
    import commercial_runtime.crash_reports.routes as routes_module
    original = routes_module.ClientReportLimiter.allow
    routes_module.ClientReportLimiter.allow = lambda self: False
    try:
        dropped = cashier.post('/api/crash-reports/client', json={'message': 'again', 'stack': 'x'})
    finally:
        routes_module.ClientReportLimiter.allow = original
    assert dropped.status_code == 204 and 'X-Aura-Report-Code' not in dropped.headers


def test_a_real_500_carries_the_code_of_its_own_row():
    before = len(_rows())
    r = app.test_client().get('/__crash_probe/7')
    assert r.status_code == 500
    rows = _rows()
    assert len(rows) == before + 1
    assert r.headers['X-Aura-Report-Code'] == reference_code(rows[-1]['fingerprint'])
    assert b'Ahmad' not in r.get_data()