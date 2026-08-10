"""
Aura Retail -- database schema and connection.

Owns retail.db: branches, categories, products, inventory_movements,
inventory_balances, customers, suppliers, purchase_orders,
purchase_order_items, sales, sale_items, returns, return_items, payments,
tax_rates, journal_entries, audit_log. `retail_settings` and `doc_sequences`
are created lazily by api/retail_api.py's `_ensure_credit_schema` (unchanged
from source).

Extracted verbatim (schema + seed) from Action Aura Enterprise's
database/subsystem_db.py `init_retail`/`_seed_retail` (lines 4732-4941), which
also owned schema for every other subsystem in that monolith -- see
docs/migration/dependency-map.md §1. Only the retail-relevant slice plus the
generic connection helpers it needs are kept here.
"""
import os
import sqlite3
import random
from datetime import datetime, timedelta

_app_data = os.environ.get('AURA_APP_DATA')
if _app_data:
    BASE_DIR = os.path.join(_app_data, 'database')
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SUBSYS_DIR = os.path.join(BASE_DIR, 'subsystems')

# Wave 1B (Part K): see products/clinic/backend/database/schema.py's
# identical CLINIC_SCHEMA_VERSION for the full rationale.
# Multi-device sync foundation (2026-08-06), v1 -> v2: categories.id moves
# from INTEGER PRIMARY KEY AUTOINCREMENT to TEXT PRIMARY KEY (client-
# generated UUID) so two offline devices can create categories without ever
# colliding on id. See _migrate_categories_to_uuid below.
# Multi-device sync foundation (2026-08-07), v2 -> v3: products.category_id's
# FOREIGN KEY gains ON DELETE SET NULL. See
# _migrate_products_category_fk_on_delete_set_null below for why a bare
# `REFERENCES categories(id)` permanently wedges sync on any device holding a
# product in a category some OTHER device deleted.
# Multi-device sync foundation (2026-08-07), v3 -> v4: products.id moves from
# INTEGER PRIMARY KEY AUTOINCREMENT to TEXT PRIMARY KEY (client-generated
# UUID), same reason categories got this in v1 -> v2: two offline devices
# must never collide on an id. Every table with a declared FK to products(id)
# -- inventory_movements, inventory_balances, sale_items,
# purchase_order_items, return_items -- is rebuilt in the same transaction
# and has its product_id values remapped. See _migrate_products_to_uuid below.
# Still v4: customers.id gets the identical INTEGER -> TEXT UUID treatment,
# plus a new `status` column (customers never had one) and the credit_mode/
# credit_limit/credit_balance columns folded in as first-class columns. No
# table has a declared FK to customers(id) (sales.customer_id and
# payments.party_id are both loose, undeclared columns), so this rides along
# on the same v4 bump rather than needing one of its own. See
# _migrate_customers_to_uuid below.
# Still v4: suppliers.id gets the identical INTEGER -> TEXT UUID treatment,
# plus the payment_terms/credit_balance columns _ensure_credit_schema adds at
# runtime folded in as first-class columns. Unlike customers, this table DOES
# have a declared FK pointing at it (purchase_orders.supplier_id
# REFERENCES suppliers(id)), so this migration carries the same rename-away
# hazard as products/categories -- see _migrate_suppliers_to_uuid below.
# v5: adds products.supplier_id (TEXT, referencing suppliers(id) which is
# already UUID by this point) -- see _migrate_products_add_supplier_fk below.
# v6: PO-preview-by-supplier foundation (Thursday demo, Stream B). Adds
# supplier_contacts (a supplier can have several named contacts -- orders/
# accounts/general -- each with its own channel), plus split-group/routing/
# idempotency columns on purchase_orders (the writers that populate them land
# later this week; this bump only adds the columns) and suppliers.
# min_order_value (used by the split preview's MOQ warning). Pure additive
# migration -- ADD COLUMN + CREATE TABLE only, nothing renamed or retyped, so
# unlike v1-v5 this needs no _new-table rebuild. See
# _migrate_add_supplier_contacts_and_po_split below.
# v7: outbox-wedge fix (2026-08-10 audit, HIGH severity -- see
# commercial_runtime/sync/sync_service.py's module docstring). Three pieces:
#   1. sync_outbox.created_at loses `DEFAULT CURRENT_TIMESTAMP` and becomes
#      NOT NULL. SQLite's CURRENT_TIMESTAMP renders space-separated
#      ("YYYY-MM-DD HH:MM:SS"); the sole writer (_queue_sync_event in
#      api/retail_api.py, mirrored by scripts/seed_demo_sweets.py) always
#      supplies its own T-separated `datetime.now(timezone.utc).isoformat()`
#      value instead. Space (0x20) sorts before 'T' (0x54) in a plain string
#      comparison, so a row that ever fell back to the column default would
#      sort ahead of every explicitly-timestamped row regardless of real
#      insertion order -- silently able to reorder a delete before its own
#      create. Forcing every future writer to supply created_at explicitly
#      turns a missing value into a loud NOT NULL constraint violation
#      instead of a silently-misordered timestamp. SQLite has no ALTER
#      COLUMN, so this needs the same rebuild-under-`_new`-then-swap
#      technique as v1/v2's UUID migrations -- see
#      _migrate_sync_outbox_ordering_and_dead_letter below.
#   2. sync_outbox gains attempt_count/last_error -- diagnostic columns
#      SyncService.push_once() now uses to detect a row that fails the same
#      way on every retry (a real, permanently-malformed event) rather than
#      an ordinary transient/offline failure.
#   3. sync_dead_letter (new table) -- mirrors sync_outbox's shape plus the
#      two diagnostic columns above and a dead_lettered_at timestamp. The
#      landing spot for a row push_once() has isolated, via bisection, as
#      the specific offender in a batch the relay keeps rejecting -- removed
#      from the active outbox so the REST of the batch can keep draining
#      instead of the whole outbox wedging on one bad row forever.
# See _migrate_sync_outbox_ordering_and_dead_letter below.
RETAIL_SCHEMA_VERSION = 7


def _get_path(name):
    os.makedirs(SUBSYS_DIR, exist_ok=True)
    return os.path.join(SUBSYS_DIR, f'{name}.db')


