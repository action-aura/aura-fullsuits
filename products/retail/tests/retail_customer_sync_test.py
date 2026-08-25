"""Aura Retail -- multi-device sync foundation: Customers wiring.

Covers Task 3: `create_customer`/`delete_customer` now queue a `sync_outbox`
row (see `retail_api.py`'s `_queue_sync_event`), customer ids are real
client-generated UUIDs (not autoincrement ints, per this task's schema
migration), and a customer delete is ALWAYS a soft-delete (`status=
'inactive'`) via a brand-new DELETE route (`delete_customer` did not exist
before this task). Also covers the pull (apply) side:
`SyncService._apply_event`'s new `customer` branch must soft-delete on a
pulled delete event too, never `DELETE FROM`, so a row this device still
references (e.g. from `sales.customer_id` or `payments.party_id`) can never
trip an FK -- moot today (neither is a declared FK to customers(id)), but
soft-delete is still the correct behaviour so a device's own local history
never loses its customer link.

This file follows the same self-contained bootstrap convention as
`retail_product_sync_test.py` (no shared conftest.py exists for
products/retail/tests/ -- confirmed by inspection): its own temp app-data
dir, its own license seed, its own Flask app boot, its own `client`/`db_conn`
fixtures local to this file.

Run:
    pytest products/retail/tests/retail_customer_sync_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_custsync_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

# Seeded BEFORE the app is built, matching every other route-level test file
# in this suite -- creating/deleting a customer is capability-guarded, so an
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
    email = f'custsync-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'CustSyncPW1'
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


def test_create_customer_queues_a_sync_outbox_event(client, db_conn):
    resp = client.post('/api/sub/retail/customers', json={
        'name': 'Sync Test Customer', 'phone': '+1-555-0001', 'email': 'sync@test.local',
    })
    assert resp.status_code == 200
    new_id = resp.get_json()['data']['id']
    assert isinstance(new_id, str) and len(new_id) == 36  # real UUID shape, not an int

    row = db_conn.execute(
        "SELECT entity_type, entity_id, event_type, payload FROM sync_outbox WHERE entity_type='customer'"
    ).fetchone()
    assert row is not None
    assert row['entity_id'] == new_id
    assert row['event_type'] == 'create'
    payload = json.loads(row['payload'])
    assert payload['id'] == new_id
    assert 'company_id' not in payload  # never on the wire -- see _queue_sync_event's category precedent


def test_delete_customer_is_always_a_soft_delete(client, db_conn):
    create = client.post('/api/sub/retail/customers', json={'name': 'To Be Deleted'})
    cust_id = create.get_json()['data']['id']

    resp = client.delete(f'/api/sub/retail/customers/{cust_id}')
    assert resp.status_code == 200

    row = db_conn.execute("SELECT status FROM customers WHERE id=?", (cust_id,)).fetchone()
    assert row is not None and row['status'] == 'inactive'  # row still exists, never hard-deleted

    outbox = db_conn.execute(
        "SELECT event_type FROM sync_outbox WHERE entity_type='customer' AND entity_id=? ORDER BY created_at DESC LIMIT 1",
        (cust_id,),
    ).fetchone()
    assert outbox['event_type'] == 'delete'

    # A deleted customer must no longer appear in the active listing.
    listing = client.get('/api/sub/retail/customers').get_json()['data']
    assert all(c['id'] != cust_id for c in listing)


def test_pulled_customer_delete_soft_deletes_and_never_touches_a_row_this_device_still_uses(monkeypatch):
    from commercial_runtime.sync.sync_service import SyncService
    import sqlite3

    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE customers (id TEXT PRIMARY KEY, company_id INTEGER, name TEXT, phone TEXT, email TEXT, address TEXT, status TEXT DEFAULT 'active');
        CREATE TABLE sales (id INTEGER PRIMARY KEY, company_id INTEGER, customer_id TEXT, total REAL);
        CREATE TABLE sync_cursor (id INTEGER PRIMARY KEY CHECK (id=1), last_seq INTEGER NOT NULL DEFAULT 0);
        INSERT INTO sync_cursor (id, last_seq) VALUES (1, 0);
        -- Phase 5: apply_pull_result() unconditionally checks this table now
        -- (SyncService._has_quarantined_events) -- see
        -- products/retail/backend/database/schema.py's own CREATE TABLE.
        CREATE TABLE sync_apply_quarantine (entity_id TEXT NOT NULL, entity_type TEXT NOT NULL,
            event_type TEXT NOT NULL, payload TEXT NOT NULL, reason TEXT NOT NULL, detail TEXT,
            quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (entity_id, event_type));
    """)
    conn.execute("INSERT INTO customers (id,company_id,name,status) VALUES ('c-1',9,'Ahmed','active')")
    conn.execute("INSERT INTO sales (company_id,customer_id,total) VALUES (9,'c-1',150.0)")
    conn.commit()

    svc = SyncService(client_factory=lambda: None, get_conn=lambda: conn, local_company_id_provider=lambda: '9')
    svc.apply_pull_result(conn, {
        "events": [{"entity_type": "customer", "event_type": "delete", "payload": {"id": "c-1"}, "seq": 1}],
        "cursor": 1,
    })
    conn.commit()

    row = conn.execute("SELECT status FROM customers WHERE id='c-1'").fetchone()
    assert row['status'] == 'inactive'  # never DELETE FROM -- sales row is untouched, no FK ever fires
    assert conn.execute("SELECT COUNT(*) c FROM sales").fetchone()['c'] == 1
