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

So metrics.py reads two `retail_settings` keys, through the same tolerant
lookup `_tax_mode()` already uses for `tax_calculation_mode`:

    business_timezone        -- the shop's IANA zone, e.g. 'Asia/Amman'
    business_day_start_hour  -- the hour the shop's trading day begins

and the DEFAULT (both unset) is byte-for-byte today's behaviour. That
default is deliberate and is asserted below: an unconfigured install has not
told us what zone its business day runs in, and inventing one (UTC, or this
machine's current offset) would move every KPI on every existing install the
day the write side starts stamping `created_at_utc`. Guessing an instant is
the same mistake v13 refused to make when it left history NULL rather than
converting it with whatever offset the migrating machine happened to have.

WHY AN IANA ZONE AND NOT A FIXED OFFSET (the W0.3 change)
=========================================================
`business_timezone` replaced an earlier `business_utc_offset_minutes`, which
held a signed integer number of minutes. Three reasons, in descending order
of how much money they move:

  1. A FIXED OFFSET CANNOT EXPRESS DST, AND HALF THE WORLD HAS IT. A shop in
     a DST zone that declares +120 is an hour wrong for roughly half of every
     year -- every daily total, every hourly bar, every KPI. The two
     transition days are worse still: a real trading day there is 23 or 25
     hours long, and no integer offset can produce a 23-hour day. Those two
     days are pinned below (`test_the_spring_forward_day_is_twenty_three...`,
     `..._fall_back_day_is_twenty_five...`) precisely because they are the
     cases an offset structurally cannot get right.
  2. THE MOBILE SIDE ALREADY SHIPPED THE OTHER CONTRACT.
     docs/retail/unified_mobile/reporting-period-timezone-contract.md and
     mobile/aura-retail-unified/.../reporting/ReportPeriod.kt build every
     report period from `date.atStartOfDayIn(tz)` over a kotlinx-datetime
     `TimeZone`, i.e. an IANA zone name. Two halves of one product filing the
     same sale on two different days is the exact defect this module exists
     to end -- having them disagree about the CONFIGURATION would have been
     the same bug one level up.
  3. THE OLD PARSER REJECTED A ZONE NAME WITH ONLY A LOG LINE. Feeding
     `'Asia/Amman'` to `business_utc_offset_minutes` produced `None` --
     "unconfigured" -- so the moment the mobile side wrote the key it
     believed in, every Python report silently reverted to device-local
     bucketing with nothing on screen to say so. That is pinned in reverse
     below: the old key is now WARNED about rather than read
     (`test_the_retired_offset_key_is_warned_about_not_silently_ignored`).

THIS FILE CARRIES ITS OWN TZ DATABASE -- ON PURPOSE
====================================================
`zoneinfo` is stdlib and always imports, but the DATA it reads is not
bundled with CPython. On Windows there is no `/usr/share/zoneinfo`, `TZPATH`
is empty, and the `tzdata` PyPI package is in none of `requirements/*.txt`.
Measured on this project's interpreter: `available_timezones()` returns
ZERO, and `ZoneInfo('America/New_York')` raises `ZoneInfoNotFoundError`.

So a DST proof written against `America/New_York` would not run here -- it
would ERROR on a dev box and pass in CI or vice versa, which is worse than
no proof. Instead this file WRITES ITS OWN TZif files (`_install_test_zones`
below) and prepends them to `TZPATH`. The rules are then stated in this
file rather than assumed from the host, the production lookup path
(`ZoneInfo(<key from retail_settings>)`) is exercised unchanged, and the
result is identical on a machine with a full tz database and on one with
none.

That is a test-fixture answer, not a product answer. THE PRODUCT STILL
NEEDS `tzdata` DECLARED -- in requirements/base.txt, in the Chaquopy pip
block for the Android build, and collected by the PyInstaller spec.
Until it is, `business_timezone` resolves nothing on Windows, on Android or
in the packaged .exe, and every install degrades to unconfigured with a
WARNING (which is pinned below, `test_a_machine_with_no_tz_database...`, so
the degradation is at least loud).

Run:
    pytest products/retail/tests/retail_metrics_business_date_test.py -v
"""
import calendar
import os
import re
import shutil
import sqlite3
import struct
import sys
import tempfile
import zoneinfo
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


# ── the fixture tz database ───────────────────────────────────────────────────
#
# See this module's docstring for WHY these are built rather than looked up.
# Format: TZif version 1 (RFC 8536 section 3.1) -- header, 32-bit transition
# times, one type index per transition, the ttinfo table, then the
# NUL-separated abbreviation blob. Version 1 is enough here: these zones only
# need transitions inside the 32-bit epoch range, and CPython's `zoneinfo`
# reads a v1 file directly (verified before this fixture was written).

TZDIR = DATA / 'zoneinfo'

#: Fixed +03:00, no DST -- the same clock Jordan keeps, and the same one
#: `owner/app/operational_reports/periods.py` hardcodes for Owner's own
#: books. The ledger below trades in this zone, so every expected total in
#: this file is hand-checkable as "the instant, plus three hours".
SHOP_TZ = 'Aura_Test/Amman'

#: US-Eastern rules: -05:00 standard, -04:00 daylight, springing forward on
#: the second Sunday in March at 02:00 local and falling back on the first
#: Sunday in November at 02:00 local. The DST proofs use this and nothing
#: else, so the rules a test depends on are written down in the test.
DST_TZ = 'Aura_Test/Eastern'

#: Two zones 25 hours apart, so whatever zone the machine running this file
#: happens to be in, at least one of them is a long way from it. Used by the
#: naive-clock `business_now` proof, which is only meaningful when the
#: reporting device and the shop are NOT on the same clock.
FAR_EAST_TZ = 'Aura_Test/Plus14'
FAR_WEST_TZ = 'Aura_Test/Minus11'

_EASTERN_TRANSITIONS_UTC = (
    # (year, month, day, hour) in UTC, and which type it switches TO.
    # Spring forward is 02:00 EST == 07:00Z; fall back is 02:00 EDT == 06:00Z.
    ((2025, 3, 9, 7), 1), ((2025, 11, 2, 6), 0),
    ((2026, 3, 8, 7), 1), ((2026, 11, 1, 6), 0),
    ((2027, 3, 14, 7), 1), ((2027, 11, 7, 6), 0),
)


def _tzif_v1(transitions, types, designations):
    """One TZif version-1 blob.

    transitions: [(utc_epoch_seconds, type_index)], ascending.
    types:       [(utc_offset_seconds, is_dst, designation_index)].
    designations: NUL-separated abbreviations, e.g. b'EST\\x00EDT\\x00'.
    """
    blob = b'TZif' + b'\x00' + b'\x00' * 15
    blob += struct.pack('>6i', 0, 0, 0, len(transitions), len(types), len(designations))
    blob += b''.join(struct.pack('>i', when) for when, _ in transitions)
    blob += b''.join(struct.pack('>B', idx) for _, idx in transitions)
    blob += b''.join(struct.pack('>iBB', off, dst, idx) for off, dst, idx in types)
    blob += designations
    return blob


def _write_zone(key, blob):
    path = TZDIR.joinpath(*key.split('/'))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)


def _install_test_zones():
    """Write the fixture zones and put them on TZPATH.

    PREPENDED to whatever TZPATH already holds rather than replacing it, so
    a machine that DOES have a real tz database keeps it: these keys are
    namespaced under `Aura_Test/` and cannot collide with a real zone.
    products/run_all_tests.py runs one pytest process per test FILE, so this
    global cannot leak into a sibling suite; the accumulate-don't-replace
    shape is for the case where somebody runs two files in one process by
    hand."""
    for key, blob in (
        (SHOP_TZ, _tzif_v1([], [(3 * 3600, 0, 0)], b'+03\x00')),
        (FAR_EAST_TZ, _tzif_v1([], [(14 * 3600, 0, 0)], b'+14\x00')),
        (FAR_WEST_TZ, _tzif_v1([], [(-11 * 3600, 0, 0)], b'-11\x00')),
        (DST_TZ, _tzif_v1(
            [(calendar.timegm(stamp + (0, 0, 0, 0)), idx)
             for stamp, idx in _EASTERN_TRANSITIONS_UTC],
            [(-5 * 3600, 0, 0), (-4 * 3600, 1, 4)],
            b'EST\x00EDT\x00')),
    ):
        _write_zone(key, blob)
    here = str(TZDIR)
    zoneinfo.reset_tzpath([here] + [p for p in zoneinfo.TZPATH if p != here])
    zoneinfo.ZoneInfo.clear_cache()


_install_test_zones()

from core.retail import metrics  # noqa: E402
from database import schema  # noqa: E402

CID = 'co-bizdate'
PID = 'prod-bizdate'
COST = 10.0

# The shop trades at UTC+3 (see SHOP_TZ). Two terminals ring into it:
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


def _seed_catalogue(conn):
    conn.execute("INSERT INTO branches (id,company_id,name) VALUES (1,?, 'Main')", (CID,))
    conn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) "
        "VALUES (?,?,'BZ-1','Business Date Item',?,?,0)", (PID, CID, COST, UNIT_PRICE))
    conn.commit()


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


def _configure(conn, zone=None, day_start_hour=None):
    """Write (or clear) the shop's business-date configuration."""
    conn.execute("DELETE FROM retail_settings WHERE company_id=?", (CID,))
    for key, value in ((metrics.BUSINESS_TIMEZONE_SETTING, zone),
                       (metrics.BUSINESS_DAY_START_SETTING, day_start_hour)):
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


def _day(text):
    return metrics.Period(start=text, end=text, days=0)


# ── the defect itself ─────────────────────────────────────────────────────────

def test_one_instant_rung_on_two_terminals_lands_on_one_business_day(ledger):
    """THE bug, in its smallest form.

    Sales S1 and S2 are the same instant. The shop is at UTC+3, so both
    happened at 01:30 on 2026-03-10 shop time. Bucketing on the writing
    device's wall clock puts S2 on 2026-03-09, because that terminal's
    clock was never taken off UTC -- and nothing anywhere reports an
    error."""
    _configure(ledger, zone=SHOP_TZ)
    assert metrics.revenue(ledger, CID, MAR10) == 290.0
    assert metrics.transactions(ledger, CID, MAR10) == 4
    # The mirror image: neither sale belongs to the 9th at all.
    assert metrics.revenue(ledger, CID, MAR09) == 0.0
    assert metrics.transactions(ledger, CID, MAR09) == 0


def test_the_day_bucket_moves_with_the_instant_not_with_the_device(ledger):
    """revenue_by_day is the sales-trend chart. Both halves of the split
    have to land in one bar."""
    _configure(ledger, zone=SHOP_TZ)
    days = {row['day']: row for row in metrics.revenue_by_day(ledger, CID, BOTH_DAYS)}
    assert '2026-03-09' not in days
    assert days['2026-03-10']['revenue'] == 290.0
    assert days['2026-03-10']['transactions'] == 4


def test_the_hour_bucket_is_shop_local_not_device_local(ledger):
    """The dashboard's hourly chart. S1 and S2 are both 01:30 in the shop;
    on the raw device clocks they are 01:30 and 22:30 -- two bars, twelve
    hours apart, for one moment in time."""
    _configure(ledger, zone=SHOP_TZ)
    hours = metrics.revenue_by_hour(ledger, CID, MAR10)
    assert hours == {'01': 200.0, '09': 50.0, '12': 40.0}


def test_cogs_window_moved_too_so_gross_profit_cannot_drift(ledger):
    """cogs() filters through the same `_scope`, and summary() subtracts it
    from revenue(). If one leg moved onto the business date and the other
    did not, gross_profit would be wrong by exactly the misfiled rows --
    which is the class of disagreement this module exists to kill."""
    _configure(ledger, zone=SHOP_TZ)
    # 7 units sold (2+2+2+1) less 1 unit returned, at 10.00 cost.
    assert metrics.cogs(ledger, CID, MAR10) == 60.0
    card = metrics.summary(ledger, CID, MAR10)
    assert card['revenue'] == 290.0
    assert card['cogs'] == 60.0
    assert card['gross_profit'] == 230.0


def test_top_products_window_moved_too(ledger):
    _configure(ledger, zone=SHOP_TZ)
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
    _configure(ledger, zone=SHOP_TZ, day_start_hour=4)
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
    _configure(ledger, zone=SHOP_TZ, day_start_hour=4)
    assert metrics.revenue_by_hour(ledger, CID, MAR09) == {'01': 200.0}


def test_the_day_start_shift_applies_to_history_that_has_no_utc_stamp(ledger):
    """`business_day_start_hour` is a business rule about the shop's own
    clock, not a timezone conversion, so it stays meaningful on a pre-v13
    row -- whose `created_at` IS a reading of a shop clock -- even though
    that row carries no instant to convert and the shop has declared no
    zone. The two settings are independent for exactly this reason: a
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


# ── daylight saving: the reason an integer offset had to go ───────────────────

@pytest.fixture(scope="module")
def dst_ledger():
    """One shop on US-Eastern rules (`DST_TZ`), one sale per interesting
    instant. Every sale is 100.00 so a bucket's revenue is a headcount.

    The instants are chosen so that a FIXED-OFFSET implementation -- either
    -300 all year or -240 all year -- puts at least one of them on the wrong
    business date. There is no integer that files all of them correctly,
    which is the whole argument for the IANA key.
    """
    conn = _new_db('dst')
    _seed_catalogue(conn)
    for name, instant in (
        # Same clock time (04:30Z) in January and in June. EST is -5, EDT is
        # -4, so the two land on OPPOSITE sides of their local midnight.
        ('W', '2026-01-15T04:30:00+00:00'),
        ('S', '2026-06-15T04:30:00+00:00'),
        # The 23-hour day: 2026-03-08 local, EST->EDT at 07:00Z.
        ('SPRING_OPEN', '2026-03-08T05:00:00+00:00'),   # 00:00 EST, first instant
        ('SPRING_CLOSE', '2026-03-09T03:59:00+00:00'),  # 23:59 EDT, last instant
        ('SPRING_NEXT', '2026-03-09T04:00:00+00:00'),   # 00:00 EDT, next day
        # The 25-hour day: 2026-11-01 local, EDT->EST at 06:00Z.
        ('FALL_OPEN', '2026-11-01T04:00:00+00:00'),     # 00:00 EDT, first instant
        ('FALL_CLOSE', '2026-11-02T04:59:00+00:00'),    # 23:59 EST, last instant
        ('FALL_NEXT', '2026-11-02T05:00:00+00:00'),     # 00:00 EST, next day
    ):
        # created_at is deliberately a clock nobody should be reading here:
        # the raw UTC wall time. If any predicate falls back to it, the
        # buckets below move and these tests go red.
        _sale(conn, name, 1, SALE_TOTAL, instant[:10] + ' ' + instant[11:19], instant)
    _configure(conn, zone=DST_TZ)
    yield conn
    conn.close()


def test_the_same_utc_clock_time_buckets_differently_in_summer_and_winter(dst_ledger):
    """THE DST PROOF. Two sales at 04:30Z, one in January and one in June.

    In January the shop is on EST (-5), so 04:30Z is 23:30 the PREVIOUS day
    and the sale belongs to 2026-01-14. In June the shop is on EDT (-4), so
    04:30Z is 00:30 the SAME day and the sale belongs to 2026-06-15.

    No fixed offset can produce both answers: -300 files the June sale on
    the 14th, -240 files the January sale on the 15th. This is the assertion
    a reversion to `business_utc_offset_minutes` cannot satisfy."""
    assert metrics.revenue(dst_ledger, CID, _day('2026-01-14')) == 100.0
    assert metrics.revenue(dst_ledger, CID, _day('2026-01-15')) == 0.0

    assert metrics.revenue(dst_ledger, CID, _day('2026-06-15')) == 100.0
    assert metrics.revenue(dst_ledger, CID, _day('2026-06-14')) == 0.0


def test_the_spring_forward_day_is_twenty_three_hours_long(dst_ledger):
    """The case the mobile contract's own DST test does NOT cover, and the
    one an offset structurally cannot express.

    Business day 2026-03-08 in this zone opens at 05:00Z (00:00 EST) and
    closes at 04:00Z the next morning (00:00 EDT). That is 23 hours. A sale
    at 03:59Z on the 9th is 23:59 on the 8th and belongs to the 8th; one
    minute later opens the 9th."""
    days = {row['day']: row['revenue'] for row in
            metrics.revenue_by_day(dst_ledger, CID,
                                   metrics.Period('2026-03-08', '2026-03-09', 1))}
    assert days.get('2026-03-08') == 200.0    # SPRING_OPEN + SPRING_CLOSE
    assert days.get('2026-03-09') == 100.0    # SPRING_NEXT alone


def test_the_fall_back_day_is_twenty_five_hours_long(dst_ledger):
    """The mirror image. Business day 2026-11-01 opens at 04:00Z (00:00 EDT)
    and closes at 05:00Z the next morning (00:00 EST) -- 25 hours. A sale at
    04:59Z on the 2nd is still 23:59 on the 1st."""
    days = {row['day']: row['revenue'] for row in
            metrics.revenue_by_day(dst_ledger, CID,
                                   metrics.Period('2026-11-01', '2026-11-02', 1))}
    assert days.get('2026-11-01') == 200.0    # FALL_OPEN + FALL_CLOSE
    assert days.get('2026-11-02') == 100.0    # FALL_NEXT alone


def test_a_window_spanning_a_transition_still_sums_back_to_the_kpi(dst_ledger):
    """The module's central identity, checked across the seam where the
    offset actually changes: if the WHERE clause and the GROUP BY key used
    two independently-built transition tables, a row near the boundary would
    pass one and fail the other and simply vanish from the chart."""
    window = metrics.Period('2026-01-01', '2026-12-31', 364)
    total = metrics.revenue(dst_ledger, CID, window)
    assert total == 800.0     # all eight sales
    assert round(sum(r['revenue'] for r in
                     metrics.revenue_by_day(dst_ledger, CID, window)), 2) == total
    assert round(sum(metrics.revenue_by_hour(dst_ledger, CID, window).values()), 2) == total
    assert round(sum(r['revenue'] for r in
                     metrics.revenue_by_employee(dst_ledger, CID, window)), 2) == total


@pytest.mark.parametrize('around,day_start', [
    ('2026-03-08', 0),    # spring forward, midnight trading day
    ('2026-11-01', 0),    # fall back, midnight trading day
    ('2026-03-08', 4),    # spring forward, 04:00 trading day
    ('2026-11-01', 4),    # fall back, 04:00 trading day
])
def test_sql_bucketing_matches_python_zoneinfo_across_a_transition(around, day_start):
    """THE GENERATED SQL, CHECKED AGAINST AN INDEPENDENT ORACLE.

    Every other test here asserts a handful of hand-computed instants. This
    one asserts the whole conversion: three days of instants at ten-minute
    resolution straddling a real DST transition, bucketed by the SQL
    metrics.py generates, compared against the answer `zoneinfo` gives
    directly in Python.

    That is the check that cannot be satisfied by a plausible-looking
    expression that is off by an hour on one side of the boundary -- which
    is precisely the failure mode of hand-written timezone arithmetic, and
    precisely what a fixed offset does for half the year.

    The 04:00 variants matter separately: the day-start shift is applied
    AFTER the zone conversion, so on a 23-hour day the shifted boundary
    lands somewhere a midnight-only test never looks."""
    zone = zoneinfo.ZoneInfo(DST_TZ)
    conn = _new_db(f'oracle-{around}-{day_start}')
    _seed_catalogue(conn)
    _configure(conn, zone=DST_TZ, day_start_hour=day_start)

    pivot = datetime.fromisoformat(around + 'T00:00:00+00:00')
    expected = {}
    for step in range(-144, 289):            # -1 day .. +2 days, 10-minute steps
        instant = pivot + timedelta(minutes=10) * step
        _sale(conn, f'O{step}', 1, SALE_TOTAL,
              '1999-01-01 00:00:00',           # a device clock that must never be read
              instant.isoformat())
        # The oracle: convert with zoneinfo, then roll the trading day back.
        shop = instant.astimezone(zone).replace(tzinfo=None)
        business = (shop - timedelta(hours=day_start)).date()
        key = business.strftime('%Y-%m-%d')
        expected[key] = round(expected.get(key, 0.0) + SALE_TOTAL, 2)

    window = metrics.Period(min(expected), max(expected), None)
    actual = {row['day']: row['revenue'] for row in metrics.revenue_by_day(conn, CID, window)}
    conn.close()

    assert actual == expected
    # ...and the window really does contain a transition, or this proves nothing.
    offsets = {(pivot + timedelta(days=d)).astimezone(zone).utcoffset() for d in (-1, 2)}
    assert len(offsets) == 2, 'this window straddles no transition and is not a DST test'


def test_the_hour_bucket_follows_daylight_saving_too(dst_ledger):
    """The hourly chart takes the shop's zone but not the day-start shift
    (see metrics.py decision 5). Both 04:30Z sales must be labelled with
    the hour a shopkeeper standing in the shop would have read off the wall:
    23 in January, 00 in June."""
    assert metrics.revenue_by_hour(dst_ledger, CID, _day('2026-01-14')) == {'23': 100.0}
    assert metrics.revenue_by_hour(dst_ledger, CID, _day('2026-06-15')) == {'00': 100.0}


# ── history must not vanish ───────────────────────────────────────────────────

def test_pre_v13_history_still_appears_in_every_report(ledger):
    """v13 left `created_at_utc` NULL on every row that already existed,
    deliberately, rather than fabricating an instant from the migrating
    machine's current offset. Reading `created_at_utc` alone would drop a
    real shop's ENTIRE trading history out of every report at once -- which
    on screen is indistinguishable from the business having collapsed.

    Mutation-proved: replace the COALESCE with a bare `created_at_utc` and
    this test goes red."""
    _configure(ledger, zone=SHOP_TZ)
    assert metrics.gross_sales(ledger, CID, MAR10) == 340.0
    days = {row['day']: row for row in metrics.revenue_by_day(ledger, CID, MAR10)}
    assert days['2026-03-10']['transactions'] == 4
    assert metrics.revenue_by_hour(ledger, CID, MAR10)['12'] == 40.0


def test_no_public_figure_drops_a_shop_whose_history_predates_v13(ledger):
    """The COALESCE audit, run over EVERY public entry point at once.

    A real shop upgrading to v13 has `created_at_utc` NULL on 100% of its
    rows, and stays that way for everything it has ever sold. If any ONE
    predicate in this module reads the instant column alone, that shop's
    reports go to zero the day it declares its timezone -- the single most
    alarming thing a POS can show a business, and the least likely to be
    read as a reporting bug.

    So this asserts the whole surface, not a sample: a ledger with no
    instants at all, a declared shop zone, and every figure the module
    exposes still showing the money."""
    conn = _new_db('legacy_only')
    _seed_catalogue(conn)
    sale_id = _sale(conn, 'L1', 1, SALE_TOTAL, '2026-03-10 09:00:00', None, actor='emp-A')
    _sale(conn, 'L2', 1, SALE_TOTAL, '2026-03-10 11:00:00', None, actor=None,
          payment_method='card')
    _refund(conn, 'LR1', 1, sale_id, REFUND_TOTAL, '2026-03-10 15:00:00', None, actor='emp-A')
    _configure(conn, zone=SHOP_TZ)

    assert metrics.gross_sales(conn, CID, MAR10) == 200.0
    assert metrics.refunds(conn, CID, MAR10) == 50.0
    assert metrics.transactions(conn, CID, MAR10) == 2
    assert metrics.revenue(conn, CID, MAR10) == 150.0
    assert metrics.cogs(conn, CID, MAR10) == 30.0        # 4 units sold, 1 returned
    assert metrics.summary(conn, CID, MAR10)['revenue'] == 150.0
    assert [r['revenue'] for r in metrics.revenue_by_day(conn, CID, MAR10)] == [150.0]
    # The 15:00 refund is its own hour bucket and reports negative -- that is
    # decision #1, not a rounding artefact, and it is what keeps the hours
    # summing back to the 150.00 KPI.
    assert metrics.revenue_by_hour(conn, CID, MAR10) == {'09': 100.0, '11': 100.0, '15': -50.0}
    assert round(sum(r['revenue'] for r in
                     metrics.revenue_by_payment_method(conn, CID, MAR10)), 2) == 150.0
    assert round(sum(r['revenue'] for r in
                     metrics.revenue_by_employee(conn, CID, MAR10)), 2) == 150.0
    assert round(sum(r['revenue'] for r in
                     metrics.revenue_by_branch(conn, CID, MAR10)), 2) == 150.0
    assert round(sum(r['revenue'] for r in metrics.top_products(conn, CID, MAR10)), 2) == 150.0
    conn.close()


def test_a_row_carrying_only_an_instant_survives_an_unconfigured_shop(ledger):
    """The mirror of the test above, on the other default.

    A row relayed in from a peer device (or written by a client that stamps
    only UTC) can have `created_at_utc` set and `created_at` NULL. An
    unconfigured shop has no zone to convert that instant WITH -- but the
    answer is still "put it in some bucket", never "drop it". Money in no
    bucket at all is the one outcome this module never accepts."""
    conn = _new_db('instant_only')
    _seed_catalogue(conn)
    _sale(conn, 'U1', 1, SALE_TOTAL, None, '2026-03-10T09:00:00+00:00')
    assert metrics.business_day(conn, CID).zone is None
    assert metrics.revenue(conn, CID, MAR10) == 100.0
    assert [r['revenue'] for r in metrics.revenue_by_day(conn, CID, MAR10)] == [100.0]
    conn.close()


def test_a_malformed_created_at_utc_falls_back_instead_of_vanishing(ledger):
    """SQLite's `datetime()` returns NULL for an unparseable timestamp
    rather than raising, so a corrupt or wrongly-formatted `created_at_utc`
    relayed in from a peer device would silently take its row out of every
    report. The COALESCE catches that too: the row falls back to the local
    clock it also carries."""
    conn = _new_db('malformed')
    _seed_catalogue(conn)
    _sale(conn, 'S1', 1, SALE_TOTAL, '2026-03-10 12:00:00', 'not-a-timestamp')
    _configure(conn, zone=SHOP_TZ)
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
    set `business_timezone` before it can trust a daily total. Phase 5 must
    not open sync to a second terminal until it does."""
    boundary = metrics.business_day(ledger, CID)
    assert boundary.zone is None
    assert boundary.day_start_hour == 0
    # Bucketed on the raw device clocks, S2 is on the 9th -- the pre-existing
    # behaviour, reproduced exactly.
    assert metrics.revenue(ledger, CID, MAR10) == 190.0
    assert metrics.revenue(ledger, CID, MAR09) == 100.0


def test_business_day_reads_the_two_settings_keys(ledger):
    _configure(ledger, zone=DST_TZ, day_start_hour=6)
    boundary = metrics.business_day(ledger, CID)
    assert boundary.zone == zoneinfo.ZoneInfo(DST_TZ)
    assert boundary.zone.key == DST_TZ
    assert boundary.day_start_hour == 6


@pytest.mark.parametrize('bad', [
    'Mars/Olympus',   # a well-formed key for a zone that does not exist
    '+02:00',         # an offset, which is what the retired key held
    '180',            # the retired key's exact value format
    'Asia/Amman/../../etc/passwd',   # a key zoneinfo refuses as malformed
    # 'EET' WAS here, described as "an abbreviation, which is not a zone
    # identifier". That was false, and it only passed because the machine
    # running the suite had NO TZ DATABASE AT ALL -- every key failed to
    # resolve, so a list of supposedly-bad keys could not tell a bad key from
    # a good one. See test_EET_is_a_real_zone_and_is_accepted below, which now
    # pins the truth. `tzdata` is a dependency as of requirements/retail.txt;
    # before it, this whole parametrisation was vacuous.
])
def test_a_value_that_is_not_a_zone_falls_back_to_unconfigured_not_to_utc(ledger, caplog, bad):
    """Rejecting toward "unconfigured" rather than toward UTC is the whole
    point, and it matters MORE under the IANA contract than it did under the
    offset one: `ZoneInfo('UTC')` is a real, valid zone, so a fallback to it
    would look principled and would silently re-file every row on every
    install. Unconfigured is the behaviour the install already had.

    '180' is in this list deliberately. It is exactly what the retired
    `business_utc_offset_minutes` key held, so an install (or a test
    fixture, or a hand-edited database) that copied the old value across
    into the new key must be REFUSED and logged -- not read as some zone."""
    ledger.execute("DELETE FROM retail_settings WHERE company_id=?", (CID,))
    ledger.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)",
                   (CID, metrics.BUSINESS_TIMEZONE_SETTING, bad))
    ledger.commit()
    with caplog.at_level('WARNING'):
        boundary = metrics.business_day(ledger, CID)
    assert boundary.zone is None
    # Loud, not silent -- the same rule _returns_query already follows for
    # its own (much narrower) tolerance.
    assert metrics.BUSINESS_TIMEZONE_SETTING in caplog.text
    assert bad in caplog.text
    # ...and the report still draws, on the behaviour the install had before.
    assert metrics.revenue(ledger, CID, MAR10) == 190.0


