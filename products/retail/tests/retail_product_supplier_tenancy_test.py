"""Aura Retail -- a product may not be filed against another company's supplier.

`create_product` and `update_product` both wrote `data.get('supplier_id')`
straight into `products` with no check of any kind -- not existence, not
`company_id`, not tombstone. The identical unvalidated-foreign-key shape
`create_purchase_order` carried until `6b79b5a`, on a different table.

SEVERITY, stated honestly, because it is lower than "cross-tenant" usually
implies and the next reader should not have to re-derive it:

Nothing in this codebase currently reads supplier DETAILS through a product.
Every read of `suppliers` was enumerated when this was found -- exactly two
joins, both in the purchase-order routes, both company-scoped since `6b79b5a`;
every other read is either company-scoped or a read-back by id immediately
after a company-scoped write. `list_products` returns `p.*`, carrying the raw
`supplier_id` and no supplier name. So a poisoned value is a DANGLING
CROSS-TENANT REFERENCE, not a live data leak.

It is fixed because it is ONE JOIN away from being one: the moment anyone puts
a supplier name on the products list -- an obvious feature -- that id starts
rendering another tenant's data, and whoever adds the join has no reason to
suspect the id is untrusted.

WHY THIS FILE DRIVES THE REAL ROUTES, which is the point worth keeping:

The first version of this file asserted the validating SQL existed in the
source and counted its occurrences. That test passed with the guard removed --
deleting the `if` that reaches the query leaves the query itself sitting there,
unreachable, and a string count cannot tell the difference. It asserted the
text was present, not that the check RAN, which is the first failure shape
ENGINEERING.md names. Rewritten to POST and PATCH for real, against two
genuinely separate companies on one install.

Run:
    pytest products/retail/tests/retail_product_supplier_tenancy_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_prod_supplier_tenancy_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True
app.config["PROPAGATE_EXCEPTIONS"] = False

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin_client(prefix):
    """One logged-in admin on its own company. Mirrors
    `retail_po_supplier_tenancy_test.py`'s helper -- the sibling file covering
    the purchase-order half of this same fix."""
    email = f'{prefix}-{uuid.uuid4().hex[:8]}@test.local'
    password = 'ProdSupplierTenancyPW1'
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
    return c, company_id


@pytest.fixture
def two_companies():
    return _make_admin_client('prodsup-a'), _make_admin_client('prodsup-b')


def _make_supplier(client, name):
    r = client.post('/api/sub/retail/suppliers', json={'name': name})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _product_count(company_id):
    conn = get_retail_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM products WHERE company_id=?", (company_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def test_a_product_cannot_be_created_with_another_companys_supplier(two_companies):
    """The bug, through the real route.

    Asserts the product row was NOT written, not merely that a 400 came back --
    a refusal that still inserted would satisfy a status-code assertion while
    leaving exactly the dangling reference this fix exists to prevent.
    """
    (client_a, company_a), (client_b, _company_b) = two_companies
    foreign_supplier = _make_supplier(client_b, 'Company B Supplier')

    before = _product_count(company_a)
    r = client_a.post('/api/sub/retail/products', json={
        'name': 'Smuggled', 'sku': f'SKU-{uuid.uuid4().hex[:8]}',
        'supplier_id': foreign_supplier,
    })
    assert r.status_code == 400, r.get_json()
    assert _product_count(company_a) == before, (
        "the product must not have been written")


def test_a_product_cannot_be_repointed_at_another_companys_supplier(two_companies):
    """The PATCH half. `supplier_id` is in `update_product`'s allowed list, so
    fixing only the create path would leave the identical hole one request
    later -- the "present on one path, absent on another" shape."""
    (client_a, _company_a), (client_b, _company_b) = two_companies
    own = _make_supplier(client_a, 'Own Supplier')
    foreign = _make_supplier(client_b, 'Company B Supplier')

    created = client_a.post('/api/sub/retail/products', json={
        'name': 'Legit', 'sku': f'SKU-{uuid.uuid4().hex[:8]}', 'supplier_id': own,
    })
    assert created.status_code == 200, created.get_json()
    pid = created.get_json()['data']['id']

    r = client_a.patch(f'/api/sub/retail/products/{pid}', json={'supplier_id': foreign})
    assert r.status_code == 400, r.get_json()

    conn = get_retail_conn()
    try:
        still = conn.execute("SELECT supplier_id FROM products WHERE id=?", (pid,)).fetchone()[0]
    finally:
        conn.close()
    assert still == own, "the product must still point at its own company's supplier"


def test_a_product_with_this_companys_supplier_is_created(two_companies):
    """The ALLOW half. Without it, a fix that refused EVERY supplier would pass
    both tests above while making it impossible to file a product against any
    supplier at all."""
    (client_a, _company_a), _b = two_companies
    own = _make_supplier(client_a, 'Own Supplier')
    r = client_a.post('/api/sub/retail/products', json={
        'name': 'Fine', 'sku': f'SKU-{uuid.uuid4().hex[:8]}', 'supplier_id': own,
    })
    assert r.status_code == 200, r.get_json()


def test_a_product_with_no_supplier_is_still_created(two_companies):
    """`supplier_id` is OPTIONAL and must stay so -- a product with no supplier
    is a real, exercised state, and only a supplied value is validated."""
    (client_a, _company_a), _b = two_companies
    r = client_a.post('/api/sub/retail/products', json={
        'name': 'Supplierless', 'sku': f'SKU-{uuid.uuid4().hex[:8]}',
    })
    assert r.status_code == 200, r.get_json()


def test_a_product_cannot_be_created_with_a_deleted_supplier(two_companies):
    """Tombstone consistency with the purchase-order check and with every
    catalogue read since Phase 6 stage 6b-iii-a. Deletes through the real
    route so the row is in whatever state the product actually produces."""
    (client_a, company_a), _b = two_companies
    doomed = _make_supplier(client_a, 'Doomed Supplier')
    assert client_a.delete(f'/api/sub/retail/suppliers/{doomed}').status_code == 200

    before = _product_count(company_a)
    r = client_a.post('/api/sub/retail/products', json={
        'name': 'Orphan', 'sku': f'SKU-{uuid.uuid4().hex[:8]}', 'supplier_id': doomed,
    })
    assert r.status_code == 400, r.get_json()
    assert _product_count(company_a) == before
