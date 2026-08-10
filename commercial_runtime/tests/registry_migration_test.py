"""Per-device login (docs/superpowers/specs/2026-08-10-per-device-login.md)
-- migration safety tests for commercial_runtime/identity/device_registry.py
applied against registry.db, via commercial_runtime/security/migration_safety.py.

Mirrors commercial_runtime/tests/einvoicing_migration_test.py's approach:
standalone, product-independent synthetic fixture databases that stand in
for a real registry.db, rather than booting a real product and depending on
AURA_APP_DATA / registry_db.get_conn()'s real filesystem path. The failure-
injection test mirrors commercial_runtime/tests/migration_safety_test.py's
`test_migration_failure_leaves_original_data_recoverable_and_does_not_advance_version`
(inject a migrate_fn that raises after making a partial change).

This file proves the schema module itself, applied through
ensure_schema_version: additive only, safe under failure, idempotent, and
existing users/company_settings/audit_logs rows survive untouched. The real
per-product wiring (registry_db.py's init_registry_db() calling
ensure_schema_version with apply_identity_device_schema) is exercised
indirectly by every other commercial_runtime/identity test that calls
registry_db.get_conn()/init_registry_db().

Run:
    pytest commercial_runtime/tests/registry_migration_test.py -v
"""
import os
import sqlite3
import sys
from pathlib import Path

SUITE_ROOT = Path(__file__).resolve().parents[2]
if str(SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(SUITE_ROOT))

from commercial_runtime.identity.device_registry import apply_identity_device_schema  # noqa: E402
from commercial_runtime.security.migration_safety import ensure_schema_version  # noqa: E402

_DEVICE_TABLES = ('devices', 'user_devices')


def _make_v0_fixture(tmp_path):
    """A stand-in for an existing registry.db: users/company_settings/
    audit_logs rows already present, at user_version=0 -- registry.db had
    no PRAGMA user_version at all before this migration, so every existing
    on-disk registry.db implicitly reads version 0 (see
    REGISTRY_SCHEMA_VERSION's docstring in registry_db.py)."""
    db_path = os.path.join(tmp_path, 'registry.db')
    conn = sqlite3.connect(db_path)
    conn.execute('''
        CREATE TABLE users (
            id TEXT PRIMARY KEY, company_id TEXT NOT NULL, employee_id TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT DEFAULT 'employee'
        )
    ''')
    conn.execute('''
        CREATE TABLE company_settings (
            id TEXT PRIMARY KEY, company_id TEXT NOT NULL UNIQUE, country TEXT, currency TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE audit_logs (
            id TEXT PRIMARY KEY, company_id TEXT NOT NULL, action TEXT NOT NULL,
            entity_type TEXT NOT NULL, entity_id TEXT NOT NULL
        )
    ''')
    conn.execute("INSERT INTO users (id, company_id, employee_id, email, password_hash, role) "
                 "VALUES ('u1', 'c1', 'ADMIN-0001', 'admin@example.com', 'hash123', 'admin')")
    conn.execute("INSERT INTO company_settings (id, company_id, country, currency) "
                 "VALUES ('cs1', 'c1', 'JO', 'JOD')")
    conn.execute("INSERT INTO audit_logs (id, company_id, action, entity_type, entity_id) "
                 "VALUES ('al1', 'c1', 'ADMIN_CREATED', 'user', 'u1')")
    conn.execute('PRAGMA user_version = 0')
    conn.commit()
    return db_path, conn


def _existing_rows(conn):
    return {
        'users': conn.execute("SELECT id, company_id, employee_id, email, password_hash, role FROM users").fetchall(),
        'company_settings': conn.execute("SELECT id, company_id, country, currency FROM company_settings").fetchall(),
        'audit_logs': conn.execute("SELECT id, company_id, action, entity_type, entity_id FROM audit_logs").fetchall(),
    }


def test_migration_creates_device_tables_and_leaves_existing_data_untouched(tmp_path):
    db_path, conn = _make_v0_fixture(str(tmp_path))
    backup_dir = str(tmp_path / 'backups')
    before = _existing_rows(conn)

    ensure_schema_version(conn, db_path, target_version=1, migrate_fn=apply_identity_device_schema, backup_dir=backup_dir)

    assert conn.execute('PRAGMA user_version').fetchone()[0] == 1

    after = _existing_rows(conn)
    assert after == before, "pre-existing users/company_settings/audit_logs rows must survive byte-identical"

    for table in _DEVICE_TABLES:
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        assert cols, f"{table} should exist after migration"
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count == 0, f"{table} must be empty immediately after migration -- schema-only, no data writes"

    conn.close()


