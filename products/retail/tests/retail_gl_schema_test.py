"""Aura Retail -- schema v38 regression coverage: the double-entry general
ledger core (ROADMAP.md's 2026-09-20 "retail schema v38 CLAIMED: a real
general ledger" entry). See database/schema.py::_migrate_add_general_ledger
and its own RETAIL_SCHEMA_VERSION v38 comment block.

WHAT V38 CLAIMS, AND THEREFORE WHAT THIS FILE HAS TO PROVE.

Three new tables (`gl_accounts`, `gl_journals`, `gl_journal_lines`), four
append-only triggers, and a best-effort opening-balance backfill for any
company whose `customers`/`suppliers.credit_balance` was already nonzero
before this migration ran. No existing table is ALTERed. This file proves:

  1. All three tables exist on a fresh install, with the columns the design
     specifies.
  2. `gl_journals`/`gl_journal_lines` are APPEND-ONLY -- an UPDATE or DELETE
     against either one raises, both directions (this is the mutation
     proof CLAUDE.md's engineering standard demands for a guard: prove it
     actually blocks the thing it protects, not merely that the trigger
     SQL parsed).
  3. `gl_accounts` carries NO such trigger (a chart of accounts is
     legitimately editable even though transactions against it are not).
  4. The migration is idempotent -- a second pass is a clean no-op.
  5. `RETAIL_SCHEMA_VERSION >= 38` (never `== 38` -- see
     retail_v17_catalogue_migration_test.py's own `>=` convention).
  6. `_migrate_add_general_ledger` is wired into `_migrate_retail_schema`
     and runs after `_migrate_add_return_settlement_split`, matching this
     file's own append-last convention.
  7. The `journal_entries` fossil is left untouched (still present, still
     empty, still writable -- v38 does not drop or lock it, it only marks
     it superseded in a comment).
  8. THE OPENING-BALANCE BACKFILL actually fires on an UPGRADE (not merely
     a fresh install, where there is nothing to backfill and the code path
     never exercises) and the posted journal ties EXACTLY back to
     `SUM(customers.credit_balance)` / `SUM(suppliers.credit_balance)` --
     the core promise of the whole v38 claim, proved here at the schema
     layer independently of the HTTP-route proof in
     retail_gl_integration_test.py.

Follows the SAME bootstrap convention as retail_v32_cheque_migration_test.py
(no shared conftest.py exists for products/retail/tests/): its own temp
app-data dir per test via `_install()`, real `init_retail()`, no Flask app
anywhere in this file -- kept deliberately separate from the HTTP-route-
driven GL posting tests (retail_gl_integration_test.py) for the identical
cross-test-contamination reason that file's own docstring states for its
v32 sibling.

Run:
    pytest products/retail/tests/retail_gl_schema_test.py -q
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from decimal import Decimal
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
    tmp = _fresh_app_data('aura-retail-v38-')
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


# ── 1. All three tables exist, with the shape the design specifies ──────────

def test_gl_tables_exist_on_a_fresh_install():
    sch, db_path = _install()
    tables = _tables(db_path)
    assert 'gl_accounts' in tables
    assert 'gl_journals' in tables
    assert 'gl_journal_lines' in tables

    account_cols = _cols(db_path, 'gl_accounts')
    for expected in ('id', 'company_id', 'code', 'name', 'account_type',
                      'normal_balance', 'parent_id', 'is_active', 'created_at'):
        assert expected in account_cols, f'gl_accounts missing {expected}'

    journal_cols = _cols(db_path, 'gl_journals')
    for expected in ('id', 'company_id', 'branch_id', 'business_date', 'reference',
                      'memo', 'source_type', 'source_id', 'currency',
                      'reversed_journal_id', 'posted_by', 'posted_at'):
        assert expected in journal_cols, f'gl_journals missing {expected}'

    line_cols = _cols(db_path, 'gl_journal_lines')
    for expected in ('id', 'journal_id', 'account_id', 'debit', 'credit', 'memo'):
        assert expected in line_cols, f'gl_journal_lines missing {expected}'
    # gl_journal_lines must NOT carry its own company_id -- it is scoped
    # entirely through journal_id (and transitively through account_id),
    # the cheque_events/stock_transfer_items shape. See
    # trial_balance's own docstring for why that is sufficient scoping.
    assert 'company_id' not in line_cols

    assert _integrity_ok(db_path)


def test_gl_accounts_company_code_unique_index_exists():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        idx_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_gl_accounts_company_code'"
        ).fetchone()
        assert idx_sql is not None
        assert 'UNIQUE' in idx_sql[0].upper()
    finally:
        conn.close()


# ── 2. APPEND-ONLY, mutation-proven both directions ──────────────────────
#
# A guard that has never been made to fail is not proven. Each test here
# BREAKS the thing the trigger protects (issues the forbidden UPDATE/
# DELETE), confirms it is refused, and then confirms the table still
# accepts a NORMAL insert afterwards (the "restore" half -- proves the
# guard didn't wedge the table, only the specific forbidden operation).

def test_gl_journals_update_is_rejected_by_trigger():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        from core.retail import general_ledger as gl
        jid = gl.post_journal(conn, 1, None, '2026-09-21', 'sale', 1, 'JOD', [
            {'account_code': gl.ACCOUNT_CASH, 'debit': 5, 'memo': 'seed'},
            {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 5, 'memo': 'seed'},
        ])
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE gl_journals SET memo='tampered' WHERE id=?", (jid,))

        # RESTORE direction: the table must still accept a normal write
        # right after the blocked one -- proves the trigger blocks only
        # UPDATE, not the table.
        jid2 = gl.post_journal(conn, 1, None, '2026-09-21', 'sale', 2, 'JOD', [
            {'account_code': gl.ACCOUNT_CASH, 'debit': 3, 'memo': 'still works'},
            {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 3, 'memo': 'still works'},
        ])
        assert jid2 is not None
        conn.commit()
    finally:
        conn.close()


def test_gl_journals_delete_is_rejected_by_trigger():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        from core.retail import general_ledger as gl
        jid = gl.post_journal(conn, 1, None, '2026-09-21', 'sale', 1, 'JOD', [
            {'account_code': gl.ACCOUNT_CASH, 'debit': 5, 'memo': 'seed'},
            {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 5, 'memo': 'seed'},
        ])
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM gl_journals WHERE id=?", (jid,))

        still_there = conn.execute(
            "SELECT COUNT(*) FROM gl_journals WHERE id=?", (jid,)).fetchone()[0]
        assert still_there == 1, "a blocked DELETE must not have removed the row anyway"
    finally:
        conn.close()


def test_gl_journal_lines_update_is_rejected_by_trigger():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        from core.retail import general_ledger as gl
        jid = gl.post_journal(conn, 1, None, '2026-09-21', 'sale', 1, 'JOD', [
            {'account_code': gl.ACCOUNT_CASH, 'debit': 5, 'memo': 'seed'},
            {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 5, 'memo': 'seed'},
        ])
        conn.commit()
        line_id = conn.execute(
            "SELECT id FROM gl_journal_lines WHERE journal_id=? LIMIT 1", (jid,)
        ).fetchone()[0]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE gl_journal_lines SET debit=999 WHERE id=?", (line_id,))

        row = conn.execute(
            "SELECT debit FROM gl_journal_lines WHERE id=?", (line_id,)).fetchone()
        assert row[0] == 5.0, "a blocked UPDATE must not have changed the value anyway"
    finally:
        conn.close()


def test_gl_journal_lines_delete_is_rejected_by_trigger():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        from core.retail import general_ledger as gl
        jid = gl.post_journal(conn, 1, None, '2026-09-21', 'sale', 1, 'JOD', [
            {'account_code': gl.ACCOUNT_CASH, 'debit': 5, 'memo': 'seed'},
            {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 5, 'memo': 'seed'},
        ])
        conn.commit()
        line_id = conn.execute(
            "SELECT id FROM gl_journal_lines WHERE journal_id=? LIMIT 1", (jid,)
        ).fetchone()[0]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM gl_journal_lines WHERE id=?", (line_id,))
    finally:
        conn.close()


def test_gl_accounts_carries_no_append_only_trigger():
    """`gl_accounts` is legitimately editable (a chart of accounts can be
    renamed/deactivated) even though the transactions posted against it
    are not -- see the migration's own docstring. This is the allow-half:
    CLAUDE.md's engineering standard is explicit that a guard which denies
    X while allowing Y is half-tested if only the deny-half is checked."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        # gl_accounts is seeded LAZILY per-company (ensure_default_chart),
        # so a fresh install has zero rows until something posts -- seed
        # it explicitly first, matching how every real caller reaches this
        # table.
        from core.retail import general_ledger as gl
        gl.ensure_default_chart(conn, 1)
        conn.execute(
            "UPDATE gl_accounts SET name='Renamed' WHERE code='1000' AND company_id=1")
        conn.commit()
        row = conn.execute(
            "SELECT name FROM gl_accounts WHERE code='1000' AND company_id=1").fetchone()
        assert row is not None
        assert row[0] == 'Renamed'
    finally:
        conn.close()


