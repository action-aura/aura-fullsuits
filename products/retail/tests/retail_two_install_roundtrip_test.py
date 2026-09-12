"""Aura Retail -- multi-device sync: two-install composition harness.

Root cause this file exists to close (see feat/sync-divergence-fix's two
2026-08-10 fixes to commercial_runtime/sync/sync_service.py and
products/retail/backend/api/retail_api.py): every sync layer -- the emit
side (retail_api.py's outbox SELECTs), the apply side (sync_service.py's
`_apply_event` upserts), and the relay client (relay_client.py) -- is
tested in isolation. A field present in one layer and absent in another
(Bug 1: products.supplier_id was in the outbox payload but never in the
apply-side upsert; Bug 2: `status` was missing from every apply-side
ON CONFLICT DO UPDATE SET, so a restore-from-soft-delete silently never
took effect on a second device) passes every one of those isolated test
suites, because none of them actually proves the pieces COMPOSE. This file
proves composition: two simulated installs, a real emit path, a real apply
path, and a real (if double-transport) relay in between, driven end to end
per test.

Two traps this harness's shape is designed around:

1. The Postgres trap -- Owner's REAL multi-device sync relay
   (owner/app/sync/routes.py, transported by
   commercial_runtime/sync/relay_client.py's SyncRelayClient) needs
   Postgres, which this dev environment does not have running. `InMemoryRelay`
   below is a double that implements exactly the push/pull surface
   SyncService's own `client_factory()` result is ever asked for --
   `.push(events) -> dict` and `.pull(since) -> {"events": [...], "cursor":
   N}` (see SyncRelayClient.push/pull, relay_client.py:127-137) -- with all
   the transport/signing/retry plumbing (which test_relay_client.py already
   covers on its own) stripped out. A single instance is shared between
   install A's and install B's own SyncService in every test below, so a
   push from one is genuinely visible to a pull from the other.

2. The AURA_APP_DATA trap -- database/schema.py's own connection helpers
   (`get_retail_conn`/`_conn`/`_get_path`) close over MODULE-LEVEL
   `SUBSYS_DIR`/`BASE_DIR` globals, resolved once at import time from the
   `AURA_APP_DATA` env var (see products/run_all_tests.py's own module
   docstring for the full story -- it is why that runner execs each test
   FILE as its own subprocess). Booting a second real Flask app in this
   same process for "install B" would either silently reuse install A's
   already-imported `database.schema`/`config` state (pointing B at A's own
   database) or require re-importing those modules under a different name
   entirely, neither of which is what "two independent installs" is
   supposed to mean. `install_b` below sidesteps this differently: it
   temporarily repoints `schema.SUBSYS_DIR`/`schema.BASE_DIR` at install B's
   own temp directory, calls the REAL `schema.init_retail()` (so install B
   ends up on the exact same migrated v5 schema install A's Flask app
   booted with -- never a hand-copied CREATE TABLE list that could quietly
   drift from the real one), then restores the globals immediately -- so
   every other fixture/route in this file that calls `get_retail_conn()`
   afterward keeps resolving to install A's own directory, undisturbed. No
   second Flask app is ever built.

This file follows the same self-contained bootstrap convention as every
other file in this suite (no shared conftest.py exists for
products/retail/tests/ -- confirmed by inspection): its own temp app-data
dir, its own license seed, its own Flask app boot (that Flask app IS
install A), its own fixtures local to this file.

Run:
    pytest products/retail/tests/retail_two_install_roundtrip_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_tworoundtrip_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

# Seeded BEFORE the app is built, matching every other route-level test file
# in this suite -- creating/updating/deleting products/customers/suppliers is
# capability-guarded, so an inactive license would 403 every test here
# before reaching the code paths this file is actually about.
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
    created company -- one company per test, so outbox/table assertions
    never see another test's rows. Mirrors every other route-level test
    file in this suite (e.g. retail_product_sync_test.py's `client`
    fixture) exactly."""
    email = f'tworoundtrip-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'TwoRoundtripPW1'
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
def a_conn():
    """A real connection to install A's own (shared, file-backed) retail
    database -- reads back whatever the Flask routes above just committed."""
    conn = get_retail_conn()
    yield conn
    conn.close()