def _conn(name):
    c = sqlite3.connect(_get_path(name), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    # Wave 0 (AUDIT-016): every declared FOREIGN KEY in this schema was
    # previously decorative -- SQLite defaults enforcement to OFF, and this
    # was the only connection factory in the file, so it was never turned on
    # anywhere. Set on every connection (SQLite does not persist this setting
    # in the database file itself; it must be set per-connection, every time).
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _is_standalone():
    """Return True when running as a packaged customer build (no seed data)."""
    try:
        import config as _cfg
        return getattr(_cfg, 'IS_STANDALONE', False)
    except ImportError:
        return False


def get_retail_conn():
    return _conn('retail')


def sub_create(conn_fn, table, data):
    conn = conn_fn()
    try:
        keys = ', '.join(data.keys())
        placeholders = ', '.join(['?'] * len(data))
        conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({placeholders})", list(data.values()))
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def _migrate_categories_to_uuid(conn):
    """One-time migration (schema v1 -> v2): categories.id moves from
    INTEGER PRIMARY KEY AUTOINCREMENT to TEXT PRIMARY KEY (client-generated
    UUIDs), so two offline devices can create categories without ever
    colliding on id. products.category_id is repointed to match, preserving
    every existing row (name/description/company_id/created_at, and every
    product's category link, including NULL).

    Two SQLite behaviours (both confirmed empirically against this exact
    connection factory/pragmas, not assumed) shaped how this is written:

    1. `PRAGMA foreign_keys=ON` is set on every connection by `_conn()`
       above, including the one this runs on. Left on, `DROP TABLE
       categories_old` (once products rows reference it) and `ALTER TABLE
       products DROP COLUMN category_id_old` (SQLite refuses to drop a
       column that is part of a FOREIGN KEY definition) both fail partway
       through a naive rename-based rebuild. So this runs with
       foreign_keys temporarily OFF (it cannot be toggled inside a
       transaction, so it's flipped before BEGIN and restored after COMMIT
       via try/finally).

    2. `ALTER TABLE x RENAME TO y` auto-rewrites any OTHER table's
       FOREIGN KEY clause that points at `x`, to point at `y` instead
       (SQLite's default legacy_alter_table=OFF behaviour). Renaming
       `categories` to `categories_old` would silently rewrite products'
       declared FK to `REFERENCES "categories_old"(id)`; renaming
       `products` away would do the same to inventory_movements/
       sale_items/purchase_order_items/return_items. Both tables are
       instead rebuilt under a `_new` name and swapped in via
       `DROP <original>` + `RENAME <new> TO <original>` -- the original
       name is never renamed away, so no other table's FK text is ever
       touched.

    The whole rebuild runs as one explicit transaction; any failure rolls
    back completely (verified: this Python/SQLite combination honors
    explicit BEGIN across mixed DDL+DML), leaving the database exactly as
    the pre-migration backup captured it for a clean retry on next launch.
    """
    import uuid as _uuid

    # Defensive idempotency: ensure_schema_version's version gate is the
    # normal guard against a second run, but check directly too rather than
    # relying solely on that.
    id_col = next((c for c in conn.execute("PRAGMA table_info(categories)").fetchall() if c["name"] == "id"), None)
    if id_col is not None and id_col["type"].upper() == "TEXT":
        return

    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")

        cat_rows = conn.execute(
            "SELECT id, company_id, name, description, created_at FROM categories"
        ).fetchall()
        id_map = {row["id"]: str(_uuid.uuid4()) for row in cat_rows}

        conn.execute("""
            CREATE TABLE categories_new (
                id TEXT PRIMARY KEY,
                company_id INTEGER DEFAULT 1,
                name TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for row in cat_rows:
            conn.execute(
                "INSERT INTO categories_new (id, company_id, name, description, created_at) VALUES (?,?,?,?,?)",
                (id_map[row["id"]], row["company_id"], row["name"], row["description"], row["created_at"]),
            )
        conn.execute("DROP TABLE categories")
        conn.execute("ALTER TABLE categories_new RENAME TO categories")

        conn.execute("""
            CREATE TABLE products_new (
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
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
            )
        """)
        conn.execute("""
            INSERT INTO products_new (id, company_id, sku, barcode, name, category_id,
                                       cost_price, sell_price, tax_rate, unit, reorder_level,
                                       status, created_at)
            SELECT id, company_id, sku, barcode, name, NULL,
                   cost_price, sell_price, tax_rate, unit, reorder_level,
                   status, created_at
            FROM products
        """)
        old_cat_ids = conn.execute(
            "SELECT DISTINCT category_id FROM products WHERE category_id IS NOT NULL"
        ).fetchall()
        for row in old_cat_ids:
            old_cid = row["category_id"]
            new_cid = id_map.get(old_cid)
            if new_cid is not None:
                conn.execute(
                    "UPDATE products_new SET category_id=? WHERE id IN "
                    "(SELECT id FROM products WHERE category_id=?)",
                    (new_cid, old_cid),
                )
        conn.execute("DROP TABLE products")
        conn.execute("ALTER TABLE products_new RENAME TO products")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk_was_on else 'OFF'}")


def _products_category_fk_is_set_null(conn):
    """True when products.category_id's FOREIGN KEY already declares
    ON DELETE SET NULL. Read from SQLite's own `PRAGMA foreign_key_list`
    (the parsed FK definition) rather than by string-matching the stored
    CREATE TABLE text, so formatting/whitespace differences between the base
    schema and a migrated rebuild can never make this misreport."""
    try:
        rows = conn.execute("PRAGMA foreign_key_list(products)").fetchall()
    except sqlite3.DatabaseError:
        return False
    for r in rows:
        if r["table"] == "categories" and r["from"] == "category_id":
            return (r["on_delete"] or "").upper() == "SET NULL"
    return False


def _migrate_products_category_fk_on_delete_set_null(conn):
    """One-time migration (schema v2 -> v3): products.category_id's FOREIGN
    KEY gains ON DELETE SET NULL.

    Why this is a correctness fix and not cosmetics: `_conn()` above sets
    `PRAGMA foreign_keys=ON` on EVERY connection, so a bare
    `REFERENCES categories(id)` is genuinely enforced. Categories are synced
    across devices on one license; products are NOT (this phase). Two devices
    therefore legitimately hold different product -> category assignments.
    Device A (no products in "Electronics") deletes that category and the
    delete is relayed; device B (which does have a product there) applies it
    via `commercial_runtime/sync/sync_service.py::_apply_event`'s
    `DELETE FROM categories WHERE id=?` and gets
    `IntegrityError: FOREIGN KEY constraint failed`. That aborts
    `apply_pull_result` before the cursor is advanced, and `run_once`
    swallows the exception -- so device B silently stops receiving ANY event
    from ANY device, permanently, with no self-healing. ON DELETE SET NULL
    makes the delete unassign B's products instead, which is also exactly
    the desktop UX intent for deleting a category locally.

    Structure mirrors `_migrate_categories_to_uuid` above (read its docstring
    for the full reasoning) and inherits its two safety properties verbatim:

    1. Runs with `PRAGMA foreign_keys` temporarily OFF (it cannot be toggled
       inside a transaction, so it is flipped before BEGIN and restored after
       COMMIT via try/finally) -- otherwise `DROP TABLE products` fails while
       inventory_movements/inventory_balances/purchase_order_items/
       sale_items/return_items rows still reference it.

    2. The original `products` name is never renamed away: the replacement is
       built as `products_new` and swapped in via `DROP products` +
       `RENAME products_new TO products`. Renaming `products` to
       `products_old` would make SQLite silently rewrite all five referencing
       tables' FK clauses to point at `products_old`.

    Idempotent (returns immediately when the FK is already SET NULL, on top
    of ensure_schema_version's user_version gate) and fully transactional:
    any failure rolls the whole rebuild back, leaving the database exactly as
    the pre-migration backup captured it for a clean retry on next launch.
    Every products row and every column value is preserved as-is -- this
    changes only the table's declared FK action, never any data.
    """
    if _products_category_fk_is_set_null(conn):
        return

    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")
        conn.execute("""
            CREATE TABLE products_new (
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
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
            )
        """)
        conn.execute("""
            INSERT INTO products_new (id, company_id, sku, barcode, name, category_id,
                                       cost_price, sell_price, tax_rate, unit, reorder_level,
                                       status, created_at)
            SELECT id, company_id, sku, barcode, name, category_id,
                   cost_price, sell_price, tax_rate, unit, reorder_level,
                   status, created_at
            FROM products
        """)
        conn.execute("DROP TABLE products")
        conn.execute("ALTER TABLE products_new RENAME TO products")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk_was_on else 'OFF'}")


def _migrate_products_to_uuid(conn):
    """One-time migration (schema v3 -> v4): products.id moves from
    INTEGER PRIMARY KEY AUTOINCREMENT to TEXT PRIMARY KEY (client-generated
    UUIDs), same reason categories got this in v1->v2: two offline devices
    must never collide on an id. Every table with a declared FK to
    products(id) -- inventory_movements, inventory_balances, sale_items,
    purchase_order_items, return_items -- is rebuilt in this SAME
    transaction and its product_id values remapped via an old-id -> new-uuid
    map, mirroring exactly how _migrate_categories_to_uuid remaps
    products.category_id (see that function's docstring for the full
    PRAGMA foreign_keys / rename-hazard reasoning, inherited verbatim here).

    Sales/Inventory/Returns/Payments are NOT synced this phase (see the
    design spec's Scope section) -- only their product_id VALUES are
    remapped here, as a one-time local consequence of products.id changing
    type. This migration runs identically on every device independently;
    it does not require or wait for any other device.
    """
    import uuid as _uuid

    id_col = next((c for c in conn.execute("PRAGMA table_info(products)").fetchall() if c["name"] == "id"), None)
    if id_col is not None and id_col["type"].upper() == "TEXT":
        return

    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")

        prod_rows = conn.execute(
            "SELECT id, company_id, sku, barcode, name, category_id, cost_price, sell_price, "
            "tax_rate, unit, reorder_level, status, created_at FROM products"
        ).fetchall()
        id_map = {row["id"]: str(_uuid.uuid4()) for row in prod_rows}

        conn.execute("""
            CREATE TABLE products_new (
                id TEXT PRIMARY KEY,
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
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
            )
        """)
        for row in prod_rows:
            conn.execute(
                "INSERT INTO products_new (id, company_id, sku, barcode, name, category_id, cost_price, "
                "sell_price, tax_rate, unit, reorder_level, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (id_map[row["id"]], row["company_id"], row["sku"], row["barcode"], row["name"],
                 row["category_id"], row["cost_price"], row["sell_price"], row["tax_rate"],
                 row["unit"], row["reorder_level"], row["status"], row["created_at"]),
            )
        conn.execute("DROP TABLE products")
        conn.execute("ALTER TABLE products_new RENAME TO products")

        referencing = [
            ("inventory_movements", """
                CREATE TABLE inventory_movements_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, product_id TEXT NOT NULL,
                    branch_id INTEGER, movement_type TEXT NOT NULL, quantity REAL NOT NULL, unit_cost REAL DEFAULT 0,
                    reference TEXT, notes TEXT, created_by TEXT DEFAULT 'System', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (product_id) REFERENCES products(id)
                )""", "id, company_id, product_id, branch_id, movement_type, quantity, unit_cost, reference, notes, created_by, created_at"),
            ("inventory_balances", """
                CREATE TABLE inventory_balances_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, product_id TEXT NOT NULL,
                    branch_id INTEGER NOT NULL, quantity_on_hand REAL DEFAULT 0, quantity_reserved REAL DEFAULT 0,
                    UNIQUE(company_id, product_id, branch_id), FOREIGN KEY (product_id) REFERENCES products(id)
                )""", "id, company_id, product_id, branch_id, quantity_on_hand, quantity_reserved"),
            ("sale_items", """
                CREATE TABLE sale_items_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, sale_id INTEGER NOT NULL, product_id TEXT NOT NULL,
                    quantity REAL NOT NULL, unit_price REAL NOT NULL, discount_pct REAL DEFAULT 0, tax_rate REAL DEFAULT 0,
                    line_total REAL NOT NULL, FOREIGN KEY (sale_id) REFERENCES sales(id), FOREIGN KEY (product_id) REFERENCES products(id)
                )""", "id, sale_id, product_id, quantity, unit_price, discount_pct, tax_rate, line_total"),
            ("purchase_order_items", """
                CREATE TABLE purchase_order_items_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, po_id INTEGER NOT NULL, product_id TEXT NOT NULL,
                    quantity REAL NOT NULL, unit_cost REAL NOT NULL, total REAL NOT NULL, received_qty REAL DEFAULT 0,
                    FOREIGN KEY (po_id) REFERENCES purchase_orders(id), FOREIGN KEY (product_id) REFERENCES products(id)
                )""", "id, po_id, product_id, quantity, unit_cost, total, received_qty"),
            ("return_items", """
                CREATE TABLE return_items_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, return_id INTEGER NOT NULL, product_id TEXT NOT NULL,
                    quantity REAL NOT NULL, unit_price REAL NOT NULL, line_total REAL NOT NULL,
                    FOREIGN KEY (return_id) REFERENCES returns(id), FOREIGN KEY (product_id) REFERENCES products(id)
                )""", "id, return_id, product_id, quantity, unit_price, line_total"),
        ]
        for table, create_sql, cols in referencing:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if exists is None:
                # `init_retail()` always creates every one of these tables
                # (via CREATE TABLE IF NOT EXISTS) before this migration ever
                # runs, so a real device is never missing one -- this guard
                # only protects a hand-built/partial database (e.g. a test
                # fixture) from an unconditional rebuild attempt against a
                # table that was never created.
                continue
            conn.execute(create_sql)
            col_list = [c.strip() for c in cols.split(",")]
            select_cols = ", ".join(c if c != "product_id" else "product_id" for c in col_list)
            conn.execute(f"INSERT INTO {table}_new ({cols}) SELECT {select_cols} FROM {table}")
            old_pids = conn.execute(f"SELECT DISTINCT product_id FROM {table} WHERE product_id IS NOT NULL").fetchall()
            for row in old_pids:
                old_pid = row["product_id"]
                new_pid = id_map.get(old_pid)
                if new_pid is not None:
                    conn.execute(
                        f"UPDATE {table}_new SET product_id=? WHERE id IN (SELECT id FROM {table} WHERE product_id=?)",
                        (new_pid, old_pid),
                    )
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk_was_on else 'OFF'}")


def _migrate_customers_to_uuid(conn):
    """One-time migration (still schema v4): customers.id moves from
    INTEGER PRIMARY KEY AUTOINCREMENT to TEXT PRIMARY KEY, adds a new
    `status` column (customers never had one), and folds the
    credit_mode/credit_limit/credit_balance columns _ensure_credit_schema
    (api/retail_api.py) adds at runtime via ALTER TABLE into the rebuilt
    table as first-class columns -- _ensure_credit_schema's own addcol()
    calls stay in place as a no-op safety net (PRAGMA table_info already
    finding the column is exactly what makes addcol() skip it).

    No table has a declared FK to customers(id) -- sales.customer_id and
    payments.party_id are both loose, undeclared columns (confirmed: grep
    "REFERENCES customers" across schema.py returns nothing) -- so there is
    no SQLite auto-rewrite-on-rename hazard here. This still rebuilds under
    `customers_new` and swaps in, for consistency with every other
    migration in this file, and because relying on "no FK today" staying
    true forever is not a safe long-term assumption to bake into a
    rename-based migration.
    """
    import uuid as _uuid

    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='customers'"
    ).fetchone()
    if exists is None:
        # `init_retail()` always creates `customers` (via CREATE TABLE IF NOT
        # EXISTS) before this migration ever runs, so a real device is never
        # missing it -- this guard only protects a hand-built/partial
        # database (e.g. a test fixture that only sets up categories/
        # products) from an unconditional rebuild attempt against a table
        # that was never created. Mirrors the identical guard in
        # _migrate_products_to_uuid's referencing-table loop above.
        return

    id_col = next((c for c in conn.execute("PRAGMA table_info(customers)").fetchall() if c["name"] == "id"), None)
    if id_col is not None and id_col["type"].upper() == "TEXT":
        return

    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")

        existing_cols = {c["name"] for c in conn.execute("PRAGMA table_info(customers)").fetchall()}
        has_credit = "credit_mode" in existing_cols  # _ensure_credit_schema may have already run on this db

        cust_rows = conn.execute("SELECT * FROM customers").fetchall()
        id_map = {row["id"]: str(_uuid.uuid4()) for row in cust_rows}

        conn.execute("""
            CREATE TABLE customers_new (
                id TEXT PRIMARY KEY,
                company_id INTEGER DEFAULT 1,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                address TEXT,
                loyalty_points REAL DEFAULT 0,
                total_spent REAL DEFAULT 0,
                status TEXT DEFAULT 'active',
                credit_mode TEXT DEFAULT 'none',
                credit_limit REAL DEFAULT 0,
                credit_balance REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for row in cust_rows:
            conn.execute(
                "INSERT INTO customers_new (id, company_id, name, phone, email, address, loyalty_points, "
                "total_spent, status, credit_mode, credit_limit, credit_balance, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (id_map[row["id"]], row["company_id"], row["name"], row["phone"], row["email"], row["address"],
                 row["loyalty_points"], row["total_spent"], "active",
                 row["credit_mode"] if has_credit else "none",
                 row["credit_limit"] if has_credit else 0,
                 row["credit_balance"] if has_credit else 0,
                 row["created_at"]),
            )
        conn.execute("DROP TABLE customers")
        conn.execute("ALTER TABLE customers_new RENAME TO customers")

        for row in cust_rows:
            old_id, new_id = row["id"], id_map[row["id"]]
            conn.execute("UPDATE sales SET customer_id=? WHERE customer_id=?", (new_id, old_id))
            conn.execute(
                "UPDATE payments SET party_id=? WHERE party_type='customer' AND party_id=?",
                (new_id, old_id),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk_was_on else 'OFF'}")


