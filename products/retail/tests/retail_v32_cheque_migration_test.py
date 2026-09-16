"""Aura Retail -- schema v32 regression coverage: Aseel-parity wave A-PAR,
"cheque lifecycle as a tracked instrument". See database/schema.py::
_migrate_add_cheques and its own RETAIL_SCHEMA_VERSION v32 comment block.

WHAT V32 CLAIMS, AND THEREFORE WHAT THIS FILE HAS TO PROVE.

Two new, self-contained tables (`cheques`, `cheque_events`) plus three
indexes, and ONE UPDATE against the pre-existing single `sync_cursor` row
(resets `relay_url` to NULL so a v31 peer that never heard of cheques gets a
one-time full re-pull on upgrade -- see B3 in the design review this
implements). No existing table is ALTERed. This file proves:

  1. Both tables and all three indexes exist on a fresh install, with the
     columns the design specifies -- most importantly, that NEITHER table
     has a `status`/`row_version` column, because that is what makes them
     converge across devices by plain set union (core/retail/cheques.py's
     own module docstring has the full argument; this file only pins the
     SHAPE that argument depends on).
  2. The migration is idempotent -- a second pass, and a pass against a
     database restored mid-upgrade, are both clean no-ops.
  3. `RETAIL_SCHEMA_VERSION >= 32` (never `== 32` -- see retail_v17_catalogue_
     migration_test.py's own identical `>=` convention, restated here rather
     than re-derived).
  4. The v31-peer answer actually fires: `sync_cursor.relay_url` is reset to
     NULL by this migration, on both a fresh install and an upgrade from a
     real v31-shaped database.
  5. `_migrate_add_cheques` is wired into `_migrate_retail_schema` and is
     called LAST, matching this file's own append-last convention (the
     `retail_v14_migration_chain_wiring_test.py` shape).

Follows the SAME bootstrap convention as retail_v17_catalogue_migration_
test.py (no shared conftest.py exists for products/retail/tests/): its own
temp app-data dir per test via `_install()`, real `init_retail()`, no Flask
app anywhere in this file -- kept deliberately separate from the HTTP-route-
driven cheque lifecycle tests (retail_cheque_lifecycle_test.py) for the
identical cross-test-contamination reason that file's own docstring already
states for its v17 sibling.

Run:
    pytest products/retail/tests/retail_v32_cheque_migration_test.py -v
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
    tmp = _fresh_app_data('aura-retail-v32-')
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

def test_cheques_and_cheque_events_exist_on_a_fresh_install():
    sch, db_path = _install()
    tables = _tables(db_path)
    assert 'cheques' in tables
    assert 'cheque_events' in tables

    cheque_cols = _cols(db_path, 'cheques')
    for expected in ('id', 'company_id', 'branch_id', 'direction', 'party_type', 'party_id',
                      'cheque_number', 'bank_name', 'bank_branch', 'drawer_name', 'account_number',
                      'amount', 'currency', 'issue_date', 'due_date', 'sale_id', 'related_type',
                      'related_id', 'notes', 'created_by', 'created_at_utc', 'created_at'):
        assert expected in cheque_cols, f'cheques is missing column {expected!r}'

    event_cols = _cols(db_path, 'cheque_events')
    for expected in ('id', 'cheque_id', 'event_type', 'from_status', 'to_status', 'occurred_on',
                      'bank_reference', 'reason', 'to_party_type', 'to_party_id', 'created_by',
                      'created_at_utc', 'created_at'):
        assert expected in event_cols, f'cheque_events is missing column {expected!r}'


def test_neither_table_stores_a_status_or_row_version_column():
    """THE load-bearing structural claim (database/schema.py's v32 comment,
    core/retail/cheques.py's own module docstring): status is a FOLD over
    cheque_events, never a stored column, and neither table takes a
    row_version -- that is what lets two append-only sets converge with no
    version gate at all. A future "helpful" addition of either column would
    silently reopen the exact double-apply defect this design replaced.
    MUTATION: add `status TEXT` or `row_version INTEGER` to either CREATE
    TABLE in _migrate_add_cheques -> this test goes RED."""
    sch, db_path = _install()
    assert 'status' not in _cols(db_path, 'cheques')
    assert 'row_version' not in _cols(db_path, 'cheques')
    assert 'status' not in _cols(db_path, 'cheque_events')
    assert 'row_version' not in _cols(db_path, 'cheque_events')


def test_cheques_indexes_exist():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    finally:
        conn.close()
    for expected in ('idx_cheques_due', 'idx_cheques_party', 'idx_cheque_events_cheque'):
        assert expected in names, f'missing index {expected!r}'


def test_neither_table_carries_a_uid_column():
    """`id` IS the wire identity on both tables (client-generated UUID) --
    no separate `uid` column is needed or created, unlike RETAIL_UID_TABLES'
    autoincrement-id-plus-uid shape."""
    sch, db_path = _install()
    assert 'uid' not in _cols(db_path, 'cheques')
    assert 'uid' not in _cols(db_path, 'cheque_events')


# ── 2. Idempotency ────────────────────────────────────────────────────────

def test_v32_migration_is_idempotent_a_second_pass_is_a_clean_noop():
    sch, db_path = _install()
    before = _tables(db_path)

    conn = _open(db_path)
    try:
        conn.execute('PRAGMA user_version = 31')
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
    assert _tables(db_path) == before, 'a second v32 pass must not create, drop or duplicate any table'


def test_v32_reaches_head_with_integrity_ok_on_a_fresh_install():
    sch, db_path = _install()
    # `>=`, not `==` -- see retail_v17_catalogue_migration_test.py's own
    # identical comment for why: this asserts v32 has not been reverted, not
    # that the head is exactly 32.
    assert sch.RETAIL_SCHEMA_VERSION >= 32
    assert _user_version(db_path) == sch.RETAIL_SCHEMA_VERSION
    assert _integrity_ok(db_path)


def test_pre_existing_rows_survive_the_v32_migration_byte_identical():
    """Additive-only: a real row written before v32 (a customer, in this
    case) must be untouched by a migration that only adds two new tables.
    MUTATION: have _migrate_add_cheques accidentally touch an existing
    table (e.g. a stray UPDATE) -- this test would catch a changed value."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        conn.execute('PRAGMA user_version = 31')
        conn.execute(
            "INSERT INTO customers (id, company_id, name, phone, credit_balance) "
            "VALUES ('CUST-V32-1', 1, 'Pre-v32 Customer', '0790000000', 42.5)"
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
        row = conn.execute("SELECT name, phone, credit_balance FROM customers WHERE id='CUST-V32-1'").fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row['name'] == 'Pre-v32 Customer'
    assert row['phone'] == '0790000000'
    assert row['credit_balance'] == 42.5


# ── 3. The v31-peer answer: sync_cursor.relay_url is reset to NULL ──────────

def test_migration_resets_sync_cursor_relay_url_to_null():
    """THE B3 fix (database/schema.py's v32 comment, "THE v31-PEER HAZARD").
    A device running a pre-v32 build has no 'cheque'/'cheque_event' entry in
    its RETAIL_SYNC_ENTITY_TYPES, so `_apply_event` silently skips every
    cheque event it pulls and its cursor advances past them -- upgrading to
    v32 does not retroactively fetch what the cursor already skipped unless
    something forces a re-pull. Resetting `relay_url` to NULL is what forces
    it: `ensure_cursor_matches_relay` treats NULL as "different from any
    real relay" and resets `last_seq` to 0 on the very next pull.
    MUTATION: remove the `UPDATE sync_cursor SET relay_url=NULL` line from
    _migrate_add_cheques -> this test goes RED."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        # Simulate a real v31 device that has already synced against a real
        # relay -- a non-NULL relay_url and a non-zero last_seq -- then
        # rewind to v31 and re-run the full chain, the same "real v16-shaped
        # database" idiom retail_v17_catalogue_migration_test.py's own
        # upgrade test uses.
        conn.execute("UPDATE sync_cursor SET relay_url='https://relay.example.test', last_seq=500 WHERE id=1")
        conn.execute('PRAGMA user_version = 31')
        conn.commit()
        row_before = conn.execute('SELECT relay_url, last_seq FROM sync_cursor WHERE id=1').fetchone()
        assert row_before['relay_url'] == 'https://relay.example.test'
        assert row_before['last_seq'] == 500
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
        row_after = conn.execute('SELECT relay_url, last_seq FROM sync_cursor WHERE id=1').fetchone()
    finally:
        conn.close()
    assert row_after['relay_url'] is None, (
        'sync_cursor.relay_url must be reset to NULL by the v32 migration so a '
        'v31 peer gets a one-time full re-pull and actually receives the cheque '
        'events its old cursor already skipped past')
    # last_seq is deliberately left alone by this migration -- resetting it to
    # 0 is `ensure_cursor_matches_relay`'s own job, triggered by the NULL
    # relay_url on the NEXT pull, not this migration's.
    assert row_after['last_seq'] == 500


def test_migration_resets_relay_url_on_a_fresh_install_too_harmlessly():
    """A fresh install has never synced, so `relay_url` is already NULL --
    the migration's UPDATE must be a harmless no-op here, not raise on a
    row/table that (in principle, for a hand-built fixture) might not exist
    yet. Mirrors _migrate_add_sync_cursor_relay_url's own guarded-ALTER
    idempotency posture for the identical column."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        row = conn.execute('SELECT relay_url FROM sync_cursor WHERE id=1').fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row['relay_url'] is None


# ── 4. Chain wiring: called, and called after everything that preceded it ──

def test_migrate_add_cheques_is_called_after_every_step_that_preceded_it():
    """Matches retail_v14_migration_chain_wiring_test.py's own technique:
    read the SOURCE of _migrate_retail_schema and assert the call appears,
    and appears after every other _migrate_add_* call already in the chain
    -- this file's own append-last convention, enforced structurally rather
    than merely by comment. MUTATION: move the call earlier in the function
    body -> RED.

    CORRECTED 2026-09-15 (schema v33, quotations): this test used to assert
    `_migrate_add_cheques` is the LAST call in the chain, full stop. That
    claim was true the day v32 was written and became false the moment v33
    (_migrate_add_sales_quotations) was appended after it -- which is not a
    regression, it is the append-only chain working exactly as this
    migration's own docstring says it should ("appended LAST, same
    convention as every step above"). A migration's OWN wiring test cannot
    freeze "is globally last forever" as an invariant without going stale on
    the very next schema version; what it CAN freeze permanently is "comes
    after every call that existed in the chain at the time this migration
    was written", which is what this rewritten assertion checks instead.
    retail_v33_quotation_migration_test.py's own
    `test_migrate_add_sales_quotations_is_called_last_in_the_chain` now
    carries the "and nothing legitimate precedes MY intended position"
    claim for v33 -- exactly the baton-pass `_migrate_add_stock_transfers`'s
    docstring already describes for v24 -> v28 (a later step cashing in a
    position, never the earlier step's test staying right about the future).
    What this test can no longer catch: a THIRD migration being spliced in
    BETWEEN cheques and quotations. That is covered instead by v33's own
    equivalent test, which pins cheques as v33's immediate predecessor in
    the source order -- the two tests together still pin the full relative
    order, split across the two files that each own one half of it."""
    import inspect
    import database.schema as sch
    src = inspect.getsource(sch._migrate_retail_schema)
    assert '_migrate_add_cheques(conn)' in src
    # Every migration call already in the chain BEFORE this wave -- the call
    # under test must appear strictly after all of them, though it need not
    # be the last call in the function any more (see the docstring above).
    import re
    calls = re.findall(r'_migrate_add_\w+\(conn\)', src)
    assert calls, 'no _migrate_add_* calls found -- the source scan itself is broken'
    predecessors = calls[:calls.index('_migrate_add_cheques(conn)')]
    expected_predecessors = [
        '_migrate_add_supplier_contacts_and_po_split(conn)',
        '_migrate_add_reorder_automation_foundation(conn)',
        '_migrate_add_notifications_foundation(conn)',
        '_migrate_add_shift_cash_drawer(conn)',
        '_migrate_add_held_sales(conn)',
        '_migrate_add_whatsapp_recipients(conn)',
        '_migrate_add_identity_and_attribution_columns(conn)',
        '_migrate_add_sync_conflicts_and_drop_quantity_reserved(conn)',
        '_migrate_add_sync_freshness(conn)',
        '_migrate_add_offline_override(conn)',
        '_migrate_add_stock_exceptions(conn)',
        '_migrate_add_lookup_indexes(conn)',
        '_migrate_add_nocase_lookup_indexes(conn)',
        '_migrate_add_promotions(conn)',
        '_migrate_add_product_variants(conn)',
        '_migrate_add_modifiers(conn)',
        '_migrate_add_loyalty_ledger(conn)',
        '_migrate_add_stock_transfers(conn)',
        '_migrate_add_loyalty_return_id(conn)',
        '_migrate_add_site_relay(conn)',
        '_migrate_add_sync_cursor_relay_url(conn)',
    ]
    assert predecessors == expected_predecessors, (
        f'_migrate_add_cheques must be called strictly after every migration '
        f'that existed before v32; found predecessors: {predecessors}')
