"""Aura Retail -- Phase 7 Part T capability-guard verification.

Proves @require_license_capability actually blocks/allows the real routes in
retail_api.py according to docs/licensing/phase7/
retail-restriction-capability-matrix.md's documented decisions. Mirrors
products/clinic/tests/clinic_capability_guard_test.py's structure.

Run:
    pytest products/retail/tests/retail_capability_guard_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_capability_guard_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository  # noqa: E402
from commercial_runtime.licensing_contracts.events import LicensingEventRecorder  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _db_path():
    return DATA / "database" / "subsystems" / "licensing.db"


def _set_state(state: str):
    repo = LicenseStateRepository(_db_path())
    record = repo.load()
    record.current_state = state
    repo.save(record)


def _make_admin_client():
    email = f'cap-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'CapabilityPW1'
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


@pytest.fixture
def client_and_company():
    _set_state("ACTIVE_ONLINE")
    return _make_admin_client()


def _seed_product(c, price=100.0, stock=50):
    r = c.post('/api/sub/retail/products', json={
        'name': 'Guard Test Product', 'sku': f'SKU-{uuid.uuid4().hex[:8]}',
        'sell_price': price, 'tax_rate': 0, 'initial_stock': stock,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_customer(c):
    r = c.post('/api/sub/retail/customers', json={'name': 'Guard Test Customer'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def test_restricted_blocks_new_product_creation(client_and_company):
    c, _ = client_and_company
    _set_state("RESTRICTED")
    r = c.post('/api/sub/retail/products', json={'name': 'x', 'sku': 'X-1'})
    assert r.status_code == 403
    assert r.get_json()["reason_code"] == "LICENSE_INACTIVE"


def test_restricted_allows_reading_products(client_and_company):
    c, _ = client_and_company
    _seed_product(c)
    _set_state("RESTRICTED")
    r = c.get('/api/sub/retail/products')
    assert r.status_code == 200


def test_restricted_blocks_new_sale(client_and_company):
    c, _ = client_and_company
    pid = _seed_product(c)
    _set_state("RESTRICTED")
    r = c.post('/api/sub/retail/sales', json={'items': [{'product_id': pid, 'quantity': 1}]})
    assert r.status_code == 403


def test_restricted_allows_return_against_existing_sale(client_and_company):
    c, _ = client_and_company
    pid = _seed_product(c, price=100.0, stock=10)
    sale = c.post('/api/sub/retail/sales', json={'items': [{'product_id': pid, 'quantity': 2}]})
    assert sale.status_code == 200, sale.get_json()
    sale_id = sale.get_json()['data']['id']

    _set_state("RESTRICTED")
    r = c.post('/api/sub/retail/returns', json={'sale_id': sale_id, 'items': [{'product_id': pid, 'quantity': 1}]})
    assert r.status_code == 200, r.get_json()


def test_restricted_allows_customer_payment(client_and_company):
    c, _ = client_and_company
    cust_id = _seed_customer(c)
    _set_state("RESTRICTED")
    r = c.post(f'/api/sub/retail/customers/{cust_id}/payments', json={'amount': 10})
    assert r.status_code == 200, r.get_json()


def test_restricted_blocks_supplier_payment(client_and_company):
    c, cid = client_and_company
    _set_state("RESTRICTED")
    # Nonexistent supplier is fine -- the guard must fire before the route's
    # own 404 lookup logic even runs.
    r = c.post('/api/sub/retail/suppliers/999999/payments', json={'amount': 10})
    assert r.status_code == 403


def test_restricted_blocks_new_customer_creation(client_and_company):
    c, _ = client_and_company
    _set_state("RESTRICTED")
    r = c.post('/api/sub/retail/customers', json={'name': 'Should Be Blocked'})
    assert r.status_code == 403


def test_suspended_blocks_same_as_restricted(client_and_company):
    c, _ = client_and_company
    _set_state("SUSPENDED")
    r = c.post('/api/sub/retail/products', json={'name': 'x', 'sku': 'X-2'})
    assert r.status_code == 403


def test_capability_denials_are_recorded_as_events(client_and_company):
    c, _ = client_and_company
    _set_state("RESTRICTED")
    c.post('/api/sub/retail/products', json={'name': 'x', 'sku': 'X-3'})
    events = LicensingEventRecorder(_db_path()).recent()
    denied = [e for e in events if e.event_type == "CAPABILITY_DENIED"]
    assert any(e.details.get("capability_code") == "retail.product.create" for e in denied)


def test_backup_never_blocked_by_license_state(client_and_company):
    c, _ = client_and_company
    _set_state("REVOKED")
    r = c.get('/api/backup/list')
    assert r.status_code != 403


def test_licensing_status_route_never_blocked(client_and_company):
    # The licensing routes themselves must remain reachable regardless of
    # license state (Part P: "no hidden bypass" cuts both ways) -- otherwise
    # a REVOKED install could never see its own status or reactivate.
    c, _ = client_and_company
    _set_state("REVOKED")
    r = c.get('/api/licensing/status')
    assert r.status_code != 403
