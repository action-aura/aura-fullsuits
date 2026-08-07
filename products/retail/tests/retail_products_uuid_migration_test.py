import os
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
