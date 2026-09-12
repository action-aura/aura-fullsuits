"""Aura Retail -- schema v17 regression coverage: launch-readiness Phase 6
("catalogue correctness"), stage 6a-i, Task A. See database/schema.py::
_migrate_add_sync_conflicts_and_drop_quantity_reserved and
docs/launch-readiness/phase6-catalogue-correctness.md.

WHAT V17 CLAIMS, AND THEREFORE WHAT THIS FILE HAS TO PROVE.

Two schema changes, both additive-or-safely-destructive, NOT the reject-stale
gate itself (that is stage 6a-ii and does not exist yet):

  1. `sync_conflicts` is created -- empty, no writer yet -- the visible
     landing spot stage 6a-ii will use instead of a silent drop.
  2. `inventory_balances.quantity_reserved` is DROPPED. This is the one
     genuinely destructive step in this migration, so this file's real job
     is proving it did not disturb the ONE thing that table exists to
     support: Phase 3's ledger-is-truth invariant
     (core/retail/stock_reconciliation.compute_drift) and the
     UNIQUE(company_id, product_id, branch_id) constraint that keeps one
     balance row per key.

Follows the SAME bootstrap convention as retail_v15_ledger_truth_migration_
test.py and retail_v16_terminal_cash_drawer_test.py (no shared conftest.py
exists for products/retail/tests/): its own temp app-data dir per test via
`_install()`, real `init_retail()`, no Flask app anywhere in this file. Kept
deliberately separate from the HTTP-route-driven row_version bump proofs
(retail_v17_row_version_bump_test.py) -- mixing this file's direct
`database.schema.BASE_DIR`/`SUBSYS_DIR` reassignment with a module-level
Flask `app` built once at import time would let one test's temp database
silently become another test's, exactly the class of cross-test
contamination `_install()`'s own docstring exists to avoid.

Run:
    pytest products/retail/tests/retail_v17_catalogue_migration_test.py -v
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

_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


# ── fixture bedrock (mirrors retail_v15_ledger_truth_migration_test.py) ─────

def _fresh_app_data(prefix):
    tmp = tempfile.mkdtemp(prefix=prefix)
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp
    return tmp


def _install():
    """A REAL install: fresh temp AURA_APP_DATA, the genuine `init_retail()`
    boot path, the full v0 -> current migration chain, real demo seed data.
    Deliberately not a hand-built minimal schema -- see
    retail_v15_ledger_truth_migration_test.py's identical helper for why."""
    tmp = _fresh_app_data('aura-retail-v17-')
    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()
    return sch, os.path.join(sch.SUBSYS_DIR, 'retail.db')


def _open(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def _user_version(db_path):
    conn = _open(db_path)
    try:
        return conn.execute('PRAGMA user_version').fetchone()[0]
    finally:
        conn.close()


def _integrity_ok(db_path):
    conn = _open(db_path)
    try:
        row = conn.execute('PRAGMA integrity_check').fetchone()
        return bool(row) and row[0] == 'ok'
    finally:
        conn.close()


def _new_company(conn, branch_count=1):
    company_id = uuid.uuid4().hex
    branch_ids = []
    for i in range(branch_count):
        cur = conn.execute(
            'INSERT INTO branches (company_id,name) VALUES (?,?)',
            (company_id, f'Branch {i + 1}'))
        branch_ids.append(cur.lastrowid)
    return company_id, branch_ids


def _product(conn, company_id, sku):
    product_id = str(uuid.uuid4())
    conn.execute(
        'INSERT INTO products (id,company_id,sku,name,cost_price,sell_price) '
        'VALUES (?,?,?,?,?,?)',
        (product_id, company_id, sku, f'Product {sku}', 4.0, 10.0))
    return product_id


def _balance(conn, company_id, product_id, branch_id, quantity):
    conn.execute(
        'INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) '
        'VALUES (?,?,?,?)',
        (company_id, product_id, branch_id, quantity))


def _movement(conn, company_id, product_id, branch_id, quantity, movement_type='sale_out'):
    conn.execute(
        'INSERT INTO inventory_movements '
        '(company_id,product_id,branch_id,movement_type,quantity,reference,created_by) '
        'VALUES (?,?,?,?,?,?,?)',
        (company_id, product_id, branch_id, movement_type, quantity, 'FIXTURE', 'System'))


def _drift(db_path, company_id):
    from core.retail.stock_reconciliation import compute_drift
    conn = _open(db_path)
    try:
        return compute_drift(conn, company_id)
    finally:
        conn.close()


# ── 1. sync_conflicts exists, empty, and is idempotent ──────────────────────

def test_sync_conflicts_table_exists_after_a_fresh_install():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_conflicts'"
        ).fetchone()
        assert row is not None, 'sync_conflicts must exist on a fresh v17 install'
        cols = {r[1] for r in conn.execute('PRAGMA table_info(sync_conflicts)').fetchall()}
        # Exactly what phase6-catalogue-correctness.md asks a human to be
        # able to act on: which row, which company, what kind of write was
        # rejected, the version comparison, when, and the full payload.
        for expected in ('id', 'company_id', 'entity_type', 'entity_id', 'event_type',
                          'local_row_version', 'incoming_row_version',
                          'incoming_payload', 'detected_at_utc'):
            assert expected in cols, f'sync_conflicts is missing column {expected!r}'
        # No writer exists yet this stage -- stage 6a-ii is the first thing
        # that ever inserts a row.
        assert conn.execute('SELECT COUNT(*) FROM sync_conflicts').fetchone()[0] == 0
    finally:
        conn.close()


