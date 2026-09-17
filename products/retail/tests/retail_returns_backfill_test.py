"""
Aura Retail -- migration BACKFILL correctness for the return settlement
split (schema v36, `database.schema._migrate_add_return_settlement_split`).

This file proves TWO defects in the BACKFILL ITSELF -- NOT the runtime,
which api/retail_api.py's create_return already owns, is being edited by
another crew this session, and this file must never import or depend on:

DEFECT 1 -- the backfill computed `original_balance_due` as
`sale_total - tender_collected` (`payments`, net of change). The runtime it
exists to mirror (create_return) computes the SAME figure from
`sale_total - points_redeemed_amount - amount_paid` (`sales.amount_paid`,
the RAW tendered figure) instead, and spends its own multi-paragraph
comment on why deriving it from `tender_collected` is exactly a regression
this codebase already shipped and fixed once. The two are only PROVABLY
equal when `balance_due` is non-negative and no points were ever redeemed
against the sale -- so a historical row that redeemed loyalty points got a
DIFFERENT (wrong) split than the runtime would give an identical new sale.
See test_points_redeemed_does_not_backfill_phantom_store_credit below for
the measured before/after -- that is the real divergence proof; test
1 (the literal "overpaid walk-in" scenario) does NOT itself diverge
numerically, and says so in its own docstring rather than pretending it
does.

DEFECT 2 -- `returns_settlement.split_return_settlement` was called with
SIX positional args, omitting the SEVENTH (`currency`), so every historical
return of every currency backfilled at the function's own 2-decimal
`_FALLBACK_QUANTUM` -- a brand-new 2dp artifact on JOD's three decimal
places (fils). See test_jod_currency_keeps_all_three_decimals below for the
measured before/after.

WHY THIS FILE HAND-BUILDS A "REWOUND" DATABASE rather than a v1 schema from
scratch: `_install()` runs the REAL, current `init_retail()` -> v0..v36
chain (same helper shape as retail_doc_series_migration_test.py's own
`_install()`), then drops the three v36 columns and rolls `PRAGMA
user_version` back to 34 (v35 is a deliberate hole -- see the migration's
own docstring). `PRAGMA table_info` cannot tell this apart from a genuine
pre-v36 database that already has historical rows in it, and
test_ensure_schema_version_integrity_and_version_advance below re-runs the
REAL chain end to end through it, not just this one function in isolation.

`payments.direction` is added directly in this file's own setup (a plain
`ALTER TABLE`), NOT by importing or calling anything from
api/retail_api.py's `_ensure_credit_schema` -- that file is owned by
another crew mid-edit this session, and this suite must not depend on it.
In the real product that column is added lazily by that same function at
runtime; adding it here mirrors a realistic historical install (this
migration's own `tender_collected` query already requires it
unconditionally) without importing a file this suite must treat as
read-only. `customers.credit_balance` needs no such treatment -- schema
v4's `_migrate_customers_to_uuid` folds it into the customers table as a
native column during the rebuild that runs as part of the same v0->v36
chain `_install()` already exercises.

MUTATION PROOFS: the exact file/line/before/after for DEFECT 1 and DEFECT 2
are written up in this session's own report rather than encoded as test
code here -- both mutations are reversions of schema.py's OWN just-applied
fix, and running them from inside this suite would mean this suite
temporarily un-fixing the very file it verifies while other tooling may be
reading it. The BEFORE/AFTER values these tests assert were measured by
hand, once with schema.py's pre-fix backfill in place and once with the
fix applied, exactly as this task's own "PROOF REQUIRED" section asks --
see each test's docstring for its own measured numbers.

Self-contained bootstrap; no shared conftest.py. CRITICAL: exactly ONE
pytest process per file (AUDIT-010).

Run:
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \
        products/retail/tests/retail_returns_backfill_test.py -v
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]        # products/retail
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent                    # aura-fullsuits
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TMP_DIRS = []

RETURN_SETTLEMENT_COLUMNS = ('tender_refund_amount', 'ar_forgiven_amount', 'store_credit_amount')


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _fresh_app_data(prefix):
    tmp = tempfile.mkdtemp(prefix=prefix)
    _TMP_DIRS.append(tmp)
    os.environ.update({
        'AURA_APP_DATA': tmp,
        'AURA_STANDALONE': '1',          # no demo seed data polluting company_id=1 -- see schema.py _init_retail
        'AURA_SITE_RELAY_ENABLED': '0',  # a licensed Windows test box must not self-elect a LAN hub
    })
    return tmp


def _install():
    """A REAL install through init_retail() -- full v0->v36 chain, no demo
    seed data. Mirrors retail_doc_series_migration_test.py's own
    `_install()`."""
    tmp = _fresh_app_data('aura-retail-returns-backfill-')
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


def _add_payments_direction_column(conn):
    """`payments.direction` is added at runtime by api/retail_api.py's
    `_ensure_credit_schema`, NOT by this file's own migration chain (grep
    confirms it appears nowhere in schema.py except the backfill's own
    `tender_collected` query). Added directly here, matching that
    function's own DDL, rather than importing api.retail_api -- that file
    is owned by another crew and must stay untouched/undepended-on by this
    suite."""
    conn.execute("ALTER TABLE payments ADD COLUMN direction TEXT")
    conn.commit()


def _ensure_retail_settings_table(conn):
    """`retail_settings` is ALSO created lazily by api/retail_api.py's
    `_ensure_credit_schema`, not by schema.py's own chain (see
    `_migrate_add_return_settlement_split`'s own docstring and
    `_v16_business_date`'s "a retail_settings table that does not exist
    yet" note a few thousand lines above it for the same, already-
    established fact). Created here with the exact shape retail_api.py
    uses, for the one test that needs an explicit currency row."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS retail_settings ("
        "company_id INTEGER, skey TEXT, svalue TEXT, PRIMARY KEY (company_id, skey))"
    )
    conn.commit()


