"""Aura Retail -- multi-device sync: changed-field deltas (launch-readiness
Phase 6 stage 6b-i).

Root cause this file exists to close: a catalogue `update` sync event has
always carried a FULL-ROW snapshot, and the apply path (sync_service.py's
`_apply_event`) has always written every column that snapshot carries. So a
device that edits only one field (say a product's `name`) also re-sends
every OTHER field at its own possibly-stale value, and on apply those stale
values silently overwrite fields some OTHER device changed in the meantime
-- even though the two edits never touched the same column. Stage 6a-ii's
reject-stale gate (row_version) narrows this but does not close it: it
decides WHETHER a row applies, not how MUCH of it lands when it does.

The fix (see sync_service.py's `_delta_set_clause` and retail_api.py's five
`update` emission sites): the payload keeps the full-row snapshot (the
UPSERT's INSERT half still needs it for a device that has never seen the
id), but gains an explicit `_changed_fields` list naming only the columns
THIS write actually changed. The apply side's DO UPDATE half writes only
those columns; every other column keeps whatever the RECEIVING device
already had.

This file follows the exact two-device composition pattern
retail_two_install_roundtrip_test.py established (see that file's own
module docstring for the full "AURA_APP_DATA trap" / "Postgres trap"
reasoning, reproduced here without repeating it verbatim) -- own temp
app-data dir, its own license seed, its own Flask app boot (install A), a
bare second SQLite database built directly from schema.init_retail()
(install B, no second Flask app). Extended here with a device-identity-aware
relay double (`_RelayHub`/`_DeviceRelayClient` below): the decisive test
needs BOTH sides to push a concurrent, as-yet-unseen edit and then have the
OTHER device pull it -- something retail_two_install_roundtrip_test.py's own
tests never need (they are always one-directional, A push -> B pull). A
device must never receive back an event it pushed itself; the shared
InMemoryRelay in that file has no need to enforce that (none of its tests
would ever notice), but owner/app/sync/routes.py's REAL pull() route does
exactly this filtering (`.where(SyncEvent.device_id != installation.id)`,
line ~609) -- `_RelayHub` reproduces it so this file's concurrent-edit
scenario matches production, not an artifact of a looser test double.

Run:
    pytest products/retail/tests/retail_changed_field_delta_test.py -v
"""
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_changedfielddelta_"))
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
    created company -- one company per test. `company_id` is stashed on the
    returned client (not part of the base pattern in
    retail_two_install_roundtrip_test.py) because THIS file's decisive test
    needs A's `service_a` to genuinely PULL, which requires a real
    `local_company_id_provider` pointed at install A's own company_id."""
    email = f'cfd-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'ChangedFieldDeltaPW1'
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
    """Install A's own retail.db is ONE module-level database shared by
    every test in this file (see the module docstring's AURA_APP_DATA trap,
    mirrored from retail_two_install_roundtrip_test.py) -- each test gets
    its own fresh COMPANY via the `client` fixture, but `sync_cursor`
    (schema.py's CREATE TABLE) is a single GLOBAL row (id=1), never scoped
    per company: in production exactly one physical device has exactly one
    "how far have I synced with the relay" counter, no matter which company
    it currently operates. Every test also builds a brand-new,
    function-scoped `hub` whose seq numbering restarts at 1. Without this
    reset, a test running AFTER an earlier one that already advanced A's
    real, persisted cursor would find A already "ahead of" its own fresh
    hub's seq numbers, and `pull_once()` would silently return zero events
    every time -- no exception, just nothing applied. Found exactly this
    way: every test in this file passed running alone, but the legacy- and
    bogus-column tests failed when the decisive test (which advances A's
    cursor via three separate pulls) ran before them in the same session."""
    conn = get_retail_conn()
    conn.execute("UPDATE sync_cursor SET last_seq=0 WHERE id=1")
    conn.commit()
    conn.close()


# ── The relay double, device-identity-aware (see module docstring) ─────────

