"""
Aura Retail -- schema v8 migration regression coverage (reorder automation
foundation, feat/reorder-automation-foundation -- see database/schema.py's
`_migrate_add_reorder_automation_foundation`).

Two test styles, matching retail_po_split_migration_test.py's own two-style
precedent exactly:

  1. Hand-builds a v7-shaped `products` table (no `reorder_method` column,
     no `reorder_requests` table -- exactly the state a real device is in
     the instant before this migration runs) and invokes the migration
     function directly.

  2. A real fresh install (`database.schema.init_retail()` against a brand
     new AURA_APP_DATA temp dir) must land on `PRAGMA user_version == 8`
     with both pieces present, proving the full v0 -> v8 chain (every prior
     migration plus this one) runs correctly in one pass, not just that the
     migration function is correct in isolation.

Run:
    pytest products/retail/tests/retail_reorder_migration_test.py -v
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


def _fresh_conn(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _seed_v7_products(conn):
    """Builds the schema-v7 shape of just the table this migration touches:
    products.id is already TEXT (post _migrate_products_to_uuid), no
    `reorder_method` column exists yet, and `reorder_requests` does not
    exist at all -- exactly the state a real device is in the instant
    before this migration runs."""
    conn.executescript("""
        CREATE TABLE products (
            id TEXT PRIMARY KEY, company_id INTEGER DEFAULT 1, sku TEXT NOT NULL,
            barcode TEXT, name TEXT NOT NULL, category_id TEXT, supplier_id TEXT,
            cost_price REAL DEFAULT 0, sell_price REAL DEFAULT 0, tax_rate REAL DEFAULT 0,
            unit TEXT DEFAULT 'pcs', reorder_level INTEGER DEFAULT 5, status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    pid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,reorder_level) "
        "VALUES (?,?,?,?,?,?,?)",
        (pid, 7, 'PRE-1', 'Pre-existing Product', 5.0, 12.0, 3),
    )
    conn.commit()
    return pid


def test_migration_adds_reorder_method_column_and_reorder_requests_table():
    from database.schema import _migrate_add_reorder_automation_foundation

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "retail.db")
        conn = _fresh_conn(path)
        pid = _seed_v7_products(conn)

        _migrate_add_reorder_automation_foundation(conn)
        conn.commit()

        cols = {r["name"] for r in conn.execute("PRAGMA table_info(products)").fetchall()}
        assert "reorder_method" in cols

        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "reorder_requests" in tables

        rr_cols = {r["name"] for r in conn.execute("PRAGMA table_info(reorder_requests)").fetchall()}
        assert rr_cols == {
            "id", "company_id", "branch_id", "product_id", "status",
            "draft_message", "created_at", "resolved_at",
        }

        indexes = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        assert "idx_reorder_requests_company" in indexes
        assert "idx_reorder_requests_open" in indexes

        # pre-existing product row survives untouched; new column defaults
        # to 'none' (opt-in, not opt-out -- an install that never sets this
        # gets zero behavior change from the post-sale hook).
        row = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        assert row is not None
        assert row["reorder_method"] == "none"
        assert row["reorder_level"] == 3

        conn.close()


def test_open_request_partial_unique_index_blocks_a_second_pending_request_but_not_a_declined_one():
    from database.schema import _migrate_add_reorder_automation_foundation

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "retail.db")
        conn = _fresh_conn(path)
        pid = _seed_v7_products(conn)
        _migrate_add_reorder_automation_foundation(conn)
        conn.commit()

        conn.execute(
            "INSERT INTO reorder_requests (id,company_id,branch_id,product_id,status) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), 7, 1, pid, 'pending'),
        )
        conn.commit()

        # A second OPEN (pending/accepted) request for the SAME product must
        # be rejected by the database itself -- this is the real backstop
        # behind core/retail/reorder_hook.py's own check-then-insert guard.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO reorder_requests (id,company_id,branch_id,product_id,status) VALUES (?,?,?,?,?)",
                (str(uuid.uuid4()), 7, 1, pid, 'accepted'),
            )

        # A DECLINED request does not hold the slot open -- inserting a new
        # pending request for a DIFFERENT (still-open) status must succeed
        # once the first is declined.
        conn.execute("UPDATE reorder_requests SET status='declined' WHERE product_id=?", (pid,))
        conn.commit()
        conn.execute(
            "INSERT INTO reorder_requests (id,company_id,branch_id,product_id,status) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), 7, 1, pid, 'pending'),
        )
        conn.commit()

        rows = conn.execute("SELECT status FROM reorder_requests WHERE product_id=?", (pid,)).fetchall()
        assert sorted(r["status"] for r in rows) == ['declined', 'pending']

        conn.close()


def test_migration_is_idempotent():
    from database.schema import _migrate_add_reorder_automation_foundation

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "retail.db")
        conn = _fresh_conn(path)
        pid = _seed_v7_products(conn)

        _migrate_add_reorder_automation_foundation(conn)
        conn.commit()
        before_tables = sorted(r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall())
        before_indexes = sorted(r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall())
        before_cols = sorted(r["name"] for r in conn.execute("PRAGMA table_info(products)").fetchall())
        before_row = dict(conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone())

        _migrate_add_reorder_automation_foundation(conn)  # second call must be a clean no-op
        conn.commit()
        after_tables = sorted(r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall())
        after_indexes = sorted(r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall())
        after_cols = sorted(r["name"] for r in conn.execute("PRAGMA table_info(products)").fetchall())
        after_row = dict(conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone())

        assert before_tables == after_tables
        assert before_indexes == after_indexes
        assert before_cols == after_cols
        assert before_row == after_row

        conn.close()