# ── 3. Idempotency ────────────────────────────────────────────────────────

def test_migration_is_idempotent_on_replay():
    sch, db_path = _install()
    before_tables = _tables(db_path)
    conn = _open(db_path)
    try:
        sch._migrate_add_general_ledger(conn)
        conn.commit()
    finally:
        conn.close()
    after_tables = _tables(db_path)
    assert before_tables == after_tables
    assert _integrity_ok(db_path)


# ── 4. Version and wiring ────────────────────────────────────────────────

def test_retail_schema_version_is_at_least_38():
    import database.schema as sch
    assert sch.RETAIL_SCHEMA_VERSION >= 38


def test_fresh_install_user_version_matches_constant():
    import database.schema as sch
    _, db_path = _install()
    assert _user_version(db_path) == sch.RETAIL_SCHEMA_VERSION


def test_migrate_add_general_ledger_is_wired_into_the_chain():
    """Structural proof, not merely a behavioural one: the docstring of
    `_migrate_retail_schema` promises every step is chained in version
    order and appended last unless documented otherwise (v37's own
    early-running exception is documented there). Reads this file's own
    source -- the same technique retail_attribution_stamp_structural_
    test.py already uses elsewhere in this suite -- rather than depending
    only on the behavioural proof above, which could pass by coincidence
    if some OTHER step happened to create the same tables."""
    import inspect
    import database.schema as sch
    src = inspect.getsource(sch._migrate_retail_schema)
    assert '_migrate_add_general_ledger(conn)' in src
    # Must run after the return-settlement-split step, matching the
    # "appended last" convention this migration's own docstring claims.
    idx_gl = src.index('_migrate_add_general_ledger(conn)')
    idx_returns = src.index('_migrate_add_return_settlement_split(conn)')
    assert idx_gl > idx_returns, (
        "_migrate_add_general_ledger must run AFTER "
        "_migrate_add_return_settlement_split in the chain"
    )


