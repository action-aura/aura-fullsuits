"""
Aura Retail -- every report route and the dashboard KPI must read ONE clock.

THE DEFECT THIS FILE EXISTS FOR. `core/retail/metrics.py` buckets every row on
the SHOP's business date (schema v13's `created_at_utc`, converted through the
shop's declared IANA zone and rolled back by the trading day's start hour).
`dashboard_stats()` builds its window the same way -- `period_today(
business_now(conn, cid, now))`. Every OTHER report route built its window
straight from `datetime.now()`, the REPORTING DEVICE's wall clock.

Two clocks, one page. A shop three hours ahead of the device reading it, at
device-local 22:30, is already at 01:30 the NEXT trading day. A sale rung at
that instant buckets to business date D+1, while `period_days(14, device_now)`
asks for `[D-14 .. D]`. The sale is outside the window by one day, so:

    /dashboard/stats     today_sales = 100.00
    /reports/summary     revenue     = 0.00
    /reports/sales-trend {"data": [], "labels": []}

Money on one panel, gone from the panel beside it, no error anywhere. This is
NOT a three-hour edge case: the row falls out of a FOURTEEN-DAY window,
because being off by one day at the END of a window drops everything past it.

WHY THIS IS A NEW FILE RATHER THAN MORE ASSERTIONS IN THE CONSISTENCY SUITE.
`retail_metrics_consistency_test.py` cannot make this assertion. Its fixture
never sets `business_timezone`, so every shop it builds reads back
`BusinessDay(zone=None)` -- and with no zone `business_now()` is the identity
function on a naive clock. Device clock and shop clock are EQUAL BY
CONSTRUCTION there, so a route on the wrong clock is indistinguishable from a
route on the right one. That file's own frozen-clock test had the mirror-image
problem: it asserted every route returned EMPTY, which is precisely the
signature of the bug, so it could only fail if a route returned MORE data.

Everything below therefore does the two things that suite cannot:
  * declares a REAL shop zone, and
  * puts the reporting device on a DIFFERENT one,
and then asserts the routes agree with each other AND with the KPI card.

NOTE ON THIS ENVIRONMENT. `zoneinfo` is stdlib and imports fine, but the tz
DATABASE it reads is not bundled with CPython on Windows and `tzdata` is in
none of requirements/*.txt -- on this machine `available_timezones()` returns
ZERO zones, so `parse_business_zone('Asia/Amman')` correctly returns None and
no shop can declare a clock at all. That is a real, reportable deployment
blocker and not something to paper over, so (exactly as
retail_reports_by_employee_shop_clock_test.py already does) these tests
install a fixed-offset stand-in through `parse_business_zone` -- the one seam a
real IANA lookup would come through -- and exercise every other line of the
real path: business_day -> BusinessDay.zone -> the generated SQL -> the
route's window. `test_the_zone_stub_really_reaches_the_metrics_boundary`
below refuses to let that stand-in silently fail to resolve, because an
unconfigured shop passes every assertion here for the worst possible reason.

(Filename prefix is this wave's file-ownership convention, not the subject.)

Run:
    pytest products/retail/tests/retail_report_clock_agreement_test.py -v
"""
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_reportclock_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

import api.retail_api as retail_api_module  # noqa: E402
from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from core.retail import metrics  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'

#: The device's wall clock, frozen. 22:30 is chosen so that a shop three hours
#: ahead has ALREADY ROLLED OVER into the next calendar day (01:30) -- that
#: rollover is the entire mechanism, because `period_days(n, now)` ends on
#: `now.date()` and a business date one day PAST the end of the window is
#: excluded from it no matter how many days wide the window is.
FROZEN_DEVICE_LOCAL = datetime(2026, 8, 19, 22, 30, 0)