def test_EET_is_a_real_zone_and_is_accepted(ledger):
    """The anti-vacuity partner to the rejection list above, and the reason it
    exists is worth keeping.

    `EET` was previously listed as a value to REJECT, on the reasoning that an
    abbreviation is not a zone identifier. It is one: tzdata ships `EET` as a
    generic European zone carrying real DST rules (UTC+2 in winter, UTC+3 in
    summer). Measured, not assumed -- the assertion below is the measurement.

    The old expectation passed for a reason that had nothing to do with the
    product: this machine had no tz database, so `ZoneInfo` raised for EVERY
    key and a list of "bad" keys could not distinguish a bad one from a good
    one. The rejection test was therefore vacuous in both directions until
    `tzdata` became a dependency.

    Keeping this as a POSITIVE test matters: without it, someone could satisfy
    the rejection list again by making `parse_business_zone` refuse everything,
    and every assertion in this file would still pass."""
    zone = zoneinfo.ZoneInfo('EET')
    summer = datetime(2026, 6, 15, 12, 0, tzinfo=zone).utcoffset()
    winter = datetime(2026, 1, 15, 12, 0, tzinfo=zone).utcoffset()
    assert summer != winter, (
        'EET is only worth accepting because it carries real DST rules; if this '
        'ever fails the reasoning above is stale')

    _configure(ledger, zone='EET', day_start_hour=0)
    boundary = metrics.business_day(ledger, CID)
    assert boundary.zone is not None, 'a real zone must not be refused'
    assert boundary.zone.key == 'EET'


