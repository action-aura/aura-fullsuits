"""Aura Retail -- schema v37, `database.schema.
_migrate_requantize_sale_tender_payments`: the `payments` rows create_sale
wrote at 2dp before its own `_record_payment` call became currency-aware.

THE BUG. `_record_payment`'s `currency` argument is OPTIONAL and defaults to
the historical 2-decimal behaviour. create_sale's own retained-cash write was
the LAST caller left on that default (ffa221a5 converted it). On JOD -- three
decimals, 1000 fils -- a real tender of 4.007 persisted as 4.01. Three fils
CREATED in the ledger.

That column caps refunds: `create_return` sums it as `tender_collected` to
decide how much cash a return may physically hand back, and
`_cash_session_report` sums it for the drawer. An over-stated row lets a
refund pay out money that never entered the till.

WHY THIS BACKFILL IS EXACT, unlike v36's immediately before it. Only ONE
caller was ever affected, and its formula reads only columns still stored at
full precision on the sale beside the payment:

    net_received = min(amount_paid, total - points_redeemed_amount)

ffa221a5's commit message said "the fils a pre-fix row already discarded
cannot be recovered from the row itself". True of the ROW; not true of the
SALE beside it. This suite is the proof of that distinction -- every test
asserts a recomputed figure against what create_sale would have written.

WHAT IS ASSERTED, and deliberately so: the DENY half (JOD rows corrected,
points subtracted) AND the ALLOW half (USD untouched, direct payments
untouched, voided rows untouched, ambiguous sales skipped). A migration that
rewrites money needs the allow-half at least as much as the deny-half --
the rows it must NOT touch are the ones nobody would notice it had.

HOW THE FIXTURE WORKS. `_install()` runs the REAL, current init_retail()
v0->v37 chain, then each test rolls `PRAGMA user_version` back and writes a
payments row carrying the 2dp artifact -- i.e. it manufactures the historical
state rather than asserting against a hand-built schema that could drift from
the real one. Same idiom as retail_returns_backfill_test.py's own
`_rewind_to_pre_v36`.

Self-contained bootstrap; no shared conftest.py. CRITICAL: exactly ONE pytest
process per file (AUDIT-010).

Run:
    pytest products/retail/tests/retail_payment_requantize_migration_test.py -v
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


def _fresh_app_data(prefix):
    tmp = tempfile.mkdtemp(prefix=prefix)
    _TMP_DIRS.append(tmp)
    os.environ.update({
        'AURA_APP_DATA': tmp,
        'AURA_STANDALONE': '1',          # no demo seed rows polluting company_id=1
        'AURA_SITE_RELAY_ENABLED': '0',  # a licensed Windows box must not self-elect a LAN hub
    })
    return tmp


def _install():
    """A REAL install through init_retail() -- the full chain, no demo seed
    data. Same shape as retail_returns_backfill_test.py's own `_install()`."""
    tmp = _fresh_app_data('aura-retail-requantize-')
    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()
    return sch, os.path.join(sch.SUBSYS_DIR, 'retail.db')


def _open(db_path, foreign_keys=True):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=%s' % ('ON' if foreign_keys else 'OFF'))
    return conn


def _add_payments_credit_columns(conn):
    """`payments.related_type` / `related_id` / `status` are added at RUNTIME
    by api/retail_api.py's `_ensure_credit_schema`, NOT by schema.py's own
    migration chain -- the same already-established fact
    retail_returns_backfill_test.py documents for `payments.direction`.

    Added here with that function's exact DDL rather than by importing
    api.retail_api, so this suite tests schema.py alone. A realistic
    historical install has them, because `_record_payment` INSERTs
    `related_type` by name and therefore cannot have written a row before
    the column existed -- which is also why the migration itself returns
    early when the column is absent."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(payments)").fetchall()}
    for col, decl in (('related_type', 'TEXT'), ('related_id', 'INTEGER'), ('status', 'TEXT')):
        if col not in cols:
            conn.execute(f"ALTER TABLE payments ADD COLUMN {col} {decl}")
    conn.commit()


def _ensure_retail_settings_table(conn):
    """`retail_settings` is created lazily by api/retail_api.py's
    `_ensure_credit_schema`, not by schema.py's own chain -- the same
    already-established fact retail_returns_backfill_test.py documents. Built
    here with the shape retail_api.py uses, for the tests that need an
    explicit currency row."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS retail_settings ("
        "company_id INTEGER, skey TEXT, svalue TEXT, PRIMARY KEY (company_id, skey))"
    )
    conn.commit()