#: The runner's OWN offset at that instant, read rather than assumed: this has
#: to give the same answer on a UTC CI box and on a UTC+3 dev machine.
DEVICE_OFFSET_MINUTES = int(
    FROZEN_DEVICE_LOCAL.astimezone().utcoffset().total_seconds() // 60)

#: The shop, three hours east of whatever the device is on.
SHOP_OFFSET_MINUTES = DEVICE_OFFSET_MINUTES + 180

#: The same instant on each of the two clocks.
SHOP_LOCAL_NOW = FROZEN_DEVICE_LOCAL + timedelta(minutes=SHOP_OFFSET_MINUTES - DEVICE_OFFSET_MINUTES)

#: The one sale. A round number with no tax, so `sales.total`,
#: `metrics.revenue()` and `top_products()`'s re-priced line are the same
#: figure and any disagreement below is about the WINDOW, never about money
#: arithmetic (retail_pricing_test.py owns that).
SALE_AMOUNT = 100.0

#: Every report route is asked for the same window the desktop Reports page
#: uses by default. Deliberately WIDE: it makes the point that this is not a
#: boundary-hour rounding quibble.
DAYS = 14


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── a stand-in zone, for a machine with no tz database ───────────────────────

class _FixedZone(tzinfo):
    """See the module docstring. Deliberately NOT `datetime.timezone(...)`:
    metrics caches its zone-offset walk keyed on the zone OBJECT, and a `key`
    attribute is what `zoneinfo.ZoneInfo` exposes and what the settings route
    stores, so the stand-in has to carry one too."""

    def __init__(self, minutes, key):
        self._offset = timedelta(minutes=minutes)
        self.key = key

    def utcoffset(self, dt):
        return self._offset

    def dst(self, dt):
        return timedelta(0)

    def tzname(self, dt):
        return self.key

    def __repr__(self):  # pragma: no cover - only rendered inside a failure
        return f'_FixedZone({self.key!r}, {int(self._offset.total_seconds()) // 60:+d}m)'


#: Synthetic names, resolvable ONLY through the stub below -- deliberately not
#: real IANA keys, so nothing here can accidentally pass by resolving a genuine
#: zone on a machine that DOES have tzdata.
_STUB_ZONES = {}


def _stub_zone(offset_minutes):
    key = f'Aura/Report{offset_minutes:+d}'
    if key not in _STUB_ZONES:
        _STUB_ZONES[key] = _FixedZone(offset_minutes, key)
    return key


_real_parse_business_zone = metrics.parse_business_zone


def _parse_with_stubs(raw):
    if raw is not None and str(raw).strip() in _STUB_ZONES:
        return _STUB_ZONES[str(raw).strip()]
    return _real_parse_business_zone(raw)


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return FROZEN_DEVICE_LOCAL.astimezone().astimezone(tz)
        return FROZEN_DEVICE_LOCAL


def _run(fn):
    """The device clock frozen and the synthetic zones resolvable, restored
    afterwards so no other module in this process ever sees either patch."""
    original_dt = retail_api_module.datetime
    retail_api_module.datetime = _FrozenDateTime
    metrics.parse_business_zone = _parse_with_stubs
    try:
        return fn()
    finally:
        retail_api_module.datetime = original_dt
        metrics.parse_business_zone = _real_parse_business_zone


# ── fixture ──────────────────────────────────────────────────────────────────

def _make_shop(day_start_hour=None, offset_minutes=None):
    """One company, one branch, one product, one admin -- and a DECLARED shop
    clock. The declaration is the whole point: without it `business_day()`
    returns zone=None, `business_now()` degenerates to the identity function,
    and every assertion in this file would hold over the broken code."""
    company_id = str(uuid.uuid4())
    email = f"rc-{uuid.uuid4().hex[:10]}@test.local"
    password = "ReportClockPW1"
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, uid, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,?,0)",
        (user_id, str(uuid.uuid4()), company_id, f"EMP-{uuid.uuid4().hex[:6].upper()}", email,
         hash_password(password), "admin", "active"))
    conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
                 (str(uuid.uuid4()), user_id, "retail", "full"))
    user_accounts.seed_capabilities_for_user(conn, user_id, "admin")
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_data(as_text=True)

    rconn = get_retail_conn()
    cur = rconn.cursor()
    cur.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = cur.lastrowid
    product_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate,status) "
        "VALUES (?,?,?,'Report Clock Item',0,?,0,'active')",
        (product_id, company_id, f"RC-{uuid.uuid4().hex[:6]}", SALE_AMOUNT))
    rconn.commit()
    rconn.close()

    client.get(f'{API}/settings/tax')     # creates retail_settings / doc_sequences

    shop = {'company_id': company_id, 'branch_id': branch_id,
            'product_id': product_id, 'client': client}
    _declare_clock(shop,
                   offset_minutes=SHOP_OFFSET_MINUTES if offset_minutes is None else offset_minutes,
                   day_start_hour=day_start_hour)
    return shop