def test_the_retired_offset_key_is_warned_about_not_silently_ignored(ledger, caplog):
    """The reverse of the migration: a database that still holds
    `business_utc_offset_minutes` (only reachable by hand-editing the file --
    no write path ever existed) must not have it read, AND must not have it
    ignored in silence.

    Silence is the failure mode this whole wave exists to end: the shop
    believes it has declared its trading day, every report is on device
    local time, and nothing anywhere says otherwise."""
    ledger.execute("DELETE FROM retail_settings WHERE company_id=?", (CID,))
    ledger.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,'180')",
                   (CID, metrics.RETIRED_UTC_OFFSET_SETTING))
    ledger.commit()
    with caplog.at_level('WARNING'):
        boundary = metrics.business_day(ledger, CID)
    assert boundary.zone is None
    assert metrics.RETIRED_UTC_OFFSET_SETTING in caplog.text
    assert metrics.BUSINESS_TIMEZONE_SETTING in caplog.text


@pytest.mark.parametrize('bad', ['24', '-1', 'midnight', '4.5'])
def test_a_nonsense_day_start_hour_falls_back_to_midnight(ledger, bad):
    ledger.execute("DELETE FROM retail_settings WHERE company_id=?", (CID,))
    ledger.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)",
                   (CID, metrics.BUSINESS_DAY_START_SETTING, bad))
    ledger.commit()
    assert metrics.business_day(ledger, CID).day_start_hour == 0


