"""Aura Retail -- multi-device sync: tombstones (launch-readiness Phase 6
stage 6b-ii, category half).

Root cause this file exists to close: deletion used to be expressed two
different, both-wrong ways. `product`/`customer`/`supplier` soft-deleted by
setting `status='inactive'` -- which overloads `status`, a column that
independently means "deactivated but not deleted". `category` hard-`DELETE`d
-- the one catalogue path a stale remote event could still destroy, because a
hard delete has no `row_version` to gate on, so stage 6a-ii's reject-stale
gate could not reach it (flagged there, fixed here).

The fix: `deleted_at_utc` (v13, dead until this stage) is now stamped on
every delete, and it is an ordinary gated write like every other catalogue
mutation -- a stale delete loses to a newer edit for the same reason every
other stale write does. `product`/`customer`/`supplier` ALSO keep writing
`status='inactive'` -- deliberately, NOT an oversight: `create_sale`'s
line-item read still filters `status='active'`, so freeing `status` before
every read path carries a `deleted_at_utc` filter would make a tombstoned
product stay sellable. `category` has no `status` column at all, so it has
no such safety net and its own read paths (`list_categories`, `list_products`
join) gained the filter directly. See docs/launch-readiness/
phase6b-decisions.md and phase6b-deltas-and-tombstones.md for the full
reasoning this file assumes rather than re-argues.

`category` deletion also cascades: schema v3 made `products.category_id`
`ON DELETE SET NULL` so that a hard delete arriving at a device still holding
products would not raise `IntegrityError` inside `_apply_event` and wedge
that device's sync cursor forever (see retail_category_delete_fk_sync_
test.py's module docstring for the full history). Tombstoning removes the
`DELETE`, so that FK cascade never fires -- `delete_category` and
`SyncService._apply_event`'s category branch both now walk `products` and
null `category_id` BY HAND, identically, so every device reaches the same
state from the same single category event. That walk deliberately does NOT
bump the touched products' own `row_version` or queue product sync events --
see this file's cascade tests for why (the loyalty-accumulator trap, reached
by a third route).

This file follows the exact two-device composition pattern
retail_changed_field_delta_test.py established one stage earlier (see that
file's own module docstring for the full "AURA_APP_DATA trap" / "Postgres
trap" reasoning, reproduced here without repeating it verbatim, and for why
`_RelayHub`/`_DeviceRelayClient` filter out a device's own pushed events --
production's real pull() route does the identical filtering). Reused
verbatim: the relay double, the device-identity-aware client wrapper, and
the `_reset_install_a_cursor` autouse fixture (install A's `sync_cursor` is
ONE global row shared by every test in this file, exactly as in that file).

THE TRAP this file's own tests had to avoid (found while writing them, not
assumed): a product whose `category_id` a cascade ALREADY nulled passes a
"tombstoned category renders as no category" assertion trivially, proving
nothing about the read-path JOIN filter itself. `test_a_tombstoned_
category_is_not_listed_and_not_joined_to_products` below deliberately writes
a product's `category_id` back onto the dead id AFTER the delete, bypassing
the cascade entirely, so it isolates the read filter (step 4) from the
cascade (step 2/3) -- reproducing the exact "residual divergence"
phase6b-decisions.md's "Decision A" names (a product concurrently
re-pointed AT a doomed category).

Run:
    pytest products/retail/tests/retail_tombstone_test.py -v
"""
import csv
import io
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_tombstone_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()  # this IS install A: a real, fully-booted Flask app
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402
import database.schema as schema  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Install A: the real Flask app + its own real database ──────────────────

@pytest.fixture
def client():
    """Install A's own logged-in Flask test client, for its own newly
    created company -- one company per test, matching retail_changed_field_
    delta_test.py's identical fixture (and the same reason: the decisive
    tests below need A's `service_a` to genuinely PULL)."""
    email = f'tomb-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'TombstonePW1'
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
    c.company_id = company_id
    return c