def _declare_clock(shop, offset_minutes, day_start_hour=None):
    conn = get_retail_conn()
    conn.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?) "
                 "ON CONFLICT(company_id,skey) DO UPDATE SET svalue=excluded.svalue",
                 (shop['company_id'], metrics.BUSINESS_TIMEZONE_SETTING, _stub_zone(offset_minutes)))
    if day_start_hour is not None:
        conn.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?) "
                     "ON CONFLICT(company_id,skey) DO UPDATE SET svalue=excluded.svalue",
                     (shop['company_id'], metrics.BUSINESS_DAY_START_SETTING, str(day_start_hour)))
    conn.commit()
    conn.close()


def _sale_at_shop_local(shop, shop_local, amount=SALE_AMOUNT, offset_minutes=None):
    """One completed sale, with its line, whose true instant lands at
    `shop_local` on the SHOP's wall clock.

    CALLERS PASS THE SHOP'S CURRENT INSTANT, not "a few minutes ago". That is
    deliberate and was a real bug in an earlier draft of this file: a shop
    whose wall clock has just crossed midnight (one of the offsets in the
    spread below lands on exactly 00:00) puts "fifteen minutes ago" on the
    PREVIOUS business day, so the dashboard's own today-card correctly reported
    0.00 and the test read that as a defect. The sale has to be inside the
    window the KPI is asking for, or the comparison is between two different
    days rather than between two clocks.

    `created_at_utc` carries the real instant (what a v13 write stamps) and
    `created_at` the DEVICE's local rendering of that same instant -- a
    different wall-clock reading, because the device is in another zone. BOTH
    are written so that nothing here can accidentally prove itself through the
    pre-v13 `COALESCE(..., created_at)` fallback arm: if a route were reading
    the device column, these tests would still see the wrong day."""
    offset = SHOP_OFFSET_MINUTES if offset_minutes is None else offset_minutes
    instant = (shop_local - timedelta(minutes=offset)).replace(tzinfo=timezone.utc)
    device_local = (instant + timedelta(minutes=DEVICE_OFFSET_MINUTES)).replace(tzinfo=None)
    conn = get_retail_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sales (company_id,sale_number,branch_id,customer_id,cashier,subtotal,"
        "discount_amount,tax_amount,total,amount_paid,payment_method,status,created_at,"
        "actor_user_uid,created_at_utc,uid) "
        "VALUES (?,?,?,NULL,'POS',?,0,0,?,?,'cash','completed',?,NULL,?,?)",
        (shop['company_id'], f'RC-{uuid.uuid4().hex[:12]}', shop['branch_id'], amount, amount,
         amount, device_local.strftime('%Y-%m-%d %H:%M:%S'), instant.isoformat(), str(uuid.uuid4())))
    sale_id = cur.lastrowid
    cur.execute(
        "INSERT INTO sale_items (sale_id,product_id,quantity,unit_price,discount_pct,tax_rate,line_total) "
        "VALUES (?,?,1,?,0,0,?)",
        (sale_id, shop['product_id'], amount, amount))
    conn.commit()
    conn.close()
    return sale_id


def _get(shop, path):
    r = _run(lambda: shop['client'].get(f'{API}{path}'))
    assert r.status_code == 200, (path, r.status_code, r.get_data(as_text=True))
    return r.get_json()


def _report_totals(shop, days=DAYS):
    """Every period-taking report surface, reduced to the ONE number they all
    claim to be reporting: net revenue over the same window. Keyed by route so
    a failure names the panel rather than an index."""
    return {
        '/reports/summary':
            _get(shop, f'/reports/summary?days={days}')['data']['revenue'],
        '/reports/sales-trend':
            round(sum(_get(shop, f'/reports/sales-trend?days={days}')['data']), 2),
        '/reports/payment-methods':
            round(sum(r['revenue'] for r in
                      _get(shop, f'/reports/payment-methods?days={days}')['data']), 2),
        '/reports/top-products':
            round(sum(_get(shop, f'/reports/top-products?days={days}')['revenue']), 2),
        '/reports/by-branch':
            round(sum(_get(shop, f'/reports/by-branch?days={days}')['data']), 2),
        '/reports/by-employee':
            round(sum(r['revenue'] for r in
                      _get(shop, f'/reports/by-employee?days={days}')['data']), 2),
    }