@pytest.fixture
def service_a(relay):
    """A SyncService that drains install A's OWN real outbox (the same
    database file the `client` fixture's routes just wrote to) into the
    shared relay double. Push-only in every test below -- install A never
    needs to apply a pulled event here -- so no local_company_id_provider is
    wired (SyncService's own docstring: a provider is only required the
    first time an apply actually needs one; omitting it here means a stray
    call to service_a.pull_once() would raise loudly rather than silently
    touching the wrong company_id, which is the correct failure mode for a
    fixture that was never meant to pull)."""
    return SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)


# ── The relay double (see module docstring, trap 1) ─────────────────────────

class InMemoryRelay:
    """Stands in for Owner's real multi-device sync relay. Implements only
    the two methods SyncService's `client_factory()` result is ever called
    with -- `push(events)` and `pull(since)` -- matching SyncRelayClient's
    own return shapes (commercial_runtime/sync/relay_client.py:127-137)
    exactly: `push` returns a plain status dict (SyncService never inspects
    it beyond "did this raise"), `pull` returns `{"events": [...], "cursor":
    N}`. `seq` is assigned serially across ALL pushes this instance has ever
    seen (never per-install), exactly like a real shared relay would."""

    def __init__(self):
        self.events = []
        self._next_seq = 1

    def push(self, events):
        for ev in events:
            self.events.append(dict(ev, seq=self._next_seq))
            self._next_seq += 1
        return {"stored": len(events), "received": len(events)}

    def pull(self, since):
        pending = [ev for ev in self.events if ev["seq"] > since]
        cursor = pending[-1]["seq"] if pending else since
        return {"events": pending, "cursor": cursor}


@pytest.fixture
def relay():
    return InMemoryRelay()


# ── Install B: a bare temp SQLite DB, no second Flask app (trap 2) ─────────

@pytest.fixture
def install_b(tmp_path):
    """Builds install B's database directly from database/schema.py's own
    init_retail() -- see the module docstring's "AURA_APP_DATA trap" note
    for why this is not a second Flask app. Returns a `get_conn` callable
    (the exact shape SyncService's own `get_conn` constructor argument
    expects) that always opens a fresh raw connection to install B's
    retail.db, using the identical connection settings schema.py's own
    `_conn()` uses (WAL, busy_timeout, foreign_keys=ON) -- opened directly
    against the captured db path rather than through `get_retail_conn()`
    again, since by the time a test actually calls it, the module globals
    below have already been restored to install A's own directory."""
    b_root = tmp_path / "install_b"
    b_subsys = b_root / "subsystems"
    b_subsys.mkdir(parents=True, exist_ok=True)

    original_base_dir, original_subsys_dir = schema.BASE_DIR, schema.SUBSYS_DIR
    schema.BASE_DIR = str(b_root)
    schema.SUBSYS_DIR = str(b_subsys)
    try:
        # The real init function -- same CREATE TABLE + same
        # ensure_schema_version migration chain install A's own Flask app
        # booted with (app.init_app() -> database.schema.init_retail()), so
        # install B lands on the identical v5 schema, not a hand-copied
        # approximation of it. AURA_STANDALONE=1 (set at module import time,
        # above) means config.IS_STANDALONE is already True for this whole
        # process, so init_retail() skips demo-data seeding here exactly as
        # it does for install A -- install B starts empty.
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
def service_b(relay, install_b):
    """A SyncService driving install B's raw database directly (no Flask
    involved at all), sharing the SAME relay instance as service_a -- this
    is what makes the two installs a genuine pair rather than two
    independently-tested halves. `local_company_id_provider` returns a
    fixed, made-up id -- deliberately NOT install A's own company_id (see
    sync_service.py's module docstring's "Cross-device company_id bug fix":
    a pulled event's payload carries the SENDING device's company_id, which
    `_apply_event` must always ignore in favour of the RECEIVING device's
    own locally-authoritative one)."""
    return SyncService(
        client_factory=lambda: relay, get_conn=install_b,
        local_company_id_provider=lambda: "install-b-company",
    )


def _sync_a_to_b(service_a, service_b):
    """One full device-A-push -> relay -> device-B-pull tick -- the unit of
    convergence every test below composes from."""
    service_a.push_once()
    service_b.pull_once()