def _set_currency(conn, code, company_id=1):
    _ensure_retail_settings_table(conn)
    conn.execute(
        "INSERT INTO retail_settings (company_id, skey, svalue) VALUES (?, 'base_currency', ?) "
        "ON CONFLICT(company_id, skey) DO UPDATE SET svalue=excluded.svalue",
        (company_id, code))
    conn.commit()


def _insert_sale(conn, company_id=1, total=0.0, amount_paid=0.0, points_redeemed_amount=0.0):
    cur = conn.execute(
        "INSERT INTO sales (company_id, sale_number, total, amount_paid, "
        "points_redeemed_amount, payment_method, status) VALUES (?,?,?,?,?, 'cash', 'completed')",
        (company_id, f"S-{uuid.uuid4().hex[:10]}", total, amount_paid, points_redeemed_amount))
    return cur.lastrowid


def _insert_payment(conn, sale_id, amount, company_id=1, related_type='sale', status='active'):
    """`related_type` matters here in a way it does not in the v36 suite: it
    is the whole scope of this migration. Written explicitly on every row so
    no test can pass by accident of a default."""
    cur = conn.execute(
        "INSERT INTO payments (company_id, sale_id, method, amount, related_type, related_id, status) "
        "VALUES (?,?, 'cash', ?, ?, ?, ?)",
        (company_id, sale_id, amount, related_type, sale_id, status))
    return cur.lastrowid


def _amount(conn, payment_id):
    return conn.execute("SELECT amount FROM payments WHERE id=?", (payment_id,)).fetchone()['amount']


def _run_migration_directly(sch, conn):
    """Calls the migration FUNCTION directly. The last test in this file goes
    through the REAL `ensure_schema_version` chain instead, which is what
    proves the wiring rather than the arithmetic."""
    sch._migrate_requantize_sale_tender_payments(conn)
    conn.commit()


# ── the deny half: rows that must be corrected ────────────────────────────────

def test_a_jod_sale_tender_row_is_corrected_to_the_fils_the_sale_records():
    """THE DEFECT, exactly as `_record_payment`'s own docstring describes it:
    a JOD tender of 4.007 persisted as 4.01. The sale still carries 4.007 in
    `amount_paid`, so the three fils are recoverable from it.

    MEASURED: 4.01 before, 4.007 after."""
    sch, db = _install()
    conn = _open(db)
    try:
        _add_payments_credit_columns(conn)
        _set_currency(conn, 'JOD')
        sale_id = _insert_sale(conn, total=10.0, amount_paid=4.007)
        pay_id = _insert_payment(conn, sale_id, 4.01)   # the 2dp artifact
        conn.commit()

        assert _amount(conn, pay_id) == 4.01, "precondition: the row starts over-stated"
        _run_migration_directly(sch, conn)
        assert _amount(conn, pay_id) == 4.007, (
            f"the three fils the sale records must be restored, got {_amount(conn, pay_id)!r}")
    finally:
        conn.close()