def test_v17_migration_is_idempotent_a_second_pass_is_a_clean_noop():
    sch, db_path = _install()
    before_tables = _open(db_path)
    try:
        table_list_before = sorted(
            r[0] for r in before_tables.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
    finally:
        before_tables.close()

    conn = _open(db_path)
    try:
        conn.execute('PRAGMA user_version = 16')
        conn.commit()
    finally:
        conn.close()

    # Second pass: current=16, target=17 -- runs the WHOLE chain again
    # (ensure_schema_version only knows "behind", not "behind by one"), but
    # every earlier step is its own idempotency-guarded no-op, and v17's own
    # function re-checks PRAGMA table_info/sqlite_master before doing
    # anything, so this must be silent and harmless.
    conn = _open(db_path)
    try:
        sch._migrate_retail_schema(conn)
        conn.commit()
    finally:
        conn.close()

    assert _user_version(db_path) == 16, (
        'this test only re-runs _migrate_retail_schema directly, not '
        'ensure_schema_version, so the marker is advanced separately below')

    from commercial_runtime.security.migration_safety import ensure_schema_version
    conn = _open(db_path)
    try:
        ensure_schema_version(conn, db_path, sch.RETAIL_SCHEMA_VERSION,
                               sch._migrate_retail_schema,
                               backup_dir=os.path.join(sch.BASE_DIR, 'migration_backups'))
    finally:
        conn.close()

    assert _user_version(db_path) == sch.RETAIL_SCHEMA_VERSION
    assert _integrity_ok(db_path)

    after = _open(db_path)
    try:
        table_list_after = sorted(
            r[0] for r in after.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        conflicts_rows = after.execute('SELECT COUNT(*) FROM sync_conflicts').fetchone()[0]
    finally:
        after.close()
    assert table_list_after == table_list_before, (
        'a second v17 pass must not create, drop or duplicate any table')
    assert conflicts_rows == 0


def test_v17_reaches_head_with_integrity_ok_on_a_fresh_install():
    sch, db_path = _install()
    # `>=`, not `==`: this asserts v17 has not been REVERTED, which is what it
    # is really for. Pinned to `== 17` it instead asserted "the head is 17",
    # which stops being true the moment any later migration lands and says
    # nothing about whether v17 itself still works. Matches the sibling v15
    # file's own `>= 15`, and the `_assert_landed_on_head` convention.
    assert sch.RETAIL_SCHEMA_VERSION >= 17
    assert _user_version(db_path) == sch.RETAIL_SCHEMA_VERSION
    assert _integrity_ok(db_path)


# ── 2. quantity_reserved is dropped, idempotently ────────────────────────────

def test_quantity_reserved_is_dropped_from_a_fresh_install():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        cols = {r[1] for r in conn.execute('PRAGMA table_info(inventory_balances)').fetchall()}
    finally:
        conn.close()
    assert 'quantity_reserved' not in cols
    assert 'quantity_on_hand' in cols  # the column this table exists for is untouched


def test_dropping_quantity_reserved_is_idempotent_on_a_database_that_never_had_the_column():
    """A fresh v17 install never had the column to begin with (the base
    executescript's frozen v6 shape is superseded by the full migration
    chain before init_retail() returns) -- re-running the migration function
    directly against a database that ALREADY lacks the column must not
    raise `sqlite3.OperationalError: no such column`."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        sch._migrate_add_sync_conflicts_and_drop_quantity_reserved(conn)  # must not raise
        conn.commit()
        cols = {r[1] for r in conn.execute('PRAGMA table_info(inventory_balances)').fetchall()}
    finally:
        conn.close()
    assert 'quantity_reserved' not in cols


def test_upgrading_a_real_v16_shaped_database_with_the_column_present_drops_it():
    """Simulates the actual upgrade path: an install that reached v16 BEFORE
    this release, so its `inventory_balances` still physically carries
    `quantity_reserved` (with real, non-zero legacy values in it -- proving
    the drop discards exactly that column and nothing else). `_install()` --
    the only realistic bootstrap this suite has -- runs the FULL chain to
    today's head in one pass, so there is no way to get a genuinely-frozen
    pre-v17 database out of it directly; the column is re-added by hand
    here, matching the `_rewind_to_v14`/`_rewind_to_v16`-style idiom every
    other v13-v16 migration test file in this suite already uses to exercise
    one step in isolation against realistic data."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        conn.execute('ALTER TABLE inventory_balances ADD COLUMN quantity_reserved REAL DEFAULT 0')
        company_id, branch_ids = _new_company(conn)
        pid = _product(conn, company_id, 'V17-UPG-1')
        conn.execute(
            'UPDATE inventory_balances SET quantity_reserved=? '
            'WHERE company_id=? AND product_id=? AND branch_id=?',
            (0, company_id, pid, branch_ids[0]))
        # Not every product has a balance row from _product() alone --
        # insert one explicitly, carrying a real legacy quantity_reserved
        # value, to prove the column really held data before the drop.
        conn.execute(
            'INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand,quantity_reserved) '
            'VALUES (?,?,?,?,?)',
            (company_id, pid, branch_ids[0], 10.0, 3.0))
        conn.execute('PRAGMA user_version = 16')
        conn.commit()
        cols_before = {r[1] for r in conn.execute('PRAGMA table_info(inventory_balances)').fetchall()}
        assert 'quantity_reserved' in cols_before
    finally:
        conn.close()

    from commercial_runtime.security.migration_safety import ensure_schema_version
    conn = _open(db_path)
    try:
        ensure_schema_version(conn, db_path, sch.RETAIL_SCHEMA_VERSION,
                               sch._migrate_retail_schema,
                               backup_dir=os.path.join(sch.BASE_DIR, 'migration_backups'))
    finally:
        conn.close()

    assert _user_version(db_path) == sch.RETAIL_SCHEMA_VERSION
    assert _integrity_ok(db_path)
    conn = _open(db_path)
    try:
        cols_after = {r[1] for r in conn.execute('PRAGMA table_info(inventory_balances)').fetchall()}
        row = conn.execute(
            'SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?',
            (company_id, pid, branch_ids[0])).fetchone()
    finally:
        conn.close()
    assert 'quantity_reserved' not in cols_after
    assert row['quantity_on_hand'] == 10.0  # the real column survives untouched


# ── 3. Phase 3 survives: compute_drift and the UNIQUE constraint ────────────

def test_compute_drift_is_zero_after_v17_migration_on_a_realistic_shop_with_real_balances_and_movements():
    """The actual proof the phase's own spec asks for: migrate a database
    that has REAL balances and REAL movements (matched, non-drifted, exactly
    what a healthy shop's ledger looks like), through v17 (dropping
    `quantity_reserved` along the way), and require `compute_drift` --
    imported from the REAL core/retail/stock_reconciliation module, not a
    reimplementation -- to still report zero drift for every key."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        conn.execute('ALTER TABLE inventory_balances ADD COLUMN quantity_reserved REAL DEFAULT 0')
        company_id, branch_ids = _new_company(conn, branch_count=2)
        skus = []
        for i in range(4):
            pid = _product(conn, company_id, f'V17-DRIFT-{i}')
            branch_id = branch_ids[i % 2]
            opening = 100.0 + i * 10
            sold = 5.0 * (i + 1)
            _movement(conn, company_id, pid, branch_id, opening, movement_type='opening_stock')
            _movement(conn, company_id, pid, branch_id, -sold, movement_type='sale_out')
            _balance(conn, company_id, pid, branch_id, opening - sold)
            skus.append((pid, branch_id))
        conn.execute('PRAGMA user_version = 16')
        conn.commit()
    finally:
        conn.close()

    # Anti-vacuity: the fixture must actually be non-drifted BEFORE the
    # migration under test runs, or a drift check afterwards proves nothing.
    drift_before = _drift(db_path, company_id)
    assert drift_before == [], f'fixture must start non-drifted, got {drift_before}'

    from commercial_runtime.security.migration_safety import ensure_schema_version
    conn = _open(db_path)
    try:
        ensure_schema_version(conn, db_path, sch.RETAIL_SCHEMA_VERSION,
                               sch._migrate_retail_schema,
                               backup_dir=os.path.join(sch.BASE_DIR, 'migration_backups'))
    finally:
        conn.close()

    assert _user_version(db_path) == sch.RETAIL_SCHEMA_VERSION
    drift_after = _drift(db_path, company_id)
    assert drift_after == [], (
        f'compute_drift must still be zero after the v17 migration; got {drift_after}')


def test_unique_company_product_branch_constraint_survives_the_v17_migration():
    """`UNIQUE(company_id, product_id, branch_id)` is what keeps
    `inventory_balances` to one row per key -- `ALTER TABLE ... DROP COLUMN`
    must not have silently rebuilt the table without it (SQLite's own DROP
    COLUMN implementation refuses to drop a column that is itself PART OF a
    unique constraint, but `quantity_reserved` is not part of this one, so
    this is confirming the constraint on the OTHER columns survived a table
    definition change next to it -- not assuming it from the DDL text)."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        company_id, branch_ids = _new_company(conn)
        pid = _product(conn, company_id, 'V17-UNIQ-1')
        _balance(conn, company_id, pid, branch_ids[0], 5.0)
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                'INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) '
                'VALUES (?,?,?,?)',
                (company_id, pid, branch_ids[0], 999.0))
    finally:
        conn.rollback()
        conn.close()
