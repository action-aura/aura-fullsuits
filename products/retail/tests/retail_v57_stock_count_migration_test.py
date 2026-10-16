"""Aura Retail -- schema v57 migration: stock counts (STOCK-TAKE; claimed in ROADMAP.md
"retail schema v57 CLAIMED for STOCK-TAKE").

A v56-shaped database is a real fresh install with the two v57 tables dropped and
`PRAGMA user_version = 56` stamped; the upgrade then runs through the REAL
`ensure_schema_version` chain (backup + integrity check before and after).

WHAT EACH TEST GUARDS (and the mutation that turns it RED):
  * upgrade: the tables exist after 56 -> 57, existing business rows are untouched, integrity ok,
    a pre-migration backup was written (drop the `_migrate_add_stock_counts` call -> RED).
  * constraints: a status outside open/posted/cancelled is refused, a second line for the same
    (count, product) is refused, a duplicate uid is refused (drop the CHECK / UNIQUE -> RED).
  * replay: running the whole chain again changes no row (a non-idempotent step -> RED).
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _install():
    tmp = tempfile.mkdtemp(prefix='aura-retail-v57-')
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


def _make_v56(db):
    """A v56-shaped database: both v57 tables gone, user_version 56, plus a business row that
    the upgrade must leave exactly as it is."""
    conn = _open(db)
    try:
        conn.execute('DROP TABLE IF EXISTS stock_count_lines')
        conn.execute('DROP TABLE IF EXISTS stock_counts')
        conn.execute("INSERT INTO branches (id, company_id, name, uid) VALUES (91, 'c57', 'Main', 'b-uid-57')")
        conn.execute('PRAGMA user_version = 56')
        conn.commit()
    finally:
        conn.close()


def _migrate(sch, db):
    from commercial_runtime.security.migration_safety import ensure_schema_version
    backup_dir = tempfile.mkdtemp(prefix='aura-retail-v57-backup-')
    _TMP_DIRS.append(backup_dir)
    conn = _open(db)
    try:
        ensure_schema_version(conn, db, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)
    finally:
        conn.close()
    return backup_dir


def test_a_v56_database_upgrades_through_the_real_chain_to_v57():
    sch, db = _install()
    _make_v56(db)
    before = _rows(db, "SELECT * FROM branches ORDER BY id")
    assert not _rows(db, "SELECT name FROM sqlite_master WHERE name IN ('stock_counts','stock_count_lines')")
    backup_dir = _migrate(sch, db)
    assert sch.RETAIL_SCHEMA_VERSION == 57
    assert _rows(db, 'PRAGMA user_version')[0]['user_version'] == 57
    assert _rows(db, 'PRAGMA integrity_check')[0]['integrity_check'] == 'ok'
    assert os.listdir(backup_dir), 'ensure_schema_version must have written its pre-migration backup'
    tables = {r['name'] for r in _rows(db, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'stock_counts', 'stock_count_lines'} <= tables
    cols = {r['name'] for r in _rows(db, 'PRAGMA table_info("stock_counts")')}
    assert {'uid', 'company_id', 'branch_id', 'status', 'note', 'started_by', 'started_at',
            'posted_at', 'posted_by'} <= cols
    lcols = {r['name'] for r in _rows(db, 'PRAGMA table_info("stock_count_lines")')}
    assert {'uid', 'company_id', 'branch_id', 'count_uid', 'product_id', 'counted_qty',
            'system_qty_at_count', 'variance', 'counted_at', 'counted_by'} <= lcols
    assert _rows(db, "SELECT * FROM branches ORDER BY id") == before, 'existing rows are untouched'


def test_constraints_refuse_a_bad_status_a_second_line_and_a_duplicate_uid():
    sch, db = _install()
    conn = _open(db)
    try:
        conn.execute("INSERT INTO stock_counts (uid, company_id, branch_id) VALUES ('u1','c',1)")
        assert conn.execute("SELECT status FROM stock_counts").fetchone()[0] == 'open', 'default is open'
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO stock_counts (uid, company_id, branch_id, status) VALUES ('u2','c',1,'done')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO stock_counts (uid, company_id, branch_id) VALUES ('u1','c',1)")
        ins = ("INSERT INTO stock_count_lines (uid, company_id, branch_id, count_uid, product_id, counted_qty, "
               "system_qty_at_count, variance) VALUES (?,?,?,?,?,?,?,?)")
        conn.execute(ins, ('l1', 'c', 1, 'u1', 'p1', 3, 5, -2))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(ins, ('l2', 'c', 1, 'u1', 'p1', 4, 5, -1))     # same (count, product)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(ins, ('l1', 'c', 1, 'u1', 'p2', 4, 5, -1))     # same uid
    finally:
        conn.close()


def test_replaying_the_whole_chain_changes_no_row():
    sch, db = _install()
    conn = _open(db)
    try:
        conn.execute("INSERT INTO stock_counts (uid, company_id, branch_id, note) VALUES ('u1','c',1,'n')")
        conn.execute("INSERT INTO stock_count_lines (uid, company_id, branch_id, count_uid, product_id, "
                     "counted_qty, system_qty_at_count, variance) VALUES ('l1','c',1,'u1','p1',3,5,-2)")
        conn.commit()
    finally:
        conn.close()
    tables = ('stock_counts', 'stock_count_lines')
    before = {t: _rows(db, f'SELECT * FROM "{t}" ORDER BY id') for t in tables}
    conn = _open(db)
    try:
        sch._migrate_retail_schema(conn)
        sch._migrate_retail_schema(conn)
        conn.commit()
    finally:
        conn.close()
    assert {t: _rows(db, f'SELECT * FROM "{t}" ORDER BY id') for t in tables} == before
