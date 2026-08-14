"""Aura Retail -- product barcode uniqueness (AUDIT, 2026-08-14).

`create_product` validated SKU uniqueness (409 on a duplicate) but never did
the same check for `barcode`, and `update_product` (PATCH) let `barcode` be
set to any value with no uniqueness check at all. Two products could end up
sharing one barcode -- a typo, or the same physical label scanned twice with
no client/server rejection, unlike SKU.

That matters specifically because of how a scanned code gets resolved back
to a product: both the desktop engine's `_findByCode` (subsystem-retail.js)
and the Android `findProductByCode` (ProductLookup.kt) do a first-match
lookup (`.find()` / `firstOrNull()`) over the scan-code candidates. Once a
duplicate barcode exists, scanning it always silently resolves to whichever
product sorts first -- the wrong item, at the wrong price, added to a sale,
with no indication to the cashier that the match was ambiguous.

This file follows the same self-contained bootstrap convention as every
other file in this suite (no shared conftest.py exists for
products/retail/tests/ -- confirmed by inspection): its own temp app-data
dir, its own license seed, its own Flask app boot, its own `client`/`db_conn`
fixtures local to this file.

Run:
    pytest products/retail/tests/retail_product_barcode_uniqueness_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_barcodeuniq_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

# Seeded BEFORE the app is built, matching every other route-level test file
# in this suite -- creating/updating a product is capability-guarded, so an
# inactive license would 403 every test here before reaching the code paths
# this file is actually about.
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    """A fresh, logged-in Flask test client for its own newly-created
    company -- one company per test, so barcode-uniqueness assertions never
    see another test's rows."""
    email = f'barcodeuniq-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'BarcodeUniqPW1'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return c


def test_create_product_rejects_a_duplicate_barcode(client):
    first = client.post('/api/sub/retail/products', json={
        'name': 'Widget A', 'sku': 'BC-A', 'barcode': '1234567890123', 'sell_price': 5,
    })
    assert first.status_code == 200, first.get_json()

    dupe = client.post('/api/sub/retail/products', json={
        'name': 'Widget B', 'sku': 'BC-B', 'barcode': '1234567890123', 'sell_price': 7,
    })
    assert dupe.status_code == 409
    assert 'barcode' in dupe.get_json()['message'].lower()


def test_create_product_allows_multiple_blank_barcodes(client):
    """Most products never get a barcode -- an empty value must not be
    treated as a duplicate of every other blank-barcode product."""
    a = client.post('/api/sub/retail/products', json={'name': 'No Barcode A', 'sku': 'BC-C', 'sell_price': 5})
    b = client.post('/api/sub/retail/products', json={'name': 'No Barcode B', 'sku': 'BC-D', 'sell_price': 5})
    assert a.status_code == 200, a.get_json()
    assert b.status_code == 200, b.get_json()


def test_update_product_rejects_setting_a_barcode_that_already_exists(client):
    owner = client.post('/api/sub/retail/products', json={
        'name': 'Widget C', 'sku': 'BC-E', 'barcode': '9990001112223', 'sell_price': 5,
    })
    assert owner.status_code == 200, owner.get_json()

    other = client.post('/api/sub/retail/products', json={'name': 'Widget D', 'sku': 'BC-F', 'sell_price': 5})
    other_id = other.get_json()['data']['id']

    patch = client.patch(f'/api/sub/retail/products/{other_id}', json={'barcode': '9990001112223'})
    assert patch.status_code == 409
    assert 'barcode' in patch.get_json()['message'].lower()


def test_update_product_allows_resaving_its_own_unchanged_barcode(client):
    """The self-exclusion in the uniqueness check must not false-positive
    when a product is PATCHed with the barcode it already has."""
    created = client.post('/api/sub/retail/products', json={
        'name': 'Widget E', 'sku': 'BC-G', 'barcode': '5551112223334', 'sell_price': 5,
    })
    pid = created.get_json()['data']['id']

    patch = client.patch(f'/api/sub/retail/products/{pid}', json={'barcode': '5551112223334', 'sell_price': 6})
    assert patch.status_code == 200, patch.get_json()
