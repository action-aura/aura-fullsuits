"""Aura Retail -- product variants, wave 1 (schema v25).

Covers the launch-readiness "product variants" wave: a variant ("Red / L")
is a PRODUCT with a `parent_product_id` pointer to another product plus a
free-text `variant_label`, not a new table with its own stock -- see
docs/launch-readiness/variants-and-modifiers-design.md section 3 and
ROADMAP.md's 2026-08-31 "retail schema v25 CLAIMED for product variants"
entry.

THE PART MOST LIKELY TO BE GOT WRONG (and this file's headline test):
`product` is already a synced entity type. The two new columns must reach
all three of the product sync payload (retail_api.py's create/update
emission), the apply-branch INSERT column list, and the changed-field delta
allowlist (both in sync_service.py's product branch) -- miss any one and a
variant arrives on a peer device as an orphan standalone product, silently,
with no error.

This file follows the same self-contained bootstrap convention as every
other file in this suite (no shared conftest.py exists for
products/retail/tests/ -- confirmed by inspection): its own temp app-data
dir, its own license seed, its own Flask app boot, its own fixtures local to
this file. The two-device sync tests below follow retail_product_sync_test.py's
own established pattern for this exact class of question (does entity X's
field survive `SyncService.apply_pull_result` on a hand-built receiving
connection) rather than the heavier `_stock_sync_harness.py` subprocess
harness, which exists for a different question -- do two independently
WRITING real devices produce a wrong number -- that this file's questions
do not need.

Run (ONE FILE PER PYTEST PROCESS, from the repo root):
    py -3.14 -m pytest products/retail/tests/retail_product_variants_test.py -v
"""
import json
import os
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_variants_"))
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
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402
from database import schema as retail_schema  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures ──────────────────────────────────────────────────────────────

def _make_admin(prefix):
    """A real admin account for its own newly-created company -- admin
    bypasses every capability gate, so these tests exercise the variants
    logic itself rather than incidentally re-testing the capability matrix
    (that suite is run separately, see the regression sweep)."""
    email = f'{prefix}-{uuid.uuid4().hex[:10]}@test.local'
    password = 'VariantsTestPW1'
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
def client():
    """One fresh company per test, so no test's rows are visible to another."""
    c, _cid = _make_admin('variants')
    return c


@pytest.fixture
def db_conn():
    conn = get_retail_conn()
    yield conn
    conn.close()


def _create_product(client, **kw):
    payload = dict({'name': 'Widget', 'sku': f'SKU-{uuid.uuid4().hex[:10]}', 'sell_price': 10.0}, **kw)
    r = client.post('/api/sub/retail/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _sell(client, product_id, qty=1):
    return client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': product_id, 'quantity': qty}],
        'amount_paid': 100000, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    })


def _products_by_id(client):
    return {p['id']: p for p in client.get('/api/sub/retail/products').get_json()['data']}


def _outbox_events(db_conn, entity_id):
    """Every sync_outbox row this device queued for one product id, in
    the order it queued them, as (event_type, decoded_payload) pairs."""
    rows = db_conn.execute(
        "SELECT event_type, payload FROM sync_outbox WHERE entity_type='product' AND entity_id=? ORDER BY id",
        (entity_id,)
    ).fetchall()
    return [(r['event_type'], json.loads(r['payload'])) for r in rows]


def _make_device_b():
    """A second, independent SQLite database standing in for a peer device's
    retail.db -- hand-built with exactly the columns sync_service.py's
    product apply-branch INSERT touches, same convention
    retail_product_sync_test.py's own
    test_pulled_product_delete_soft_deletes_and_never_touches_a_row_this_device_still_uses
    already uses for this exact class of question."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE products (
            id TEXT PRIMARY KEY, company_id INTEGER, sku TEXT, barcode TEXT, name TEXT,
            category_id TEXT, supplier_id TEXT, cost_price REAL DEFAULT 0, sell_price REAL DEFAULT 0,
            tax_rate REAL DEFAULT 0, unit TEXT DEFAULT 'pcs', reorder_level INTEGER DEFAULT 5,
            reorder_method TEXT DEFAULT 'none', status TEXT DEFAULT 'active', deleted_at_utc TEXT,
            parent_product_id TEXT, variant_label TEXT,
            row_version INTEGER NOT NULL DEFAULT 1, updated_at_utc TEXT
        );
        CREATE TABLE sync_cursor (id INTEGER PRIMARY KEY CHECK (id = 1), last_seq INTEGER NOT NULL DEFAULT 0);
        INSERT INTO sync_cursor (id, last_seq) VALUES (1, 0);
        CREATE TABLE sync_conflicts (
            id TEXT PRIMARY KEY, company_id INTEGER, entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL, event_type TEXT NOT NULL, local_row_version INTEGER,
            incoming_row_version INTEGER, incoming_payload TEXT NOT NULL, detected_at_utc TEXT NOT NULL
        );
    """)
    conn.commit()
    return conn


