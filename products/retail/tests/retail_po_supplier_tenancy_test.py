"""Aura Retail -- cross-tenant leak fix for purchase_orders.supplier_id.

Two independent halves of the SAME leak, in api/retail_api.py:

  1. WRITE half (create_purchase_order): `supplier_id` was accepted from the
     request with NO validation at all -- not existence, not company
     ownership, not tombstone. A caller in company A could pass company B's
     supplier_id straight through and the row landed cleanly (no FK
     enforces a company_id match across tables in this schema). Already
     tracked as an open gap in ROADMAP.md ("create_purchase_order still has
     no cross-tenant validation on supplier_id").

  2. READ half (list_purchase_orders / get_purchase_order): both routes
     joined `suppliers` with NO company condition on the join at all --
     `FROM purchase_orders po LEFT JOIN suppliers s ON po.supplier_id=s.id`.
     purchase_orders rows are company-scoped in the WHERE; the joined
     supplier was not. Any row carrying a foreign supplier_id -- whether
     freshly blocked by fix (1) above, or already sitting in a database from
     before this fix shipped -- would join straight across the tenant
     boundary and hand back the OTHER company's supplier NAME.

Neither half alone is sufficient: validation-only does nothing for rows
already written before the fix; join-scoping-only still lets a bad row be
written in the first place (and would silently blank its supplier_name on
read rather than refuse the write that created it).

`supplier_id` is, and must stay, OPTIONAL -- a purchase order with no
supplier at all is a legitimate document (several branches in
create_purchase_order already read `if supplier_id:`). A join fix that
moves the company condition into the WHERE clause instead of the JOIN
condition would silently drop every supplier-less PO from the list/get
routes -- the exact mistake launch-readiness stage 6b-ii's `list_products`
categories-join comment (api/retail_api.py) already documents and avoids
for the unrelated products/categories case.

This file follows the same self-contained bootstrap convention as
retail_po_number_uniqueness_test.py / retail_po_split_route_test.py (no
shared conftest.py exists for products/retail/tests/): its own temp
app-data dir, its own license seed, its own Flask app boot, its own
fixtures local to this file.

Run:
    pytest products/retail/tests/retail_po_supplier_tenancy_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_po_supplier_tenancy_"))
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
    email = f'{prefix}-{uuid.uuid4().hex[:8]}@test.local'
    password = 'PoSupplierTenancyPW1'
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
    """Two independently-created companies on this ONE shared install/test
    database -- company A must never be able to see anything belonging to
    company B through either half of this leak."""
    return _make_admin_client('potenancy-a'), _make_admin_client('potenancy-b')


@pytest.fixture
def client_and_company():
    return _make_admin_client('potenancy-solo')


def _seed_supplier(c, name='Tenancy Test Supplier', **kw):
    payload = {'name': name}
    payload.update(kw)
    r = c.post('/api/sub/retail/suppliers', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_product(c, **kw):
    payload = {
        'name': 'PO Supplier Tenancy Widget', 'sku': f'POSUP-{uuid.uuid4().hex[:8]}',
        'cost_price': 5.0, 'sell_price': 10.0,
    }
    payload.update(kw)
    r = c.post('/api/sub/retail/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_po(c, product_id, **kw):
    payload = {'items': [{'product_id': product_id, 'quantity': 1, 'unit_cost': 5.0}]}
    payload.update(kw)
    return c.post('/api/sub/retail/purchase-orders', json=payload)


def _po_count(cid):
    conn = get_retail_conn()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM purchase_orders WHERE company_id=?", (cid,)).fetchone()
        return row['n']
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════════════
# 1. WRITE half -- create_purchase_order must refuse a foreign supplier_id
# ═════════════════════════════════════════════════════════════════════════

def test_a_po_cannot_be_created_with_another_companys_supplier(two_companies):
    """The write half of the leak. Before the fix, `supplier_id` carried NO
    validation at all -- company A could pass company B's supplier_id
    straight through and the INSERT landed cleanly. Asserts BOTH that the
    route refuses the request AND that no purchase_orders row was actually
    written -- a bare 4xx assertion would still pass if the route returned
    an error status while the row landed anyway (a real, different bug this
    test must not let slip through)."""
    (client_a, cid_a), (client_b, cid_b) = two_companies
    supplier_b = _seed_supplier(client_b, name='Company B Supplier')
    product_a = _seed_product(client_a)

    before = _po_count(cid_a)
    r = client_a.post('/api/sub/retail/purchase-orders', json={
        'items': [{'product_id': product_a, 'quantity': 1, 'unit_cost': 5.0}],
        'supplier_id': supplier_b,
    })
    assert r.status_code == 400, r.get_json()
    assert supplier_b in r.get_json()['message']
    after = _po_count(cid_a)
    assert after == before, "a purchase_orders row was written despite the refusal"


def test_a_po_cannot_be_created_with_a_deleted_supplier(client_and_company):
    """Tombstone consistency with the product existence check already on
    this route (and with create_supplier_contact's identical rule): a
    supplier that has been soft-deleted must be refused exactly like one
    that never existed, not silently accepted onto a brand-new PO."""
    c, cid = client_and_company
    supplier_id = _seed_supplier(c, name='Soon Deleted Supplier')
    product_id = _seed_product(c)
    del_r = c.delete(f'/api/sub/retail/suppliers/{supplier_id}')
    assert del_r.status_code == 200, del_r.get_json()

    before = _po_count(cid)
    r = _create_po(c, product_id, supplier_id=supplier_id)
    assert r.status_code == 400, r.get_json()
    assert supplier_id in r.get_json()['message']
    after = _po_count(cid)
    assert after == before, "a purchase_orders row was written for a deleted supplier"


# ═════════════════════════════════════════════════════════════════════════
# 2. READ half -- list/get must not leak a foreign supplier's name
# ═════════════════════════════════════════════════════════════════════════

def test_a_po_row_carrying_a_foreign_supplier_id_does_not_leak_the_name_on_read(two_companies):
    """The half that protects data already written before this fix shipped.
    Inserts a purchase_orders row DIRECTLY (bypassing the now-fixed write
    guard) carrying company B's supplier_id under company A's company_id --
    exactly the shape a pre-fix database is left in. Reads it back through
    BOTH list_purchase_orders and get_purchase_order as company A and
    asserts company B's supplier name is not present in either response."""
    (client_a, cid_a), (client_b, cid_b) = two_companies
    supplier_b = _seed_supplier(client_b, name='Leaked Supplier Name')

    conn = get_retail_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO purchase_orders (company_id,po_number,supplier_id,status,subtotal,total) "
            "VALUES (?,?,?,?,?,?)",
            (cid_a, f'PO-LEGACY-{uuid.uuid4().hex[:8]}', supplier_b, 'pending', 0, 0),
        )
        po_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    list_r = client_a.get('/api/sub/retail/purchase-orders')
    assert list_r.status_code == 200, list_r.get_json()
    rows = list_r.get_json()['data']
    row = next(r for r in rows if r['id'] == po_id)
    assert row['supplier_name'] != 'Leaked Supplier Name'

    get_r = client_a.get(f'/api/sub/retail/purchase-orders/{po_id}')
    assert get_r.status_code == 200, get_r.get_json()
    assert get_r.get_json()['data']['po']['supplier_name'] != 'Leaked Supplier Name'