def _migrate_suppliers_to_uuid(conn):
    """One-time migration (still schema v4): suppliers.id moves from
    INTEGER PRIMARY KEY AUTOINCREMENT to TEXT PRIMARY KEY, folding
    payment_terms/credit_balance (added at runtime by _ensure_credit_schema)
    into the rebuilt table as first-class columns, same reasoning as
    _migrate_customers_to_uuid.

    Unlike customers, purchase_orders.supplier_id IS a declared FK
    (`FOREIGN KEY (supplier_id) REFERENCES suppliers(id)`, schema.py:428) --
    same rename-away hazard as products/categories: suppliers is rebuilt
    under `suppliers_new` and swapped in, never renamed away directly.
    """
    import uuid as _uuid

    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='suppliers'"
    ).fetchone()
    if exists is None:
        # `init_retail()` always creates `suppliers` (via CREATE TABLE IF NOT
        # EXISTS) before this migration ever runs, so a real device is never
        # missing it -- this guard only protects a hand-built/partial
        # database (e.g. a test fixture that only sets up categories/
        # products) from an unconditional rebuild attempt against a table
        # that was never created. Mirrors the identical guard in
        # _migrate_customers_to_uuid above.
        return

    id_col = next((c for c in conn.execute("PRAGMA table_info(suppliers)").fetchall() if c["name"] == "id"), None)
    if id_col is not None and id_col["type"].upper() == "TEXT":
        return

    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")

        existing_cols = {c["name"] for c in conn.execute("PRAGMA table_info(suppliers)").fetchall()}
        has_credit = "payment_terms" in existing_cols

        sup_rows = conn.execute("SELECT * FROM suppliers").fetchall()
        id_map = {row["id"]: str(_uuid.uuid4()) for row in sup_rows}

        conn.execute("""
            CREATE TABLE suppliers_new (
                id TEXT PRIMARY KEY,
                company_id INTEGER DEFAULT 1,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                address TEXT,
                status TEXT DEFAULT 'active',
                payment_terms TEXT DEFAULT 'none',
                credit_balance REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for row in sup_rows:
            conn.execute(
                "INSERT INTO suppliers_new (id, company_id, name, phone, email, address, status, "
                "payment_terms, credit_balance, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (id_map[row["id"]], row["company_id"], row["name"], row["phone"], row["email"], row["address"],
                 row["status"],
                 row["payment_terms"] if has_credit else "none",
                 row["credit_balance"] if has_credit else 0,
                 row["created_at"]),
            )
        conn.execute("DROP TABLE suppliers")
        conn.execute("ALTER TABLE suppliers_new RENAME TO suppliers")

        for row in sup_rows:
            old_id, new_id = row["id"], id_map[row["id"]]
            conn.execute("UPDATE purchase_orders SET supplier_id=? WHERE supplier_id=?", (new_id, old_id))
            conn.execute(
                "UPDATE payments SET party_id=? WHERE party_type='supplier' AND party_id=?",
                (new_id, old_id),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk_was_on else 'OFF'}")


def _migrate_retail_schema(conn):
    """The single `migrate_fn` handed to `ensure_schema_version` -- runs every
    migration this file owns, in version order, on any database behind
    RETAIL_SCHEMA_VERSION. `ensure_schema_version` only tells a migration
    "you are behind", not "you are behind by exactly one version", so a v1
    install upgrading straight to v4 must run ALL steps in one pass. Each
    step is independently idempotent (each inspects the live schema and
    returns immediately when its own change is already present), so running
    them all is correct regardless of which version the database actually
    starts from."""
    _migrate_categories_to_uuid(conn)
    _migrate_products_category_fk_on_delete_set_null(conn)
    _migrate_products_to_uuid(conn)
    _migrate_customers_to_uuid(conn)
    _migrate_suppliers_to_uuid(conn)
    _migrate_products_add_supplier_fk(conn)
    _migrate_add_supplier_contacts_and_po_split(conn)
    _migrate_sync_outbox_ordering_and_dead_letter(conn)


def _migrate_products_add_supplier_fk(conn):
    """One-time migration (schema v4 -> v5): adds products.supplier_id.

    TEXT, not INTEGER -- suppliers.id is already UUID text by the time this
    runs (_migrate_suppliers_to_uuid, just above, runs first). This is a
    brand-new nullable column, not a change to an existing constraint, so
    unlike _migrate_products_category_fk_on_delete_set_null above, no
    DROP+RENAME rebuild is needed: SQLite allows ADD COLUMN with a
    REFERENCES clause directly, and NULL always satisfies a foreign key
    check, so existing rows are left unassigned rather than backfilled.

    Idempotent: returns immediately if the column already exists.
    """
    cols = {row[1] for row in conn.execute('PRAGMA table_info(products)').fetchall()}
    if 'supplier_id' in cols:
        return
    conn.execute('ALTER TABLE products ADD COLUMN supplier_id TEXT REFERENCES suppliers(id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_products_supplier ON products(supplier_id)')


def _migrate_add_supplier_contacts_and_po_split(conn):
    """One-time migration (schema v5 -> v6): PO-preview-by-supplier
    foundation (Thursday demo, Stream B -- descoped to preview-only PO
    splitting by supplier; the routes that write these columns land later
    this week, not today).

    Three independent additive pieces, each guarded by its own idempotency
    check (mirrors _migrate_products_add_supplier_fk just above -- brand-new
    nullable columns / IF NOT EXISTS tables, so unlike the UUID migrations
    earlier in this chain, no DROP+RENAME rebuild is needed anywhere here):

    1. `supplier_contacts` -- a supplier can have several named contacts
       (orders/accounts/general), each with its own preferred channel. New
       table entirely, so CREATE TABLE IF NOT EXISTS is itself idempotent.

    2. `purchase_orders` gains split-group/routing/idempotency columns:
       split_group_id/split_index/split_count identify which slice of a
       supplier-split preview a PO belongs to; routing_status/routed_channel/
       routed_at/routed_to record how (and whether) it was sent once routing
       ships; idempotency_key gets the same partial-unique-index treatment as
       `returns.idempotency_key` (see api/retail_api.py's
       `_ensure_credit_schema`, `idx_returns_idempotency`) -- SQLite can't add
       a UNIQUE column via ALTER TABLE, so a partial unique index does the
       job instead, and NULLs (every pre-v6 row) are exempt, matching
       SQLite's own UNIQUE-column NULL semantics.

    3. `suppliers.min_order_value` -- procurement metadata the split
       preview's MOQ warning reads; not touched by anything else yet.

    Idempotent: each ALTER COLUMN is preceded by a PRAGMA table_info check,
    each CREATE TABLE/INDEX already uses IF NOT EXISTS, so a second call
    (or a fresh install that already has this shape via init_retail's base
    executescript) is a clean no-op.
    """
    existing_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    if 'supplier_contacts' not in existing_tables:
        conn.execute("""
            CREATE TABLE supplier_contacts (
                id TEXT PRIMARY KEY,
                company_id TEXT,
                supplier_id TEXT NOT NULL REFERENCES suppliers(id),
                name TEXT NOT NULL,
                role TEXT DEFAULT 'orders',
                email TEXT,
                phone TEXT,
                whatsapp TEXT,
                channel_preference TEXT DEFAULT 'whatsapp',
                is_primary INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_supplier_contacts_supplier "
        "ON supplier_contacts(company_id, supplier_id, status)"
    )

    if 'purchase_orders' in existing_tables:
        po_cols = {row[1] for row in conn.execute('PRAGMA table_info(purchase_orders)').fetchall()}
        for col, decl in [
            ('split_group_id', 'TEXT'),
            ('split_index', 'INTEGER'),
            ('split_count', 'INTEGER'),
            ('routing_status', 'TEXT'),
            ('routed_channel', 'TEXT'),
            ('routed_at', 'TEXT'),
            ('routed_to', 'TEXT'),
            ('idempotency_key', 'TEXT'),
        ]:
            if col not in po_cols:
                conn.execute(f'ALTER TABLE purchase_orders ADD COLUMN {col} {decl}')
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_po_split_group "
            "ON purchase_orders(company_id, split_group_id)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_po_idempotency "
            "ON purchase_orders(idempotency_key) WHERE idempotency_key IS NOT NULL"
        )

    if 'suppliers' in existing_tables:
        sup_cols = {row[1] for row in conn.execute('PRAGMA table_info(suppliers)').fetchall()}
        if 'min_order_value' not in sup_cols:
            conn.execute('ALTER TABLE suppliers ADD COLUMN min_order_value REAL DEFAULT 0')