# ── 0. guards on the fixture itself ──────────────────────────────────────────
#
# Three ways this file could pass while proving nothing, each closed here
# rather than assumed. Every one of them is a state in which the device clock
# and the shop clock agree -- and where they agree, a route on the wrong one
# is indistinguishable from a route on the right one.

def test_the_scenario_this_file_is_built_on_is_reproducible_on_this_runner():
    """The shop must be EAST of the device, far enough that the two are on
    different calendar days at the frozen instant. West would merely misfile
    the sale a day early -- still inside a 14-day window, so no money would go
    missing and every assertion below would pass over the broken code."""
    assert -14 * 60 <= SHOP_OFFSET_MINUTES <= 14 * 60, (
        f"this runner is on UTC{DEVICE_OFFSET_MINUTES // 60:+d}, so a shop three hours "
        f"further east ({SHOP_OFFSET_MINUTES} minutes) is not a real offset -- the "
        f"scenario needs re-anchoring for this machine.")
    assert SHOP_LOCAL_NOW.date() > FROZEN_DEVICE_LOCAL.date(), (
        f"device reads {FROZEN_DEVICE_LOCAL} and the shop reads {SHOP_LOCAL_NOW}; they are "
        f"on the SAME calendar day, so this file cannot tell the two clocks apart")


def test_the_zone_stub_really_reaches_the_metrics_boundary():
    """A stand-in that failed to resolve would leave every shop below
    UNCONFIGURED -- and an unconfigured shop has zone=None, which makes
    `business_now()` the identity function. Every agreement assertion in this
    file would then hold trivially over the broken code."""
    shop = _make_shop()
    boundary = _run(lambda: _business_day_of(shop))
    assert boundary.zone is not None, (
        'the shop read back as unconfigured -- every assertion in this file '
        'would be vacuous')
    assert int(boundary.zone.utcoffset(FROZEN_DEVICE_LOCAL).total_seconds()) // 60 \
        == SHOP_OFFSET_MINUTES


def _business_day_of(shop):
    conn = get_retail_conn()
    try:
        return metrics.business_day(conn, shop['company_id'])
    finally:
        conn.close()


def test_the_two_clocks_really_disagree_about_what_day_it_is():
    """THE precondition, asserted against the real code rather than against
    arithmetic in this file: `business_now()` and a bare `datetime.now()` must
    name DIFFERENT dates for the frozen instant. If they ever agree, every
    test below is measuring nothing and has to be re-anchored."""
    shop = _make_shop()

    def check():
        conn = get_retail_conn()
        try:
            shop_now = metrics.business_now(conn, shop['company_id'],
                                            retail_api_module.datetime.now())
        finally:
            conn.close()
        return shop_now

    shop_now = _run(check)
    device_window = metrics.period_days(DAYS, FROZEN_DEVICE_LOCAL)
    shop_window = metrics.period_days(DAYS, shop_now)
    assert shop_window.end != device_window.end, (
        f"the shop clock and the device clock produced the SAME window "
        f"({shop_window}); this file cannot detect a route on the wrong one")
    assert shop_window.end == SHOP_LOCAL_NOW.strftime('%Y-%m-%d')


def test_the_sale_really_buckets_onto_the_day_the_device_window_excludes():
    """The other half of the precondition. It is not enough that the two
    windows differ -- the money has to land in the gap between them, or a
    route on the device clock would still report it and this file would go
    green over the defect."""
    shop = _make_shop()
    _sale_at_shop_local(shop, SHOP_LOCAL_NOW)

    device_window = metrics.period_days(DAYS, FROZEN_DEVICE_LOCAL)
    revenue_in_device_window = _run(
        lambda: _revenue_over(shop, device_window))
    assert revenue_in_device_window == 0.0, (
        f"the sale is INSIDE the device's own window {device_window}, so a route "
        f"built on the device clock would report it correctly and nothing below "
        f"would be measuring the defect")

    shop_window = metrics.period_days(DAYS, SHOP_LOCAL_NOW)
    assert _run(lambda: _revenue_over(shop, shop_window)) == SALE_AMOUNT


