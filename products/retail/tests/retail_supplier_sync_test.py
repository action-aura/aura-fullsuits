"""Aura Retail -- multi-device sync foundation: Suppliers wiring.

Covers Task 4: `create_supplier`/`delete_supplier` now queue a `sync_outbox`
row (see `retail_api.py`'s `_queue_sync_event`), supplier ids are real
client-generated UUIDs (not autoincrement ints, per this task's schema
migration), and a supplier delete is ALWAYS a soft-delete (`status=
'inactive'`) via a brand-new DELETE route (`delete_supplier` did not exist
before this task). Also covers the pull (apply) side:
`SyncService._apply_event`'s new `supplier` branch must soft-delete on a
pulled delete event too, never `DELETE FROM` -- unlike customers, suppliers
DOES have a declared FK pointing at it (`purchase_orders.supplier_id
REFERENCES suppliers(id)`), so a hard delete relayed in from another device
would trip that FK the moment this device still has a PO against the row;
soft-delete sidesteps that entirely.

This file follows the same self-contained bootstrap convention as
`retail_customer_sync_test.py` (no shared conftest.py exists for
products/retail/tests/ -- confirmed by inspection): its own temp app-data
dir, its own license seed, its own Flask app boot, its own `client`/`db_conn`
fixtures local to this file.

Run:
    pytest products/retail/tests/retail_supplier_sync_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_supsync_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

# Seeded BEFORE the app is built, matching every other route-level test file
# in this suite -- creating/deleting a supplier is capability-guarded, so an
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
    email = f'supsync-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'SupSyncPW1'
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


def test_create_supplier_queues_a_sync_outbox_event(client, db_conn):
    resp = client.post('/api/sub/retail/suppliers', json={
        'name': 'Sync Test Supplier', 'phone': '+1-555-0001', 'email': 'sync@test.local',
    })
    assert resp.status_code == 200
    new_id = resp.get_json()['data']['id']
    assert isinstance(new_id, str) and len(new_id) == 36  # real UUID shape, not an int

    row = db_conn.execute(
        "SELECT entity_type, entity_id, event_type, payload FROM sync_outbox WHERE entity_type='supplier'"
    ).fetchone()
    assert row is not None
    assert row['entity_id'] == new_id
    assert row['event_type'] == 'create'
    payload = json.loads(row['payload'])
    assert payload['id'] == new_id
    assert 'company_id' not in payload  # never on the wire -- see _queue_sync_event's category precedent


def test_update_supplier_queues_a_sync_outbox_event(client, db_conn):
    create = client.post('/api/sub/retail/suppliers', json={'name': 'Original Name'})
    sup_id = create.get_json()['data']['id']

    resp = client.patch(f'/api/sub/retail/suppliers/{sup_id}', json={'name': 'Renamed Supplier'})
    assert resp.status_code == 200

    row = db_conn.execute(
        "SELECT entity_type, entity_id, event_type, payload FROM sync_outbox WHERE entity_type='supplier' "
        "AND event_type='update' AND entity_id=?", (sup_id,)
    ).fetchone()
    assert row is not None
    payload = json.loads(row['payload'])
    assert payload['name'] == 'Renamed Supplier'

    updated = db_conn.execute("SELECT name FROM suppliers WHERE id=?", (sup_id,)).fetchone()
    assert updated['name'] == 'Renamed Supplier'


def test_update_supplier_missing_returns_404(client):
    resp = client.patch('/api/sub/retail/suppliers/not-a-real-id', json={'name': 'Whoever'})
    assert resp.status_code == 404


def test_delete_supplier_is_always_a_soft_delete(client, db_conn):
    create = client.post('/api/sub/retail/suppliers', json={'name': 'To Be Deleted'})
    sup_id = create.get_json()['data']['id']

    resp = client.delete(f'/api/sub/retail/suppliers/{sup_id}')
    assert resp.status_code == 200

    row = db_conn.execute("SELECT status FROM suppliers WHERE id=?", (sup_id,)).fetchone()
    assert row is not None and row['status'] == 'inactive'  # row still exists, never hard-deleted

    outbox = db_conn.execute(
        "SELECT event_type FROM sync_outbox WHERE entity_type='supplier' AND entity_id=? ORDER BY created_at DESC LIMIT 1",
        (sup_id,),
    ).fetchone()
    assert outbox['event_type'] == 'delete'

    # A deleted supplier must no longer appear in the active listing.
    listing = client.get('/api/sub/retail/suppliers').get_json()['data']
    assert all(s['id'] != sup_id for s in listing)


def test_delete_supplier_missing_returns_404(client):
    resp = client.delete('/api/sub/retail/suppliers/not-a-real-id')
    assert resp.status_code == 404


def test_pulled_supplier_delete_soft_deletes_and_never_touches_a_row_this_device_still_uses(monkeypatch):
    from commercial_runtime.sync.sync_service import SyncService
    import sqlite3

    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        -- launch-readiness Phase 6 stage 6a-i (2026-08-26 follow-up):
        -- row_version/updated_at_utc added -- _apply_event's supplier
        -- create/update/delete branches now write both columns (carrying
        -- the sender's row_version through, so two devices' counters
        -- converge instead of silently diverging), and this hand-built
        -- minimal fixture predates that.
        -- launch-readiness Phase 6 stage 6b-ii (tombstones): deleted_at_utc
        -- added too -- the supplier delete branch now stamps it (alongside
        -- the unchanged status='inactive'; see retail_api.py's
        -- delete_supplier comment for why both are written), and this
        -- fixture predates that the same way it predated row_version.
        CREATE TABLE suppliers (id TEXT PRIMARY KEY, company_id INTEGER, name TEXT, phone TEXT, email TEXT, address TEXT, status TEXT DEFAULT 'active',
            row_version INTEGER NOT NULL DEFAULT 1, updated_at_utc TEXT, deleted_at_utc TEXT);
        CREATE TABLE purchase_orders (id INTEGER PRIMARY KEY, company_id INTEGER, supplier_id TEXT, total REAL,
            FOREIGN KEY (supplier_id) REFERENCES suppliers(id));
        CREATE TABLE sync_cursor (id INTEGER PRIMARY KEY CHECK (id=1), last_seq INTEGER NOT NULL DEFAULT 0);
        INSERT INTO sync_cursor (id, last_seq) VALUES (1, 0);
        -- launch-readiness Phase 6 stage 6a-ii: `sync_conflicts`, shaped
        -- exactly like _migrate_add_sync_conflicts_and_drop_quantity_reserved's
        -- own CREATE TABLE (products/retail/backend/database/schema.py, v17).
        -- The supplier delete branch's reject-stale gate can legitimately
        -- write a row here on a genuine (non-legacy) stale discard, so this
        -- hand-built fixture -- which predates v17 the same way it predated
        -- the row_version columns above -- has to carry the table forward
        -- too, or it raises `sqlite3.OperationalError: no such table:
        -- sync_conflicts` the moment that path is reached.
        CREATE TABLE sync_conflicts (
            id TEXT PRIMARY KEY, company_id INTEGER, entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL, event_type TEXT NOT NULL, local_row_version INTEGER,
            incoming_row_version INTEGER, incoming_payload TEXT NOT NULL, detected_at_utc TEXT NOT NULL
        );
    """)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("INSERT INTO suppliers (id,company_id,name,status) VALUES ('s-1',9,'TechDistrib','active')")
    conn.execute("INSERT INTO purchase_orders (company_id,supplier_id,total) VALUES (9,'s-1',450.0)")
    conn.commit()

    svc = SyncService(client_factory=lambda: None, get_conn=lambda: conn, local_company_id_provider=lambda: '9')
    svc.apply_pull_result(conn, {
        "events": [{"entity_type": "supplier", "event_type": "delete", "payload": {"id": "s-1"}, "seq": 1}],
        "cursor": 1,
    })
    conn.commit()

    row = conn.execute("SELECT status FROM suppliers WHERE id='s-1'").fetchone()
    assert row['status'] == 'inactive'  # never DELETE FROM -- the declared FK from purchase_orders would fire
    assert conn.execute("SELECT COUNT(*) c FROM purchase_orders").fetchone()['c'] == 1


