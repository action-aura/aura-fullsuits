"""
Aura Retail -- report bucketing runs on the SHOP's business date, not on
whichever device's wall clock happened to ring the sale.

THE DEFECT THIS FILE PINS
=========================
`core/retail/metrics.py` bucketed and filtered on `date(created_at)` and
`strftime('%H', created_at)`. `created_at` is written by `create_sale()` /
`create_return()` as LOCAL WALL CLOCK -- of the device that rang the
transaction (api/retail_api.py, `now_local`). That is fine for exactly one
configuration: a single till whose clock is right.

It is wrong for every other configuration, and it is wrong SILENTLY:

  * Two terminals in two timezones (a shop laptop left on UTC, a phone that
    picked up the carrier's zone) file the SAME INSTANT on two different
    calendar days. One sale lands on Monday, its twin on Sunday, and the
    daily totals for both days are wrong with nothing logged, no exception,
    and no visible symptom until somebody reconciles the till by hand.
  * One device with a clock an hour or two off does the same thing to itself
    around midnight.
  * A shop that closes at 02:00 has its takings split down the middle of the
    night by ANY midnight-anchored bucket -- local or UTC. The night's money
    belongs to the day the shop opened, not to the two calendar dates the
    trading session happened to straddle.

v13 added `created_at_utc` for the first problem. This file proves the
report predicates actually moved onto it, and that the third problem -- the
business-day boundary -- is answered rather than ignored.

WHAT "BUSINESS DATE" MEANS HERE, AND WHY IT NEEDED INVENTING
============================================================
There was no retail-side business-date rule to reuse. Searched and confirmed
absent:

  * `cash_sessions` looks like the obvious owner of "the trading day" and is
    NOT: `_cash_session_report()` scopes by the `session_id` FK explicitly
    "not a time-range" (api/retail_api.py, that function's own docstring), so
    a drawer session defines no calendar boundary at all.
  * `owner/` DOES have one -- `owner/app/operational_reports/periods.py`
    pins `OWNER_TIMEZONE = ZoneInfo("Asia/Amman")` and derives business days
    from it -- but that is Owner's OWN books on Postgres, hardcoded to
    Owner's own country, unreachable from a product install, and it carries
    no intra-day boundary either (its business day is a plain midnight
    local date).
  * `docs/launch-readiness/multi-device-design.md` states the accepted
    design as "daily buckets stay on the shop's CONFIGURED business-date
    offset, not per-device local time" -- a configuration that did not
    exist.

So metrics.py now reads two `retail_settings` keys, through the same
tolerant lookup `_tax_mode()` already uses for `tax_calculation_mode`:

    business_utc_offset_minutes  -- the shop's fixed offset from UTC
    business_day_start_hour      -- the hour the shop's trading day begins

and the DEFAULT (both unset) is byte-for-byte today's behaviour. That
default is deliberate and is asserted below: an unconfigured install has not
told us what zone its business day runs in, and inventing one (UTC, or this
machine's current offset) would move every KPI on every existing install the
day the write side starts stamping `created_at_utc`. Guessing an instant is
the same mistake v13 refused to make when it left history NULL rather than
converting it with whatever offset the migrating machine happened to have.

Run:
    pytest products/retail/tests/retail_metrics_business_date_test.py -v
"""
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# AURA_APP_DATA is set only so importing config/schema is well-defined. This
# file deliberately does NOT go through get_retail_conn()/init_retail(): it
# builds its own sqlite file and calls the v13 migration directly, the same
# way retail_products_uuid_migration_test.py calls the migration it tests.
# That keeps the fixture immune to the module-level AURA_APP_DATA caching
# that products/run_all_tests.py exists to work around -- these tests hold a
# path they created themselves, so a sibling test file's teardown cannot
# delete the database out from under them.
DATA = Path(tempfile.mkdtemp(prefix="aura_retail_bizdate_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from core.retail import metrics  # noqa: E402
from database import schema  # noqa: E402

CID = 'co-bizdate'
PID = 'prod-bizdate'
COST = 10.0

# The shop trades at UTC+3 (Amman -- the same zone owner/ already hardcodes
# for its own books). Two terminals ring into it:
SHOP_OFFSET_MIN = 180
#   T1 is configured correctly, so its wall clock IS the shop's wall clock.
#   T2 was never taken off UTC, so its wall clock is three hours behind the
#   shop it is standing in. Both stamp the same, correct, created_at_utc.
#
# Every sale below is 100.00 (2 units at 50.00, no tax) except the legacy
# one. tax_rate is 0 here ON PURPOSE and it is safe here, unlike in
# retail_metrics_consistency_test.py where a zero rate once certified a real
# tax-basis bug as fixed: nothing in this file asserts anything about the
# tax BASIS, only about which calendar bucket a row lands in, and a zero
# rate keeps every expected total hand-checkable.
UNIT_PRICE = 50.0
SALE_TOTAL = 100.0
LEGACY_TOTAL = 40.0
REFUND_TOTAL = 50.0


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── fixture ───────────────────────────────────────────────────────────────────

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
    """A retail.db carrying the real v13 columns.

    The v13 migration is CALLED, not hand-copied: if a later edit renames
    `created_at_utc` or `actor_user_uid`, these tests break at the fixture
    rather than quietly testing a column metrics.py no longer reads."""
    path = DATA / f'{name}.db'
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_BASE_DDL)
    schema._migrate_add_identity_and_attribution_columns(conn)
    conn.commit()
    return conn


def _sale(conn, sale_no, branch_id, total, created_at, created_at_utc=None,
          actor=None, payment_method='cash', units=2.0, unit_price=UNIT_PRICE):
    cur = conn.execute(
        "INSERT INTO sales (company_id,sale_number,branch_id,total,amount_paid,"
        "payment_method,status,created_at,created_at_utc,actor_user_uid) "
        "VALUES (?,?,?,?,?,?,'completed',?,?,?)",
        (CID, sale_no, branch_id, total, total, payment_method,
         created_at, created_at_utc, actor))
    sale_id = cur.lastrowid
    conn.execute(
        "INSERT INTO sale_items (sale_id,product_id,quantity,unit_price,discount_pct,tax_rate,line_total) "
        "VALUES (?,?,?,?,0,0,?)",
        (sale_id, PID, units, unit_price, units * unit_price))
    conn.commit()
    return sale_id


def _refund(conn, ret_no, branch_id, sale_id, amount, created_at,
            created_at_utc=None, actor=None, refund_method='cash',
            units=1.0, unit_price=UNIT_PRICE):
    cur = conn.execute(
        "INSERT INTO returns (company_id,return_number,sale_id,branch_id,refund_method,"
        "refund_amount,status,created_at,created_at_utc,actor_user_uid) "
        "VALUES (?,?,?,?,?,?,'completed',?,?,?)",
        (CID, ret_no, sale_id, branch_id, refund_method, amount,
         created_at, created_at_utc, actor))
    ret_id = cur.lastrowid
    conn.execute(
        "INSERT INTO return_items (return_id,product_id,quantity,unit_price,line_total) "
        "VALUES (?,?,?,?,?)",
        (ret_id, PID, units, unit_price, units * unit_price))
    conn.commit()
    return ret_id


def _configure(conn, utc_offset_minutes=None, day_start_hour=None):
    """Write (or clear) the shop's business-date configuration."""
    conn.execute("DELETE FROM retail_settings WHERE company_id=?", (CID,))
    for key, value in (('business_utc_offset_minutes', utc_offset_minutes),
                       ('business_day_start_hour', day_start_hour)):
        if value is not None:
            conn.execute(
                "INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)",
                (CID, key, str(value)))
    conn.commit()


@pytest.fixture(scope="module")
def ledger():
    """One shop at UTC+3, one branch, four sales and one refund.

    Every row's TRUE instant is unambiguous (`created_at_utc`); the local
    wall clocks disagree with each other on purpose.

        #   instant (UTC)          rang by  created_at (device local)  shop local
        1   2026-03-09T22:30:00Z   T1       2026-03-10 01:30:00        03-10 01:30
        2   2026-03-09T22:30:00Z   T2       2026-03-09 22:30:00        03-10 01:30
        3   2026-03-10T06:15:00Z   T1       2026-03-10 09:15:00        03-10 09:15
        4   (pre-v13 history)      --       2026-03-10 12:00:00        (no UTC)
        R   2026-03-10T06:15:00Z   T1       2026-03-10 09:15:00        03-10 09:15

    Sales 1 and 2 are THE SAME INSTANT rung on two terminals. Today they
    file on two different days; that is the bug, in two rows.

    Row 4 is history: `created_at_utc` NULL, exactly as v13 left every row
    that existed before it ran. It must keep appearing in every report.
    """
    conn = _new_db('ledger')
    conn.execute("INSERT INTO branches (id,company_id,name) VALUES (1,?, 'Main')", (CID,))
    conn.execute("INSERT INTO branches (id,company_id,name) VALUES (2,?, 'Kiosk')", (CID,))
    conn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) "
        "VALUES (?,?,'BZ-1','Business Date Item',?,?,0)", (PID, CID, COST, UNIT_PRICE))
    conn.commit()

    s1 = _sale(conn, 'S1', 1, SALE_TOTAL, '2026-03-10 01:30:00', '2026-03-09T22:30:00+00:00', actor='emp-A')
    _sale(conn, 'S2', 1, SALE_TOTAL, '2026-03-09 22:30:00', '2026-03-09T22:30:00+00:00', actor='emp-B')
    _sale(conn, 'S3', 1, SALE_TOTAL, '2026-03-10 09:15:00', '2026-03-10T06:15:00+00:00', actor='emp-A')
    _sale(conn, 'S4', 1, LEGACY_TOTAL, '2026-03-10 12:00:00', None, actor=None,
          units=1.0, unit_price=LEGACY_TOTAL)
    _refund(conn, 'R1', 1, s1, REFUND_TOTAL, '2026-03-10 09:15:00', '2026-03-10T06:15:00+00:00', actor='emp-B')

    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _reset_settings(ledger):
    """Every test states its own configuration. Nothing inherits one."""
    _configure(ledger)
    yield
    _configure(ledger)


