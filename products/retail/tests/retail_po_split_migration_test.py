"""
Aura Retail -- schema v6 migration regression coverage (PO-preview-by-
supplier foundation, Thursday demo Stream B -- see database/schema.py's
`_migrate_add_supplier_contacts_and_po_split`).

Hand-builds a v5-shaped database (suppliers/purchase_orders exactly as
init_retail() + the v1-v5 chain leave them -- suppliers.id already a
client-generated UUID, products.supplier_id already present, but none of
v6's supplier_contacts table or the new purchase_orders/suppliers columns
exist yet) using the same "build the old-version tables by hand, then invoke
the migration function directly" technique as
retail_products_uuid_migration_test.py. Only the two tables this migration
actually touches are built -- categories/products/customers are irrelevant
to this change and replaying the full v1->v5 chain here would add setup
cost without adding signal.

Run:
    pytest products/retail/tests/retail_po_split_migration_test.py -v
"""
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

SUPPLIER_CONTACTS_COLUMNS = {
    "id", "company_id", "supplier_id", "name", "role", "email", "phone",
    "whatsapp", "channel_preference", "is_primary", "status", "created_at",
}
NEW_PO_COLUMNS = (
    "split_group_id", "split_index", "split_count", "routing_status",
    "routed_channel", "routed_at", "routed_to", "idempotency_key",
)


def _fresh_conn(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _seed_v5_suppliers_and_purchase_orders(conn):
    """Builds the schema-v5 shape of just the two tables this migration
    touches: suppliers.id is already TEXT (post _migrate_suppliers_to_uuid),
    purchase_orders already carries the payment/due_date columns Wave 0
    added -- but neither table has any v6 column, and supplier_contacts does
    not exist at all, exactly the state a real device is in the instant
    before this migration runs."""
    conn.executescript("""
        CREATE TABLE suppliers (
            id TEXT PRIMARY KEY, company_id INTEGER DEFAULT 1, name TEXT NOT NULL,
            phone TEXT, email TEXT, address TEXT, status TEXT DEFAULT 'active',
            payment_terms TEXT DEFAULT 'none', credit_balance REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE purchase_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1,
            po_number TEXT UNIQUE, supplier_id TEXT, branch_id INTEGER,
            status TEXT DEFAULT 'pending', subtotal REAL DEFAULT 0, tax_total REAL DEFAULT 0,
            total REAL DEFAULT 0, notes TEXT, ordered_at TEXT, received_at TEXT,
            amount_paid REAL DEFAULT 0, payment_status TEXT DEFAULT 'unpaid', due_date TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
        );
    """)
    sid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO suppliers (id,company_id,name,phone,email) VALUES (?,?,?,?,?)",
        (sid, 7, 'Acme Supply', '+1-555-1000', 'orders@acme.test'),
    )
    conn.execute(
        "INSERT INTO purchase_orders (company_id,po_number,supplier_id,status,total) VALUES (?,?,?,?,?)",
        (7, 'PO-9001', sid, 'pending', 250.0),
    )
    conn.commit()
    return sid


def test_migration_creates_supplier_contacts_table_and_po_supplier_columns():
    from database.schema import _migrate_add_supplier_contacts_and_po_split

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "retail.db")
        conn = _fresh_conn(path)
        sid = _seed_v5_suppliers_and_purchase_orders(conn)

        _migrate_add_supplier_contacts_and_po_split(conn)
        conn.commit()

        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "supplier_contacts" in tables

        sc_cols = {r["name"] for r in conn.execute("PRAGMA table_info(supplier_contacts)").fetchall()}
        assert sc_cols == SUPPLIER_CONTACTS_COLUMNS

        po_cols = {r["name"] for r in conn.execute("PRAGMA table_info(purchase_orders)").fetchall()}
        for col in NEW_PO_COLUMNS:
            assert col in po_cols, f"missing purchase_orders.{col}"

        sup_cols = {r["name"] for r in conn.execute("PRAGMA table_info(suppliers)").fetchall()}
        assert "min_order_value" in sup_cols

        indexes = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        assert "idx_supplier_contacts_supplier" in indexes
        assert "idx_po_split_group" in indexes
        assert "idx_po_idempotency" in indexes

        # pre-existing purchase_orders row survives untouched; new column defaults to NULL
        row = conn.execute("SELECT * FROM purchase_orders WHERE po_number='PO-9001'").fetchone()
        assert row is not None
        assert row["supplier_id"] == sid
        assert row["total"] == 250.0
        assert row["status"] == "pending"
        assert row["split_group_id"] is None

        conn.close()


