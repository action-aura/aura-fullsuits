"""Aura Retail -- bulk import must feed the multi-device sync outbox.

Regression suite for the launch-readiness audit's CRITICAL finding: the
import handlers in `api/import_api.py` (`_handle_retail_products` /
`_handle_retail_customers` / `_handle_retail_suppliers` /
`_handle_retail_categories`) inserted rows WITHOUT queueing the
`sync_outbox` events the equivalent single-record routes in
`api/retail_api.py` queue via `_queue_sync_event`. Result: an operator
imports a 500-item catalogue on one device and no other device ever sees
any of it -- no error anywhere, the data simply never leaves the machine.

These tests assert parity at the outbox level: an imported record must
produce the SAME sync event (entity_type, event_type, payload key set,
no `company_id` on the wire) that creating that record through the normal
API route produces. Branches are deliberately NOT covered: `branch` is not
a sync entity type at all (see sync_service.py's `_apply_event` scope), so
the branches import handler correctly queues nothing.

This file follows the same self-contained bootstrap convention as
`retail_customer_sync_test.py`/`retail_import_export_test.py` (no shared
conftest.py exists for products/retail/tests/): its own temp app-data dir,
its own license seed, its own Flask app boot.

Run:
    pytest products/retail/tests/retail_import_sync_test.py -v
"""
import csv
import io
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_importsync_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

# Seeded BEFORE the app is built -- the comparison creates below go through
# capability-guarded routes that would 403 on an inactive license.
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    """A fresh, logged-in admin client for its own newly-created company --
    one company per test, so payload lookups (by unique SKU/name) never see
    another test's rows."""
    email = f'importsync-{uuid.uuid4().hex[:8]}@test.local'
    password = 'ImportSyncPW1'
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


def _csv_bytes(rows, headers):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=headers)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode('utf-8')