# ── (1) Migration lands on head and is idempotent ────────────────────────

def test_v25_migration_step_is_idempotent_and_adds_the_right_shape():
    """`_migrate_add_product_variants` in isolation, against a hand-built
    'already past v22' products table -- proves the migration step itself
    (idempotent ADD COLUMN pair + partial index), independent of the whole
    chain."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE products (
            id TEXT PRIMARY KEY, company_id INTEGER DEFAULT 1, sku TEXT NOT NULL,
            barcode TEXT, name TEXT NOT NULL, category_id TEXT, status TEXT DEFAULT 'active',
            deleted_at_utc TEXT
        );
    """)
    conn.execute("INSERT INTO products (id,company_id,sku,name) VALUES ('p-1',1,'SKU-1','Widget')")
    conn.commit()

    retail_schema._migrate_add_product_variants(conn)
    conn.commit()
    cols = {row[1] for row in conn.execute('PRAGMA table_info(products)').fetchall()}
    assert {'parent_product_id', 'variant_label'} <= cols

    row = conn.execute("SELECT parent_product_id, variant_label FROM products WHERE id='p-1'").fetchone()
    assert row['parent_product_id'] is None and row['variant_label'] is None  # additive, no data touched

    idx_names = {row[1] for row in conn.execute("PRAGMA index_list(products)").fetchall()}
    assert 'idx_products_parent' in idx_names

    # Idempotent -- the normal case after any interrupted migration, since
    # ensure_schema_version leaves user_version un-advanced on failure.
    retail_schema._migrate_add_product_variants(conn)
    conn.commit()
    cols_after = {row[1] for row in conn.execute('PRAGMA table_info(products)').fetchall()}
    assert cols_after == cols
    assert conn.execute("SELECT COUNT(*) c FROM products").fetchone()['c'] == 1


def test_the_real_migration_chain_lands_a_fresh_install_on_v25(db_conn):
    version = db_conn.execute('PRAGMA user_version').fetchone()[0]
    # Reads RETAIL_SCHEMA_VERSION rather than a hardcoded 25 -- stays true if
    # the constant is ever bumped again without needing an edit here.
    assert version == retail_schema.RETAIL_SCHEMA_VERSION
    assert retail_schema.RETAIL_SCHEMA_VERSION == 25, (
        "v24 is reserved by name for inter-branch transfers (ROADMAP.md "
        "2026-08-30); this branch's own claim is v25 -- a drift here is "
        "the exact version-number collision the ROADMAP ledger exists to "
        "prevent."
    )
    cols = {row[1] for row in db_conn.execute('PRAGMA table_info(products)').fetchall()}
    assert {'parent_product_id', 'variant_label'} <= cols


# ── (2) Invisible unless used ─────────────────────────────────────────────

def test_products_with_no_variants_anywhere_behave_exactly_as_before(client):
    pid = _create_product(client, name='Plain Widget', initial_stock=5)

    row = _products_by_id(client)[pid]
    assert row['parent_product_id'] is None
    assert row['variant_label'] is None

    looked_up = client.get(f"/api/sub/retail/products/lookup?code={row['sku']}")
    assert looked_up.status_code == 200, looked_up.get_json()
    assert looked_up.get_json()['data']['has_variants'] == False

    sale = _sell(client, pid, qty=2)
    assert sale.status_code == 200, sale.get_json()
    assert _products_by_id(client)[pid]['total_stock'] == 3


# ── (3) A variant sells: own barcode, own stock ───────────────────────────

def test_variant_sells_via_own_barcode_and_only_its_own_stock_decrements(client):
    parent_barcode = f'PARENT-{uuid.uuid4().hex[:8]}'
    variant_barcode = f'VARIANT-{uuid.uuid4().hex[:8]}'
    parent_id = _create_product(client, name='Polo Shirt', barcode=parent_barcode, initial_stock=5)
    variant_id = _create_product(
        client, name='Polo Shirt - Red / L', barcode=variant_barcode,
        parent_product_id=parent_id, variant_label='Red / L', initial_stock=10,
    )

    # The v22 four-rung scan ladder is UNCHANGED -- verified, not assumed: a
    # variant's own barcode resolves straight to the variant row.
    looked_up = client.get(f'/api/sub/retail/products/lookup?code={variant_barcode}')
    assert looked_up.status_code == 200, looked_up.get_json()
    assert looked_up.get_json()['data']['id'] == variant_id
    assert looked_up.get_json()['data']['has_variants'] == False  # a variant cannot itself have children

    # Scanning the PARENT's own barcode resolves to the parent and now says
    # so (design section 3.5's one additive response field).
    parent_lookup = client.get(f'/api/sub/retail/products/lookup?code={parent_barcode}')
    assert parent_lookup.status_code == 200, parent_lookup.get_json()
    assert parent_lookup.get_json()['data']['id'] == parent_id
    assert parent_lookup.get_json()['data']['has_variants'] == True

    sale = _sell(client, variant_id, qty=3)
    assert sale.status_code == 200, sale.get_json()

    products = _products_by_id(client)
    assert products[variant_id]['total_stock'] == 7   # 10 - 3 -- the variant's OWN balance
    assert products[parent_id]['total_stock'] == 5     # UNCHANGED -- not the parent's