def test_a_machine_with_no_tz_database_degrades_loudly_not_silently(ledger, caplog):
    """`zoneinfo` is stdlib and always imports; the tz DATA is a separate,
    undeclared dependency (see this module's docstring). On Windows, on
    Android and inside the packaged .exe there is currently none, so a
    perfectly valid `business_timezone` resolves to nothing.

    That must not 500 the dashboard and must not be silent. The install
    keeps the bucketing it already had, and the log says WHY -- naming the
    missing package, because "no time zone found with key Asia/Amman" reads
    like a typo in the settings screen and is not one.

    TZPATH is emptied for the duration so this is deterministic on a machine
    that DOES have a tz database, instead of only proving something on the
    one that does not.

    EMPTYING TZPATH IS NOT ENOUGH ON ITS OWN, and this test failed the moment
    `tzdata` became a real dependency (requirements/retail.txt, and the
    Chaquopy pip block for Android). `zoneinfo` has TWO resolution paths: the
    filesystem search list, and the `tzdata` PACKAGE. Blanking the first
    leaves the second, so zones still resolved, `_tz_database_present()`
    correctly answered True, and the code correctly emitted the "not an IANA
    name" message -- which this test then read as a failure.

    The product behaviour was right in both states; the simulation was
    incomplete. Both paths are closed below. `sys.modules['tzdata'] = None` is
    the documented way to make a subsequent `import tzdata` raise ImportError
    without touching the filesystem."""
    _configure(ledger, zone=SHOP_TZ)
    saved = list(zoneinfo.TZPATH)
    empty = DATA / 'no_zoneinfo_at_all'
    empty.mkdir(exist_ok=True)
    saved_modules = {name: mod for name, mod in sys.modules.items()
                     if name == 'tzdata' or name.startswith('tzdata.')}
    for name in saved_modules:
        del sys.modules[name]
    sys.modules['tzdata'] = None          # a subsequent `import tzdata` raises
    zoneinfo.reset_tzpath([str(empty)])
    zoneinfo.ZoneInfo.clear_cache()
    try:
        # Guard the guard: if this ever resolves, the simulation has stopped
        # simulating and every assertion below is vacuous.
        with pytest.raises(Exception):
            zoneinfo.ZoneInfo('Asia/Amman')
        with caplog.at_level('WARNING'):
            boundary = metrics.business_day(ledger, CID)
            # Not a 500: the shop reports exactly what an unconfigured one does.
            assert metrics.revenue(ledger, CID, MAR10) == 190.0
    finally:
        sys.modules.pop('tzdata', None)
        sys.modules.update(saved_modules)
        zoneinfo.reset_tzpath(saved)
        zoneinfo.ZoneInfo.clear_cache()

    assert boundary.zone is None
    assert 'tzdata' in caplog.text, (
        'a missing tz DATABASE must not be reported as if it were a typo in '
        'the settings value -- the fix is a package, not an edit')


