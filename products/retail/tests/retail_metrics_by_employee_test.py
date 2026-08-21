"""
Aura Retail -- takings per employee, from the v13 attribution column.

WHY THIS IS A NEW METRIC AND NOT A NEW QUERY
============================================
Before v13 there was nothing to group by. `sales.cashier` is free text --
whatever string the client posted, defaulting to the literal 'POS' -- and
v13's own migration docstring is explicit that it is KEPT, never read, and
never used to guess at an identity, because a wrong name on a sale destroys
the only surviving evidence of who the shop believed rang it. Grouping money
by a free-text field would have produced a report that looks authoritative
and is not.

`actor_user_uid` is a real identity (registry v3's `users.uid`), so a
per-employee breakdown is now answerable. It is added to core/retail/
metrics.py rather than written inline in a route for the same reason every
other figure lives there: the moment "revenue" is spelled out twice, the two
spellings drift, and the last time that happened this product shipped
screens that contradicted each other (see metrics.py's own docstring).

THE THREE DECISIONS THIS FILE PINS
==================================
1. UNATTRIBUTED ROWS ARE REPORTED, NOT DROPPED. Every row written before
   v13 -- i.e. a real shop's entire history -- has `actor_user_uid` NULL,
   and so does every row written after it until the write side starts
   stamping the column. `GROUP BY actor_user_uid` collects those into a
   single NULL bucket, and that bucket is RETURNED. Filtering it out would
   quietly delete most of the shop's money from a screen whose columns still
   add up, which is the worst possible failure shape for a financial report.

2. A REFUND BELONGS TO WHOEVER PROCESSED IT. Not to whoever rang the
   original sale. That is the same rule `revenue_by_payment_method` already
   applies to `refund_method` (cash handed back reduces cash taken, whatever
   the sale was paid with), it is how the cash drawer already reconciles,
   and it is what makes an employee's figure answer the question actually
   being asked -- "what moved through this person's hands today".

   The visible consequence, which is correct and must not be "fixed": an
   employee who only processed refunds reports NEGATIVE takings.

3. NO NAMES. `actor_user_uid` points into registry.db's `users` table --
   a DIFFERENT DATABASE, on a connection metrics.py does not hold and must
   not open (this module takes one `conn` and is pure over it). The uid is
   returned raw and the route resolves it. That also keeps the function
   usable from the reporting paths that have no session.

Run:
    pytest products/retail/tests/retail_metrics_by_employee_test.py -v
"""
import os
import shutil
import sqlite3
import struct
import sys
import tempfile
import zoneinfo
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# See retail_metrics_business_date_test.py's identical note: this file holds
# a database path it created itself instead of going through
# get_retail_conn(), so the module-level AURA_APP_DATA caching that
# products/run_all_tests.py works around cannot reach it.
DATA = Path(tempfile.mkdtemp(prefix="aura_retail_byemp_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

# ── the fixture tz zone ───────────────────────────────────────────────────────
#
# `business_timezone` holds an IANA zone NAME, and `zoneinfo` resolves those
# out of a tz database that is NOT bundled with CPython -- on Windows,
# on the Android build and inside the packaged .exe there is currently none
# (see retail_metrics_business_date_test.py's module docstring, which carries
# the full reasoning and the measurement). So the one zone this file needs is
# written here as a TZif blob and put on TZPATH, which makes the test
# identical on a machine with a full tz database and on one with none.
#
# Only a fixed +03:00 is needed here -- this file is about ATTRIBUTION, and
# the daylight-saving proofs live next door in the file that owns the
# business-date contract.
TZDIR = DATA / 'zoneinfo'
SHOP_TZ = 'Aura_Test/Amman'


def _install_shop_zone():
    blob = (b'TZif' + b'\x00' + b'\x00' * 15
            + struct.pack('>6i', 0, 0, 0, 0, 1, 4)
            + struct.pack('>iBB', 3 * 3600, 0, 0) + b'+03\x00')
    path = TZDIR.joinpath(*SHOP_TZ.split('/'))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    here = str(TZDIR)
    zoneinfo.reset_tzpath([here] + [p for p in zoneinfo.TZPATH if p != here])
    zoneinfo.ZoneInfo.clear_cache()


_install_shop_zone()

from core.retail import metrics  # noqa: E402
from database import schema  # noqa: E402

CID = 'co-byemp'
PID = 'prod-byemp'
COST = 10.0
UNIT_PRICE = 50.0

# Real uids, not names -- see decision 3 above.
EMP_A = 'a1111111-1111-4111-8111-111111111111'
EMP_B = 'b2222222-2222-4222-8222-222222222222'

DAY = metrics.Period(start='2026-04-01', end='2026-04-01', days=0)


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


_BASE_DDL = """
CREATE TABLE branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1,
    name TEXT NOT NULL, address TEXT, phone TEXT, status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE products (
    id TEXT PRIMARY KEY, company_id INTEGER DEFAULT 1, sku TEXT NOT NULL, barcode TEXT,
    name TEXT NOT NULL, category_id TEXT, cost_price REAL DEFAULT 0, sell_price REAL DEFAULT 0,
    tax_rate REAL DEFAULT 0, unit TEXT DEFAULT 'pcs', reorder_level INTEGER DEFAULT 5,
    status TEXT DEFAULT 'active', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE inventory_balances (
    id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1,
    product_id TEXT NOT NULL, branch_id INTEGER NOT NULL,
    quantity_on_hand REAL DEFAULT 0, quantity_reserved REAL DEFAULT 0,
    UNIQUE(company_id, product_id, branch_id));
CREATE TABLE sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, sale_number TEXT UNIQUE,
    branch_id INTEGER, customer_id INTEGER, cashier TEXT DEFAULT 'POS',
    subtotal REAL DEFAULT 0, discount_amount REAL DEFAULT 0, tax_amount REAL DEFAULT 0,
    total REAL DEFAULT 0, amount_paid REAL DEFAULT 0, change_amount REAL DEFAULT 0,
    payment_method TEXT DEFAULT 'cash', status TEXT DEFAULT 'completed',
    idempotency_key TEXT UNIQUE, notes TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE sale_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, sale_id INTEGER NOT NULL, product_id TEXT NOT NULL,
    quantity REAL NOT NULL, unit_price REAL NOT NULL, discount_pct REAL DEFAULT 0,
    tax_rate REAL DEFAULT 0, line_total REAL NOT NULL);
CREATE TABLE returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 1, return_number TEXT UNIQUE,
    sale_id INTEGER, branch_id INTEGER, cashier TEXT DEFAULT 'POS', reason TEXT,
    refund_method TEXT DEFAULT 'cash', refund_amount REAL DEFAULT 0,
    status TEXT DEFAULT 'completed', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE return_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, return_id INTEGER NOT NULL, product_id TEXT NOT NULL,
    quantity REAL NOT NULL, unit_price REAL NOT NULL, line_total REAL NOT NULL);
CREATE TABLE retail_settings (
    company_id INTEGER, skey TEXT, svalue TEXT, PRIMARY KEY (company_id, skey));
"""


def _new_db(name):
    """The v13 migration is CALLED, not hand-copied -- see the identical
    helper in retail_metrics_business_date_test.py."""
    path = DATA / f'{name}.db'
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_BASE_DDL)
    schema._migrate_add_identity_and_attribution_columns(conn)
    conn.execute("INSERT INTO branches (id,company_id,name) VALUES (1,?, 'Main')", (CID,))
    conn.execute("INSERT INTO branches (id,company_id,name) VALUES (2,?, 'Kiosk')", (CID,))
    conn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) "
        "VALUES (?,?,'BE-1','Attributed Item',?,?,0)", (PID, CID, COST, UNIT_PRICE))
    conn.commit()
    return conn