def test_a_product_with_variants_cannot_itself_be_sold(client):
    """The parent guard (design section 3.3): the grouping product has no
    inventory_balances row that means anything -- every real unit on the
    shelf is counted under a variant -- so selling the parent directly is
    refused before any row is written, rather than silently misfiling a
    sale onto a product nobody can restock against."""
    parent_id = _create_product(client, name='Polo Shirt (guard)', initial_stock=5)
    _create_product(client, name='Polo Shirt (guard) - Red / L',
                     parent_product_id=parent_id, variant_label='Red / L', initial_stock=5)

    blocked = _sell(client, parent_id, qty=1)
    assert blocked.status_code == 400, blocked.get_json()
    assert 'variant' in blocked.get_json()['message'].lower()

    # And it really did not happen.
    assert _products_by_id(client)[parent_id]['total_stock'] == 5


# ── (4) SYNC: the headline test ───────────────────────────────────────────

def test_sync_create_carries_the_parent_link_and_label_to_device_b(client, db_conn):
    parent_id = _create_product(client, name='Polo Shirt (sync)')
    variant_id = _create_product(
        client, name='Polo Shirt (sync) - Red / L',
        parent_product_id=parent_id, variant_label='Red / L',
    )

    parent_events = _outbox_events(db_conn, parent_id)
    variant_events = _outbox_events(db_conn, variant_id)
    assert parent_events and parent_events[0][0] == 'create'
    assert variant_events and variant_events[0][0] == 'create'
    # The site named in ROADMAP.md's own warning: both new columns must be
    # ON THE WIRE, not just in this device's own local database.
    assert variant_events[0][1]['parent_product_id'] == parent_id
    assert variant_events[0][1]['variant_label'] == 'Red / L'

    device_b = _make_device_b()
    svc = SyncService(client_factory=lambda: None, get_conn=lambda: device_b,
                       local_company_id_provider=lambda: 'device-b-company')
    svc.apply_pull_result(device_b, {
        "events": [
            {"entity_type": "product", "event_type": "create", "payload": parent_events[0][1], "seq": 1},
            {"entity_type": "product", "event_type": "create", "payload": variant_events[0][1], "seq": 2},
        ],
        "cursor": 2,
    })
    device_b.commit()

    row = device_b.execute(
        "SELECT parent_product_id, variant_label FROM products WHERE id=?", (variant_id,)
    ).fetchone()
    assert row is not None, "the variant's product row never arrived on device B at all"
    # THE assertion -- not "the row exists", the LINK.
    assert row['parent_product_id'] == parent_id
    assert row['variant_label'] == 'Red / L'


def test_sync_update_of_a_variants_label_reaches_device_b_too(client, db_conn):
    """The UPDATE path test_sync_create... does not cover -- M3's target.
    Establishes the row on device B via a create (as above), then proves a
    later PATCH's delta-gated update ALSO carries the two columns."""
    parent_id = _create_product(client, name='Polo Shirt (sync update)')
    variant_id = _create_product(
        client, name='Polo Shirt (sync update) - Red / L',
        parent_product_id=parent_id, variant_label='Red / L',
    )
    parent_events = _outbox_events(db_conn, parent_id)
    variant_events = _outbox_events(db_conn, variant_id)

    device_b = _make_device_b()
    svc = SyncService(client_factory=lambda: None, get_conn=lambda: device_b,
                       local_company_id_provider=lambda: 'device-b-company')
    svc.apply_pull_result(device_b, {
        "events": [
            {"entity_type": "product", "event_type": "create", "payload": parent_events[0][1], "seq": 1},
            {"entity_type": "product", "event_type": "create", "payload": variant_events[0][1], "seq": 2},
        ],
        "cursor": 2,
    })
    device_b.commit()

    # A PATCH that touches ONLY variant_label -- `_changed_fields` names
    # exactly that one column (stage 6b-i delta-gating), so this exercises
    # the changed-field delta ALLOWLIST specifically, not just the payload.
    patch = client.patch(f'/api/sub/retail/products/{variant_id}', json={'variant_label': 'Red / XL'})
    assert patch.status_code == 200, patch.get_json()

    update_events = [e for e in _outbox_events(db_conn, variant_id) if e[0] == 'update']
    assert update_events, "update_product did not queue an update sync event"
    update_payload = update_events[-1][1]
    assert update_payload.get('_changed_fields') == ['variant_label']

    svc.apply_pull_result(device_b, {
        "events": [{"entity_type": "product", "event_type": "update", "payload": update_payload, "seq": 3}],
        "cursor": 3,
    })
    device_b.commit()

    row = device_b.execute(
        "SELECT variant_label, parent_product_id FROM products WHERE id=?", (variant_id,)
    ).fetchone()
    assert row['variant_label'] == 'Red / XL'
    assert row['parent_product_id'] == parent_id  # untouched by this PATCH, must survive


