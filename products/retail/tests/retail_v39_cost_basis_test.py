"""Aura Retail -- schema v39 regression coverage: cost basis for Cost of
Goods Sold (ROADMAP.md's 2026-09-21 "retail schema v39 CLAIMED: COGS and
inventory valuation" entry, cashed in here). See
database/schema.py::_migrate_add_cost_basis and its own RETAIL_SCHEMA_
VERSION v39 comment block.

WHAT V39 CLAIMS, AND THEREFORE WHAT THIS FILE HAS TO PROVE.

Two new, additive-only columns, no existing table ALTERed beyond that:

    products.avg_cost    REAL DEFAULT 0  -- BACKFILLED from cost_price
    sale_items.unit_cost REAL DEFAULT 0  -- NEVER backfilled, ever

This file proves:

  1. `RETAIL_SCHEMA_VERSION >= 39` (never `== 39` -- see
     retail_gl_schema_test.py's own `>=` convention).
  2. The real chain runs end to end through `ensure_schema_version` --
     `PRAGMA integrity_check` reads 'ok' both before and after, and
     `PRAGMA user_version` lands exactly on the constant.
  3. Both columns exist, with the right declared SQL type and default.
  4. The migration is IDEMPOTENT: calling it twice never raises, never
     duplicates a column, and -- the sharper claim -- never re-derives
     `avg_cost` from a `cost_price` that moved on after the first call.
  5. AN UPGRADE backfills `products.avg_cost` from `products.cost_price`
     exactly.
  6. THE SAME UPGRADE leaves every pre-existing `sale_items.unit_cost` row
     at 0 -- asserted explicitly and positively, because it is a
     DELIBERATE decision that looks like an omission and a later reader
     will be tempted to "fix" it. See the migration's own docstring for
     why manufacturing that figure would be the exact false precision
     `_v15_seed_opening_counts`'s docstring (schema.py:3743-3745) already
     refuses in writing, for a different table.
  7. `_migrate_add_cost_basis` is wired into `_migrate_retail_schema` and
     runs after `_migrate_add_general_ledger`, matching this file's own
     append-last convention.
  8. An install where `products`/`sale_items` is missing, or where the
     columns are already present, does not raise.

Follows the SAME bootstrap convention as retail_gl_schema_test.py /
retail_payment_requantize_migration_test.py (no shared conftest.py exists
for products/retail/tests/): its own temp app-data dir per test via
`_install()`, real `init_retail()`, no Flask app anywhere in this file.

CRITICAL: exactly ONE pytest process per file (AUDIT-010) -- these test
files pollute each other in a shared pytest process.

Run:
    pytest products/retail/tests/retail_v39_cost_basis_test.py -q
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


def _fresh_app_data(prefix):
    tmp = tempfile.mkdtemp(prefix=prefix)
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp
    return tmp


def _install():
    """A REAL install: fresh temp AURA_APP_DATA, the genuine `init_retail()`
    boot path, the full v0 -> current migration chain. Deliberately not a
    hand-built minimal schema -- see retail_v17_catalogue_migration_test.py's
    identical helper for why."""
    tmp = _fresh_app_data('aura-retail-v39-')
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


def _tables(db_path):
    conn = _open(db_path)
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        conn.close()


def _cols(db_path, table):
    conn = _open(db_path)
    try:
        return {r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()}
    finally:
        conn.close()


def _col_info(db_path, table, col):
    """Returns (name, type, notnull, dflt_value, pk) for one column, or None."""
    conn = _open(db_path)
    try:
        for row in conn.execute(f'PRAGMA table_info({table})').fetchall():
            if row[1] == col:
                return row
        return None
    finally:
        conn.close()


def _rewind_to_pre_v39(db_path):
    """Simulates a genuine pre-v39 install against a REAL, already-migrated
    (v39) database: drops both new columns (SQLite 3.35+ supports `ALTER
    TABLE ... DROP COLUMN`; this repo's bundled sqlite3 is 3.50.4) and rolls
    `PRAGMA user_version` back to 38. Same idiom as
    retail_payment_requantize_migration_test.py's own rewind helper --
    manufacture the historical STATE rather than assert against a hand-built
    schema that could drift from the real one."""
    conn = _open(db_path)
    try:
        conn.executescript("""
            ALTER TABLE products DROP COLUMN avg_cost;
            ALTER TABLE sale_items DROP COLUMN unit_cost;
        """)
        conn.execute('PRAGMA user_version = 38')
        conn.commit()
    finally:
        conn.close()


# ── 1. Version ────────────────────────────────────────────────────────────

def test_retail_schema_version_is_at_least_39():
    import database.schema as sch
    assert sch.RETAIL_SCHEMA_VERSION >= 39


def test_fresh_install_user_version_matches_constant():
    import database.schema as sch
    _, db_path = _install()
    assert _user_version(db_path) == sch.RETAIL_SCHEMA_VERSION
    assert _integrity_ok(db_path)


# ── 2. Column shape ──────────────────────────────────────────────────────

def test_products_avg_cost_column_exists_with_right_type_and_default():
    sch, db_path = _install()
    cols = _cols(db_path, 'products')
    assert 'avg_cost' in cols
    info = _col_info(db_path, 'products', 'avg_cost')
    assert info is not None
    # PRAGMA table_info row shape: (cid, name, type, notnull, dflt_value, pk)
    assert info[2].upper() == 'REAL'
    assert info[4] == '0'


def test_sale_items_unit_cost_column_exists_with_right_type_and_default():
    sch, db_path = _install()
    cols = _cols(db_path, 'sale_items')
    assert 'unit_cost' in cols
    info = _col_info(db_path, 'sale_items', 'unit_cost')
    assert info is not None
    assert info[2].upper() == 'REAL'
    assert info[4] == '0'


# ── 3. Idempotency -- the sharper claim ──────────────────────────────────

def test_migration_is_idempotent_on_replay_via_full_chain():
    sch, db_path = _install()
    before_tables = _tables(db_path)
    before_products_cols = _cols(db_path, 'products')
    before_sale_items_cols = _cols(db_path, 'sale_items')
    conn = _open(db_path)
    try:
        sch._migrate_add_cost_basis(conn)
        conn.commit()
    finally:
        conn.close()
    assert _tables(db_path) == before_tables
    assert _cols(db_path, 'products') == before_products_cols
    assert _cols(db_path, 'sale_items') == before_sale_items_cols
    assert _integrity_ok(db_path)


def test_second_call_never_rederives_avg_cost_from_a_moved_cost_price():
    """The idempotency guard's whole point, proved directly rather than only
    by column-set equality: after the FIRST call backfills `avg_cost` from
    `cost_price`, `cost_price` is changed at runtime (exactly what the real
    app does -- a manual price edit) and the migration is called a SECOND
    time. `avg_cost` must stay at its first-call value, not silently track
    the new `cost_price` -- a real re-derivation bug would pass the simpler
    "no duplicate column" idempotency check above while still corrupting
    every historical `avg_cost` on any database this function is ever
    replayed against (a restored mid-upgrade backup, per this migration's
    own docstring)."""
    tmp = _fresh_app_data('aura-retail-v39-rederive-')
    import database.schema as sch
    db_path = os.path.join(tmp, 'retail_rederive.db')
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER DEFAULT 1,
            cost_price REAL DEFAULT 0
        );
    """)
    conn.execute("INSERT INTO products (id, company_id, cost_price) VALUES (1, 1, 7.500)")
    conn.commit()

    sch._migrate_add_cost_basis(conn)
    conn.commit()
    first = conn.execute("SELECT avg_cost FROM products WHERE id=1").fetchone()[0]
    assert first == 7.5

    # Runtime moves cost_price on, exactly like a manual price edit would.
    conn.execute("UPDATE products SET cost_price=99.0 WHERE id=1")
    conn.commit()

    sch._migrate_add_cost_basis(conn)
    conn.commit()
    second = conn.execute("SELECT avg_cost FROM products WHERE id=1").fetchone()[0]
    assert second == 7.5, (
        f"avg_cost must NOT be re-derived on a replay; expected 7.5 (first-call "
        f"value), got {second} (which would mean it tracked the moved cost_price)"
    )
    conn.close()


