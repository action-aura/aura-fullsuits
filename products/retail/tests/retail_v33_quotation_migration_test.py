"""Aura Retail -- schema v33 regression coverage: Aseel-parity wave A-PAR,
"quotations and sales orders as real documents". See database/schema.py::
_migrate_add_sales_quotations and its own RETAIL_SCHEMA_VERSION v33 comment
block.

WHAT V33 CLAIMS, AND THEREFORE WHAT THIS FILE HAS TO PROVE.

Two new, self-contained tables (`sales_quotations`, `sales_quotation_lines`)
plus five indexes. No existing table is ALTERed, read, or written. This file
proves:

  1. Both tables exist on a fresh install, with the columns the design
     specifies -- most importantly, that NEITHER table has a `uid` column
     (the `id` itself IS the wire identity) and that `sales_quotations` has
     NO `deleted_at_utc` column (a commercial document is cancelled, never
     deleted -- see C4 in the reviewed design).
  2. `sales_quotations` DOES carry `row_version`/`updated_at_utc` -- unlike
     `cheques`, which deliberately carries neither -- because this table is
     a mutable header synced as create-then-update events, not an
     append-only fold.
  3. All five indexes exist, including the UNIQUE index on `doc_number`.
  4. The migration is idempotent -- a second pass, and a pass against a
     database restored mid-upgrade, are both clean no-ops.
  5. `RETAIL_SCHEMA_VERSION >= 33` (never `== 33` -- see
     retail_v17_catalogue_migration_test.py's own identical `>=`
     convention).
  6. `_migrate_add_sales_quotations` is wired into `_migrate_retail_schema`
     and is called LAST, matching this file's own append-last convention.
  7. Neither table is a member of RETAIL_UID_TABLES/RETAIL_ACTOR_TABLES/
     RETAIL_ROW_VERSION_TABLES -- those three drive the v13 retrofit onto
     tables that existed before v13, and adding a v33 table to any of them
     would fire for no database.
  8. Pre-existing rows survive the migration byte-identical (additive-only
     proof, matching the v32 cheque migration test's identical check).

Follows the SAME bootstrap convention as retail_v32_cheque_migration_test.py
(no shared conftest.py exists for products/retail/tests/): its own temp
app-data dir per test via `_install()`, real `init_retail()`, no Flask app
anywhere in this file -- kept deliberately separate from the HTTP-route-
driven quotation lifecycle tests (retail_quotation_test.py) for the identical
cross-test-contamination reason that file's own docstring states.

Run:
    pytest products/retail/tests/retail_v33_quotation_migration_test.py -v
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
    tmp = _fresh_app_data('aura-retail-v33-')
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


# ── 1. Both tables exist, with the shape the design specifies ───────────────

def test_sales_quotations_and_lines_exist_on_a_fresh_install():
    sch, db_path = _install()
    tables = _tables(db_path)
    assert 'sales_quotations' in tables
    assert 'sales_quotation_lines' in tables

    header_cols = _cols(db_path, 'sales_quotations')
    for expected in ('id', 'company_id', 'branch_id', 'customer_id', 'doc_number', 'doc_kind',
                      'status', 'valid_until', 'currency', 'tax_mode', 'subtotal',
                      'discount_amount', 'tax_amount', 'total', 'notes', 'created_by',
                      'created_at', 'sent_at', 'accepted_at', 'declined_at', 'cancelled_at',
                      'converted_at', 'converted_sale_uid', 'conversion_variance_json',
                      'row_version', 'updated_at_utc'):
        assert expected in header_cols, f'sales_quotations is missing column {expected!r}'

    line_cols = _cols(db_path, 'sales_quotation_lines')
    for expected in ('id', 'quotation_id', 'product_id', 'product_name_snapshot', 'quantity',
                      'unit_price', 'discount_pct', 'tax_rate', 'line_total',
                      'promotion_name_snapshot', 'line_no'):
        assert expected in line_cols, f'sales_quotation_lines is missing column {expected!r}'


def test_sales_quotations_has_no_deleted_at_utc_column():
    """THE C4 fix (database/schema.py's v33 comment): a commercial document
    is CANCELLED, never deleted -- `status='cancelled'` is the withdrawal
    channel and it is already filtered by every query that matters. A
    soft-delete column nothing filters is how a cancelled-but-still-counted
    row keeps contributing to committed demand.
    MUTATION: add `deleted_at_utc TEXT` to the sales_quotations CREATE TABLE
    -> this test goes RED."""
    sch, db_path = _install()
    assert 'deleted_at_utc' not in _cols(db_path, 'sales_quotations')
    assert 'deleted_at_utc' not in _cols(db_path, 'sales_quotation_lines')


def test_neither_table_carries_a_uid_column():
    """`id` IS the wire identity on both tables (client-generated UUID) --
    no separate `uid` column is needed or created, unlike RETAIL_UID_TABLES'
    autoincrement-id-plus-uid shape."""
    sch, db_path = _install()
    assert 'uid' not in _cols(db_path, 'sales_quotations')
    assert 'uid' not in _cols(db_path, 'sales_quotation_lines')


def test_sales_quotations_carries_row_version_and_updated_at_utc():
    """UNLIKE `cheques` (append-only, no version gate needed),
    `sales_quotations` is a MUTABLE header synced as create-then-update
    events, so it needs the reject-stale gate `reorder_requests` already
    has. MUTATION: drop `row_version`/`updated_at_utc` from the CREATE TABLE
    -> this test goes RED, and (per v17's own lesson) every writer's bump
    would then be silently meaningless."""
    sch, db_path = _install()
    cols = _cols(db_path, 'sales_quotations')
    assert 'row_version' in cols
    assert 'updated_at_utc' in cols


def test_sales_quotations_indexes_exist():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    finally:
        conn.close()
    for expected in ('idx_sales_quotations_company_status', 'idx_sales_quotations_customer',
                      'idx_sales_quotations_doc_number', 'idx_sales_quotations_open_commit',
                      'idx_sales_quotation_lines_quotation'):
        assert expected in names, f'missing index {expected!r}'


def test_doc_number_index_is_unique():
    """The bare UNIQUE index that AUDIT-032B's company+device fragments make
    practically uncollidable but must still exist as a real constraint --
    mirrors sales.sale_number/purchase_orders.po_number's own bare UNIQUE.
    MUTATION: create the doc_number index as a plain (non-UNIQUE) index ->
    this test goes RED."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_sales_quotations_doc_number'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert 'UNIQUE' in row['sql'].upper()


# ── 2. Not retrofitted onto the v13 tuples ───────────────────────────────────

def test_new_tables_are_not_in_any_v13_retrofit_tuple():
    """Adding a v33 table to RETAIL_UID_TABLES/RETAIL_ACTOR_TABLES/
    RETAIL_ROW_VERSION_TABLES "would fire for no database" -- the identical
    reasoning _migrate_add_loyalty_ledger/_migrate_add_stock_transfers/
    _migrate_add_cheques already established for the identical choice.
    MUTATION: add 'sales_quotations' to any of the three tuples -> RED."""
    import database.schema as sch
    assert 'sales_quotations' not in sch.RETAIL_UID_TABLES
    assert 'sales_quotations' not in sch.RETAIL_ACTOR_TABLES
    assert 'sales_quotations' not in sch.RETAIL_ROW_VERSION_TABLES
    assert 'sales_quotation_lines' not in sch.RETAIL_UID_TABLES
    assert 'sales_quotation_lines' not in sch.RETAIL_ACTOR_TABLES
    assert 'sales_quotation_lines' not in sch.RETAIL_ROW_VERSION_TABLES


# ── 3. Idempotency ────────────────────────────────────────────────────────

def test_v33_migration_is_idempotent_a_second_pass_is_a_clean_noop():
    sch, db_path = _install()
    before = _tables(db_path)

    conn = _open(db_path)
    try:
        conn.execute('PRAGMA user_version = 32')
        conn.commit()
    finally:
        conn.close()

    conn = _open(db_path)
    try:
        sch._migrate_retail_schema(conn)  # must not raise on a second pass
        conn.commit()
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
    assert _tables(db_path) == before, 'a second v33 pass must not create, drop or duplicate any table'


def test_v33_reaches_head_with_integrity_ok_on_a_fresh_install():
    sch, db_path = _install()
    # `>=`, not `==` -- see retail_v17_catalogue_migration_test.py's own
    # identical comment for why: this asserts v33 has not been reverted, not
    # that the head is exactly 33.
    assert sch.RETAIL_SCHEMA_VERSION >= 33
    assert _user_version(db_path) == sch.RETAIL_SCHEMA_VERSION
    assert _integrity_ok(db_path)


def test_pre_existing_rows_survive_the_v33_migration_byte_identical():
    """Additive-only: a real row written before v33 (a customer, in this
    case) must be untouched by a migration that only adds two new tables.
    MUTATION: have _migrate_add_sales_quotations accidentally touch an
    existing table (e.g. a stray UPDATE) -- this test would catch a changed
    value."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        conn.execute('PRAGMA user_version = 32')
        conn.execute(
            "INSERT INTO customers (id, company_id, name, phone, credit_balance) "
            "VALUES ('CUST-V33-1', 1, 'Pre-v33 Customer', '0790000001', 17.25)"
        )
        conn.commit()
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

    conn = _open(db_path)
    try:
        row = conn.execute("SELECT name, phone, credit_balance FROM customers WHERE id='CUST-V33-1'").fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row['name'] == 'Pre-v33 Customer'
    assert row['phone'] == '0790000001'
    assert row['credit_balance'] == 17.25


# ── 4. Foreign keys behave as the design specifies ───────────────────────────

def test_quotation_line_fk_to_products_is_enforced():
    """`sales_quotation_lines.product_id` DOES carry a declared FOREIGN KEY
    (both sides are client-generated UUIDs meaning the same thing on every
    device) -- this is what makes the missing-parent quarantine on the sync
    apply side load-bearing rather than decorative.
    MUTATION: drop the FK from the CREATE TABLE -> this insert would no
    longer raise, and this test goes RED."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        conn.execute(
            "INSERT INTO sales_quotations (id, company_id, branch_id, doc_number) "
            "VALUES ('Q-FK-1', 1, 1, 'QUO-FK-TEST-1')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sales_quotation_lines "
                "(id, quotation_id, product_id, product_name_snapshot, quantity, unit_price, line_total, line_no) "
                "VALUES ('L-FK-1', 'Q-FK-1', 'NO-SUCH-PRODUCT', 'Ghost', 1, 1, 1, 1)"
            )
            conn.commit()
    finally:
        conn.rollback()
        conn.close()


# ── 5. Chain wiring: called, and called LAST ────────────────────────────────

def test_migrate_add_sales_quotations_is_appended_after_its_predecessor():
    """Matches retail_v14_migration_chain_wiring_test.py's own technique:
    read the SOURCE of _migrate_retail_schema and assert the call appears,
    and appears after the chain that already preceded it -- this file's own
    append-last convention, enforced structurally rather than merely by
    comment. MUTATION: move the call earlier in the function body -> RED."""
    import inspect
    import database.schema as sch
    src = inspect.getsource(sch._migrate_retail_schema)
    assert '_migrate_add_sales_quotations(conn)' in src
    import re
    calls = re.findall(r'_migrate_add_\w+\(conn\)', src)
    assert calls, 'no _migrate_add_* calls found -- the source scan itself is broken'
    # THIS USED TO ASSERT `calls[-1] == '_migrate_add_sales_quotations(conn)'`
    # -- that quotations is the LAST call in the chain. That form is
    # self-obsoleting: it holds only while quotations happens to be the newest
    # migration, so EVERY later migration turns it red even when the
    # append-last convention was followed exactly. It went red on
    # _migrate_add_doc_series (v34) and again on
    # _migrate_add_return_settlement_split; neither did anything wrong.
    #
    # It also contradicted this test's OWN docstring, which says "after every
    # other _migrate_add_* call ALREADY in the chain" -- i.e. the ones that
    # preceded it, which is the real convention. The assertion over-reached
    # past its own stated intent.
    #
    # Pinning against its immediate predecessor (_migrate_add_cheques, v32)
    # keeps the guard this test exists for: the documented MUTATION -- moving
    # the quotations call earlier in the function body -- still turns this
    # RED. It simply no longer fails for migrations that arrive later and
    # correctly append themselves.
    #
    # WHAT THIS CAN NO LONGER CATCH, plainly: a brand-new migration INSERTED
    # into the middle of the chain instead of appended. The old form caught
    # that only incidentally, and only until the next person bumped the
    # literal. Nothing here replaces that guard -- it needs a check over the
    # whole chain's declared order, which does not exist yet.
    assert '_migrate_add_cheques(conn)' in calls, (
        'the predecessor this ordering is pinned against has been renamed or '
        'removed -- re-pin this assertion rather than deleting it')
    assert (calls.index('_migrate_add_sales_quotations(conn)')
            > calls.index('_migrate_add_cheques(conn)')), (
        f'_migrate_add_sales_quotations must be appended AFTER '
        f'_migrate_add_cheques, not inserted earlier in the chain; '
        f'found order: {calls}')