MAR10 = metrics.Period(start='2026-03-10', end='2026-03-10', days=0)
MAR09 = metrics.Period(start='2026-03-09', end='2026-03-09', days=0)
BOTH_DAYS = metrics.Period(start='2026-03-09', end='2026-03-10', days=1)


# ── the defect itself ─────────────────────────────────────────────────────────

def test_one_instant_rung_on_two_terminals_lands_on_one_business_day(ledger):
    """THE bug, in its smallest form.

    Sales S1 and S2 are the same instant. The shop is at UTC+3, so both
    happened at 01:30 on 2026-03-10 shop time. Bucketing on the writing
    device's wall clock puts S2 on 2026-03-09, because that terminal's
    clock was never taken off UTC -- and nothing anywhere reports an
    error."""
    _configure(ledger, utc_offset_minutes=SHOP_OFFSET_MIN)
    assert metrics.revenue(ledger, CID, MAR10) == 290.0
    assert metrics.transactions(ledger, CID, MAR10) == 4
    # The mirror image: neither sale belongs to the 9th at all.
    assert metrics.revenue(ledger, CID, MAR09) == 0.0
    assert metrics.transactions(ledger, CID, MAR09) == 0


def test_the_day_bucket_moves_with_the_instant_not_with_the_device(ledger):
    """revenue_by_day is the sales-trend chart. Both halves of the split
    have to land in one bar."""
    _configure(ledger, utc_offset_minutes=SHOP_OFFSET_MIN)
    days = {row['day']: row for row in metrics.revenue_by_day(ledger, CID, BOTH_DAYS)}
    assert '2026-03-09' not in days
    assert days['2026-03-10']['revenue'] == 290.0
    assert days['2026-03-10']['transactions'] == 4