def _revenue_over(shop, period):
    conn = get_retail_conn()
    try:
        return metrics.revenue(conn, shop['company_id'], period)
    finally:
        conn.close()


# ── 1. the KPI and every report panel must name the same figure ──────────────

def test_every_report_route_reports_the_money_the_dashboard_kpi_counted():
    """THE headline.

    One shop, one 100.00 sale, rung at 01:15 on the shop's clock while the
    device still reads 22:15 the previous evening. The dashboard KPI counts it
    (dashboard_stats already converts through `business_now`). Every report
    route must too -- they are describing the SAME shop over a window that
    CONTAINS the KPI's single day.

    Asserted as a POSITIVE: the figure must be 100.00 on every panel. The
    superseded version of this test asserted every route returned EMPTY, which
    is the bug's own signature -- it could only fail if a route returned MORE
    data, never less, so it stayed green while five routes dropped this
    sale."""
    shop = _make_shop()
    _sale_at_shop_local(shop, SHOP_LOCAL_NOW)

    kpi = _get(shop, '/dashboard/stats')['data']['today_sales']
    assert kpi == SALE_AMOUNT, (
        f"the dashboard KPI itself is wrong ({kpi}); fix that before reading "
        f"anything below, because it is the figure the reports are compared against")

    totals = _report_totals(shop)
    disagreed = {route: value for route, value in totals.items() if value != kpi}
    assert not disagreed, (
        f"the dashboard KPI card reads {kpi:.2f} and these panels, over a "
        f"{DAYS}-day window that CONTAINS that day, do not agree with it:\n  "
        + "\n  ".join(f"{route}: {value}" for route, value in sorted(disagreed.items()))
        + f"\n\nThe device reads {FROZEN_DEVICE_LOCAL} and the shop reads "
          f"{SHOP_LOCAL_NOW}. A route that builds its window from `datetime.now()` "
          f"asks for a window ending "
          f"{FROZEN_DEVICE_LOCAL.strftime('%Y-%m-%d')}, one day BEFORE the business "
          f"date this sale belongs to -- so the sale falls out of a fourteen-day "
          f"window entirely. Move the route onto "
          f"`metrics.business_now(conn, cid, datetime.now())`.")


def test_the_report_panels_agree_with_each_other():
    """The same invariant without the dashboard in it at all, so this still
    fails if dashboard_stats ever regresses onto the device clock too and the
    whole page becomes consistently wrong. Six panels on one screen, one
    `days` control, one figure."""
    shop = _make_shop()
    _sale_at_shop_local(shop, SHOP_LOCAL_NOW)

    totals = _report_totals(shop)
    assert len(set(totals.values())) == 1, (
        "the panels of ONE Reports page, over ONE window, disagree:\n  "
        + "\n  ".join(f"{route}: {value}" for route, value in sorted(totals.items())))
    assert set(totals.values()) == {SALE_AMOUNT}, (
        f"the panels agree with each other but on the wrong figure -- they all "
        f"report {totals['/reports/summary']} for a shop that took "
        f"{SALE_AMOUNT:.2f}: {totals}")


#: The eastward shift needed to keep the scenario alive once a 04:00 trading
#: day is in play. At +3h the shop reads 01:30, and 01:30 MINUS a four-hour
#: day start lands back on the device's own date -- the two clocks agree again
#: and the test below would be vacuous. +6h puts the shop at 04:30, past its
#: own opening hour, so the business date is genuinely the day the device has
#: not reached. Derived here rather than hand-waved, and asserted in the test.
DAY_START_HOUR = 4
LATE_SHOP_OFFSET_MINUTES = DEVICE_OFFSET_MINUTES + 360