def test_a_database_with_no_retail_settings_table_still_reports(ledger):
    """Same tolerance, and the same narrowness, as `_tax_mode`: a hand-built
    fixture or a partially-migrated restore degrades to "unconfigured", not
    to a 500 on the dashboard."""
    conn = _new_db('nosettings')
    _seed_catalogue(conn)
    conn.execute("DROP TABLE retail_settings")
    conn.commit()
    _sale(conn, 'S1', 1, SALE_TOTAL, '2026-03-10 12:00:00', '2026-03-10T09:00:00+00:00')
    assert metrics.business_day(conn, CID).zone is None
    assert metrics.revenue(conn, CID, MAR10) == 100.0
    conn.close()


# ── the identity that stops one screen contradicting another ──────────────────

def test_every_breakdown_still_sums_back_to_the_kpi_under_a_zone(ledger):
    """metrics.py's central guarantee: the bars sum to the number printed
    above them. Moving the bucket key without moving the period predicate
    (or vice versa) breaks this silently -- a row inside the window buckets
    to a day outside it and simply disappears from the chart."""
    _configure(ledger, zone=SHOP_TZ, day_start_hour=4)
    total = metrics.revenue(ledger, CID, BOTH_DAYS)
    assert total == 290.0
    assert round(sum(r['revenue'] for r in metrics.revenue_by_day(ledger, CID, BOTH_DAYS)), 2) == total
    assert round(sum(metrics.revenue_by_hour(ledger, CID, BOTH_DAYS).values()), 2) == total
    assert round(sum(r['revenue'] for r in
                     metrics.revenue_by_payment_method(ledger, CID, BOTH_DAYS)), 2) == total


