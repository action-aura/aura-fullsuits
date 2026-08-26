"""Aura Retail -- multi-device sync foundation: Products wiring.

Covers the Task 2 wiring: `create_product`/`delete_product` now queue a
`sync_outbox` row (Task 3's outbox, relayed by Task 5's push loop -- see
`retail_api.py`'s `_queue_sync_event`), product ids are real client-generated
UUIDs (not autoincrement ints, per Task 1's schema migration), and a product
delete is ALWAYS a soft-delete (`status='inactive'`), even with no sales
history -- the design spec's decided simplification over the old
has-sales-history branch. Also covers the pull (apply) side:
`SyncService._apply_event`'s new `product` branch must soft-delete on a
pulled delete event too, never `DELETE FROM`, so a row this device still
references (e.g. from `sale_items`) can never trip an FK.

This file follows the same self-contained bootstrap convention as every
other file in this suite (no shared conftest.py exists for
products/retail/tests/ -- confirmed by inspection): its own temp app-data
dir, its own license seed, its own Flask app boot, its own `client`/`db_conn`
fixtures local to this file.

Run:
    pytest products/retail/tests/retail_product_sync_test.py -v
"""
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_prodsync_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

# Seeded BEFORE the app is built, matching every other route-level test file
# in this suite -- creating/deleting a product is capability-guarded, so an
# inactive license would 403 every test here before reaching the code paths
# this file is actually about.
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
    """A fresh, logged-in Flask test client for its own newly-created
    company -- one company per test, so sync_outbox assertions never see
    another test's rows."""
    email = f'prodsync-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'ProdSyncPW1'
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


@pytest.fixture
def db_conn():
    """A real connection to the (shared, file-backed) retail database this
    process is using -- reads back whatever the routes above just committed."""
    conn = get_retail_conn()
    yield conn
    conn.close()


def test_create_product_queues_a_sync_outbox_event(client, db_conn):
    resp = client.post('/api/sub/retail/products', json={
        'name': 'Sync Test Widget', 'sku': 'SYNC-1', 'cost_price': 2, 'sell_price': 5,
    })
    assert resp.status_code == 200
    new_id = resp.get_json()['data']['id']
    assert isinstance(new_id, str) and len(new_id) == 36  # real UUID shape, not an int

    row = db_conn.execute(
        "SELECT entity_type, entity_id, event_type, payload FROM sync_outbox WHERE entity_type='product'"
    ).fetchone()
    assert row is not None
    assert row['entity_id'] == new_id
    assert row['event_type'] == 'create'
    payload = json.loads(row['payload'])
    assert payload['id'] == new_id
    assert 'company_id' not in payload  # never on the wire -- see _queue_sync_event's category precedent


def test_delete_product_is_always_a_soft_delete_even_with_no_sales_history(client, db_conn):
    create = client.post('/api/sub/retail/products', json={'name': 'No History', 'sku': 'SYNC-2', 'sell_price': 5})
    pid = create.get_json()['data']['id']

    resp = client.delete(f'/api/sub/retail/products/{pid}')
    assert resp.status_code == 200

    row = db_conn.execute("SELECT status FROM products WHERE id=?", (pid,)).fetchone()
    assert row is not None and row['status'] == 'inactive'  # row still exists, never hard-deleted

    outbox = db_conn.execute(
        "SELECT event_type FROM sync_outbox WHERE entity_type='product' AND entity_id=? ORDER BY created_at DESC LIMIT 1",
        (pid,),
    ).fetchone()
    assert outbox['event_type'] == 'delete'


def test_pulled_product_delete_soft_deletes_and_never_touches_a_row_this_device_still_uses(monkeypatch):
    from commercial_runtime.sync.sync_service import SyncService
    import sqlite3

    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        -- launch-readiness Phase 6 stage 6a-i (2026-08-26 follow-up):
        -- row_version/updated_at_utc added -- _apply_event's product
        -- delete branch now writes both columns (carrying the sender's
        -- row_version through, so two devices' counters converge instead
        -- of silently diverging), and this hand-built minimal fixture
        -- predates that.
        CREATE TABLE products (id TEXT PRIMARY KEY, company_id INTEGER, sku TEXT, name TEXT, status TEXT DEFAULT 'active',
            row_version INTEGER NOT NULL DEFAULT 1, updated_at_utc TEXT);
        CREATE TABLE sale_items (id INTEGER PRIMARY KEY, sale_id INTEGER, product_id TEXT, quantity REAL, unit_price REAL, line_total REAL);
        CREATE TABLE sync_cursor (id INTEGER PRIMARY KEY CHECK (id=1), last_seq INTEGER NOT NULL DEFAULT 0);
        INSERT INTO sync_cursor (id, last_seq) VALUES (1, 0);
    """)
    conn.execute("INSERT INTO products (id,company_id,sku,name,status) VALUES ('p-1',9,'SKU-X','X','active')")
    conn.execute("INSERT INTO sale_items (sale_id,product_id,quantity,unit_price,line_total) VALUES (1,'p-1',1,10,10)")
    conn.commit()

    svc = SyncService(client_factory=lambda: None, get_conn=lambda: conn, local_company_id_provider=lambda: '9')
    svc.apply_pull_result(conn, {
        "events": [{"entity_type": "product", "event_type": "delete", "payload": {"id": "p-1"}, "seq": 1}],
        "cursor": 1,
    })
    conn.commit()

    row = conn.execute("SELECT status FROM products WHERE id='p-1'").fetchone()
    assert row['status'] == 'inactive'  # never DELETE FROM -- sale_items row is untouched, no FK ever fires
    assert conn.execute("SELECT COUNT(*) c FROM sale_items").fetchone()['c'] == 1