def test_points_are_subtracted_so_a_points_sale_is_not_overstated():
    """`net_received = min(paid, total - points_redeemed_amount)`. A sale of
    10.000 with 3.000 redeemed in points and 9.000 tendered retained only
    7.000 -- the points already covered the rest, and counting the full 9.000
    as drawer cash is THE CASH TRAP create_sale's own comment names.

    The 2dp artifact here would be 7.0; the point of the test is that the
    migration reproduces the min() and the points term, not merely a
    requantization."""
    sch, db = _install()
    conn = _open(db)
    try:
        _add_payments_credit_columns(conn)
        _set_currency(conn, 'JOD')
        sale_id = _insert_sale(conn, total=10.0, amount_paid=9.0, points_redeemed_amount=3.0)
        pay_id = _insert_payment(conn, sale_id, 9.0)   # as if the min() had never been applied
        conn.commit()

        _run_migration_directly(sch, conn)
        assert _amount(conn, pay_id) == 7.0, (
            f"points-covered value is not drawer cash, got {_amount(conn, pay_id)!r}")
    finally:
        conn.close()


def test_rerunning_the_migration_changes_nothing():
    """Idempotent BY CONSTRUCTION -- it writes only when the recomputed value
    differs from the stored one, so there is no "has this run" flag to get
    wrong. Re-running must be a complete no-op, including for the row it
    just corrected."""
    sch, db = _install()
    conn = _open(db)
    try:
        _add_payments_credit_columns(conn)
        _set_currency(conn, 'JOD')
        sale_id = _insert_sale(conn, total=10.0, amount_paid=4.007)
        pay_id = _insert_payment(conn, sale_id, 4.01)
        conn.commit()

        _run_migration_directly(sch, conn)
        first = _amount(conn, pay_id)
        _run_migration_directly(sch, conn)
        _run_migration_directly(sch, conn)
        assert _amount(conn, pay_id) == first == 4.007
    finally:
        conn.close()


# ── the allow half: rows that must NOT be touched ─────────────────────────────

def test_a_two_decimal_shop_is_left_exactly_as_it_was():
    """THE CONTROL that proves this corrects PRECISION rather than merely
    rewriting every row it can reach. On USD the stored 4.01 IS the correct
    quantization of a 4.01 tender, and the migration must leave it alone --
    including leaving it alone when the sale's own `amount_paid` carries
    sub-cent noise a 2-decimal shop can never owe."""
    sch, db = _install()
    conn = _open(db)
    try:
        _add_payments_credit_columns(conn)
        _set_currency(conn, 'USD')
        sale_id = _insert_sale(conn, total=10.0, amount_paid=4.007)
        pay_id = _insert_payment(conn, sale_id, 4.01)
        conn.commit()

        _run_migration_directly(sch, conn)
        assert _amount(conn, pay_id) == 4.01, (
            f"a 2-decimal shop must keep cents, got {_amount(conn, pay_id)!r}")
    finally:
        conn.close()


def test_a_direct_account_payment_is_never_touched():
    """A customer/supplier account payment (`related_type` NULL) records an
    amount that exists NOWHERE else. Even though none were ever written at
    the wrong precision, the migration must refuse to recompute one rather
    than depend on that staying true -- there is nothing to recompute it
    FROM, so any value it invented would be a guess about money.

    The row here is deliberately given an amount that DOES differ from what
    the sale formula would produce, so a migration that ignored
    `related_type` would visibly rewrite it."""
    sch, db = _install()
    conn = _open(db)
    try:
        _add_payments_credit_columns(conn)
        _set_currency(conn, 'JOD')
        sale_id = _insert_sale(conn, total=10.0, amount_paid=4.007)
        pay_id = _insert_payment(conn, sale_id, 4.01, related_type=None)
        conn.commit()

        _run_migration_directly(sch, conn)
        assert _amount(conn, pay_id) == 4.01, (
            f"a non-sale payment row must be untouched, got {_amount(conn, pay_id)!r}")
    finally:
        conn.close()


def test_a_voided_row_is_never_touched():
    """A voided row is a historical fact about what was voided. Rewriting its
    amount changes what the void meant."""
    sch, db = _install()
    conn = _open(db)
    try:
        _add_payments_credit_columns(conn)
        _set_currency(conn, 'JOD')
        sale_id = _insert_sale(conn, total=10.0, amount_paid=4.007)
        pay_id = _insert_payment(conn, sale_id, 4.01, status='voided')
        conn.commit()

        _run_migration_directly(sch, conn)
        assert _amount(conn, pay_id) == 4.01, (
            f"a voided row must be untouched, got {_amount(conn, pay_id)!r}")
    finally:
        conn.close()