def _normalize_legacy_outbox_created_at(value):
    """Best-effort normalization of a pre-v7 sync_outbox.created_at value
    into the same shape the sole writer (_queue_sync_event) has always
    produced: `datetime.now(timezone.utc).isoformat()`, T-separated.

    Only ever touches a value that could not have come from that writer --
    i.e. one that still has SQLite's own space-separated
    `CURRENT_TIMESTAMP` shape ("YYYY-MM-DD HH:MM:SS"), which only a row that
    fell back to the (about to be removed) column default could have. Real
    installs on this branch are not expected to actually have any such row
    (the writer has always supplied its own value), but this is written
    defensively in case one exists rather than assuming it never happened.
    CURRENT_TIMESTAMP is documented UTC, so a bare space -> 'T' swap plus an
    explicit '+00:00' offset is a faithful (if second-precision-only)
    isoformat equivalent -- exact sub-second ordering among normalized rows
    is still resolved correctly by the `rowid` tiebreaker read_outbox() now
    also orders by, so losing sub-second precision here is harmless."""
    if not value or 'T' in value:
        return value  # already isoformat-shaped (or empty/NULL) -- leave untouched
    normalized = value.replace(' ', 'T', 1)
    if '+' not in normalized and 'Z' not in normalized:
        normalized += '+00:00'
    return normalized


