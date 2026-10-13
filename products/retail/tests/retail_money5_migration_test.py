"""MONEY-5 (schema v55): the migration.

  * v54 -> v55 adds sales.rounding_amount and payment_methods.gl_account_code. Replaying the step
    (or the whole chain) is a no-op and a database that already has a column keeps its data.
  * `payment_methods` is created lazily by the route layer, not by schema.py, so a database that
    never had the table is NOT given one by the migration (shape-gated) -- the runtime ensure adds
    the column when it creates the table.
  * The step never rewrites a journal: the cash already booked to 1000 stays in 1000.

Run: pytest products/retail/tests/retail_money5_migration_test.py -q
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

for _k in ("AURA_SYNC_RELAY_URL", "AURA_OWNER_LICENSING_URL"):
    os.environ.pop(_k, None)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RETAIL_BACKEND_DIR = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_RETAIL_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import database.schema as schema  # noqa: E402
import api.retail_api as _retail_api_module  # noqa: E402
from api.retail_api import _ensure_credit_schema  # noqa: E402
from commercial_runtime.identity import registry_db  # noqa: E402

NEW_COLS = (("sales", "rounding_amount"), ("payment_methods", "gl_account_code"))


@pytest.fixture
def get_conn(tmp_path, monkeypatch):
    reg_dir = tmp_path / "registry_db"
    monkeypatch.setattr(registry_db, "_db_dir", str(reg_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(reg_dir / "registry.db"))
    registry_db.init_registry_db()
    root = tmp_path / "install"
    subsys = root / "subsystems"
    subsys.mkdir(parents=True, exist_ok=True)
    original = (schema.BASE_DIR, schema.SUBSYS_DIR)
    schema.BASE_DIR, schema.SUBSYS_DIR = str(root), str(subsys)
    try:
        schema.init_retail()
    finally:
        schema.BASE_DIR, schema.SUBSYS_DIR = original
    db_path = subsys / "retail.db"

    def _get():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=30000")
        return c

    previous = _retail_api_module._CREDIT_SCHEMA_READY
    _retail_api_module._CREDIT_SCHEMA_READY = False
    try:
        c = _get()
        try:
            _ensure_credit_schema(c)   # creates payment_methods, like the first route request
            c.commit()
        finally:
            c.close()
    finally:
        _retail_api_module._CREDIT_SCHEMA_READY = previous
    return _get


def _cols(conn, table):
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def test_a_fresh_install_has_both_columns_and_is_stamped_55(get_conn):
    conn = get_conn()
    try:
        assert schema.RETAIL_SCHEMA_VERSION == 56  # v56 UPDATE: head moved 55 -> 56 (SYNC-BOOT Phase 2); v56's own shape is pinned by retail_site_stream_cursors_migration_test.
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 56  # v56 UPDATE: same exact pin.
        for table, col in NEW_COLS:
            assert col in _cols(conn, table), (table, col)
    finally:
        conn.close()


def test_the_step_adds_each_missing_column_to_a_v54_shaped_database(get_conn):
    conn = get_conn()
    try:
        for table, col in NEW_COLS:                       # make it v54-shaped
            conn.execute(f'ALTER TABLE "{table}" DROP COLUMN {col}')
        conn.commit()
        for table, col in NEW_COLS:
            assert col not in _cols(conn, table)
        schema._migrate_add_money5_tender_columns(conn)
        conn.commit()
        for table, col in NEW_COLS:
            assert col in _cols(conn, table)
    finally:
        conn.close()


def test_the_step_is_idempotent_and_keeps_existing_data(get_conn):
    conn = get_conn()
    try:
        conn.execute("INSERT INTO sales (company_id, uid, sale_number, total, rounding_amount) "
                     "VALUES (?,?,?,?,?)", ("c", "u-1", "S-1", 3.457, -0.007))
        conn.execute("INSERT INTO payment_methods (company_id, name, type, gl_account_code) "
                     "VALUES (?,?,?,?)", ("c", "Cliq", "wallet", "1040"))
        conn.commit()
        schema._migrate_add_money5_tender_columns(conn)
        schema._migrate_add_money5_tender_columns(conn)
        assert conn.execute("SELECT rounding_amount FROM sales WHERE uid='u-1'").fetchone()[0] == -0.007
        assert conn.execute("SELECT gl_account_code FROM payment_methods WHERE name='Cliq'"
                            ).fetchone()[0] == "1040"
    finally:
        conn.close()


def test_a_database_without_payment_methods_is_not_given_one_by_the_migration(get_conn):
    """payment_methods is the route layer's table. Creating it here would hand the runtime
    ensure a table whose other columns it did not declare."""
    conn = get_conn()
    try:
        conn.execute("DROP TABLE payment_methods")
        conn.commit()
        schema._migrate_add_money5_tender_columns(conn)
        live = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "payment_methods" not in live
        assert "rounding_amount" in _cols(conn, "sales")
    finally:
        conn.close()


def test_the_runtime_ensure_adds_gl_account_code_when_it_creates_payment_methods(get_conn):
    """The other half of the two-path column: no migration ran for a table created later."""
    conn = get_conn()
    try:
        conn.execute("DROP TABLE payment_methods")
        conn.commit()
        previous = _retail_api_module._CREDIT_SCHEMA_READY
        _retail_api_module._CREDIT_SCHEMA_READY = False
        try:
            _ensure_credit_schema(conn)
        finally:
            _retail_api_module._CREDIT_SCHEMA_READY = previous
        assert "gl_account_code" in _cols(conn, "payment_methods")
    finally:
        conn.close()


def test_the_step_posts_and_rewrites_no_journal(get_conn):
    conn = get_conn()
    try:
        before = conn.execute("SELECT COUNT(*) FROM gl_journals").fetchone()[0]
        schema._migrate_add_money5_tender_columns(conn)
        assert conn.execute("SELECT COUNT(*) FROM gl_journals").fetchone()[0] == before
    finally:
        conn.close()
