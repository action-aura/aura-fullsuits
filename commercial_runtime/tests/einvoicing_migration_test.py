"""JoFotara e-invoicing (docs/einvoicing/phase1/) -- migration safety tests
for commercial_runtime/einvoicing/schema.py.

Mirrors commercial_runtime/tests/migration_safety_test.py's approach:
standalone, product-independent synthetic fixture databases, not a real
Retail/Clinic boot -- the real per-product wiring (schema.py's
_apply_retail_alters / _apply_clinic_alters calling
apply_einvoicing_schema) is covered end-to-end by
products/retail/tests/retail_einvoicing_test.py and
products/clinic/tests/clinic_einvoicing_test.py (docs/einvoicing/phase1/
Step 13/14), which boot the real app and can't share a process with each
other (both define top-level `app` / `database` modules) -- see
products/run_all_tests.py's one-process-per-file design.

This file proves the schema module itself: additive only, safe under
ensure_schema_version, idempotent, and existing data survives untouched.

Run:
    pytest commercial_runtime/tests/einvoicing_migration_test.py -v
"""
import os
import sqlite3
import sys
from pathlib import Path

SUITE_ROOT = Path(__file__).resolve().parents[2]
if str(SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(SUITE_ROOT))

from commercial_runtime.einvoicing.schema import apply_einvoicing_schema  # noqa: E402
from commercial_runtime.security.migration_safety import ensure_schema_version  # noqa: E402

_EINVOICE_TABLES = (
    'einvoice_settings', 'einvoice_sequence', 'einvoice_outbox',
    'einvoice_party_ids', 'einvoice_audit',
)


def _make_v1_fixture(tmp_path):
    """A stand-in for an existing Retail/Clinic v1 database: one pre-existing
    table with a row, at user_version=1 (post-migration_safety_test.py's own
    v0->v1 pattern, i.e. representing an install that already ran a prior
    migration once, same as real pilot installs are at today)."""
    db_path = os.path.join(tmp_path, 'product.db')
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, sale_number TEXT UNIQUE, total REAL)")
    conn.execute("INSERT INTO sales (sale_number, total) VALUES ('SALE-000001', 42.50)")
    conn.execute('PRAGMA user_version = 1')
    conn.commit()
    return db_path, conn


def _apply_retail_like_alters(migrating_conn):
    apply_einvoicing_schema(migrating_conn)


def test_migration_adds_einvoice_tables_and_leaves_existing_data_untouched(tmp_path):
    db_path, conn = _make_v1_fixture(str(tmp_path))
    backup_dir = str(tmp_path / 'backups')

    ensure_schema_version(conn, db_path, target_version=2, migrate_fn=_apply_retail_like_alters, backup_dir=backup_dir)

    assert conn.execute('PRAGMA user_version').fetchone()[0] == 2

    row = conn.execute("SELECT sale_number, total FROM sales").fetchone()
    assert row == ('SALE-000001', 42.50), "pre-existing row must survive byte-for-byte"

    existing_cols = [r[1] for r in conn.execute("PRAGMA table_info(sales)").fetchall()]
    assert existing_cols == ['id', 'sale_number', 'total'], "no column was added to an existing table"

    for table in _EINVOICE_TABLES:
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        assert cols, f"{table} should exist after migration"
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count == 0, f"{table} must be empty immediately after migration -- schema-only, no data writes"

    conn.close()


def test_pre_migration_backup_exists_and_passes_integrity_check(tmp_path):
    db_path, conn = _make_v1_fixture(str(tmp_path))
    backup_dir = str(tmp_path / 'backups')

    ensure_schema_version(conn, db_path, target_version=2, migrate_fn=_apply_retail_like_alters, backup_dir=backup_dir)
    conn.close()

    backups = os.listdir(backup_dir)
    assert len(backups) == 1
    assert 'pre-migration-v1-to-v2' in backups[0]

    restore_conn = sqlite3.connect(os.path.join(backup_dir, backups[0]))
    assert restore_conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    row = restore_conn.execute("SELECT sale_number FROM sales").fetchone()
    assert row[0] == 'SALE-000001'
    for table in _EINVOICE_TABLES:
        found = restore_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        assert found is None, f"pre-migration backup must NOT contain {table} -- it's a snapshot from before the migration ran"
    restore_conn.close()


def test_running_migration_twice_is_a_noop(tmp_path):
    db_path, conn = _make_v1_fixture(str(tmp_path))
    backup_dir = str(tmp_path / 'backups')

    ensure_schema_version(conn, db_path, target_version=2, migrate_fn=_apply_retail_like_alters, backup_dir=backup_dir)
    first_backup_count = len(os.listdir(backup_dir))

    calls = {'n': 0}

    def _counting_migrate(c):
        calls['n'] += 1
        apply_einvoicing_schema(c)

    ensure_schema_version(conn, db_path, target_version=2, migrate_fn=_counting_migrate, backup_dir=backup_dir)

    assert calls['n'] == 0, "already at target_version -- migrate_fn must not run again"
    assert len(os.listdir(backup_dir)) == first_backup_count, "no second backup on the no-op fast path"
    conn.close()


def test_v1_shaped_read_still_works_against_v2_file(tmp_path):
    """A v1-era code path (one that has never heard of einvoice_* tables)
    reading a v2 database must work exactly as before -- proves forward
    compatibility, part of the Part 10 rollback story (a v1 build can read a
    v2 file untouched, so a code-only rollback needs no data migration)."""
    db_path, conn = _make_v1_fixture(str(tmp_path))
    backup_dir = str(tmp_path / 'backups')
    ensure_schema_version(conn, db_path, target_version=2, migrate_fn=_apply_retail_like_alters, backup_dir=backup_dir)
    conn.close()

    v1_shaped_conn = sqlite3.connect(db_path)
    row = v1_shaped_conn.execute("SELECT sale_number, total FROM sales WHERE sale_number = ?", ('SALE-000001',)).fetchone()
    assert row == ('SALE-000001', 42.50)
    v1_shaped_conn.close()


def test_apply_einvoicing_schema_alone_is_idempotent():
    """Calling the schema function directly, twice, on the same connection
    (independent of ensure_schema_version's version gate) must not raise --
    every statement is IF NOT EXISTS."""
    conn = sqlite3.connect(':memory:')
    apply_einvoicing_schema(conn)
    apply_einvoicing_schema(conn)
    for table in _EINVOICE_TABLES:
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        assert cols
    conn.close()
