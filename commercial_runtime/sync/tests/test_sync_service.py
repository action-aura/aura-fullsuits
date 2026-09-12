"""Tests for SyncService, mirroring
commercial_runtime/licensing_contracts/tests/test_checkin_scheduler.py's
pattern: a fake at the *client* boundary (FakeRelayClient, analogous to that
file's FakeClient) rather than a fake HTTP session -- SyncRelayClient's own
transport behavior is already covered end-to-end by test_relay_client.py.
Local persistence uses a real sqlite file under tmp_path (same convention
test_checkin_scheduler.py/test_state_repository.py use for
LicenseStateRepository), built with the exact categories/sync_outbox/
sync_cursor schema products/retail/backend/database/schema.py creates.
"""
import json
import sqlite3
import threading
import time
import uuid

import pytest

from commercial_runtime.sync.relay_client import NetworkError, RelayRejected
from commercial_runtime.sync.sync_service import (
    _PUSH_CHUNK_SIZE,
    RETAIL_SYNC_ENTITY_TYPES,
    SyncService,
    get_active_health,
    nudge,
    register_active_service,
    unregister_active_service,
)


class FakeRelayClient:
    """Queue of canned push/pull outcomes, consumed in order -- mirrors
    test_checkin_scheduler.py's FakeClient. An item that is an Exception
    instance is raised instead of returned, exactly like that fixture."""

    def __init__(self, push_responses=None, pull_responses=None):
        self._push_responses = list(push_responses or [])
        self._pull_responses = list(pull_responses or [])
        self.push_calls = []
        self.pull_calls = []

    def push(self, events):
        self.push_calls.append(events)
        item = self._push_responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def pull(self, since):
        self.pull_calls.append(since)
        item = self._pull_responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


_SCHEMA = """
-- launch-readiness Phase 6 stage 6a-i (2026-08-26 follow-up): row_version/
-- updated_at_utc added to all five catalogue tables below -- _apply_event's
-- category/product/customer/supplier/reorder_request branches now write
-- both columns (carrying the sender's row_version through, so two devices'
-- counters converge instead of silently diverging), and this hand-built
-- minimal fixture predates that. test_internal_routes.py mirrors this same
-- shape for its own `categories` table -- keep the two in step.
--
-- launch-readiness Phase 6 stage 6b-ii (tombstones): `deleted_at_utc` added
-- to categories/products/customers/suppliers (NOT reorder_requests -- there
-- is no reorder_request delete event type at all, see this module's own
-- comment on that branch) -- the SAME "this fixture predates the new
-- column" update this file's own history already made once for row_version/
-- updated_at_utc above, done again for the identical reason: every delete
-- branch in `_apply_event` now unconditionally references the column.
CREATE TABLE categories (
    id TEXT PRIMARY KEY,
    company_id INTEGER DEFAULT 1,
    name TEXT NOT NULL,
    description TEXT,
    row_version INTEGER NOT NULL DEFAULT 1,
    updated_at_utc TEXT,
    deleted_at_utc TEXT
);
-- launch-readiness "product variants, wave 1" (schema v25): `parent_product_id`/
-- `variant_label` added below, the SAME "this fixture predates the new
-- column" update this file's own history already made for row_version/
-- updated_at_utc/deleted_at_utc above. This is the hand copy of the real
-- `products` table (products/retail/backend/database/schema.py) that MUST be
-- kept in step with every column the `product` branch of `_apply_event`
-- writes -- on 2026-09-05 it lagged v25 by these exact two columns, and the
-- gap didn't show up as a wrong assertion, it showed up as
-- `sqlite3.OperationalError: table products has no column named
-- parent_product_id` fired from inside the INSERT, hiding 14 real test
-- failures behind a schema mismatch for weeks.
CREATE TABLE products (
    id TEXT PRIMARY KEY,
    company_id INTEGER DEFAULT 1,
    sku TEXT NOT NULL,
    barcode TEXT,
    name TEXT NOT NULL,
    category_id TEXT,
    supplier_id TEXT,
    cost_price REAL DEFAULT 0,
    sell_price REAL DEFAULT 0,
    tax_rate REAL DEFAULT 0,
    unit TEXT DEFAULT 'pcs',
    reorder_level INTEGER DEFAULT 5,
    reorder_method TEXT DEFAULT 'none',
    status TEXT DEFAULT 'active',
    parent_product_id TEXT,
    variant_label TEXT,
    row_version INTEGER NOT NULL DEFAULT 1,
    updated_at_utc TEXT,
    deleted_at_utc TEXT
);
CREATE TABLE customers (
    id TEXT PRIMARY KEY,
    company_id INTEGER DEFAULT 1,
    name TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    address TEXT,
    status TEXT DEFAULT 'active',
    row_version INTEGER NOT NULL DEFAULT 1,
    updated_at_utc TEXT,
    deleted_at_utc TEXT
);
CREATE TABLE suppliers (
    id TEXT PRIMARY KEY,
    company_id INTEGER DEFAULT 1,
    name TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    address TEXT,
    status TEXT DEFAULT 'active',
    row_version INTEGER NOT NULL DEFAULT 1,
    updated_at_utc TEXT,
    deleted_at_utc TEXT
);
CREATE TABLE reorder_requests (
    id TEXT PRIMARY KEY,
    company_id TEXT,
    branch_id INTEGER,
    product_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    draft_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT,
    row_version INTEGER NOT NULL DEFAULT 1,
    updated_at_utc TEXT
);
-- launch-readiness Phase 6 stage 6a-ii (this stage): `sync_conflicts`,
-- shaped exactly like _migrate_add_sync_conflicts_and_drop_quantity_reserved's
-- own CREATE TABLE (products/retail/backend/database/schema.py, v17). The
-- five catalogue branches' reject-stale gate below writes a row here on
-- every discard -- this fixture predates v17 the same way the row_version
-- columns above did (see this file's own module docstring), so it has to
-- carry the table forward too, or every discard test in this file raises
-- `sqlite3.OperationalError: no such table: sync_conflicts` before it ever
-- reaches its own assertions.
CREATE TABLE sync_conflicts (
    id TEXT PRIMARY KEY,
    company_id INTEGER,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    local_row_version INTEGER,
    incoming_row_version INTEGER,
    incoming_payload TEXT NOT NULL,
    detected_at_utc TEXT NOT NULL
);
CREATE TABLE sync_outbox (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE sync_cursor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_seq INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO sync_cursor (id, last_seq) VALUES (1, 0);
-- Phase 5 (money-moving sync): the five entity types' tables, same shape
-- (columns that matter to _apply_event) as products/retail/backend/
-- database/schema.py's real ones -- separate local autoincrement `id` +
-- wire `uid` with its own partial UNIQUE index, exactly like the real v13
-- migration leaves behind.
CREATE TABLE sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER DEFAULT 1,
    sale_number TEXT UNIQUE,
    branch_id INTEGER,
    customer_id INTEGER,
    cashier TEXT DEFAULT 'POS',
    subtotal REAL DEFAULT 0,
    discount_amount REAL DEFAULT 0,
    tax_amount REAL DEFAULT 0,
    total REAL DEFAULT 0,
    amount_paid REAL DEFAULT 0,
    change_amount REAL DEFAULT 0,
    payment_method TEXT DEFAULT 'cash',
    status TEXT DEFAULT 'completed',
    idempotency_key TEXT UNIQUE,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    due_date TEXT,
    session_id TEXT REFERENCES cash_sessions(id),
    uid TEXT,
    actor_user_uid TEXT,
    terminal_id TEXT,
    created_at_utc TEXT
);
CREATE UNIQUE INDEX idx_sales_uid ON sales(uid) WHERE uid IS NOT NULL;
CREATE TABLE sale_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity REAL NOT NULL,
    unit_price REAL NOT NULL,
    discount_pct REAL DEFAULT 0,
    tax_rate REAL DEFAULT 0,
    line_total REAL NOT NULL,
    uid TEXT,
    FOREIGN KEY (sale_id) REFERENCES sales(id),
    -- The real schema (products/retail/backend/database/schema.py) declares
    -- BOTH FKs on this table. This fixture declared only the sale one, so a
    -- pulled line item pointing at a product this device does not have looked
    -- harmless here while raising `sqlite3.IntegrityError: FOREIGN KEY
    -- constraint failed` on a real install -- wedging the receiving cursor
    -- forever. That gap is why the wedge was found by a two-real-install
    -- harness and not by this file. Restored so the fixture cannot hide it
    -- again; `_seed_product` below supplies the row the builders reference.
    FOREIGN KEY (product_id) REFERENCES products(id)
);
CREATE UNIQUE INDEX idx_sale_items_uid ON sale_items(uid) WHERE uid IS NOT NULL;
CREATE TABLE returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER DEFAULT 1,
    return_number TEXT UNIQUE,
    sale_id INTEGER,
    branch_id INTEGER,
    cashier TEXT DEFAULT 'POS',
    reason TEXT,
    refund_method TEXT DEFAULT 'cash',
    refund_amount REAL DEFAULT 0,
    status TEXT DEFAULT 'completed',
    idempotency_key TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    session_id TEXT REFERENCES cash_sessions(id),
    uid TEXT,
    actor_user_uid TEXT,
    terminal_id TEXT,
    created_at_utc TEXT,
    FOREIGN KEY (sale_id) REFERENCES sales(id)
);
CREATE UNIQUE INDEX idx_returns_uid ON returns(uid) WHERE uid IS NOT NULL;
CREATE TABLE return_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    return_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity REAL NOT NULL,
    unit_price REAL NOT NULL,
    line_total REAL NOT NULL,
    uid TEXT,
    FOREIGN KEY (return_id) REFERENCES returns(id),
    -- See the identical note on sale_items above.
    FOREIGN KEY (product_id) REFERENCES products(id)
);
CREATE UNIQUE INDEX idx_return_items_uid ON return_items(uid) WHERE uid IS NOT NULL;
CREATE TABLE payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER DEFAULT 1,
    reference TEXT,
    party_type TEXT,
    party_id TEXT,
    direction TEXT,
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'USD',
    fx_rate REAL DEFAULT 1,
    method TEXT,
    related_type TEXT,
    related_id INTEGER,
    sale_id INTEGER,
    notes TEXT,
    status TEXT DEFAULT 'active',
    created_by TEXT,
    device TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    uid TEXT,
    FOREIGN KEY (sale_id) REFERENCES sales(id)
);
CREATE UNIQUE INDEX idx_payments_uid ON payments(uid) WHERE uid IS NOT NULL;
-- Minimal cash_sessions, ONLY so sales.session_id's REFERENCES clause above
-- resolves -- never populated by any test in this file. Its emptiness is
-- itself part of what test_sale_apply_never_stamps_a_session_id proves: a
-- pulled sale's session_id must be NULL, and an FK to a table that has and
-- will always have zero rows is the sharpest way to prove that -- ANY
-- non-NULL value would fail the FK outright under foreign_keys=ON.
CREATE TABLE cash_sessions (id TEXT PRIMARY KEY);
CREATE TABLE sync_apply_quarantine (
    entity_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    reason TEXT NOT NULL,
    detail TEXT,
    quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entity_id, event_type)
);
-- `branches`, shaped as products/retail/backend/database/schema.py declares it.
-- Required, not decorative: AUDIT-032C's `_resolve_branch_id` reads this table
-- on EVERY sale/return apply, so without it those branches raise
-- `sqlite3.OperationalError: no such table: branches` before any of the logic
-- the tests below actually pin ever runs -- 8 of this file's tests failed that
-- way when the AUDIT-032C fix landed without this table alongside it.
-- Deliberately left EMPTY: `_resolve_branch_id`'s tier-2 fallback self-heals a
-- default branch when a company has none, exactly like retail_api.py's own
-- `_default_branch()` does, so an empty table exercises the real fallback path
-- rather than hiding it behind a pre-seeded row.
CREATE TABLE branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id TEXT,
    name TEXT NOT NULL,
    address TEXT,
    phone TEXT,
    uid TEXT
);
"""


#: The product every money line-item builder in this file points at
#: (`_sale_item_event`/`_return_item_event` default `product_id` to it).
#: `sale_items`/`return_items` both declare a real FK to `products`, exactly
#: as the shipping schema does, so this row has to EXIST before an ordinary
#: line item can apply.
#:
#: Seeded per-test by `_seed_product()` rather than in the `db_path` fixture,
#: deliberately: the pre-existing catalogue tests in this file count
#: `products` rows and assert exact totals, so a globally-seeded row would
#: silently change what THOSE tests are measuring. A money test that needs the
#: product says so.
LINE_ITEM_PRODUCT_ID = 1


def _seed_product(get_conn, product_id=LINE_ITEM_PRODUCT_ID):
    conn = get_conn()
    try:
        conn.execute("INSERT INTO products (id, sku, name) VALUES (?,?,?)",
                     (product_id, f"SEED-{product_id}", "Seeded Product"))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "retail.db"
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def get_conn(db_path):
    # Same connection settings as products/retail/backend/database/schema.py's
    # real _conn() -- WAL mode + a real busy_timeout so a reader/writer or
    # two overlapping writers block-and-retry instead of raising "database
    # is locked" the way a bare sqlite3.connect() default would (this
    # matters for the concurrency/nudge tests below, which deliberately run
    # two real connections against the same file at once).
    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        # Matches production's real _conn() (schema.py) -- ON, always. The
        # Phase 5 money-moving tables below declare real FKs (sale_items ->
        # sales, payments -> sales, sales -> cash_sessions), and several
        # tests in this file exist specifically to prove _apply_event never
        # lets one of those raise and wedge the whole pull batch -- a
        # meaningless proof if this connection silently never enforced them.
        c.execute("PRAGMA foreign_keys=ON")
        return c
    return _get_conn


