"""Aura Retail -- regression coverage for the schema v3 change
(`products.category_id` FOREIGN KEY -> ON DELETE SET NULL).

Why this file exists (the exact bug it reproduces): categories ARE synced
across devices on one license; products are NOT (this phase). Two devices
therefore legitimately hold different product -> category assignments.
Before v3, `products.category_id` declared a bare
`REFERENCES categories(id)`, and `database/schema.py::_conn` sets
`PRAGMA foreign_keys=ON` on every connection -- so when device A (holding no
products in "Electronics") deleted that category and device B (holding one)
pulled the relayed delete, `SyncService._apply_event`'s
`DELETE FROM categories WHERE id=?` raised
`IntegrityError: FOREIGN KEY constraint failed`. That aborted
`apply_pull_result` BEFORE the cursor was advanced, and `run_once` swallowed
the exception -- so device B silently stopped receiving ANY event from ANY
device, forever, with no self-healing.

Every test here runs against the REAL retail schema (built by the real
`init_retail()`) on a REAL connection from the real `get_retail_conn()`
factory -- not a hand-written test schema -- because the bug only exists at
all when `PRAGMA foreign_keys=ON` meets the real declared FK. A test that
stubbed either of those would pass against the broken schema too.

Run:
    pytest products/retail/tests/retail_category_delete_fk_sync_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_catfk_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))

from database import schema as retail_schema  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402


def setup_module(module):
    retail_schema.init_retail()


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_tables():
    conn = retail_schema.get_retail_conn()
    conn.execute("DELETE FROM products")
    conn.execute("DELETE FROM categories")
    conn.execute("UPDATE sync_cursor SET last_seq=0 WHERE id=1")
    conn.commit()
    conn.close()
    yield


def _seed_category_with_product(cat_id, company_id=1):
    conn = retail_schema.get_retail_conn()
    conn.execute(
        "INSERT INTO categories (id, company_id, name, description) VALUES (?,?,?,?)",
        (cat_id, company_id, "Electronics", "Device B's own copy"),
    )
    conn.execute(
        "INSERT INTO products (id, company_id, sku, barcode, name, category_id, cost_price, sell_price) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), company_id, "SKU-FK-1", "9990001", "Laptop", cat_id, 750.0, 1199.99),
    )
    conn.commit()
    conn.close()


# ── The schema change itself ────────────────────────────────────────────────

def test_schema_version_is_v6_and_products_fk_declares_on_delete_set_null():
    # Was v3 when this file was written; the multi-device sync foundation's
    # products.id -> UUID migration bumped this to v4, the customers/
    # suppliers UUID migration (database/schema.py's _migrate_customers_to_uuid /
    # _migrate_suppliers_to_uuid) bumped it again to v5, the PO-preview-by-
    # supplier foundation (database/schema.py's
    # _migrate_add_supplier_contacts_and_po_split) bumped it again to v6, and
    # merging master's einvoicing work (feat/retail-mobile-build-baseline,
    # 2026-08-10) bumped it once more to v7, the reorder automation
    # foundation (feat/reorder-automation-foundation, database/schema.py's
    # _migrate_add_reorder_automation_foundation) bumped it once more to v8,
    # the email outbox foundation (feat/email-outbox-foundation,
    # database/schema.py's _migrate_add_notifications_foundation) bumped it
    # once more to v9, and hold/resume sale (feat/pos-hold-resume-sale,
    # database/schema.py's _migrate_add_held_sales) bumped it once more to
    # v11 (NOT v10 -- v10 was independently claimed for real by the
    # unmerged feat/shift-cash-drawer branch on the same v9 base; see
    # RETAIL_SCHEMA_VERSION's own comment for the full collision story) --
    # see RETAIL_SCHEMA_VERSION's own comment for why that bump is
    # load-bearing, not cosmetic. The FK-on-delete-set-null assertion this
    # test exists for is unaffected by any of these later changes.
    assert retail_schema.RETAIL_SCHEMA_VERSION == 11
    conn = retail_schema.get_retail_conn()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 11
        assert retail_schema._products_category_fk_is_set_null(conn) is True
    finally:
        conn.close()


def test_foreign_keys_are_actually_enforced_on_a_real_connection():
    """The whole bug depends on FK enforcement genuinely being ON -- if this
    ever regressed to OFF, every other test in this file would pass
    vacuously."""
    conn = retail_schema.get_retail_conn()
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO products (id, company_id, sku, name, category_id) "
                "VALUES (?,1,'SKU-BAD','Bad','no-such-category')",
                (str(uuid.uuid4()),),
            )
            conn.commit()
    finally:
        conn.rollback()
        conn.close()


def test_local_category_delete_unassigns_its_products_instead_of_failing():
    cat_id = str(uuid.uuid4())
    _seed_category_with_product(cat_id)
    conn = retail_schema.get_retail_conn()
    try:
        conn.execute("DELETE FROM categories WHERE id=?", (cat_id,))
        conn.commit()
        row = conn.execute("SELECT name, category_id FROM products WHERE sku='SKU-FK-1'").fetchone()
        assert row is not None, "the product must survive its category being deleted"
        assert row["category_id"] is None
    finally:
        conn.close()


# ── The real cross-device scenario (the reason for the change) ──────────────

def test_pulled_delete_of_a_category_this_device_has_a_product_in_applies_cleanly():
    """Device A deletes "Electronics" (it holds no products there); device B
    holds a product assigned to it and pulls the relayed delete.

    Asserts the three things that were ALL broken before v3, not merely that
    no exception escaped:
      1. B's product survives, with `category_id` set to NULL.
      2. The category row is genuinely gone locally.
      3. B's sync cursor advances to Owner's returned value -- i.e. B keeps
         receiving every later event from every device, which is precisely
         what the FK failure used to stop forever.
    """
    cat_id = str(uuid.uuid4())
    _seed_category_with_product(cat_id)

    service = SyncService(
        client_factory=lambda: None,  # never used: apply_pull_result makes no network call
        get_conn=retail_schema.get_retail_conn,
        local_company_id_provider=lambda: "1",
    )

    conn = retail_schema.get_retail_conn()
    try:
        service.apply_pull_result(conn, {
            "events": [{
                "entity_type": "category",
                "entity_id": cat_id,
                "event_type": "delete",
                "payload": {"id": cat_id},
            }],
            "cursor": 42,
        })
        conn.commit()
    finally:
        conn.close()

    conn = retail_schema.get_retail_conn()
    try:
        product = conn.execute("SELECT name, category_id FROM products WHERE sku='SKU-FK-1'").fetchone()
        assert product is not None, "the pulled delete must not remove this device's product"
        assert product["category_id"] is None, "the product must simply be unassigned"
        assert conn.execute("SELECT COUNT(*) FROM categories WHERE id=?", (cat_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()[0] == 42
    finally:
        conn.close()


def test_a_later_batch_still_applies_after_a_delete_of_a_linked_category():
    """The failure mode being fixed was not "one event lost" but "this device
    is wedged forever" -- so prove the NEXT batch, from the next device, still
    lands after the delete that used to poison the cursor."""
    cat_id = str(uuid.uuid4())
    other_id = str(uuid.uuid4())
    _seed_category_with_product(cat_id)

    service = SyncService(
        client_factory=lambda: None,
        get_conn=retail_schema.get_retail_conn,
        local_company_id_provider=lambda: "1",
    )

    conn = retail_schema.get_retail_conn()
    try:
        service.apply_pull_result(conn, {
            "events": [{"entity_type": "category", "entity_id": cat_id,
                        "event_type": "delete", "payload": {"id": cat_id}}],
            "cursor": 10,
        })
        conn.commit()
        service.apply_pull_result(conn, {
            "events": [{"entity_type": "category", "entity_id": other_id, "event_type": "create",
                        "payload": {"id": other_id, "name": "Groceries", "description": "from device C"}}],
            "cursor": 11,
        })
        conn.commit()
        assert conn.execute("SELECT name FROM categories WHERE id=?", (other_id,)).fetchone()["name"] == "Groceries"
        assert conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()[0] == 11
    finally:
        conn.close()


# ── The migration for existing installations ───────────────────────────────

def _build_v2_database(path):
    """A schema-v2 retail.db exactly as an installation created before this
    fix would have it: categories.id already TEXT, but products.category_id's
    FK still a bare `REFERENCES categories(id)` -- plus real rows in products
    AND in a table that references products (inventory_movements), since the
    latter is what makes a naive rebuild fail.

    inventory_movements is declared with its full real column set (matching
    database/schema.py's `init_retail()` CREATE TABLE, not a trimmed-down
    stand-in) -- this codebase's inventory_movements shape has never had
    fewer columns than that, and since the multi-device sync foundation's
    products.id -> UUID migration (_migrate_products_to_uuid) now also
    unconditionally rebuilds this table in the same migration chain, a
    trimmed fixture would fail for a reason that has nothing to do with what
    this test file actually covers.
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE categories (
            id TEXT PRIMARY KEY,
            company_id INTEGER DEFAULT 1,
            name TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER DEFAULT 1,
            sku TEXT NOT NULL,
            barcode TEXT,
            name TEXT NOT NULL,
            category_id TEXT,
            cost_price REAL DEFAULT 0,
            sell_price REAL DEFAULT 0,
            tax_rate REAL DEFAULT 0,
            unit TEXT DEFAULT 'pcs',
            reorder_level INTEGER DEFAULT 5,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES categories(id)
        );
        CREATE TABLE inventory_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER DEFAULT 1,
            product_id INTEGER NOT NULL,
            branch_id INTEGER,
            movement_type TEXT NOT NULL,
            quantity REAL NOT NULL,
            unit_cost REAL DEFAULT 0,
            reference TEXT,
            notes TEXT,
            created_by TEXT DEFAULT 'System',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(id)
        );
        INSERT INTO categories (id, company_id, name, description) VALUES ('cat-1', 7, 'Electronics', 'kept');
        INSERT INTO products (id, company_id, sku, barcode, name, category_id, cost_price, sell_price,
                              tax_rate, unit, reorder_level, status)
            VALUES (5, 7, 'SKU-OLD', '111', 'Legacy Laptop', 'cat-1', 750.0, 1199.99, 15, 'pcs', 3, 'active');
        INSERT INTO products (id, company_id, sku, name, category_id) VALUES (6, 7, 'SKU-NULL', 'Unfiled', NULL);
        INSERT INTO inventory_movements (company_id, product_id, branch_id, movement_type, quantity)
            VALUES (7, 5, 1, 'opening_stock', 12);
        PRAGMA user_version = 2;
    """)
    conn.commit()
    conn.close()


def test_the_old_v2_schema_really_did_wedge_on_this_exact_event(tmp_path):
    """Pins the bug itself, so this file can never quietly become a test that
    would also pass against the broken schema. Same `_apply_event` call, same
    `PRAGMA foreign_keys=ON`, only the pre-v3 FK definition -- and it raises,
    leaving the cursor un-advanced (the permanent wedge)."""
    path = tmp_path / "retail.db"
    _build_v2_database(path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        "CREATE TABLE sync_cursor (id INTEGER PRIMARY KEY CHECK (id = 1), last_seq INTEGER NOT NULL DEFAULT 0);"
        "INSERT INTO sync_cursor (id, last_seq) VALUES (1, 0);"
    )
    conn.commit()
    service = SyncService(client_factory=lambda: None, get_conn=lambda: conn,
                          local_company_id_provider=lambda: "7")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            service.apply_pull_result(conn, {
                "events": [{"entity_type": "category", "entity_id": "cat-1",
                            "event_type": "delete", "payload": {"id": "cat-1"}}],
                "cursor": 42,
            })
        conn.rollback()
        assert conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()[0] == 0
    finally:
        conn.close()


def test_v2_to_v3_migration_rebuilds_the_fk_without_losing_a_single_row(tmp_path):
    path = tmp_path / "retail.db"
    _build_v2_database(path)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        assert retail_schema._products_category_fk_is_set_null(conn) is False
        retail_schema._migrate_retail_schema(conn)
        assert retail_schema._products_category_fk_is_set_null(conn) is True

        rows = {r["sku"]: dict(r) for r in conn.execute("SELECT * FROM products ORDER BY id")}
        assert set(rows) == {"SKU-OLD", "SKU-NULL"}
        kept = rows["SKU-OLD"]
        # `_migrate_retail_schema` chains the FK rebuild AND the products.id ->
        # UUID rebuild (`_migrate_products_to_uuid`) in one pass (multi-device
        # sync foundation, schema v3 -> v4) -- the old autoincrement id (5)
        # does not survive; every other non-id column does.
        assert isinstance(kept["id"], str) and len(kept["id"]) == 36  # a real UUID, not the old autoincrement int
        assert (kept["company_id"], kept["barcode"], kept["name"]) == (7, "111", "Legacy Laptop")
        assert (kept["category_id"], kept["cost_price"], kept["sell_price"]) == ("cat-1", 750.0, 1199.99)
        assert (kept["tax_rate"], kept["unit"], kept["reorder_level"], kept["status"]) == (15, "pcs", 3, "active")
        assert rows["SKU-NULL"]["category_id"] is None

        # The referencing table's own FK text must still point at `products`,
        # not at some `products_old` SQLite silently rewrote it to.
        fks = conn.execute("PRAGMA foreign_key_list(inventory_movements)").fetchall()
        assert [r["table"] for r in fks] == ["products"]
        assert conn.execute("SELECT COUNT(*) FROM inventory_movements").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_v2_to_v3_migration_is_idempotent(tmp_path):
    path = tmp_path / "retail.db"
    _build_v2_database(path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        retail_schema._migrate_retail_schema(conn)
        before = [dict(r) for r in conn.execute("SELECT * FROM products ORDER BY id")]
        retail_schema._migrate_retail_schema(conn)  # a second, unnecessary pass
        after = [dict(r) for r in conn.execute("SELECT * FROM products ORDER BY id")]
        assert before == after
        assert retail_schema._products_category_fk_is_set_null(conn) is True
    finally:
        conn.close()


def test_v1_database_migrates_all_the_way_to_v3_in_one_pass(tmp_path):
    """`ensure_schema_version` only reports "behind", never "behind by one" --
    an installation still on v1 (integer category ids) must land on BOTH the
    UUID rebuild and the FK rebuild in a single upgrade."""
    path = tmp_path / "retail.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER DEFAULT 1,
            name TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER DEFAULT 1,
            sku TEXT NOT NULL,
            barcode TEXT,
            name TEXT NOT NULL,
            category_id INTEGER,
            cost_price REAL DEFAULT 0,
            sell_price REAL DEFAULT 0,
            tax_rate REAL DEFAULT 0,
            unit TEXT DEFAULT 'pcs',
            reorder_level INTEGER DEFAULT 5,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES categories(id)
        );
        INSERT INTO categories (id, company_id, name) VALUES (1, 3, 'Electronics');
        INSERT INTO products (company_id, sku, name, category_id) VALUES (3, 'SKU-V1', 'Old Laptop', 1);
        PRAGMA user_version = 1;
    """)
    conn.commit()
    try:
        retail_schema._migrate_retail_schema(conn)
        cat = conn.execute("SELECT id, name FROM categories").fetchone()
        assert cat["name"] == "Electronics"
        assert isinstance(cat["id"], str) and len(cat["id"]) == 36  # a real UUID, not an int
        prod = conn.execute("SELECT sku, category_id FROM products").fetchone()
        assert prod["sku"] == "SKU-V1"
        assert prod["category_id"] == cat["id"], "the product's category link must survive the id remap"
        assert retail_schema._products_category_fk_is_set_null(conn) is True
    finally:
        conn.close()