# ── (5) A parent's variants can be listed; list_products stays unchanged ──

def test_a_parents_variants_can_be_listed_and_list_products_stays_unchanged_for_others(client):
    parent_id = _create_product(client, name='Polo Shirt (list)')
    v1 = _create_product(client, name='Polo Shirt (list) - Red / S',
                          parent_product_id=parent_id, variant_label='Red / S')
    v2 = _create_product(client, name='Polo Shirt (list) - Red / L',
                          parent_product_id=parent_id, variant_label='Red / L')
    unrelated_id = _create_product(client, name='Unrelated Widget (list)')

    variants = client.get(f'/api/sub/retail/products/{parent_id}/variants')
    assert variants.status_code == 200, variants.get_json()
    ids = {row['id'] for row in variants.get_json()['data']}
    assert ids == {v1, v2}
    assert parent_id not in ids and unrelated_id not in ids

    # A caller that knows nothing about variants -- no filter, no special
    # handling -- keeps getting the exact same array shape: every row it
    # asks about is still there, unrelated products included.
    all_ids = {p['id'] for p in client.get('/api/sub/retail/products').get_json()['data']}
    assert {parent_id, v1, v2, unrelated_id} <= all_ids


# ── (6) TENANCY ────────────────────────────────────────────────────────────

def test_parent_product_id_from_another_company_is_refused(client):
    other_client, _other_cid = _make_admin('variants-other')
    foreign_parent_id = _create_product(other_client, name='Other Company Parent')

    r = client.post('/api/sub/retail/products', json={
        'name': 'Sneaky Variant', 'sku': f'SKU-{uuid.uuid4().hex[:10]}', 'sell_price': 5,
        'parent_product_id': foreign_parent_id, 'variant_label': 'Red / L',
    })
    assert r.status_code == 400, r.get_json()
    assert 'parent_product_id' in r.get_json()['message']

    # And it really did not happen -- a 400 that writes anyway is not a guard.
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT COUNT(*) c FROM products WHERE parent_product_id=?", (foreign_parent_id,)
    ).fetchone()
    conn.close()
    assert row['c'] == 0


def test_parent_product_id_from_another_company_is_refused_on_update_too(client):
    """The same unvalidated-foreign-key shape, one request later -- see
    _validate_parent_product's own docstring for why both routes are
    checked, not just create."""
    other_client, _other_cid = _make_admin('variants-other-upd')
    foreign_parent_id = _create_product(other_client, name='Other Company Parent (update)')
    pid = _create_product(client, name='Ordinary Product (update)')

    r = client.patch(f'/api/sub/retail/products/{pid}', json={'parent_product_id': foreign_parent_id})
    assert r.status_code == 400, r.get_json()

    conn = get_retail_conn()
    row = conn.execute("SELECT parent_product_id FROM products WHERE id=?", (pid,)).fetchone()
    conn.close()
    assert row['parent_product_id'] is None


# ── (7) No grandchildren ───────────────────────────────────────────────────

def test_a_variant_of_a_variant_is_refused(client):
    parent_id = _create_product(client, name='Polo Shirt (grandchild)')
    variant_id = _create_product(client, name='Polo Shirt (grandchild) - Red / L',
                                  parent_product_id=parent_id, variant_label='Red / L')

    r = client.post('/api/sub/retail/products', json={
        'name': 'Grandchild', 'sku': f'SKU-{uuid.uuid4().hex[:10]}', 'sell_price': 5,
        'parent_product_id': variant_id, 'variant_label': 'XL',
    })
    assert r.status_code == 400, r.get_json()
    assert 'grandch' in r.get_json()['message'].lower()

    conn = get_retail_conn()
    row = conn.execute(
        "SELECT COUNT(*) c FROM products WHERE parent_product_id=?", (variant_id,)
    ).fetchone()
    conn.close()
    assert row['c'] == 0