def _insert_outbox_row(get_conn, entity_id=None, event_type="create", payload=None):
    entity_id = entity_id or str(uuid.uuid4())
    payload = payload if payload is not None else {"id": entity_id, "company_id": 1, "name": "Widgets", "description": ""}
    conn = get_conn()
    conn.execute(
        "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), "category", entity_id, event_type, json.dumps(payload), "2026-08-06T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return entity_id, payload


def _outbox_rows(get_conn):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM sync_outbox").fetchall()]
    conn.close()
    return rows


def _categories(get_conn):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM categories").fetchall()]
    conn.close()
    return rows


def _products(get_conn):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM products").fetchall()]
    conn.close()
    return rows


def _customers(get_conn):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM customers").fetchall()]
    conn.close()
    return rows


def _suppliers(get_conn):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM suppliers").fetchall()]
    conn.close()
    return rows


def _reorder_requests(get_conn):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM reorder_requests").fetchall()]
    conn.close()
    return rows


def _sync_conflicts(get_conn):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM sync_conflicts").fetchall()]
    conn.close()
    return rows


def _cursor(get_conn):
    conn = get_conn()
    row = conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()
    conn.close()
    return row["last_seq"]


# ── push_once() ──────────────────────────────────────────────────────────

def test_push_once_with_empty_outbox_never_calls_the_relay(get_conn):
    client = FakeRelayClient()
    service = SyncService(lambda: client, get_conn)
    service.push_once()
    assert client.push_calls == []


def test_push_once_success_clears_the_pushed_rows(get_conn):
    _insert_outbox_row(get_conn)
    _insert_outbox_row(get_conn)
    client = FakeRelayClient(push_responses=[{"stored": 2, "received": 2}])
    service = SyncService(lambda: client, get_conn)

    service.push_once()

    assert _outbox_rows(get_conn) == []
    assert len(client.push_calls) == 1
    assert len(client.push_calls[0]) == 2


def test_push_once_sends_events_with_decoded_payload(get_conn):
    entity_id, payload = _insert_outbox_row(get_conn)
    client = FakeRelayClient(push_responses=[{"stored": 1, "received": 1}])
    service = SyncService(lambda: client, get_conn)

    service.push_once()

    sent_event = client.push_calls[0][0]
    assert sent_event["entity_id"] == entity_id
    assert sent_event["entity_type"] == "category"
    assert sent_event["event_type"] == "create"
    assert sent_event["payload"] == payload  # decoded from the stored JSON string


def test_push_once_failure_leaves_outbox_untouched_and_does_not_crash(get_conn):
    _insert_outbox_row(get_conn)
    client = FakeRelayClient(push_responses=[NetworkError("NETWORK_UNAVAILABLE", "offline")])
    service = SyncService(lambda: client, get_conn)

    with pytest.raises(NetworkError):
        service.push_once()

    # Nothing was deleted -- the exact row that would have been pushed is
    # still there for the next attempt.
    assert len(_outbox_rows(get_conn)) == 1


def test_push_once_business_rejection_also_leaves_outbox_untouched(get_conn):
    _insert_outbox_row(get_conn)
    client = FakeRelayClient(push_responses=[RelayRejected("INVALID_SIGNATURE", "bad sig")])
    service = SyncService(lambda: client, get_conn)

    with pytest.raises(RelayRejected):
        service.push_once()

    assert len(_outbox_rows(get_conn)) == 1


def test_push_once_only_deletes_rows_that_were_actually_pushed(get_conn):
    """A concurrent insert (e.g. a route committing a new category) landing
    between the SELECT and the DELETE must never be swept up by an
    unqualified DELETE -- push_once() deletes by the specific ids it read,
    never by a blanket 'DELETE FROM sync_outbox'."""
    _insert_outbox_row(get_conn)
    client = FakeRelayClient(push_responses=[{"stored": 1, "received": 1}])

    real_get_conn = get_conn

    class _ConnWrapper:
        """Wraps the real connection so a second row can be inserted (by a
        different connection, simulating a concurrent writer) after the
        SELECT this push_once() call already made but before its DELETE."""

        def __init__(self, inner):
            self._inner = inner
            self._selected = False

        def execute(self, sql, params=()):
            result = self._inner.execute(sql, params)
            if sql.strip().upper().startswith("SELECT * FROM SYNC_OUTBOX") and not self._selected:
                self._selected = True
                _insert_outbox_row(real_get_conn, entity_id="concurrent-row")
            return result

        def __getattr__(self, item):
            return getattr(self._inner, item)

    def wrapped_get_conn():
        return _ConnWrapper(real_get_conn())

    service = SyncService(lambda: client, wrapped_get_conn)
    service.push_once()

    remaining = _outbox_rows(get_conn)
    assert len(remaining) == 1
    assert remaining[0]["entity_id"] == "concurrent-row"


def _bulk_insert_outbox(get_conn, n):
    """n category-create outbox rows on ONE connection/transaction --
    _insert_outbox_row opens and commits a connection per row, which is the
    right shape for the two-or-three-row tests above but needlessly slow for
    the hundreds-of-rows chunking tests below."""
    conn = get_conn()
    for _ in range(n):
        entity_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), "category", entity_id, "create",
             json.dumps({"id": entity_id, "name": "Bulk", "description": ""}),
             "2026-08-06T00:00:00+00:00"),
        )
    conn.commit()
    conn.close()


def test_push_once_chunks_an_outbox_larger_than_the_server_cap_and_drains_it(get_conn):
    """FAILED before the chunking fix: push_once() sent the ENTIRE outbox in
    one request, so any outbox over Owner's 200-event `_MAX_PUSH_BATCH` cap
    was rejected wholesale (INVALID_BATCH) on every attempt, forever -- the
    device could never push again. Now it must go out as sequential chunks
    of at most _PUSH_CHUNK_SIZE, and the outbox must fully drain."""
    _bulk_insert_outbox(get_conn, _PUSH_CHUNK_SIZE * 2 + 50)
    client = FakeRelayClient(push_responses=[
        {"stored": _PUSH_CHUNK_SIZE, "received": _PUSH_CHUNK_SIZE},
        {"stored": _PUSH_CHUNK_SIZE, "received": _PUSH_CHUNK_SIZE},
        {"stored": 50, "received": 50},
    ])
    service = SyncService(lambda: client, get_conn)

    service.push_once()

    assert _outbox_rows(get_conn) == []
    assert [len(c) for c in client.push_calls] == [_PUSH_CHUNK_SIZE, _PUSH_CHUNK_SIZE, 50]


def test_push_once_midway_chunk_failure_keeps_every_unacked_event_queued(get_conn):
    """A failure on chunk N must leave chunks >= N completely intact (never
    acked, never skipped) while chunks < N -- which Owner genuinely stored --
    stay acked, so the next attempt resumes exactly where this one stopped
    instead of re-pushing accepted events or losing queued ones."""
    _bulk_insert_outbox(get_conn, _PUSH_CHUNK_SIZE + 30)
    client = FakeRelayClient(push_responses=[
        {"stored": _PUSH_CHUNK_SIZE, "received": _PUSH_CHUNK_SIZE},
        NetworkError("NETWORK_UNAVAILABLE", "offline"),
    ])
    service = SyncService(lambda: client, get_conn)

    with pytest.raises(NetworkError):
        service.push_once()

    remaining = _outbox_rows(get_conn)
    # Exactly the rows of the failed second chunk survive -- the acked first
    # chunk is gone, and nothing from the failed chunk was skipped past.
    assert {r["id"] for r in remaining} == {e["id"] for e in client.push_calls[1]}

    # The next attempt (fresh client, as run_once() would build) drains them.
    retry_client = FakeRelayClient(push_responses=[{"stored": 30, "received": 30}])
    SyncService(lambda: retry_client, get_conn).push_once()
    assert _outbox_rows(get_conn) == []
    assert [len(c) for c in retry_client.push_calls] == [30]


def test_read_outbox_orders_by_insertion_sequence_not_wall_clock(get_conn):
    """FAILED before the rowid-ordering fix: read_outbox() ordered by
    `created_at`, which is local-clock ISO text with no tiebreaker. A clock
    step-back (NTP correction, manual change) between a parent write and its
    child write makes the CHILD sort first -- and receivers apply with
    PRAGMA foreign_keys=ON, so supplier-after-product / product-after-
    reorder_request raises on apply and freezes every other device on the
    same failing batch forever. The outbox must replay in true insertion
    order regardless of what the wall clock claimed."""
    supplier_id, product_id, product2_id, reorder_id = (str(uuid.uuid4()) for _ in range(4))
    conn = get_conn()
    rows = [
        # (entity_type, entity_id, payload, created_at) in INSERTION order.
        # The supplier (parent) carries the LATEST created_at -- the exact
        # inversion a clock step-back between writes produces.
        ("supplier", supplier_id, {"id": supplier_id, "name": "Parent Supplies"},
         "2026-08-06T00:00:10+00:00"),
        ("product", product_id, {"id": product_id, "sku": "P-1", "name": "Child", "supplier_id": supplier_id},
         "2026-08-06T00:00:05+00:00"),
        # And an exact created_at TIE between a parent and its child --
        # two writes inside the same clock tick.
        ("product", product2_id, {"id": product2_id, "sku": "P-2", "name": "Tied Parent"},
         "2026-08-06T00:00:07+00:00"),
        ("reorder_request", reorder_id, {"id": reorder_id, "product_id": product2_id, "status": "pending"},
         "2026-08-06T00:00:07+00:00"),
    ]
    for entity_type, entity_id, payload, created_at in rows:
        conn.execute(
            "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), entity_type, entity_id, "create", json.dumps(payload), created_at),
        )
    conn.commit()

    service = SyncService(lambda: None, get_conn)
    events = service.read_outbox(conn)
    conn.close()

    assert [e["entity_id"] for e in events] == [supplier_id, product_id, product2_id, reorder_id]


# ── pull_once() ──────────────────────────────────────────────────────────

def _pull_event(entity_id, event_type, payload):
    return {
        "id": str(uuid.uuid4()), "entity_type": "category", "entity_id": entity_id,
        "event_type": event_type, "payload": payload, "created_at": "2026-08-06T00:00:00+00:00",
    }


def _pull_event_of(entity_type, entity_id, event_type, payload):
    """Same shape as `_pull_event` above, for the product/customer/supplier
    entity types added by retail-catalog-party-sync-expansion -- kept
    separate rather than adding an entity_type parameter to `_pull_event`
    itself so every existing category test above stays untouched."""
    return {
        "id": str(uuid.uuid4()), "entity_type": entity_type, "entity_id": entity_id,
        "event_type": event_type, "payload": payload, "created_at": "2026-08-06T00:00:00+00:00",
    }


def _product_payload(pid, **overrides):
    """A full product row payload, matching what update_product's outbox
    SELECT actually sends (retail_api.py) -- every field present, status
    defaulting to omitted (create events never send it; _apply_event's own
    p.get("status", "active") covers that case)."""
    payload = {
        "id": pid, "company_id": 1, "sku": "SKU-1", "barcode": "", "name": "Widget",
        "category_id": None, "supplier_id": None, "cost_price": 1.5, "sell_price": 3.0,
        "tax_rate": 0, "unit": "pcs", "reorder_level": 5,
    }
    payload.update(overrides)
    return payload


def test_pull_once_upserts_on_create(get_conn):
    cat_id = str(uuid.uuid4())
    event = _pull_event(cat_id, "create", {"id": cat_id, "company_id": 1, "name": "Electronics", "description": "d"})
    client = FakeRelayClient(pull_responses=[{"events": [event], "cursor": 3}])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()

    rows = _categories(get_conn)
    assert len(rows) == 1
    assert rows[0]["id"] == cat_id
    assert rows[0]["name"] == "Electronics"
    assert _cursor(get_conn) == 3


def test_pull_once_upserts_on_update_overwriting_existing_row(get_conn):
    # launch-readiness Phase 6 stage 6a-ii: row_version must strictly
    # increase across the two events for the update to apply at all -- a
    # real emitter (retail_api.py, since stage 6a-i) always bumps it in the
    # same statement as the field change, so `row_version=2` on the update
    # here is what a genuinely later edit actually looks like on the wire,
    # not a magic number chosen to pass.
    cat_id = str(uuid.uuid4())
    create_event = _pull_event(cat_id, "create", {"id": cat_id, "company_id": 1, "name": "Old Name", "description": "", "row_version": 1})
    update_event = _pull_event(cat_id, "update", {"id": cat_id, "company_id": 1, "name": "New Name", "description": "updated", "row_version": 2})
    client = FakeRelayClient(pull_responses=[{"events": [create_event], "cursor": 1}, {"events": [update_event], "cursor": 2}])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    service.pull_once()

    rows = _categories(get_conn)
    assert len(rows) == 1  # still one row, not two
    assert rows[0]["name"] == "New Name"
    assert rows[0]["description"] == "updated"
    assert _cursor(get_conn) == 2


# launch-readiness Phase 6 stage 6b-ii (tombstones, phase6b-decisions.md
# "Decision A"): RENAMED from `test_pull_once_hard_deletes_on_delete` and its
# one outdated assertion CHANGED -- category delete is no longer a hard
# `DELETE` (see `_apply_event`'s category branch, and
# retail_category_delete_fk_sync_test.py's module docstring for the full
# history of why the old hard delete existed and why it was replaced). What
# this test could ONLY ever prove either way -- that a `delete` event applies
# and the cursor advances past it -- is unchanged and still asserted below;
# only the SHAPE of "applied" changes, from "the row is gone" to "the row is
# tombstoned and invisible to reads" (the read-path filters this device
# would need to prove that are exercised by retail_tombstone_test.py, not
# here -- this file has no `deleted_at_utc IS NULL` read-path queries at
# all, hand-built minimal fixture that it is).
def test_pull_once_soft_deletes_on_delete(get_conn):
    cat_id = str(uuid.uuid4())
    create_event = _pull_event(cat_id, "create", {"id": cat_id, "company_id": 1, "name": "Gone Soon", "description": ""})
    delete_event = _pull_event(cat_id, "delete", {"id": cat_id})
    client = FakeRelayClient(pull_responses=[{"events": [create_event], "cursor": 1}, {"events": [delete_event], "cursor": 2}])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    assert len(_categories(get_conn)) == 1
    service.pull_once()

    rows = _categories(get_conn)
    assert len(rows) == 1, "tombstoning must not remove the row"
    assert rows[0]["id"] == cat_id
    assert rows[0]["deleted_at_utc"] is not None, "the category must be tombstoned, not merely untouched"
    assert _cursor(get_conn) == 2


# ── product/customer/supplier upsert regressions (2026-08-10) ────────────
# Two confirmed-by-reading, silent, permanent data-divergence bugs in
# _apply_event's product/customer/supplier upserts:
#
#   Bug 1 (product only): supplier_id was in retail_api.py's outbox payload
#   for both create and update but never in this upsert's INSERT column
#   list or ON CONFLICT DO UPDATE SET -- it silently never propagated to a
#   second device, ever.
#
#   Bug 2 (product, customer, supplier): `status` (the soft-delete flag)
#   was missing from ON CONFLICT DO UPDATE SET for all three entity types.
#   A restore (PATCH setting status back to 'active', which for
#   product/supplier is an ordinary "update" event, not "delete") never
#   took effect on another device -- it stayed stuck showing the record
#   inactive forever, even though the soft-delete itself (a "delete" event,
#   applied by a separate, always-worked UPDATE ... SET status='inactive'
#   branch) DID propagate correctly.

def test_pull_once_product_upsert_round_trips_supplier_id(get_conn):
    pid = str(uuid.uuid4())
    supplier_id = str(uuid.uuid4())
    event = _pull_event_of("product", pid, "create", _product_payload(pid, supplier_id=supplier_id))
    client = FakeRelayClient(pull_responses=[{"events": [event], "cursor": 1}])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()

    rows = _products(get_conn)
    assert len(rows) == 1
    assert rows[0]["supplier_id"] == supplier_id


def test_pull_once_product_upsert_updates_supplier_id_on_conflict(get_conn):
    """The bug as it actually manifested: a create followed by an update
    that changes supplier_id must overwrite it on the receiving device, not
    just apply it once and then silently ignore it forever after.

    launch-readiness Phase 6 stage 6a-ii: `row_version` must strictly
    increase across create -> update, same reasoning as the category test
    above -- a stale-or-equal incoming row_version is now discarded, not
    applied, so the update payload has to carry a genuinely higher version
    for this test to still exercise the overwrite it is named for."""
    pid = str(uuid.uuid4())
    supplier_a = str(uuid.uuid4())
    supplier_b = str(uuid.uuid4())
    create_event = _pull_event_of("product", pid, "create", _product_payload(pid, supplier_id=supplier_a, row_version=1))
    update_event = _pull_event_of("product", pid, "update", _product_payload(pid, supplier_id=supplier_b, row_version=2))
    client = FakeRelayClient(pull_responses=[
        {"events": [create_event], "cursor": 1},
        {"events": [update_event], "cursor": 2},
    ])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    assert _products(get_conn)[0]["supplier_id"] == supplier_a
    service.pull_once()
    assert _products(get_conn)[0]["supplier_id"] == supplier_b