def test_the_hour_bucket_is_shop_local_not_device_local(ledger):
    """The dashboard's hourly chart. S1 and S2 are both 01:30 in the shop;
    on the raw device clocks they are 01:30 and 22:30 -- two bars, twelve
    hours apart, for one moment in time."""
    _configure(ledger, utc_offset_minutes=SHOP_OFFSET_MIN)
    hours = metrics.revenue_by_hour(ledger, CID, MAR10)
    assert hours == {'01': 200.0, '09': 50.0, '12': 40.0}


def test_cogs_window_moved_too_so_gross_profit_cannot_drift(ledger):
    """cogs() filters through the same `_scope`, and summary() subtracts it
    from revenue(). If one leg moved onto the business date and the other
    did not, gross_profit would be wrong by exactly the misfiled rows --
    which is the class of disagreement this module exists to kill."""
    _configure(ledger, utc_offset_minutes=SHOP_OFFSET_MIN)
    # 7 units sold (2+2+2+1) less 1 unit returned, at 10.00 cost.
    assert metrics.cogs(ledger, CID, MAR10) == 60.0
    card = metrics.summary(ledger, CID, MAR10)
    assert card['revenue'] == 290.0
    assert card['cogs'] == 60.0
    assert card['gross_profit'] == 230.0


def test_top_products_window_moved_too(ledger):
    _configure(ledger, utc_offset_minutes=SHOP_OFFSET_MIN)
    rows = metrics.top_products(ledger, CID, MAR10)
    assert len(rows) == 1
    assert rows[0]['units_sold'] == 6.0     # 7 sold - 1 returned
    assert rows[0]['revenue'] == 290.0