# ── 4. The real chain, end to end ────────────────────────────────────────

def test_the_real_chain_runs_end_to_end_with_integrity_checks():
    """Goes through the REAL `ensure_schema_version`, which takes a live
    backup and runs `PRAGMA integrity_check` BEFORE and AFTER, and advances
    the version marker only on full success -- proves the step is actually
    WIRED into the chain and that the database survives it, neither of
    which a direct call to `_migrate_add_cost_basis` can show."""
    sch, db_path = _install()
    assert _integrity_ok(db_path), "must be 'ok' BEFORE the rewound chain runs"
    _rewind_to_pre_v39(db_path)
    assert _integrity_ok(db_path), "dropping the v39 columns must not itself corrupt the db"

    from commercial_runtime.security.migration_safety import ensure_schema_version
    backup_dir = tempfile.mkdtemp(prefix='aura-retail-v39-backup-')
    _TMP_DIRS.append(backup_dir)
    conn = _open(db_path)
    try:
        ensure_schema_version(
            conn, db_path, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)
    finally:
        conn.close()

    assert _integrity_ok(db_path), "must be 'ok' AFTER the real chain runs"
    assert _user_version(db_path) == sch.RETAIL_SCHEMA_VERSION
    assert 'avg_cost' in _cols(db_path, 'products')
    assert 'unit_cost' in _cols(db_path, 'sale_items')