def test_pull_once_product_deleted_at_utc_round_trips_through_soft_delete_then_restore(get_conn):
    # launch-readiness Phase 6 stage 6a-ii: each of the three events must
    # carry a strictly higher row_version than the one before it, same as
    # retail_api.py's own delete_product/update_product bump on every real
    # write -- a delete's row_version gate is on `_local_row_version`, not
    # `excluded.row_version` (it is a bare UPDATE, not an UPSERT), but the
    # "strictly greater or discarded" posture is identical.
    #
    # RENAMED and REWRITTEN for launch-readiness Phase 6 stage 6b-iii-a
    # (deletion stops overloading `status`): this test used to be named
    # `test_pull_once_product_status_round_trips_through_soft_delete_then_
    # restore` and tracked `status` flipping active -> inactive -> active.
    # `delete_product`'s apply branch no longer writes `status='inactive'`,
    # so `status` stays 'active' through the whole cycle now; `deleted_at_utc`
    # is what actually round-trips (stamped on delete, cleared on restore via
    # the update branch's own DELTA-GATED column list). The restore event
    # below now also has to carry `deleted_at_utc` explicitly (mirroring what
    # `update_product`'s real route does) and NOT set `_changed_fields`, so
    # the "no `_changed_fields` key means every column changed" legacy rule
    # (`_delta_set_clause`'s docstring) is what clears it here.
    pid = str(uuid.uuid4())
    create_event = _pull_event_of("product", pid, "create", _product_payload(pid, row_version=1))
    delete_event = _pull_event_of("product", pid, "delete", {"id": pid, "row_version": 2})
    restore_event = _pull_event_of(
        "product", pid, "update",
        _product_payload(pid, status="active", deleted_at_utc=None, row_version=3))
    client = FakeRelayClient(pull_responses=[
        {"events": [create_event], "cursor": 1},
        {"events": [delete_event], "cursor": 2},
        {"events": [restore_event], "cursor": 3},
    ])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    row = _products(get_conn)[0]
    assert row["status"] == "active"
    assert row["deleted_at_utc"] is None

    service.pull_once()  # soft-delete -- already worked before this fix
    row = _products(get_conn)[0]
    assert row["status"] == "active", "status no longer marks deletion as of stage 6b-iii-a"
    assert row["deleted_at_utc"] is not None

    service.pull_once()  # restore -- the part Bug 2 broke, now via deleted_at_utc
    row = _products(get_conn)[0]
    assert row["status"] == "active"
    assert row["deleted_at_utc"] is None


def test_pull_once_customer_deleted_at_utc_round_trips_through_soft_delete_then_restore(get_conn):
    # launch-readiness Phase 6 stage 6a-ii: same strictly-increasing
    # row_version requirement as the product round trip above -- `base`
    # itself deliberately carries no row_version so each event below states
    # its own, rather than all three silently sharing one via `dict(base, ...)`.
    #
    # RENAMED and REWRITTEN for stage 6b-iii-a -- see the product round trip
    # above for the full reasoning. NOTE, stated rather than silently
    # exercised: `update_customer` (the real route) has no restore path at
    # all today (a pre-existing asymmetry, not fixed by this stage -- see
    # phase6b-decisions.md), so this hand-built `restore_event` proves the
    # APPLY side is ready for a restore, not that one is reachable through
    # the real HTTP route the way the supplier round trip below is.
    cust_id = str(uuid.uuid4())
    base = {"id": cust_id, "company_id": 1, "name": "Acme Co", "phone": "", "email": "", "address": ""}
    create_event = _pull_event_of("customer", cust_id, "create", dict(base, row_version=1))
    delete_event = _pull_event_of("customer", cust_id, "delete", {"id": cust_id, "row_version": 2})
    restore_event = _pull_event_of(
        "customer", cust_id, "update", dict(base, status="active", deleted_at_utc=None, row_version=3))
    client = FakeRelayClient(pull_responses=[
        {"events": [create_event], "cursor": 1},
        {"events": [delete_event], "cursor": 2},
        {"events": [restore_event], "cursor": 3},
    ])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    row = _customers(get_conn)[0]
    assert row["status"] == "active"
    assert row["deleted_at_utc"] is None

    service.pull_once()
    row = _customers(get_conn)[0]
    assert row["status"] == "active", "status no longer marks deletion as of stage 6b-iii-a"
    assert row["deleted_at_utc"] is not None

    service.pull_once()
    row = _customers(get_conn)[0]
    assert row["status"] == "active"
    assert row["deleted_at_utc"] is None


def test_pull_once_supplier_deleted_at_utc_round_trips_through_soft_delete_then_restore(get_conn):
    # launch-readiness Phase 6 stage 6a-ii: same reasoning as the customer
    # round trip above. RENAMED and REWRITTEN for stage 6b-iii-a -- see the
    # product round trip above for the full reasoning.
    sup_id = str(uuid.uuid4())
    base = {"id": sup_id, "company_id": 1, "name": "Acme Supply Co", "phone": "", "email": "", "address": ""}
    create_event = _pull_event_of("supplier", sup_id, "create", dict(base, row_version=1))
    delete_event = _pull_event_of("supplier", sup_id, "delete", {"id": sup_id, "row_version": 2})
    restore_event = _pull_event_of(
        "supplier", sup_id, "update", dict(base, status="active", deleted_at_utc=None, row_version=3))
    client = FakeRelayClient(pull_responses=[
        {"events": [create_event], "cursor": 1},
        {"events": [delete_event], "cursor": 2},
        {"events": [restore_event], "cursor": 3},
    ])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    row = _suppliers(get_conn)[0]
    assert row["status"] == "active"
    assert row["deleted_at_utc"] is None

    service.pull_once()
    row = _suppliers(get_conn)[0]
    assert row["status"] == "active", "status no longer marks deletion as of stage 6b-iii-a"
    assert row["deleted_at_utc"] is not None

    service.pull_once()
    row = _suppliers(get_conn)[0]
    assert row["status"] == "active"
    assert row["deleted_at_utc"] is None


# ── reorder_request create/accept/decline round trip (2026-08-12) ────────
# feat/reorder-automation-foundation. Unlike category/product/customer/
# supplier above, this entity has no "delete" event type at all (see
# sync_service.py's module docstring) -- only create (the post-sale hook
# opening a request) and update (an accept/decline status change).

def test_pull_once_reorder_request_create_upserts_a_pending_row(get_conn):
    rid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    event = _pull_event_of("reorder_request", rid, "create", {
        "id": rid, "branch_id": 1, "product_id": pid, "status": "pending",
        "draft_message": "Stock low.", "resolved_at": None,
    })
    client = FakeRelayClient(pull_responses=[{"events": [event], "cursor": 1}])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()

    rows = _reorder_requests(get_conn)
    assert len(rows) == 1
    assert rows[0]["id"] == rid
    assert rows[0]["product_id"] == pid
    assert rows[0]["status"] == "pending"
    assert rows[0]["company_id"] == "receiving-company"  # receiving device's own id, never the sender's
    assert rows[0]["resolved_at"] is None


def test_pull_once_reorder_request_round_trips_through_accept(get_conn):
    """The exact flow an Accept action on ANOTHER device produces: a create
    event (status='pending'), then an update event (status='accepted',
    resolved_at set) -- both must be visible here, and there must still be
    only one row, not two."""
    # launch-readiness Phase 6 stage 6a-ii: row_version must strictly
    # increase from create (1) to the accept update (2), same reasoning as
    # the category/product/customer/supplier tests above.
    rid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    create_event = _pull_event_of("reorder_request", rid, "create", {
        "id": rid, "branch_id": 1, "product_id": pid, "status": "pending",
        "draft_message": "Stock low.", "resolved_at": None, "row_version": 1,
    })
    accept_event = _pull_event_of("reorder_request", rid, "update", {
        "id": rid, "branch_id": 1, "product_id": pid, "status": "accepted",
        "draft_message": "Stock low.", "resolved_at": "2026-08-12T00:00:00+00:00", "row_version": 2,
    })
    client = FakeRelayClient(pull_responses=[
        {"events": [create_event], "cursor": 1},
        {"events": [accept_event], "cursor": 2},
    ])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    assert _reorder_requests(get_conn)[0]["status"] == "pending"

    service.pull_once()
    rows = _reorder_requests(get_conn)
    assert len(rows) == 1  # still one row, not two
    assert rows[0]["status"] == "accepted"
    assert rows[0]["resolved_at"] == "2026-08-12T00:00:00+00:00"


def test_pull_once_reorder_request_round_trips_through_decline(get_conn):
    # launch-readiness Phase 6 stage 6a-ii: same reasoning as the accept
    # round trip above.
    rid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    create_event = _pull_event_of("reorder_request", rid, "create", {
        "id": rid, "branch_id": 1, "product_id": pid, "status": "pending",
        "draft_message": "Stock low.", "resolved_at": None, "row_version": 1,
    })
    decline_event = _pull_event_of("reorder_request", rid, "update", {
        "id": rid, "branch_id": 1, "product_id": pid, "status": "declined",
        "draft_message": "Stock low.", "resolved_at": "2026-08-12T00:00:00+00:00", "row_version": 2,
    })
    client = FakeRelayClient(pull_responses=[
        {"events": [create_event], "cursor": 1},
        {"events": [decline_event], "cursor": 2},
    ])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    service.pull_once()

    rows = _reorder_requests(get_conn)
    assert len(rows) == 1
    assert rows[0]["status"] == "declined"


# ── Cross-device company_id bug fix (2026-08-07) ─────────────────────────
# Confirmed live during multi-device testing: a pulled event's own
# `payload["company_id"]` is the SENDING device's company_id (derived
# locally, once, at that device's own onboarding -- see
# onboarding_routes.py::create_admin). Two devices on the SAME license,
# activated with different admin emails, have two different company_id
# values. _apply_event must stamp the RECEIVING device's own company_id
# (from local_company_id_provider) on every pulled row -- never the
# payload's -- or the row becomes permanently invisible to that device's
# own `WHERE company_id=?` queries (retail_api.py::list_categories).

def test_pull_once_stamps_the_receiving_devices_own_company_id_not_the_payloads(get_conn):
    """The exact bug found in live device testing, reproduced directly:
    device A pushed a category with ITS OWN company_id ("company-A") in the
    payload; applying that pulled event on device B (whose own company_id
    is "company-B", supplied via local_company_id_provider, entirely
    independent of the payload) must write the row under "company-B" --
    never "company-A" -- so device B's own list_categories-style
    `WHERE company_id=?` filter (reproduced below against the real
    categories table) finds it."""
    cat_id = str(uuid.uuid4())
    event = _pull_event(
        cat_id, "create",
        {"id": cat_id, "company_id": "company-A", "name": "Beverages", "description": "from device A"},
    )
    client = FakeRelayClient(pull_responses=[{"events": [event], "cursor": 1}])
    service = SyncService(lambda: client, get_conn, lambda: "company-B")

    service.pull_once()

    rows = _categories(get_conn)
    assert len(rows) == 1
    assert rows[0]["id"] == cat_id
    assert rows[0]["company_id"] == "company-B"  # device B's own id, never "company-A"

    # Reproduces list_categories's own `WHERE c.company_id=?` filter against
    # the receiving device's own company_id -- this is the row actually
    # being invisible in the UI that live testing caught.
    conn = get_conn()
    visible = conn.execute("SELECT * FROM categories WHERE company_id=?", ("company-B",)).fetchall()
    invisible_under_senders_id = conn.execute("SELECT * FROM categories WHERE company_id=?", ("company-A",)).fetchall()
    conn.close()
    assert len(visible) == 1
    assert invisible_under_senders_id == []


def test_pull_once_without_a_company_id_provider_raises_instead_of_silently_misfiling(get_conn):
    """A SyncService wired with no local_company_id_provider at all (a
    misconfiguration) must fail loudly the moment a category create/update
    actually needs it -- never fall back to writing the payload's
    (wrong-device) company_id, and never insert a NULL/garbage company_id
    silently. run_once()'s own per-call try/except is what turns this into
    an ordinary retried-next-tick condition for a real caller; pull_once()
    itself must still raise so that swallowing is a deliberate choice made
    one layer up, not baked into apply_pull_result."""
    cat_id = str(uuid.uuid4())
    event = _pull_event(cat_id, "create", {"id": cat_id, "company_id": "company-A", "name": "X", "description": ""})
    client = FakeRelayClient(pull_responses=[{"events": [event], "cursor": 1}])
    service = SyncService(lambda: client, get_conn)  # no local_company_id_provider

    with pytest.raises(RuntimeError):
        service.pull_once()

    # Nothing partially applied, cursor untouched -- same all-or-nothing
    # guarantee as any other mid-apply failure (see the partial-batch test
    # below).
    assert _categories(get_conn) == []
    assert _cursor(get_conn) == 0


# launch-readiness Phase 6 stage 6b-ii (tombstones): SPLIT IN TWO from
# `test_pull_once_delete_only_batch_never_needs_a_company_id_provider`. The
# claim is unchanged and still holds for both entity types -- no delete-only
# batch needs a provider -- but category now reaches that outcome by a
# different route (its cascade derives the tenant from the tombstoned row),
# so the two halves are worth asserting separately rather than leaving
# category's harder guarantee riding on a test named for the easy case. See
# the category test immediately below for the full reasoning, including the
# wedge this arrangement exists to avoid.
def test_pull_once_a_product_delete_only_batch_never_needs_a_company_id_provider(get_conn):
    """A product delete is keyed by products.id alone (a client-generated
    UUID, globally unique) and never writes a company_id, so a batch
    containing only product deletes must apply cleanly even with no
    local_company_id_provider configured."""
    pid = str(uuid.uuid4())
    conn = get_conn()
    conn.execute(
        "INSERT INTO products (id, company_id, sku, name) VALUES (?,?,?,?)",
        (pid, "company-B", "SKU-NOPROV", "Pre-existing"))
    conn.commit()
    conn.close()

    delete_event = _pull_event_of("product", pid, "delete", {"id": pid})
    client = FakeRelayClient(pull_responses=[{"events": [delete_event], "cursor": 1}])
    service = SyncService(lambda: client, get_conn)  # no local_company_id_provider

    service.pull_once()  # must not raise

    rows = _products(get_conn)
    assert len(rows) == 1
    # launch-readiness Phase 6 stage 6b-iii-a: REWRITTEN. This docstring and
    # these assertions used to describe the OPPOSITE of what stage 6b-iii-a
    # made true, and the history is worth keeping rather than deleting,
    # because it is exactly the bug Step 2c closed. Before this stage,
    # product/customer/supplier got NO `deleted_at_utc` fallback for a
    # legacy-shaped payload with no timestamp at all -- `status='inactive'`
    # was written unconditionally instead, and THAT is what hid the row.
    # `delete_product`'s apply branch no longer writes `status` at all, so a
    # bare `p.get("deleted_at_utc")` here would have applied "successfully"
    # (rowcount 1) while leaving this row fully live and visible forever --
    # the exact silent-correctness bug the category branch's own fallback
    # was already written to prevent. The product/customer/supplier delete
    # branches now carry the SAME `p.get("deleted_at_utc") or datetime.now(
    # ...)` fallback the category branch has always had, so `status` stays
    # untouched (still 'active') and `deleted_at_utc` is what actually
    # hides this row.
    assert rows[0]["status"] == "active"
    assert rows[0]["deleted_at_utc"] is not None, \
        "a legacy delete payload with no deleted_at_utc key must still tombstone the row (Step 2c)"