class _RelayHub:
    """Shared server-side state. `pull()` filters out events pushed by the
    SAME device asking to pull, matching owner/app/sync/routes.py's real
    pull() route (`SyncEvent.device_id != installation.id`) -- without this,
    a device that pushes its own concurrent edit before either side has
    synced would eventually pull that SAME event back and re-apply it to
    itself, which the reject-stale gate (incoming row_version not strictly
    greater than a row_version this device itself just wrote) would flag as
    a spurious self-conflict, contaminating this file's conflict-count
    assertions with noise the real system never produces. Cursor is bounded
    to the FILTERED (own-device-excluded) pending list, exactly like the
    real route's `rows[-1].seq if rows else <since>` -- an own-device event
    beyond `since` does not, by itself, advance that device's own cursor."""

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
    """Binds one `_RelayHub` to one device identity -- the shape
    SyncService's own `client_factory()` result is ever called with
    (`.push(events) -> dict`, `.pull(since) -> {"events": [...], "cursor":
    N}`), matching SyncRelayClient.push/pull (relay_client.py:127-137)."""

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
    """local_company_id_provider is wired here (unlike
    retail_two_install_roundtrip_test.py's push-only service_a) because this
    file's decisive tests need A to genuinely pull B's concurrent edit, not
    just push its own."""
    return SyncService(client_factory=lambda: relay_a, get_conn=get_retail_conn,
                        local_company_id_provider=lambda: client.company_id)


# ── Install B: a bare temp SQLite DB, no second Flask app ──────────────────