def test_pre_migration_backup_exists_on_disk(tmp_path):
    db_path, conn = _make_v0_fixture(str(tmp_path))
    backup_dir = str(tmp_path / 'backups')

    ensure_schema_version(conn, db_path, target_version=1, migrate_fn=apply_identity_device_schema, backup_dir=backup_dir)
    conn.close()

    backups = os.listdir(backup_dir)
    assert len(backups) == 1
    assert 'pre-migration-v0-to-v1' in backups[0]

    restore_conn = sqlite3.connect(os.path.join(backup_dir, backups[0]))
    assert restore_conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    row = restore_conn.execute("SELECT email FROM users").fetchone()
    assert row[0] == 'admin@example.com'
    for table in _DEVICE_TABLES:
        found = restore_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        assert found is None, f"pre-migration backup must NOT contain {table} -- it's a snapshot from before the migration ran"
    restore_conn.close()


def test_user_version_advances_0_to_1(tmp_path):
    db_path, conn = _make_v0_fixture(str(tmp_path))
    backup_dir = str(tmp_path / 'backups')

    assert conn.execute('PRAGMA user_version').fetchone()[0] == 0
    ensure_schema_version(conn, db_path, target_version=1, migrate_fn=apply_identity_device_schema, backup_dir=backup_dir)
    assert conn.execute('PRAGMA user_version').fetchone()[0] == 1
    conn.close()


def test_failing_migrate_fn_leaves_user_version_at_0_with_backup_still_on_disk(tmp_path):
    """Mirrors migration_safety_test.py's
    test_migration_failure_leaves_original_data_recoverable_and_does_not_advance_version:
    inject a migrate_fn that partially applies the real schema, then raises.
    The backup must exist and be intact, and user_version must NOT advance,
    so the next launch retries from the same safe starting point."""
    db_path, conn = _make_v0_fixture(str(tmp_path))
    backup_dir = str(tmp_path / 'backups')

    def _broken_migrate(c):
        apply_identity_device_schema(c)  # tables land in this transaction...
        raise RuntimeError("simulated failure mid-migration (e.g. disk full, crash)")

    raised = False
    try:
        ensure_schema_version(conn, db_path, target_version=1, migrate_fn=_broken_migrate, backup_dir=backup_dir)
    except RuntimeError:
        raised = True
    assert raised, "the simulated migration failure must propagate, not be swallowed"

    assert conn.execute('PRAGMA user_version').fetchone()[0] == 0, "user_version must NOT advance on a failed migration"

    backups = os.listdir(backup_dir)
    assert len(backups) == 1, "a pre-migration backup must exist even though the migration failed"
    restore_conn = sqlite3.connect(os.path.join(backup_dir, backups[0]))
    assert restore_conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    row = restore_conn.execute("SELECT email FROM users").fetchone()
    assert row[0] == 'admin@example.com', "backup must be a clean pre-migration snapshot, fully queryable"
    restore_conn.close()
    conn.close()


def test_running_init_a_second_time_is_a_noop_with_no_new_backup(tmp_path):
    db_path, conn = _make_v0_fixture(str(tmp_path))
    backup_dir = str(tmp_path / 'backups')

    ensure_schema_version(conn, db_path, target_version=1, migrate_fn=apply_identity_device_schema, backup_dir=backup_dir)
    first_backup_count = len(os.listdir(backup_dir))

    calls = {'n': 0}

    def _counting_migrate(c):
        calls['n'] += 1
        apply_identity_device_schema(c)

    ensure_schema_version(conn, db_path, target_version=1, migrate_fn=_counting_migrate, backup_dir=backup_dir)

    assert calls['n'] == 0, "already at target_version -- migrate_fn must not run again"
    assert len(os.listdir(backup_dir)) == first_backup_count, "no second backup on the no-op fast path"
    conn.close()


def test_apply_identity_device_schema_alone_is_idempotent():
    """Calling the schema function directly, twice, on the same connection
    (independent of ensure_schema_version's version gate) must not raise --
    every statement is IF NOT EXISTS."""
    conn = sqlite3.connect(':memory:')
    apply_identity_device_schema(conn)
    apply_identity_device_schema(conn)
    for table in _DEVICE_TABLES:
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        assert cols
    conn.close()
