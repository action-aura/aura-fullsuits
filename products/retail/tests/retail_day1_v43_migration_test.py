"""DAY-1c: schema v43 adds the nullable `payments.cash_session_id`.

A v42 database (with and without the `payments` columns retail_api creates
lazily at runtime) must migrate through the REAL `ensure_schema_version` chain
to v43, gain the column, keep its existing rows (no backfill, NULL), pass
`PRAGMA integrity_check`, and replaying the chain must be a no-op.

The v42 shape is manufactured by `ALTER TABLE payments DROP COLUMN` plus
`PRAGMA user_version = 42` on a real, freshly built install -- the version
stamp and the missing column are the whole difference between v42 and v43.

Run:
    pytest products/retail/tests/retail_day1_v43_migration_test.py -q
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


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _install():
    tmp = tempfile.mkdtemp(prefix='aura-retail-v43-')
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


def _cols(conn):
    return {r[1] for r in conn.execute('PRAGMA table_info(payments)').fetchall()}


def _rcols(conn):
    return {r[1] for r in conn.execute('PRAGMA table_info(returns)').fetchall()}


def _make_v42(db, with_lazy_columns):
    conn = _open(db)
    try:
        if with_lazy_columns:
            for col, decl in (('party_type', 'TEXT'), ('related_type', 'TEXT'), ('status', 'TEXT')):
                if col not in _cols(conn):
                    conn.execute(f'ALTER TABLE payments ADD COLUMN {col} {decl}')
        if 'cash_session_id' in _cols(conn):
            conn.execute('ALTER TABLE payments DROP COLUMN cash_session_id')
        # DAY-1d review F1: the v43 extension's column is absent from a v42 DB too.
        if 'tax_refunded' in _rcols(conn):
            conn.execute('ALTER TABLE returns DROP COLUMN tax_refunded')
        conn.execute("INSERT INTO payments (company_id, method, amount) VALUES (1, 'cash', 5.0)")
        conn.execute("INSERT INTO returns (company_id, return_number, refund_amount) VALUES (1, 'RET-OLD', 7.0)")
        conn.execute('PRAGMA user_version = 42')
        conn.commit()
        assert 'cash_session_id' not in _cols(conn), 'precondition: this is a v42-shaped payments table'
        assert 'tax_refunded' not in _rcols(conn), 'precondition: no returns.tax_refunded yet'
    finally:
        conn.close()


def _migrate(sch, db):
    from commercial_runtime.security.migration_safety import ensure_schema_version
    backup_dir = tempfile.mkdtemp(prefix='aura-retail-v43-backup-')
    _TMP_DIRS.append(backup_dir)
    conn = _open(db)
    try:
        ensure_schema_version(conn, db, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)
    finally:
        conn.close()


def _check(sch, db):
    conn = _open(db)
    try:
        assert sch.RETAIL_SCHEMA_VERSION == 43
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 43
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert 'cash_session_id' in _cols(conn)
        row = conn.execute('SELECT amount, cash_session_id FROM payments').fetchone()
        assert row['amount'] == 5.0 and row['cash_session_id'] is None, 'existing rows keep their data and get NULL'
        assert 'tax_refunded' in _rcols(conn)
        old = conn.execute("SELECT refund_amount, tax_refunded FROM returns WHERE return_number='RET-OLD'").fetchone()
        assert old['refund_amount'] == 7.0 and old['tax_refunded'] is None,             'a pre-existing return keeps its data and gets NULL (the report falls back for it)'
    finally:
        conn.close()


def test_v42_without_the_lazy_payments_columns_migrates_to_v43():
    sch, db = _install()
    _make_v42(db, with_lazy_columns=False)
    _migrate(sch, db)
    _check(sch, db)


def test_v42_with_the_lazy_payments_columns_migrates_to_v43():
    sch, db = _install()
    _make_v42(db, with_lazy_columns=True)
    _migrate(sch, db)
    _check(sch, db)


def test_replaying_the_step_is_a_no_op():
    sch, db = _install()
    _make_v42(db, with_lazy_columns=True)
    _migrate(sch, db)
    conn = _open(db)
    try:
        before = _cols(conn)
        sch._migrate_add_payment_cash_session(conn)      # direct replay
        sch._migrate_add_payment_cash_session(conn)
        assert _cols(conn) == before
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        conn.close()
    _migrate(sch, db)                                    # chain replay at target: fast path
    _check(sch, db)


def test_a_database_with_no_payments_table_is_left_alone():
    sch, _ = _install()
    conn = sqlite3.connect(':memory:')
    sch._migrate_add_payment_cash_session(conn)          # must not raise
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='payments'").fetchone()[0] == 0
    conn.close()


def test_a_database_already_at_v43_without_returns_tax_refunded_gains_it_on_boot():
    """DAY-1d review F1: the v43 extension. A database that reached v43 BEFORE
    the column existed has user_version == 43, so ensure_schema_version's fast
    path never re-enters the chain -- init_retail's boot step must add it."""
    sch, db = _install()
    conn = _open(db)
    try:
        conn.execute('ALTER TABLE returns DROP COLUMN tax_refunded')
        conn.execute('PRAGMA user_version = 43')
        conn.commit()
        assert 'tax_refunded' not in _rcols(conn)
    finally:
        conn.close()
    sch.init_retail()
    conn = _open(db)
    try:
        assert 'tax_refunded' in _rcols(conn)
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 43
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        before = _rcols(conn)
        sch._migrate_add_return_tax_refunded(conn)       # replay: no-op
        assert _rcols(conn) == before
    finally:
        conn.close()