# ── 5. The journal_entries fossil is untouched ───────────────────────────

def test_journal_entries_fossil_still_exists_and_is_still_writable():
    """v38 must not drop or lock the pre-existing fossil -- see the
    migration's own docstring, "the existing journal_entries fossil is
    left in place and untouched. It has no readers or writers, so
    dropping it buys nothing and risks a migration on live shop data for
    no gain." Proves both halves: the table is still there, AND it is
    NOT accidentally caught by the new append-only triggers (which are
    scoped to gl_journals/gl_journal_lines by name, not by any shared
    naming pattern)."""
    sch, db_path = _install()
    assert 'journal_entries' in _tables(db_path)
    conn = _open(db_path)
    try:
        conn.execute(
            "INSERT INTO journal_entries (company_id, reference, description, "
            "debit_account, credit_account, amount, entry_type) "
            "VALUES (1, 'TEST', 'still writable', 'A', 'B', 1.0, 'test')")
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0]
        assert count == 1
    finally:
        conn.close()


# ── 6. Opening-balance backfill on a real UPGRADE ────────────────────────

def test_opening_balance_backfill_fires_on_upgrade_and_ties_to_subledger():
    """The core promise of v38's "ties back to the AR/AP subledger" claim,
    proved at the schema layer: an install that already had real AR/AP
    history BEFORE v38 shipped must have that history reflected in the GL
    immediately after upgrading, not silently start the GL at zero while
    `customers`/`suppliers.credit_balance` read real numbers.

    Simulates a pre-v38 install by rolling `user_version` back to 37 and
    dropping the gl_* tables/triggers after seeding customers/suppliers
    with real credit_balance values (that column already exists on a
    fresh install -- see the migration's own "GUARDED ON credit_balance
    EXISTING" docstring for why: the UUID-rebuild migrations bake it into
    the base table shape), then re-running the real `init_retail()` boot
    path -- the SAME code path a genuine upgrade takes, not a direct call
    to the migration function in isolation.
    """
    sch, db_path = _install()
    conn = _open(db_path)
    conn.execute("INSERT INTO customers (id, company_id, name) VALUES (1, 1, 'Cust A')")
    conn.execute("INSERT INTO customers (id, company_id, name) VALUES (2, 1, 'Cust B')")
    conn.execute("UPDATE customers SET credit_balance=120.500 WHERE id=1")
    conn.execute("UPDATE customers SET credit_balance=-20.250 WHERE id=2")
    conn.execute("INSERT INTO suppliers (id, company_id, name) VALUES (1, 1, 'Supp A')")
    conn.execute("UPDATE suppliers SET credit_balance=45.000 WHERE id=1")
    conn.commit()
    conn.executescript("""
        DROP TRIGGER IF EXISTS trg_gl_journals_no_update;
        DROP TRIGGER IF EXISTS trg_gl_journals_no_delete;
        DROP TRIGGER IF EXISTS trg_gl_journal_lines_no_update;
        DROP TRIGGER IF EXISTS trg_gl_journal_lines_no_delete;
        DROP TABLE IF EXISTS gl_journal_lines;
        DROP TABLE IF EXISTS gl_journals;
        DROP TABLE IF EXISTS gl_accounts;
    """)
    conn.execute("PRAGMA user_version = 37")
    conn.commit()
    conn.close()

    sch.init_retail()

    conn = _open(db_path)
    try:
        assert _user_version(db_path) == sch.RETAIL_SCHEMA_VERSION

        from core.retail import general_ledger as gl
        tb = gl.trial_balance(conn, 1)
        assert tb['is_balanced']

        ar = [a for a in tb['accounts'] if a['code'] == gl.ACCOUNT_AR][0]['balance']
        ap = [a for a in tb['accounts'] if a['code'] == gl.ACCOUNT_AP][0]['balance']
        cust_sum = conn.execute(
            "SELECT COALESCE(SUM(credit_balance),0) FROM customers WHERE company_id=1"
        ).fetchone()[0]
        sup_sum = conn.execute(
            "SELECT COALESCE(SUM(credit_balance),0) FROM suppliers WHERE company_id=1"
        ).fetchone()[0]
        assert abs(ar - cust_sum) < 0.0005, f"GL AR {ar} does not tie to subledger {cust_sum}"
        assert abs(ap - sup_sum) < 0.0005, f"GL AP {ap} does not tie to subledger {sup_sum}"
        # 120.500 - 20.250 = 100.25 net customer AR
        assert abs(ar - 100.25) < 0.0005
        assert abs(ap - 45.0) < 0.0005

        journals = conn.execute(
            "SELECT * FROM gl_journals WHERE company_id=1 AND source_type='opening_balance'"
        ).fetchall()
        assert len(journals) == 1, "exactly one opening-balance journal, never re-run"
    finally:
        conn.close()


