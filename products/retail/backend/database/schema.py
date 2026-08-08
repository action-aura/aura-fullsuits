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
# v3 -> v4 (docs/einvoicing/phase1/, merged 2026-08-08): adds the einvoice_*
# tables. Originally cut as its own "v2" on a branch from before the sync
# migrations above existed -- renumbered to v4 here since both branches
# independently claimed v2 for unrelated migrations. See
# _apply_retail_alters below -- never lower this or reuse a number.
RETAIL_SCHEMA_VERSION = 4


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


def _migrate_retail_schema(conn):
    """The single `migrate_fn` handed to `ensure_schema_version` -- runs every
    migration this file owns, in version order, on any database behind
    RETAIL_SCHEMA_VERSION. `ensure_schema_version` only tells a migration
    "you are behind", not "you are behind by exactly one version", so a v1
    install upgrading straight to v4 must run every step in one pass. Each
    step is independently idempotent (each inspects the live schema and
    returns immediately when its own change is already present), so running
    them all is correct regardless of which version the database actually
    starts from."""
    _migrate_categories_to_uuid(conn)
    _migrate_products_category_fk_on_delete_set_null(conn)
    _apply_retail_alters(conn)


def _apply_retail_alters(conn):
    """v3 -> v4 (docs/einvoicing/phase1/): adds the einvoice_* tables used by
    opt-in Jordan JoFotara e-invoicing. CREATE TABLE IF NOT EXISTS only --
    no existing table is ALTERed, no existing row is read or written. A
    pilot install that never enables the feature gains only empty tables."""
    from commercial_runtime.einvoicing.schema import apply_einvoicing_schema
    apply_einvoicing_schema(conn)


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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    CREATE TABLE IF NOT EXISTS sync_outbox (
        id TEXT PRIMARY KEY,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sync_cursor (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        last_seq INTEGER NOT NULL DEFAULT 0
    );
    INSERT OR IGNORE INTO sync_cursor (id, last_seq) VALUES (1, 0);
    """)
    conn.commit()

    # Wave 1B (Part K) / multi-device sync foundation (2026-08-06): first
    # real Retail schema changes -- see _migrate_retail_schema above, which
    # chains _migrate_categories_to_uuid (v2), _migrate_products_category_fk_
    # on_delete_set_null (v3), and _apply_retail_alters/einvoicing (v4).
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

    cur.executemany("INSERT INTO suppliers (company_id,name,phone,email) VALUES (?,?,?,?)", [
        (cid, 'TechDistrib Ltd', '+1-555-9001', 'orders@techdistrib.com'),
        (cid, 'FashionWholesale Co', '+1-555-9002', 'supply@fashionwholesale.com'),
        (cid, 'GroceryDirect', '+1-555-9003', 'bulk@grocerydirect.com'),
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
    for p in products:
        cat_id = category_ids[p[3] - 1]
        row = (p[0], p[1], p[2], cat_id) + p[4:]
        cur.execute("INSERT INTO products (company_id,sku,barcode,name,category_id,cost_price,sell_price,tax_rate,unit,reorder_level) VALUES (?,?,?,?,?,?,?,?,?,?)", (cid,) + row)

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
    for c in customers:
        cur.execute("INSERT INTO customers (company_id,name,phone,email,loyalty_points,total_spent) VALUES (?,?,?,?,?,?)", (cid,) + c)

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
        cust_id = random.choice([1, 2, 3, 4, 5, None])
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