def test_pull_once_a_category_delete_only_batch_never_needs_a_company_id_provider_and_still_cascades(get_conn):
    """The category half of the same guarantee, and the harder one, because
    stage 6b-ii's cascade (`UPDATE products SET category_id=NULL ...`) needs
    a tenant to scope by and a delete-only batch has no provider to ask.

    This test exists because the first cut of that cascade took the tenant
    from `local_company_id`, which quietly made a category-delete-only batch
    -- the common real-world shape, a delete pushed on its own -- the FIRST
    delete batch in this system's history to require a provider.
    `_get_local_company_id` RAISES when none is wired or when onboarding has
    not finished, so that put a throw into the apply loop, and a throw in the
    apply loop aborts before the cursor advances while `run_once` swallows
    it: precisely the silent, permanent, whole-device sync wedge that
    `retail_category_delete_fk_sync_test.py`'s module docstring exists to
    record. Trading a hard-delete wedge for a no-provider wedge would have
    been no fix at all.

    The cascade derives the tenant from the tombstoned category row instead,
    which is guaranteed to still be there precisely because a tombstone is
    not a `DELETE`. So this asserts BOTH halves at once: no raise, and the
    cascade genuinely ran anyway."""
    cat_id = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    conn = get_conn()
    conn.execute("INSERT INTO categories (id, company_id, name, description) VALUES (?,?,?,?)",
                 (cat_id, "company-B", "Pre-existing", ""))
    conn.execute(
        "INSERT INTO products (id, company_id, sku, name, category_id) VALUES (?,?,?,?,?)",
        (pid, "company-B", "SKU-CASCADE", "Assigned", cat_id))
    conn.commit()
    conn.close()

    delete_event = _pull_event(cat_id, "delete", {"id": cat_id})
    client = FakeRelayClient(pull_responses=[{"events": [delete_event], "cursor": 1}])
    service = SyncService(lambda: client, get_conn)  # no local_company_id_provider

    service.pull_once()  # must not raise

    rows = _categories(get_conn)
    assert len(rows) == 1, "a tombstone must not remove the row"
    assert rows[0]["deleted_at_utc"] is not None, "the tombstone must be stamped"
    # The half that proves the tenant was resolved: with `company_id` bound
    # NULL the cascade would match nothing (SQL never equates NULL), so this
    # assertion is what actually fails if the scoping regresses.
    assert _products(get_conn)[0]["category_id"] is None, (
        "the cascade must run without a provider, not silently no-op")
    assert _cursor(get_conn) == 1, "the cursor must advance -- no wedge"


def test_pull_once_ignores_unknown_entity_types(get_conn):
    # "warehouse": genuinely unknown to _apply_event -- "category"/"product"/
    # "customer"/"supplier"/"reorder_request"/"sale"/"sale_item"/"payment"/
    # "return"/"return_item" are all wired in by this point (see retail-
    # catalog-party-sync-expansion's Tasks 1-4 and launch-readiness Phase 5),
    # so any of those would no longer exercise the ignore path this test is
    # actually about. "branch" USED to be the example this test reached for
    # (see git history) on the theory that it was permanently out of scope --
    # Phase 5 wave B (stock-moving sync) proved that theory wrong: `branch`
    # is now a synced entity type (`inventory_movement`'s own sibling, see
    # sync_service.py's module docstring), so it would silently stop
    # exercising THIS test's actual point (an entity_type _apply_event has
    # never heard of) the moment it landed. "warehouse" names nothing this
    # product has ever modeled and is the safe permanent stand-in.
    event = {
        "id": str(uuid.uuid4()), "entity_type": "warehouse", "entity_id": "b-1",
        "event_type": "create", "payload": {"id": "b-1"}, "created_at": "2026-08-06T00:00:00+00:00",
    }
    client = FakeRelayClient(pull_responses=[{"events": [event], "cursor": 5}])
    service = SyncService(lambda: client, get_conn)

    service.pull_once()  # must not raise

    assert _categories(get_conn) == []
    assert _cursor(get_conn) == 5  # cursor still advances -- the event was seen, just not applicable here


def test_pull_once_failure_leaves_cursor_untouched(get_conn):
    client = FakeRelayClient(pull_responses=[NetworkError("NETWORK_UNAVAILABLE", "offline")])
    service = SyncService(lambda: client, get_conn)

    with pytest.raises(NetworkError):
        service.pull_once()

    assert _cursor(get_conn) == 0