def _sale(conn, sale_no, branch_id, total, created_at, actor,
          created_at_utc=None, payment_method='cash'):
    units = total / UNIT_PRICE
    cur = conn.execute(
        "INSERT INTO sales (company_id,sale_number,branch_id,total,amount_paid,"
        "payment_method,status,created_at,created_at_utc,actor_user_uid) "
        "VALUES (?,?,?,?,?,?,'completed',?,?,?)",
        (CID, sale_no, branch_id, total, total, payment_method,
         created_at, created_at_utc, actor))
    sale_id = cur.lastrowid
    conn.execute(
        "INSERT INTO sale_items (sale_id,product_id,quantity,unit_price,discount_pct,tax_rate,line_total) "
        "VALUES (?,?,?,?,0,0,?)", (sale_id, PID, units, UNIT_PRICE, total))
    conn.commit()
    return sale_id


def _refund(conn, ret_no, branch_id, sale_id, amount, created_at, actor,
            created_at_utc=None, refund_method='cash'):
    units = amount / UNIT_PRICE
    cur = conn.execute(
        "INSERT INTO returns (company_id,return_number,sale_id,branch_id,refund_method,"
        "refund_amount,status,created_at,created_at_utc,actor_user_uid) "
        "VALUES (?,?,?,?,?,?,'completed',?,?,?)",
        (CID, ret_no, sale_id, branch_id, refund_method, amount,
         created_at, created_at_utc, actor))
    ret_id = cur.lastrowid
    conn.execute(
        "INSERT INTO return_items (return_id,product_id,quantity,unit_price,line_total) "
        "VALUES (?,?,?,?,?)", (ret_id, PID, units, UNIT_PRICE, amount))
    conn.commit()
    return ret_id