def test_the_branch_predicate_survives_the_move(ledger):
    """`_scope` grew a business-date expression; it must not have lost the
    branch leg on the way through."""
    _configure(ledger, zone=SHOP_TZ)
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
    NORMALISED to UTC by SQLite before the zone conversion is applied, so a
    device that writes its own offset rather than UTC is still filed
    correctly. It is also why the period predicate can never be a plain text
    comparison against the raw column -- '2026-03-10T01:30:00+03:00' sorts
    nowhere near the instant it denotes."""
    conn = _new_db('fmt-' + re.sub(r'\W', '_', written))
    _seed_catalogue(conn)
    # A local clock that is deliberately WRONG, so the assertion can only
    # pass by reading created_at_utc.
    _sale(conn, 'S1', 1, SALE_TOTAL, '2026-03-01 00:00:00', written)
    _configure(conn, zone=SHOP_TZ)
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


def test_business_now_converts_an_aware_clock_onto_the_shop_zone(ledger):
    """A caller that hands over an AWARE datetime gets the exact answer:
    22:30 UTC is already tomorrow in a UTC+3 shop."""
    _configure(ledger, zone=SHOP_TZ)
    utc_now = datetime(2026, 3, 9, 22, 30, tzinfo=timezone.utc)
    assert metrics.period_today(metrics.business_now(ledger, CID, utc_now)).start == '2026-03-10'


def test_business_now_follows_daylight_saving_rather_than_a_fixed_offset(dst_ledger):
    """The same 04:30Z the day-bucket proof uses, asked of the "today" card.

    In January the shop's clock reads 23:30 on the 14th; in June it reads
    00:30 on the 15th. A fixed offset gets one of the two wrong, and the
    symptom is the nastiest kind: the KPI card and the chart beside it
    describe different days, on exactly the days a shopkeeper is already
    confused about."""
    winter = datetime(2026, 1, 15, 4, 30, tzinfo=timezone.utc)
    summer = datetime(2026, 6, 15, 4, 30, tzinfo=timezone.utc)
    assert metrics.period_today(metrics.business_now(dst_ledger, CID, winter)).start == '2026-01-14'
    assert metrics.period_today(metrics.business_now(dst_ledger, CID, summer)).start == '2026-06-15'


def test_business_now_leaves_an_unconfigured_naive_clock_alone(ledger):
    """No configuration, no shift -- the reporting device's own clock, which
    is what every caller passes today."""
    naive = datetime(2026, 3, 10, 1, 30)
    assert metrics.business_now(ledger, CID, naive) == naive


def _far_shop_zone():
    """A shop zone a long way from whatever zone THIS machine is in.

    `Aura_Test/Plus14` and `Aura_Test/Minus11` are 25 hours apart, so no
    matter where the machine running these tests sits, at least one of them
    is more than twelve hours from it. Picking the far one is what makes the
    next test discriminating rather than accidentally trivial."""
    device = datetime(2026, 3, 10, 12, 0).astimezone().utcoffset()
    east = zoneinfo.ZoneInfo(FAR_EAST_TZ).utcoffset(datetime(2026, 3, 10, 12, 0))
    west = zoneinfo.ZoneInfo(FAR_WEST_TZ).utcoffset(datetime(2026, 3, 10, 12, 0))
    return (FAR_EAST_TZ if abs(east - device) >= abs(west - device) else FAR_WEST_TZ)


def test_business_now_converts_a_naive_device_clock_onto_the_shop_zone():
    """THE SECOND DEFECT THIS WAVE FIXES.

    `business_now` converted an AWARE `now` and left a NAIVE one completely
    alone -- but every caller in api/retail_api.py passes `datetime.now()`,
    which is naive. So on a shop that HAD declared its trading day, the
    "today" card asked for the REPORTING DEVICE's calendar date while every
    figure inside it was bucketed on the SHOP's. On a device in a different
    zone from the shop, the dashboard's today card reads 0.00 while the
    sale that was just rung sits one bucket over.

    Proved end to end rather than by restating the conversion: take a real
    instant, derive the naive wall clock this machine would have reported at
    that instant, ring a sale at it, and assert the money is inside the
    window `business_now` produces. Under the old code the two dates differ
    by design -- the guard assertion below refuses to let this test pass
    vacuously if the machine happens to sit in the shop's own zone."""
    shop_tz = _far_shop_zone()
    zone = zoneinfo.ZoneInfo(shop_tz)

    # Find an instant where the device's calendar date and the shop's differ.
    # With >12h between them this always exists; searching for it rather than
    # hardcoding one keeps the test honest on any machine.
    instant = None
    for hour in range(24):
        candidate = datetime(2026, 3, 10, hour, 30, tzinfo=timezone.utc)
        if candidate.astimezone().date() != candidate.astimezone(zone).date():
            instant = candidate
            break
    assert instant is not None, 'no instant separates the device and shop dates'

    device_now = instant.astimezone().replace(tzinfo=None)   # what datetime.now() returns
    shop_local = instant.astimezone(zone)
    assert device_now.date() != shop_local.date(), (
        'this test only discriminates when the reporting device and the shop '
        'are on different calendar dates')

    conn = _new_db('naive_now')
    _seed_catalogue(conn)
    _sale(conn, 'N1', 1, SALE_TOTAL,
          device_now.strftime('%Y-%m-%d %H:%M:%S'), instant.isoformat())
    _configure(conn, zone=shop_tz)

    window = metrics.period_today(metrics.business_now(conn, CID, device_now))
    assert window.start == shop_local.strftime('%Y-%m-%d')
    assert metrics.revenue(conn, CID, window) == 100.0, (
        "the dashboard's today card asked for the reporting device's date "
        "while the figures inside it were bucketed on the shop's")
    conn.close()