def _import(client, entity, mapping, rows, headers):
    r = client.post("/api/import/execute", data={
        'system': 'retail', 'entity': entity,
        'mapping': json.dumps(mapping),
        'file': (io.BytesIO(_csv_bytes(rows, headers)), f'{entity}.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['success'] is True
    return r.get_json()


def _outbox_events(entity_type, event_type=None):
    """All sync_outbox rows of one entity type, payloads decoded, in
    insertion (rowid) order -- the same order push replays them in."""
    conn = get_retail_conn()
    sql = "SELECT entity_type, entity_id, event_type, payload FROM sync_outbox WHERE entity_type=?"
    params = [entity_type]
    if event_type:
        sql += " AND event_type=?"
        params.append(event_type)
    rows = conn.execute(sql + " ORDER BY rowid", params).fetchall()
    conn.close()
    return [dict(r) | {"payload": json.loads(r["payload"])} for r in rows]


def _event_for(entity_type, event_type, **payload_match):
    """The single outbox event of that type whose payload carries all the
    given key/value pairs (used to pick THIS test's event out of the shared
    outbox by its unique SKU/name)."""
    matches = [
        e for e in _outbox_events(entity_type, event_type)
        if all(e["payload"].get(k) == v for k, v in payload_match.items())
    ]
    assert len(matches) == 1, f"expected exactly one {entity_type}/{event_type} event matching {payload_match}, got {len(matches)}"
    return matches[0]


# ═════════════════════════════════════════════════════════════════════════════
# 1. Imported records produce the SAME sync events a normal create does
# ═════════════════════════════════════════════════════════════════════════════

def test_imported_product_queues_the_same_create_event_as_the_normal_route(client):
    # Reference: the normal single-record create path's outbox event.
    api_sku = f"API-{uuid.uuid4().hex[:8]}"
    r = client.post('/api/sub/retail/products', json={
        'name': 'API Product', 'sku': api_sku, 'sell_price': 10, 'cost_price': 5,
    })
    assert r.status_code == 200, r.get_json()
    api_event = _event_for('product', 'create', sku=api_sku)

    # The imported product must queue an event of the same shape.
    imp_sku = f"IMP-{uuid.uuid4().hex[:8]}"
    result = _import(client, 'products',
                     {"name": "Product Name", "sku": "SKU", "sell_price": "Selling Price"},
                     [{'Product Name': 'Imported Product', 'SKU': imp_sku, 'Selling Price': '19.99'}],
                     ['Product Name', 'SKU', 'Selling Price'])
    assert result['imported'] == 1

    imp_event = _event_for('product', 'create', sku=imp_sku)
    # Same payload key set as the normal create path -- a receiving device's
    # _apply_event must not be able to tell the two apart structurally.
    assert set(imp_event['payload'].keys()) == set(api_event['payload'].keys())
    assert 'company_id' not in imp_event['payload']  # never on the wire

    # entity_id is the real products.id the import wrote.
    conn = get_retail_conn()
    row = conn.execute("SELECT id FROM products WHERE sku=?", (imp_sku,)).fetchone()
    conn.close()
    assert imp_event['entity_id'] == row['id'] == imp_event['payload']['id']


def test_imported_customer_queues_the_same_create_event_as_the_normal_route(client):
    api_name = f"API Customer {uuid.uuid4().hex[:8]}"
    r = client.post('/api/sub/retail/customers', json={'name': api_name, 'phone': '+1-555-0001'})
    assert r.status_code == 200, r.get_json()
    api_event = _event_for('customer', 'create', name=api_name)

    imp_name = f"Imported Customer {uuid.uuid4().hex[:8]}"
    result = _import(client, 'customers',
                     {"name": "Customer Name", "phone": "Phone", "loyalty_points": "Points"},
                     [{'Customer Name': imp_name, 'Phone': '+1-555-0002', 'Points': '120'}],
                     ['Customer Name', 'Phone', 'Points'])
    assert result['imported'] == 1

    imp_event = _event_for('customer', 'create', name=imp_name)
    # Identical key set -- and loyalty_points/total_spent stay OFF the wire,
    # exactly like the normal create path (they are local-only columns the
    # apply side never writes).
    assert set(imp_event['payload'].keys()) == set(api_event['payload'].keys())
    assert 'company_id' not in imp_event['payload']


def test_imported_supplier_queues_the_same_create_event_as_the_normal_route(client):
    api_name = f"API Supplier {uuid.uuid4().hex[:8]}"
    r = client.post('/api/sub/retail/suppliers', json={'name': api_name})
    assert r.status_code == 200, r.get_json()
    api_event = _event_for('supplier', 'create', name=api_name)

    imp_name = f"Imported Supplier {uuid.uuid4().hex[:8]}"
    result = _import(client, 'suppliers',
                     {"name": "Company Name", "phone": "Phone Number"},
                     [{'Company Name': imp_name, 'Phone Number': '+1-555-9999'}],
                     ['Company Name', 'Phone Number'])
    assert result['imported'] == 1

    imp_event = _event_for('supplier', 'create', name=imp_name)
    assert set(imp_event['payload'].keys()) == set(api_event['payload'].keys())
    assert 'company_id' not in imp_event['payload']


def test_imported_category_queues_the_same_create_event_as_the_normal_route(client):
    api_name = f"API Category {uuid.uuid4().hex[:8]}"
    r = client.post('/api/sub/retail/categories', json={'name': api_name})
    assert r.status_code == 200, r.get_json()
    api_event = _event_for('category', 'create', name=api_name)

    imp_name = f"Imported Category {uuid.uuid4().hex[:8]}"
    result = _import(client, 'categories',
                     {"name": "Category Name", "description": "Description"},
                     [{'Category Name': imp_name, 'Description': 'From CSV'}],
                     ['Category Name', 'Description'])
    assert result['imported'] == 1

    imp_event = _event_for('category', 'create', name=imp_name)
    assert set(imp_event['payload'].keys()) == set(api_event['payload'].keys())
    assert 'company_id' not in imp_event['payload']


# ═════════════════════════════════════════════════════════════════════════════
# 2. Parent-before-child and update paths inside one import
# ═════════════════════════════════════════════════════════════════════════════

def test_product_import_creating_a_new_category_queues_category_before_product(client):
    """A product row naming a category that doesn't exist yet makes the
    import create the category on the fly -- the category event must land in
    the outbox BEFORE the product event that references it, so the receiving
    device applies parent before child."""
    cat_name = f"OnTheFly {uuid.uuid4().hex[:8]}"
    sku = f"CAT-{uuid.uuid4().hex[:8]}"
    _import(client, 'products',
            {"name": "Product Name", "sku": "SKU", "sell_price": "Selling Price", "category": "Category"},
            [{'Product Name': 'Categorized', 'SKU': sku, 'Selling Price': '9.99', 'Category': cat_name}],
            ['Product Name', 'SKU', 'Selling Price', 'Category'])

    cat_event = _event_for('category', 'create', name=cat_name)
    prod_event = _event_for('product', 'create', sku=sku)
    assert prod_event['payload']['category_id'] == cat_event['payload']['id']

    # Insertion order in the outbox = replay order on push (rowid).
    conn = get_retail_conn()
    order = [r['entity_type'] for r in conn.execute(
        "SELECT entity_type FROM sync_outbox WHERE entity_id IN (?,?) ORDER BY rowid",
        (cat_event['entity_id'], prod_event['entity_id'])).fetchall()]
    conn.close()
    assert order == ['category', 'product']


def test_reimporting_an_existing_sku_queues_an_update_event(client):
    """The import's existing-SKU branch UPDATEs the product -- that write
    must queue the same 'update' event the PATCH route queues (full current
    row, status included), or an imported price change never reaches other
    devices."""
    sku = f"UPD-{uuid.uuid4().hex[:8]}"
    mapping = {"name": "Product Name", "sku": "SKU", "sell_price": "Selling Price"}
    headers = ['Product Name', 'SKU', 'Selling Price']
    _import(client, 'products', mapping, [{'Product Name': 'Original', 'SKU': sku, 'Selling Price': '10'}], headers)
    result = _import(client, 'products', mapping, [{'Product Name': 'Renamed', 'SKU': sku, 'Selling Price': '20'}], headers)
    assert result['updated'] == 1

    upd_event = _event_for('product', 'update', sku=sku)
    assert upd_event['payload']['name'] == 'Renamed'
    assert upd_event['payload']['sell_price'] == 20.0
    # Full-row payload like update_product's, so a soft-delete/restore state
    # is always carried too.
    assert 'status' in upd_event['payload']