def _rewind_to_pre_v36(conn):
    """Drop the three v36 return-settlement columns and roll PRAGMA
    user_version back to 34 (v35 is a deliberate hole -- see the
    migration's own docstring), simulating a real pre-v36 install that
    already has historical `returns` rows. Every other table stays exactly
    as the real v0->v36 chain built it -- the same "roll the stamped
    version back without touching any [other] table" idiom
    retail_doc_series_migration_test.py's own test 4 already uses."""
    for col in RETURN_SETTLEMENT_COLUMNS:
        conn.execute(f'ALTER TABLE returns DROP COLUMN {col}')
    conn.execute('PRAGMA user_version = 34')
    conn.commit()


def _run_backfill_directly(sch, conn):
    """Calls the migration FUNCTION directly (not the full chain) -- the
    same shape this function's own docstring already documents test
    fixtures using ("a minimal test fixture ... may call `_migrate_retail_
    schema` directly"). test_ensure_schema_version_integrity_and_version_
    advance below is the one test that goes through the REAL
    ensure_schema_version chain instead."""
    sch._migrate_add_return_settlement_split(conn)
    conn.commit()


def _insert_customer(conn, company_id=1, name='Test Customer', credit_balance=0.0):
    """customers.id is TEXT PRIMARY KEY (client-generated UUID) since
    schema v4's `_migrate_customers_to_uuid`, which the full v0->v36 chain
    `_install()` runs -- NOT an autoincrement integer, so the caller must
    mint and pass its own id rather than reading `lastrowid`."""
    cid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO customers (id, company_id, name, credit_balance) VALUES (?,?,?,?)",
        (cid, company_id, name, credit_balance)
    )
    return cid


def _insert_sale(conn, company_id=1, customer_id=None, total=0.0, amount_paid=0.0,
                  points_redeemed_amount=0.0, payment_method='cash'):
    cur = conn.execute(
        "INSERT INTO sales (company_id, sale_number, customer_id, total, amount_paid, "
        "points_redeemed_amount, payment_method, status) VALUES (?,?,?,?,?,?,?, 'completed')",
        (company_id, f"S-{uuid.uuid4().hex[:10]}", customer_id, total, amount_paid,
         points_redeemed_amount, payment_method)
    )
    return cur.lastrowid


def _insert_payment(conn, sale_id, amount, company_id=1, method='cash', direction='in', status='active'):
    conn.execute(
        "INSERT INTO payments (company_id, sale_id, method, amount, direction, status) "
        "VALUES (?,?,?,?,?,?)",
        (company_id, sale_id, method, amount, direction, status)
    )


def _insert_return(conn, sale_id, refund_amount, refund_method='cash', company_id=1):
    cur = conn.execute(
        "INSERT INTO returns (company_id, return_number, sale_id, refund_method, refund_amount, status) "
        "VALUES (?,?,?,?,?, 'completed')",
        (company_id, f"R-{uuid.uuid4().hex[:10]}", sale_id, refund_method, refund_amount)
    )
    return cur.lastrowid