# ── 5. Upgrade backfill: products.avg_cost DOES get backfilled ──────────

def test_upgrade_backfills_avg_cost_from_cost_price():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        # products.id is TEXT (client-generated UUID) since v4's
        # _migrate_products_to_uuid, NOT an autoincrement int -- a real
        # install must never be assumed to have integer product ids. Pull
        # two real seeded ids rather than hand-picking a literal.
        pids = [r[0] for r in conn.execute(
            "SELECT id FROM products ORDER BY rowid LIMIT 2").fetchall()]
        assert len(pids) == 2, "the seed must have produced at least 2 products"
        conn.execute("UPDATE products SET cost_price=7.500 WHERE id=?", (pids[0],))
        conn.execute("UPDATE products SET cost_price=12.250 WHERE id=?", (pids[1],))
        conn.commit()
    finally:
        conn.close()

    _rewind_to_pre_v39(db_path)

    from commercial_runtime.security.migration_safety import ensure_schema_version
    backup_dir = tempfile.mkdtemp(prefix='aura-retail-v39-backfill-backup-')
    _TMP_DIRS.append(backup_dir)
    conn = _open(db_path)
    try:
        ensure_schema_version(
            conn, db_path, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)
    finally:
        conn.close()

    conn = _open(db_path)
    try:
        rows = conn.execute(
            "SELECT id, cost_price, avg_cost FROM products WHERE id IN (?, ?)",
            (pids[0], pids[1]),
        ).fetchall()
        assert len(rows) == 2
        for row in rows:
            assert row['avg_cost'] == row['cost_price'], (
                f"product {row['id']}: avg_cost {row['avg_cost']} must equal "
                f"cost_price {row['cost_price']} straight after the v39 backfill"
            )
    finally:
        conn.close()


# ── 6. Upgrade backfill: sale_items.unit_cost is DELIBERATELY untouched ──

