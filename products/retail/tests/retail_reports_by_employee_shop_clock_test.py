"""
Aura Retail -- the dashboard's KPI card and its hourly chart must read ONE
clock, and the shop clock must have a way to be set.

(Filename prefix is this wave's file-ownership convention, not the subject.)

TWO DEFECTS, one cause.

1. THE CONTRADICTION. `metrics.revenue_by_hour()` buckets on the SHOP's wall
   clock -- `strftime('%H', <the instant converted through the shop's zone>)`.
   `dashboard_stats()` then truncated that dict to `range(current_hour + 1)`
   using `int(now.strftime('%H'))`, which is the REPORTING DEVICE's hour, and
   built its periods from the device's date. Two clocks, one screen. A shop on
   +03:00 read by a device left on UTC drops every sale rung in the last three
   shop-hours out of the chart while the Revenue KPI directly above it still
   counts them: KPI 150.00, bars summing to 50.00, and 100.00 gone with no
   error anywhere.

   Before business dates existed both sides read the device clock and COULD
   NOT disagree. The bucketing fix opened the seam.

   The existing consistency suite cannot see this: it sums the RAW dict
   metrics returns and never applies the route's truncation, so the missing
   money is still inside the number it sums. Every assertion here sums
   `hourly_data` -- the array the route actually ships -- against
   `today_sales`, the KPI beside it.

2. THE SETTING HAS NO WRITER. `business_timezone` and
   `business_day_start_hour` are read by `metrics.business_day()` and were
   written by NOTHING in this repository -- neither `/settings/credit`'s
   allowlist nor `/settings/tax`'s. They could only be set by hand-editing
   retail.db, so on every install in the field the shop clock is permanently
   undeclared and the whole business-date feature is unreachable code.

NOTE ON THIS ENVIRONMENT, because it changes how these tests are written.
`zoneinfo` is stdlib and imports fine, but the tz DATABASE it reads is not
bundled with CPython on Windows, and `tzdata` is in none of
requirements/*.txt. On this machine `zoneinfo.available_timezones()` returns
ZERO zones, so `metrics.parse_business_zone('Asia/Amman')` correctly returns
None and no shop can declare a clock at all. That is a real, reportable
deployment blocker and NOT something to paper over -- so the clock tests below
install a fixed-offset stand-in zone through `parse_business_zone`, the one
seam a real lookup would come through, and exercise every other line of the
real path (business_day -> BusinessDay.zone -> the generated SQL -> the
route's axis). The write-path tests drive the real route and prove its
decision follows `parse_business_zone`'s verdict whichever way that verdict
goes, so they hold on a machine with tzdata and on one without.

Run:
    pytest products/retail/tests/retail_reports_by_employee_shop_clock_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_shopclock_"))
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

STATS_URL = '/api/sub/retail/dashboard/stats'
SETTINGS_URL = '/api/sub/retail/settings/business-day'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── the frozen device clock ──────────────────────────────────────────────────
#
# One fixed wall-clock reading for the reporting device, so nothing here
# depends on what time the suite happens to run. 10:00 is chosen so a shop
# three hours AHEAD sits at 13:00 -- late enough that the old truncation
# visibly cut real sales off, early enough that adding three hours cannot wrap
# past midnight and make a wrong answer look right.

FROZEN_DEVICE_LOCAL = datetime(2026, 8, 19, 10, 0, 0)

#: The RUNNER's own offset at that instant, read at runtime rather than
#: assumed: this suite has to give the same answer on a UTC CI box and on a
#: UTC+3 dev machine, and "the device clock" means whatever this machine says.
DEVICE_OFFSET_MINUTES = int(
    FROZEN_DEVICE_LOCAL.astimezone().utcoffset().total_seconds() // 60)

#: The shop, deliberately three hours ahead of whatever the device is on.
SHOP_OFFSET_MINUTES = DEVICE_OFFSET_MINUTES + 180


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return FROZEN_DEVICE_LOCAL.astimezone().astimezone(tz)
        return FROZEN_DEVICE_LOCAL


class _FixedZone(tzinfo):
    """A stand-in for a real IANA zone, for a machine that has no tz database.

    Deliberately NOT `datetime.timezone(...)`: metrics caches its zone-offset
    walk under `getattr(zone, 'key', None) or id(zone)`, and two different
    stand-ins whose ids happened to be recycled would silently share a cache
    entry. A `key` makes each one distinct and matches what `zoneinfo.ZoneInfo`
    exposes, which is also the attribute the settings route stores.

    A fixed offset cannot express daylight saving -- that is the entire reason
    the setting moved to IANA names in the first place -- so nothing here
    claims to test a DST transition. What it does test is every line between
    "the zone resolved" and "the number on the screen".
    """

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


#: Synthetic zone names, resolvable ONLY through the stub below. Deliberately
#: not real IANA keys, so nothing here can accidentally pass by resolving a
#: genuine zone on a machine that does have tzdata.
_STUB_ZONES = {}


def _stub_zone(offset_minutes):
    key = f'Aura/Test{offset_minutes:+d}'
    zone = _STUB_ZONES.get(key)
    if zone is None:
        zone = _STUB_ZONES[key] = _FixedZone(offset_minutes, key)
    return key


_real_parse_business_zone = metrics.parse_business_zone


def _parse_with_stubs(raw):
    if raw is not None and str(raw).strip() in _STUB_ZONES:
        return _STUB_ZONES[str(raw).strip()]
    return _real_parse_business_zone(raw)


def _run(fn):
    """Everything the dashboard tests need in place: the device clock frozen,
    and the synthetic zones resolvable through the one function a real lookup
    would come through. Restored afterwards so no other file in the process
    ever sees either patch."""
    original_dt = retail_api_module.datetime
    retail_api_module.datetime = _FrozenDateTime
    metrics.parse_business_zone = _parse_with_stubs
    try:
        return fn()
    finally:
        retail_api_module.datetime = original_dt
        metrics.parse_business_zone = _real_parse_business_zone


def test_the_scenario_this_file_is_built_on_is_actually_reproducible_here():
    """Guard on the fixture itself. Every money assertion below depends on the
    shop being AHEAD of the device -- that is the direction in which the old
    truncation lost money rather than merely padding the axis. A runner east
    of UTC+11 would silently invert the scenario and everything here would
    pass for the wrong reason."""
    assert -14 * 60 <= SHOP_OFFSET_MINUTES <= 14 * 60, (
        f"this runner is on UTC{DEVICE_OFFSET_MINUTES // 60:+d}, so a shop three hours "
        f"further east ({SHOP_OFFSET_MINUTES} minutes) is not a real offset. The "
        f"scenario needs re-anchoring for this machine.")
    assert SHOP_OFFSET_MINUTES > DEVICE_OFFSET_MINUTES


def test_the_zone_stub_really_reaches_the_metrics_boundary():
    """A stand-in that did not actually resolve would leave every shop below
    unconfigured, and an unconfigured shop passes the KPI-equals-chart
    assertions trivially, because then both sides ARE on one clock."""
    shop = _make_shop()
    _configure(shop, offset_minutes=SHOP_OFFSET_MINUTES)

    def check():
        conn = get_retail_conn()
        boundary = metrics.business_day(conn, shop['company_id'])
        conn.close()
        return boundary

    boundary = _run(check)
    assert boundary.zone is not None, (
        'the shop read back as unconfigured -- every clock assertion in this '
        'file would be vacuous')
    assert int(boundary.zone.utcoffset(FROZEN_DEVICE_LOCAL).total_seconds()) // 60 \
        == SHOP_OFFSET_MINUTES


# ── fixture ──────────────────────────────────────────────────────────────────

def _make_user(company_id, role='admin'):
    email = f"clock-{uuid.uuid4().hex[:10]}@test.local"
    password = "ShopClockPW1"
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, uid, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,?,0)",
        (user_id, str(uuid.uuid4()), company_id, f"EMP-{uuid.uuid4().hex[:6].upper()}", email,
         hash_password(password), role, "active"))
    conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
                 (str(uuid.uuid4()), user_id, "retail", "full"))
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    conn.commit()
    conn.close()
    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_data(as_text=True)
    return client


def _make_shop():
    company_id = str(uuid.uuid4())
    client = _make_user(company_id, 'admin')
    conn = get_retail_conn()
    conn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = conn.execute("SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1",
                             (company_id,)).fetchone()[0]
    conn.commit()
    conn.close()
    client.get('/api/sub/retail/settings/tax')  # creates retail_settings / doc_sequences
    return {'company_id': company_id, 'branch_id': branch_id, 'client': client}


def _configure(shop, offset_minutes=None, day_start_hour=None):
    """Declare the shop's clock straight into the table -- the only thing that
    could write these keys before this wave, and still the shortest way to set
    up a shop whose zone is a synthetic stand-in."""
    conn = get_retail_conn()
    if offset_minutes is not None:
        conn.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?) "
                     "ON CONFLICT(company_id,skey) DO UPDATE SET svalue=excluded.svalue",
                     (shop['company_id'], metrics.BUSINESS_TIMEZONE_SETTING,
                      _stub_zone(offset_minutes)))
    if day_start_hour is not None:
        conn.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?) "
                     "ON CONFLICT(company_id,skey) DO UPDATE SET svalue=excluded.svalue",
                     (shop['company_id'], metrics.BUSINESS_DAY_START_SETTING, str(day_start_hour)))
    conn.commit()
    conn.close()


def _sale_at_shop_local(shop, shop_local, amount, offset_minutes=None):
    """One completed sale whose true instant lands at `shop_local` on the
    SHOP's wall clock.

    `created_at_utc` carries the real instant (what a v13 write stamps) and
    `created_at` the DEVICE's local rendering of the same instant -- which on
    a device in another zone is a different wall-clock reading. Both are
    present so nothing here accidentally proves itself through the pre-v13
    fallback arm of the COALESCE."""
    offset = SHOP_OFFSET_MINUTES if offset_minutes is None else offset_minutes
    instant = (shop_local - timedelta(minutes=offset)).replace(tzinfo=timezone.utc)
    device_local = (instant + timedelta(minutes=DEVICE_OFFSET_MINUTES)).replace(tzinfo=None)
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO sales (company_id,sale_number,branch_id,cashier,subtotal,total,amount_paid,"
        "payment_method,status,created_at,actor_user_uid,created_at_utc,uid) "
        "VALUES (?,?,?,'POS',?,?,?,'cash','completed',?,NULL,?,?)",
        (shop['company_id'], f'CLK-{uuid.uuid4().hex[:12]}', shop['branch_id'], amount, amount,
         amount, device_local.strftime('%Y-%m-%d %H:%M:%S'), instant.isoformat(), str(uuid.uuid4())))
    conn.commit()
    conn.close()


def _stats(shop):
    r = _run(lambda: shop['client'].get(STATS_URL))
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()['data']


def _today():
    return FROZEN_DEVICE_LOCAL.date()


# ── 1. the KPI and the chart may not disagree ────────────────────────────────

def test_the_hourly_chart_ships_every_pound_the_kpi_card_counted():
    """THE headline, and the assertion the existing consistency suite cannot
    make: sum the array the route SHIPPED -- after its own truncation -- not
    the raw dict metrics handed it."""
    shop = _make_shop()
    _configure(shop, offset_minutes=SHOP_OFFSET_MINUTES)
    day = _today()
    _sale_at_shop_local(shop, datetime(day.year, day.month, day.day, 5, 30), 50.0)
    _sale_at_shop_local(shop, datetime(day.year, day.month, day.day, 12, 30), 100.0)

    data = _stats(shop)
    assert data['today_sales'] == 150.0, data
    assert round(sum(data['hourly_data']), 2) == data['today_sales'], (
        f"the KPI card reads {data['today_sales']} and the chart bars sum to "
        f"{round(sum(data['hourly_data']), 2)} -- money the KPI counted is not on "
        f"the chart beside it.\nlabels={data['hourly_labels']}\ndata={data['hourly_data']}")


def test_the_hourly_axis_ends_on_the_shops_hour_not_the_reporting_devices():
    """The mechanism, named. `revenue_by_hour` keys on the shop's wall clock;
    the axis has to be cut on the same clock or the two disagree by exactly
    the difference between them."""
    shop = _make_shop()
    _configure(shop, offset_minutes=SHOP_OFFSET_MINUTES)
    data = _stats(shop)
    shop_hour = (FROZEN_DEVICE_LOCAL + timedelta(minutes=180)).strftime('%H')
    device_hour = FROZEN_DEVICE_LOCAL.strftime('%H')
    assert shop_hour != device_hour, 'the fixture stopped exercising the difference'
    assert data['hourly_labels'][-1] == f'{shop_hour}:00', (
        f"the axis stops at {data['hourly_labels'][-1]} -- the reporting device's hour "
        f"({device_hour}:00) rather than the shop's ({shop_hour}:00)")


def test_a_sale_rung_in_the_shops_most_recent_hours_is_on_the_chart():
    """The concrete loss, isolated: one sale, inside the window the old
    truncation cut off, and nothing else."""
    shop = _make_shop()
    _configure(shop, offset_minutes=SHOP_OFFSET_MINUTES)
    day = _today()
    _sale_at_shop_local(shop, datetime(day.year, day.month, day.day, 12, 45), 100.0)
    data = _stats(shop)
    assert data['today_sales'] == 100.0, data
    assert '12:00' in data['hourly_labels'], data['hourly_labels']
    assert round(sum(data['hourly_data']), 2) == 100.0, (data['hourly_labels'], data['hourly_data'])


def test_an_unconfigured_shop_keeps_exactly_the_axis_it_always_had():
    """The regression guard on the default, which is what almost every install
    in the field is. A shop that has declared nothing keeps the behaviour it
    had before business dates existed -- the device IS the shop -- so this
    change may not move the axis by so much as one label there."""
    shop = _make_shop()
    data = _stats(shop)
    device_hour = int(FROZEN_DEVICE_LOCAL.strftime('%H'))
    assert data['hourly_labels'] == [f'{h:02d}:00' for h in range(device_hour + 1)]


def test_a_trading_day_that_starts_at_04_00_lists_its_own_hours_and_loses_nothing():
    """`business_day_start_hour` is the other half of the shop clock and the
    axis has to follow it too. A shop whose day starts at 04:00 and is now at
    13:00 has been trading for ten hours, so the chart carries 04:00-13:00.
    With "midnight through now", the 22:00 sales of a trading day that runs
    past midnight are INSIDE the KPI's period and outside the chart's axis --
    the same money-off-the-chart failure by a different route."""
    shop = _make_shop()
    _configure(shop, offset_minutes=SHOP_OFFSET_MINUTES, day_start_hour=4)
    day = _today()
    _sale_at_shop_local(shop, datetime(day.year, day.month, day.day, 5, 30), 50.0)
    _sale_at_shop_local(shop, datetime(day.year, day.month, day.day, 12, 30), 100.0)
    # 02:00 today still belongs to YESTERDAY's trading day -- out of both.
    _sale_at_shop_local(shop, datetime(day.year, day.month, day.day, 2, 0), 999.0)

    data = _stats(shop)
    assert data['hourly_labels'][0] == '04:00', data['hourly_labels']
    assert data['hourly_labels'][-1] == '13:00', data['hourly_labels']
    assert data['today_sales'] == 150.0, data
    assert round(sum(data['hourly_data']), 2) == data['today_sales'], (
        data['hourly_labels'], data['hourly_data'])


def test_a_trading_day_that_has_run_past_midnight_still_carries_its_evening():
    """The wrap. Shop opens at 04:00 and it is now 01:00 the next morning, so
    the trading day is twenty-one hours old and its busiest hours are on the
    far side of midnight. A "midnight through now" axis would ship two labels
    and drop the entire evening while the KPI still counted it."""
    shop = _make_shop()
    # Put the shop at 01:00 while the device reads 10:00: fifteen hours ahead.
    offset = DEVICE_OFFSET_MINUTES + 15 * 60
    if not -14 * 60 <= offset <= 14 * 60:
        offset = DEVICE_OFFSET_MINUTES - 9 * 60      # same wall clock, other way round
    _configure(shop, offset_minutes=offset, day_start_hour=4)

    shop_now = FROZEN_DEVICE_LOCAL + timedelta(minutes=offset - DEVICE_OFFSET_MINUTES)
    assert shop_now.hour == 1, shop_now
    evening = shop_now.replace(hour=22, minute=15) - timedelta(days=1)
    _sale_at_shop_local(shop, evening, 200.0, offset_minutes=offset)

    data = _stats(shop)
    assert data['hourly_labels'][0] == '04:00', data['hourly_labels']
    assert data['hourly_labels'][-1] == '01:00', data['hourly_labels']
    assert '22:00' in data['hourly_labels'], data['hourly_labels']
    assert data['today_sales'] == 200.0, data
    assert round(sum(data['hourly_data']), 2) == data['today_sales'], (
        data['hourly_labels'], data['hourly_data'])


def test_the_kpi_and_the_chart_agree_across_a_spread_of_shop_offsets():
    """Not one lucky offset. Each of these is a real shop somewhere, and the
    invariant is the same every time: nothing the KPI counts may be missing
    from the array beside it."""
    day = _today()
    for delta_minutes in (-300, -90, 0, 30, 180, 330):
        offset = DEVICE_OFFSET_MINUTES + delta_minutes
        if not -14 * 60 <= offset <= 14 * 60:
            continue
        shop = _make_shop()
        _configure(shop, offset_minutes=offset)
        for hour, amount in ((1, 11.0), (9, 22.0), (13, 33.0), (20, 44.0)):
            _sale_at_shop_local(shop, datetime(day.year, day.month, day.day, hour, 15),
                                amount, offset_minutes=offset)
        data = _stats(shop)
        assert round(sum(data['hourly_data']), 2) == data['today_sales'], (
            f"offset {offset}: KPI {data['today_sales']} vs chart "
            f"{round(sum(data['hourly_data']), 2)}\n{data['hourly_labels']}\n"
            f"{data['hourly_data']}")


def test_a_row_stamped_ahead_of_the_shops_own_clock_is_still_on_the_chart():
    """Clock skew between two terminals is the one way a row inside today's
    period can carry an hour the shop has not reached yet. "The trading day so
    far" would have nowhere to draw it while the KPI above still counted it --
    the same silent disappearance this file exists to end, arriving by a
    different door. The axis reaches any hour that holds money."""
    shop = _make_shop()
    _configure(shop, offset_minutes=SHOP_OFFSET_MINUTES)
    day = _today()
    _sale_at_shop_local(shop, datetime(day.year, day.month, day.day, 9, 0), 25.0)
    # Shop clock reads 13:00; this till thinks it is 17:00.
    _sale_at_shop_local(shop, datetime(day.year, day.month, day.day, 17, 0), 75.0)

    data = _stats(shop)
    assert data['today_sales'] == 100.0, data
    assert '17:00' in data['hourly_labels'], data['hourly_labels']
    assert round(sum(data['hourly_data']), 2) == data['today_sales'], (
        data['hourly_labels'], data['hourly_data'])


def test_the_payment_doughnut_still_agrees_with_the_kpi_too():
    """The third view of the same period. dashboard_stats' own docstring
    promises `sum(hourly) == sum(payment_methods) == today_sales`; moving the
    window onto the shop clock has to keep all three, not two of them."""
    shop = _make_shop()
    _configure(shop, offset_minutes=SHOP_OFFSET_MINUTES)
    day = _today()
    _sale_at_shop_local(shop, datetime(day.year, day.month, day.day, 5, 30), 50.0)
    _sale_at_shop_local(shop, datetime(day.year, day.month, day.day, 12, 30), 100.0)
    data = _stats(shop)
    doughnut = round(sum(v['revenue'] for v in data['payment_methods'].values()), 2)
    assert doughnut == data['today_sales'] == round(sum(data['hourly_data']), 2), data


def test_the_today_window_itself_moves_onto_the_shops_date():
    """Not only the axis. At 22:30 on the device, a shop three hours east is
    already into the next day; a window built from the device's DATE asks
    about a day the shop has finished, and the today card reads 0.00 with the
    sale sitting one bucket over."""
    shop = _make_shop()
    _configure(shop, offset_minutes=SHOP_OFFSET_MINUTES)

    late = datetime(2026, 8, 19, 22, 30, 0)

    class _LateDevice(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return late.astimezone().astimezone(tz)
            return late

    # Rung right now: 22:30 on the device is 01:30 on the 20th at the shop.
    _sale_at_shop_local(shop, datetime(2026, 8, 20, 1, 30), 60.0)

    original_dt = retail_api_module.datetime
    retail_api_module.datetime = _LateDevice
    metrics.parse_business_zone = _parse_with_stubs
    try:
        data = shop['client'].get(STATS_URL).get_json()['data']
    finally:
        retail_api_module.datetime = original_dt
        metrics.parse_business_zone = _real_parse_business_zone

    assert data['today_sales'] == 60.0, (
        "the today card is on the reporting device's date, not the shop's -- the "
        f"sale it just rang is in another bucket.\n{data['hourly_labels']}\n"
        f"{data['hourly_data']}")
    assert round(sum(data['hourly_data']), 2) == data['today_sales'], data


# ── 2. the setting needs a writer ────────────────────────────────────────────

def _post(shop, payload, client=None):
    return _run(lambda: (client or shop['client']).post(SETTINGS_URL, json=payload))


def _read_back(shop, client=None):
    r = _run(lambda: (client or shop['client']).get(SETTINGS_URL))
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()['data']


def _with_parser(parser, fn):
    """Drive the route with a specific verdict from the function it is
    supposed to be validating through. Anything the route decides under this
    is decided BY `parse_business_zone` and by nothing else -- the same
    discipline the void_payment authority tests use on
    session_has_capability."""
    original_dt = retail_api_module.datetime
    retail_api_module.datetime = _FrozenDateTime
    metrics.parse_business_zone = parser
    try:
        return fn()
    finally:
        retail_api_module.datetime = original_dt
        metrics.parse_business_zone = _real_parse_business_zone


def test_the_shop_clock_has_a_write_path_at_all():
    """Until this passes, the two settings `metrics.business_day()` reads are
    readable by metrics and writable by nothing -- so business dates are dead
    code on every install in the field."""
    rules = {str(r.rule) for r in app.url_map.iter_rules()}
    assert SETTINGS_URL in rules, (
        "the two settings metrics.business_day() reads have no route that writes "
        "them; they can only be set by editing retail.db by hand")


def test_a_valid_shop_clock_round_trips_and_the_reader_agrees():
    shop = _make_shop()
    key = _stub_zone(SHOP_OFFSET_MINUTES)
    r = _post(shop, {'business_timezone': key, 'business_day_start_hour': 4})
    assert r.status_code == 200, r.get_data(as_text=True)
    read = _read_back(shop)
    assert read['business_timezone'] == key
    assert read['business_day_start_hour'] == 4
    # ...and the flag reports THIS install's real capability, not the stub's:
    # only the synthetic keys resolve through the stand-in, so on a machine
    # with no tz database 'UTC' still fails and the flag still says so.
    assert read['timezone_database_available'] is (_real_parse_business_zone('UTC') is not None)

    def read():
        conn = get_retail_conn()
        boundary = metrics.business_day(conn, shop['company_id'])
        conn.close()
        return boundary

    boundary = _run(read)
    assert getattr(boundary.zone, 'key', None) == key
    assert boundary.day_start_hour == 4


def test_the_route_validates_through_metrics_and_not_through_a_second_check():
    """The strongest form of the "one definition of valid" rule: force
    `parse_business_zone` to a fixed verdict and the route's answer must
    follow it, both ways. A private zone check inside the route would keep
    refusing a value the forced parser accepts."""
    shop = _make_shop()
    accepted = _FixedZone(120, 'Aura/Forced')

    granting = _with_parser(lambda raw: accepted,
                            lambda: shop['client'].post(SETTINGS_URL,
                                                        json={'business_timezone': 'anything at all'}))
    assert granting.status_code == 200, granting.get_data(as_text=True)
    assert granting.get_json()['data']['business_timezone'] == 'Aura/Forced', (
        'the route stored the raw text instead of the zone the parser returned')

    denying = _with_parser(lambda raw: None,
                           lambda: shop['client'].post(SETTINGS_URL,
                                                       json={'business_timezone': 'Asia/Amman'}))
    assert denying.status_code in (400, 503), denying.get_data(as_text=True)
    assert denying.get_json()['status'] == 'error'


def test_a_value_the_reader_would_throw_away_is_refused_at_write_time():
    """metrics' reader tolerates nonsense on purpose -- it warns and treats
    the shop as unconfigured, because a report is still worth drawing on the
    old bucketing. Silently degrading a value an owner just typed into a
    settings screen is a different thing entirely: they would leave believing
    the shop is on Amman time when no report agrees."""
    shop = _make_shop()

    def parser(raw):
        # A tz database exists (so 'UTC' probes fine) but this key is not a zone.
        return _FixedZone(0, 'UTC') if str(raw).strip() == 'UTC' else None

    for bad in ('+02:00', 'EET', '120', 'two hours', 'Mars/Olympus'):
        r = _with_parser(parser, lambda: shop['client'].post(
            SETTINGS_URL, json={'business_timezone': bad}))
        assert r.status_code == 400, (bad, r.get_data(as_text=True))
        assert r.get_json()['status'] == 'error'
        assert _read_back(shop)['business_timezone'] is None, (
            f"{bad!r} was refused with a 400 and written anyway")


def test_a_missing_timezone_database_is_reported_as_an_environment_problem():
    """`parse_business_zone` refuses a perfectly good zone name on any install
    with no tz database -- which today is every Windows install, the Android
    build and the packaged exe, because `tzdata` is in no requirements file,
    not in the Chaquopy pip block and not collected by the PyInstaller spec.
    Answering "Asia/Amman is not a valid timezone" would send whoever is
    debugging it to edit a value that is already correct, so the two failures
    have to be told apart."""
    shop = _make_shop()
    r = _with_parser(lambda raw: None, lambda: shop['client'].post(
        SETTINGS_URL, json={'business_timezone': 'Asia/Amman'}))
    assert r.status_code == 503, r.get_data(as_text=True)
    message = r.get_json()['message']
    assert 'tzdata' in message, message
    assert _read_back(shop)['business_timezone'] is None


def test_a_real_iana_zone_round_trips_when_this_install_has_a_tz_database():
    """Both branches assert something real, so this test proves something on a
    machine with tzdata AND on one without -- and gets strictly stronger the
    day the dependency is added, rather than needing to be rewritten."""
    shop = _make_shop()
    r = _run(lambda: shop['client'].post(SETTINGS_URL, json={'business_timezone': 'Asia/Amman'}))
    if _real_parse_business_zone('UTC') is None:
        assert r.status_code == 503, r.get_data(as_text=True)
        assert 'tzdata' in r.get_json()['message']
        assert _read_back(shop)['timezone_database_available'] is False
        return
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()['data']['business_timezone'] == 'Asia/Amman'
    assert _read_back(shop)['business_timezone'] == 'Asia/Amman'


@pytest.mark.parametrize('bad', [24, -1, 'midnight', '4.5', 4.5])
def test_a_day_start_that_is_not_an_hour_of_the_day_is_refused(bad):
    shop = _make_shop()
    r = _post(shop, {'business_day_start_hour': bad})
    assert r.status_code == 400, (bad, r.get_data(as_text=True))
    assert _read_back(shop)['business_day_start_hour'] == 0


def test_every_day_start_the_writer_accepts_survives_the_reader_unchanged():
    """The rule that keeps this honest. A route with its own private notion of
    "valid" is a SECOND definition and the two drift: metrics narrows its
    parser, the settings screen keeps accepting the old shape, and an owner
    saves a value that silently stops working."""
    shop = _make_shop()
    for hour in range(24):
        assert _post(shop, {'business_day_start_hour': hour}).status_code == 200, hour

        def read():
            conn = get_retail_conn()
            boundary = metrics.business_day(conn, shop['company_id'])
            conn.close()
            return boundary

        assert _run(read).day_start_hour == hour, (
            f"the writer accepted {hour} and the reader read back something else -- "
            f"two definitions of 'valid'")


def test_clearing_the_setting_returns_the_shop_to_undeclared():
    """"Undeclared" is not the same as UTC, and a settings screen has to be
    able to say "I no longer declare a zone". `ZoneInfo('UTC')` is a real
    zone; writing it to mean "unset" would silently re-file the trading
    history of every shop that merely stopped declaring."""
    shop = _make_shop()
    key = _stub_zone(SHOP_OFFSET_MINUTES)
    assert _post(shop, {'business_timezone': key, 'business_day_start_hour': 6}).status_code == 200
    assert _post(shop, {'business_timezone': None, 'business_day_start_hour': None}).status_code == 200
    assert _read_back(shop)['business_timezone'] is None
    assert _read_back(shop)['business_day_start_hour'] == 0

    def read():
        conn = get_retail_conn()
        boundary = metrics.business_day(conn, shop['company_id'])
        conn.close()
        return boundary

    boundary = _run(read)
    assert boundary.zone is None and boundary.day_start_hour == 0


def test_a_body_naming_neither_key_is_refused_rather_than_silently_doing_nothing():
    shop = _make_shop()
    r = _post(shop, {'timezone': 'Asia/Amman'})
    assert r.status_code == 400, r.get_data(as_text=True)
    assert r.get_json()['status'] == 'error'


def test_writing_the_setting_actually_moves_the_dashboards_clock():
    """End to end, through the two routes a real owner would use: the setting
    is only worth having if the report follows it."""
    shop = _make_shop()
    day = _today()
    _sale_at_shop_local(shop, datetime(day.year, day.month, day.day, 12, 30), 100.0)

    before = _stats(shop)['hourly_labels'][-1]
    assert _post(shop, {'business_timezone': _stub_zone(SHOP_OFFSET_MINUTES)}).status_code == 200
    after = _stats(shop)
    assert after['hourly_labels'][-1] != before, 'the dashboard ignored the new shop clock'
    assert round(sum(after['hourly_data']), 2) == after['today_sales'], after


def test_the_shop_clock_is_owner_authority_like_its_two_settings_siblings():
    """`/settings/credit` and `/settings/tax` are both retail.employees. This
    one decides which trading DAY every figure in the shop's history is
    counted on -- if anything it is the more consequential of the three."""
    shop = _make_shop()
    manager = _make_user(shop['company_id'], 'manager')
    assert user_accounts.CAP_EMPLOYEES not in user_accounts.capabilities_for_role(
        user_accounts.ROLE_MANAGER), 'this assertion would be vacuous otherwise'
    r = _post(shop, {'business_timezone': _stub_zone(SHOP_OFFSET_MINUTES)}, client=manager)
    assert r.status_code == 403, r.get_data(as_text=True)
    assert _read_back(shop)['business_timezone'] is None, 'the refused write happened anyway'


def test_an_anonymous_write_is_401_not_403():
    anon = app.test_client()
    assert anon.post(SETTINGS_URL, json={'business_timezone': 'Asia/Amman'}).status_code == 401
