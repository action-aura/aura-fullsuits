"""Aura Retail -- schema v58 migration: the licence-pause clock (keep-selling C3; claimed in
ROADMAP.md "retail schema v58 CLAIMED"). ONE nullable column, `sync_freshness.last_paused_seen_at`,
so the 72h sales stop can tell "sync is broken" from "we paused sync on purpose, then renewed".

A v57-shaped database is a real fresh install with the v58 column dropped and `PRAGMA user_version
= 57` stamped; the upgrade then runs through the REAL `ensure_schema_version` chain (backup +
integrity check before and after).

WHAT EACH TEST GUARDS (and the mutation that turns it RED):
  * upgrade: the column exists after 57 -> 58, the existing freshness row keeps every value it had,
    integrity ok, a pre-migration backup was written (drop the `_migrate_add_sync_pause_clock`
    call from the chain -> RED).
  * replay: running the whole chain again changes no row and does not fail on a duplicate column
    (an unguarded ALTER -> RED).
  * helpers: `record_paused_seen` writes the column and `load_sync_freshness` reads it back under
    the key the SyncService expects (break either -> RED).
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TMP_DIRS = []

PUSH_TS = '2026-10-01T08:00:00+00:00'
PULL_TS = '2026-10-01T08:05:00+00:00'
OVERRIDE_TS = '2026-10-01T09:00:00+00:00'


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _install():
    tmp = tempfile.mkdtemp(prefix='aura-retail-v58-')
    _TMP_DIRS.append(tmp)
    os.environ.update({'AURA_APP_DATA': tmp, 'AURA_STANDALONE': '1', 'AURA_SITE_RELAY_ENABLED': '0'})
    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()
    return sch, os.path.join(sch.SUBSYS_DIR, 'retail.db')


def _open(db):
    conn = sqlite3.connect(db, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(db, sql, params=()):
    conn = _open(db)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _make_v57(db):
    """A v57-shaped database: the v58 column gone, user_version 57, and a freshness row holding
    real values the upgrade must carry through untouched."""
    conn = _open(db)
    try:
        conn.execute('ALTER TABLE sync_freshness DROP COLUMN last_paused_seen_at')
        conn.execute('UPDATE sync_freshness SET last_push_success_at=?, last_pull_success_at=?, '
                     'offline_override_at=? WHERE id=1', (PUSH_TS, PULL_TS, OVERRIDE_TS))
        conn.execute('PRAGMA user_version = 57')
        conn.commit()
    finally:
        conn.close()


def _migrate(sch, db):
    from commercial_runtime.security.migration_safety import ensure_schema_version
    backup_dir = tempfile.mkdtemp(prefix='aura-retail-v58-backup-')
    _TMP_DIRS.append(backup_dir)
    conn = _open(db)
    try:
        ensure_schema_version(conn, db, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)
    finally:
        conn.close()
    return backup_dir


def _cols(db):
    return {r['name'] for r in _rows(db, 'PRAGMA table_info("sync_freshness")')}


def test_a_v57_database_upgrades_through_the_real_chain_to_v58():
    sch, db = _install()
    _make_v57(db)
    assert 'last_paused_seen_at' not in _cols(db), 'fixture must really be pre-v58 or this proves nothing'
    backup_dir = _migrate(sch, db)
    assert sch.RETAIL_SCHEMA_VERSION == 58
    assert _rows(db, 'PRAGMA user_version')[0]['user_version'] == 58
    assert _rows(db, 'PRAGMA integrity_check')[0]['integrity_check'] == 'ok'
    assert os.listdir(backup_dir), 'ensure_schema_version must have written its pre-migration backup'
    assert 'last_paused_seen_at' in _cols(db)
    row = _rows(db, 'SELECT * FROM sync_freshness WHERE id=1')[0]
    assert row['last_push_success_at'] == PUSH_TS, 'existing freshness values survive the ALTER'
    assert row['last_pull_success_at'] == PULL_TS
    assert row['offline_override_at'] == OVERRIDE_TS
    assert row['last_paused_seen_at'] is None, 'NULL = never observed paused (every pre-v58 install)'


def test_replaying_the_whole_chain_changes_no_row_and_does_not_fail():
    sch, db = _install()
    conn = _open(db)
    try:
        conn.execute("UPDATE sync_freshness SET last_paused_seen_at='2026-10-02T00:00:00+00:00', "
                     "last_push_success_at=? WHERE id=1", (PUSH_TS,))
        conn.commit()
    finally:
        conn.close()
    before = _rows(db, 'SELECT * FROM sync_freshness ORDER BY id')
    conn = _open(db)
    try:
        sch._migrate_retail_schema(conn)
        sch._migrate_retail_schema(conn)  # a second ALTER without the column guard would raise here
        conn.commit()
    finally:
        conn.close()
    assert _rows(db, 'SELECT * FROM sync_freshness ORDER BY id') == before


def test_record_paused_seen_and_load_round_trip_under_the_key_the_service_reads():
    sch, db = _install()
    conn = _open(db)
    try:
        assert sch.load_sync_freshness(conn)['paused_seen_at'] is None
        sch.record_paused_seen(conn, '2026-10-03T12:00:00+00:00')
        conn.commit()
        loaded = sch.load_sync_freshness(conn)
    finally:
        conn.close()
    assert loaded['paused_seen_at'] == '2026-10-03T12:00:00+00:00'
    # the other three values still come back from the same single call (one query, one instant)
    assert set(loaded) == {'push', 'pull', 'override_at', 'paused_seen_at'}