def test_pull_once_partial_batch_apply_failure_never_advances_cursor_or_partially_commits(get_conn, monkeypatch):
    """The specific invariant the coordinator flagged: if applying event N
    of a batch raises partway through, event N-1's already-applied write
    must not be left committed, and the cursor must not advance -- the next
    attempt must re-pull and re-apply the WHOLE batch from the same `since`,
    never a gap and never a half-applied batch. Forced deterministically by
    making the second _apply_event call raise, rather than depending on any
    particular real-world payload shape to trigger a DB-level error."""
    cat_id_1 = str(uuid.uuid4())
    cat_id_2 = str(uuid.uuid4())
    event_1 = _pull_event(cat_id_1, "create", {"id": cat_id_1, "company_id": 1, "name": "First", "description": ""})
    event_2 = _pull_event(cat_id_2, "create", {"id": cat_id_2, "company_id": 1, "name": "Second", "description": ""})
    client = FakeRelayClient(pull_responses=[{"events": [event_1, event_2], "cursor": 9}])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    real_apply = SyncService._apply_event
    calls = {"n": 0}

    def _apply_event_second_one_explodes(self, conn, ev, local_company_id=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated failure applying the second event in the batch")
        return real_apply(self, conn, ev, local_company_id, **kwargs)

    monkeypatch.setattr(SyncService, "_apply_event", _apply_event_second_one_explodes)

    with pytest.raises(RuntimeError):
        service.pull_once()

    # Event 1 was applied to the connection before the explosion, but the
    # connection was never committed (pull_once()'s finally: conn.close()
    # with no prior commit() discards the whole transaction) -- so event 1's
    # write must NOT be visible either. Proven against a real sqlite file,
    # not asserted from code inspection.
    assert _categories(get_conn) == []
    assert _cursor(get_conn) == 0  # unchanged -- next attempt re-pulls since=0, the whole batch again


def test_run_once_swallows_push_failure_and_still_attempts_pull(get_conn):
    client = FakeRelayClient(
        push_responses=[NetworkError("NETWORK_UNAVAILABLE", "offline")],
        pull_responses=[{"events": [], "cursor": 0}],
    )
    _insert_outbox_row(get_conn)
    service = SyncService(lambda: client, get_conn)

    service.run_once()  # must not raise

    assert len(client.pull_calls) == 1  # pull still attempted even though push failed


def test_run_once_swallows_pull_failure(get_conn):
    client = FakeRelayClient(pull_responses=[NetworkError("NETWORK_UNAVAILABLE", "offline")])
    service = SyncService(lambda: client, get_conn)

    service.run_once()  # must not raise


# ── Health tracking ───────────────────────────────────────────────────────

def test_run_once_records_push_failure_and_pull_success_independently(get_conn):
    client = FakeRelayClient(
        push_responses=[NetworkError("NETWORK_UNAVAILABLE", "offline")],
        pull_responses=[{"events": [], "cursor": 0}],
    )
    _insert_outbox_row(get_conn)
    service = SyncService(lambda: client, get_conn)

    service.run_once()

    health = service.get_health()
    assert health["push"]["healthy"] is False
    assert health["push"]["consecutive_failures"] == 1
    assert health["pull"]["healthy"] is True
    assert health["pull"]["consecutive_failures"] == 0
    assert health["healthy"] is False  # overall is False when either half is unhealthy


def test_consecutive_failures_increments_then_resets_on_success(get_conn):
    client = FakeRelayClient(
        pull_responses=[
            NetworkError("NETWORK_UNAVAILABLE", "offline"),
            NetworkError("NETWORK_UNAVAILABLE", "offline"),
            {"events": [], "cursor": 0},
        ],
    )
    service = SyncService(lambda: client, get_conn)

    service.run_once()
    assert service.get_health()["pull"]["consecutive_failures"] == 1
    service.run_once()
    assert service.get_health()["pull"]["consecutive_failures"] == 2
    service.run_once()
    health = service.get_health()
    assert health["pull"]["consecutive_failures"] == 0
    assert health["pull"]["healthy"] is True


def test_health_failure_reason_is_the_relay_reason_code_never_a_raw_message(get_conn):
    client = FakeRelayClient(
        pull_responses=[NetworkError("NETWORK_UNAVAILABLE", "offline relay at https://owner.example.invalid/api/sync/v1/pull")],
    )
    service = SyncService(lambda: client, get_conn)

    service.run_once()

    reason = service.get_health()["pull"]["last_failure_reason"]
    assert reason == "NETWORK_UNAVAILABLE"
    assert "owner.example.invalid" not in reason


def test_get_health_reports_zero_pending_count_on_an_empty_outbox(get_conn):
    service = SyncService(lambda: FakeRelayClient(), get_conn)

    assert service.get_health()["pending_count"] == 0


def test_get_health_pending_count_reflects_unsynced_outbox_rows(get_conn):
    service = SyncService(lambda: FakeRelayClient(), get_conn)
    _insert_outbox_row(get_conn)
    _insert_outbox_row(get_conn)

    assert service.get_health()["pending_count"] == 2


def test_get_health_pending_count_drops_to_zero_after_a_successful_push(get_conn):
    """The freshness indicator's "N pending" must track real outbox state,
    not a cached/derived counter -- pending_count is a live COUNT(*), so it
    has to fall the moment push_once() acks the rows it just sent."""
    _insert_outbox_row(get_conn)
    client = FakeRelayClient(push_responses=[{"stored": 1, "received": 1}])
    service = SyncService(lambda: client, get_conn)
    assert service.get_health()["pending_count"] == 1

    service.push_once()

    assert service.get_health()["pending_count"] == 0


def test_next_interval_backs_off_exponentially_and_caps(get_conn):
    client = FakeRelayClient()
    service = SyncService(lambda: client, get_conn)

    service._health["push"]["consecutive_failures"] = 1
    assert 10.0 <= service._next_interval_seconds() <= 12.0

    service._health["push"]["consecutive_failures"] = 3
    assert 40.0 <= service._next_interval_seconds() <= 48.0

    service._health["push"]["consecutive_failures"] = 99
    assert service._next_interval_seconds() <= 300.0

    service._health["push"]["consecutive_failures"] = 5000
    assert service._next_interval_seconds() <= 300.0  # must not raise OverflowError


# ── start()/stop() lifecycle ─────────────────────────────────────────────

def test_start_and_stop_lifecycle_does_not_raise(get_conn):
    client = FakeRelayClient(pull_responses=[{"events": [], "cursor": 0}] * 5)
    service = SyncService(lambda: client, get_conn)
    service.start(interval_seconds=3600)
    service.stop()  # must cancel cleanly, no dangling timer firing later


# ── nudge() / module-level registration ─────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_active_service():
    unregister_active_service()
    yield
    unregister_active_service()


def test_nudge_with_no_registered_service_is_a_harmless_noop():
    nudge()  # must not raise, must not spawn anything observable


def test_get_active_health_with_no_registered_service_reports_not_configured():
    assert get_active_health() == {"configured": False}


def test_get_active_health_reflects_the_registered_services_own_health(get_conn):
    client = FakeRelayClient(pull_responses=[{"events": [], "cursor": 0}])
    service = SyncService(lambda: client, get_conn)
    register_active_service(service)

    service.run_once()

    health = get_active_health()
    assert health["configured"] is True
    assert health["healthy"] is True


def test_nudge_fires_push_once_without_blocking_the_caller(get_conn):
    """Proves nudge() is a real fire-and-forget dispatch, not an inline
    call: push() blocks on an Event the test controls, and nudge() must
    return long before that Event is ever set."""
    _insert_outbox_row(get_conn)
    release = threading.Event()
    entered = threading.Event()

    class _BlockingClient:
        def push(self, events):
            entered.set()
            release.wait(timeout=5)
            return {"stored": len(events), "received": len(events)}

        def pull(self, since):
            return {"events": [], "cursor": since}

    client = _BlockingClient()
    service = SyncService(lambda: client, get_conn)
    register_active_service(service)

    start = time.monotonic()
    nudge()
    elapsed = time.monotonic() - start

    assert elapsed < 0.5  # returned immediately, did not wait for push() to finish
    assert entered.wait(timeout=2)  # but the background thread really did start pushing

    release.set()  # let it finish so the test doesn't leak a hung thread
    # Give the background thread a moment to actually finish and drain the outbox.
    for _ in range(50):
        if _outbox_rows(get_conn) == []:
            break
        time.sleep(0.05)
    assert _outbox_rows(get_conn) == []


def test_nudge_and_a_concurrent_scheduled_tick_never_run_push_once_at_the_same_time(get_conn):
    """Real concurrency test (not code inspection): SyncService's internal
    lock must serialize push_once() calls made from two different threads
    (the nudge() background thread and what would otherwise be the 10s
    timer's own thread) -- proves the FakeRelayClient never observes two
    overlapping push() calls."""
    _insert_outbox_row(get_conn)
    _insert_outbox_row(get_conn)

    concurrent_calls = {"active": 0, "max_active": 0}
    lock_for_counter = threading.Lock()

    class _CountingClient:
        def push(self, events):
            with lock_for_counter:
                concurrent_calls["active"] += 1
                concurrent_calls["max_active"] = max(concurrent_calls["max_active"], concurrent_calls["active"])
            time.sleep(0.15)  # hold the "critical section" long enough for a real race to show up
            with lock_for_counter:
                concurrent_calls["active"] -= 1
            return {"stored": len(events), "received": len(events)}

        def pull(self, since):
            return {"events": [], "cursor": since}

    client = _CountingClient()
    service = SyncService(lambda: client, get_conn)
    register_active_service(service)

    # Thread A: simulates the scheduled timer tick calling run_once()/push_once()
    # directly. Thread B: a route-triggered nudge(), which spawns its OWN
    # background thread internally.
    thread_a = threading.Thread(target=service.push_once)
    thread_a.start()
    nudge()  # thread B, via the module-level dispatch
    thread_a.join(timeout=5)

    # nudge()'s own background thread may still be finishing (it no-ops once
    # thread_a already drained the outbox, but wait for it to be sure the
    # counter's final state is settled before asserting).
    for _ in range(50):
        if concurrent_calls["active"] == 0:
            break
        time.sleep(0.05)

    assert concurrent_calls["max_active"] <= 1  # never two push() calls in flight at once


# ── Phase 5: money-moving sync (sale/sale_item/payment/return/return_item) ──
#
# Unlike categories/products/customers/suppliers above, these five are
# APPLIED directly via `apply_pull_result(conn, result)` rather than driven
# through a fake relay's push/pull round trip -- exactly the same technique
# `test_pulled_customer_delete_soft_deletes_and_never_touches_a_row_this_
# device_still_uses` (products/retail/tests/retail_customer_sync_test.py)
# already uses for its own single-layer apply-side proof. The composed,
# real-relay, real-two-install version of the SAME claims lives in
# products/retail/tests/retail_money_sync_test.py; this file is the
# per-branch unit layer underneath it.

def _sale_event(uid=None, **overrides):
    uid = uid or str(uuid.uuid4())
    payload = {
        "uid": uid, "sale_number": "SALE-1", "branch_id": 1, "customer_id": None,
        "cashier": "POS", "subtotal": 100.0, "discount_amount": 0.0, "tax_amount": 0.0,
        "total": 100.0, "amount_paid": 100.0, "change_amount": 0.0,
        "payment_method": "cash", "status": "completed", "notes": "",
        "created_at": "2026-08-25 10:00:00", "due_date": None,
        "actor_user_uid": "actor-A", "terminal_id": "TERM-A",
        "created_at_utc": "2026-08-25T10:00:00+00:00",
    }
    payload.update(overrides)
    # event_type defaults to "create" and is set on the RETURNED dict
    # directly by callers that want a non-create event (see
    # test_non_create_events_are_ignored_for_immutable_money_entities) --
    # never via a kwarg here, so there is no ambiguity about whether
    # "event_type" belongs in the payload (it never does) or the envelope.
    return {"id": str(uuid.uuid4()), "entity_type": "sale", "entity_id": uid,
            "event_type": "create", "payload": payload}


def _sale_item_event(sale_uid, uid=None, **overrides):
    uid = uid or str(uuid.uuid4())
    payload = {
        "uid": uid, "sale_uid": sale_uid, "product_id": 1, "quantity": 2.0,
        "unit_price": 50.0, "discount_pct": 0.0, "tax_rate": 0.0, "line_total": 100.0,
    }
    payload.update(overrides)
    return {"id": str(uuid.uuid4()), "entity_type": "sale_item", "entity_id": uid,
            "event_type": "create", "payload": payload}


def _payment_event(sale_uid=None, uid=None, **overrides):
    uid = uid or str(uuid.uuid4())
    payload = {
        "uid": uid, "reference": "RCPT-1", "party_type": None, "party_id": None,
        "direction": "in", "amount": 100.0, "currency": "USD", "method": "cash",
        "related_type": "sale" if sale_uid else None, "related_id": 1 if sale_uid else None,
        "sale_uid": sale_uid, "notes": "", "status": "active", "created_by": "cashier-A",
        "device": None, "created_at": "2026-08-25 10:00:00",
    }
    payload.update(overrides)
    return {"id": str(uuid.uuid4()), "entity_type": "payment", "entity_id": uid,
            "event_type": "create", "payload": payload}


def _return_event(sale_uid, uid=None, **overrides):
    uid = uid or str(uuid.uuid4())
    payload = {
        "uid": uid, "sale_uid": sale_uid, "return_number": "RET-1", "branch_id": 1,
        "cashier": "POS", "reason": "test return", "refund_method": "cash",
        "refund_amount": 50.0, "status": "completed", "created_at": "2026-08-25 11:00:00",
        "actor_user_uid": "actor-A", "terminal_id": "TERM-A",
        "created_at_utc": "2026-08-25T11:00:00+00:00",
    }
    payload.update(overrides)
    return {"id": str(uuid.uuid4()), "entity_type": "return", "entity_id": uid,
            "event_type": "create", "payload": payload}


def _return_item_event(return_uid, uid=None, **overrides):
    uid = uid or str(uuid.uuid4())
    payload = {
        "uid": uid, "return_uid": return_uid, "product_id": 1, "quantity": 1.0,
        "unit_price": 50.0, "line_total": 50.0,
    }
    payload.update(overrides)
    return {"id": str(uuid.uuid4()), "entity_type": "return_item", "entity_id": uid,
            "event_type": "create", "payload": payload}


def _apply(get_conn, events, cursor=1, local_company_id="money-co"):
    """One-shot apply of a pre-built event batch, mirroring exactly what a
    real pull would hand `apply_pull_result` -- committed immediately so the
    next call in the same test sees durable state, matching how
    `pull_once()` itself commits after every batch."""
    conn = get_conn()
    service = SyncService(client_factory=lambda: None, get_conn=get_conn,
                          local_company_id_provider=lambda: local_company_id)
    try:
        service.apply_pull_result(conn, {"events": events, "cursor": cursor})
        conn.commit()
    finally:
        conn.close()
    return service


def _sales(get_conn):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM sales").fetchall()]
    conn.close()
    return rows


def _sale_items(get_conn):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM sale_items").fetchall()]
    conn.close()
    return rows


def _returns(get_conn):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM returns").fetchall()]
    conn.close()
    return rows


def _return_items(get_conn):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM return_items").fetchall()]
    conn.close()
    return rows


def _payments(get_conn):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM payments").fetchall()]
    conn.close()
    return rows


def _quarantine(get_conn):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM sync_apply_quarantine").fetchall()]
    conn.close()
    return rows


# ── 1. All five money-moving types are recognized (allowlist widening) ─────

def test_all_five_money_moving_entity_types_are_individually_recognized_not_skipped(get_conn):
    """MUTATION PROOF for 'widen only ONE of the two allowlist places' (see
    the phase brief's mutation-proof #5) -- and, more directly, for the
    entity_type membership tuple itself. If `_apply_event`'s gate omitted
    even ONE of these five (the shape "widen the docstring/local_company_id
    check but forget the actual `if entity_type not in (...)` gate, or vice
    versa, would produce), that type's branch is never reached at all and
    its table stays permanently empty -- exactly the silent-skip behaviour
    `test_unknown_entity_type_in_outbox_is_silently_skipped_by_apply_side`
    (retail_two_install_roundtrip_test.py) already proves for a genuinely
    unrecognized type. Each of the five is applied here with everything its
    own parent-resolution needs already present, so a row NOT appearing is
    attributable only to the entity_type gate, not to an orphan parking."""
    # The line items below reference LINE_ITEM_PRODUCT_ID, and
    # sale_items/return_items declare a real FK to `products` (as the
    # shipping schema does) -- so the product has to be here first, the
    # same way real traffic can only ever sell a product the device
    # already has.
    _seed_product(get_conn)
    sale_uid = str(uuid.uuid4())
    _apply(get_conn, [_sale_event(uid=sale_uid)], cursor=1)
    assert len(_sales(get_conn)) == 1, "entity_type=sale was silently skipped"

    _apply(get_conn, [_sale_item_event(sale_uid)], cursor=2)
    assert len(_sale_items(get_conn)) == 1, "entity_type=sale_item was silently skipped"

    _apply(get_conn, [_payment_event(sale_uid=sale_uid)], cursor=3)
    assert len(_payments(get_conn)) == 1, "entity_type=payment was silently skipped"

    return_uid = str(uuid.uuid4())
    _apply(get_conn, [_return_event(sale_uid, uid=return_uid)], cursor=4)
    assert len(_returns(get_conn)) == 1, "entity_type=return was silently skipped"

    _apply(get_conn, [_return_item_event(return_uid)], cursor=5)
    assert len(_return_items(get_conn)) == 1, "entity_type=return_item was silently skipped"


# ── 2. Idempotency: ON CONFLICT(uid) DO NOTHING, never DO UPDATE ───────────

def test_applying_the_same_money_batch_three_times_never_doubles_the_takings(get_conn):
    """THE headline hazard. `uid` is the wire idempotency key for all five
    money-moving tables -- a re-delivered batch, a replayed quarantined
    event, or a device that pulls the same cursor range twice must all be
    no-ops. Applied 3 times (not 2 -- twice could coincidentally look right
    if a bug only doubled on the SECOND application and not the third)."""
    # The line items below reference LINE_ITEM_PRODUCT_ID, and
    # sale_items/return_items declare a real FK to `products` (as the
    # shipping schema does) -- so the product has to be here first, the
    # same way real traffic can only ever sell a product the device
    # already has.
    _seed_product(get_conn)
    sale_uid = str(uuid.uuid4())
    events = [_sale_event(uid=sale_uid), _sale_item_event(sale_uid), _payment_event(sale_uid=sale_uid)]

    for attempt in range(3):
        _apply(get_conn, events, cursor=attempt + 1)

    sales = _sales(get_conn)
    items = _sale_items(get_conn)
    pays = _payments(get_conn)
    assert len(sales) == 1, f"sale row count after 3 applies: {len(sales)}"
    assert len(items) == 1, f"sale_item row count after 3 applies: {len(items)}"
    assert len(pays) == 1, f"payment row count after 3 applies: {len(pays)}"
    # The actual money, not just row counts -- a hypothetical bug that
    # inserted 3 rows but with amount=0 on the duplicates would pass a
    # row-count-only assertion while still being wrong in a different way.
    assert sum(p["amount"] for p in pays) == 100.0, "applying the batch 3 times changed total takings"
    assert sales[0]["total"] == 100.0


# ── 3. company_id is always the RECEIVING device's own ─────────────────────

def test_pulled_sale_is_stamped_with_the_receiving_devices_own_company_id(get_conn):
    """Same cross-device company_id bug already covered for categories
    (test_pull_apply_stamps_the_receiving_devices_own_company_id_not_the_
    payloads in test_internal_routes.py), proven again for `sale`. The
    payload here never legitimately carries a `company_id` key at all (see
    retail_api.py's create_sale emission comment) -- a bogus one is injected
    anyway, to prove `_apply_event` never even looks at it."""
    sale_uid = str(uuid.uuid4())
    _apply(get_conn, [_sale_event(uid=sale_uid, company_id="attacker-company")], cursor=1,
           local_company_id="the-real-receiving-company")
    row = _sales(get_conn)[0]
    assert row["company_id"] == "the-real-receiving-company"


# ── 4. session_id: hazard #2, never re-attributed to a local drawer ────────

def test_pulled_sale_session_id_is_always_null_never_a_local_drawer(get_conn):
    """Hazard #2 from the phase brief, at the unit layer: `cash_sessions` is
    not a synced entity, so NO value of `session_id` can ever be valid on a
    pulled sale -- not the payload's (there is no legitimate one -- see
    retail_api.py's emission comment), and above all not a lookup of
    whatever session happens to be open locally right now. A bogus
    `session_id` is injected into the payload to prove it is ignored outright
    -- if `_apply_event` ever started reading it, this would either write the
    bogus value (this assertion) or raise a real FK violation (this table's
    own `session_id REFERENCES cash_sessions(id)`, which has zero rows in
    this fixture -- see the schema's own comment)."""
    _apply(get_conn, [_sale_event(session_id="some-other-devices-local-session-uuid")], cursor=1)
    row = _sales(get_conn)[0]
    assert row["session_id"] is None

    # And the identical claim for `returns.session_id`. A DISTINCT
    # sale_number from the sale above -- `sales.sale_number` carries its own
    # bare UNIQUE constraint, unrelated to the `uid` conflict target this
    # test isn't exercising here, so two real sales in one test must not
    # share the fixture helper's default.
    sale_uid = str(uuid.uuid4())
    _apply(get_conn, [_sale_event(uid=sale_uid, sale_number="SALE-2")], cursor=2)
    _apply(get_conn, [_return_event(sale_uid, session_id="some-other-devices-local-session-uuid")], cursor=3)
    ret_row = [r for r in _returns(get_conn) if r["sale_id"] is not None][0]
    assert ret_row["session_id"] is None


# ── 5. Actor/terminal ARE preserved -- the opposite rule from session_id ───

def test_pulled_sale_keeps_its_originating_actor_and_terminal(get_conn):
    """The contrast case to session_id above: actor_user_uid/terminal_id/
    created_at_utc must NOT be reset, unlike session_id -- attributing
    another till's sale to the receiving device's own cashier/terminal would
    be a fabricated audit fact (phase brief, 'ALSO REQUIRED')."""
    _apply(get_conn, [_sale_event(actor_user_uid="cashier-on-the-phone", terminal_id="PHONE-TERM-9",
                                   created_at_utc="2026-08-25T09:30:00+00:00")], cursor=1)
    row = _sales(get_conn)[0]
    assert row["actor_user_uid"] == "cashier-on-the-phone"
    assert row["terminal_id"] == "PHONE-TERM-9"
    assert row["created_at_utc"] == "2026-08-25T09:30:00+00:00"


# ── 6. Immutability: update/delete are ignored for all five, never applied ─

@pytest.mark.parametrize("entity_type,builder", [
    ("sale", lambda: _sale_event()),
    ("return", lambda: _return_event("nonexistent-parent-uid")),
])
def test_non_create_events_are_ignored_for_immutable_money_entities(get_conn, entity_type, builder):
    """A sale/return is corrected by a NEW fact (a return), never edited or
    deleted in place -- see the module docstring's Phase 5 note. An
    update/delete event reaching this branch must be a silent no-op, not an
    error and not a write."""
    for bad_event_type in ("update", "delete"):
        ev = builder()
        ev["event_type"] = bad_event_type
        _apply(get_conn, [ev], cursor=1)
    assert _sales(get_conn) == []
    assert _returns(get_conn) == []


# ── 7. Orphans: park, don't drop, don't wedge the batch ────────────────────

def test_sale_item_with_no_matching_parent_is_quarantined_not_dropped_or_raised(get_conn):
    """The third hazard: Owner's own push-side quarantine (681b0fa) can skip
    a malformed PARENT event while still relaying its already-valid
    children. `apply_pull_result` must neither raise (which would wedge the
    cursor and every event behind this one, forever) nor silently drop the
    child (money vanishing with no trace)."""
    service = _apply(get_conn, [_sale_item_event("no-such-sale-uid-exists")], cursor=1)
    assert _sale_items(get_conn) == [], "the orphan was applied anyway -- its parent check did not run"

    parked = _quarantine(get_conn)
    assert len(parked) == 1
    assert parked[0]["entity_type"] == "sale_item"
    assert parked[0]["reason"] == "missing_parent:sale"
    assert "no-such-sale-uid-exists" in parked[0]["detail"]
    del service  # unused beyond documenting apply_pull_result did not raise


def test_return_item_with_no_matching_parent_is_quarantined(get_conn):
    _apply(get_conn, [_return_item_event("no-such-return-uid-exists")], cursor=1)
    assert _return_items(get_conn) == []
    parked = _quarantine(get_conn)
    assert len(parked) == 1
    assert parked[0]["entity_type"] == "return_item"
    assert parked[0]["reason"] == "missing_parent:return"


@pytest.mark.parametrize("entity_type, item_builder, rows_reader", [
    ("sale_item", _sale_item_event, _sale_items),
    ("return_item", _return_item_event, _return_items),
])
def test_a_line_item_with_NO_product_id_at_all_is_quarantined_not_waved_through(
        get_conn, entity_type, item_builder, rows_reader):
    """Added by the verification pass. The product guard in `_apply_event`'s
    sale_item/return_item branches is deliberately written as
    `if not self._row_exists(conn, "products", p.get("product_id"))` -- a
    FALSY product_id (absent, None, or empty) is parked together with a
    dangling one, because `sale_items.product_id`/`return_items.product_id`
    are both `INTEGER NOT NULL`: a payload missing the field raises
    `sqlite3.IntegrityError: NOT NULL constraint failed` and wedges the whole
    pull batch exactly as surely as a real FK violation does. That is the
    reason the branch's own inline comment gives for NOT writing the
    tempting-looking `if p.get("product_id") is not None and not
    _row_exists(...)`: an `is not None` escape hatch would treat "the field
    is missing" as "there is nothing to check" and wave the row straight
    into the INSERT that cannot accept it.

    THIS TEST EXISTS BECAUSE NOTHING ELSE COVERED THAT. Verified directly:
    reintroducing the `is not None` escape hatch at BOTH branches left this
    file green at 57/57 and products/retail/tests/retail_money_sync_test.py
    green at 13/13. The neighbouring orphan tests all supply a perfectly
    good `product_id` and vary the PARENT uid instead, and
    `test_a_line_item_whose_product_never_arrived_is_quarantined_not_wedged`
    (retail_money_sync_test.py) drops the product ROW while still sending
    the id -- so the missing-FIELD shape, which is the one the escape hatch
    lets through, had no test at all. This is the same defect class the
    previous round of this phase shipped once already.

    Parametrized over both line-item types because the guard is duplicated
    in both branches and a fix applied to only one of them is a live wedge
    on the other.
    """
    _seed_product(get_conn)
    # Build the REAL parent chain rather than a stub: a return whose own
    # `sale_uid` does not resolve is itself quarantined by the branch above
    # this one, which would make the assertions below pass for entirely the
    # wrong reason.
    sale = _sale_event()
    batch = [sale]
    if entity_type == "return_item":
        batch.append(_return_event(sale["payload"]["uid"]))
    _apply(get_conn, batch, cursor=1)
    assert _quarantine(get_conn) == [], \
        "the parent chain itself did not apply -- this test cannot isolate the product guard"
    parent_uid = batch[-1]["payload"]["uid"]

    # The ONLY thing wrong with this event is the absent product_id -- its
    # parent resolves, and every other column is well-formed -- so a
    # quarantine here can only be the product guard's doing.
    orphan = item_builder(parent_uid)
    del orphan["payload"]["product_id"]

    # Must NOT raise: raising is precisely the wedge (cursor never advances,
    # every event behind this one re-fails forever, from every device).
    _apply(get_conn, [orphan], cursor=2)

    assert rows_reader(get_conn) == [], (
        f"a {entity_type} with NO product_id was applied anyway -- an "
        "INTEGER NOT NULL column was written from a missing payload field")
    parked = _quarantine(get_conn)
    assert len(parked) == 1, f"expected exactly one parked row, got {parked}"
    assert parked[0]["entity_type"] == entity_type
    assert parked[0]["reason"] == "missing_parent:product", (
        "an absent product_id must be parked under the SAME reason vocabulary "
        f"a dangling one is, not silently waved through: {dict(parked[0])}")
    # Still replayable, like every other parked row -- the payload is stored
    # verbatim so an operator can see exactly what arrived.
    assert json.loads(parked[0]["payload"])["uid"] == orphan["payload"]["uid"]


def test_payment_tied_to_a_missing_sale_is_quarantined(get_conn):
    _apply(get_conn, [_payment_event(sale_uid="no-such-sale-uid-exists")], cursor=1)
    assert _payments(get_conn) == []
    parked = _quarantine(get_conn)
    assert len(parked) == 1
    assert parked[0]["entity_type"] == "payment"
    assert parked[0]["reason"] == "missing_parent:sale"


def test_a_payment_not_tied_to_any_sale_never_touches_the_quarantine_path(get_conn):
    """A customer/supplier account payment (related_type != 'sale') has no
    parent to resolve at all -- it must apply directly, first try, never
    routed through the orphan check."""
    _apply(get_conn, [_payment_event(sale_uid=None, related_type=None, related_id=None,
                                     party_type="customer", party_id="cust-1")], cursor=1)
    assert len(_payments(get_conn)) == 1
    assert _quarantine(get_conn) == []


def test_quarantined_child_resolves_once_its_parent_arrives_in_a_later_batch(get_conn):
    """The replay path. A quarantined sale_item must not stay lost forever:
    once its parent sale is applied (e.g. an operator replayed the
    once-quarantined parent event from Owner's console), the very next pull
    that carries it resolves the parked child in the SAME tick."""
    # The line items below reference LINE_ITEM_PRODUCT_ID, and
    # sale_items/return_items declare a real FK to `products` (as the
    # shipping schema does) -- so the product has to be here first, the
    # same way real traffic can only ever sell a product the device
    # already has.
    _seed_product(get_conn)
    sale_uid = str(uuid.uuid4())
    item_uid = str(uuid.uuid4())
    _apply(get_conn, [_sale_item_event(sale_uid, uid=item_uid)], cursor=1)
    assert _sale_items(get_conn) == []
    assert len(_quarantine(get_conn)) == 1

    # The parent arrives in a LATER batch, alongside something unrelated --
    # proving the retry doesn't need the parent and child in the same batch.
    _apply(get_conn, [_sale_event(uid=sale_uid)], cursor=2)

    assert len(_sale_items(get_conn)) == 1, "the parked child was never retried after its parent resolved"
    resolved_item = _sale_items(get_conn)[0]
    assert resolved_item["uid"] == item_uid
    assert _quarantine(get_conn) == [], "the resolved orphan was left sitting in quarantine"


def test_a_still_unresolved_orphan_is_not_duplicated_across_repeated_retries(get_conn):
    """`_quarantine_apply_event` keys on `(entity_id, event_type)` -- the
    child's own wire uid plus which kind of event it is, see sync_
    apply_quarantine's own CREATE TABLE comment for why entity_id alone
    would be too coarse -- as sync_apply_quarantine's PRIMARY KEY
    specifically so that re-parking the SAME still-blocked event on every
    retry tick is a no-op, not an ever-growing pile of rows for one stuck
    event."""
    item_uid = str(uuid.uuid4())
    _apply(get_conn, [_sale_item_event("still-missing-parent", uid=item_uid)], cursor=1)
    # Three more pulls, each carrying nothing relevant -- each one still
    # retries the existing quarantine table (see apply_pull_result).
    for cursor in (2, 3, 4):
        _apply(get_conn, [_sale_event(sale_number=f"unrelated-{cursor}")], cursor=cursor)

    parked = _quarantine(get_conn)
    assert len(parked) == 1, f"the same stuck orphan was re-parked as a duplicate row: {parked}"
    assert parked[0]["entity_id"] == item_uid


def test_quarantine_key_is_entity_id_and_event_type_not_entity_id_alone(get_conn):
    """Cheap insurance, not (yet) reachable through `_apply_event`: a
    `payment`'s `create` and its later `void` share ONE `entity_id` (the
    payment's own wire uid) but are DIFFERENT events. If the primary key
    were `entity_id` alone, quarantining the second of the two would
    `INSERT OR IGNORE` right past the first -- silently leaving whichever
    one arrived first stuck forever with no error, no second row, nothing
    to retry. Keying on `(entity_id, event_type)` instead means both are
    tracked, and each resolves independently.

    Talks to `_quarantine_apply_event` directly (bypassing `_apply_event`)
    because today's ONLY quarantinable payment path -- a sale-tied payment
    -- is refused by `void` before it would ever reach here (see that
    route's own guard); this proves the TABLE's own key shape holds
    regardless of whether any current caller happens to exercise both event
    types for one entity_id yet.
    """
    same_id = str(uuid.uuid4())
    conn = get_conn()
    try:
        SyncService._quarantine_apply_event(
            conn, {"entity_id": same_id, "entity_type": "payment", "event_type": "create", "payload": {}},
            reason="missing_parent:sale", detail="create arrived first",
        )
        SyncService._quarantine_apply_event(
            conn, {"entity_id": same_id, "entity_type": "payment", "event_type": "void", "payload": {}},
            reason="missing_parent:sale", detail="void arrived second",
        )
        conn.commit()
        rows = {r["event_type"]: r for r in conn.execute(
            "SELECT * FROM sync_apply_quarantine WHERE entity_id=?", (same_id,)).fetchall()}
        assert set(rows) == {"create", "void"}, (
            f"expected BOTH event types parked under the same entity_id, got {set(rows)} -- "
            "one silently clobbered the other")

        # Resolving one (the real _retry_quarantined_events shape: DELETE
        # keyed on both columns) must not touch the other.
        conn.execute("DELETE FROM sync_apply_quarantine WHERE entity_id=? AND event_type=?",
                     (same_id, "create"))
        conn.commit()
        remaining = [dict(r) for r in conn.execute(
            "SELECT * FROM sync_apply_quarantine WHERE entity_id=?", (same_id,)).fetchall()]
        assert len(remaining) == 1 and remaining[0]["event_type"] == "void", (
            f"deleting the resolved 'create' event also removed (or left extra) rows: {remaining}")
    finally:
        conn.close()


def test_unresolved_branch_fallback_logs_one_summary_warning_per_batch_not_per_row(get_conn, caplog):
    """A per-row WARNING for every pulled sale whose `branch_uid` doesn't
    resolve locally (the ordinary steady state -- `branch` itself doesn't
    sync, see `_resolve_branch_id`'s own docstring) is, on a busy
    multi-device install, the same as no log at all: nobody reads a WARNING
    line repeated thousands of times a day. This batch has THREE such sales;
    the fix must emit exactly ONE WARNING for the whole batch, not three --
    while still naming the discarded uid and the exact phrase a per-row line
    would have used ('did not resolve'), so nothing that used to grep for
    that text on a small batch silently stops matching.
    """
    same_bad_uid = "unresolvable-branch-uid"
    events = [_sale_event(branch_uid=same_bad_uid, sale_number=f"SALE-BF-{i}") for i in range(3)]
    with caplog.at_level("WARNING", logger="commercial_runtime.sync.sync_service"):
        _apply(get_conn, events, cursor=1)

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    branch_warnings = [m for m in warnings if "did not resolve" in m]
    assert len(branch_warnings) == 1, (
        f"expected exactly ONE consolidated WARNING for a 3-row batch, got "
        f"{len(branch_warnings)}: {branch_warnings}")
    assert same_bad_uid in branch_warnings[0]
    assert "3" in branch_warnings[0], (
        f"the summary must still say HOW MANY rows fell back: {branch_warnings[0]}")

    # And all three sales actually landed -- the summary is purely additive
    # logging, never a reason to drop or delay the rows themselves.
    assert len(_sales(get_conn)) == 3


# ── 8. Products/customers stay untouched by any of this ────────────────────

def test_money_entity_apply_never_touches_the_pre_existing_five_catalogue_types(get_conn):
    """Scope discipline, proven rather than assumed: applying a full
    sale+sale_item+payment+return+return_item batch must not write a single
    row into categories/products/customers/suppliers/reorder_requests."""
    # The line items below reference LINE_ITEM_PRODUCT_ID, and
    # sale_items/return_items declare a real FK to `products` (as the
    # shipping schema does) -- so the product has to be here first, the
    # same way real traffic can only ever sell a product the device
    # already has.
    _seed_product(get_conn)

    tables = ("categories", "products", "customers", "suppliers", "reorder_requests")

    def counts():
        conn = get_conn()
        try:
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
        finally:
            conn.close()

    # Compared BEFORE against AFTER rather than asserted equal to zero:
    # `products` is no longer empty at the start (see SEEDED_PRODUCT_ID -- the
    # line-item tables declare a real FK to it, exactly as the shipping schema
    # does). "Unchanged by this batch" is the claim this test always meant and
    # is strictly stronger than "empty" anyway: a batch that both added and
    # removed a row would satisfy the old assertion and fail this one.
    before = counts()
    sale_uid = str(uuid.uuid4())
    return_uid = str(uuid.uuid4())
    _apply(get_conn, [
        _sale_event(uid=sale_uid), _sale_item_event(sale_uid), _payment_event(sale_uid=sale_uid),
        _return_event(sale_uid, uid=return_uid), _return_item_event(return_uid),
    ], cursor=1)
    after = counts()
    for table in tables:
        assert after[table] == before[table], (
            f"{table} changed from {before[table]} to {after[table]} "
            f"on a money-entity-only batch")


# ── Phase 5 wave B2 stage 1: cross-stream entity-type allowlist ────────────
#
# A second SyncService instance will eventually pull the SAME relay stream
# from a DIFFERENT database (registry.db, syncing `users` -- see
# sync_service.py's module docstring "Two-stream design" paragraph and
# docs/launch-readiness/phase5-waveb2-user-sync.md). `_apply_event`
# dispatches on entity_type alone, so each instance has to know which events
# are its OWN, or a foreign-stream event reaching a branch that writes a
# table THIS instance's database doesn't have would wedge every device's
# cursor forever (wave A's defect #1, reproduced by construction the moment
# a second stream exists).
#
# Stage 1 adds no new entity type at all -- "product" and "customer" stand
# in here for "a type this instance handles" and "a type belonging to the
# OTHER stream" (both are already fully wired in this fixture, with real
# tables and helpers above), proving the `handled_entity_types` mechanism
# itself is genuinely parameterised rather than merely "ignore everything
# unusual".

def _product_event(product_id=None, **overrides):
    product_id = product_id or str(uuid.uuid4())
    payload = {
        "id": product_id, "company_id": 1, "sku": f"SKU-{product_id[:8]}", "barcode": "",
        "name": "Widget", "category_id": None, "supplier_id": None, "cost_price": 1.0,
        "sell_price": 2.0, "tax_rate": 0, "unit": "pcs", "reorder_level": 5,
        "reorder_method": "none", "status": "active",
    }
    payload.update(overrides)
    return _pull_event_of("product", product_id, "create", payload)


def _customer_event(customer_id=None, **overrides):
    customer_id = customer_id or str(uuid.uuid4())
    payload = {
        "id": customer_id, "company_id": 1, "name": "Alice", "phone": "",
        "email": "", "address": "", "status": "active",
    }
    payload.update(overrides)
    return _pull_event_of("customer", customer_id, "create", payload)


def test_default_handled_entity_types_matches_the_module_constant():
    """Documents the contract `__init__`'s docstring promises: a SyncService
    built with no `handled_entity_types` argument at all -- exactly how both
    of retail's own real call sites (products/retail/backend/app.py) build
    theirs -- ends up with the exact same set as the module-level default,
    so introducing this argument changed nothing about their behavior."""
    service = SyncService(lambda: None, lambda: None, lambda: "co")
    assert service._handled_entity_types == RETAIL_SYNC_ENTITY_TYPES


def test_instance_configured_for_retail_set_applies_a_retail_event(get_conn):
    """The allow half, baseline: an instance explicitly configured with a
    handled_entity_types set that includes "product" applies a product
    create exactly like the default (unconfigured) instance would."""
    event = _product_event()
    client = FakeRelayClient(pull_responses=[{"events": [event], "cursor": 1}])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company",
                           handled_entity_types={"product", "customer"})

    service.pull_once()

    assert len(_products(get_conn)) == 1
    assert _cursor(get_conn) == 1