@pytest.fixture
def a_conn():
    """A real connection to install A's own (shared, file-backed) retail
    database -- reads back whatever the Flask routes above just committed."""
    conn = get_retail_conn()
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _reset_install_a_cursor():
    """See retail_changed_field_delta_test.py's identical fixture for the
    full reasoning -- install A's own retail.db is ONE module-level database
    shared by every test in THIS file too, but `sync_cursor` is a single
    GLOBAL row (id=1), never scoped per company. Without this reset, a test
    running after an earlier one that already advanced A's real cursor would
    find A "ahead of" its own fresh hub's seq numbers, and pull_once() would
    silently return zero events every time."""
    conn = get_retail_conn()
    conn.execute("UPDATE sync_cursor SET last_seq=0 WHERE id=1")
    conn.commit()
    conn.close()


# ── The relay double, device-identity-aware -- verbatim from retail_
#    changed_field_delta_test.py; see that file's module docstring ─────────

class _RelayHub:
    """Shared server-side state. `pull()` filters out events pushed by the
    SAME device asking to pull, matching owner/app/sync/routes.py's real
    pull() route (`SyncEvent.device_id != installation.id`)."""

    def __init__(self):
        self.events = []
        self._next_seq = 1

    def push(self, device_id, events):
        for ev in events:
            self.events.append(dict(ev, seq=self._next_seq, _device_id=device_id))
            self._next_seq += 1
        return {"stored": len(events), "received": len(events)}

    def pull(self, device_id, since):
        pending = [ev for ev in self.events if ev["seq"] > since and ev["_device_id"] != device_id]
        cursor = pending[-1]["seq"] if pending else since
        return {
            "events": [{k: v for k, v in ev.items() if k != "_device_id"} for ev in pending],
            "cursor": cursor,
        }


class _DeviceRelayClient:
    """Binds one `_RelayHub` to one device identity -- matches
    SyncRelayClient.push/pull (relay_client.py:127-137)."""

    def __init__(self, hub, device_id):
        self._hub = hub
        self._device_id = device_id

    def push(self, events):
        return self._hub.push(self._device_id, events)

    def pull(self, since):
        return self._hub.pull(self._device_id, since)


@pytest.fixture
def hub():
    return _RelayHub()


@pytest.fixture
def relay_a(hub):
    return _DeviceRelayClient(hub, "device-a")


@pytest.fixture
def relay_b(hub):
    return _DeviceRelayClient(hub, "device-b")


@pytest.fixture
def service_a(relay_a, client):
    return SyncService(client_factory=lambda: relay_a, get_conn=get_retail_conn,
                        local_company_id_provider=lambda: client.company_id)


# ── Install B: a bare temp SQLite DB, no second Flask app ──────────────────

@pytest.fixture
def install_b(tmp_path):
    """Identical in shape to retail_changed_field_delta_test.py's own
    `install_b` fixture -- see that file's module docstring for the full
    "AURA_APP_DATA trap" reasoning this sidesteps."""
    b_root = tmp_path / "install_b"
    b_subsys = b_root / "subsystems"
    b_subsys.mkdir(parents=True, exist_ok=True)

    original_base_dir, original_subsys_dir = schema.BASE_DIR, schema.SUBSYS_DIR
    schema.BASE_DIR = str(b_root)
    schema.SUBSYS_DIR = str(b_subsys)
    try:
        schema.init_retail()
    finally:
        schema.BASE_DIR, schema.SUBSYS_DIR = original_base_dir, original_subsys_dir

    db_path = b_subsys / "retail.db"

    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    return _get_conn


# B's own local_company_id -- a fixed, non-numeric string (matching retail_
# changed_field_delta_test.py's identical choice), which doubles as a check
# that the cascade's `company_id=?` comparison works against an INTEGER-
# affinity column the same way the rest of this codebase's cross-device
# writes already do.
_INSTALL_B_COMPANY_ID = "install-b-company"


@pytest.fixture
def service_b(relay_b, install_b):
    return SyncService(
        client_factory=lambda: relay_b, get_conn=install_b,
        local_company_id_provider=lambda: _INSTALL_B_COMPANY_ID,
    )


def _conflict_count(conn, entity_type, entity_id):
    return conn.execute(
        "SELECT COUNT(*) c FROM sync_conflicts WHERE entity_type=? AND entity_id=?",
        (entity_type, entity_id),
    ).fetchone()["c"]


