"""Registry v9 -- DEVICE-SYNC half: five new `devices` columns and the
`admin_device_assignment` table, with the epoch-1 backfill.

Same technique as test_registry_v9_accounts_schema.py: a COPY of the real lab
registry (census `proof-a2`: v8, one non-main device) is re-copied into
tmp_path with its -wal/-shm and migrated through the REAL `init_registry_db()`
path. The census copy has no main computer, so the test flips its one device to
`is_admin_device=1` first -- the state whose backfill is the interesting one.

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/identity/tests/test_registry_v9_device_sync_schema.py -v
"""
import os
import shutil
import sqlite3

import pytest

from commercial_runtime.identity import registry_db

CENSUS = r"C:\Users\MSI\.claude\jobs\c5a4f4c4\tmp\p2s\census\copies\proof-a2\database"
DEVICE_ID = "c3e0ab70-0ae9-45e7-9c7a-f4991bf06219"
COMPANY_ID = "aa124f0c-fdd0-4eb7-85e9-f340d6cf6eac"
NEW_COLUMNS = ("row_version", "updated_at_utc", "app_version",
               "retail_schema_version", "registry_schema_version")


def _q(db_path, sql, args=()):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


@pytest.fixture
def v8_main_device(tmp_path, monkeypatch):
    src = os.path.join(CENSUS, "registry.db")
    if not os.path.exists(src):
        pytest.skip("census copy not present on this machine")
    db_dir = tmp_path / "database"
    db_dir.mkdir()
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(src + suffix):
            shutil.copy2(src + suffix, str(db_dir / ("registry.db" + suffix)))
    db_path = db_dir / "registry.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE devices SET is_admin_device=1 WHERE id=?", (DEVICE_ID,))
    conn.commit()
    conn.close()
    assert _q(db_path, "PRAGMA user_version")[0][0] == 8
    assert not {r[1] for r in _q(db_path, "PRAGMA table_info(devices)")} & set(NEW_COLUMNS)
    monkeypatch.setattr(registry_db, "_db_dir", str(db_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(db_path))
    return db_dir, db_path


def test_migration_adds_columns_table_and_backfills_epoch_one(v8_main_device):
    db_dir, db_path = v8_main_device
    first_seen = _q(db_path, "SELECT first_seen_at FROM devices WHERE id=?", (DEVICE_ID,))[0][0]
    registry_db.init_registry_db()

    cols = {r[1] for r in _q(db_path, "PRAGMA table_info(devices)")}
    assert set(NEW_COLUMNS) <= cols
    # The device row itself is untouched apart from the new columns' defaults.
    row = _q(db_path, "SELECT is_admin_device, status, row_version, updated_at_utc, app_version, "
                      "retail_schema_version, registry_schema_version FROM devices WHERE id=?",
             (DEVICE_ID,))[0]
    assert row == (1, "active", 1, None, None, None, None)

    # Backfill: exactly one epoch-1 assignment, naming the flagged device, with
    # the documented first_seen_at proxy for the time.
    assert _q(db_path, "SELECT company_id, device_id, epoch, assigned_at_utc, "
                       "assigned_by_user_uid, assigned_by_device_id FROM admin_device_assignment") == [
        (COMPANY_ID, DEVICE_ID, 1, first_seen, None, None)]


def test_migration_emits_nothing_and_leaves_integrity_and_a_backup(v8_main_device):
    db_dir, db_path = v8_main_device
    assert _q(db_path, "PRAGMA integrity_check")[0][0] == "ok"
    registry_db.init_registry_db()
    # Count the effect: the assignment row DID land (so the backfill ran) ...
    assert _q(db_path, "SELECT COUNT(*) FROM admin_device_assignment")[0][0] == 1
    # ... and no device / admin_device event was queued for it.
    assert _q(db_path, "SELECT COUNT(*) FROM sync_outbox")[0][0] == 0
    assert _q(db_path, "PRAGMA integrity_check")[0][0] == "ok"
    # S-AUTH (AUDIT-033) moved the registry to v10 on top of v9: this migrates a v8 copy
    # through the REAL current chain, so the final marker is 10. The v9 step's own claims
    # (columns, backfill, no emit, backup taken) are asserted unchanged below.
    assert _q(db_path, "PRAGMA user_version")[0][0] == 10
    backups = os.listdir(str(db_dir / "migration_backups"))
    assert any("v8-to-v10" in b for b in backups), backups


def test_rerun_never_overwrites_a_real_assignment(v8_main_device):
    db_dir, db_path = v8_main_device
    registry_db.init_registry_db()
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE admin_device_assignment SET epoch=5, assigned_by_user_uid='u-real'")
    conn.commit()
    conn.close()
    # Force the chain to run again (a retry after a partial failure).
    conn = sqlite3.connect(str(db_path))
    registry_db._migrate_registry_schema(conn)
    conn.close()
    assert _q(db_path, "SELECT epoch, assigned_by_user_uid FROM admin_device_assignment") == [(5, "u-real")]


def test_no_main_computer_means_no_assignment_row(tmp_path, monkeypatch):
    """A company with devices but no flagged main gets NO backfill: inventing an
    assignment for a company that never had a main would make its first real
    claim look like a takeover."""
    src = os.path.join(CENSUS, "registry.db")
    if not os.path.exists(src):
        pytest.skip("census copy not present on this machine")
    db_dir = tmp_path / "database"
    db_dir.mkdir()
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(src + suffix):
            shutil.copy2(src + suffix, str(db_dir / ("registry.db" + suffix)))
    monkeypatch.setattr(registry_db, "_db_dir", str(db_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(db_dir / "registry.db"))
    registry_db.init_registry_db()
    assert _q(db_dir / "registry.db", "SELECT COUNT(*) FROM devices")[0][0] == 1
    assert _q(db_dir / "registry.db", "SELECT COUNT(*) FROM admin_device_assignment")[0][0] == 0


def test_a_clinic_shaped_registry_gets_columns_and_no_assignment(tmp_path, monkeypatch):
    """Clinic shares registry.db but never registers a device: the migration must
    add the (harmless) columns and an empty assignment table, and nothing else."""
    db_dir = tmp_path / "database"
    db_dir.mkdir()
    db_path = db_dir / "registry.db"
    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 8)
    monkeypatch.setattr(registry_db, "_db_dir", str(db_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(db_path))
    registry_db.init_registry_db()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, clinic_role) "
        "VALUES ('c-u1', 'c-co', 'c-e1', 'dr@clinic.test', 'x', 'admin', 'dentist')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(registry_db, "REGISTRY_SCHEMA_VERSION", 9)
    registry_db.init_registry_db()
    assert _q(db_path, "SELECT COUNT(*) FROM devices")[0][0] == 0
    assert _q(db_path, "SELECT COUNT(*) FROM admin_device_assignment")[0][0] == 0
    assert _q(db_path, "SELECT COUNT(*) FROM sync_outbox")[0][0] == 0
    assert _q(db_path, "SELECT email, role, clinic_role FROM users") == [("dr@clinic.test", "admin", "dentist")]
    assert _q(db_path, "PRAGMA integrity_check")[0][0] == "ok"