@pytest.fixture
def install_b(tmp_path):
    """Identical in shape to retail_two_install_roundtrip_test.py's own
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


@pytest.fixture
def service_b(relay_b, install_b):
    return SyncService(
        client_factory=lambda: relay_b, get_conn=install_b,
        local_company_id_provider=lambda: "install-b-company",
    )


def _conflict_count(conn, entity_type, entity_id):
    return conn.execute(
        "SELECT COUNT(*) c FROM sync_conflicts WHERE entity_type=? AND entity_id=?",
        (entity_type, entity_id),
    ).fetchone()["c"]


# ── 1. THE decisive test: an edit on one device must not revert a field ────
#      the OTHER device edited, even when the write that lands is genuinely
#      newer (row_version-wise) than both.

def test_an_edit_on_one_device_does_not_revert_a_field_the_other_device_edited(
        client, a_conn, service_a, service_b, install_b, relay_b):
    # 1. Devices A and B both hold customer C: name='Acme', phone='111',
    #    address='Old St', row_version = 1.
    create = client.post('/api/sub/retail/customers', json={
        'name': 'Acme', 'phone': '111', 'address': 'Old St',
    })
    assert create.status_code == 200
    cust_id = create.get_json()['data']['id']
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    seed = dict(b_conn.execute(
        "SELECT name, phone, address, row_version FROM customers WHERE id=?", (cust_id,)).fetchone())
    b_conn.close()
    assert seed == {'name': 'Acme', 'phone': '111', 'address': 'Old St', 'row_version': 1}

    # 2. B edits `address` to 'New Road' -> B row_version = 2, B emits.
    #    Install B has no Flask app of its own -- this reproduces exactly
    #    what update_customer's route does on install A (a local UPDATE
    #    bumping row_version in the SAME statement, then a `_changed_fields`
    #    -carrying outbox event), pushed through B's own per-device relay
    #    client so the hub's self-echo filter tags it correctly.
    b_now_1 = "2026-08-27T00:00:01+00:00"
    b_conn = install_b()
    b_conn.execute(
        "UPDATE customers SET address=?, row_version=row_version+1, updated_at_utc=? WHERE id=?",
        ('New Road', b_now_1, cust_id))
    b_conn.commit()
    b_snapshot = dict(b_conn.execute(
        "SELECT name, phone, email, address, status FROM customers WHERE id=?", (cust_id,)).fetchone())
    b_conn.close()
    relay_b.push([{
        "id": str(uuid.uuid4()), "entity_type": "customer", "entity_id": cust_id, "event_type": "update",
        "payload": dict(b_snapshot, id=cust_id, row_version=2, updated_at_utc=b_now_1,
                        _changed_fields=['address']),
        "created_at": b_now_1,
    }])

    # 3. A edits `phone` to '222' -> A row_version = 2, A emits. Neither has
    #    seen the other yet. Each rejects the other's event as stale
    #    (row_version 2 is not > 2) and records a conflict. That is stage
    #    6a-ii's EXISTING, CORRECT behaviour -- asserted here, unchanged.
    update_a1 = client.patch(f'/api/sub/retail/customers/{cust_id}', json={'phone': '222'})
    assert update_a1.status_code == 200
    service_a.push_once()
    service_b.pull_once()
    service_a.pull_once()

    b_conn = install_b()
    b_after_reject = dict(b_conn.execute(
        "SELECT phone, address, row_version FROM customers WHERE id=?", (cust_id,)).fetchone())
    b_conflicts_after_reject = _conflict_count(b_conn, "customer", cust_id)
    b_conn.close()
    assert b_after_reject == {'phone': '111', 'address': 'New Road', 'row_version': 2}
    assert b_conflicts_after_reject == 1

    a_after_reject = dict(a_conn.execute(
        "SELECT phone, address, row_version FROM customers WHERE id=?", (cust_id,)).fetchone())
    a_conflicts_after_reject = _conflict_count(a_conn, "customer", cust_id)
    assert a_after_reject == {'phone': '222', 'address': 'Old St', 'row_version': 2}
    assert a_conflicts_after_reject == 1

    # 4. A now edits `phone` again to '333' -> A row_version = 3, A emits a
    #    full snapshot carrying A's stale copy of address ('Old St').
    update_a2 = client.patch(f'/api/sub/retail/customers/{cust_id}', json={'phone': '333'})
    assert update_a2.status_code == 200
    a_row_v3 = dict(a_conn.execute(
        "SELECT phone, address, row_version FROM customers WHERE id=?", (cust_id,)).fetchone())
    assert a_row_v3 == {'phone': '333', 'address': 'Old St', 'row_version': 3}

    # 5. B applies it (3 > 2, legitimately newer).
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    b_final = dict(b_conn.execute(
        "SELECT phone, address, row_version FROM customers WHERE id=?", (cust_id,)).fetchone())
    b_conn.close()
    # Before stage 6b-i, this next line's address assertion fails with
    # 'Old St' -- A's second write's DO UPDATE used to apply EVERY column
    # from the payload, including A's own stale `address`, clobbering B's
    # untouched edit. That is the bug this stage exists to fix.
    assert b_final['phone'] == '333'          # the real edit landed
    assert b_final['address'] == 'New Road'   # B's untouched field SURVIVED
    assert b_final['row_version'] == 3


# ── 2. A legacy payload with no `_changed_fields` key applies every field ──

def test_a_legacy_update_payload_with_no_changed_fields_key_still_applies_every_field(
        client, a_conn, service_a, service_b, install_b, relay_b):
    create = client.post('/api/sub/retail/customers', json={
        'name': 'Legacy Co', 'phone': '000', 'address': 'First St',
    })
    assert create.status_code == 200
    cust_id = create.get_json()['data']['id']
    service_a.push_once()
    service_b.pull_once()

    # Hand-built "legacy" update event -- no `_changed_fields` key at all,
    # simulating an event queued by a pre-6b-i device (or one already
    # sitting in an outbox/relay when this stage ships). By design (see
    # sync_service.py's `_delta_set_clause` docstring) this must be treated
    # as "every column changed" -- byte-for-byte the pre-6b-i behaviour.
    legacy_now = "2026-08-27T00:00:02+00:00"
    relay_b.push([{
        "id": str(uuid.uuid4()), "entity_type": "customer", "entity_id": cust_id, "event_type": "update",
        "payload": {
            'id': cust_id, 'name': 'Legacy Co Renamed', 'phone': '999', 'email': 'legacy@test.local',
            'address': 'Second St', 'status': 'active', 'row_version': 2, 'updated_at_utc': legacy_now,
            # deliberately no '_changed_fields' key
        },
        "created_at": legacy_now,
    }])
    service_a.pull_once()

    a_row = dict(a_conn.execute(
        "SELECT name, phone, email, address, status, row_version FROM customers WHERE id=?", (cust_id,)).fetchone())
    assert a_row == {
        'name': 'Legacy Co Renamed', 'phone': '999', 'email': 'legacy@test.local',
        'address': 'Second St', 'status': 'active', 'row_version': 2,
    }


# ── 3. A create event still lands every field on a device with no prior row

def test_a_create_event_still_lands_every_field_on_a_device_that_has_never_seen_the_row(
        client, a_conn, service_a, service_b, install_b):
    # This is the test that would have caught the true-delta design being
    # wrong (see retail_changed_field_delta_test.py's -- this file's --
    # module docstring / the launch-readiness plan this stage follows): a
    # true "send only changed fields" delta would INSERT a product with a
    # NULL sku and no price on a device that has never seen this id. `create`
    # emission sites are deliberately UNTOUCHED by stage 6b-i -- there is
    # nothing to diff a create against -- so this proves the INSERT half of
    # the upsert is unaffected by the delta machinery entirely.
    supplier = client.post('/api/sub/retail/suppliers', json={'name': 'Create-Half Supplier'}).get_json()['data']['id']
    category = client.post('/api/sub/retail/categories', json={'name': 'Create-Half Category'}).get_json()['data']['id']
    service_a.push_once()
    service_b.pull_once()

    create = client.post('/api/sub/retail/products', json={
        'name': 'Create-Half Widget', 'sku': 'CFD-CREATE-1', 'barcode': 'BAR-CFD-1',
        'sell_price': 12.5, 'cost_price': 6, 'tax_rate': 7, 'unit': 'pcs', 'reorder_level': 4,
        'category_id': category, 'supplier_id': supplier,
    })
    assert create.status_code == 200
    pid = create.get_json()['data']['id']
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    b_row = b_conn.execute(
        "SELECT sku, barcode, name, category_id, supplier_id, cost_price, sell_price, tax_rate, unit, "
        "reorder_level, status, row_version FROM products WHERE id=?", (pid,)).fetchone()
    b_conn.close()
    assert b_row is not None, "the create event's INSERT half never landed on a device that had never seen this id"
    assert dict(b_row) == {
        'sku': 'CFD-CREATE-1', 'barcode': 'BAR-CFD-1', 'name': 'Create-Half Widget',
        'category_id': category, 'supplier_id': supplier, 'cost_price': 6, 'sell_price': 12.5,
        'tax_rate': 7, 'unit': 'pcs', 'reorder_level': 4, 'status': 'active', 'row_version': 1,
    }


# ── 4. Product-side equivalent of the decisive test (far more columns) ─────

def test_a_product_edit_on_one_device_does_not_revert_a_field_the_other_device_edited(
        client, a_conn, service_a, service_b, install_b, relay_b):
    supplier = client.post('/api/sub/retail/suppliers', json={'name': 'CFD Product Supplier'}).get_json()['data']['id']
    category = client.post('/api/sub/retail/categories', json={'name': 'CFD Product Category'}).get_json()['data']['id']
    service_a.push_once()
    service_b.pull_once()

    create = client.post('/api/sub/retail/products', json={
        'name': 'CFD Widget', 'sku': 'CFD-DECISIVE-1', 'barcode': 'BAR-CFD-DEC',
        'sell_price': 10, 'cost_price': 5, 'tax_rate': 0, 'unit': 'pcs', 'reorder_level': 5,
        'category_id': category, 'supplier_id': supplier,
    })
    assert create.status_code == 200
    pid = create.get_json()['data']['id']
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    seed = dict(b_conn.execute(
        "SELECT unit, cost_price, row_version FROM products WHERE id=?", (pid,)).fetchone())
    b_conn.close()
    assert seed == {'unit': 'pcs', 'cost_price': 5, 'row_version': 1}

    # 2. B edits `unit` to 'box' locally -> B row_version = 2, B emits.
    b_now_1 = "2026-08-27T00:00:03+00:00"
    b_conn = install_b()
    b_conn.execute(
        "UPDATE products SET unit=?, row_version=row_version+1, updated_at_utc=? WHERE id=?",
        ('box', b_now_1, pid))
    b_conn.commit()
    b_snapshot = dict(b_conn.execute(
        "SELECT sku, barcode, name, category_id, supplier_id, cost_price, sell_price, tax_rate, unit, "
        "reorder_level, reorder_method, status FROM products WHERE id=?", (pid,)).fetchone())
    b_conn.close()
    relay_b.push([{
        "id": str(uuid.uuid4()), "entity_type": "product", "entity_id": pid, "event_type": "update",
        "payload": dict(b_snapshot, id=pid, row_version=2, updated_at_utc=b_now_1, _changed_fields=['unit']),
        "created_at": b_now_1,
    }])

    # 3. A edits `cost_price` to 8 -> A row_version = 2. Neither has seen
    #    the other yet -- each rejects the other's event as stale
    #    (row_version 2 is not > 2). Stage 6a-ii's existing behaviour,
    #    unchanged.
    update_a1 = client.patch(f'/api/sub/retail/products/{pid}', json={'cost_price': 8})
    assert update_a1.status_code == 200
    service_a.push_once()
    service_b.pull_once()
    service_a.pull_once()

    b_conn = install_b()
    b_after_reject = dict(b_conn.execute(
        "SELECT cost_price, unit, row_version FROM products WHERE id=?", (pid,)).fetchone())
    b_conn.close()
    assert b_after_reject == {'cost_price': 5, 'unit': 'box', 'row_version': 2}

    a_after_reject = dict(a_conn.execute(
        "SELECT cost_price, unit, row_version FROM products WHERE id=?", (pid,)).fetchone())
    assert a_after_reject == {'cost_price': 8, 'unit': 'pcs', 'row_version': 2}

    # 4. A edits `cost_price` again to 9 -> A row_version = 3, emitting a
    #    full snapshot carrying A's stale copy of `unit` ('pcs').
    update_a2 = client.patch(f'/api/sub/retail/products/{pid}', json={'cost_price': 9})
    assert update_a2.status_code == 200

    # 5. B applies it (3 > 2, legitimately newer).
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    b_final = dict(b_conn.execute(
        "SELECT cost_price, unit, row_version FROM products WHERE id=?", (pid,)).fetchone())
    b_conn.close()
    assert b_final['cost_price'] == 9      # the real edit landed
    assert b_final['unit'] == 'box'        # B's untouched field SURVIVED
    assert b_final['row_version'] == 3


# ── 5. An unknown/bogus column name in `_changed_fields` is ignored ────────

def test_changed_fields_naming_an_unknown_column_is_ignored_not_injected(
        client, a_conn, service_a, service_b, install_b, relay_b):
    create = client.post('/api/sub/retail/categories', json={
        'name': 'Bogus Field Category', 'description': 'Original desc',
    })
    assert create.status_code == 200
    cat_id = create.get_json()['data']['id']
    service_a.push_once()
    service_b.pull_once()

    bogus_now = "2026-08-27T00:00:04+00:00"
    relay_b.push([{
        "id": str(uuid.uuid4()), "entity_type": "category", "entity_id": cat_id, "event_type": "update",
        "payload": {
            'id': cat_id, 'name': 'Bogus Field Category Renamed', 'description': 'Original desc',
            'row_version': 2, 'updated_at_utc': bogus_now,
            # `_delta_set_clause`'s `columns` argument is code-owned
            # (["name", "description"] for categories); this wire value is
            # only ever membership-tested against it, never interpolated,
            # so a column name that does not exist anywhere in the schema
            # must be silently ignored, never reach the SQL string.
            '_changed_fields': ['name', 'drop_table_categories_or_whatever'],
        },
        "created_at": bogus_now,
    }])
    # Must not raise -- if the bogus name ever reached the SQL string this
    # would be an sqlite3.OperationalError (no such column), not a quiet
    # no-op for that one name.
    service_a.pull_once()

    a_row = dict(a_conn.execute(
        "SELECT name, description, row_version FROM categories WHERE id=?", (cat_id,)).fetchone())
    assert a_row == {'name': 'Bogus Field Category Renamed', 'description': 'Original desc', 'row_version': 2}