def _migrate_sync_outbox_ordering_and_dead_letter(conn):
    """One-time migration (schema v6 -> v7): closes the outbox-wedge bug
    found in the 2026-08-10 audit -- see RETAIL_SCHEMA_VERSION's v7 comment
    above and commercial_runtime/sync/sync_service.py's module docstring for
    the full writeup. Three independent pieces, applied together because the
    first (the rebuild) is the natural place to add the other two's columns
    without a second full-table rebuild:

    1. sync_outbox is rebuilt (SQLite has no ALTER COLUMN) so created_at
       loses `DEFAULT CURRENT_TIMESTAMP` and becomes NOT NULL -- see
       _normalize_legacy_outbox_created_at above for why existing rows are
       safe to carry across as-is (or normalized, for the one shape that
       would not be). Uses the same CREATE-`_new`-then-DROP-then-RENAME
       technique as _migrate_categories_to_uuid (v1 -> v2) -- no other table
       declares a FOREIGN KEY against sync_outbox, so unlike that migration
       this needs no `PRAGMA foreign_keys=OFF` dance.

    2. attempt_count (INTEGER NOT NULL DEFAULT 0) / last_error (TEXT) added
       to the rebuilt table -- read/written by SyncService.push_once()'s new
       rejection-tracking and bisection logic.

    3. sync_dead_letter created (CREATE TABLE IF NOT EXISTS -- new table,
       trivially idempotent on its own).

    Idempotent as a whole: guarded by a PRAGMA table_info check on
    sync_outbox's own `created_at` column (dflt_value already NULL and
    notnull already 1 means this already ran) before doing anything else.

    Defensive against sync_outbox not existing at all yet: several
    hand-built pre-sync-foundation test databases (and, in principle, a
    real install that started life on a schema version before sync_outbox
    was introduced and is upgrading straight through several versions in
    one `ensure_schema_version` pass) legitimately have no sync_outbox table
    the instant before this runs. That case just creates it directly in the
    final v7 shape -- there is nothing to copy, so no rebuild is needed.
    """
    existing_tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }

    if 'sync_outbox' not in existing_tables:
        conn.execute("""
            CREATE TABLE sync_outbox (
                id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            )
        """)
    else:
        cols = {row["name"]: row for row in conn.execute('PRAGMA table_info(sync_outbox)').fetchall()}
        created_at_col = cols.get('created_at')
        already_rebuilt = (
            created_at_col is not None
            and created_at_col["dflt_value"] is None
            and created_at_col["notnull"] == 1
        )

        if not already_rebuilt:
            conn.execute("""
                CREATE TABLE sync_outbox_new (
                    id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                )
            """)
            rows = conn.execute(
                "SELECT id, entity_type, entity_id, event_type, payload, created_at FROM sync_outbox"
            ).fetchall()
            for row in rows:
                created_at = _normalize_legacy_outbox_created_at(row["created_at"])
                conn.execute(
                    "INSERT INTO sync_outbox_new (id, entity_type, entity_id, event_type, payload, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (row["id"], row["entity_type"], row["entity_id"], row["event_type"], row["payload"], created_at),
                )
            conn.execute("DROP TABLE sync_outbox")
            conn.execute("ALTER TABLE sync_outbox_new RENAME TO sync_outbox")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_dead_letter (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            dead_lettered_at TIMESTAMP NOT NULL
        )
    """)


def init_retail():
    conn = get_retail_conn()
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS branches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        name TEXT NOT NULL,
        address TEXT,
        phone TEXT,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        name TEXT NOT NULL,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS products (
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
        -- ON DELETE SET NULL, not a bare REFERENCES: a category delete
        -- relayed in from ANOTHER device must never be able to fail against
        -- this device's own product links (products are not synced, so two
        -- devices on one license legitimately hold different product ->
        -- category assignments). See
        -- _migrate_products_category_fk_on_delete_set_null above.
        FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS inventory_movements (
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
    CREATE TABLE IF NOT EXISTS inventory_balances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        product_id INTEGER NOT NULL,
        branch_id INTEGER NOT NULL,
        quantity_on_hand REAL DEFAULT 0,
        quantity_reserved REAL DEFAULT 0,
        UNIQUE(company_id, product_id, branch_id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    );
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        name TEXT NOT NULL,
        phone TEXT,
        email TEXT,
        address TEXT,
        loyalty_points REAL DEFAULT 0,
        total_spent REAL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS suppliers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        name TEXT NOT NULL,
        phone TEXT,
        email TEXT,
        address TEXT,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        -- PO-preview-by-supplier foundation (schema v6): procurement
        -- metadata the split preview's MOQ warning reads. See
        -- _migrate_add_supplier_contacts_and_po_split below -- included
        -- here too so a brand-new install gets v6 shape directly without
        -- ever running that migration, same as sync_outbox/sync_cursor.
        min_order_value REAL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS purchase_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        po_number TEXT UNIQUE,
        supplier_id INTEGER,
        branch_id INTEGER,
        status TEXT DEFAULT 'pending',
        subtotal REAL DEFAULT 0,
        tax_total REAL DEFAULT 0,
        total REAL DEFAULT 0,
        notes TEXT,
        ordered_at TEXT,
        received_at TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        -- PO-preview-by-supplier foundation (schema v6): split-group/
        -- routing/idempotency columns -- the writers that populate them
        -- land later this week, this only adds the columns. See
        -- _migrate_add_supplier_contacts_and_po_split below -- included
        -- here too so a brand-new install gets v6 shape directly without
        -- ever running that migration, same as sync_outbox/sync_cursor.
        split_group_id TEXT,
        split_index INTEGER,
        split_count INTEGER,
        routing_status TEXT,
        routed_channel TEXT,
        routed_at TEXT,
        routed_to TEXT,
        idempotency_key TEXT,
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    );
    CREATE TABLE IF NOT EXISTS purchase_order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        po_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity REAL NOT NULL,
        unit_cost REAL NOT NULL,
        total REAL NOT NULL,
        received_qty REAL DEFAULT 0,
        FOREIGN KEY (po_id) REFERENCES purchase_orders(id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    );
    CREATE TABLE IF NOT EXISTS sales (
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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sale_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sale_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity REAL NOT NULL,
        unit_price REAL NOT NULL,
        discount_pct REAL DEFAULT 0,
        tax_rate REAL DEFAULT 0,
        line_total REAL NOT NULL,
        FOREIGN KEY (sale_id) REFERENCES sales(id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    );
    CREATE TABLE IF NOT EXISTS returns (
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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (sale_id) REFERENCES sales(id)
    );
    CREATE TABLE IF NOT EXISTS return_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        return_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity REAL NOT NULL,
        unit_price REAL NOT NULL,
        line_total REAL NOT NULL,
        FOREIGN KEY (return_id) REFERENCES returns(id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    );
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        sale_id INTEGER,
        method TEXT NOT NULL,
        amount REAL NOT NULL,
        reference TEXT,
        status TEXT DEFAULT 'success',
        idempotency_key TEXT UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (sale_id) REFERENCES sales(id)
    );
    CREATE TABLE IF NOT EXISTS tax_rates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        name TEXT NOT NULL,
        rate REAL NOT NULL,
        is_default INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS journal_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        reference TEXT,
        description TEXT,
        debit_account TEXT,
        credit_account TEXT,
        amount REAL NOT NULL,
        entry_type TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        user_id TEXT,
        action TEXT NOT NULL,
        entity TEXT,
        entity_id INTEGER,
        details TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    -- Multi-device sync foundation (2026-08-06): sync_outbox holds locally
    -- committed writes not yet relayed to the Owner; sync_cursor is a
    -- single-row (id=1) high-water-mark of the last Owner-side seq this
    -- device has pulled. Consumed by Task 4 (routes write to sync_outbox)
    -- and Task 5 (sync client reads/writes both).
    --
    -- Outbox-wedge fix (schema v7): created_at is NOT NULL with NO default
    -- -- the sole writer (_queue_sync_event in api/retail_api.py, mirrored
    -- by scripts/seed_demo_sweets.py) has always supplied its own
    -- T-separated isoformat() value; a silent fallback to SQLite's own
    -- space-separated CURRENT_TIMESTAMP default sorts BEFORE every real
    -- value in a plain string ORDER BY, which can reorder a delete before
    -- its own create. See _migrate_sync_outbox_ordering_and_dead_letter
    -- below for the existing-install migration and the full writeup, and
    -- commercial_runtime/sync/sync_service.py's module docstring for how
    -- attempt_count/last_error are used. Included here too so a brand-new
    -- install gets v7 shape directly without ever running that migration,
    -- same as every other version bump in this file.
    CREATE TABLE IF NOT EXISTS sync_outbox (
        id TEXT PRIMARY KEY,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        last_error TEXT
    );
    CREATE TABLE IF NOT EXISTS sync_cursor (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        last_seq INTEGER NOT NULL DEFAULT 0
    );
    INSERT OR IGNORE INTO sync_cursor (id, last_seq) VALUES (1, 0);
    -- Outbox-wedge fix (schema v7): landing spot for a sync_outbox row
    -- SyncService.push_once() has isolated, via bisection, as the specific
    -- offender in a batch the relay keeps rejecting -- see
    -- _migrate_sync_outbox_ordering_and_dead_letter below.
    CREATE TABLE IF NOT EXISTS sync_dead_letter (
        id TEXT PRIMARY KEY,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        dead_lettered_at TIMESTAMP NOT NULL
    );
    -- PO-preview-by-supplier foundation (schema v6): a supplier can have
    -- several named contacts (orders/accounts/general), each with its own
    -- preferred channel -- read by core/retail/po_split.py's contact
    -- resolution ladder. See _migrate_add_supplier_contacts_and_po_split
    -- below -- included here too so a brand-new install gets v6 shape
    -- directly without ever running that migration, same as sync_outbox/
    -- sync_cursor just above.
    CREATE TABLE IF NOT EXISTS supplier_contacts (
        id TEXT PRIMARY KEY,
        company_id TEXT,
        supplier_id TEXT NOT NULL REFERENCES suppliers(id),
        name TEXT NOT NULL,
        role TEXT DEFAULT 'orders',
        email TEXT,
        phone TEXT,
        whatsapp TEXT,
        channel_preference TEXT DEFAULT 'whatsapp',
        is_primary INTEGER DEFAULT 0,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_supplier_contacts_supplier
        ON supplier_contacts(company_id, supplier_id, status);
    CREATE INDEX IF NOT EXISTS idx_po_split_group
        ON purchase_orders(company_id, split_group_id);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_po_idempotency
        ON purchase_orders(idempotency_key) WHERE idempotency_key IS NOT NULL;
    """)
    conn.commit()

    # Wave 1B (Part K) / multi-device sync foundation (2026-08-06): first
    # real Retail schema changes -- see _migrate_retail_schema above, which
    # chains _migrate_categories_to_uuid (v2) and
    # _migrate_products_category_fk_on_delete_set_null (v3).
    # Mirrors products/clinic/backend/database/schema.py's identical
    # ensure_schema_version pattern; see
    # commercial_runtime/security/migration_safety.py.
    from commercial_runtime.security.migration_safety import ensure_schema_version
    ensure_schema_version(
        conn, _get_path('retail'), RETAIL_SCHEMA_VERSION, _migrate_retail_schema,
        backup_dir=os.path.join(BASE_DIR, 'migration_backups'),
    )

    if cur.execute("SELECT COUNT(*) FROM branches").fetchone()[0] == 0 and not _is_standalone():
        _seed_retail(conn, cur)
    conn.close()