def test_instance_ignores_an_event_outside_its_configured_set(get_conn):
    """The deny half: an instance configured to handle ONLY "product" must
    not write a "customer" event at all -- no row, no exception raised, and
    no quarantine row either (a foreign-stream event is not poison; see the
    module docstring -- it simply belongs to the other stream)."""
    event = _customer_event()
    client = FakeRelayClient(pull_responses=[{"events": [event], "cursor": 1}])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company",
                           handled_entity_types={"product"})

    service.pull_once()  # must not raise

    assert _customers(get_conn) == []
    conn = get_conn()
    quarantined = conn.execute("SELECT COUNT(*) FROM sync_apply_quarantine").fetchone()[0]
    conn.close()
    assert quarantined == 0, "a foreign-stream event must never be parked in the quarantine table"


def test_cursor_still_advances_past_an_ignored_event(get_conn):
    """THE critical proof this guard exists for: an event outside the
    instance's handled set must not stall the cursor -- that is the exact
    permanent-wedge failure shape (wave A's defect #1) this guard is meant
    to prevent. Read directly from sync_cursor, never inferred merely from
    "pull_once() did not raise"."""
    event = _customer_event()
    client = FakeRelayClient(pull_responses=[{"events": [event], "cursor": 7}])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company",
                           handled_entity_types={"product"})

    service.pull_once()

    assert _cursor(get_conn) == 7