# ── the shop day is not a midnight day ────────────────────────────────────────

def test_a_shop_that_closes_at_two_am_keeps_the_night_on_one_business_day(ledger):
    """The nuance a naive UTC (or naive local) bucket gets wrong.

    With the trading day starting at 04:00, the 01:30 pair belongs to the
    business day that OPENED on the 9th -- the shop was still trading. The
    09:15 sale, the 12:00 legacy sale and the 09:15 refund belong to the
    10th. A midnight boundary of any flavour cuts that trading session in
    half and reports two wrong days instead of one right one."""
    _configure(ledger, utc_offset_minutes=SHOP_OFFSET_MIN, day_start_hour=4)
    days = {row['day']: row for row in metrics.revenue_by_day(ledger, CID, BOTH_DAYS)}
    assert days['2026-03-09']['revenue'] == 200.0
    assert days['2026-03-09']['transactions'] == 2
    assert days['2026-03-10']['revenue'] == 90.0     # 100 + 40 - 50
    assert days['2026-03-10']['transactions'] == 2


def test_the_day_start_shift_does_not_relabel_the_hour_of_day(ledger):
    """A trading day that starts at 04:00 does not make 01:30 "hour 21".

    The hourly chart is labelled with clock hours a shopkeeper recognises,
    so the day-start shift decides WHICH DAY a row belongs to and nothing
    else. Getting this wrong is invisible on the day totals and obvious
    only to whoever reads the chart."""
    _configure(ledger, utc_offset_minutes=SHOP_OFFSET_MIN, day_start_hour=4)
    assert metrics.revenue_by_hour(ledger, CID, MAR09) == {'01': 200.0}


def test_the_day_start_shift_applies_to_history_that_has_no_utc_stamp(ledger):
    """`business_day_start_hour` is a business rule about the shop's own
    clock, not a timezone conversion, so it stays meaningful on a pre-v13
    row -- whose `created_at` IS a reading of a shop clock -- even though
    that row carries no instant to convert and the shop has declared no
    offset. The two settings are independent for exactly this reason: a
    single-till shop that closes at 02:00 gets correct daily buckets today,
    with no UTC stamps and no sync, by setting the day start alone.

    At a 10:00 trading-day start the 09:15 sale belongs to the day that
    opened on the 9th while the 12:00 legacy sale opens the 10th -- the two
    rows swap sides relative to a midnight boundary, which is precisely
    what a midnight boundary gets wrong."""
    _configure(ledger, day_start_hour=10)
    days = {row['day']: row for row in metrics.revenue_by_day(ledger, CID, BOTH_DAYS)}
    assert days['2026-03-09']['transactions'] == 3      # S1 01:30, S2 22:30, S3 09:15
    assert days['2026-03-09']['revenue'] == 250.0       # 300 sold less the 09:15 refund
    assert days['2026-03-10']['transactions'] == 1      # only the 12:00 legacy row
    assert days['2026-03-10']['revenue'] == 40.0


# ── history must not vanish ───────────────────────────────────────────────────

def test_pre_v13_history_still_appears_in_every_report(ledger):
    """v13 left `created_at_utc` NULL on every row that already existed,
    deliberately, rather than fabricating an instant from the migrating
    machine's current offset. Reading `created_at_utc` alone would drop a
    real shop's ENTIRE trading history out of every report at once -- which
    on screen is indistinguishable from the business having collapsed.

    Mutation-proved: replace the COALESCE with a bare `created_at_utc` and
    this test goes red."""
    _configure(ledger, utc_offset_minutes=SHOP_OFFSET_MIN)
    assert metrics.gross_sales(ledger, CID, MAR10) == 340.0
    days = {row['day']: row for row in metrics.revenue_by_day(ledger, CID, MAR10)}
    assert days['2026-03-10']['transactions'] == 4
    assert metrics.revenue_by_hour(ledger, CID, MAR10)['12'] == 40.0