def test_idempotency_key_partial_unique_index_allows_nulls_but_rejects_duplicate_values():
    from database.schema import _migrate_add_supplier_contacts_and_po_split

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "retail.db")
        conn = _fresh_conn(path)
        _seed_v5_suppliers_and_purchase_orders(conn)
        _migrate_add_supplier_contacts_and_po_split(conn)
        conn.commit()

        # many NULL idempotency_key rows must coexist (every pre-v6 PO, plus
        # any future PO that has not gone through split/routing yet)
        conn.execute("INSERT INTO purchase_orders (company_id,po_number) VALUES (7,'PO-9002')")
        conn.execute("INSERT INTO purchase_orders (company_id,po_number) VALUES (7,'PO-9003')")
        conn.commit()

        conn.execute("UPDATE purchase_orders SET idempotency_key='dup-key' WHERE po_number='PO-9002'")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE purchase_orders SET idempotency_key='dup-key' WHERE po_number='PO-9003'")

        conn.close()


def test_migration_is_idempotent():
    from database.schema import _migrate_add_supplier_contacts_and_po_split

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "retail.db")
        conn = _fresh_conn(path)
        _seed_v5_suppliers_and_purchase_orders(conn)

        _migrate_add_supplier_contacts_and_po_split(conn)
        conn.commit()
        before_tables = sorted(r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall())
        before_indexes = sorted(r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall())
        before_po_cols = sorted(r["name"] for r in conn.execute("PRAGMA table_info(purchase_orders)").fetchall())
        before_row = dict(conn.execute("SELECT * FROM purchase_orders WHERE po_number='PO-9001'").fetchone())

        _migrate_add_supplier_contacts_and_po_split(conn)  # second call must be a clean no-op
        conn.commit()
        after_tables = sorted(r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall())
        after_indexes = sorted(r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall())
        after_po_cols = sorted(r["name"] for r in conn.execute("PRAGMA table_info(purchase_orders)").fetchall())
        after_row = dict(conn.execute("SELECT * FROM purchase_orders WHERE po_number='PO-9001'").fetchone())

        assert before_tables == after_tables
        assert before_indexes == after_indexes
        assert before_po_cols == after_po_cols
        assert before_row == after_row

        conn.close()


def test_ensure_schema_version_advances_user_version_to_6():
    """Exercises the real ensure_schema_version path (integrity check,
    live backup, migrate, integrity re-check, advance PRAGMA user_version)
    that init_retail() actually calls in production -- not just the bare
    migration function -- so this also proves the v5->v6 bump is wired up
    correctly end to end, not merely that the migration body is correct in
    isolation."""
    from database.schema import _migrate_add_supplier_contacts_and_po_split
    from commercial_runtime.security.migration_safety import ensure_schema_version

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "retail.db")
        backup_dir = os.path.join(tmp, "migration_backups")
        conn = _fresh_conn(path)
        _seed_v5_suppliers_and_purchase_orders(conn)
        conn.execute("PRAGMA user_version = 5")
        conn.commit()

        ensure_schema_version(conn, path, 6, _migrate_add_supplier_contacts_and_po_split, backup_dir=backup_dir)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "supplier_contacts" in tables

        # a later app launch against an already-migrated db: current(6) >=
        # target(6), so ensure_schema_version's fast path returns immediately
        # and migrate_fn never runs again -- version simply stays 6.
        ensure_schema_version(conn, path, 6, _migrate_add_supplier_contacts_and_po_split, backup_dir=backup_dir)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6

        conn.close()
