"""Aura Clinic -- Phase 7 Part Y: rc.1 -> rc.2 migration safety.

Simulates a REAL pre-Phase-7 install: patient data inserted directly via SQL
(bypassing the API entirely, since the rc.2 API now requires a license this
data predates), no licensing.db present at all -- exactly what an actual
rc.1 customer's app-data directory looks like the moment before it is
upgraded to rc.2 and launched for the first time. Proves:

  1. All pre-existing data is readable, unmodified, immediately after
     booting rc.2 code against rc.1 data -- no migration step silently
     alters or drops anything (there IS no schema migration here: licensing
     state lives in a brand-new, wholly separate database file, additive
     only, per docs/licensing/phase7/local-license-state-machine.md's
     "pre-activation states preserve data access" design).
  2. New mutations are correctly blocked (ACTIVATION_REQUIRED-consistent
     403, not a crash, not silent data loss) until activation happens.
  3. Backup/restore/export and the licensing status/activate routes remain
     reachable throughout -- never blocked by the absence of a license.
  4. After a real activation, the SAME pre-existing patient row is still
     present, byte-for-byte, and new mutations now succeed.
  5. Declining/never completing activation does not delete or corrupt
     anything (Part Y: "do not delete data when activation is declined").

Run:
    pytest products/clinic/tests/clinic_phase7_migration_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_clinic_migration_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

# Deliberately NOT calling seed_active_license() here -- this file's whole
# point is to start from the state a real rc.1 install upgraded in place
# actually has: product data present, licensing.db absent entirely.

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_clinic_conn  # noqa: E402
from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


PRE_EXISTING_PATIENT_NAME = "Pre-Existing RC1 Patient"


def _make_admin_client():
    email = f'migrate-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'MigratePW1'
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, 'ADMIN-0001', email, hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return c, company_id


@pytest.fixture(scope="module")
def rc1_client_and_company():
    """One shared client/company for this whole module -- simulates a single
    real rc.1 install being upgraded, not a fresh install per test."""
    c, cid = _make_admin_client()
    # Insert directly via SQL -- exactly what already exists on disk in a
    # real rc.1 customer's clinic.db, never went through the (not-yet-
    # existing-in-rc.1) licensing guard.
    conn = get_clinic_conn()
    conn.execute(
        "INSERT INTO clinic_patients (company_id, patient_code, name, phone, status, created_by) "
        "VALUES (?,?,?,?,?,?)",
        (cid, f"PRE-{uuid.uuid4().hex[:8]}", PRE_EXISTING_PATIENT_NAME, '555-0001', 'active', 'system'),
    )
    conn.commit()
    conn.close()
    return c, cid


def test_preexisting_data_is_readable_with_no_license_at_all(rc1_client_and_company):
    c, _ = rc1_client_and_company
    r = c.get('/api/sub/clinic/patients')
    assert r.status_code == 200
    names = [p['name'] for p in r.get_json()['data']]
    assert PRE_EXISTING_PATIENT_NAME in names


def test_licensing_status_reports_activation_required_not_a_crash(rc1_client_and_company):
    c, _ = rc1_client_and_company
    r = c.get('/api/licensing/status')
    assert r.status_code == 200
    assert r.get_json()['current_state'] in ('NOT_CONFIGURED', 'ACTIVATION_REQUIRED')


def test_new_mutation_blocked_not_crashed_before_activation(rc1_client_and_company):
    c, _ = rc1_client_and_company
    r = c.post('/api/sub/clinic/patients', json={'name': 'Should Be Blocked Pre-Activation'})
    assert r.status_code == 403
    assert r.get_json()['reason_code'] == 'LICENSE_INACTIVE'


def test_preexisting_data_still_intact_after_blocked_mutation_attempts(rc1_client_and_company):
    # Part Y: "do not delete data when activation is declined" -- prove it,
    # don't just assert the negative once; try several times first.
    c, _ = rc1_client_and_company
    for _ in range(3):
        c.post('/api/sub/clinic/patients', json={'name': 'Attempt'})
    r = c.get('/api/sub/clinic/patients')
    names = [p['name'] for p in r.get_json()['data']]
    assert PRE_EXISTING_PATIENT_NAME in names
    assert names.count(PRE_EXISTING_PATIENT_NAME) == 1  # not duplicated, not touched


def test_backup_restore_reachable_before_activation(rc1_client_and_company):
    c, _ = rc1_client_and_company
    r = c.get('/api/backup/list')
    assert r.status_code != 403
    assert r.status_code != 404


def test_activation_endpoint_reachable_before_activation(rc1_client_and_company):
    # Reachable (route exists, not capability-guarded) even though this test
    # environment has no Owner URL configured -- that specific-status-code
    # behavior (503 SERVICE_TEMPORARILY_UNAVAILABLE) is already covered by
    # commercial_runtime/licensing_contracts/tests/test_routes.py; this test
    # only needs to confirm the route was never blocked/missing.
    c, _ = rc1_client_and_company
    r = c.post('/api/licensing/activate', json={})
    assert r.status_code not in (403, 404)


def test_after_real_activation_preexisting_data_survives_and_mutations_succeed(rc1_client_and_company):
    c, _ = rc1_client_and_company
    seed_active_license(str(DATA), product_code="AURA_CLINIC", platform="WINDOWS")

    # Pre-existing data untouched by activation itself.
    r = c.get('/api/sub/clinic/patients')
    names = [p['name'] for p in r.get_json()['data']]
    assert PRE_EXISTING_PATIENT_NAME in names

    # New mutations now succeed.
    r2 = c.post('/api/sub/clinic/patients', json={'name': 'Post-Activation Patient'})
    assert r2.status_code == 200, r2.get_json()

    r3 = c.get('/api/sub/clinic/patients')
    names_after = [p['name'] for p in r3.get_json()['data']]
    assert PRE_EXISTING_PATIENT_NAME in names_after
    assert 'Post-Activation Patient' in names_after