def test_business_now_and_the_day_bucket_cannot_disagree_about_today():
    """The invariant behind the test above, stated directly: whatever
    `business_now` says today is, the sale rung at that instant is in it.

    Asserted over a whole day of instants so a conversion that is right at
    noon and wrong at 23:00 (which is every off-by-one-offset bug) cannot
    slip through."""
    shop_tz = _far_shop_zone()
    zone = zoneinfo.ZoneInfo(shop_tz)
    conn = _new_db('now_agrees')
    _seed_catalogue(conn)
    _configure(conn, zone=shop_tz, day_start_hour=4)

    for hour in range(24):
        instant = datetime(2026, 5, 20, hour, 0, tzinfo=timezone.utc)
        device_now = instant.astimezone().replace(tzinfo=None)
        _sale(conn, f'H{hour}', 1, SALE_TOTAL,
              device_now.strftime('%Y-%m-%d %H:%M:%S'), instant.isoformat())
        window = metrics.period_today(metrics.business_now(conn, CID, device_now))
        assert metrics.revenue(conn, CID, window) >= 100.0, (
            f'the sale rung at {instant.isoformat()} fell outside the window '
            f'business_now built for that same instant')
        conn.execute("DELETE FROM sales WHERE sale_number=?", (f'H{hour}',))
        conn.commit()
    conn.close()