@pytest.fixture(scope="module")
def ledger():
    """One trading day, two identified employees, one unattributed row.

        S1  emp-A  100.00 cash  branch 1
        S2  emp-A   60.00 card  branch 1
        S3  emp-B   40.00 cash  branch 2
        S4  (NULL)  25.00 cash  branch 1     <- pre-v13 history
        R1  emp-B   30.00 refund against S1, branch 1

    emp-A  takings 160.00 over 2 transactions
    emp-B  takings  10.00 over 1 transaction (40.00 rung, 30.00 handed back)
    NULL   takings  25.00 over 1 transaction

    gross 225.00, refunds 30.00, REVENUE 195.00 over 4 transactions.
    """
    conn = _new_db('ledger')
    s1 = _sale(conn, 'S1', 1, 100.0, '2026-04-01 09:00:00', EMP_A)
    _sale(conn, 'S2', 1, 60.0, '2026-04-01 10:00:00', EMP_A, payment_method='card')
    _sale(conn, 'S3', 2, 40.0, '2026-04-01 11:00:00', EMP_B)
    _sale(conn, 'S4', 1, 25.0, '2026-04-01 12:00:00', None)
    _refund(conn, 'R1', 1, s1, 30.0, '2026-04-01 15:00:00', EMP_B)
    yield conn
    conn.close()


def _by_uid(rows):
    return {row['actor_user_uid']: row for row in rows}


# ── the breakdown itself ──────────────────────────────────────────────────────

def test_takings_and_transaction_count_per_employee(ledger):
    rows = _by_uid(metrics.revenue_by_employee(ledger, CID, DAY))
    assert rows[EMP_A]['revenue'] == 160.0
    assert rows[EMP_A]['transactions'] == 2
    assert rows[EMP_B]['revenue'] == 10.0
    assert rows[EMP_B]['transactions'] == 1


def test_a_refund_is_charged_to_whoever_processed_it(ledger):
    """emp-B handed back 30.00 against a sale emp-A rang. emp-A's takings
    are untouched; emp-B's carry the refund. Charging it to emp-A instead
    would make one employee's figure move because of another employee's
    action, which is not a number anybody can act on."""
    rows = _by_uid(metrics.revenue_by_employee(ledger, CID, DAY))
    assert rows[EMP_A]['refunds'] == 0.0
    assert rows[EMP_A]['gross_sales'] == 160.0
    assert rows[EMP_B]['refunds'] == 30.0
    assert rows[EMP_B]['gross_sales'] == 40.0


def test_unattributed_rows_are_reported_not_silently_dropped(ledger):
    """Every row a real shop already has is unattributed, and will stay that
    way -- v13 refused to guess an actor for history, for the same reason it
    refused to guess an instant. If this bucket were filtered out, the
    per-employee report would omit most of the shop's money while every
    column on the screen still added up."""
    rows = _by_uid(metrics.revenue_by_employee(ledger, CID, DAY))
    assert None in rows, "the unattributed bucket disappeared"
    assert rows[None]['revenue'] == 25.0
    assert rows[None]['transactions'] == 1


def test_the_buckets_sum_back_to_the_revenue_kpi(ledger):
    """`actor_user_uid` partitions sales exhaustively (NULL included), so
    this breakdown owes the same identity revenue_by_day and
    revenue_by_hour owe -- unlike top_products, which truncates, or
    revenue_by_branch, which enumerates active branches only."""
    rows = metrics.revenue_by_employee(ledger, CID, DAY)
    assert round(sum(r['revenue'] for r in rows), 2) == metrics.revenue(ledger, CID, DAY)
    assert sum(r['transactions'] for r in rows) == metrics.transactions(ledger, CID, DAY)


def test_average_ticket_is_the_module_definition_not_a_local_one(ledger):
    """Net revenue over transactions (metrics.py decision 3), never
    AVG(total) -- the exact drift this module was built to end."""
    rows = _by_uid(metrics.revenue_by_employee(ledger, CID, DAY))
    assert rows[EMP_A]['avg_ticket'] == 80.0     # 160.00 / 2
    assert rows[EMP_B]['avg_ticket'] == 10.0     # 10.00 / 1


def test_highest_takings_first(ledger):
    rows = metrics.revenue_by_employee(ledger, CID, DAY)
    assert [r['revenue'] for r in rows] == sorted((r['revenue'] for r in rows), reverse=True)
    assert rows[0]['actor_user_uid'] == EMP_A


def test_nothing_is_truncated(ledger):
    """No `limit` parameter, deliberately: a payroll-shaped figure that
    silently stops at ten people is worse than no figure. top_products can
    truncate because "top 10" is what it says on the chart."""
    rows = metrics.revenue_by_employee(ledger, CID, DAY)
    assert len(rows) == 3