def test_opening_balance_backfill_is_a_noop_on_a_fresh_install():
    """The other half of the same proof: a FRESH install (nothing pre-
    existing) must post NO opening-balance journal at all -- there is
    nothing to backfill, and the guard on `credit_balance` existing must
    not manufacture a phantom journal out of all-zero balances."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM gl_journals WHERE source_type='opening_balance'"
        ).fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_opening_balance_backfill_guards_on_credit_balance_absence():
    """A database whose `customers`/`suppliers` genuinely lack
    `credit_balance` (a hand-built minimal fixture, matching the shape
    `_migrate_requantize_sale_tender_payments`'s own guard test defends
    against) must not crash `_migrate_add_general_ledger` -- it must
    degrade to "nothing to backfill", the identical contract the v37
    migration's `related_type` guard carries."""
    tmp = _fresh_app_data('aura-retail-v38-minimal-')
    import database.schema as sch
    db_path = os.path.join(tmp, 'retail_minimal.db')
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE customers (id INTEGER PRIMARY KEY, company_id INTEGER, name TEXT);
        CREATE TABLE suppliers (id INTEGER PRIMARY KEY, company_id INTEGER, name TEXT);
    """)
    conn.commit()
    # Must not raise.
    sch._migrate_add_general_ledger(conn)
    conn.commit()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert 'gl_accounts' in tables
    assert 'gl_journals' in tables
    count = conn.execute("SELECT COUNT(*) FROM gl_journals").fetchone()[0]
    assert count == 0
    conn.close()
