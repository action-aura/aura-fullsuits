# Retail Catalog & Party Sync Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend multi-device sync (currently Category-only) to Products, Customers, Suppliers, across desktop, Aura POS (shared Python), and the KMP mobile app — closed-LAN demo readiness, no cloud dependency.

**Architecture:** Same proven shape as Category's sync (UUID-migrated id, `sync_outbox`/`sync_cursor` outbox, Owner relay unchanged). Delete is **always** a soft-delete (`status='inactive'`) for all three entities — a deliberate simplification versus Category's hard-delete-plus-`ON DELETE SET NULL` precedent, chosen specifically to avoid touching 5+ FK relationships' delete semantics.

**Tech Stack:** Python/Flask/SQLite (desktop + Aura POS via Chaquopy), Kotlin/SQLDelight/Ktor (KMP mobile).

**Spec:** `docs/superpowers/specs/2026-08-07-retail-catalog-party-sync-expansion-design.md` — read it once for full rationale; this plan does not repeat the "why," only the "what."

## Global Constraints

- A synced delete is **always** `UPDATE <table> SET status='inactive' WHERE id=?`, never `DELETE FROM` — for products, customers, suppliers, on every client. No `ON DELETE` clause changes anywhere in this plan.
- Every migration function must be idempotent (check live schema state, return immediately if already migrated) and fully transactional (`PRAGMA foreign_keys=OFF` around it, `BEGIN`/`commit()`/`rollback()` on exception), mirroring `_migrate_categories_to_uuid` exactly (`products/retail/backend/database/schema.py:87-208`).
- A table with a declared FK pointing at it must never be renamed away directly — always rebuild under a `_new` name and swap via `DROP <original>` + `ALTER TABLE <new> RENAME TO <original>`. This applies to `products` (5 referencing tables) and `suppliers` (`purchase_orders.supplier_id`).
- Every `INSERT` that creates a product/customer/supplier row anywhere in the codebase (routes, import handlers, seed data, tests) must supply an explicit client-generated `id` (`str(uuid.uuid4())`, Python; `Uuid.random().toString()` KMP) — there is no AUTOINCREMENT default to fall back on once the column is `TEXT PRIMARY KEY`.
- `company_id` in a sync payload is never trusted on apply — every pulled row is stamped with the RECEIVING device's own `local_company_id_from_registry()` value (already-proven pattern, `commercial_runtime/sync/sync_service.py:44-71`).
- Owner (`owner/`) needs **zero** code changes — `entity_type` is already unvalidated free text there.
- No `run_in_background` for verification steps — run tests in the foreground and read the real output before reporting DONE (this was an explicit, repeated instruction in the prior plan's session after an implementer stalled 3 times by backgrounding test runs).

---

### Task 1: Desktop schema — Products UUID migration

**Files:**
- Modify: `products/retail/backend/database/schema.py`
- Test: `products/retail/tests/retail_products_uuid_migration_test.py` (new)

**Interfaces:**
- Produces: `_migrate_products_to_uuid(conn)` — callable from `_migrate_retail_schema`. `RETAIL_SCHEMA_VERSION` becomes `4`.
- Consumes: `_migrate_categories_to_uuid` and `_migrate_products_category_fk_on_delete_set_null` (schema.py:87-310) as the exact structural template — same idempotency check, same `PRAGMA foreign_keys=OFF`/`BEGIN`/`commit`/`rollback`/`finally` shape, same "build `_new`, copy, drop original, rename" swap.

This is the highest-risk task in the plan: `products` has 5 tables with a
declared FK pointing at it (`inventory_movements.product_id`,
`inventory_balances.product_id`, `sale_items.product_id`,
`purchase_order_items.product_id`, `return_items.product_id`), all rebuilt
in the SAME transaction as `products` itself, all remapped from old integer
ids to new UUIDs via an id map — mirroring exactly how
`_migrate_categories_to_uuid` remaps `products.category_id` (schema.py:188-199)
but repeated once per referencing table instead of once.

- [ ] **Step 1: Write the failing test**

```python
# products/retail/tests/retail_products_uuid_migration_test.py
import os
import sqlite3
import tempfile
import uuid

import pytest


def _fresh_conn(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _seed_v3_products_and_dependents(conn):
    """Builds a schema-v3-shaped database by hand (INTEGER products.id,
    every referencing table declared exactly as schema.py's base CREATE
    TABLE has them today) with real cross-referencing data, so the
    migration test never depends on init_retail()'s current seed data."""
    conn.executescript("""
        CREATE TABLE categories (id TEXT PRIMARY KEY, company_id INTEGER DEFAULT 1, name TEXT NOT NULL, description TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, sku TEXT NOT NULL, barcode TEXT,
            name TEXT NOT NULL, category_id TEXT, cost_price REAL DEFAULT 0, sell_price REAL DEFAULT 0,
            tax_rate REAL DEFAULT 0, unit TEXT DEFAULT 'pcs', reorder_level INTEGER DEFAULT 5,
            status TEXT DEFAULT 'active', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
        );
        CREATE TABLE inventory_movements (id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, product_id INTEGER NOT NULL, branch_id INTEGER, movement_type TEXT NOT NULL, quantity REAL NOT NULL, unit_cost REAL DEFAULT 0, reference TEXT, notes TEXT, created_by TEXT DEFAULT 'System', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (product_id) REFERENCES products(id));
        CREATE TABLE inventory_balances (id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, product_id INTEGER NOT NULL, branch_id INTEGER NOT NULL, quantity_on_hand REAL DEFAULT 0, quantity_reserved REAL DEFAULT 0, UNIQUE(company_id, product_id, branch_id), FOREIGN KEY (product_id) REFERENCES products(id));
        CREATE TABLE sales (id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, sale_number TEXT UNIQUE, branch_id INTEGER, customer_id INTEGER, cashier TEXT DEFAULT 'POS', subtotal REAL DEFAULT 0, discount_amount REAL DEFAULT 0, tax_amount REAL DEFAULT 0, total REAL DEFAULT 0, amount_paid REAL DEFAULT 0, change_amount REAL DEFAULT 0, payment_method TEXT DEFAULT 'cash', status TEXT DEFAULT 'completed', idempotency_key TEXT UNIQUE, notes TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE sale_items (id INTEGER PRIMARY KEY AUTOINCREMENT, sale_id INTEGER NOT NULL, product_id INTEGER NOT NULL, quantity REAL NOT NULL, unit_price REAL NOT NULL, discount_pct REAL DEFAULT 0, tax_rate REAL DEFAULT 0, line_total REAL NOT NULL, FOREIGN KEY (sale_id) REFERENCES sales(id), FOREIGN KEY (product_id) REFERENCES products(id));
        CREATE TABLE suppliers (id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, name TEXT NOT NULL, phone TEXT, email TEXT, address TEXT, status TEXT DEFAULT 'active', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE purchase_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, po_number TEXT UNIQUE, supplier_id INTEGER, branch_id INTEGER, status TEXT DEFAULT 'pending', subtotal REAL DEFAULT 0, tax_total REAL DEFAULT 0, total REAL DEFAULT 0, notes TEXT, ordered_at TEXT, received_at TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (supplier_id) REFERENCES suppliers(id));
        CREATE TABLE purchase_order_items (id INTEGER PRIMARY KEY AUTOINCREMENT, po_id INTEGER NOT NULL, product_id INTEGER NOT NULL, quantity REAL NOT NULL, unit_cost REAL NOT NULL, total REAL NOT NULL, received_qty REAL DEFAULT 0, FOREIGN KEY (po_id) REFERENCES purchase_orders(id), FOREIGN KEY (product_id) REFERENCES products(id));
        CREATE TABLE returns (id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, return_number TEXT UNIQUE, sale_id INTEGER, branch_id INTEGER, cashier TEXT DEFAULT 'POS', reason TEXT, refund_method TEXT DEFAULT 'cash', refund_amount REAL DEFAULT 0, status TEXT DEFAULT 'completed', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (sale_id) REFERENCES sales(id));
        CREATE TABLE return_items (id INTEGER PRIMARY KEY AUTOINCREMENT, return_id INTEGER NOT NULL, product_id INTEGER NOT NULL, quantity REAL NOT NULL, unit_price REAL NOT NULL, line_total REAL NOT NULL, FOREIGN KEY (return_id) REFERENCES returns(id), FOREIGN KEY (product_id) REFERENCES products(id));
    """)
    conn.execute("INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,status) VALUES (1,7,'SKU-A','Widget A',5,10,'active')")
    conn.execute("INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,status) VALUES (2,7,'SKU-B','Widget B',3,8,'inactive')")
    conn.execute("INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity) VALUES (7,1,1,'opening_stock',10)")
    conn.execute("INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (7,1,1,10)")
    conn.execute("INSERT INTO sales (id,company_id,sale_number) VALUES (900,7,'S-900')")
    conn.execute("INSERT INTO sale_items (sale_id,product_id,quantity,unit_price,line_total) VALUES (900,1,2,10,20)")
    conn.execute("INSERT INTO suppliers (id,company_id,name) VALUES (50,7,'Acme Supply')")
    conn.execute("INSERT INTO purchase_orders (id,company_id,po_number,supplier_id) VALUES (700,7,'PO-700',50)")
    conn.execute("INSERT INTO purchase_order_items (po_id,product_id,quantity,unit_cost,total) VALUES (700,1,5,4,20)")
    conn.execute("INSERT INTO returns (id,company_id,return_number,sale_id) VALUES (600,7,'R-600',900)")
    conn.execute("INSERT INTO return_items (return_id,product_id,quantity,unit_price,line_total) VALUES (600,1,1,10,10)")
    conn.commit()


def test_products_uuid_migration_preserves_every_row_and_remaps_every_referencing_table():
    from database.schema import _migrate_products_to_uuid

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "retail.db")
        conn = _fresh_conn(path)
        _seed_v3_products_and_dependents(conn)

        _migrate_products_to_uuid(conn)

        id_col = next(c for c in conn.execute("PRAGMA table_info(products)").fetchall() if c["name"] == "id")
        assert id_col["type"].upper() == "TEXT"

        rows = {r["sku"]: r for r in conn.execute("SELECT * FROM products").fetchall()}
        assert len(rows) == 2
        new_id_a = rows["SKU-A"]["id"]
        new_id_b = rows["SKU-B"]["id"]
        uuid.UUID(new_id_a)  # raises ValueError if not a real UUID string
        assert rows["SKU-A"]["status"] == "active"
        assert rows["SKU-B"]["status"] == "inactive"  # non-id columns untouched

        assert conn.execute("SELECT product_id FROM inventory_movements").fetchone()["product_id"] == new_id_a
        assert conn.execute("SELECT product_id FROM inventory_balances").fetchone()["product_id"] == new_id_a
        assert conn.execute("SELECT product_id FROM sale_items").fetchone()["product_id"] == new_id_a
        assert conn.execute("SELECT product_id FROM purchase_order_items").fetchone()["product_id"] == new_id_a
        assert conn.execute("SELECT product_id FROM return_items").fetchone()["product_id"] == new_id_a
        assert new_id_b not in [
            r["product_id"] for r in conn.execute(
                "SELECT product_id FROM inventory_movements UNION SELECT product_id FROM sale_items"
            ).fetchall()
        ]

        # FK enforcement still real after the rebuild
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO sale_items (sale_id,product_id,quantity,unit_price,line_total) VALUES (900,'not-a-real-id',1,1,1)")

        conn.close()


def test_products_uuid_migration_is_idempotent():
    from database.schema import _migrate_products_to_uuid

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "retail.db")
        conn = _fresh_conn(path)
        _seed_v3_products_and_dependents(conn)
        _migrate_products_to_uuid(conn)
        before = {r["sku"]: r["id"] for r in conn.execute("SELECT * FROM products").fetchall()}

        _migrate_products_to_uuid(conn)  # second call must be a no-op
        after = {r["sku"]: r["id"] for r in conn.execute("SELECT * FROM products").fetchall()}

        assert before == after
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest products/retail/tests/retail_products_uuid_migration_test.py -v` (from `products/retail/backend`'s parent so `database.schema` resolves — mirror whichever working-directory convention the existing `retail_category_delete_fk_sync_test.py` uses, since it already imports from `database.schema` successfully today).
Expected: FAIL with `ImportError: cannot import name '_migrate_products_to_uuid'`.

- [ ] **Step 3: Write the migration function**

Add to `products/retail/backend/database/schema.py`, immediately after `_migrate_products_category_fk_on_delete_set_null` (after line 310):

```python
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
```

Note on the `referencing` loop: each table is rebuilt BEFORE its `product_id` values are remapped, and the remap reads `product_id` from the OLD (not-yet-dropped) table — this is safe only because `{table}_new` is populated by a straight `SELECT * FROM {table}` first (still holding OLD integer product_id values, copied verbatim), and only THEN remapped in `{table}_new` using a lookup against the OLD table's still-present rows. Do not drop `{table}` before the remap loop reads from it.

Wire it into the dispatcher and bump the version (schema.py:40, 313-324):

```python
RETAIL_SCHEMA_VERSION = 4
```

```python
def _migrate_retail_schema(conn):
    _migrate_categories_to_uuid(conn)
    _migrate_products_category_fk_on_delete_set_null(conn)
    _migrate_products_to_uuid(conn)
```

Update the version-history comment block above `RETAIL_SCHEMA_VERSION` (schema.py:29-39) with a v3->v4 entry, same style as the existing v1->v2/v2->v3 entries.

Fix the seed data (`_seed_retail`, schema.py:615-632) the same way categories' own seed already had to be fixed (schema.py:590-599) — generate ids client-side before insert:

```python
    product_ids = [str(_uuid.uuid4()) for _ in products]
    for i, p in enumerate(products):
        cat_id = category_ids[p[3] - 1]
        row = (p[0], p[1], p[2], cat_id) + p[4:]
        cur.execute(
            "INSERT INTO products (id,company_id,sku,barcode,name,category_id,cost_price,sell_price,tax_rate,unit,reorder_level) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (product_ids[i], cid) + row,
        )
```

(`_uuid` is already imported at the top of `_seed_retail`'s body from the categories fix, schema.py:594 — reuse it, do not re-import.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest products/retail/tests/retail_products_uuid_migration_test.py -v`
Expected: PASS, both tests.

- [ ] **Step 5: Run the full retail suite per-file (NOT one combined run — see Global Constraints/prior-session note on cross-file DB contamination)**

```bash
for f in products/retail/tests/*.py; do python -m pytest "$f" -q; done
```
Expected: every file passes on its own. Any failure here is real regression from this task and must be fixed before moving on — do not defer to a later task. (Task 2 owns fixing test fixtures that raw-INSERT INTO products — if Step 5 surfaces those failures, note them in the report for Task 2, but Task 1 itself is scoped to the migration function and seed data, not every test file's fixtures.)

- [ ] **Step 6: Commit**

```bash
git add products/retail/backend/database/schema.py products/retail/tests/retail_products_uuid_migration_test.py
git commit -m "feat(retail-sync): migrate products.id to client-generated UUID (schema v4)"
```

---

### Task 2: Desktop backend — Products sync wiring, always-soft-delete, import/test fixes

**Files:**
- Modify: `products/retail/backend/api/retail_api.py`
- Modify: `products/retail/backend/api/import_api.py`
- Modify: `commercial_runtime/sync/sync_service.py`
- Modify: `products/retail/frontend/subsystem-retail.js`
- Modify: every test file under `products/retail/tests/` whose raw `INSERT INTO products` omits `id` (found via Step 1 below — do not guess the list in advance, the grep is the source of truth)
- Test: `products/retail/tests/retail_product_sync_test.py` (new)

**Interfaces:**
- Consumes: Task 1's migrated `products` schema (TEXT id). `_queue_sync_event(cur, entity_type, entity_id, event_type, payload)` (`retail_api.py:65-70`, unchanged signature). `nudge()` from `commercial_runtime.sync.sync_service` (already imported at `retail_api.py:19`).
- Produces: `_apply_event`'s new `product` branch in `sync_service.py`, consumed by Task 7's end-to-end verification.

- [ ] **Step 1: Find every raw INSERT INTO products test fixture**

```bash
grep -rn "INSERT INTO products" products/retail/tests/
```
For each match that does NOT already list `id` in the column list, the row must gain an explicit id. The simplest fix per site: generate one string literal id in the test (e.g. `'11111111-1111-1111-1111-111111111111'` or a real `str(uuid.uuid4())` at test setup) and add it to both the column list and the VALUES. Where a test currently relies on a specific small integer id for a later assertion (e.g. `retail_category_delete_fk_sync_test.py`'s literal `(6, 7, ...)`/`(3, ...)` inserts), replace the integer literal with an explicit UUID-shaped string literal in both the INSERT and every place that same test later references that id — do not leave a stray integer literal that used to rely on autoincrement matching it. Handle EVERY site the grep finds, not a sample.

- [ ] **Step 2: Write the failing sync test**

```python
# products/retail/tests/retail_product_sync_test.py
import json
import pytest


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
        CREATE TABLE products (id TEXT PRIMARY KEY, company_id INTEGER, sku TEXT, name TEXT, status TEXT DEFAULT 'active');
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
```

(Use whatever `client`/`db_conn` pytest fixtures the existing suite already provides — check `retail_category_delete_route_test.py` for the exact fixture names and setup this repo's tests already use; do not invent new fixture plumbing.)

- [ ] **Step 3: Run to verify it fails, then wire the backend**

`retail_api.py` changes (mirror `create_category`/`update_category`/`delete_category`, lines 221-309, exactly in shape):

`create_product` (currently :331-375) — after the existing `pid = cur.lastrowid` line, replace product-id generation with an explicit UUID generated BEFORE the insert (matching how `create_category` does it), and queue the sync event:

```python
def create_product():
    data = request.json or {}
    cid  = _cid()
    if not data.get('name') or not data.get('sku'):
        return jsonify({'status': 'error', 'message': 'Name and SKU are required'}), 400
    conn = get_retail_conn()
    try:
        existing = conn.execute("SELECT id FROM products WHERE company_id=? AND sku=?",
                                (cid, data['sku'])).fetchone()
        if existing:
            conn.close()
            return jsonify({'status': 'error', 'message': 'SKU already exists'}), 409
        cur = conn.cursor()
        pid = str(_uuid.uuid4())
        cur.execute("""
            INSERT INTO products (id,company_id,sku,barcode,name,category_id,cost_price,
                                  sell_price,tax_rate,unit,reorder_level,status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,'active')
        """, (pid, cid, data['sku'], data.get('barcode',''), data['name'],
              data.get('category_id'), data.get('cost_price',0), data.get('sell_price',0),
              data.get('tax_rate',0), data.get('unit','pcs'), data.get('reorder_level',5)))
        bid = _default_branch(conn, cid)
        conn.execute("""
            INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand)
            VALUES (?,?,?,?)
        """, (cid, pid, bid, data.get('initial_stock', 0)))
        if data.get('initial_stock', 0) > 0:
            conn.execute("""
                INSERT INTO inventory_movements (company_id,product_id,branch_id,movement_type,quantity,reference,created_by)
                VALUES (?,?,?,'opening_stock',?,?,?)
            """, (cid, pid, bid, data.get('initial_stock', 0), 'OPENING', _uid()))
        _audit(conn, 'PRODUCT_CREATED', 'product', pid, data['name'])
        _queue_sync_event(cur, 'product', pid, 'create', {
            'id': pid, 'sku': data['sku'], 'barcode': data.get('barcode', ''), 'name': data['name'],
            'category_id': data.get('category_id'), 'cost_price': data.get('cost_price', 0),
            'sell_price': data.get('sell_price', 0), 'tax_rate': data.get('tax_rate', 0),
            'unit': data.get('unit', 'pcs'), 'reorder_level': data.get('reorder_level', 5),
        })
        conn.commit(); conn.close()
        _emit('ProductCreated', {'product_id': pid})
        _sync_nudge()
        return jsonify({'status': 'success', 'data': {'id': pid}})
    except Exception as e:
        conn.close()
        return jsonify({'status': 'error', 'message': str(e)}), 500
```

`update_product` (currently :377-394) — route decorator `<int:pid>` becomes `<string:pid>`; add outbox queueing:

```python
@retail_bp.route('/products/<string:pid>', methods=['PATCH'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.product.update", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def update_product(pid):
    data = request.json or {}
    cid  = _cid()
    allowed = ['name','barcode','category_id','cost_price','sell_price','tax_rate','unit','reorder_level','status']
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return jsonify({'status': 'error', 'message': 'No valid fields'}), 400
    conn = get_retail_conn()
    cur = conn.cursor()
    sets = ', '.join(f'{k}=?' for k in fields)
    cur.execute(f'UPDATE products SET {sets} WHERE id=? AND company_id=?',
                list(fields.values()) + [pid, cid])
    if cur.rowcount == 0:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Product not found'}), 404
    _audit(conn, 'PRODUCT_UPDATED', 'product', pid)
    row = conn.execute("SELECT sku,barcode,name,category_id,cost_price,sell_price,tax_rate,unit,reorder_level FROM products WHERE id=?", (pid,)).fetchone()
    _queue_sync_event(cur, 'product', pid, 'update', dict(row) | {'id': pid})
    conn.commit(); conn.close()
    _sync_nudge()
    return jsonify({'status': 'success'})
```

`delete_product` (currently :396-415) — becomes always-soft-delete (the design spec's decided simplification), route decorator `<int:pid>` -> `<string:pid>`, mirrors `delete_category`'s try/except containment:

```python
@retail_bp.route('/products/<string:pid>', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.product.update", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def delete_product(pid):
    cid = _cid()
    conn = get_retail_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE products SET status='inactive' WHERE id=? AND company_id=?", (pid, cid))
        if cur.rowcount == 0:
            return jsonify({'status': 'error', 'message': 'Product not found'}), 404
        _audit(conn, 'PRODUCT_DELETED', 'product', pid, 'Product deactivated')
        _queue_sync_event(cur, 'product', pid, 'delete', {'id': pid})
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        current_app.logger.warning("delete_product(%s) failed on a database constraint: %s", pid, exc)
        return jsonify({'status': 'error', 'message': 'This product could not be deleted.'}), 409
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        current_app.logger.exception("delete_product(%s) failed: %s", pid, exc)
        return jsonify({'status': 'error', 'message': 'Could not delete this product.'}), 400
    finally:
        conn.close()
    _sync_nudge()
    return jsonify({'status': 'success', 'message': 'Product deactivated'})
```

`adjust_stock` (:417+) takes `pid` from the URL too — its route decorator also needs `<int:pid>` -> `<string:pid>`; the function body itself needs no other change (it already treats `pid` as an opaque value passed through to parameterized queries).

`import_api.py`'s `_handle_retail_products` (:1030-1099) — mirror its own existing category-id-generation fix (line 1050-1051) for products, replacing `pid = cur.lastrowid` (:1083):

```python
        else:
            pid = str(_uuid.uuid4())
            cur.execute("""
                INSERT INTO products (id,company_id,sku,barcode,name,category_id,cost_price,
                                      sell_price,tax_rate,unit,reorder_level,status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,'active')
            """, (pid, cid, sku, rec.get('barcode',''), rec.get('name',''), cat_id,
                  rec.get('cost_price') or 0, rec.get('sell_price') or 0,
                  rec.get('tax_rate') or 0, rec.get('unit','pcs') or 'pcs',
                  rec.get('reorder_level') or 5))
            imported += 1
```
(`_uuid` is already imported at the top of `import_api.py` — confirm before adding a duplicate import; the categories branch a few lines above already uses `_uuid.uuid4()`.)

`sync_service.py`'s `_apply_event` (:237-253) gains a `product` branch alongside `category`, and `apply_pull_result`'s local-company-id gate (:207-209) extends to check for `product` too:

```python
        local_company_id = None
        if any(
            ev.get("entity_type") in ("category", "product") and ev.get("event_type") in ("create", "update")
            for ev in events
        ):
            local_company_id = self._get_local_company_id()
```

```python
    def _apply_event(self, conn, ev: dict, local_company_id: Optional[str] = None) -> None:
        entity_type = ev.get("entity_type")
        if entity_type not in ("category", "product"):
            return
        p = ev.get("payload") or {}
        event_type = ev.get("event_type")
        if entity_type == "category":
            if event_type in ("create", "update"):
                conn.execute(
                    "INSERT INTO categories (id, company_id, name, description) VALUES (?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description",
                    (p.get("id"), local_company_id, p.get("name"), p.get("description", "")),
                )
            elif event_type == "delete":
                conn.execute("DELETE FROM categories WHERE id=?", (p.get("id"),))
        elif entity_type == "product":
            if event_type in ("create", "update"):
                conn.execute(
                    "INSERT INTO products (id, company_id, sku, barcode, name, category_id, cost_price, "
                    "sell_price, tax_rate, unit, reorder_level, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,'active') "
                    "ON CONFLICT(id) DO UPDATE SET sku=excluded.sku, barcode=excluded.barcode, name=excluded.name, "
                    "category_id=excluded.category_id, cost_price=excluded.cost_price, sell_price=excluded.sell_price, "
                    "tax_rate=excluded.tax_rate, unit=excluded.unit, reorder_level=excluded.reorder_level",
                    (p.get("id"), local_company_id, p.get("sku"), p.get("barcode", ""), p.get("name"),
                     p.get("category_id"), p.get("cost_price", 0), p.get("sell_price", 0), p.get("tax_rate", 0),
                     p.get("unit", "pcs"), p.get("reorder_level", 5)),
                )
            elif event_type == "delete":
                conn.execute("UPDATE products SET status='inactive' WHERE id=?", (p.get("id"),))
```

Note the deliberate asymmetry preserved from Category: `category`'s delete is still a real `DELETE FROM` (unchanged, out of this task's scope — Category's own delete semantics were decided and fixed in the prior plan) — `product`'s delete is the new always-soft-delete branch. Do not "fix" Category's branch to match; that would be an unrequested, unrelated behavior change.

`subsystem-retail.js` — fix every bareword product-id interpolation broken by the id-type change (grep confirmed these exact sites):
- Line 464: `RetailSystem._addToCart(${p.id})` -> `RetailSystem._addToCart('${this._esc(p.id)}')`
- Line 831: `customer_id: customerId ? +customerId : null` -> `customer_id: customerId || null` (drop the `+` unary-numeric-coercion — this is customer_id, not product_id, but it lives in the same checkout-payload-building code path and is broken by Task 3's customer migration; fixing it here now, while this exact line is already being read for the product fix beside it, avoids a second pass over this file for one line. If Task 3 is implemented by a different agent before this line is touched, that agent should skip this specific line since it will already be fixed.)
- Lines 1023-1025: `_openEditProduct(${p.id})` -> `_openEditProduct('${this._esc(p.id)}')`; `_openStockAdjust(${p.id},'${p.name.replace(/'/g,"\\'")}',${p.total_stock})` -> `_openStockAdjust('${this._esc(p.id)}','${p.name.replace(/'/g,"\\'")}',${p.total_stock})`; `_deleteProduct(${p.id},'${p.name.replace(/'/g,"\\'")}')` -> `_deleteProduct('${this._esc(p.id)}','${p.name.replace(/'/g,"\\'")}')`
- Line 1873: `product_id:+cb.dataset.pid` -> `product_id:cb.dataset.pid` (drop the `+` coercion; `dataset.pid` is already a string, unary-plus on a UUID string yields `NaN`)
- `_openEditProduct(pid)` (currently :1036) does a lookup by id — confirm it uses `String(x.id) === String(pid)` comparison (matching `_openEditCategory`'s existing pattern) rather than `===` directly on a bareword-passed value; fix if it does a strict `===` today.
- Also add `this._esc()` around `p.name`/`p.description`-equivalent fields in the products list render if any are currently unescaped (check the render function above line 1023; products can now arrive from another device, same new trust boundary Category's Fix 7 addressed).

- [ ] **Step 4: Run the new test, then the full suite per-file**

```bash
python -m pytest products/retail/tests/retail_product_sync_test.py -v
for f in products/retail/tests/*.py; do python -m pytest "$f" -q; done
node --check products/retail/frontend/subsystem-retail.js
```
Expected: all pass. Fix any test fixture Step 1's grep found that isn't fixed yet — this task does not end until every file in the per-file loop passes.

- [ ] **Step 5: Commit**

```bash
git add products/retail/backend/api/retail_api.py products/retail/backend/api/import_api.py commercial_runtime/sync/sync_service.py products/retail/frontend/subsystem-retail.js products/retail/tests/
git commit -m "feat(retail-sync): wire Products into sync (always-soft-delete), fix id-type regressions"
```

---

### Task 3: Desktop — Customers (schema, routes, new delete, frontend)

**Files:**
- Modify: `products/retail/backend/database/schema.py`
- Modify: `products/retail/backend/api/retail_api.py`
- Modify: `products/retail/backend/api/import_api.py`
- Modify: `commercial_runtime/sync/sync_service.py`
- Modify: `products/retail/frontend/subsystem-retail.js`
- Test: `products/retail/tests/retail_customer_sync_test.py` (new)

**Interfaces:**
- Consumes: `_migrate_products_to_uuid` as the structural template (Task 1) — Customers is lower-risk (no declared FK anywhere points at `customers.id`: `sales.customer_id` and `payments.party_id` are both loose, undeclared columns, so `customers` can technically be renamed away safely, but this migration still uses the same `_new`-then-swap pattern for consistency, not because it's structurally required here).
- Produces: `_migrate_customers_to_uuid(conn)`, added to `_migrate_retail_schema`'s chain after `_migrate_products_to_uuid` (still schema v4 — no further version bump needed, v4 already covers every step in this plan's chain).

Migration function (add to `schema.py` after `_migrate_products_to_uuid`):

```python
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
```

`payments.party_id`/`sales.customer_id` remain declared `INTEGER` in their own `CREATE TABLE` (unchanged by this migration, since neither is rebuilt) — SQLite's dynamic typing stores the UUID string in them without error (confirmed: SQLite column affinity only attempts, never forces, numeric conversion). This is intentional, not a loose end — do not attempt to change `sales`/`payments`' declared column types, since neither table is otherwise in scope this round.

Note the `has_credit` guard: `_ensure_credit_schema` may or may not have run yet on a given database before this migration fires (it's called lazily, per-request, from several routes) — if it hasn't, `credit_mode`/`credit_limit`/`credit_balance` won't exist as columns on the OLD `customers` table yet, so this migration must not assume they're present. `has_credit` decides the source expression per-row instead of relying on the SELECT succeeding.

Wire into dispatcher:

```python
def _migrate_retail_schema(conn):
    _migrate_categories_to_uuid(conn)
    _migrate_products_category_fk_on_delete_set_null(conn)
    _migrate_products_to_uuid(conn)
    _migrate_customers_to_uuid(conn)
```

Fix `_seed_retail`'s customer seed (schema.py:647-655) the same way as products:

```python
    customer_ids = [str(_uuid.uuid4()) for _ in customers]
    for i, c in enumerate(customers):
        cur.execute(
            "INSERT INTO customers (id,company_id,name,phone,email,loyalty_points,total_spent) VALUES (?,?,?,?,?,?,?)",
            (customer_ids[i], cid) + c,
        )
```

`retail_api.py` routes:

`create_customer` (:479-495):

```python
@retail_bp.route('/customers', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.customer.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def create_customer():
    data = request.json or {}
    if not data.get('name'):
        return jsonify({'status': 'error', 'message': 'Customer name required'}), 400
    cid  = _cid()
    conn = get_retail_conn()
    cur  = conn.cursor()
    nid = str(_uuid.uuid4())
    cur.execute("INSERT INTO customers (id,company_id,name,phone,email,address) VALUES (?,?,?,?,?,?)",
                (nid, cid, data['name'], data.get('phone',''), data.get('email',''), data.get('address','')))
    _audit(conn, 'CUSTOMER_CREATED', 'customer', nid, data['name'])
    _queue_sync_event(cur, 'customer', nid, 'create', {
        'id': nid, 'name': data['name'], 'phone': data.get('phone', ''),
        'email': data.get('email', ''), 'address': data.get('address', ''),
    })
    conn.commit(); conn.close()
    _sync_nudge()
    return jsonify({'status': 'success', 'data': {'id': nid}})
```

`update_customer` (:497-513) — route param `<int:cust_id>` -> `<string:cust_id>`, add outbox queueing (only the fields in `allowed` that this plan syncs — `credit_mode`/`credit_limit` stay local-only, same as they are today, not part of the sync payload):

```python
@retail_bp.route('/customers/<string:cust_id>', methods=['PATCH'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.customer.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def update_customer(cust_id):
    data = request.json or {}
    cid  = _cid()
    fields = {k: v for k, v in data.items() if k in ['name','phone','email','address','credit_mode','credit_limit']}
    if not fields:
        return jsonify({'status': 'error', 'message': 'No valid fields'}), 400
    conn = get_retail_conn()
    cur = conn.cursor()
    sets = ', '.join(f'{k}=?' for k in fields)
    cur.execute(f'UPDATE customers SET {sets} WHERE id=? AND company_id=?',
                list(fields.values()) + [cust_id, cid])
    if cur.rowcount == 0:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Customer not found'}), 404
    _audit(conn, 'CUSTOMER_UPDATED', 'customer', cust_id)
    row = conn.execute("SELECT name,phone,email,address FROM customers WHERE id=?", (cust_id,)).fetchone()
    _queue_sync_event(cur, 'customer', cust_id, 'update', dict(row) | {'id': cust_id})
    conn.commit(); conn.close()
    _sync_nudge()
    return jsonify({'status': 'success'})
```

New `delete_customer` route, inserted right after `update_customer`, mirrors `delete_category`'s containment pattern:

```python
@retail_bp.route('/customers/<string:cust_id>', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.customer.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def delete_customer(cust_id):
    cid = _cid()
    conn = get_retail_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE customers SET status='inactive' WHERE id=? AND company_id=?", (cust_id, cid))
        if cur.rowcount == 0:
            return jsonify({'status': 'error', 'message': 'Customer not found'}), 404
        _audit(conn, 'CUSTOMER_DELETED', 'customer', cust_id, 'Customer deactivated')
        _queue_sync_event(cur, 'customer', cust_id, 'delete', {'id': cust_id})
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        current_app.logger.warning("delete_customer(%s) failed on a database constraint: %s", cust_id, exc)
        return jsonify({'status': 'error', 'message': 'This customer could not be deleted.'}), 409
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        current_app.logger.exception("delete_customer(%s) failed: %s", cust_id, exc)
        return jsonify({'status': 'error', 'message': 'Could not delete this customer.'}), 400
    finally:
        conn.close()
    _sync_nudge()
    return jsonify({'status': 'success', 'message': 'Customer deactivated'})
```

`list_customers` (:459-477, look just above `create_customer`) needs a `WHERE c.company_id=? AND c.status='active'` filter added (currently has no status filter at all, since the column is new) — match the exact `AND ... status='active'` clause shape `list_products` already uses (:325).

`import_api.py`'s `_handle_retail_customers` (:1102-1128) — both branches' `INSERT`/`UPDATE` need an explicit id on create:

```python
        else:
            nid = str(_uuid.uuid4())
            cur.execute("INSERT INTO customers (id,company_id,name,phone,email,address,loyalty_points,total_spent) VALUES (?,?,?,?,?,?,?,?)",
                        (nid, cid, name, rec.get('phone',''), email, rec.get('address',''), lp, ts))
            imported += 1
```

`sync_service.py`'s `_apply_event` gains a `customer` branch (same shape as `product`'s, delete is soft):

```python
        elif entity_type == "customer":
            if event_type in ("create", "update"):
                conn.execute(
                    "INSERT INTO customers (id, company_id, name, phone, email, address, status) "
                    "VALUES (?,?,?,?,?,?,'active') "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name, phone=excluded.phone, "
                    "email=excluded.email, address=excluded.address",
                    (p.get("id"), local_company_id, p.get("name"), p.get("phone", ""),
                     p.get("email", ""), p.get("address", "")),
                )
            elif event_type == "delete":
                conn.execute("UPDATE customers SET status='inactive' WHERE id=?", (p.get("id"),))
```
And extend the `apply_pull_result` local-company-id gate (already touched in Task 2) to include `"customer"` in its entity-type tuple.

`subsystem-retail.js` — fix every bareword customer-id site and add delete UI + escaping (mirrors `_renderCategories`/`_deleteCategory`, :1185-1300, in shape):
- Line 1336: `onclick="RetailSystem._viewCustomer(${cu.id})"` -> `onclick="RetailSystem._viewCustomer('${this._esc(cu.id)}')"`
- Line 1337-1341: wrap `cu.name`, and any other unescaped customer-authored field rendered here, in `this._esc()` — customer data can now arrive from another device (new trust boundary, mirrors Category's Fix 7).
- Line 1344: `_openEditCustomer(${cu.id})` -> `_openEditCustomer('${this._esc(cu.id)}')`; add a Delete button beside it: `<button class="ret-btn ret-btn-danger ret-btn-sm" style="margin-left:6px" onclick="RetailSystem._deleteCustomer('${this._esc(cu.id)}')">${t('Delete')}</button>`
- `_openEditCustomer(id)` (:1356-1359): change `x=>x.id===id` to `x=>String(x.id)===String(id)`.
- Line 1377: `_saveCustomer(${cu.id||'null'})` -> `_saveCustomer(${cu.id ? `'${this._esc(cu.id)}'` : 'null'})`
- New `_deleteCustomer(custId)` function, inserted after `_saveCustomer`, copying `_deleteCategory`'s exact shape (:1284-1300) with `this._customers`/`/customers/` substituted for `this._categories`/`/categories/`.
- `_viewCustomer(cid)` (:1405+): change `x=>x.id===cid` to `x=>String(x.id)===String(cid)`.

- [ ] **Step: Write the failing test, implement, verify, per-file suite run, commit** (same shape as Task 2's Steps 2/4/5 — test file mirrors `retail_product_sync_test.py`'s three-test structure: outbox-on-create, soft-delete-on-delete, pulled-delete-soft-deletes).

```bash
python -m pytest products/retail/tests/retail_customer_sync_test.py -v
for f in products/retail/tests/*.py; do python -m pytest "$f" -q; done
node --check products/retail/frontend/subsystem-retail.js
```

```bash
git add products/retail/backend/database/schema.py products/retail/backend/api/retail_api.py products/retail/backend/api/import_api.py commercial_runtime/sync/sync_service.py products/retail/frontend/subsystem-retail.js products/retail/tests/retail_customer_sync_test.py
git commit -m "feat(retail-sync): migrate + wire Customers into sync, add delete (soft-delete, new route+UI)"
```

---

### Task 4: Desktop — Suppliers (schema, routes, new delete, frontend)

**Files:** same shape as Task 3, `suppliers` in place of `customers`.

**Interfaces:**
- Consumes: Task 1's `_migrate_products_to_uuid` structural template AND its rename-hazard discipline specifically — unlike Customers, `suppliers` DOES have a declared FK pointing at it (`purchase_orders.supplier_id`, schema.py:428), so this migration must never rename `suppliers` away directly (same constraint as `products`, `categories`).

Migration function (add after `_migrate_customers_to_uuid`):

```python
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
```

Wire into dispatcher (final chain, all four v4 steps present):

```python
def _migrate_retail_schema(conn):
    _migrate_categories_to_uuid(conn)
    _migrate_products_category_fk_on_delete_set_null(conn)
    _migrate_products_to_uuid(conn)
    _migrate_customers_to_uuid(conn)
    _migrate_suppliers_to_uuid(conn)
```

Fix `_seed_retail`'s supplier seed (schema.py:601-605), same pattern:

```python
    supplier_ids = [str(_uuid.uuid4()) for _ in range(3)]
    cur.executemany("INSERT INTO suppliers (id,company_id,name,phone,email) VALUES (?,?,?,?,?)", [
        (supplier_ids[0], cid, 'TechDistrib Ltd', '+1-555-9001', 'orders@techdistrib.com'),
        (supplier_ids[1], cid, 'FashionWholesale Co', '+1-555-9002', 'supply@fashionwholesale.com'),
        (supplier_ids[2], cid, 'GroceryDirect', '+1-555-9003', 'bulk@grocerydirect.com'),
    ])
```

`retail_api.py` routes — mirror Task 3's `create_customer`/`update_customer`/new-`delete_customer` shape exactly, substituting `suppliers`/`supplier`/`sid` for `customers`/`customer`/`cust_id`. `create_supplier` (:549-565), `update_supplier` (:567-583, route param `<int:sid>` -> `<string:sid>`, allowed fields stay `['name','phone','email','address','status','payment_terms']` exactly as today), new `delete_supplier` route.

`list_suppliers` (:533-547) already filters `AND s.status='active'` — no change needed there (unlike customers, which needed that filter added).

`import_api.py`'s `_handle_retail_suppliers` (:1131-1146) — explicit id on insert:

```python
        nid = str(_uuid.uuid4())
        cur.execute("INSERT INTO suppliers (id,company_id,name,phone,email,address) VALUES (?,?,?,?,?,?)",
                    (nid, cid, name, rec.get('phone',''), rec.get('email',''), rec.get('address','')))
        imported += 1
```

`sync_service.py`'s `_apply_event` gains a `supplier` branch (same shape as `customer`'s):

```python
        elif entity_type == "supplier":
            if event_type in ("create", "update"):
                conn.execute(
                    "INSERT INTO suppliers (id, company_id, name, phone, email, address, status) "
                    "VALUES (?,?,?,?,?,?,'active') "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name, phone=excluded.phone, "
                    "email=excluded.email, address=excluded.address",
                    (p.get("id"), local_company_id, p.get("name"), p.get("phone", ""),
                     p.get("email", ""), p.get("address", "")),
                )
            elif event_type == "delete":
                conn.execute("UPDATE suppliers SET status='inactive' WHERE id=?", (p.get("id"),))
```
Extend `apply_pull_result`'s local-company-id gate to include `"supplier"`.

`subsystem-retail.js` — fix every bareword supplier-id site, add delete UI + escaping:
- Line 1489-1492: wrap `s.name`, `s.phone`, `s.email`, `s.address` in `this._esc()`.
- Line 1495: `_openEditSupplier(${s.id},'${s.name.replace(/'/g,"\\'")}','${s.phone||''}','${s.email||''}','${s.address||''}')` -> `_openEditSupplier('${this._esc(s.id)}','${this._esc(s.name).replace(/'/g,"\\'")}','${this._esc(s.phone||'')}','${this._esc(s.email||'')}','${this._esc(s.address||'')}')`; add Delete button: `<button class="ret-btn ret-btn-danger ret-btn-sm" style="margin-left:6px" onclick="RetailSystem._deleteSupplier('${this._esc(s.id)}','${this._esc(s.name).replace(/'/g,"\\'")}')">${t('Delete')}</button>`
- Line 1496: `_openCreatePO(${s.id},'${s.name.replace(/'/g,"\\'")}')` -> `_openCreatePO('${this._esc(s.id)}','${this._esc(s.name).replace(/'/g,"\\'")}')` — this site is inside Purchase Orders, not Suppliers CRUD, but it breaks the instant `suppliers.id` becomes a UUID string (currently a bareword-interpolated arg) — this is a direct regression of THIS task's migration, must be fixed here even though Purchase Orders itself is out of scope.
- Line 1521: `_saveSupplier(${s.id||'null'})` -> `_saveSupplier(${s.id ? `'${this._esc(s.id)}'` : 'null'})`
- New `_deleteSupplier(supId, name)` function mirroring `_deleteCategory`'s shape, substituting `this._suppliers`/`/suppliers/` (note: unlike categories/customers, the current suppliers render doesn't cache a `this._suppliers` array — add one, populated in `_loadSuppliers`, so `_deleteSupplier`'s confirm dialog can show the real name without re-fetching).
- `_openCreatePO(supplierId=null, supplierName='')` (:1593+) and its internal `s.id===supplierId` comparison (:1603, inside the `<option>` map) — confirm this still works with string ids (`===` on two strings is fine; only the CALLING site's bareword-vs-quoted issue needed fixing, not this comparison itself).

- [ ] **Step: Write the failing test, implement, verify, per-file suite run, commit**

```bash
python -m pytest products/retail/tests/retail_supplier_sync_test.py -v
for f in products/retail/tests/*.py; do python -m pytest "$f" -q; done
node --check products/retail/frontend/subsystem-retail.js
```

```bash
git add products/retail/backend/database/schema.py products/retail/backend/api/retail_api.py products/retail/backend/api/import_api.py commercial_runtime/sync/sync_service.py products/retail/frontend/subsystem-retail.js products/retail/tests/retail_supplier_sync_test.py
git commit -m "feat(retail-sync): migrate + wire Suppliers into sync, add delete (soft-delete, new route+UI)"
```

---

### Task 5: KMP mobile — Products (DEFERRED, not built this plan)

**Dropped during implementation (decided 2026-08-08).** The implementer
correctly stopped before writing code: `products.id` in KMP is a live FK
target of 5 MORE schema files this plan never read during
planning — `Inventory.sq`, `Sales.sq`, `Returns.sq`, `Purchasing.sq`,
`Reporting.sq` — plus their repositories/use-cases (including
`SqlDelightInventoryRepository.kt`, explicitly the file where M5.5.14's
real concurrency races were caught) and a second, separate importer
(`ImportCommitExecutor.kt`) with its own product-creation path. Doing this
correctly is a materially larger migration than "KMP mobile — Products" —
comparable in size to desktop's entire Products work (Tasks 1+2 combined),
on higher-risk, concurrency-hardened code. Full findings:
`.superpowers/sdd/2026-08-07-retail-catalog-party-sync-expansion/task-5-report.md`.

Given Aura POS (the actual investor-facing app, shares desktop's Python)
already has full Products/Customers/Suppliers sync from Tasks 1-4
regardless, and KMP already had its Customers/Suppliers sync dropped for a
similar reason (secondary app, no proportionate payoff this round), this
was deferred rather than widened. KMP keeps Category-only sync. Revisit as
its own future plan if KMP Products sync becomes a real priority — that
plan should start by reading the full FK graph above (not just `Catalog.sq`),
which this plan did not do.

**Files:**
- Modify: `mobile/aura-retail-unified/shared/src/commonMain/sqldelight/com/actionaura/retail/db/Catalog.sq`
- Modify: `SqlDelightProductRepository.kt` (locate exact path via `grep -rl "class.*ProductRepository" mobile/aura-retail-unified/shared/src/commonMain/kotlin/`)
- Modify: `CatalogImporter.kt` (locate via `grep -rl "class CatalogImporter"`) — `import_conflicts.canonical_product_id`'s type change ripples here.
- Test: mirror existing product repository/importer test files, plus new UUID-migration and outbox-wiring coverage.

**Interfaces:**
- Consumes: Task 5's Customers/Suppliers KMP work as the proven outbox-wiring pattern for this module. `Catalog.sq`'s own existing `insertCategory`/`updateCategoryFields` (already TEXT id, already sync-wired from the prior plan) as the closest in-file precedent — closer than Parties.sq's, since Products shares this same file.

This is the highest-complexity KMP task: unlike Customers/Suppliers, `products` here has real divergence from the desktop schema (`normalized_name`, `updated_at` with optimistic concurrency, case-insensitive unique indexes, `import_conflicts.canonical_product_id`) that the migration must account for without breaking.

`Catalog.sq` changes to the `products` table — id column only (`INTEGER PRIMARY KEY AUTOINCREMENT` -> `TEXT PRIMARY KEY`), every other column and every index unchanged:

```sql
CREATE TABLE products (
    id TEXT PRIMARY KEY,
    company_id INTEGER NOT NULL DEFAULT 1,
    sku TEXT NOT NULL COLLATE NOCASE,
    barcode TEXT COLLATE NOCASE,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    category_id TEXT REFERENCES categories(id),
    cost_price TEXT NOT NULL DEFAULT '0.00',
    sell_price TEXT NOT NULL DEFAULT '0.00',
    tax_rate TEXT NOT NULL DEFAULT '0',
    unit TEXT NOT NULL DEFAULT 'pcs',
    reorder_level INTEGER NOT NULL DEFAULT 5,
    status TEXT NOT NULL DEFAULT 'active',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
```

`insertProduct`/`importProduct` gain an explicit `id` column, matching `insertCategory`'s existing shape:

```sql
insertProduct:
INSERT INTO products(id, company_id, sku, barcode, name, normalized_name, category_id, cost_price, sell_price, tax_rate, unit, reorder_level, status, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?);

importProduct:
INSERT INTO products(id, company_id, sku, barcode, name, normalized_name, category_id, cost_price, sell_price, tax_rate, unit, reorder_level, status, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
```

`lastInsertRowId` (currently `SELECT last_insert_rowid();`, used by whichever repository code called `insertProduct` then read this back) is no longer meaningful for products — the repository must generate the id BEFORE calling `insertProduct` and use that value directly, never this query, for a product's own id. Check whether `lastInsertRowId` is used for anything else in this file (branches/categories/import_conflicts still use rowid-based ids) before removing or narrowing it — it likely still needs to exist for those.

`updateProduct`'s optimistic-concurrency WHERE clause needs no logic change — `WHERE id = ? AND company_id = ? AND updated_at = ?` compares a `String` instead of a `Long` for `id`, which SQLDelight/Kotlin handles identically (string equality, not numeric).

`import_conflicts.canonical_product_id` (currently `INTEGER`) becomes `TEXT`:

```sql
CREATE TABLE import_conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    legacy_product_id INTEGER NOT NULL,
    field TEXT NOT NULL,
    original_value TEXT NOT NULL,
    resolution TEXT NOT NULL,
    canonical_product_id TEXT,
    created_at INTEGER NOT NULL
);
```
`legacy_product_id` stays `INTEGER` deliberately — it records the ORIGINAL legacy monolith's integer id (a historical fact about where the row came from during data-preservation import), never this app's own current product id; only `canonical_product_id` (this app's own product id, post-import) changes type. Read `CatalogImporter.kt` in full before touching it — every place it currently treats a product id as `Long` (assignment, comparison, the value it writes into `canonical_product_id`) needs to become `String`. `selectAllProductsForImportIdempotency`'s `id` column read is likewise now a `String`.

`selectLowStockProducts`, `selectProductById`, `selectProductByBarcode(AnyStatus)`, `selectProductBySku`, `updateProductStatus`, `changes` — no SQL changes needed (none reference `id`'s type directly in a way affected by TEXT vs INTEGER), but confirm the generated Kotlin types for their `id`-typed return/parameter fields compile cleanly as `String` everywhere they're consumed.

- [ ] Read `SqlDelightProductRepository.kt` and `CatalogImporter.kt` in full first — every `Long`-typed product id parameter/property in both files needs to become `String`. Do not guess the list; grep `Long` usages near anything named `productId`/`product_id`/`canonicalProductId` in both files as the starting point.
- [ ] Write failing tests: UUID-shaped id on insert, optimistic-concurrency `updateProduct` still rejects a stale `updatedAt` with a string id, `CatalogImporter`'s conflict-tracking still records a real canonical (string) product id correctly, outbox wiring on create/update/delete (soft-delete via `updateProductStatus`, mirroring `Catalog.sq`'s existing `updateCategoryStatus`/Category's KMP delete pattern from the prior plan).
- [ ] Implement.
- [ ] Run the full shared-module test suite and confirm green — this file's test surface is the largest of any KMP task in this plan (M5.5's own product-domain-contract tests, the importer's tests, plus this task's new ones), budget accordingly.
- [ ] Commit: `git commit -m "feat(retail-sync): migrate Products to UUID + wire sync (KMP), fix CatalogImporter id type"`

---

### Task 5 (renumbered): End-to-end LAN verification

**Files:** none (verification only — fix anything found, in whichever file it's actually in, then re-verify).

**Interfaces:**
- Consumes: every prior task's shipped code, running for real on desktop + Aura POS.

- [ ] Start desktop Retail and Aura POS (built from this branch) on the same local network, both pointed at the same Owner instance and the same license — reuse whatever local-network setup the prior plan's Task 11 (Category's own end-to-end verification) established, adapted from USB-cable to LAN per this plan's actual target (closed-network in-person demo, not a cable).
- [ ] On device A: create a product, a customer, a supplier. Confirm each appears on device B within one sync cycle (or immediately after a manual nudge/app-foreground).
- [ ] On device B: edit the product's price, the customer's phone, the supplier's address. Confirm device A reflects each edit.
- [ ] On device A: delete the product, the customer, the supplier (soft-delete). Confirm device B's lists no longer show them (status='inactive' filtered out of the active list queries) — and confirm device B's own existing local data that still references them (e.g. a locally-recorded sale against that product, if any test data has one) is untouched, not corrupted or nulled.
- [ ] Confirm Category sync (already shipped) still works unaffected — a live regression check, not just "the code still compiles."
- [ ] If anything fails: fix it in the file it's actually broken in, re-verify the same scenario, do not move on with a known-broken flow.
- [ ] Write up the verification report (what was tested, what passed, what was fixed) to `.superpowers/sdd/2026-08-07-retail-catalog-party-sync-expansion/task-7-report.md`.
- [ ] No commit needed unless Step 6 required a code fix — if it did, commit that fix with a message describing what live testing found.

---

## Residual/deferred items (do not build, document only if the final review asks)

- Branches: no real feature exists, out of scope entirely.
- KMP Customers/Suppliers sync: dropped (decided during planning, 2026-08-07) — no Customer/Supplier feature/UI exists in KMP at all today, only import-time table population. Desktop and Aura POS still sync both fully.
- KMP Products sync: dropped (decided during Task 5 implementation, 2026-08-08) — `products.id` is a live FK target of 5 more KMP schema files this plan never scoped (Inventory/Sales/Returns/Purchasing/Reporting.sq) plus their repositories and a second importer; a correct migration is comparable in size to desktop's entire Products work. See Task 5's section above for full detail. Desktop and Aura POS still sync Products fully.
- Sales/Inventory/Returns/Payments sync: deferred to a future plan (needs its own conflict-resolution design pass per the wider roadmap).
- KMP's `company_id` sync-apply gap (hardcoded `companyId = 1L` at every KMP call site): unchanged by this plan, still inert since nothing varies it yet.