def test_upgrade_leaves_historical_sale_items_unit_cost_at_zero():
    """The deliberate half of the same claim. A pre-v39 `sale_items` row,
    for a product whose `cost_price` is real and nonzero, must NOT have its
    `unit_cost` backfilled -- it must land, and stay, at exactly 0. This is
    the assertion a later reader will be tempted to "fix"; it is asserted
    here explicitly and positively so that temptation is on record as
    wrong."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        # products.id is TEXT (UUID) on a real install -- see the identical
        # note in test_upgrade_backfills_avg_cost_from_cost_price above.
        pid = conn.execute("SELECT id FROM products ORDER BY rowid LIMIT 1").fetchone()[0]
        conn.execute("UPDATE products SET cost_price=15.750 WHERE id=?", (pid,))
        sale_id = conn.execute(
            "INSERT INTO sales (company_id, sale_number, total) "
            "VALUES (1, 'V39-TEST-001', 31.50)"
        ).lastrowid
        item_id = conn.execute(
            "INSERT INTO sale_items (sale_id, product_id, quantity, unit_price, line_total) "
            "VALUES (?, ?, 2, 15.75, 31.50)",
            (sale_id, pid),
        ).lastrowid
        conn.commit()
    finally:
        conn.close()

    _rewind_to_pre_v39(db_path)

    from commercial_runtime.security.migration_safety import ensure_schema_version
    backup_dir = tempfile.mkdtemp(prefix='aura-retail-v39-nobackfill-backup-')
    _TMP_DIRS.append(backup_dir)
    conn = _open(db_path)
    try:
        ensure_schema_version(
            conn, db_path, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)
    finally:
        conn.close()

    conn = _open(db_path)
    try:
        row = conn.execute(
            "SELECT unit_cost FROM sale_items WHERE id=?", (item_id,)
        ).fetchone()
        assert row is not None
        assert row['unit_cost'] == 0, (
            f"historical sale_items.unit_cost must stay at 0 (no snapshot was "
            f"taken), got {row['unit_cost']!r} -- this would mean the migration "
            f"manufactured a cost for a sale that happened before v39 existed"
        )
    finally:
        conn.close()


# ── 7. Wiring ─────────────────────────────────────────────────────────────

def test_migrate_add_cost_basis_is_wired_into_the_chain():
    """Structural proof, not merely a behavioural one: the docstring of
    `_migrate_retail_schema` promises every step is chained in version
    order and appended last unless documented otherwise. Reads this file's
    own source -- the same technique retail_gl_schema_test.py already uses
    for the identical wiring proof on v38 -- rather than depending only on
    the behavioural proof above, which could pass by coincidence if some
    OTHER step happened to create the same columns."""
    import inspect
    import database.schema as sch
    src = inspect.getsource(sch._migrate_retail_schema)
    assert '_migrate_add_cost_basis(conn)' in src
    idx_cost_basis = src.index('_migrate_add_cost_basis(conn)')
    idx_gl = src.index('_migrate_add_general_ledger(conn)')
    assert idx_cost_basis > idx_gl, (
        "_migrate_add_cost_basis must run AFTER _migrate_add_general_ledger "
        "in the chain"
    )


# ── 8. Missing tables / already-present columns do not raise ────────────

def test_missing_products_and_sale_items_tables_does_not_raise():
    tmp = _fresh_app_data('aura-retail-v39-minimal-')
    import database.schema as sch
    db_path = os.path.join(tmp, 'retail_minimal.db')
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE customers (id INTEGER PRIMARY KEY, company_id INTEGER, name TEXT);
    """)
    conn.commit()
    # Must not raise.
    sch._migrate_add_cost_basis(conn)
    conn.commit()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert 'products' not in tables
    assert 'sale_items' not in tables
    conn.close()


def test_products_without_sale_items_does_not_raise():
    """A fixture that has `products` but not `sale_items` (or vice versa)
    must migrate the one that exists and skip the other cleanly -- the two
    columns are independently guarded, not an all-or-nothing pair."""
    tmp = _fresh_app_data('aura-retail-v39-products-only-')
    import database.schema as sch
    db_path = os.path.join(tmp, 'retail_products_only.db')
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER DEFAULT 1,
            cost_price REAL DEFAULT 0
        );
    """)
    conn.execute("INSERT INTO products (id, company_id, cost_price) VALUES (1, 1, 3.0)")
    conn.commit()
    sch._migrate_add_cost_basis(conn)
    conn.commit()
    cols = {r[1] for r in conn.execute('PRAGMA table_info(products)').fetchall()}
    assert 'avg_cost' in cols
    val = conn.execute("SELECT avg_cost FROM products WHERE id=1").fetchone()[0]
    assert val == 3.0
    conn.close()


def test_already_migrated_database_does_not_raise_on_replay():
    """An install already at v39 (columns present) must not raise when the
    migration function is called again directly -- the base case of the
    idempotency guard."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        # Must not raise.
        sch._migrate_add_cost_basis(conn)
        conn.commit()
    finally:
        conn.close()
    assert _integrity_ok(db_path)