# ── the awkward, correct cases ────────────────────────────────────────────────

def test_a_refund_only_employee_reports_negative_takings(ledger):
    """Scoped to branch 1, emp-B rang nothing and handed back 30.00. The
    honest bucket is -30.00 over zero transactions, and it is what makes the
    branch's buckets still sum to the branch's revenue. metrics.py already
    states this for every other breakdown ("a bucket that saw only refunds
    reports NEGATIVE revenue... that is what makes the buckets sum back to
    the KPI"); it is not a special case here."""
    rows = _by_uid(metrics.revenue_by_employee(ledger, CID, DAY, branch_id=1))
    assert rows[EMP_B]['revenue'] == -30.0
    assert rows[EMP_B]['transactions'] == 0
    assert rows[EMP_B]['avg_ticket'] == 0.0      # never a ZeroDivisionError
    assert round(sum(r['revenue'] for r in rows.values()), 2) == \
        metrics.revenue(ledger, CID, DAY, branch_id=1)


def test_the_branch_filter_applies_to_sales_and_refunds_alike(ledger):
    """metrics.py decision 6: filtering the sales but not the refunds lets
    another branch's refund eat into this branch's takings."""
    rows = _by_uid(metrics.revenue_by_employee(ledger, CID, DAY, branch_id=2))
    assert set(rows) == {EMP_B}
    assert rows[EMP_B]['revenue'] == 40.0        # branch 2 saw no refund
    assert rows[EMP_B]['refunds'] == 0.0


def test_a_period_with_nothing_in_it_is_empty_not_an_error(ledger):
    quiet = metrics.Period(start='2026-04-05', end='2026-04-05', days=0)
    assert metrics.revenue_by_employee(ledger, CID, quiet) == []


# ── it is date-bucketed like everything else ──────────────────────────────────

def test_the_employee_breakdown_uses_the_shop_business_date_too(ledger):
    """A per-employee figure inherits the whole business-date question: a
    terminal left on UTC would otherwise file that employee's late-evening
    takings under yesterday, and a shift that crosses midnight would be
    split across two rows for one person.

    emp-B rings 80.00 at 22:00 UTC, which is 01:00 the NEXT day in this
    UTC+3 shop -- the same instant, two different answers depending on
    which clock is trusted."""
    conn = _new_db('crossing')
    _sale(conn, 'S1', 1, 100.0, '2026-04-01 09:00:00', EMP_A, '2026-04-01T06:00:00+00:00')
    _sale(conn, 'S2', 1, 80.0, '2026-03-31 22:00:00', EMP_B, '2026-03-31T22:00:00+00:00')
    conn.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)",
                 (CID, metrics.BUSINESS_TIMEZONE_SETTING, SHOP_TZ))
    conn.commit()

    rows = _by_uid(metrics.revenue_by_employee(conn, CID, DAY))
    assert rows[EMP_B]['revenue'] == 80.0, "emp-B's 01:00 sale was filed on the wrong day"
    assert rows[EMP_A]['revenue'] == 100.0
    assert round(sum(r['revenue'] for r in rows.values()), 2) == metrics.revenue(conn, CID, DAY)
    conn.close()


def test_the_unattributed_bucket_is_exactly_one_row(ledger):
    """A hard contract term for whatever route serves this, not a nicety.

    The Android reporting screen's LazyColumn deliberately does NOT key on
    `actor_user_uid` (EmployeeSalesScreen.kt) because two null-uid rows
    would throw "Key was already used" and take the whole screen down. The
    GROUP BY guarantees a single NULL bucket; this pins it so a future
    change that splits unattributed rows by anything else -- terminal,
    branch, the free-text `cashier` -- fails here rather than in a crash
    report from a shop."""
    rows = metrics.revenue_by_employee(ledger, CID, DAY)
    assert [r['actor_user_uid'] for r in rows].count(None) == 1


def test_the_employee_breakdown_never_falls_back_to_the_free_text_cashier(ledger):
    """metrics.py decision 8. `sales.cashier` is whatever string the client
    posted, defaulting to the literal 'POS', and money grouped by free text
    looks authoritative and is worthless. Asserted at the SQL level because
    the behavioural symptom -- a bucket keyed 'POS' instead of None -- only
    appears on a database whose cashier column happens to be populated."""

    class _RecordingConn:
        def __init__(self, real):
            self._real, self.statements = real, []

        def execute(self, sql, params=()):
            self.statements.append(sql)
            return self._real.execute(sql, params)

    rec = _RecordingConn(ledger)
    metrics.revenue_by_employee(rec, CID, DAY)
    offenders = [' '.join(s.split()) for s in rec.statements if 'cashier' in s]
    assert not offenders, 'the per-employee breakdown read the free-text cashier column'