def test_a_malformed_created_at_utc_falls_back_instead_of_vanishing(ledger):
    """SQLite's `datetime()` returns NULL for an unparseable timestamp
    rather than raising, so a corrupt or wrongly-formatted `created_at_utc`
    relayed in from a peer device would silently take its row out of every
    report. The COALESCE catches that too: the row falls back to the local
    clock it also carries."""
    conn = _new_db('malformed')
    conn.execute("INSERT INTO branches (id,company_id,name) VALUES (1,?, 'Main')", (CID,))
    conn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) "
        "VALUES (?,?,'BZ-1','Item',?,?,0)", (PID, CID, COST, UNIT_PRICE))
    conn.commit()
    _sale(conn, 'S1', 1, SALE_TOTAL, '2026-03-10 12:00:00', 'not-a-timestamp')
    conn.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)",
                 (CID, 'business_utc_offset_minutes', str(SHOP_OFFSET_MIN)))
    conn.commit()
    assert metrics.revenue(conn, CID, MAR10) == 100.0
    conn.close()


# ── the default is today's behaviour, on purpose ──────────────────────────────

def test_an_unconfigured_shop_reports_exactly_what_it_reported_before(ledger):
    """No configuration means the shop has not told us which zone its
    trading day runs in. There is no safe guess: UTC moves every existing
    install's numbers by its own offset, and this machine's current offset
    is wrong for half the year and wrong for every row rung elsewhere.

    So an unconfigured install keeps bucketing on the clock the writing
    device recorded -- which for the single-till shop that is the entire
    installed base today IS the shop's clock. The change costs those
    installs nothing, exactly the way licensing and e-invoicing cost an
    install that never enables them nothing.

    THE CONSEQUENCE, stated rather than buried: a MULTI-DEVICE shop must
    set `business_utc_offset_minutes` before it can trust a daily total.
    Phase 5 must not open sync to a second terminal until it does."""
    boundary = metrics.business_day(ledger, CID)
    assert boundary.utc_offset_minutes is None
    assert boundary.day_start_hour == 0
    # Bucketed on the raw device clocks, S2 is on the 9th -- the pre-existing
    # behaviour, reproduced exactly.
    assert metrics.revenue(ledger, CID, MAR10) == 190.0
    assert metrics.revenue(ledger, CID, MAR09) == 100.0


def test_business_day_reads_the_two_settings_keys(ledger):
    _configure(ledger, utc_offset_minutes=-300, day_start_hour=6)
    boundary = metrics.business_day(ledger, CID)
    assert boundary.utc_offset_minutes == -300
    assert boundary.day_start_hour == 6


def test_a_nonsense_offset_falls_back_to_unconfigured_not_to_utc(ledger, caplog):
    """Rejecting toward "unconfigured" rather than toward 0 is the whole
    point: 0 is a REAL offset that would silently re-file every row, while
    unconfigured is the behaviour the install already had. A typo in a
    settings screen must not restate a shop's trading history."""
    ledger.execute("DELETE FROM retail_settings WHERE company_id=?", (CID,))
    ledger.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)",
                   (CID, 'business_utc_offset_minutes', 'Asia/Amman'))
    ledger.commit()
    with caplog.at_level('WARNING'):
        boundary = metrics.business_day(ledger, CID)
    assert boundary.utc_offset_minutes is None
    # Loud, not silent -- the same rule _returns_query already follows for
    # its own (much narrower) tolerance.
    assert 'business_utc_offset_minutes' in caplog.text


@pytest.mark.parametrize('bad', ['24', '-1', 'midnight', '4.5'])
def test_a_nonsense_day_start_hour_falls_back_to_midnight(ledger, bad):
    ledger.execute("DELETE FROM retail_settings WHERE company_id=?", (CID,))
    ledger.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)",
                   (CID, 'business_day_start_hour', bad))
    ledger.commit()
    assert metrics.business_day(ledger, CID).day_start_hour == 0


def test_an_out_of_range_offset_is_refused(ledger):
    """+/- 14h is the widest real offset on earth; anything beyond a day is
    not a timezone, it is a corrupted value."""
    _configure(ledger, utc_offset_minutes=100000)
    assert metrics.business_day(ledger, CID).utc_offset_minutes is None


