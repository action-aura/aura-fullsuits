"""
Aura Retail -- first-run onboarding regression suite (Wave 0, AUDIT-001).

Covers: onboarding_status on a clean install, admin creation, rejection of a
second onboarding attempt once a real admin exists, weak-password rejection,
rejection of an email collision with an unrelated account (the previous code
silently deleted that account instead), login using the created admin, and
that the DB (not config.json) remains the source of truth after a
process-restart-equivalent (a fresh app boot against the same on-disk data).

See docs/corrections/wave0/retail-onboarding-correction.md.

Run:
    pytest products/retail/tests/retail_onboarding_wave0_test.py -v
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_onboarding_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def test_clean_install_reports_needs_setup():
    client = app.test_client()
    r = client.get('/api/onboarding/status')
    assert r.status_code == 200
    assert r.get_json()['needs_setup'] is True


def test_create_admin_succeeds_on_clean_install():
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner@wave0.test', 'password': 'FirstRunPW1',
        'company_name': 'Wave0 Co',
    })
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    assert data['success'] is True
    assert data['user']['role'] == 'admin'

    status = client.get('/api/onboarding/status').get_json()
    assert status['needs_setup'] is False


def test_login_works_with_the_created_admin():
    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': 'owner@wave0.test', 'password': 'FirstRunPW1'})
    assert r.status_code == 200, r.get_json()


def test_duplicate_onboarding_rejected_once_admin_exists():
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Second Owner', 'email': 'second@wave0.test', 'password': 'AnotherPW1',
    })
    assert r.status_code == 409
    assert 'already exists' in r.get_json()['error'].lower()


def test_weak_password_rejected():
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Weak', 'email': 'weak@wave0.test', 'password': '123',
    })
    assert r.status_code == 400

    conn = registry_conn()
    row = conn.execute("SELECT id FROM users WHERE email=?", ('weak@wave0.test',)).fetchone()
    conn.close()
    assert row is None  # rejected request must not create a row


def test_duplicate_email_of_unrelated_account_is_rejected_not_deleted():
    """Regression for the pre-Wave-0 bug: create-admin used to unconditionally
    DELETE any existing user row with the target email before inserting the
    new admin. Once a real admin already exists, a second onboarding attempt
    reusing ANY registered email (including an unrelated employee account)
    must be rejected, and that account must survive untouched."""
    client = app.test_client()
    conn = registry_conn()
    import uuid
    from commercial_runtime.security.passwords import hash_password
    employee_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (employee_id, 'wave0-co', 'EMP-9001', 'staffer@wave0.test', hash_password('StaffPW1'), 'employee', 'active'),
    )
    conn.commit()
    conn.close()

    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Hijacker', 'email': 'staffer@wave0.test', 'password': 'HijackPW1',
    })
    assert r.status_code in (400, 409)

    conn = registry_conn()
    survivor = conn.execute("SELECT id FROM users WHERE id=?", (employee_id,)).fetchone()
    conn.close()
    assert survivor is not None, "unrelated account must not be deleted by a failed onboarding attempt"


def test_restart_equivalent_still_reports_setup_complete_and_admin_can_login():
    """Booting a fresh Flask app instance against the same on-disk AURA_APP_DATA
    (the closest a same-process test can get to a real process restart)
    must still see needs_setup == False and allow the original admin to log
    in -- the database, not any in-memory state, is the source of truth."""
    fresh_app = _app_module.init_app()
    fresh_app.config['TESTING'] = True
    client = fresh_app.test_client()

    status = client.get('/api/onboarding/status').get_json()
    assert status['needs_setup'] is False

    r = client.post('/api/auth/login', json={'email': 'owner@wave0.test', 'password': 'FirstRunPW1'})
    assert r.status_code == 200, r.get_json()


def test_direct_create_admin_abuse_after_setup_still_rejected():
    """A caller who skips the UI and calls the onboarding API directly after
    setup is already complete must be rejected the same way the UI path is
    (AUDIT-001's original symptom was a 404 route; this asserts the *fixed*
    route now enforces the real business rule instead of just existing)."""
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Abuser', 'email': 'abuser@wave0.test', 'password': 'AbusePW123',
    })
    assert r.status_code == 409
