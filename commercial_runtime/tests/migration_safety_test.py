"""
Wave 1B (Part K) -- schema migration safety tests for
commercial_runtime/security/migration_safety.py.

Standalone, product-independent: builds its own throwaway SQLite databases
directly rather than importing products/*/backend, so it isn't subject to
the module-level AURA_APP_DATA caching pattern documented in
docs/release/wave1b/pytest-isolation-correction.md (AUDIT-010) -- there is
nothing here for that to interfere with.

Run:
    pytest commercial_runtime/tests/migration_safety_test.py -v
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

SUITE_ROOT = Path(__file__).resolve().parents[2]
if str(SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(SUITE_ROOT))

from commercial_runtime.security.migration_safety import (  # noqa: E402
    MigrationError, ensure_schema_version, prune_old_migration_backups,
)


def _make_db(tmp_path):
    db_path = os.path.join(tmp_path, 'test.db')
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    conn.execute("INSERT INTO widgets (name) VALUES ('real synthetic test data')")
    conn.commit()
    return db_path, conn


def test_first_migration_runs_and_creates_a_backup(tmp_path):
    db_path, conn = _make_db(str(tmp_path))
    backup_dir = str(tmp_path / 'backups')

    def add_column(c):
        c.execute("ALTER TABLE widgets ADD COLUMN price REAL DEFAULT 0")

    assert conn.execute('PRAGMA user_version').fetchone()[0] == 0
    ensure_schema_version(conn, db_path, target_version=1, migrate_fn=add_column, backup_dir=backup_dir)

    assert conn.execute('PRAGMA user_version').fetchone()[0] == 1
    cols = [r[1] for r in conn.execute("PRAGMA table_info(widgets)").fetchall()]
    assert 'price' in cols
    backups = os.listdir(backup_dir)
    assert len(backups) == 1, f"expected exactly one pre-migration backup, found {backups}"
    conn.close()


def test_already_current_version_is_a_true_noop_no_backup(tmp_path):
    db_path, conn = _make_db(str(tmp_path))
    backup_dir = str(tmp_path / 'backups')
    conn.execute('PRAGMA user_version = 1')
    conn.commit()

    called = {'n': 0}

    def should_not_run(c):
        called['n'] += 1

    ensure_schema_version(conn, db_path, target_version=1, migrate_fn=should_not_run, backup_dir=backup_dir)

    assert called['n'] == 0, "migrate_fn must not run when already at target_version"
    assert not os.path.isdir(backup_dir), "no backup should be created on the no-op fast path"
    conn.close()


def test_migration_failure_leaves_original_data_recoverable_and_does_not_advance_version(tmp_path):
    """The core data-safety proof this wave's Definition of Done requires:
    simulate a migration failure and confirm the OLD database remains
    intact and recoverable, not silently reset or left in a broken state."""
    db_path, conn = _make_db(str(tmp_path))
    backup_dir = str(tmp_path / 'backups')

    def broken_migration(c):
        c.execute("ALTER TABLE widgets ADD COLUMN price REAL DEFAULT 0")
        raise RuntimeError("simulated failure mid-migration (e.g. disk full, crash)")

    raised = False
    try:
        ensure_schema_version(conn, db_path, target_version=1, migrate_fn=broken_migration, backup_dir=backup_dir)
    except RuntimeError:
        raised = True
    assert raised, "the simulated migration failure must propagate, not be swallowed"

    # version must NOT have advanced -- next launch will correctly retry
    assert conn.execute('PRAGMA user_version').fetchone()[0] == 0

    # A pre-migration backup exists and is fully intact/queryable -- this is
    # the actual recovery path a real deployment would use.
    backups = os.listdir(backup_dir)
    assert len(backups) == 1
    restore_conn = sqlite3.connect(os.path.join(backup_dir, backups[0]))
    row = restore_conn.execute("SELECT name FROM widgets").fetchone()
    assert row[0] == 'real synthetic test data'
    restore_conn.close()
    conn.close()


def test_pre_migration_backup_of_an_already_corrupt_database_is_refused(tmp_path):
    """If the database was already damaged for an unrelated reason, don't
    let a migration attempt make things more confusing -- refuse up front
    with a clear error, before touching anything."""
    db_path = str(tmp_path / 'corrupt.db')
    with open(db_path, 'wb') as f:
        f.write(b'this is not a valid sqlite file')
    backup_dir = str(tmp_path / 'backups')

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("SELECT 1")
    except sqlite3.DatabaseError:
        pass  # expected -- it's not a real database

    try:
        ensure_schema_version(conn, db_path, target_version=1, migrate_fn=lambda c: None, backup_dir=backup_dir)
        assert False, "expected MigrationError for an already-corrupt database"
    except MigrationError as e:
        assert 'BEFORE any migration was attempted' in str(e)
        assert 'schema version' in str(e)
    conn.close()


def test_newer_database_version_is_refused_not_migrated(tmp_path):
    """AUDIT: a shop that installs a newer build, then rolls back to an
    older one, must not have the older code silently operate on a schema
    it doesn't understand. Refuse outright -- no downgrade, no repair."""
    db_path, conn = _make_db(str(tmp_path))
    backup_dir = str(tmp_path / 'backups')
    conn.execute('PRAGMA user_version = 5')
    conn.commit()

    # Anti-vacuity: prove the fixture really is ahead of target_version
    # before calling, so a fixture that silently failed to set the version
    # can't make this test pass for the wrong reason.
    assert conn.execute('PRAGMA user_version').fetchone()[0] == 5

    called = {'n': 0}

    def should_not_run(c):
        called['n'] += 1

    raised = False
    try:
        ensure_schema_version(conn, db_path, target_version=3, migrate_fn=should_not_run, backup_dir=backup_dir)
    except MigrationError as e:
        raised = True
        msg = str(e)
        assert '5' in msg, "message must name the database's (higher) version"
        assert '3' in msg, "message must name the code's (lower) expected version"
        assert 'newer' in msg.lower()
    assert raised, "a database version AHEAD of target_version must raise MigrationError"

    assert called['n'] == 0, "migrate_fn must never run on the refusal path"
    assert not os.path.isdir(backup_dir), "no backup may be taken on the refusal path -- nothing is being changed"
    assert conn.execute('PRAGMA user_version').fetchone()[0] == 5, "user_version must be untouched by the refusal"
    conn.close()


def test_prune_keeps_only_the_most_recent_n_backups(tmp_path):
    backup_dir = str(tmp_path / 'backups')
    os.makedirs(backup_dir)
    for i in range(8):
        path = os.path.join(backup_dir, f'backup-{i}.db')
        with open(path, 'wb') as f:
            f.write(b'x')
        os.utime(path, (i, i))  # deterministic mtime ordering

    prune_old_migration_backups(backup_dir, keep=3)
    remaining = os.listdir(backup_dir)
    assert len(remaining) == 3
    assert set(remaining) == {'backup-7.db', 'backup-6.db', 'backup-5.db'}