def _fetch_split(conn, return_id):
    row = conn.execute(
        "SELECT tender_refund_amount, ar_forgiven_amount, store_credit_amount FROM returns WHERE id=?",
        (return_id,)
    ).fetchone()
    return (row['tender_refund_amount'], row['ar_forgiven_amount'], row['store_credit_amount'])


# ── 1. Overpaid walk-in (task scenario 1) ──────────────────────────────────

def test_overpaid_walk_in_matches_runtime_formula():
    """Tender 50 on a 42 total, walk-in (no customer). `amount_paid`=50
    (raw tendered) but only 42 ever reached the drawer (`payments.amount`,
    net of the 8 change handed back) -- the exact shape create_return's own
    comment names as where `amount_paid` and `tender_collected` differ.

    MEASURED: at these magnitudes the two formulas do NOT actually diverge
    in the backfilled figures. `original_balance_due` floors to 0 under
    BOTH the old (`sale_total - tender_collected` = 42-42 = 0) and the new
    (`sale_total - points_redeemed - amount_paid` = 42-0-50 = -8 -> 0)
    formula, because create_return's own comment states this identity
    directly: "amount_paid and the net tender actually received are
    PROVABLY EQUAL whenever balance_due is non-negative ... i.e. exactly
    the overpaid-change case ... this whole calculation is moot". Measured
    by hand against BOTH the pre-fix and post-fix backfill while writing
    this test -- both produced (42.0, 0.0, 0.0). This test therefore proves
    the fix does not regress the correct, no-AR outcome for the ordinary
    overpaid case; it is NOT a before/after divergence proof -- see
    test_points_redeemed_does_not_backfill_phantom_store_credit below for
    that."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        _add_payments_direction_column(conn)
        sale_id = _insert_sale(conn, total=42.0, amount_paid=50.0, payment_method='cash')
        _insert_payment(conn, sale_id, amount=42.0)  # net received; the 8 change is excluded
        return_id = _insert_return(conn, sale_id, refund_amount=42.0, refund_method='cash')
        conn.commit()

        _rewind_to_pre_v36(conn)
        _run_backfill_directly(sch, conn)

        tender_refund, ar_forgiven, store_credit = _fetch_split(conn, return_id)
        assert tender_refund == pytest.approx(42.0)
        assert ar_forgiven == pytest.approx(0.0)
        assert store_credit == pytest.approx(0.0)
    finally:
        conn.close()


# ── 2. JOD (3-decimal) currency is honored, not the 2dp fallback ──────────

def test_jod_currency_keeps_all_three_decimals():
    """Task scenario 2 -- the DEFECT 2 divergence proof. An explicit
    `retail_settings.base_currency='JOD'` row, and a return whose
    settleable amount lands exactly on the 3rd decimal (10.005) --
    ROUND_HALF_UP at 2dp rounds this UP to 10.01 (a brand-new artifact on
    a currency that has no 2nd-decimal ambiguity at all), while 3dp leaves
    it exactly alone.

    MEASURED BEFORE (schema.py's pre-fix backfill, `currency` omitted from
    the `split_return_settlement` call -- `_FALLBACK_QUANTUM`=0.01):
        tender_refund_amount = 10.01   -- WRONG: rounds a real fils amount
    MEASURED AFTER (fixed backfill, currency='JOD' resolved from
    `retail_settings`, quantum=0.001):
        tender_refund_amount = 10.005  -- correct, exact
    (both measured by hand while writing this test)."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        _add_payments_direction_column(conn)
        _ensure_retail_settings_table(conn)
        conn.execute(
            "INSERT INTO retail_settings (company_id, skey, svalue) VALUES (1, 'base_currency', 'JOD')"
        )
        sale_id = _insert_sale(conn, total=10.005, amount_paid=10.005, payment_method='cash')
        _insert_payment(conn, sale_id, amount=10.005)
        return_id = _insert_return(conn, sale_id, refund_amount=10.005, refund_method='cash')
        conn.commit()

        _rewind_to_pre_v36(conn)
        _run_backfill_directly(sch, conn)

        tender_refund, ar_forgiven, store_credit = _fetch_split(conn, return_id)
        assert tender_refund == pytest.approx(10.005, abs=1e-9), \
            "must keep all three JOD decimals, not round to 10.01 (2dp fallback)"
        assert ar_forgiven == pytest.approx(0.0)
        assert store_credit == pytest.approx(0.0)
    finally:
        conn.close()