def test_mixed_batch_applies_handled_and_skips_unhandled_cursor_lands_at_batch_max(get_conn):
    """A single pull batch mixing a handled type and an unhandled type --
    exactly what a real two-stream relay pull would hand either instance,
    since both pull the SAME relay stream. The handled event applies, the
    unhandled one is skipped, and the cursor lands at the WHOLE BATCH's max
    seq (Owner's own returned cursor value), not some partial value stopping
    short at the skipped event."""
    product_event = _product_event()
    customer_event = _customer_event()
    client = FakeRelayClient(pull_responses=[
        {"events": [product_event, customer_event], "cursor": 42}
    ])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company",
                           handled_entity_types={"product"})

    service.pull_once()

    assert len(_products(get_conn)) == 1
    assert _customers(get_conn) == []
    assert _cursor(get_conn) == 42


def test_a_differently_configured_instance_applies_what_the_first_ignored(get_conn):
    """The allow half, proven SEPARATELY from the deny half -- this project
    has shipped a guard that denied everything and passed every deny test
    once already (ENGINEERING.md §1's "prove both directions" rule exists
    because of exactly that). The SAME customer event the product-only
    instance above ignores is APPLIED by a second instance configured with a
    DIFFERENT set, proving the mechanism is genuinely parameterised -- not
    "ignore everything unusual"."""
    event = _customer_event()

    client_a = FakeRelayClient(pull_responses=[{"events": [event], "cursor": 1}])
    service_a = SyncService(lambda: client_a, get_conn, lambda: "receiving-company",
                             handled_entity_types={"product"})
    service_a.pull_once()
    assert _customers(get_conn) == [], "fixture bug: instance A already applied the customer event"

    client_b = FakeRelayClient(pull_responses=[{"events": [event], "cursor": 1}])
    service_b = SyncService(lambda: client_b, get_conn, lambda: "receiving-company",
                             handled_entity_types={"customer"})
    service_b.pull_once()

    assert len(_customers(get_conn)) == 1


# ── launch-readiness Phase 6 stage 6a-ii: reject-stale gate for the five
# catalogue types (docs/launch-readiness/phase6-catalogue-correctness.md,
# Task A/B/C) ────────────────────────────────────────────────────────────
#
# Stage 6a-i (adcb712) made row_version REAL: every catalogue write site
# bumps it in the same statement as the field change, and the apply side
# carries it through -- but still applies with plain last-write-wins
# (`ON CONFLICT(id) DO UPDATE`, no gate). This stage switches the gate on:
# apply only when incoming row_version is strictly greater than local,
# identical posture to wave B2's `user` branch (`WHERE excluded.row_version
# > users.row_version`). A discarded row is NOT an error and NOT a silent
# drop -- it writes a `sync_conflicts` entry (Task B) and the batch logs
# once (Task B), not once per row, matching `_log_branch_fallback_summary`'s
# own reasoning above.
#
# Task C is explicit that the ALLOW half is the one that matters -- a gate
# mutated to `WHERE 0` passes every "stale is rejected" test while silently
# discarding the ENTIRE catalogue, exactly the wave B2 `user` gate shape
# already proved once. The two tests immediately below (category, product)
# are the ones that mutation-proof pins down: with the real gate's `WHERE
# excluded.row_version > <table>.row_version` mutated to `WHERE 0`, ONLY
# these two go red while every DENY-half/create/delete test in this section
# stays green. See this stage's own report for the RED/GREEN transcript.

def test_category_update_with_genuinely_higher_row_version_applies_every_field(get_conn):
    """THE headline proof (Task C #1): a genuinely newer incoming
    row_version must still apply, and EVERY synced column must take the
    incoming value, not merely row_version itself. Mutating the gate's
    `WHERE excluded.row_version > categories.row_version` to `WHERE 0` must
    turn ONLY this test (and its product twin below) red."""
    cat_id = str(uuid.uuid4())
    create_event = _pull_event(cat_id, "create", {
        "id": cat_id, "company_id": 1, "name": "Old Name", "description": "old desc",
        "row_version": 1, "updated_at_utc": "2026-08-01T00:00:00+00:00",
    })
    update_event = _pull_event(cat_id, "update", {
        "id": cat_id, "company_id": 1, "name": "New Name", "description": "new desc",
        "row_version": 2, "updated_at_utc": "2026-08-02T00:00:00+00:00",
    })
    client = FakeRelayClient(pull_responses=[
        {"events": [create_event], "cursor": 1},
        {"events": [update_event], "cursor": 2},
    ])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    service.pull_once()

    rows = _categories(get_conn)
    assert len(rows) == 1
    assert rows[0]["name"] == "New Name"
    assert rows[0]["description"] == "new desc"
    assert rows[0]["row_version"] == 2
    assert rows[0]["updated_at_utc"] == "2026-08-02T00:00:00+00:00"
    assert _sync_conflicts(get_conn) == [], "a genuinely newer row must never write a conflict entry"


