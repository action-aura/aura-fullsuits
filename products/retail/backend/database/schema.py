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
        FOREIGN KEY (category_id) REFERENCES categories(id)
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
    """)
    conn.commit()

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

    cur.executemany("INSERT INTO categories (company_id,name) VALUES (?,?)", [
        (cid, 'Electronics'), (cid, 'Clothing'), (cid, 'Food & Beverages'),
        (cid, 'Home & Living'), (cid, 'Health & Beauty'),
    ])

    cur.executemany("INSERT INTO suppliers (company_id,name,phone,email) VALUES (?,?,?,?)", [
        (cid, 'TechDistrib Ltd', '+1-555-9001', 'orders@techdistrib.com'),
        (cid, 'FashionWholesale Co', '+1-555-9002', 'supply@fashionwholesale.com'),
        (cid, 'GroceryDirect', '+1-555-9003', 'bulk@grocerydirect.com'),
    ])

    cur.execute("INSERT INTO tax_rates (company_id,name,rate,is_default) VALUES (?,'Standard VAT',15,1)", (cid,))
    cur.execute("INSERT INTO tax_rates (company_id,name,rate,is_default) VALUES (?,'Zero Rated',0,0)", (cid,))
    cur.execute("INSERT INTO tax_rates (company_id,name,rate,is_default) VALUES (?,'Reduced Rate',5,0)", (cid,))

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
        cur.execute("INSERT INTO products (company_id,sku,barcode,name,category_id,cost_price,sell_price,tax_rate,unit,reorder_level) VALUES (?,?,?,?,?,?,?,?,?,?)", (cid,) + p)

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