# ═════════════════════════════════════════════════════════════════════════
# 3. ALLOW half -- a supplier-less PO must survive the join fix
# ═════════════════════════════════════════════════════════════════════════

def test_a_po_with_no_supplier_is_still_created_and_still_listed(client_and_company):
    """supplier_id is optional. A join fix that puts the company condition
    in the WHERE clause instead of the JOIN condition would silently drop
    every supplier-less PO from list_purchase_orders entirely (it is a LEFT
    JOIN) -- this is the test that catches that mistake."""
    c, _cid = client_and_company
    product_id = _seed_product(c)

    r = _create_po(c, product_id)
    assert r.status_code == 200, r.get_json()
    po_id = r.get_json()['data']['id']

    list_r = c.get('/api/sub/retail/purchase-orders')
    assert list_r.status_code == 200, list_r.get_json()
    rows = list_r.get_json()['data']
    row = next((row for row in rows if row['id'] == po_id), None)
    assert row is not None, "a supplier-less PO must still appear in the list"
    assert row['supplier_name'] is None

    get_r = c.get(f'/api/sub/retail/purchase-orders/{po_id}')
    assert get_r.status_code == 200, get_r.get_json()
    assert get_r.get_json()['data']['po']['supplier_name'] is None


# ═════════════════════════════════════════════════════════════════════════
# 4. ALLOW half -- a legitimate own-company supplier still shows its name
# ═════════════════════════════════════════════════════════════════════════

def test_a_po_with_a_legitimate_supplier_still_shows_its_name(client_and_company):
    """The other allow half. A fix that blanks EVERY supplier name (e.g. by
    breaking the join condition entirely instead of scoping it) would pass
    tests 1-3 above while making the feature useless -- this is the test
    that catches that."""
    c, _cid = client_and_company
    supplier_id = _seed_supplier(c, name='Real Own-Company Supplier')
    product_id = _seed_product(c)

    r = _create_po(c, product_id, supplier_id=supplier_id)
    assert r.status_code == 200, r.get_json()
    po_id = r.get_json()['data']['id']

    list_r = c.get('/api/sub/retail/purchase-orders')
    assert list_r.status_code == 200, list_r.get_json()
    row = next(row for row in list_r.get_json()['data'] if row['id'] == po_id)
    assert row['supplier_name'] == 'Real Own-Company Supplier'

    get_r = c.get(f'/api/sub/retail/purchase-orders/{po_id}')
    assert get_r.status_code == 200, get_r.get_json()
    assert get_r.get_json()['data']['po']['supplier_name'] == 'Real Own-Company Supplier'
