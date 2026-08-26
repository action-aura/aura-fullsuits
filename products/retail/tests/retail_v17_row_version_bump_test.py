"""Aura Retail -- launch-readiness Phase 6 ("catalogue correctness"), stage
6a-i, Task B/C: every catalogue write site actually bumps `row_version` and
stamps `updated_at_utc` in the SAME statement as the field change, the
emission payload carries the NEW value, and the two accumulator sites
(loyalty points/total spent, credit balance) do neither -- see
docs/launch-readiness/phase6-catalogue-correctness.md and
database/schema.py's RETAIL_SCHEMA_VERSION v17 comment.

THE RULE THIS WHOLE FILE HOLDS DOWN: bump `row_version` (and emit a sync
event) ONLY when a request actually changes a field sync_service.py's apply
side reads for that entity type. A write that touches only a device-local
column -- total_spent/loyalty_points on every sale, credit_balance on every
AR/AP movement, credit_mode/credit_limit on a customer PATCH, payment_terms
on a supplier PATCH -- must bump nothing and emit nothing, because bumping on
a device-local-only change would let a customer/supplier's version race
ahead of what the SYNCED fields actually describe, and once stage 6a-ii's
reject-stale gate is live, a later GENUINE edit made on a different device
would arrive with a LOWER version than that phantom bump and be silently
discarded as stale.

Follows the same self-contained HTTP-route bootstrap as
retail_customer_sync_test.py / retail_reorder_sync_test.py (no shared
conftest.py exists for products/retail/tests/): its own temp app-data dir,
its own license seed, its own Flask app boot, its own fixtures local to this
file. Kept deliberately separate from retail_v17_catalogue_migration_test.py
-- see that file's own module docstring for why mixing the two bootstrap
styles in one file is a real cross-test-contamination hazard, not a style
preference.

Run:
    pytest products/retail/tests/retail_v17_row_version_bump_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_v17bump_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

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
    """A fresh, logged-in ADMIN Flask test client for its own newly-created
    company -- one company per test, so row_version/sync_outbox assertions
    never see another test's rows. Admin role so session_has_capability()
    passes every gate this file needs (credit terms, reorder accept/decline)
    without provisioning a per-code permission matrix."""
    email = f'v17bump-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'V17BumpPW1'
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

    # `credit_mode`/`credit_limit`/`payment_terms`/`doc_sequences` are all
    # created LAZILY by _ensure_credit_schema() on first use -- primed here
    # via a harmless GET, same technique retail_reorder_sync_test.py's
    # `client` fixture already uses for the identical reason.
    c.get('/api/sub/retail/settings/tax')

    # purchase_orders.po_number carries a bare (not company-scoped) UNIQUE
    # constraint -- accept_reorder_request drafts a PO, so this file needs
    # the same doc_sequences random-seed workaround retail_reorder_sync_
    # test.py's fixture uses to avoid colliding with another test's company
    # in this shared, multi-company test database.
    import random as _random
    seed_conn = get_retail_conn()
    seed_conn.execute(
        "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'po',?)",
        (company_id, _random.randint(1, 5_000_000)),
    )
    seed_conn.commit()
    seed_conn.close()

    return c


@pytest.fixture
def db_conn():
    conn = get_retail_conn()
    yield conn
    conn.close()


def _sync_events(db_conn, entity_type, entity_id):
    return db_conn.execute(
        "SELECT event_type, payload FROM sync_outbox WHERE entity_type=? AND entity_id=? ORDER BY created_at",
        (entity_type, str(entity_id)),
    ).fetchall()


def _row_version(db_conn, table, row_id):
    row = db_conn.execute(f"SELECT row_version, updated_at_utc FROM {table} WHERE id=?", (row_id,)).fetchone()
    assert row is not None, f'{table} row {row_id} not found'
    return row['row_version'], row['updated_at_utc']


def _make_product(client, *, reorder_level=5, reorder_method='none', initial_stock=10, sell_price=10.0):
    resp = client.post('/api/sub/retail/products', json={
        'name': f'V17 Bump Widget {uuid.uuid4().hex[:6]}',
        'sku': f'V17BUMP-{uuid.uuid4().hex[:8]}',
        'cost_price': 2, 'sell_price': sell_price,
        'reorder_level': reorder_level, 'reorder_method': reorder_method,
        'initial_stock': initial_stock,
    })
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()['data']['id']


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


def _sell(client, product_id, qty, customer_id=None, payment_method='cash', amount_paid=999999):
    body = {
        'items': [{'product_id': product_id, 'quantity': qty}],
        'amount_paid': amount_paid, 'payment_method': payment_method,
        'idempotency_key': str(uuid.uuid4()),
    }
    if customer_id:
        body['customer_id'] = customer_id
    r = client.post('/api/sub/retail/sales', json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


# ── Categories ────────────────────────────────────────────────────────────

def test_create_category_stamps_row_version_1_and_emits_it(client, db_conn):
    resp = client.post('/api/sub/retail/categories', json={'name': 'V17 Cat', 'description': 'd'})
    assert resp.status_code == 200
    cat_id = resp.get_json()['data']['id']

    version, updated_at = _row_version(db_conn, 'categories', cat_id)
    assert version == 1
    assert updated_at is not None

    events = _sync_events(db_conn, 'category', cat_id)
    assert len(events) == 1 and events[0]['event_type'] == 'create'
    payload = json.loads(events[0]['payload'])
    assert payload['row_version'] == 1
    assert payload['updated_at_utc'] == updated_at


def test_update_category_bumps_row_version_and_emits_the_new_value(client, db_conn):
    cat_id = client.post('/api/sub/retail/categories', json={'name': 'Before'}).get_json()['data']['id']
    v1, u1 = _row_version(db_conn, 'categories', cat_id)

    resp = client.put(f'/api/sub/retail/categories/{cat_id}', json={'name': 'After', 'description': 'x'})
    assert resp.status_code == 200

    v2, u2 = _row_version(db_conn, 'categories', cat_id)
    assert v2 == v1 + 1
    assert u2 != u1

    events = _sync_events(db_conn, 'category', cat_id)
    assert events[-1]['event_type'] == 'update'
    payload = json.loads(events[-1]['payload'])
    assert payload['row_version'] == v2  # the NEW value, not v1
    assert payload['updated_at_utc'] == u2


# ── Products ──────────────────────────────────────────────────────────────

def test_create_product_stamps_row_version_1_and_emits_it(client, db_conn):
    pid = _make_product(client)
    version, updated_at = _row_version(db_conn, 'products', pid)
    assert version == 1 and updated_at is not None
    payload = json.loads(_sync_events(db_conn, 'product', pid)[0]['payload'])
    assert payload['row_version'] == 1
    assert payload['updated_at_utc'] == updated_at


def test_update_product_bumps_row_version_and_emits_the_new_value(client, db_conn):
    pid = _make_product(client)
    v1, u1 = _row_version(db_conn, 'products', pid)

    resp = client.patch(f'/api/sub/retail/products/{pid}', json={'sell_price': 55.5})
    assert resp.status_code == 200

    v2, u2 = _row_version(db_conn, 'products', pid)
    assert v2 == v1 + 1 and u2 != u1
    payload = json.loads(_sync_events(db_conn, 'product', pid)[-1]['payload'])
    assert payload['row_version'] == v2
    assert payload['sell_price'] == 55.5


def test_delete_product_soft_delete_bumps_row_version_and_emits_the_new_value(client, db_conn):
    pid = _make_product(client)
    v1, u1 = _row_version(db_conn, 'products', pid)

    resp = client.delete(f'/api/sub/retail/products/{pid}')
    assert resp.status_code == 200

    v2, u2 = _row_version(db_conn, 'products', pid)
    assert v2 == v1 + 1 and u2 != u1
    events = _sync_events(db_conn, 'product', pid)
    assert events[-1]['event_type'] == 'delete'
    assert json.loads(events[-1]['payload'])['row_version'] == v2


# ── Customers ─────────────────────────────────────────────────────────────

def test_create_customer_stamps_row_version_1_and_emits_it(client, db_conn):
    resp = client.post('/api/sub/retail/customers', json={'name': 'V17 Cust'})
    cust_id = resp.get_json()['data']['id']
    version, updated_at = _row_version(db_conn, 'customers', cust_id)
    assert version == 1 and updated_at is not None
    payload = json.loads(_sync_events(db_conn, 'customer', cust_id)[0]['payload'])
    assert payload['row_version'] == 1


def test_update_customer_synced_field_bumps_and_emits(client, db_conn):
    cust_id = client.post('/api/sub/retail/customers', json={'name': 'Before'}).get_json()['data']['id']
    v1, u1 = _row_version(db_conn, 'customers', cust_id)

    resp = client.patch(f'/api/sub/retail/customers/{cust_id}', json={'phone': '+1-555-9999'})
    assert resp.status_code == 200

    v2, u2 = _row_version(db_conn, 'customers', cust_id)
    assert v2 == v1 + 1 and u2 != u1
    events = _sync_events(db_conn, 'customer', cust_id)
    assert events[-1]['event_type'] == 'update'
    payload = json.loads(events[-1]['payload'])
    assert payload['row_version'] == v2
    assert payload['phone'] == '+1-555-9999'


def test_update_customer_credit_only_patch_does_not_bump_or_emit(client, db_conn):
    """THE RULE, applied to update_customer: credit_mode/credit_limit are
    NOT in SYNCED_CUSTOMER_FIELDS (they have no apply-side column at all --
    sync_service.py's customer branch never reads them), so a PATCH that
    touches ONLY those two fields must bump nothing and emit nothing --
    exactly the shape of the loyalty-accumulator trap, reproduced on a
    second route."""
    cust_id = client.post('/api/sub/retail/customers', json={'name': 'Credit Only'}).get_json()['data']['id']
    v1, u1 = _row_version(db_conn, 'customers', cust_id)
    events_before = len(_sync_events(db_conn, 'customer', cust_id))

    resp = client.patch(f'/api/sub/retail/customers/{cust_id}',
                         json={'credit_mode': 'allowed', 'credit_limit': 500})
    assert resp.status_code == 200, resp.get_json()

    v2, u2 = _row_version(db_conn, 'customers', cust_id)
    assert v2 == v1, 'a credit-only PATCH must not bump row_version'
    assert u2 == u1, 'a credit-only PATCH must not stamp updated_at_utc'
    events_after = len(_sync_events(db_conn, 'customer', cust_id))
    assert events_after == events_before, 'a credit-only PATCH must not queue a sync event'


def test_delete_customer_soft_delete_bumps_row_version_and_emits(client, db_conn):
    cust_id = client.post('/api/sub/retail/customers', json={'name': 'To Delete'}).get_json()['data']['id']
    v1, u1 = _row_version(db_conn, 'customers', cust_id)

    resp = client.delete(f'/api/sub/retail/customers/{cust_id}')
    assert resp.status_code == 200

    v2, u2 = _row_version(db_conn, 'customers', cust_id)
    assert v2 == v1 + 1 and u2 != u1
    events = _sync_events(db_conn, 'customer', cust_id)
    assert events[-1]['event_type'] == 'delete'
    assert json.loads(events[-1]['payload'])['row_version'] == v2


# ── Suppliers ─────────────────────────────────────────────────────────────

def test_create_supplier_stamps_row_version_1_and_emits_it(client, db_conn):
    resp = client.post('/api/sub/retail/suppliers', json={'name': 'V17 Supplier'})
    sid = resp.get_json()['data']['id']
    version, updated_at = _row_version(db_conn, 'suppliers', sid)
    assert version == 1 and updated_at is not None
    payload = json.loads(_sync_events(db_conn, 'supplier', sid)[0]['payload'])
    assert payload['row_version'] == 1


def test_update_supplier_synced_field_bumps_and_emits(client, db_conn):
    sid = client.post('/api/sub/retail/suppliers', json={'name': 'Before'}).get_json()['data']['id']
    v1, u1 = _row_version(db_conn, 'suppliers', sid)

    resp = client.patch(f'/api/sub/retail/suppliers/{sid}', json={'phone': '+1-555-1111'})
    assert resp.status_code == 200

    v2, u2 = _row_version(db_conn, 'suppliers', sid)
    assert v2 == v1 + 1 and u2 != u1
    events = _sync_events(db_conn, 'supplier', sid)
    payload = json.loads(events[-1]['payload'])
    assert payload['row_version'] == v2


def test_update_supplier_payment_terms_only_patch_does_not_bump_or_emit(client, db_conn):
    """`payment_terms` is accepted by update_supplier's own `allowed` list
    but is NOT yet a first-class synced column (see that route's own
    comment and SYNCED_SUPPLIER_FIELDS in retail_api.py) -- same rule as
    the customer credit-only PATCH above."""
    sid = client.post('/api/sub/retail/suppliers', json={'name': 'Terms Only'}).get_json()['data']['id']
    v1, u1 = _row_version(db_conn, 'suppliers', sid)
    events_before = len(_sync_events(db_conn, 'supplier', sid))

    resp = client.patch(f'/api/sub/retail/suppliers/{sid}', json={'payment_terms': 'net30'})
    assert resp.status_code == 200, resp.get_json()

    v2, u2 = _row_version(db_conn, 'suppliers', sid)
    assert v2 == v1, 'a payment_terms-only PATCH must not bump row_version'
    assert u2 == u1
    assert len(_sync_events(db_conn, 'supplier', sid)) == events_before


def test_delete_supplier_soft_delete_bumps_row_version_and_emits(client, db_conn):
    sid = client.post('/api/sub/retail/suppliers', json={'name': 'To Delete'}).get_json()['data']['id']
    v1, u1 = _row_version(db_conn, 'suppliers', sid)

    resp = client.delete(f'/api/sub/retail/suppliers/{sid}')
    assert resp.status_code == 200

    v2, u2 = _row_version(db_conn, 'suppliers', sid)
    assert v2 == v1 + 1 and u2 != u1
    events = _sync_events(db_conn, 'supplier', sid)
    assert events[-1]['event_type'] == 'delete'
    assert json.loads(events[-1]['payload'])['row_version'] == v2


# ── Reorder requests ──────────────────────────────────────────────────────

def test_reorder_request_created_by_post_sale_hook_stamps_row_version_1(client, db_conn):
    pid = _make_product(client, reorder_level=8, reorder_method='whatsapp', initial_stock=10)
    _sell(client, pid, 5)  # 10 - 5 = 5, at/below reorder_level=8 -> creates a request

    req = db_conn.execute(
        "SELECT id FROM reorder_requests WHERE company_id=(SELECT company_id FROM products WHERE id=?) "
        "AND product_id=?", (pid, pid)).fetchone()
    assert req is not None, 'the post-sale hook must have created a pending reorder request'
    version, updated_at = _row_version(db_conn, 'reorder_requests', req['id'])
    assert version == 1 and updated_at is not None

    events = _sync_events(db_conn, 'reorder_request', req['id'])
    assert events and events[0]['event_type'] == 'create'
    payload = json.loads(events[0]['payload'])
    assert payload['row_version'] == 1
    assert payload['updated_at_utc'] == updated_at


def test_accept_reorder_request_bumps_row_version_and_emits_the_new_value(client, db_conn):
    pid = _make_product(client, reorder_level=8, reorder_method='whatsapp', initial_stock=10)
    _sell(client, pid, 5)
    req = db_conn.execute("SELECT id FROM reorder_requests WHERE product_id=?", (pid,)).fetchone()
    v1, u1 = _row_version(db_conn, 'reorder_requests', req['id'])

    resp = client.post(f'/api/sub/retail/reorder-requests/{req["id"]}/accept')
    assert resp.status_code == 200, resp.get_json()

    v2, u2 = _row_version(db_conn, 'reorder_requests', req['id'])
    assert v2 == v1 + 1 and u2 != u1
    events = _sync_events(db_conn, 'reorder_request', req['id'])
    payload = json.loads(events[-1]['payload'])
    assert payload['row_version'] == v2
    assert payload['status'] == 'accepted'


def test_decline_reorder_request_bumps_row_version_and_emits_the_new_value(client, db_conn):
    pid = _make_product(client, reorder_level=8, reorder_method='whatsapp', initial_stock=10)
    _sell(client, pid, 5)
    req = db_conn.execute("SELECT id FROM reorder_requests WHERE product_id=?", (pid,)).fetchone()
    v1, u1 = _row_version(db_conn, 'reorder_requests', req['id'])

    resp = client.post(f'/api/sub/retail/reorder-requests/{req["id"]}/decline')
    assert resp.status_code == 200, resp.get_json()

    v2, u2 = _row_version(db_conn, 'reorder_requests', req['id'])
    assert v2 == v1 + 1 and u2 != u1
    events = _sync_events(db_conn, 'reorder_request', req['id'])
    payload = json.loads(events[-1]['payload'])
    assert payload['row_version'] == v2
    assert payload['status'] == 'declined'


# ── THE TRAP: accumulators must never bump or emit ───────────────────────

def test_loyalty_accumulator_sale_does_not_bump_row_version_or_emit_a_customer_event(client, db_conn):
    """create_sale's `UPDATE customers SET total_spent=total_spent+?,
    loyalty_points=loyalty_points+?` runs on EVERY sale against a named
    customer -- the hottest write path any customer row has. It must not
    move row_version, or a shop that only ever sells to a customer (never
    edits their name/phone) would still watch that customer's version climb
    on every visit, and once stage 6a-ii's gate is live, a genuine phone
    correction made on a different till would then arrive with a LOWER
    version than this device's own phantom bumps and be discarded as stale
    -- see database/schema.py's RETAIL_SCHEMA_VERSION v17 comment and
    retail_api.py's create_sale comment on this exact UPDATE."""
    cust_id = client.post('/api/sub/retail/customers', json={'name': 'Loyalty Customer'}).get_json()['data']['id']
    pid = _make_product(client, initial_stock=50, sell_price=25.0)
    v1, u1 = _row_version(db_conn, 'customers', cust_id)
    events_before = len(_sync_events(db_conn, 'customer', cust_id))

    _sell(client, pid, 2, customer_id=cust_id)  # $50 -> 5 loyalty points, real accumulator movement

    cust = db_conn.execute("SELECT total_spent, loyalty_points FROM customers WHERE id=?", (cust_id,)).fetchone()
    assert cust['total_spent'] > 0 and cust['loyalty_points'] > 0  # the accumulator DID move

    v2, u2 = _row_version(db_conn, 'customers', cust_id)
    assert v2 == v1, 'a sale must not bump the customer row_version'
    assert u2 == u1, 'a sale must not stamp the customer updated_at_utc'
    events_after = len(_sync_events(db_conn, 'customer', cust_id))
    assert events_after == events_before, 'a sale must not queue a customer sync event'


def test_credit_sale_adjust_credit_does_not_bump_row_version_or_emit_a_customer_event(client, db_conn):
    """`_adjust_credit` (retail_api.py) writes ONLY `customers.credit_balance`
    / `suppliers.credit_balance` -- a second accumulator column found by
    exhaustively searching every UPDATE against these five tables (its
    table name is an f-string variable, so a literal-text search for
    "UPDATE customers"/"UPDATE suppliers" alone would miss it). Structurally
    identical to the loyalty accumulator: device-local, never synced, must
    never bump or emit."""
    cust_id = client.post('/api/sub/retail/customers', json={'name': 'Credit Sale Customer'}).get_json()['data']['id']
    client.patch(f'/api/sub/retail/customers/{cust_id}', json={'credit_mode': 'allowed', 'credit_limit': 1000})
    pid = _make_product(client, initial_stock=50, sell_price=25.0)
    v1, u1 = _row_version(db_conn, 'customers', cust_id)
    events_before = len(_sync_events(db_conn, 'customer', cust_id))

    resp = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 2}],
        'amount_paid': 0, 'payment_method': 'credit', 'customer_id': cust_id,
        'idempotency_key': str(uuid.uuid4()),
    })
    assert resp.status_code == 200, resp.get_json()

    cust = db_conn.execute("SELECT credit_balance FROM customers WHERE id=?", (cust_id,)).fetchone()
    assert cust['credit_balance'] and cust['credit_balance'] > 0  # the accumulator DID move

    v2, u2 = _row_version(db_conn, 'customers', cust_id)
    assert v2 == v1, 'a credit sale must not bump the customer row_version'
    assert u2 == u1
    assert len(_sync_events(db_conn, 'customer', cust_id)) == events_before


# ── import_api.py: the two write sites this file's HTTP fixtures above
# never exercise (create sites there follow the same INSERT-with-literal-1
# pattern already proven by the routes above; these are its two UPDATE
# sites, each with its own `row_version=row_version+1` bump) ────────────

def test_import_product_update_bumps_row_version_and_emits_the_new_value(client, db_conn):
    sku = f'V17IMPORT-{uuid.uuid4().hex[:8]}'
    mapping = {"name": "Product Name", "sku": "SKU", "sell_price": "Selling Price"}
    headers = ['Product Name', 'SKU', 'Selling Price']
    _import(client, 'products', mapping,
            [{'Product Name': 'Original', 'SKU': sku, 'Selling Price': '10'}], headers)
    pid = db_conn.execute("SELECT id FROM products WHERE sku=?", (sku,)).fetchone()['id']
    v1, u1 = _row_version(db_conn, 'products', pid)

    result = _import(client, 'products', mapping,
                      [{'Product Name': 'Renamed', 'SKU': sku, 'Selling Price': '20'}], headers)
    assert result['updated'] == 1

    v2, u2 = _row_version(db_conn, 'products', pid)
    assert v2 == v1 + 1 and u2 != u1
    events = _sync_events(db_conn, 'product', pid)
    payload = json.loads(events[-1]['payload'])
    assert payload['row_version'] == v2


def test_import_customer_update_bumps_row_version_and_emits_the_new_value(client, db_conn):
    email = f'v17import-{uuid.uuid4().hex[:8]}@test.local'
    mapping = {"name": "Name", "email": "Email"}
    headers = ['Name', 'Email']
    _import(client, 'customers', mapping, [{'Name': 'Original', 'Email': email}], headers)
    cust_id = db_conn.execute("SELECT id FROM customers WHERE email=?", (email,)).fetchone()['id']
    v1, u1 = _row_version(db_conn, 'customers', cust_id)

    result = _import(client, 'customers', mapping, [{'Name': 'Renamed', 'Email': email}], headers)
    assert result['updated'] == 1

    v2, u2 = _row_version(db_conn, 'customers', cust_id)
    assert v2 == v1 + 1 and u2 != u1
    events = _sync_events(db_conn, 'customer', cust_id)
    payload = json.loads(events[-1]['payload'])
    assert payload['row_version'] == v2
    assert payload['name'] == 'Renamed'