def _sync_event_count(conn, entity_type, entity_id):
    return conn.execute(
        "SELECT COUNT(*) c FROM sync_outbox WHERE entity_type=? AND entity_id=?",
        (entity_type, entity_id),
    ).fetchone()["c"]


# ── CSV import helpers -- matching retail_v17_row_version_bump_test.py's
#    identical helpers, needed only by the Decision B resurrection test ────

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


# ── 1. THE decisive test: a stale category delete must not remove a
#      category that was renamed, on the version, on another device ───────

def test_a_stale_category_delete_does_not_remove_a_category_renamed_on_another_device(
        client, a_conn, service_a, service_b, install_b, relay_b):
    # 1. Devices A and B both hold category C: name='Groceries',
    #    row_version=1.
    create = client.post('/api/sub/retail/categories', json={'name': 'Groceries'})
    assert create.status_code == 200
    cat_id = create.get_json()['data']['id']
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    seed = dict(b_conn.execute(
        "SELECT name, row_version, deleted_at_utc FROM categories WHERE id=?", (cat_id,)).fetchone())
    b_conn.close()
    assert seed == {'name': 'Groceries', 'row_version': 1, 'deleted_at_utc': None}

    # 2. B renames it -> B row_version=2, B emits (reproducing exactly what
    #    update_category's route does on install A: a local UPDATE bumping
    #    row_version in the SAME statement, then a `_changed_fields`-carrying
    #    outbox event).
    b_now = "2026-08-27T00:00:10+00:00"
    b_conn = install_b()
    b_conn.execute(
        "UPDATE categories SET name=?, row_version=row_version+1, updated_at_utc=? WHERE id=?",
        ('Groceries Renamed', b_now, cat_id))
    b_conn.commit()
    b_conn.close()
    relay_b.push([{
        "id": str(uuid.uuid4()), "entity_type": "category", "entity_id": cat_id, "event_type": "update",
        "payload": {
            'id': cat_id, 'name': 'Groceries Renamed', 'description': '',
            'row_version': 2, 'updated_at_utc': b_now, '_changed_fields': ['name'],
        },
        "created_at": b_now,
    }])

    # 3. A, having NOT seen B's rename, deletes the SAME category -- A's own
    #    row is still at row_version=1, so A's delete bumps it to 2, exactly
    #    like B's concurrent rename did. Neither device has seen the other's
    #    write yet: two genuinely concurrent edits racing to the same
    #    row_version, the identical shape retail_changed_field_delta_
    #    test.py's own decisive test proves for an update-vs-update race,
    #    reproduced here for a delete-vs-update race.
    delete = client.delete(f'/api/sub/retail/categories/{cat_id}')
    assert delete.status_code == 200
    a_row = dict(a_conn.execute(
        "SELECT row_version, deleted_at_utc FROM categories WHERE id=?", (cat_id,)).fetchone())
    assert a_row['row_version'] == 2 and a_row['deleted_at_utc'] is not None

    # 4. A pushes its delete; B pulls it. B's local row_version is ALREADY 2
    #    (from its own rename) -- incoming row_version 2 is NOT strictly
    #    greater than 2, so stage 6a-ii's existing reject-stale gate
    #    discards it, exactly as it already does for update-vs-update.
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    b_after = dict(b_conn.execute(
        "SELECT name, row_version, deleted_at_utc FROM categories WHERE id=?", (cat_id,)).fetchone())
    b_conflicts = _conflict_count(b_conn, "category", cat_id)
    b_conn.close()
    assert b_after['name'] == 'Groceries Renamed', "B's rename must survive the stale delete"
    assert b_after['deleted_at_utc'] is None, "a discarded stale delete must not tombstone the row"
    assert b_after['row_version'] == 2
    assert b_conflicts == 1, "the discarded delete must be visible in sync_conflicts, not silently dropped"


# ── 2. The ALLOW half -- without this, a gate that rejects everything
#      would pass test 1 for the wrong reason ──────────────────────────────