def test_a_trading_day_that_starts_at_04_00_moves_the_window_too():
    """`business_day_start_hour` is the other half of the shop clock and the
    WINDOW has to follow it, not only the bucketing.

    The shop opens at 04:00 and its wall clock reads 04:30, so the trading day
    that is now running opened half an hour ago -- on a calendar date the
    reporting device, still on the previous evening, has not reached. A sale
    rung at 04:15 belongs to that day. With the day-start shift applied to the
    ROWS but the window still built from the device clock, the sale sits one
    day past the end of the window that asks for it."""
    shop = _make_shop(day_start_hour=DAY_START_HOUR,
                      offset_minutes=LATE_SHOP_OFFSET_MINUTES)
    shop_now = FROZEN_DEVICE_LOCAL + timedelta(
        minutes=LATE_SHOP_OFFSET_MINUTES - DEVICE_OFFSET_MINUTES)

    # The precondition, asserted rather than assumed -- see this file's §0.
    # A day start that pulled the business date back onto the device's own
    # date would make every assertion below hold over the broken code.
    business_date = (shop_now - timedelta(hours=DAY_START_HOUR)).date()
    assert business_date > FROZEN_DEVICE_LOCAL.date(), (
        f"shop wall clock {shop_now} minus a {DAY_START_HOUR}h day start is business "
        f"date {business_date}, which the device's own window (ending "
        f"{FROZEN_DEVICE_LOCAL.date()}) already covers -- this test would prove nothing")

    _sale_at_shop_local(shop, shop_now, offset_minutes=LATE_SHOP_OFFSET_MINUTES)

    kpi = _get(shop, '/dashboard/stats')['data']['today_sales']
    assert kpi == SALE_AMOUNT, kpi
    totals = _report_totals(shop)
    assert set(totals.values()) == {kpi}, (kpi, totals)


def test_an_unconfigured_shop_is_unchanged_by_all_of_this():
    """The regression guard on the default, which is what every install in the
    field is today. A shop that has declared no zone keeps exactly the
    behaviour it had before business dates existed -- the device IS the shop --
    so moving the routes onto `business_now` may not alter a single figure
    there. `business_now` returns a naive clock through untouched when
    `boundary.zone is None`, and this is what pins that."""
    shop = _make_shop()
    conn = get_retail_conn()
    conn.execute("DELETE FROM retail_settings WHERE company_id=? AND skey=?",
                 (shop['company_id'], metrics.BUSINESS_TIMEZONE_SETTING))
    conn.commit()
    conn.close()
    assert _run(lambda: _business_day_of(shop)).zone is None

    # Rung at the DEVICE's wall clock this time, which is what an unconfigured
    # shop's own clock is by definition.
    _sale_at_shop_local(shop, FROZEN_DEVICE_LOCAL, offset_minutes=DEVICE_OFFSET_MINUTES)

    kpi = _get(shop, '/dashboard/stats')['data']['today_sales']
    assert kpi == SALE_AMOUNT, kpi
    totals = _report_totals(shop)
    assert set(totals.values()) == {kpi}, (kpi, totals)


def test_the_agreement_holds_across_a_spread_of_shop_offsets():
    """Not one lucky offset. Each of these is a real shop somewhere.

    The two directions do DIFFERENT jobs and the difference is worth being
    explicit about, because counting the westward ones as proof would be the
    same vacuity this file exists to clear out:

      * EASTWARD of the device (the device still on the previous evening at
        22:30) the shop has rolled into the next calendar day, so its business
        date is one PAST the end of a device-clock window and the money
        vanishes. These are the cases that detect the defect.
      * WESTWARD the shop is still on the device's own date, so a device-clock
        window happens to contain the sale anyway. Those cases cannot fail on
        the original defect and are here only as a regression guard: a "fix"
        that converted in the wrong direction would push these OUT of the
        window, and they would then be the ones that catch it.

    `diverged` counts the first kind, so this loop cannot quietly degrade into
    only the second."""
    diverged = []
    for delta_minutes in (-330, -180, -90, 90, 180, 330):
        offset = DEVICE_OFFSET_MINUTES + delta_minutes
        if not -14 * 60 <= offset <= 14 * 60:
            continue
        shop = _make_shop(offset_minutes=offset)
        shop_now = FROZEN_DEVICE_LOCAL + timedelta(minutes=delta_minutes)
        if shop_now.date() != FROZEN_DEVICE_LOCAL.date():
            diverged.append(delta_minutes)
        _sale_at_shop_local(shop, shop_now, offset_minutes=offset)

        kpi = _get(shop, '/dashboard/stats')['data']['today_sales']
        totals = _report_totals(shop)
        assert kpi == SALE_AMOUNT, (delta_minutes, kpi)
        assert set(totals.values()) == {kpi}, (
            f"shop offset {offset:+d} (device {DEVICE_OFFSET_MINUTES:+d}): KPI {kpi}, "
            f"panels {totals}")

    assert len(diverged) >= 3, (
        f"only {len(diverged)} of the offsets in this spread actually put the shop on a "
        f"different calendar day from the device ({diverged}), so most of this loop is "
        f"asserting agreement between two clocks that already agree. Re-anchor "
        f"FROZEN_DEVICE_LOCAL (currently {FROZEN_DEVICE_LOCAL.time()}) closer to midnight.")