def _seed_retail(conn, cur, company_id=1):
    """Seed demo data for ONE company. `company_id` defaults to 1 to preserve
    the existing dev-mode first-boot behaviour. Every INSERT is parameterized
    on company_id -- callers that need to reset a specific company's demo
    data (see api/retail_api.py::demo_seed) must pass company_id explicitly."""
    now = datetime.now()
    cid = company_id

    cur.executemany("INSERT INTO branches (company_id,name,address,phone) VALUES (?,?,?,?)", [
        (cid, 'Main Branch', '100 Market Street, Downtown', '+1-555-1001'),
        (cid, 'North Branch', '45 Commerce Ave, Northside', '+1-555-1002'),
        (cid, 'West Mall', '200 Shopping Blvd, Westgate', '+1-555-1003'),
    ])

    # categories.id is TEXT (client-generated UUID) since the multi-device
    # sync foundation migration (see _migrate_categories_to_uuid) -- there is
    # no autoincrement to fall back on, so seed data must generate its own
    # ids explicitly, same as any real device would.
    import uuid as _uuid
    category_names = ['Electronics', 'Clothing', 'Food & Beverages', 'Home & Living', 'Health & Beauty']
    category_ids = [str(_uuid.uuid4()) for _ in category_names]
    cur.executemany("INSERT INTO categories (id,company_id,name) VALUES (?,?,?)", [
        (category_ids[i], cid, category_names[i]) for i in range(len(category_names))
    ])

    # suppliers.id is TEXT (client-generated UUID) since the multi-device
    # sync foundation migration (see _migrate_suppliers_to_uuid) -- there is
    # no autoincrement to fall back on, so seed data must generate its own
    # ids explicitly, same as categories/products/customers above.
    supplier_ids = [str(_uuid.uuid4()) for _ in range(3)]
    cur.executemany("INSERT INTO suppliers (id,company_id,name,phone,email) VALUES (?,?,?,?,?)", [
        (supplier_ids[0], cid, 'TechDistrib Ltd', '+1-555-9001', 'orders@techdistrib.com'),
        (supplier_ids[1], cid, 'FashionWholesale Co', '+1-555-9002', 'supply@fashionwholesale.com'),
        (supplier_ids[2], cid, 'GroceryDirect', '+1-555-9003', 'bulk@grocerydirect.com'),
    ])

    cur.execute("INSERT INTO tax_rates (company_id,name,rate,is_default) VALUES (?,'Standard VAT',15,1)", (cid,))
    cur.execute("INSERT INTO tax_rates (company_id,name,rate,is_default) VALUES (?,'Zero Rated',0,0)", (cid,))
    cur.execute("INSERT INTO tax_rates (company_id,name,rate,is_default) VALUES (?,'Reduced Rate',5,0)", (cid,))

    # 4th element is a 1-based index into category_names/category_ids above
    # (1=Electronics .. 5=Health & Beauty), preserved from the source data's
    # positional convention -- resolved to the real generated UUID below,
    # since category_id is no longer a small autoincrement int.
    products = [
        ('SKU-R001', '6001001', 'Laptop 15" Pro', 1, 750.0, 1199.99, 15, 'pcs', 3),
        ('SKU-R002', '6001002', 'Wireless Earbuds', 1, 25.0, 59.99, 15, 'pcs', 10),
        ('SKU-R003', '6001003', 'Smart Watch', 1, 85.0, 199.99, 15, 'pcs', 5),
        ('SKU-R004', '6001004', 'USB-C Charger', 1, 8.0, 24.99, 15, 'pcs', 15),
        ('SKU-R005', '6001005', 'Classic T-Shirt', 2, 4.5, 14.99, 0, 'pcs', 20),
        ('SKU-R006', '6001006', 'Denim Jeans', 2, 18.0, 49.99, 0, 'pcs', 10),
        ('SKU-R007', '6001007', 'Running Shoes', 2, 35.0, 89.99, 0, 'pcs', 8),
        ('SKU-R008', '6001008', 'Mineral Water 1L', 3, 0.3, 1.49, 0, 'pcs', 50),
        ('SKU-R009', '6001009', 'Orange Juice 1L', 3, 0.8, 2.99, 0, 'pcs', 40),
        ('SKU-R010', '6001010', 'Coffee Blend 500g', 3, 5.0, 12.99, 0, 'pcs', 25),
        ('SKU-R011', '6001011', 'LED Desk Lamp', 4, 12.0, 34.99, 15, 'pcs', 8),
        ('SKU-R012', '6001012', 'Shampoo 400ml', 5, 2.5, 7.99, 5, 'pcs', 20),
    ]
    # products.id is TEXT (client-generated UUID) since the multi-device sync
    # foundation migration (see _migrate_products_to_uuid) -- there is no
    # autoincrement to fall back on, so seed data must generate its own ids
    # explicitly, same as any real device would (mirrors the categories fix
    # above; `_uuid` is already imported there, reused here).
    product_ids = [str(_uuid.uuid4()) for _ in products]
    for i, p in enumerate(products):
        cat_id = category_ids[p[3] - 1]
        row = (p[0], p[1], p[2], cat_id) + p[4:]
        cur.execute(
            "INSERT INTO products (id,company_id,sku,barcode,name,category_id,cost_price,sell_price,tax_rate,unit,reorder_level) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (product_ids[i], cid) + row,
        )

    cur.execute("SELECT id FROM products WHERE company_id=?", (cid,))
    prod_ids = [r[0] for r in cur.fetchall()]
    for pid in prod_ids:
        qty = random.randint(10, 150)
        cur.execute(
            "INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,1,?)",
            (cid, pid, qty)
        )
        cur.execute(
            "INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,2,?)",
            (cid, pid, random.randint(5, 60))
        )

    customers = [
        ('Ahmed Al-Mansouri', '+1-555-2001', 'ahmed@email.com', 450, 3200.0),
        ('Sara Johnson', '+1-555-2002', 'sara@email.com', 120, 890.0),
        ('Michael Chen', '+1-555-2003', 'mchen@email.com', 800, 6500.0),
        ('Fatima Al-Hassan', '+1-555-2004', 'fatima@email.com', 60, 340.0),
        ('Carlos Rivera', '+1-555-2005', 'carlos@email.com', 290, 2100.0),
    ]
    # customers.id is TEXT (client-generated UUID) since the multi-device
    # sync foundation migration (see _migrate_customers_to_uuid) -- there is
    # no autoincrement to fall back on, so seed data must generate its own
    # ids explicitly, same as categories/products above.
    customer_ids = [str(_uuid.uuid4()) for _ in customers]
    for i, c in enumerate(customers):
        cur.execute(
            "INSERT INTO customers (id,company_id,name,phone,email,loyalty_points,total_spent) VALUES (?,?,?,?,?,?,?)",
            (customer_ids[i], cid) + c,
        )

    from core.retail import pricing as _tax_engine
    try:
        _row = cur.execute(
            "SELECT svalue FROM retail_settings WHERE company_id=? AND skey='tax_calculation_mode'", (cid,)
        ).fetchone()
        tax_mode = _tax_engine.normalize_mode(_row[0] if _row else None)
    except Exception:
        tax_mode = _tax_engine.DEFAULT_MODE  # retail_settings not created yet (fresh install) -- use the default
    sale_num = 1000
    methods = ['cash', 'cash', 'card', 'card', 'digital_wallet']
    for i in range(40):
        d = (now - timedelta(days=random.randint(0, 30))).strftime('%Y-%m-%d %H:%M:%S')
        branch_id = random.choice([1, 2])
        # customer_ids holds real generated UUIDs (customers.id is TEXT now,
        # see _migrate_customers_to_uuid) -- picking from it, not a
        # hardcoded 1-5 range, mirrors the cat_id fix above for the same
        # reason: a stale small-int id would never match any real customer
        # row via `s.customer_id=c.id`, silently showing every demo sale as
        # "Walk-in".
        cust_id = random.choice(customer_ids + [None])
        method = random.choice(methods)
        num_items = random.randint(1, 4)
        subtotal = 0
        tax_total = 0
        sale_num += 1
        sn = f'S-{sale_num}'
        cur.execute(
            "INSERT INTO sales (company_id,sale_number,branch_id,customer_id,cashier,payment_method,status,created_at) VALUES (?,?,?,?,?,?,'completed',?)",
            (cid, sn, branch_id, cust_id, 'Cashier', method, d)
        )
        sid = cur.lastrowid
        for _ in range(num_items):
            pid = random.choice(prod_ids)
            row = cur.execute("SELECT sell_price,tax_rate FROM products WHERE id=?", (pid,)).fetchone()
            price, trate = row[0], row[1]
            qty = random.randint(1, 3)
            disc = random.choice([0, 0, 0, 5, 10])
            _calc = _tax_engine.calculate_line(price, qty, discount_pct=disc, tax_rate=trate, mode=tax_mode)
            line = round(_calc['gross'] - _calc['discount_amount'], 2)
            tax = _calc['tax']
            subtotal += line
            tax_total += tax
            cur.execute(
                "INSERT INTO sale_items (sale_id,product_id,quantity,unit_price,discount_pct,tax_rate,line_total) VALUES (?,?,?,?,?,?,?)",
                (sid, pid, qty, price, disc, trate, line)
            )
            cur.execute(
                "INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,unit_cost,reference,created_by) VALUES (?,?,?,?,?,?,?,?)",
                (cid, pid, branch_id, 'sale_out', -qty, price * 0.6, sn, 'System')
            )
        total = round(subtotal + tax_total, 2)
        cur.execute(
            "UPDATE sales SET subtotal=?,tax_amount=?,total=?,amount_paid=?,change_amount=0 WHERE id=?",
            (round(subtotal, 2), round(tax_total, 2), total, total, sid)
        )

    conn.commit()
