"""Aura Retail -- Phase 7 Part Y: rc.1 -> rc.2 migration safety.

Mirrors products/clinic/tests/clinic_phase7_migration_test.py -- see that
file's module docstring for the full rationale. Simulates a real pre-Phase-7
install: product data inserted directly via SQL, no licensing.db present at
all, then boots rc.2 code against it.

Run:
    pytest products/retail/tests/retail_phase7_migration_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_migration_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

# Deliberately NOT seeding a license here -- see the Clinic mirror's
# docstring for why.

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


PRE_EXISTING_PRODUCT_NAME = "Pre-Existing RC1 Product"


def _make_admin_client():
    email = f'migrate-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'MigratePW1'
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
    return c, company_id


@pytest.fixture(scope="module")
def rc1_client_and_company():
    c, cid = _make_admin_client()
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO products (id, company_id, sku, name, sell_price, status) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), cid, f"PRE-{uuid.uuid4().hex[:8]}", PRE_EXISTING_PRODUCT_NAME, 99.0, 'active'),
    )
    conn.commit()
    conn.close()
    return c, cid


def test_preexisting_data_is_readable_with_no_license_at_all(rc1_client_and_company):
    c, _ = rc1_client_and_company
    r = c.get('/api/sub/retail/products')
    assert r.status_code == 200
    names = [p['name'] for p in r.get_json()['data']]
    assert PRE_EXISTING_PRODUCT_NAME in names


def test_licensing_status_reports_activation_required_not_a_crash(rc1_client_and_company):
    c, _ = rc1_client_and_company
    r = c.get('/api/licensing/status')
    assert r.status_code == 200
    assert r.get_json()['current_state'] in ('NOT_CONFIGURED', 'ACTIVATION_REQUIRED')


def test_new_mutation_blocked_not_crashed_before_activation(rc1_client_and_company):
    c, _ = rc1_client_and_company
    r = c.post('/api/sub/retail/products', json={'name': 'Blocked', 'sku': 'BLOCKED-1'})
    assert r.status_code == 403
    assert r.get_json()['reason_code'] == 'LICENSE_INACTIVE'


def test_preexisting_data_still_intact_after_blocked_mutation_attempts(rc1_client_and_company):
    c, _ = rc1_client_and_company
    for i in range(3):
        c.post('/api/sub/retail/products', json={'name': 'Attempt', 'sku': f'ATT-{i}'})
    r = c.get('/api/sub/retail/products')
    names = [p['name'] for p in r.get_json()['data']]
    assert PRE_EXISTING_PRODUCT_NAME in names
    assert names.count(PRE_EXISTING_PRODUCT_NAME) == 1


def test_backup_restore_reachable_before_activation(rc1_client_and_company):
    c, _ = rc1_client_and_company
    r = c.get('/api/backup/list')
    assert r.status_code != 403
    assert r.status_code != 404


def test_activation_endpoint_reachable_before_activation(rc1_client_and_company):
    c, _ = rc1_client_and_company
    r = c.post('/api/licensing/activate', json={})
    assert r.status_code not in (403, 404)


def test_after_real_activation_preexisting_data_survives_and_mutations_succeed(rc1_client_and_company):
    c, _ = rc1_client_and_company
    seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

    r = c.get('/api/sub/retail/products')
    names = [p['name'] for p in r.get_json()['data']]
    assert PRE_EXISTING_PRODUCT_NAME in names

    r2 = c.post('/api/sub/retail/products', json={'name': 'Post-Activation Product', 'sku': 'POST-1'})
    assert r2.status_code == 200, r2.get_json()

    r3 = c.get('/api/sub/retail/products')
    names_after = [p['name'] for p in r3.get_json()['data']]
    assert PRE_EXISTING_PRODUCT_NAME in names_after
    assert 'Post-Activation Product' in names_after