# ── 3. points_redeemed_amount > 0 -- no phantom store credit ──────────────

def test_points_redeemed_does_not_backfill_phantom_store_credit():
    """Task scenario 3 -- the headline DEFECT 1 divergence proof.
    total=100, points_redeemed_amount=20 (amount_due_after_points=80),
    amount_paid=50 (a real, genuine underpayment: balance_due = 80-50 = 30,
    matching what create_sale would have recorded as this sale's own AR).
    `payments` collects the raw 50 (uncapped -- paid < amount_due_after_
    points, so net_received=paid exactly, per create_sale's own
    `net_received = min(paid, amount_due_after_points)`).
    customer.credit_balance=30, matching that AR. Full return of the whole
    sale, refund_amount=100.

    MEASURED BEFORE (schema.py's pre-fix backfill -- `original_balance_due
    = sale_total - tender_collected` = 100-50 = 50, ignoring the 20 of
    points -- 20 too much AR headroom invented):
        tender_refund_amount = 50.0
        ar_forgiven_amount   = 30.0  (capped at current_balance=30, not at
                                       the wrong balance_due_remaining=50)
        store_credit_amount  = 20.0  -- PHANTOM: minted from nothing. The
                                       settleable cap (tender_available +
                                       balance_due_remaining = 50+50=100)
                                       let store_credit absorb the leftover
                                       the wrong balance_due_remaining
                                       created.
    MEASURED AFTER (fixed backfill -- `100 - 20 - 50` = 30, matching
    create_sale exactly):
        tender_refund_amount = 50.0
        ar_forgiven_amount   = 30.0
        store_credit_amount  = 0.0   -- correct: nothing left over once the
                                       real (points-aware) debt is forgiven.
    (both measured by hand while writing this test)."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        _add_payments_direction_column(conn)
        customer_id = _insert_customer(conn, credit_balance=30.0)
        sale_id = _insert_sale(conn, customer_id=customer_id, total=100.0, amount_paid=50.0,
                                points_redeemed_amount=20.0, payment_method='cash')
        _insert_payment(conn, sale_id, amount=50.0)
        return_id = _insert_return(conn, sale_id, refund_amount=100.0, refund_method='cash')
        conn.commit()

        _rewind_to_pre_v36(conn)
        _run_backfill_directly(sch, conn)

        tender_refund, ar_forgiven, store_credit = _fetch_split(conn, return_id)
        assert tender_refund == pytest.approx(50.0)
        assert ar_forgiven == pytest.approx(30.0)
        assert store_credit == pytest.approx(0.0), \
            "must not mint phantom store_credit by ignoring points_redeemed_amount"
    finally:
        conn.close()


# ── 4. Two successive partial returns -- running claim tracking ───────────

def test_successive_partial_returns_running_claim_tracking_prevents_overclaim():
    """Task scenario 4. Deliberately keeps points OUT of the fixture (the
    parallel runtime fix to `original_balance_due` also changes with
    points in play, and this test's whole point is to isolate the
    PRE-EXISTING `_running` prior-claims tracking this migration already
    had -- unchanged by this fix -- from either defect above.
    `balance_due_remaining` must be reduced by AR already forgiven by an
    earlier partial return of the SAME sale, not re-read from scratch each
    row.

    total=100, amount_paid=40 (a real AR of 60, matching create_sale:
    balance_due = 100-40 = 60). customer.credit_balance=60. Two partial
    returns of the SAME sale, inserted (and therefore processed, in
    `(sale_id, id)` order) in sequence: refund 50, then refund 70.

    Return A: tender_available=40 (tender_collected 40, no prior claim),
        balance_due_remaining=60 (no prior claim).
        tender_refund=min(50,40)=40. remainder=10.
        ar_forgiven=min(10,60,60)=10. settleable=min(50,40+60)=50.
        store_credit=max(0,50-40-10)=0.
    Return B: tender_available=40-40=0 (A already claimed all of it).
        balance_due_remaining=60-10=50 (A already forgave 10).
        WITHOUT the running subtraction, balance_due_remaining would stay
        60 and ar_forgiven would wrongly reach min(70,60,60)=60 -- a 10
        OVER-CLAIM of debt return A already forgave. WITH it:
        tender_refund=min(70,0)=0. remainder=70.
        ar_forgiven=min(70,50,60)=50 exactly (capped by the running
        balance_due_remaining, not by current_balance).
        settleable=min(70,0+50)=50. store_credit=max(0,50-0-50)=0.

    Both measured by hand while writing this test and confirmed equal
    before and after this fix (no points, no sub-quantum edge, no currency
    dependence at these round magnitudes) -- this is a regression guard for
    logic this fix does not touch, not a DEFECT 1/2 divergence proof."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        _add_payments_direction_column(conn)
        customer_id = _insert_customer(conn, credit_balance=60.0)
        sale_id = _insert_sale(conn, customer_id=customer_id, total=100.0, amount_paid=40.0,
                                payment_method='cash')
        _insert_payment(conn, sale_id, amount=40.0)
        return_a = _insert_return(conn, sale_id, refund_amount=50.0, refund_method='cash')
        return_b = _insert_return(conn, sale_id, refund_amount=70.0, refund_method='cash')
        conn.commit()

        _rewind_to_pre_v36(conn)
        _run_backfill_directly(sch, conn)

        a_tender, a_ar, a_credit = _fetch_split(conn, return_a)
        b_tender, b_ar, b_credit = _fetch_split(conn, return_b)

        assert a_tender == pytest.approx(40.0)
        assert a_ar == pytest.approx(10.0)
        assert a_credit == pytest.approx(0.0)

        assert b_tender == pytest.approx(0.0)
        assert b_ar == pytest.approx(50.0), \
            "must be capped at 50 (60 original AR minus 10 already forgiven by return A), not 60"
        assert b_credit == pytest.approx(0.0)
    finally:
        conn.close()


# ── 5. Idempotency -- a redundant second pass must not double-claim ───────

def test_rerunning_backfill_is_idempotent_no_double_claim():
    """Task's idempotency requirement. The pre-existing `added_any` guard
    (unchanged by this fix) already makes a second call a pure no-op --
    this proves it still holds with the fixed formula/currency resolution.
    Shaped on retail_doc_series_migration_test.py's own
    test_rerunning_migration_is_a_no_op."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        _add_payments_direction_column(conn)
        customer_id = _insert_customer(conn, credit_balance=30.0)
        sale_id = _insert_sale(conn, customer_id=customer_id, total=100.0, amount_paid=50.0,
                                points_redeemed_amount=20.0, payment_method='cash')
        _insert_payment(conn, sale_id, amount=50.0)
        return_id = _insert_return(conn, sale_id, refund_amount=100.0, refund_method='cash')
        conn.commit()

        _rewind_to_pre_v36(conn)
        _run_backfill_directly(sch, conn)
        first = _fetch_split(conn, return_id)

        _run_backfill_directly(sch, conn)  # second, unnecessary pass
        second = _fetch_split(conn, return_id)

        assert first == second, "a redundant second pass must not change the backfilled split"
        assert first == (pytest.approx(50.0), pytest.approx(30.0), pytest.approx(0.0))
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == 'ok'
    finally:
        conn.close()


# ── 6. The real ensure_schema_version chain, integrity checks included ────

def test_ensure_schema_version_integrity_and_version_advance():
    """Confirms `ensure_schema_version`'s own before/after `PRAGMA
    integrity_check` still pass through the REAL `_migrate_retail_schema`
    chain (not just this one function called in isolation), on a database
    carrying historical rows this fix must backfill correctly, and that
    `PRAGMA user_version` advances to RETAIL_SCHEMA_VERSION."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        _add_payments_direction_column(conn)
        customer_id = _insert_customer(conn, credit_balance=30.0)
        sale_id = _insert_sale(conn, customer_id=customer_id, total=100.0, amount_paid=50.0,
                                points_redeemed_amount=20.0, payment_method='cash')
        _insert_payment(conn, sale_id, amount=50.0)
        return_id = _insert_return(conn, sale_id, refund_amount=100.0, refund_method='cash')
        conn.commit()

        _rewind_to_pre_v36(conn)
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 34

        backup_dir = tempfile.mkdtemp(prefix='aura-retail-returns-backfill-backup-')
        _TMP_DIRS.append(backup_dir)
        from commercial_runtime.security.migration_safety import ensure_schema_version
        ensure_schema_version(conn, db_path, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)

        assert conn.execute('PRAGMA user_version').fetchone()[0] == sch.RETAIL_SCHEMA_VERSION
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == 'ok'

        tender_refund, ar_forgiven, store_credit = _fetch_split(conn, return_id)
        assert tender_refund == pytest.approx(50.0)
        assert ar_forgiven == pytest.approx(30.0)
        assert store_credit == pytest.approx(0.0)
    finally:
        conn.close()