def test_a_database_with_no_retail_settings_table_still_reports(ledger):
    """Same tolerance, and the same narrowness, as `_tax_mode`: a hand-built
    fixture or a partially-migrated restore degrades to "unconfigured", not
    to a 500 on the dashboard."""
    conn = _new_db('nosettings')
    conn.execute("INSERT INTO branches (id,company_id,name) VALUES (1,?, 'Main')", (CID,))
    conn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) "
        "VALUES (?,?,'BZ-1','Item',?,?,0)", (PID, CID, COST, UNIT_PRICE))
    conn.execute("DROP TABLE retail_settings")
    conn.commit()
    _sale(conn, 'S1', 1, SALE_TOTAL, '2026-03-10 12:00:00', '2026-03-10T09:00:00+00:00')
    assert metrics.business_day(conn, CID).utc_offset_minutes is None
    assert metrics.revenue(conn, CID, MAR10) == 100.0
    conn.close()


# ── the identity that stops one screen contradicting another ──────────────────

def test_every_breakdown_still_sums_back_to_the_kpi_under_an_offset(ledger):
    """metrics.py's central guarantee: the bars sum to the number printed
    above them. Moving the bucket key without moving the period predicate
    (or vice versa) breaks this silently -- a row inside the window buckets
    to a day outside it and simply disappears from the chart."""
    _configure(ledger, utc_offset_minutes=SHOP_OFFSET_MIN, day_start_hour=4)
    total = metrics.revenue(ledger, CID, BOTH_DAYS)
    assert total == 290.0
    assert round(sum(r['revenue'] for r in metrics.revenue_by_day(ledger, CID, BOTH_DAYS)), 2) == total
    assert round(sum(metrics.revenue_by_hour(ledger, CID, BOTH_DAYS).values()), 2) == total
    assert round(sum(r['revenue'] for r in
                     metrics.revenue_by_payment_method(ledger, CID, BOTH_DAYS)), 2) == total


def test_the_branch_predicate_survives_the_move(ledger):
    """`_scope` grew a business-date expression; it must not have lost the
    branch leg on the way through."""
    _configure(ledger, utc_offset_minutes=SHOP_OFFSET_MIN)
    assert metrics.revenue(ledger, CID, MAR10, branch_id=1) == 290.0
    assert metrics.revenue(ledger, CID, MAR10, branch_id=2) == 0.0


# ── the format contract for whoever wires the write side ──────────────────────

@pytest.mark.parametrize('written', [
    '2026-03-09T22:30:00.123456+00:00',  # WHAT IS ACTUALLY WRITTEN today
    '2026-03-09T22:30:00+00:00',         # the same, on a whole second
    '2026-03-09T22:30:00Z',              # the 'Z' spelling
    '2026-03-09 22:30:00',               # space-separated, implicitly UTC
    '2026-03-10T01:30:00+03:00',         # an OFFSET-bearing stamp: same instant
])
def test_every_reasonable_utc_spelling_buckets_identically(written):
    """The stamp `retail_api._stamp()` writes comes from
    `user_accounts.now_utc_iso()`, i.e. `datetime.now(timezone.utc)
    .isoformat()` -- microseconds and a '+00:00' suffix. That is the FIRST
    case above, and it is the one that would actually break a report if
    SQLite's date functions rejected it.

    The rest are pinned because the write side is one edit away from
    changing spelling (a `.replace('+00:00','Z')`, a `strftime`, a row
    relayed in from a peer device or an importer), and a spelling SQLite
    cannot parse turns into a NULL rather than an error -- so the symptom
    would be rows silently falling back to the local clock, not a crash.
    All five are the same instant and must land on the same business day.

    The last matters for a different reason: an offset-bearing stamp is
    NORMALISED to UTC by SQLite before the modifier is applied, so a device
    that writes its own offset rather than UTC is still filed correctly."""
    conn = _new_db('fmt-' + re.sub(r'\W', '_', written))
    conn.execute("INSERT INTO branches (id,company_id,name) VALUES (1,?, 'Main')", (CID,))
    conn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) "
        "VALUES (?,?,'BZ-1','Item',?,?,0)", (PID, CID, COST, UNIT_PRICE))
    conn.commit()
    # A local clock that is deliberately WRONG, so the assertion can only
    # pass by reading created_at_utc.
    _sale(conn, 'S1', 1, SALE_TOTAL, '2026-03-01 00:00:00', written)
    conn.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)",
                 (CID, 'business_utc_offset_minutes', str(SHOP_OFFSET_MIN)))
    conn.commit()
    assert metrics.revenue(conn, CID, MAR10) == 100.0
    conn.close()