def test_ensure_schema_version_advances_user_version_to_8():
    """Exercises the real ensure_schema_version path (integrity check, live
    backup, migrate, integrity re-check, advance PRAGMA user_version) that
    init_retail() actually calls in production -- not just the bare
    migration function -- proving the v7->v8 bump is wired up correctly
    end to end. Mirrors retail_po_split_migration_test.py's identical v5->v6
    test."""
    from database.schema import _migrate_add_reorder_automation_foundation
    from commercial_runtime.security.migration_safety import ensure_schema_version

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "retail.db")
        backup_dir = os.path.join(tmp, "migration_backups")
        conn = _fresh_conn(path)
        _seed_v7_products(conn)
        conn.execute("PRAGMA user_version = 7")
        conn.commit()

        ensure_schema_version(conn, path, 8, _migrate_add_reorder_automation_foundation, backup_dir=backup_dir)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "reorder_requests" in tables

        # a later app launch against an already-migrated db: current(8) >=
        # target(8), so ensure_schema_version's fast path returns
        # immediately and migrate_fn never runs again -- version stays 8.
        ensure_schema_version(conn, path, 8, _migrate_add_reorder_automation_foundation, backup_dir=backup_dir)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8

        conn.close()


# ── Fresh install: the full v0 -> v8 chain in one pass ───────────────────────

def test_fresh_install_lands_on_v8_with_reorder_schema_present():
    """A brand-new install (no pre-existing database file at all) must boot
    straight to the CURRENT live schema version -- init_retail() runs the
    FULL migration chain in one pass from PRAGMA user_version=0 (see
    _migrate_retail_schema's own docstring: 'a v1 install upgrading
    straight to v7 must run ALL steps in one pass' -- each later bump
    extends that same guarantee by one more step). Uses a fresh
    AURA_APP_DATA temp dir and a fresh Python import of database.schema so
    this genuinely exercises init_retail(), not just the bare migration
    function already covered above.

    Asserts v11, not v8 (this test's own name still says v8, describing the
    reorder-automation schema it was written to cover -- the version
    NUMBER moved again under it, first when feat/email-outbox-foundation
    bumped RETAIL_SCHEMA_VERSION to 9, then when feat/shift-cash-drawer
    bumped it to 10, then when feat/pos-hold-resume-sale bumped it to 11
    (database/schema.py's _migrate_add_held_sales), then when the
    whatsapp-recipients feature bumped it to 12 (database/schema.py's
    _migrate_add_whatsapp_recipients), same as this file's sibling
    test_ensure_schema_version_advances_user_version_to_8 test intentionally
    keeps testing the v7->v8 step in isolation at a frozen target=8. This
    one, unlike that one, calls the REAL init_retail() and therefore always
    reflects whatever RETAIL_SCHEMA_VERSION currently is -- see
    retail_category_delete_fk_sync_test.py's identical
    RETAIL_SCHEMA_VERSION==12 update for the same reasoning)."""
    data_dir = Path(tempfile.mkdtemp(prefix="aura_retail_reorder_freshinstall_"))
    (data_dir / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
    old_app_data = os.environ.get("AURA_APP_DATA")
    old_standalone = os.environ.get("AURA_STANDALONE")
    os.environ["AURA_APP_DATA"] = str(data_dir)
    os.environ["AURA_STANDALONE"] = "1"  # skip demo-data seeding, irrelevant here
    try:
        import importlib
        import database.schema as retail_schema
        importlib.reload(retail_schema)  # fresh module state, in case another test file already imported it

        retail_schema.init_retail()

        conn = retail_schema.get_retail_conn()
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 12
            cols = {r[1] for r in conn.execute("PRAGMA table_info(products)").fetchall()}
            assert "reorder_method" in cols
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            assert "reorder_requests" in tables
            # feat/email-outbox-foundation rides along on this same fresh-
            # install chain now (v8 -> v9) -- assert its tables exist too,
            # same "prove the WHOLE chain ran, not just the step this file
            # was originally written for" spirit as the reorder assertions
            # just above.
            assert "email_outbox" in tables
            assert "email_settings" in tables
            # feat/shift-cash-drawer rides along too now (v9 -> v10).
            assert "cash_sessions" in tables
            assert "cash_movements" in tables
            sales_cols = {r[1] for r in conn.execute("PRAGMA table_info(sales)").fetchall()}
            assert "session_id" in sales_cols
        finally:
            conn.close()
    finally:
        if old_app_data is None:
            os.environ.pop("AURA_APP_DATA", None)
        else:
            os.environ["AURA_APP_DATA"] = old_app_data
        if old_standalone is None:
            os.environ.pop("AURA_STANDALONE", None)
        else:
            os.environ["AURA_STANDALONE"] = old_standalone
        shutil.rmtree(data_dir, ignore_errors=True)