def test_a_sale_carrying_two_tender_rows_is_skipped_rather_than_guessed_at():
    """create_sale writes at most one, so this cannot arise today. If it ever
    does, the row-to-formula attribution is ambiguous -- the formula produces
    ONE figure and there would be two rows to spend it on -- and a guess
    about money is worse than an untouched row."""
    sch, db = _install()
    conn = _open(db)
    try:
        _add_payments_credit_columns(conn)
        _set_currency(conn, 'JOD')
        sale_id = _insert_sale(conn, total=10.0, amount_paid=4.007)
        first = _insert_payment(conn, sale_id, 4.01)
        second = _insert_payment(conn, sale_id, 1.23)
        conn.commit()

        _run_migration_directly(sch, conn)
        assert _amount(conn, first) == 4.01 and _amount(conn, second) == 1.23, (
            "an ambiguous sale must be skipped entirely, got "
            f"{_amount(conn, first)!r} / {_amount(conn, second)!r}")
    finally:
        conn.close()


def test_a_payment_whose_sale_is_absent_is_skipped():
    """On a synced till a peer's payment row can arrive before -- or without
    -- its sale. There is nothing to recompute from; the peer corrects its
    own copy when this migration runs there.

    Foreign keys are disabled for this one connection so the orphan can be
    manufactured at all; `payments.sale_id REFERENCES sales(id)` would
    otherwise refuse it."""
    sch, db = _install()
    conn = _open(db, foreign_keys=False)
    try:
        _add_payments_credit_columns(conn)
        _set_currency(conn, 'JOD')
        pay_id = _insert_payment(conn, 999_999, 4.01)   # no such sale
        conn.commit()

        _run_migration_directly(sch, conn)
        assert _amount(conn, pay_id) == 4.01, (
            f"an orphan payment must be left alone, got {_amount(conn, pay_id)!r}")
    finally:
        conn.close()


# ── the wiring, not the arithmetic ────────────────────────────────────────────

def test_the_real_chain_advances_the_version_and_keeps_integrity():
    """Everything above calls the migration function directly. This one goes
    through the REAL `ensure_schema_version`, which takes a live backup and
    runs `PRAGMA integrity_check` BEFORE and AFTER, and advances the version
    marker only on full success -- so it proves the step is actually WIRED
    into the chain and that the database survives it, neither of which a
    direct call can show.

    The rewind is `PRAGMA user_version = 36` with the rows left in place:
    this migration adds no columns, so there is nothing to drop to simulate
    a pre-v37 install -- the stamped version IS the whole difference."""
    sch, db = _install()
    conn = _open(db)
    try:
        _add_payments_credit_columns(conn)
        _set_currency(conn, 'JOD')
        sale_id = _insert_sale(conn, total=10.0, amount_paid=4.007)
        pay_id = _insert_payment(conn, sale_id, 4.01)
        conn.execute('PRAGMA user_version = 36')
        conn.commit()
    finally:
        conn.close()

    from commercial_runtime.security.migration_safety import ensure_schema_version
    backup_dir = tempfile.mkdtemp(prefix='aura-retail-requantize-backup-')
    _TMP_DIRS.append(backup_dir)
    conn = _open(db)
    try:
        ensure_schema_version(
            conn, db, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)
    finally:
        conn.close()

    conn = _open(db)
    try:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert conn.execute('PRAGMA user_version').fetchone()[0] == sch.RETAIL_SCHEMA_VERSION
        assert sch.RETAIL_SCHEMA_VERSION == 37, (
            f"this suite is written against v37; constant is {sch.RETAIL_SCHEMA_VERSION}")
        assert _amount(conn, pay_id) == 4.007, (
            f"the chain must have applied the correction, got {_amount(conn, pay_id)!r}")
    finally:
        conn.close()