def test_product_update_with_genuinely_higher_row_version_applies_every_field(get_conn):
    """The second of Task C #1's "at least two entity types" -- same
    headline proof as the category test above, for `product`."""
    pid = str(uuid.uuid4())
    cat_a, cat_b = str(uuid.uuid4()), str(uuid.uuid4())
    sup_a, sup_b = str(uuid.uuid4()), str(uuid.uuid4())
    create_payload = _product_payload(
        pid, sku="SKU-OLD", barcode="OLD-BC", name="Old Widget", category_id=cat_a,
        supplier_id=sup_a, cost_price=1.0, sell_price=2.0, tax_rate=0, unit="pcs",
        reorder_level=5, reorder_method="none", status="active",
        row_version=1, updated_at_utc="2026-08-01T00:00:00+00:00",
    )
    update_payload = _product_payload(
        pid, sku="SKU-NEW", barcode="NEW-BC", name="New Widget", category_id=cat_b,
        supplier_id=sup_b, cost_price=9.0, sell_price=19.99, tax_rate=0.05, unit="box",
        reorder_level=10, reorder_method="auto", status="active",
        row_version=2, updated_at_utc="2026-08-02T00:00:00+00:00",
    )
    create_event = _pull_event_of("product", pid, "create", create_payload)
    update_event = _pull_event_of("product", pid, "update", update_payload)
    client = FakeRelayClient(pull_responses=[
        {"events": [create_event], "cursor": 1},
        {"events": [update_event], "cursor": 2},
    ])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    service.pull_once()

    rows = _products(get_conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["sku"] == "SKU-NEW"
    assert row["barcode"] == "NEW-BC"
    assert row["name"] == "New Widget"
    assert row["category_id"] == cat_b
    assert row["supplier_id"] == sup_b
    assert row["cost_price"] == 9.0
    assert row["sell_price"] == 19.99
    assert row["tax_rate"] == 0.05
    assert row["unit"] == "box"
    assert row["reorder_level"] == 10
    assert row["reorder_method"] == "auto"
    assert row["row_version"] == 2
    assert row["updated_at_utc"] == "2026-08-02T00:00:00+00:00"
    assert _sync_conflicts(get_conn) == [], "a genuinely newer row must never write a conflict entry"


def test_category_update_with_lower_row_version_is_discarded_byte_unchanged(get_conn):
    """Task C #2 (DENY, lower): compared as a WHOLE ROW, not field-by-field
    -- a discard that changed even ONE column nobody thought to assert on
    would slip past a narrower check."""
    cat_id = str(uuid.uuid4())
    create_event = _pull_event(cat_id, "create", {
        "id": cat_id, "company_id": 1, "name": "Kept Name", "description": "kept desc",
        "row_version": 5, "updated_at_utc": "2026-08-05T00:00:00+00:00",
    })
    stale_event = _pull_event(cat_id, "update", {
        "id": cat_id, "company_id": 1, "name": "Stale Name", "description": "stale desc",
        "row_version": 3, "updated_at_utc": "2026-08-03T00:00:00+00:00",
    })
    client = FakeRelayClient(pull_responses=[
        {"events": [create_event], "cursor": 1},
        {"events": [stale_event], "cursor": 2},
    ])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    before = _categories(get_conn)[0]
    service.pull_once()
    after = _categories(get_conn)[0]

    assert after == before, (
        f"a stale (lower row_version) update must leave the row byte-unchanged: {before} -> {after}")
    assert _cursor(get_conn) == 2, "the cursor must still advance past a discarded row"


def test_category_update_with_equal_row_version_is_discarded_byte_unchanged(get_conn):
    """Task C #2 (DENY, equal) -- `>`, not `>=`: an incoming row_version
    EQUAL to local must also be discarded, matching wave B2's `user` branch
    (`WHERE excluded.row_version > users.row_version`, strictly greater)."""
    cat_id = str(uuid.uuid4())
    create_event = _pull_event(cat_id, "create", {
        "id": cat_id, "company_id": 1, "name": "Kept Name", "description": "kept desc",
        "row_version": 5, "updated_at_utc": "2026-08-05T00:00:00+00:00",
    })
    equal_event = _pull_event(cat_id, "update", {
        "id": cat_id, "company_id": 1, "name": "Equal-Version Name", "description": "equal desc",
        "row_version": 5, "updated_at_utc": "2026-08-06T00:00:00+00:00",
    })
    client = FakeRelayClient(pull_responses=[
        {"events": [create_event], "cursor": 1},
        {"events": [equal_event], "cursor": 2},
    ])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    before = _categories(get_conn)[0]
    service.pull_once()
    after = _categories(get_conn)[0]

    assert after == before, (
        f"an EQUAL row_version update must also be discarded, not applied: {before} -> {after}")


def test_product_update_with_lower_row_version_is_discarded_byte_unchanged(get_conn):
    """Task C #2 (DENY, lower), for `product` -- the second entity type."""
    pid = str(uuid.uuid4())
    create_event = _pull_event_of("product", pid, "create",
                                   _product_payload(pid, name="Kept", row_version=5))
    stale_event = _pull_event_of("product", pid, "update",
                                  _product_payload(pid, name="Stale", sell_price=999, row_version=2))
    client = FakeRelayClient(pull_responses=[
        {"events": [create_event], "cursor": 1},
        {"events": [stale_event], "cursor": 2},
    ])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    before = _products(get_conn)[0]
    service.pull_once()
    after = _products(get_conn)[0]

    assert after == before, f"a stale product update must leave the row byte-unchanged: {before} -> {after}"


def test_device_a_price_change_survives_a_stale_push_from_a_device_b_that_was_offline(get_conn):
    """Task C #3 -- the exact scenario launch-readiness Phase 6 exists to
    fix (docs/launch-readiness/phase6-catalogue-correctness.md, "The
    problem, in shop terms"): device A changes a product's PRICE and syncs
    it out (row_version bumped, stage 6a-i). Device B was offline while A's
    edit happened and, on reconnecting, pushes its OWN stale copy of the
    same product -- the ORIGINAL price, at the row_version it had before it
    went offline. Before this stage, B's stale push would silently win
    (ON CONFLICT(id) DO UPDATE, plain last-write-wins) and the shop would
    sell at the old price until a human noticed. After this stage, B's push
    is discarded and A's price survives."""
    pid = str(uuid.uuid4())
    original = _pull_event_of("product", pid, "create", _product_payload(
        pid, sell_price=10.00, row_version=1, updated_at_utc="2026-08-01T00:00:00+00:00"))

    # Device A's price change, relayed to THIS device with a genuinely
    # bumped row_version -- exactly what retail_api.py's update_product does
    # since stage 6a-i.
    device_a_price_change = _pull_event_of("product", pid, "update", _product_payload(
        pid, sell_price=12.50, row_version=2, updated_at_utc="2026-08-10T00:00:00+00:00"))

    # Device B never saw A's edit -- it pushes its own copy, still at the
    # ORIGINAL price and the ORIGINAL row_version it had before going offline.
    device_b_stale_push = _pull_event_of("product", pid, "update", _product_payload(
        pid, sell_price=10.00, row_version=1, updated_at_utc="2026-08-01T00:00:00+00:00"))

    client = FakeRelayClient(pull_responses=[
        {"events": [original], "cursor": 1},
        {"events": [device_a_price_change], "cursor": 2},
        {"events": [device_b_stale_push], "cursor": 3},
    ])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    assert _products(get_conn)[0]["sell_price"] == 10.00

    service.pull_once()
    assert _products(get_conn)[0]["sell_price"] == 12.50, "device A's price change must apply"

    service.pull_once()  # device B's stale, offline push
    row = _products(get_conn)[0]
    assert row["sell_price"] == 12.50, (
        f"device B's stale, offline copy silently reverted the shop's price to "
        f"{row['sell_price']} -- this is the exact bug launch-readiness Phase 6 exists to fix")
    assert row["row_version"] == 2, "the local row_version must still reflect A's write, not B's stale one"

    conflicts = _sync_conflicts(get_conn)
    assert len(conflicts) == 1
    assert conflicts[0]["entity_type"] == "product"
    assert conflicts[0]["entity_id"] == pid
    assert conflicts[0]["event_type"] == "update"
    assert conflicts[0]["local_row_version"] == 2
    assert conflicts[0]["incoming_row_version"] == 1
    assert json.loads(conflicts[0]["incoming_payload"])["sell_price"] == 10.00


def test_create_for_a_never_before_seen_id_lands_even_at_row_version_1(get_conn):
    """Task C #4: the INSERT half of the gated UPSERT is never subject to
    the WHERE clause -- a `create` for an id this device has never seen has
    no local row to be "stale" against, so it must land regardless of how
    low its own row_version is (an emitter that predates stage 6a-i's bump
    would send no row_version at all, falling back to 1 -- see `p.get(
    "row_version") or 1` in every catalogue branch)."""
    cat_id = str(uuid.uuid4())
    event = _pull_event(cat_id, "create", {
        "id": cat_id, "company_id": 1, "name": "Brand New", "description": "", "row_version": 1,
    })
    client = FakeRelayClient(pull_responses=[{"events": [event], "cursor": 1}])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()

    rows = _categories(get_conn)
    assert len(rows) == 1
    assert rows[0]["name"] == "Brand New"
    assert _sync_conflicts(get_conn) == [], "a create for an unseen id must never be treated as a conflict"


def test_stale_delete_does_not_revert_a_newer_edit(get_conn):
    """Task C #5 (first half): the three soft-deletes (product/customer/
    supplier) stamp row_version on the sender -- a delete arriving with a
    LOWER row_version than an edit this device already applied must not
    revert that edit.

    launch-readiness Phase 6 stage 6b-iii-a: the original assertions here
    checked `status` reverting to 'inactive'. Since `status` no longer
    changes on delete at all, a passing `status == 'active'` assertion after
    the stale delete would be true FOR THE WRONG REASON post-6b-iii-a (it
    would pass even if the reject-stale gate were deleted outright) -- see
    ENGINEERING.md's "the pass condition IS the bug signature". The
    meaningful check is now `deleted_at_utc`, the column that actually
    carries deletion: a stale delete must leave it untouched (still None),
    not merely leave `status` untouched."""
    pid = str(uuid.uuid4())
    create_event = _pull_event_of("product", pid, "create", _product_payload(pid, row_version=1))
    newer_edit = _pull_event_of("product", pid, "update", _product_payload(
        pid, name="Edited After The Delete Was Sent", row_version=5))
    stale_delete = _pull_event_of("product", pid, "delete", {"id": pid, "row_version": 3})

    client = FakeRelayClient(pull_responses=[
        {"events": [create_event], "cursor": 1},
        {"events": [newer_edit], "cursor": 2},
        {"events": [stale_delete], "cursor": 3},
    ])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    service.pull_once()
    row = _products(get_conn)[0]
    assert row["status"] == "active"
    assert row["deleted_at_utc"] is None

    service.pull_once()  # the stale delete
    row = _products(get_conn)[0]
    assert row["status"] == "active", "status is not the tombstone as of stage 6b-iii-a, but must stay untouched"
    assert row["deleted_at_utc"] is None, "a stale delete must not tombstone a row that has a newer edit"
    assert row["row_version"] == 5, "the stale delete must not overwrite the newer row_version either"

    conflicts = _sync_conflicts(get_conn)
    assert len(conflicts) == 1
    assert conflicts[0]["event_type"] == "delete"
    assert conflicts[0]["local_row_version"] == 5
    assert conflicts[0]["incoming_row_version"] == 3


def test_genuinely_newer_delete_still_soft_deletes(get_conn):
    """Task C #5 (second half): a delete with a genuinely HIGHER row_version
    than local must still take effect -- the gate must not accidentally
    block every delete, only stale ones.

    launch-readiness Phase 6 stage 6b-iii-a: asserts `deleted_at_utc`, not
    `status` -- see `test_stale_delete_does_not_revert_a_newer_edit`'s
    identical note above for why the `status` assertion this test used to
    have would no longer mean anything."""
    pid = str(uuid.uuid4())
    create_event = _pull_event_of("product", pid, "create", _product_payload(pid, row_version=1))
    real_delete = _pull_event_of("product", pid, "delete", {"id": pid, "row_version": 2})

    client = FakeRelayClient(pull_responses=[
        {"events": [create_event], "cursor": 1},
        {"events": [real_delete], "cursor": 2},
    ])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    service.pull_once()

    row = _products(get_conn)[0]
    assert row["status"] == "active", "status is no longer the tombstone as of stage 6b-iii-a"
    assert row["deleted_at_utc"] is not None, "a genuinely newer delete must still tombstone the row"
    assert row["row_version"] == 2
    assert _sync_conflicts(get_conn) == []


def test_multiple_stale_catalogue_rows_in_one_batch_log_one_summary_warning_not_one_per_row(get_conn, caplog):
    """Task B/C #6: same reasoning, same fix shape, as
    test_unresolved_branch_fallback_logs_one_summary_warning_per_batch_not_per_row
    above -- a WARNING repeated once per discarded row is the same as no log
    at all on a busy multi-device install. Three catalogue rows (two
    products, one category) are pushed stale in the SAME pull batch; the fix
    must log exactly ONE summary WARNING, while every discarded row still
    gets its OWN `sync_conflicts` entry -- the per-row detail lives in the
    table, not in the log line."""
    pid_a, pid_b = str(uuid.uuid4()), str(uuid.uuid4())
    cat_id = str(uuid.uuid4())
    seed_events = [
        _pull_event_of("product", pid_a, "create", _product_payload(pid_a, row_version=5)),
        _pull_event_of("product", pid_b, "create", _product_payload(pid_b, row_version=5)),
        _pull_event(cat_id, "create", {
            "id": cat_id, "company_id": 1, "name": "X", "description": "", "row_version": 5}),
    ]
    _apply(get_conn, seed_events, cursor=1, local_company_id="receiving-company")

    stale_events = [
        _pull_event_of("product", pid_a, "update", _product_payload(pid_a, name="stale a", row_version=1)),
        _pull_event_of("product", pid_b, "update", _product_payload(pid_b, name="stale b", row_version=1)),
        _pull_event(cat_id, "update", {
            "id": cat_id, "company_id": 1, "name": "stale cat", "description": "", "row_version": 1}),
    ]
    with caplog.at_level("WARNING", logger="commercial_runtime.sync.sync_service"):
        _apply(get_conn, stale_events, cursor=2, local_company_id="receiving-company")

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    conflict_warnings = [m for m in warnings if "discarded as stale" in m]
    assert len(conflict_warnings) == 1, (
        f"expected exactly ONE consolidated WARNING for a 3-row stale batch, got "
        f"{len(conflict_warnings)}: {conflict_warnings}")
    assert "3" in conflict_warnings[0]

    conflicts = _sync_conflicts(get_conn)
    assert len(conflicts) == 3, "each discarded row must still get its OWN sync_conflicts entry"
    assert {c["entity_id"] for c in conflicts} == {pid_a, pid_b, cat_id}
    for c in conflicts:
        assert c["local_row_version"] == 5
        assert c["incoming_row_version"] == 1


def test_a_discarded_row_does_not_wedge_the_batch_cursor_advances_and_unrelated_events_land(get_conn):
    """Task C #7: a discard must never wedge a batch -- same posture the
    unhandled-entity-type gate and the money-moving quarantine path already
    have (see this file's other cursor-advancement tests above). A single
    event failing the reject-stale gate must not prevent every OTHER event
    in the same batch from landing, and the cursor must still advance to the
    batch's own returned value."""
    stale_pid = str(uuid.uuid4())
    unrelated_cat_id = str(uuid.uuid4())
    seed = _pull_event_of("product", stale_pid, "create", _product_payload(stale_pid, row_version=5))
    SyncService(lambda: FakeRelayClient(pull_responses=[{"events": [seed], "cursor": 1}]),
                get_conn, lambda: "receiving-company").pull_once()

    stale_update = _pull_event_of("product", stale_pid, "update",
                                   _product_payload(stale_pid, name="should not land", row_version=1))
    unrelated_create = _pull_event(unrelated_cat_id, "create", {
        "id": unrelated_cat_id, "company_id": 1, "name": "Unrelated Category", "description": "",
        "row_version": 1,
    })
    client = FakeRelayClient(pull_responses=[
        {"events": [stale_update, unrelated_create], "cursor": 99},
    ])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()  # must not raise

    assert _products(get_conn)[0]["name"] != "should not land"
    cats = _categories(get_conn)
    assert len(cats) == 1 and cats[0]["id"] == unrelated_cat_id, (
        "the unrelated category create must land in the SAME batch as the discarded product update")
    assert _cursor(get_conn) == 99, (
        "the cursor must advance to the whole batch's returned value, not stall on the discard")


# ── launch-readiness Phase 6 stage 6a-ii follow-up: missing is not stale ───
#
# Retail's own catalogue sync tests (retail_product_sync_test.py,
# retail_customer_sync_test.py, retail_supplier_sync_test.py) push a bare
# `{"id": "..."}` delete payload -- NO `row_version` key at all -- against a
# local row still at the schema `DEFAULT 1`. The ORIGINAL stage 6a-ii gate
# coalesced a missing key to `p.get("row_version") or 1`, making that event
# indistinguishable from a genuinely stale write at version 1: `1 > 1` is
# false, so the write was silently DISCARDED (visible in `sync_conflicts`,
# never applied) -- and on retail's fixtures, which predated `sync_conflicts`
# existing at all, that discard path raised `sqlite3.OperationalError: no
# such table: sync_conflicts` before the wrong-discard itself was even
# visible.
#
# A payload with no `row_version` key predates stage 6a-i entirely --
# already sitting in an outbox, in flight to the relay, or queued on a
# device that upgrades mid-backlog. Missing is not stale; missing means
# legacy, and legacy must still apply, or a shop upgrading with a backlog
# silently loses real catalogue changes. The fix: the WHERE gate treats a
# missing key as `NULL` (not 1) and always passes; `row_version=MAX(<table>.
# row_version, excluded.row_version)` (not plain `excluded.row_version`)
# keeps that legacy apply from regressing a counter that had legitimately
# advanced past 1.

def test_legacy_event_missing_row_version_key_applies_and_writes_no_conflict(get_conn):
    """The first half: a legacy update (no `row_version` key at all) must
    APPLY its other fields, not be silently discarded, and must never write
    a `sync_conflicts` entry -- it was not rejected, it was applied."""
    cat_id = str(uuid.uuid4())
    create_event = _pull_event(cat_id, "create", {
        "id": cat_id, "company_id": 1, "name": "Original", "description": "orig", "row_version": 1,
    })
    legacy_update = _pull_event(cat_id, "update", {
        "id": cat_id, "company_id": 1, "name": "Legacy Field Update", "description": "legacy",
        # deliberately NO "row_version" key -- a payload that predates stage 6a-i.
    })
    client = FakeRelayClient(pull_responses=[
        {"events": [create_event], "cursor": 1},
        {"events": [legacy_update], "cursor": 2},
    ])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    service.pull_once()

    row = _categories(get_conn)[0]
    assert row["name"] == "Legacy Field Update", "a legacy event must APPLY its other fields, not be discarded"
    assert row["description"] == "legacy"
    assert _sync_conflicts(get_conn) == [], (
        "a legacy event was applied, not rejected -- it must never write a sync_conflicts entry")


def test_legacy_create_for_a_never_before_seen_id_still_lands_at_row_version_1(get_conn):
    """A `create` event that predates stage 6a-i (no `row_version` key) for
    an id this device has never seen must still land -- there is no local
    row to be "stale" against, and the NOT NULL row_version column falls
    back to 1, exactly as it always has."""
    cat_id = str(uuid.uuid4())
    event = _pull_event(cat_id, "create", {
        "id": cat_id, "company_id": 1, "name": "Legacy Create", "description": "",
    })
    client = FakeRelayClient(pull_responses=[{"events": [event], "cursor": 1}])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()

    row = _categories(get_conn)[0]
    assert row["name"] == "Legacy Create"
    assert row["row_version"] == 1
    assert _sync_conflicts(get_conn) == []


def test_legacy_delete_missing_row_version_key_still_soft_deletes(get_conn):
    """The EXACT production bug the coordinator found, reproduced at the
    sync_service level: retail's own product/customer/supplier delete sync
    tests push a bare `{"id": "..."}` delete payload -- no `row_version` key
    -- against a local row at the schema DEFAULT (1). It must still
    soft-delete.

    launch-readiness Phase 6 stage 6b-iii-a: asserts `deleted_at_utc`, not
    `status` -- `status` no longer marks deletion, so it would stay 'active'
    here regardless of whether this gate works at all. `deleted_at_utc` is
    what a "legacy, no row_version key" payload must still set (it ALSO has
    no `deleted_at_utc` key, so this doubles as coverage for Step 2c's
    `now()` fallback)."""
    pid = str(uuid.uuid4())
    create_event = _pull_event_of("product", pid, "create", _product_payload(pid))  # no row_version -> lands at 1
    legacy_delete = _pull_event_of("product", pid, "delete", {"id": pid})  # no row_version key at all
    client = FakeRelayClient(pull_responses=[
        {"events": [create_event], "cursor": 1},
        {"events": [legacy_delete], "cursor": 2},
    ])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    service.pull_once()

    row = _products(get_conn)[0]
    assert row["status"] == "active", "status is no longer the tombstone as of stage 6b-iii-a"
    assert row["deleted_at_utc"] is not None, "a legacy delete (no row_version, no deleted_at_utc key) must still soft-delete"
    assert _sync_conflicts(get_conn) == []


def test_legacy_event_does_not_regress_a_local_row_version_that_has_advanced(get_conn):
    """THE subtle half: a local row that legitimately reached row_version 5
    through real modern edits must not be pulled back down by an old,
    un-versioned event applying its OTHER fields on top. This is the test
    the `MAX(<table>.row_version, excluded.row_version)` -- vs. plain
    `excluded.row_version` -- mutation is proven against (see this stage's
    own report for the RED/GREEN transcript)."""
    pid = str(uuid.uuid4())
    create_event = _pull_event_of("product", pid, "create", _product_payload(pid, row_version=1))
    # Four genuine modern edits, each bumping row_version by one -- exactly
    # what real traffic looks like after stage 6a-i.
    edit_events = [
        _pull_event_of("product", pid, "update", _product_payload(pid, name=f"Edit {rv}", row_version=rv))
        for rv in (2, 3, 4, 5)
    ]
    # No `row_version=` override at all -- genuinely missing the key, same
    # as the production bug.
    legacy_event = _pull_event_of("product", pid, "update", _product_payload(pid, name="Legacy Touch"))

    pull_responses = [{"events": [create_event], "cursor": 1}]
    pull_responses += [{"events": [e], "cursor": i} for i, e in enumerate(edit_events, start=2)]
    pull_responses.append({"events": [legacy_event], "cursor": 6})
    client = FakeRelayClient(pull_responses=pull_responses)
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    for _ in pull_responses:
        service.pull_once()

    row = _products(get_conn)[0]
    assert row["row_version"] == 5, (
        f"a legacy event must never regress a local row_version that had legitimately "
        f"advanced to 5 -- got {row['row_version']}")
    assert row["name"] == "Legacy Touch", "the legacy event's OTHER fields must still apply"
    assert _sync_conflicts(get_conn) == []


def test_modern_stale_or_equal_row_version_is_still_denied_after_the_legacy_fix(get_conn):
    """Confirms the legacy fix did not loosen the deny half: a MODERN event
    (an explicit, real `row_version` key) at a lower OR equal value than
    local must still be discarded and still record a `sync_conflicts`
    entry -- only a MISSING key gets the free pass, never an explicit low
    one."""
    cat_id = str(uuid.uuid4())
    create_event = _pull_event(cat_id, "create", {
        "id": cat_id, "company_id": 1, "name": "Kept", "description": "", "row_version": 5,
    })
    lower_event = _pull_event(cat_id, "update", {
        "id": cat_id, "company_id": 1, "name": "Should Not Land (lower)", "description": "",
        "row_version": 3,
    })
    equal_event = _pull_event(cat_id, "update", {
        "id": cat_id, "company_id": 1, "name": "Should Not Land (equal)", "description": "",
        "row_version": 5,
    })
    client = FakeRelayClient(pull_responses=[
        {"events": [create_event], "cursor": 1},
        {"events": [lower_event], "cursor": 2},
        {"events": [equal_event], "cursor": 3},
    ])
    service = SyncService(lambda: client, get_conn, lambda: "receiving-company")

    service.pull_once()
    service.pull_once()
    service.pull_once()

    row = _categories(get_conn)[0]
    assert row["name"] == "Kept", "an explicit lower-or-equal row_version must still be denied"
    conflicts = _sync_conflicts(get_conn)
    assert len(conflicts) == 2, "each explicit stale write must still record its own sync_conflicts entry"
    assert {c["incoming_row_version"] for c in conflicts} == {3, 5}