def _table_columns(conn, table):
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


# Columns that are, by design, device-local and never travel in a sync
# payload at all: company_id is derived independently per-device at
# onboarding (sync_service.py's module docstring), and created_at is a
# plain local-clock default never selected into any _queue_sync_event
# payload in retail_api.py. Excluded from the field-by-field comparison in
# test_update_round_trips_every_payload_field below, which otherwise
# compares EVERY column PRAGMA table_info(products) actually reports --
# introspected, not a hardcoded list that could quietly go stale the next
# time a column is added.
DEVICE_LOCAL_PRODUCT_COLUMNS = {"company_id", "created_at"}


# ── 1. Create-with-supplier round-trips supplier_id (exactly Bug 1) ────────

def test_create_with_supplier_round_trips_supplier_id_a_to_b(client, a_conn, service_a, service_b, install_b):
    supplier_resp = client.post('/api/sub/retail/suppliers', json={'name': 'TechDistrib Two-Install'})
    assert supplier_resp.status_code == 200
    supplier_id = supplier_resp.get_json()['data']['id']
    # products.supplier_id declares REFERENCES suppliers(id) and every
    # connection here runs with PRAGMA foreign_keys=ON -- the supplier row
    # must exist on install B BEFORE a product referencing it is applied
    # there, exactly like two real devices converging in the correct order.
    _sync_a_to_b(service_a, service_b)

    b_conn = install_b()
    b_supplier = b_conn.execute("SELECT name FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
    b_conn.close()
    assert b_supplier is not None and b_supplier["name"] == 'TechDistrib Two-Install'

    product_resp = client.post('/api/sub/retail/products', json={
        'name': 'Two-Install Widget', 'sku': 'TWO-RT-1', 'sell_price': 9.99, 'supplier_id': supplier_id,
    })
    assert product_resp.status_code == 200
    pid = product_resp.get_json()['data']['id']
    _sync_a_to_b(service_a, service_b)

    a_row = a_conn.execute("SELECT supplier_id FROM products WHERE id=?", (pid,)).fetchone()
    assert a_row["supplier_id"] == supplier_id  # sanity: A itself has it

    b_conn = install_b()
    b_row = b_conn.execute("SELECT supplier_id FROM products WHERE id=?", (pid,)).fetchone()
    b_conn.close()
    assert b_row is not None
    # This is exactly the bug: before yesterday's fix, supplier_id was in
    # the outbox payload but never in _apply_event's INSERT column list or
    # ON CONFLICT DO UPDATE SET -- b_row["supplier_id"] would have silently
    # stayed NULL forever.
    assert b_row["supplier_id"] == supplier_id


# ── 2. Update round-trips EVERY payload field ───────────────────────────────

def test_update_round_trips_every_payload_field_against_actual_columns(client, a_conn, service_a, service_b, install_b):
    supplier1 = client.post('/api/sub/retail/suppliers', json={'name': 'Supplier One'}).get_json()['data']['id']
    category1 = client.post('/api/sub/retail/categories', json={'name': 'Category One'}).get_json()['data']['id']
    _sync_a_to_b(service_a, service_b)

    create = client.post('/api/sub/retail/products', json={
        'name': 'Original Widget', 'sku': 'TWO-RT-2', 'barcode': 'BAR-ORIG',
        'sell_price': 5, 'cost_price': 2, 'tax_rate': 5, 'unit': 'pcs', 'reorder_level': 3,
        'category_id': category1, 'supplier_id': supplier1,
    })
    assert create.status_code == 200
    pid = create.get_json()['data']['id']
    _sync_a_to_b(service_a, service_b)

    # A second supplier/category to update TO -- proves an update that
    # changes the FK-shaped fields (not just sets them once at create time)
    # round-trips too, not just that they were applied once and then
    # silently frozen (see test_pull_once_product_upsert_updates_supplier_id
    # _on_conflict in commercial_runtime/sync/tests/test_sync_service.py for
    # the equivalent single-layer regression test this composes on top of).
    supplier2 = client.post('/api/sub/retail/suppliers', json={'name': 'Supplier Two'}).get_json()['data']['id']
    category2 = client.post('/api/sub/retail/categories', json={'name': 'Category Two'}).get_json()['data']['id']
    _sync_a_to_b(service_a, service_b)

    update = client.patch(f'/api/sub/retail/products/{pid}', json={
        'name': 'Renamed Widget', 'barcode': 'BAR-NEW', 'category_id': category2, 'supplier_id': supplier2,
        'cost_price': 4.25, 'sell_price': 11.5, 'tax_rate': 8, 'unit': 'box', 'reorder_level': 9,
    })
    assert update.status_code == 200
    _sync_a_to_b(service_a, service_b)

    a_row = dict(a_conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone())
    b_conn = install_b()
    b_row_raw = b_conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    assert b_row_raw is not None
    b_row = dict(b_row_raw)
    b_conn.close()

    # Introspected, not hardcoded -- this is the whole point: a future
    # column added to `products` is automatically included in this
    # comparison without anyone having to remember to update a list here.
    columns = _table_columns(a_conn, "products")
    compared = [c for c in columns if c not in DEVICE_LOCAL_PRODUCT_COLUMNS]
    assert compared, "PRAGMA table_info(products) returned no comparable columns -- introspection is broken"
    for col in compared:
        assert a_row[col] == b_row[col], f"products.{col} diverged after update: A={a_row[col]!r} B={b_row[col]!r}"


# ── 3. Soft-delete then restore round-trips `deleted_at_utc` (exactly Bug 2,
#      updated for stage 6b-iii-a -- see that stage's own note below) ──────

def test_soft_delete_then_restore_round_trips_deleted_at_utc_a_to_b(client, service_a, service_b, install_b):
    # Supplier, not product/customer: update_supplier's own `allowed` PATCH
    # fields list (retail_api.py) includes 'status', so a restore is
    # actually reachable through the real HTTP route, exactly like a real
    # second device would trigger it.
    #
    # RENAMED and REWRITTEN for launch-readiness Phase 6 stage 6b-iii-a
    # (deletion stops overloading `status`): this test used to be named
    # `test_soft_delete_then_restore_round_trips_status_a_to_b` and asserted
    # `status` flipping 'active' -> 'inactive' -> 'active' across the wire.
    # That is no longer what happens -- `delete_supplier` stopped writing
    # `status='inactive'` this stage, so `status` stays 'active' through the
    # whole delete/restore cycle now. `deleted_at_utc` is what actually
    # round-trips: it gets stamped on delete and cleared on restore (Step 3's
    # `update_supplier` change), so this test asserts THAT column instead --
    # the underlying property Bug 2 fixed (a restore reaching the other
    # device via the upsert's DO UPDATE SET, not just the delete branch) is
    # unchanged and still exercised here, just through the column that now
    # actually carries the meaning.
    create = client.post('/api/sub/retail/suppliers', json={'name': 'Round Trip Supplier'})
    assert create.status_code == 200
    sup_id = create.get_json()['data']['id']
    _sync_a_to_b(service_a, service_b)

    def _b_row():
        b_conn = install_b()
        row = b_conn.execute("SELECT status, deleted_at_utc FROM suppliers WHERE id=?", (sup_id,)).fetchone()
        b_conn.close()
        return dict(row) if row else None

    assert _b_row() == {"status": "active", "deleted_at_utc": None}

    delete_resp = client.delete(f'/api/sub/retail/suppliers/{sup_id}')
    assert delete_resp.status_code == 200
    _sync_a_to_b(service_a, service_b)
    # This direction already worked before yesterday's fix -- a "delete"
    # event was always applied via its own dedicated soft-delete branch,
    # never through the upsert. `status` stays 'active' now (stage
    # 6b-iii-a); `deleted_at_utc` is the one that must have landed.
    b_after_delete = _b_row()
    assert b_after_delete["status"] == "active"
    assert b_after_delete["deleted_at_utc"] is not None

    restore_resp = client.patch(f'/api/sub/retail/suppliers/{sup_id}', json={'status': 'active'})
    assert restore_resp.status_code == 200
    _sync_a_to_b(service_a, service_b)
    # This is exactly Bug 2, reached through `deleted_at_utc` instead of
    # `status`: a restore is an "update" event, and `deleted_at_utc` is now
    # in the upsert's DELTA-GATED ON CONFLICT DO UPDATE SET -- without that
    # (stage 6b-iii-a's Step 3), this would have silently stayed tombstoned
    # on B forever, the same failure shape Bug 2 originally described for
    # `status`.
    assert _b_row() == {"status": "active", "deleted_at_utc": None}


# ── 4. Unknown entity_type is silently skipped by the apply side ───────────

def test_unknown_entity_type_in_outbox_is_silently_skipped_by_apply_side(client, service_a, service_b, install_b, relay):
    # Simulates a THIRD device (or a newer Owner-relay-understood entity
    # type this particular build's _apply_event doesn't know how to apply
    # yet -- see sync_service.py's module docstring's "Scope note") pushing
    # an event ahead of a real, known one. Pushed directly onto the shared
    # relay, not through install A's own outbox -- retail_api.py's routes
    # never emit an entity_type outside category/product/customer/supplier,
    # so this is exactly how an unknown type would actually arrive: from
    # some OTHER device on the same license, never from this test's own
    # install A.
    relay.push([{
        "id": str(uuid.uuid4()), "entity_type": "promotion", "entity_id": "promo-1",
        "event_type": "create", "payload": {"id": "promo-1", "name": "Loyalty Discount"},
        "created_at": "2026-08-10T00:00:00+00:00",
    }])

    # A real, known event alongside it, in the SAME pull batch -- proves the
    # unknown one doesn't abort or corrupt processing of its neighbors.
    create = client.post('/api/sub/retail/suppliers', json={'name': 'Known Entity Supplier'})
    assert create.status_code == 200
    sup_id = create.get_json()['data']['id']
    service_a.push_once()

    service_b.pull_once()  # must not raise -- an unhandled exception here fails this test on its own

    b_conn = install_b()
    row = b_conn.execute("SELECT status FROM suppliers WHERE id=?", (sup_id,)).fetchone()
    cursor = b_conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()["last_seq"]
    b_conn.close()

    assert row is not None and row["status"] == "active"  # the known-type neighbor still applied correctly
    assert cursor == 2  # relay seq: promotion=1, supplier create=2 -- cursor advances past the unknown one too


# ── 5. The cursor advances exactly once per pull, not once per event ───────

class _CountingConnWrapper:
    """Wraps a real connection to count how many times a SQL statement
    containing `substr` (already-uppercased) is executed -- the same
    interception technique
    commercial_runtime/sync/tests/test_sync_service.py's own
    `_ConnWrapper` uses (test_push_once_only_deletes_rows_that_were_
    actually_pushed), reused here to prove sync_cursor is written exactly
    ONCE per pull_once() call, never once per event in the pulled batch."""

    def __init__(self, inner, substr):
        self._inner = inner
        self._substr = substr
        self.match_count = 0

    def execute(self, sql, params=()):
        if self._substr in sql.strip().upper():
            self.match_count += 1
        return self._inner.execute(sql, params)

    def __getattr__(self, item):
        return getattr(self._inner, item)


def test_pull_once_advances_cursor_exactly_once_not_once_per_event(client, service_a, install_b, relay):
    client.post('/api/sub/retail/suppliers', json={'name': 'Cursor Supplier'})
    client.post('/api/sub/retail/categories', json={'name': 'Cursor Category'})
    client.post('/api/sub/retail/customers', json={'name': 'Cursor Customer'})
    service_a.push_once()  # drains all 3 outbox rows in ONE push -- relay assigns them seq 1, 2, 3

    assert len(relay.events) == 3  # sanity: this really is a single 3-event pull batch below, not 3 separate pulls

    wrapper_holder = {}

    def wrapped_get_conn():
        wrapper = _CountingConnWrapper(install_b(), "UPDATE SYNC_CURSOR")
        wrapper_holder["wrapper"] = wrapper
        return wrapper

    service_b = SyncService(
        client_factory=lambda: relay, get_conn=wrapped_get_conn,
        local_company_id_provider=lambda: "install-b-company",
    )
    service_b.pull_once()

    assert wrapper_holder["wrapper"].match_count == 1  # exactly one cursor UPDATE for a 3-event batch, not 3

    b_conn = install_b()
    cursor = b_conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()["last_seq"]
    b_conn.close()
    assert cursor == 3  # advanced straight to the batch's final seq, not partially