# ── 2. the seam itself, structurally ─────────────────────────────────────────

def test_no_report_route_builds_a_window_from_the_bare_device_clock():
    """THE RULE, GREPPED FOR DIRECTLY, so the NEXT route to be added is caught
    by machinery rather than by somebody noticing.

    Walks api/retail_api.py's syntax tree for calls to any `metrics.period_*`
    constructor and requires the `now` argument to have come through
    `metrics.business_now(...)` -- never a bare `datetime.now()`. The
    behavioural tests above prove the routes that exist today; this one
    prevents the seam from reopening on a route that does not exist yet, which
    is exactly how five of them ended up on the wrong clock after
    dashboard_stats was already fixed.

    A LOCAL NAME IS ACCEPTED, but only if it was itself assigned from a
    `business_now(...)` call in the same function. That is not a loophole, it
    is the pattern the fixed routes use and MUST use: `dashboard_stats` reads
    the clock ONCE into `shop_now` and feeds it to three constructors, because
    three separate reads could straddle midnight and put the KPI card, the
    yesterday comparison and the month-to-date figure on different days. A
    scanner that only accepted the inline call shape would push every
    multi-period route back into that bug to keep this test quiet.

    `period_for_day` is excluded: it takes a DATE the caller already chose
    (`?date=` on a route), not a clock reading."""
    import ast

    source = Path(retail_api_module.__file__).read_text(encoding='utf-8')
    tree = ast.parse(source)

    clock_taking = {'period_days', 'period_today', 'period_yesterday',
                    'period_all_time', 'period_month_to_date'}

    def _is_business_now(node):
        return (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'business_now')

    def _converted_names(scope):
        """Names bound to a `business_now(...)` result inside one function.

        Deliberately a per-scope map and not a module-wide one: two functions
        may both call a local `now` and only one of them may have converted
        it, so a global name set would let the unconverted one inherit the
        other's clearance."""
        names = set()
        for node in ast.walk(scope):
            if isinstance(node, ast.Assign) and _is_business_now(node.value):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif (isinstance(node, ast.AnnAssign) and node.value is not None
                    and _is_business_now(node.value) and isinstance(node.target, ast.Name)):
                names.add(node.target.id)
        return names

    #: (scope node, names converted inside it). Module last, so the innermost
    #: enclosing function wins when a call sits inside one.
    scopes = [(fn, _converted_names(fn)) for fn in ast.walk(tree)
              if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))]
    scopes.append((tree, _converted_names(tree)))

    def _cleared_names_around(call):
        for scope, names in scopes:
            if any(node is call for node in ast.walk(scope)):
                return names
        return set()

    offenders = []
    seen = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in clock_taking:
            continue
        seen.append(node.lineno)
        # `now` is the last positional argument of every constructor here.
        if not node.args:
            continue
        now_arg = node.args[-1]
        converted = _is_business_now(now_arg) or (
            isinstance(now_arg, ast.Name) and now_arg.id in _cleared_names_around(node))
        if not converted:
            offenders.append(f'line {node.lineno}: {ast.unparse(node)}')

    assert seen, (
        'the AST scan matched no period constructor at all in api/retail_api.py -- '
        'it is not reading what it thinks it is, and would pass over every '
        'possible violation')
    assert not offenders, (
        "these window constructors are fed a clock that has NOT been converted "
        "onto the shop's business day, while core/retail/metrics.py buckets every "
        "row that answers them onto it. A shop east of the device reading it "
        "loses every sale rung after the device's midnight out of the whole "
        "window.\n\nPass "
        "`metrics.business_now(conn, cid, datetime.now())` instead of "
        "`datetime.now()`:\n  " + "\n  ".join(offenders)
        + "\n\n(If a call site legitimately has no company connection to convert "
          "with, it does not belong in this file's report surface -- say so on "
          "the route and exclude it here deliberately, in a diff.)")