# ── "now" is a business-day question too ──────────────────────────────────────

def test_business_now_puts_the_small_hours_on_the_trading_day_that_opened(ledger):
    """The period constructors take `now` from the caller and do pure
    calendar arithmetic on it -- deliberately, so one response cannot
    straddle midnight (see metrics.py's own note on why `now` is mandatory).
    That leaves one gap: at 01:30 in a shop whose day starts at 04:00, the
    dashboard's "today" card must show the day that is still trading, not
    the calendar date that just began.

    `business_now` is the conversion, kept OUT of the constructors so none
    of their signatures move."""
    _configure(ledger, day_start_hour=4)
    at_0130 = metrics.business_now(ledger, CID, datetime(2026, 3, 10, 1, 30))
    assert metrics.period_today(at_0130).start == '2026-03-09'
    at_0900 = metrics.business_now(ledger, CID, datetime(2026, 3, 10, 9, 0))
    assert metrics.period_today(at_0900).start == '2026-03-10'


def test_business_now_converts_an_aware_clock_onto_the_shop_offset(ledger):
    """A caller that hands over an AWARE datetime gets the exact answer:
    22:30 UTC is already tomorrow in a UTC+3 shop."""
    _configure(ledger, utc_offset_minutes=SHOP_OFFSET_MIN)
    utc_now = datetime(2026, 3, 9, 22, 30, tzinfo=timezone.utc)
    assert metrics.period_today(metrics.business_now(ledger, CID, utc_now)).start == '2026-03-10'


def test_business_now_leaves_an_unconfigured_naive_clock_alone(ledger):
    """No configuration, no shift -- the reporting device's own clock, which
    is what every caller passes today."""
    naive = datetime(2026, 3, 10, 1, 30)
    assert metrics.business_now(ledger, CID, naive) == naive


# ── source-level guard ────────────────────────────────────────────────────────

class _RecordingConn:
    """Delegating proxy that keeps every SQL string metrics.py executes."""

    def __init__(self, real):
        self._real = real
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append(sql)
        return self._real.execute(sql, params)


def test_no_query_buckets_on_a_bare_local_wall_clock(ledger):
    """A behavioural pin, not a source grep: run every public metric under a
    configured shop offset and assert that no statement which touches
    `created_at` at all fails to also name `created_at_utc`.

    This is the regression that would be easiest to reintroduce -- one new
    breakdown copied from an old one, with `date(created_at)` in it, and
    every other test in this file still passes."""
    _configure(ledger, utc_offset_minutes=SHOP_OFFSET_MIN, day_start_hour=4)
    rec = _RecordingConn(ledger)
    metrics.summary(rec, CID, BOTH_DAYS)
    metrics.revenue_by_day(rec, CID, BOTH_DAYS)
    metrics.revenue_by_hour(rec, CID, BOTH_DAYS)
    metrics.revenue_by_payment_method(rec, CID, BOTH_DAYS)
    metrics.revenue_by_branch(rec, CID, BOTH_DAYS)
    metrics.revenue_by_employee(rec, CID, BOTH_DAYS)
    metrics.top_products(rec, CID, BOTH_DAYS)

    offenders = [
        ' '.join(sql.split()) for sql in rec.statements
        if 'created_at' in sql and 'created_at_utc' not in sql
    ]
    assert not offenders, (
        "these statements still read a raw device wall clock:\n  "
        + "\n  ".join(offenders))


def test_the_scope_predicate_names_the_utc_column(ledger):
    """The narrow version of the test above, aimed straight at the one
    fragment every figure in the module is built on."""
    boundary = metrics.BusinessDay(utc_offset_minutes=SHOP_OFFSET_MIN, day_start_hour=4)
    sql, params = metrics._scope(CID, MAR10, boundary)
    assert 'created_at_utc' in sql
    assert params == [CID, '2026-03-10', '2026-03-10']
    aliased, _ = metrics._scope(CID, MAR10, boundary, alias='s')
    assert 's.created_at_utc' in aliased