def test_pulled_supplier_create_stamps_the_receiving_devices_own_company_id():
    from commercial_runtime.sync.sync_service import SyncService
    import sqlite3

    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        -- launch-readiness Phase 6 stage 6a-i (2026-08-26 follow-up):
        -- row_version/updated_at_utc added -- _apply_event's supplier
        -- create/update/delete branches now write both columns (carrying
        -- the sender's row_version through, so two devices' counters
        -- converge instead of silently diverging), and this hand-built
        -- minimal fixture predates that.
        -- launch-readiness Phase 6 stage 6b-ii (tombstones): deleted_at_utc
        -- added too -- the supplier delete branch now stamps it (alongside
        -- the unchanged status='inactive'; see retail_api.py's
        -- delete_supplier comment for why both are written), and this
        -- fixture predates that the same way it predated row_version.
        CREATE TABLE suppliers (id TEXT PRIMARY KEY, company_id INTEGER, name TEXT, phone TEXT, email TEXT, address TEXT, status TEXT DEFAULT 'active',
            row_version INTEGER NOT NULL DEFAULT 1, updated_at_utc TEXT, deleted_at_utc TEXT);
        CREATE TABLE sync_cursor (id INTEGER PRIMARY KEY CHECK (id=1), last_seq INTEGER NOT NULL DEFAULT 0);
        INSERT INTO sync_cursor (id, last_seq) VALUES (1, 0);
    """)
    conn.commit()

    svc = SyncService(client_factory=lambda: None, get_conn=lambda: conn, local_company_id_provider=lambda: 'company-B')
    svc.apply_pull_result(conn, {
        "events": [{
            "entity_type": "supplier", "event_type": "create",
            "payload": {"id": "s-2", "company_id": "company-A", "name": "GroceryDirect"}, "seq": 1,
        }],
        "cursor": 1,
    })
    conn.commit()

    row = conn.execute("SELECT company_id FROM suppliers WHERE id='s-2'").fetchone()
    assert row['company_id'] == 'company-B'  # device B's own id, never the sending device's "company-A"