# ── source-level guard ────────────────────────────────────────────────────────

class _RecordingConn:
    """Delegating proxy that keeps every SQL string metrics.py executes."""

    def __init__(self, real):
        self._real = real
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append(sql)
        return self._real.execute(sql, params)


def _record_every_public_query(conn, period):
    rec = _RecordingConn(conn)
    metrics.summary(rec, CID, period)
    metrics.revenue_by_day(rec, CID, period)
    metrics.revenue_by_hour(rec, CID, period)
    metrics.revenue_by_payment_method(rec, CID, period)
    metrics.revenue_by_branch(rec, CID, period)
    metrics.revenue_by_employee(rec, CID, period)
    metrics.top_products(rec, CID, period)
    return rec.statements


def test_no_query_buckets_on_a_bare_local_wall_clock(ledger):
    """A behavioural pin, not a source grep: run every public metric under a
    configured shop zone and assert that no statement which touches
    `created_at` at all fails to also name `created_at_utc`.

    This is the regression that would be easiest to reintroduce -- one new
    breakdown copied from an old one, with `date(created_at)` in it, and
    every other test in this file still passes."""
    _configure(ledger, zone=SHOP_TZ, day_start_hour=4)
    offenders = [
        ' '.join(sql.split()) for sql in _record_every_public_query(ledger, BOTH_DAYS)
        if 'created_at' in sql and 'created_at_utc' not in sql
    ]
    assert not offenders, (
        "these statements still read a raw device wall clock:\n  "
        + "\n  ".join(offenders))


def test_no_query_ever_asks_sqlite_for_the_server_machines_own_zone(ledger):
    """SQLite's only timezone modifiers are 'localtime' and 'utc', and BOTH
    mean the zone of the process running the query -- a shop laptop, a
    droplet, a phone. Reading the reporting machine's zone is the original
    defect wearing a different hat: it moves every bucket the day the report
    is opened somewhere else.

    Pinned under a configured shop zone AND an unconfigured one, because the
    tempting place to reach for 'localtime' is the unconfigured fallback."""
    for kwargs in ({'zone': SHOP_TZ, 'day_start_hour': 4}, {}):
        _configure(ledger, **kwargs)
        offenders = [' '.join(sql.split()) for sql in _record_every_public_query(ledger, BOTH_DAYS)
                     if "'localtime'" in sql or "'utc'" in sql]
        assert not offenders, (
            'these statements read the reporting machine\'s own zone:\n  '
            + '\n  '.join(offenders))


def test_the_scope_predicate_names_the_utc_column(ledger):
    """The narrow version of the test above, aimed straight at the one
    fragment every figure in the module is built on."""
    boundary = metrics.BusinessDay(zone=zoneinfo.ZoneInfo(SHOP_TZ), day_start_hour=4)
    sql, params = metrics._scope(CID, MAR10, boundary)
    assert 'created_at_utc' in sql
    assert params == [CID, '2026-03-10', '2026-03-10']
    aliased, _ = metrics._scope(CID, MAR10, boundary, alias='s')
    assert 's.created_at_utc' in aliased


def test_the_period_predicate_and_the_day_bucket_are_one_expression(ledger):
    """The structural guarantee, asserted rather than trusted.

    Under a zone with transitions inside the window, the day key is built
    from a generated table of offset segments. If the WHERE clause built its
    own table and the GROUP BY built another, a row could pass the filter
    and bucket to a day outside it -- gone from the chart, still counted in
    the KPI above it. They must be the SAME STRING, not merely equivalent
    ones."""
    boundary = metrics.BusinessDay(zone=zoneinfo.ZoneInfo(DST_TZ), day_start_hour=0)
    window = metrics.Period('2026-01-01', '2026-12-31', 364)
    day_key = metrics._day_key(boundary, window)
    sql, _ = metrics._scope(CID, window, boundary)
    assert day_key in sql
    assert sql.count(day_key) == 2, 'both ends of the period predicate use it'