def test_a_genuinely_newer_category_delete_does_remove_it(
        client, a_conn, service_a, service_b, install_b):
    create = client.post('/api/sub/retail/categories', json={'name': 'Genuinely Deleted'})
    assert create.status_code == 200
    cat_id = create.get_json()['data']['id']
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    seed_version = b_conn.execute(
        "SELECT row_version FROM categories WHERE id=?", (cat_id,)).fetchone()['row_version']
    b_conn.close()
    assert seed_version == 1

    delete = client.delete(f'/api/sub/retail/categories/{cat_id}')
    assert delete.status_code == 200

    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    b_after = dict(b_conn.execute(
        "SELECT row_version, deleted_at_utc FROM categories WHERE id=?", (cat_id,)).fetchone())
    b_conflicts = _conflict_count(b_conn, "category", cat_id)
    b_conn.close()
    assert b_after['row_version'] == 2
    assert b_after['deleted_at_utc'] is not None, "a genuinely newer delete must actually tombstone the row"
    assert b_conflicts == 0, "an applied delete must not also record a conflict"


# ── 3. The cascade, asserted on the RECEIVING device (not just locally) ────

def test_deleting_a_category_clears_it_from_its_products_on_both_devices(
        client, a_conn, service_a, service_b, install_b):
    create = client.post('/api/sub/retail/categories', json={'name': 'Electronics'})
    assert create.status_code == 200
    cat_id = create.get_json()['data']['id']
    service_a.push_once()
    service_b.pull_once()

    # A's own product, filed under the category through the real route.
    a_product = client.post('/api/sub/retail/products', json={
        'name': 'A Widget', 'sku': f'TOMB-A-{uuid.uuid4().hex[:8]}', 'category_id': cat_id,
    })
    assert a_product.status_code == 200
    a_pid = a_product.get_json()['data']['id']

    # B independently holds a DIFFERENT product filed under the SAME
    # category id. Products are NOT synced (this phase) -- see retail_
    # category_delete_fk_sync_test.py's module docstring -- so this is not a
    # contrivance, it is the exact scenario schema v3's ON DELETE SET NULL
    # (and this cascade, its replacement) exists for: two devices legitimately
    # holding different product -> category assignments.
    b_conn = install_b()
    b_pid = str(uuid.uuid4())
    b_conn.execute(
        "INSERT INTO products (id, company_id, sku, barcode, name, category_id, cost_price, sell_price) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (b_pid, _INSTALL_B_COMPANY_ID, f'TOMB-B-{uuid.uuid4().hex[:8]}', '', 'B Widget', cat_id, 1, 2))
    b_conn.commit()
    b_conn.close()

    delete = client.delete(f'/api/sub/retail/categories/{cat_id}')
    assert delete.status_code == 200

    # THE LOCAL half -- A's own product, cascaded by the local delete path
    # (retail_api.py's delete_category).
    a_row = a_conn.execute("SELECT category_id FROM products WHERE id=?", (a_pid,)).fetchone()
    assert a_row['category_id'] is None

    service_a.push_once()
    service_b.pull_once()

    # THE RECEIVING half -- B never ran delete_category locally; its product
    # is cleared entirely by the APPLY-side cascade in sync_service.py's
    # category branch.
    b_conn = install_b()
    b_row = b_conn.execute("SELECT category_id FROM products WHERE id=?", (b_pid,)).fetchone()
    b_cat_row = b_conn.execute("SELECT deleted_at_utc FROM categories WHERE id=?", (cat_id,)).fetchone()
    b_conn.close()
    assert b_row['category_id'] is None, "the cascade must run on the RECEIVING device too, not just locally"
    assert b_cat_row['deleted_at_utc'] is not None


# ── 4. The cascade must not advance the touched products' own row_version,
#      nor queue a product sync event -- the loyalty-accumulator trap,
#      reached by a third route ────────────────────────────────────────────

def test_the_cascade_does_not_bump_row_version_on_the_products_it_touches(client, a_conn):
    create = client.post('/api/sub/retail/categories', json={'name': 'Cascade RV Category'})
    assert create.status_code == 200
    cat_id = create.get_json()['data']['id']

    product = client.post('/api/sub/retail/products', json={
        'name': 'Cascade RV Widget', 'sku': f'TOMB-RV-{uuid.uuid4().hex[:8]}', 'category_id': cat_id,
    })
    assert product.status_code == 200
    pid = product.get_json()['data']['id']
    before = dict(a_conn.execute(
        "SELECT row_version, updated_at_utc FROM products WHERE id=?", (pid,)).fetchone())
    events_before = _sync_event_count(a_conn, 'product', pid)

    delete = client.delete(f'/api/sub/retail/categories/{cat_id}')
    assert delete.status_code == 200

    after = dict(a_conn.execute(
        "SELECT category_id, row_version, updated_at_utc FROM products WHERE id=?", (pid,)).fetchone())
    events_after = _sync_event_count(a_conn, 'product', pid)
    assert after['category_id'] is None, "the cascade itself must still have run"
    assert after['row_version'] == before['row_version'], \
        "the cascade must not bump the touched product's own row_version"
    assert after['updated_at_utc'] == before['updated_at_utc'], \
        "the cascade must not stamp the touched product's own updated_at_utc"
    assert events_after == events_before, "the cascade must not queue a product sync event"


# ── 5. The deliberate double-write, pinned so a future "free status from
#      deletion" cleanup has to face this test ─────────────────────────────

def test_a_deleted_product_stamps_deleted_at_utc_and_still_sets_status_inactive(client, a_conn):
    create = client.post('/api/sub/retail/products', json={
        'name': 'Double Write Widget', 'sku': f'TOMB-DW-{uuid.uuid4().hex[:8]}',
    })
    assert create.status_code == 200
    pid = create.get_json()['data']['id']

    resp = client.delete(f'/api/sub/retail/products/{pid}')
    assert resp.status_code == 200

    row = dict(a_conn.execute(
        "SELECT status, deleted_at_utc FROM products WHERE id=?", (pid,)).fetchone())
    # `status='inactive'` is NOT freed by this stage -- create_sale's
    # line-item read still filters `status='active'`, so a tombstoned
    # product that only carried `deleted_at_utc` would stay sellable until
    # every read path is proven to carry the new filter. See retail_api.py's
    # delete_product comment.
    assert row['status'] == 'inactive'
    assert row['deleted_at_utc'] is not None


# ── 6. The two read paths from step 4, isolated from the cascade ───────────

def test_a_tombstoned_category_is_not_listed_and_not_joined_to_products(client, a_conn):
    create = client.post('/api/sub/retail/categories', json={'name': 'Doomed Category'})
    assert create.status_code == 200
    cat_id = create.get_json()['data']['id']

    product = client.post('/api/sub/retail/products', json={
        'name': 'Dangling Widget', 'sku': f'TOMB-DANGLE-{uuid.uuid4().hex[:8]}',
    })
    assert product.status_code == 200
    pid = product.get_json()['data']['id']

    delete = client.delete(f'/api/sub/retail/categories/{cat_id}')
    assert delete.status_code == 200

    # Deliberately bypasses the cascade: this product had NO category at the
    # time "Doomed Category" was deleted, so the cascade never touched it.
    # Re-pointing it AT the now-tombstoned id afterwards reproduces the
    # "residual divergence" phase6b-decisions.md's "Decision A" names (a
    # product concurrently re-pointed at a doomed category on another
    # device) -- and isolates the JOIN filter (step 4) from the cascade
    # (step 2/3): a product whose category_id the cascade already nulled
    # would pass the assertion below trivially, proving nothing about the
    # filter itself.
    a_conn.execute("UPDATE products SET category_id=? WHERE id=?", (cat_id, pid))
    a_conn.commit()

    categories = client.get('/api/sub/retail/categories').get_json()['data']
    assert cat_id not in {c['id'] for c in categories}, "a tombstoned category must not be listed"

    products = client.get('/api/sub/retail/products').get_json()['data']
    dangling = next(p for p in products if p['id'] == pid)
    assert dangling['category_name'] is None, \
        "a dangling reference to a tombstoned category must render as no category, not the dead name"


# ── 7. Decision B: an import naming a tombstoned category resurrects it ────

def test_importing_a_row_naming_a_tombstoned_category_resurrects_it(client, a_conn):
    cat_name = f'Resurrect Me {uuid.uuid4().hex[:6]}'
    create = client.post('/api/sub/retail/categories', json={'name': cat_name})
    assert create.status_code == 200
    cat_id = create.get_json()['data']['id']

    delete = client.delete(f'/api/sub/retail/categories/{cat_id}')
    assert delete.status_code == 200
    before = dict(a_conn.execute(
        "SELECT deleted_at_utc, row_version FROM categories WHERE id=?", (cat_id,)).fetchone())
    assert before['deleted_at_utc'] is not None

    sku = f'TOMB-IMPORT-{uuid.uuid4().hex[:8]}'
    mapping = {"name": "Product Name", "sku": "SKU", "sell_price": "Selling Price", "category": "Category"}
    headers = ['Product Name', 'SKU', 'Selling Price', 'Category']
    result = _import(client, 'products', mapping,
                      [{'Product Name': 'Resurrected Widget', 'SKU': sku, 'Selling Price': '15', 'Category': cat_name}],
                      headers)
    assert result['imported'] == 1

    after = dict(a_conn.execute(
        "SELECT deleted_at_utc, row_version FROM categories WHERE id=?", (cat_id,)).fetchone())
    assert after['deleted_at_utc'] is None, "an import naming this category by name must resurrect it"
    assert after['row_version'] > before['row_version']

    landed_category_id = a_conn.execute(
        "SELECT category_id FROM products WHERE sku=?", (sku,)).fetchone()['category_id']
    assert landed_category_id == cat_id, \
        "the imported product must land under the RESURRECTED category, not a new duplicate"

    # The resurrection must itself be a real, gated sync event -- not a
    # silent local-only fix-up -- so another device can pick it up too.
    events = a_conn.execute(
        "SELECT event_type, payload FROM sync_outbox WHERE entity_type='category' AND entity_id=? "
        "ORDER BY created_at", (cat_id,)).fetchall()
    resurrection_payload = json.loads(events[-1]['payload'])
    assert events[-1]['event_type'] == 'update'
    assert resurrection_payload['_changed_fields'] == ['deleted_at_utc']
    assert resurrection_payload['deleted_at_utc'] is None


# ── 8. "Missing is not stale" for a delete, the shape 6a-ii shipped a
#      defect on for row_version -- proven here for deleted_at_utc too ────

def test_a_legacy_category_delete_payload_with_no_row_version_still_applies(
        client, a_conn, service_a, service_b, install_b, relay_b):
    create = client.post('/api/sub/retail/categories', json={'name': 'Legacy Delete Target'})
    assert create.status_code == 200
    cat_id = create.get_json()['data']['id']
    service_a.push_once()
    service_b.pull_once()

    # The OLDEST legacy shape -- delete_category used to queue only
    # `{'id': category_id}` before this stage (no row_version, no
    # deleted_at_utc, no updated_at_utc at all; stage 6a-ii's own report
    # flagged this as the one ungated destructive catalogue path). "Missing
    # is not stale" must still hold for a delete exactly as it already does
    # for an update: a device running old code, or an event already queued
    # before this stage shipped, must still actually tombstone the category
    # on every OTHER device, not silently no-op while reporting success.
    relay_b.push([{
        "id": str(uuid.uuid4()), "entity_type": "category", "entity_id": cat_id, "event_type": "delete",
        "payload": {"id": cat_id},
        "created_at": "2026-08-27T00:00:20+00:00",
    }])
    service_a.pull_once()

    row = dict(a_conn.execute(
        "SELECT deleted_at_utc FROM categories WHERE id=?", (cat_id,)).fetchone())
    assert row['deleted_at_utc'] is not None, \
        "a legacy delete payload with no timestamp at all must still tombstone, not silently no-op"

    categories = client.get('/api/sub/retail/categories').get_json()['data']
    assert cat_id not in {c['id'] for c in categories}


# ── 9. Double-delete is a clean no-op 404, not a re-stamp -- this is what
#      `AND deleted_at_utc IS NULL` in delete_category's own WHERE exists
#      for; added beyond the 8 required tests specifically so stage 7's
#      first mutation proof (removing that guard) has a real target ───────

def test_deleting_an_already_tombstoned_category_is_a_404_not_a_restamp(client, a_conn):
    create = client.post('/api/sub/retail/categories', json={'name': 'Delete Me Twice'})
    assert create.status_code == 200
    cat_id = create.get_json()['data']['id']

    first = client.delete(f'/api/sub/retail/categories/{cat_id}')
    assert first.status_code == 200
    first_row = dict(a_conn.execute(
        "SELECT deleted_at_utc, row_version FROM categories WHERE id=?", (cat_id,)).fetchone())
    assert first_row['deleted_at_utc'] is not None

    second = client.delete(f'/api/sub/retail/categories/{cat_id}')
    assert second.status_code == 404, "deleting an already-tombstoned category must be a clean 404, not a second success"

    second_row = dict(a_conn.execute(
        "SELECT deleted_at_utc, row_version FROM categories WHERE id=?", (cat_id,)).fetchone())
    assert second_row == first_row, \
        "a repeat delete must not re-stamp deleted_at_utc/row_version over the original tombstone"


# ── 10. Phase 6 continuation (2026-08-27): `deleted_at_utc` joined the
#      category upsert's DELTA-GATED column list, not written unconditionally
#      -- the DENY half. Unconditional would let a device that never saw a
#      tombstone resurrect it off the back of an edit that never touched
#      deletion at all: A renames the category and re-sends its own stale
#      `deleted_at_utc` of NULL alongside the name, exactly the untouched-
#      field clobber stage 6b-i closed, pointed at the one column where it
#      un-deletes something ───────────────────────────────────────────────

def test_a_name_edit_from_a_device_that_never_saw_the_delete_does_not_resurrect_the_category(
        client, service_a, service_b, install_b):
    # 1. Devices A and B both hold category C: row_version=1, deleted_at_utc=None.
    create = client.post('/api/sub/retail/categories', json={'name': 'Coffee'})
    assert create.status_code == 200
    cat_id = create.get_json()['data']['id']
    service_a.push_once()
    service_b.pull_once()

    # 2. B tombstones it LOCALLY -- raw SQL, matching delete_category's own
    #    UPDATE exactly (deleted_at_utc stamped, row_version bumped in the
    #    SAME statement). B never pushes this anywhere: A must never learn
    #    about it, which is the whole point of this test.
    b_now = "2026-08-27T00:00:40+00:00"
    b_conn = install_b()
    b_conn.execute(
        "UPDATE categories SET deleted_at_utc=?, row_version=row_version+1, updated_at_utc=? WHERE id=?",
        (b_now, b_now, cat_id))
    b_conn.commit()
    b_seed = dict(b_conn.execute(
        "SELECT row_version, deleted_at_utc FROM categories WHERE id=?", (cat_id,)).fetchone())
    b_conn.close()
    assert b_seed == {'row_version': 2, 'deleted_at_utc': b_now}

    # 3. A, oblivious to B's delete (never pulls in between), renames the
    #    category through the REAL route TWICE. One PUT would only reach
    #    row_version=2 -- a TIE with B's tombstone, which 6a-ii's own
    #    reject-stale gate would already discard for a reason that has
    #    nothing to do with THIS stage's delta gating, proving nothing about
    #    it. Two PUTs land A on row_version=3, genuinely higher than B's 2,
    #    so the reject-stale gate lets the write through and only the delta
    #    gating on `deleted_at_utc` can still save the tombstone. Each PUT
    #    sends only `name`, so `_changed_fields` is `['name']` on the event
    #    that matters -- and update_category (retail_api.py) never puts
    #    `deleted_at_utc` in its wire payload at all, so `p.get(
    #    'deleted_at_utc')` reads None on the wire exactly as if the field
    #    had been sent explicitly as None, which is the attack this test
    #    pins.
    r1 = client.put(f'/api/sub/retail/categories/{cat_id}', json={'name': 'Coffee (Renamed Once)'})
    assert r1.status_code == 200
    r2 = client.put(f'/api/sub/retail/categories/{cat_id}', json={'name': 'Coffee Renamed By A'})
    assert r2.status_code == 200

    # 4. A pushes both queued events; B pulls. The first (row_version=2) is
    #    a tie against B's local 2 and is discarded as stale (harmless,
    #    unrelated to this test's assertions); the second (row_version=3) is
    #    genuinely newer and applies.
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    b_after = dict(b_conn.execute(
        "SELECT name, row_version, deleted_at_utc FROM categories WHERE id=?", (cat_id,)).fetchone())
    b_conn.close()
    assert b_after['name'] == 'Coffee Renamed By A', "A's rename must land, not be silently dropped"
    assert b_after['row_version'] == 3
    assert b_after['deleted_at_utc'] is not None, \
        "a name-only edit from a device that never saw the delete must not resurrect B's tombstone"


# ── 11. The mirror ALLOW half -- without this, a gate that denies
#      `deleted_at_utc` unconditionally (rather than delta-gating it) would
#      pass test 10 for the wrong reason. `test_importing_a_row_naming_a_
#      tombstoned_category_resurrects_it` above already proves the LOCAL
#      half of Decision B (the importing device's own row); this proves the
#      resurrection genuinely reaches the OTHER device too, which needs the
#      apply-side column list this whole stage added ────────────────────────

def test_an_import_resurrection_of_a_tombstoned_category_reaches_the_other_device(
        client, a_conn, service_a, service_b, install_b, relay_b):
    cat_name = f'Cross-Device Resurrect {uuid.uuid4().hex[:6]}'
    create = client.post('/api/sub/retail/categories', json={'name': cat_name})
    assert create.status_code == 200
    cat_id = create.get_json()['data']['id']
    service_a.push_once()
    service_b.pull_once()

    # B tombstones it and PUSHES the delete this time -- unlike test 10, A
    # here MUST learn about the tombstone first: import_api.py's resurrect
    # path (~line 1335) only fires when A's OWN local row already reads
    # `deleted_at_utc IS NOT NULL`. Payload shape matches delete_category's
    # own `_queue_sync_event` call exactly (retail_api.py).
    b_now = "2026-08-27T00:00:50+00:00"
    b_conn = install_b()
    b_conn.execute(
        "UPDATE categories SET deleted_at_utc=?, row_version=row_version+1, updated_at_utc=? WHERE id=?",
        (b_now, b_now, cat_id))
    b_conn.commit()
    b_conn.close()
    relay_b.push([{
        "id": str(uuid.uuid4()), "entity_type": "category", "entity_id": cat_id, "event_type": "delete",
        "payload": {
            'id': cat_id, 'deleted_at_utc': b_now, 'row_version': 2,
            'updated_at_utc': b_now, '_changed_fields': ['deleted_at_utc'],
        },
        "created_at": b_now,
    }])
    service_a.pull_once()

    a_seed = dict(a_conn.execute(
        "SELECT row_version, deleted_at_utc FROM categories WHERE id=?", (cat_id,)).fetchone())
    assert a_seed['deleted_at_utc'] is not None, "A must see B's tombstone before the import can resurrect it"
    assert a_seed['row_version'] == 2

    # A imports a CSV row naming the tombstoned category by name -- the real
    # /api/import/execute endpoint, exercising import_api.py's actual
    # resurrect branch verbatim (same shape `test_importing_a_row_naming_a_
    # tombstoned_category_resurrects_it` above already proves the LOCAL half
    # of; this test's new ground is the cross-device half that helper never
    # touches).
    sku = f'TOMB-CROSS-{uuid.uuid4().hex[:8]}'
    mapping = {"name": "Product Name", "sku": "SKU", "sell_price": "Selling Price", "category": "Category"}
    headers = ['Product Name', 'SKU', 'Selling Price', 'Category']
    result = _import(client, 'products', mapping,
                      [{'Product Name': 'Cross Resurrect Widget', 'SKU': sku, 'Selling Price': '9',
                        'Category': cat_name}],
                      headers)
    assert result['imported'] == 1

    a_after = dict(a_conn.execute(
        "SELECT deleted_at_utc, row_version FROM categories WHERE id=?", (cat_id,)).fetchone())
    assert a_after['deleted_at_utc'] is None, "the import must resurrect A's own local row first"
    assert a_after['row_version'] == 3

    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    b_after = dict(b_conn.execute(
        "SELECT deleted_at_utc, row_version FROM categories WHERE id=?", (cat_id,)).fetchone())
    b_conn.close()
    assert b_after['deleted_at_utc'] is None, "the import resurrection must travel to the OTHER device too"
    assert b_after['row_version'] == 3
