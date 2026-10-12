"""Aura Retail -- MONEY-4a / review D1: the BOOKS date every live journal on the shop's
BUSINESS day, the same clock the sales reports use.

THE DEFECT (measured by an independent review, probe_business_date_test.py). Every
sales report (`metrics`) buckets by the business day: `created_at` shifted back by
`business_day_start_hour`. But the routes called `gl.post_sale` / `post_cogs` /
`post_return` / `post_customer_payment` / `post_supplier_payment` / the stock and PO
posters with no `business_date`, and `general_ledger._today()` -- the fallback -- is the
CALENDAR day. A shop whose trading day starts at 06:00 that rang a 100.000 sale at 01:30
saw it on Saturday in the Z-report and Today card, and on Sunday in the P&L, balance
sheet and cash flow. Month-end books disagreed with the shop's own sales reports by the
night's takings.

THE FIX: each live route computes `_gl_business_date(conn, cid)` once and passes it to
every poster it calls. `_today()` itself is untouched (replay/backfill callers keep it) and
a synced `gl_journal` keeps the SENDER's business_date (not exercised here).

WHAT THIS FILE PROVES, through the real routes, read back from the real database:
  DENY half  (start hour 6, clocks frozen at 01:30-01:58 on 2026-03-11)
    * the sale journal, its COGS journal, a return, its COGS reversal, a customer
      payment, a supplier payment and a stock adjustment are ALL dated 2026-03-10;
    * the journal COUNT is asserted, so "no journal matched" can never read as a pass;
    * the P&L for the business day equals the sales report's revenue for that day.
  ALLOW half (the control: nothing that should keep its date moved)
    * start hour 0: the same 01:30 sale is dated 2026-03-11 (the calendar day);
    * start hour 6 but a 07:00 sale: dated 2026-03-11 (already inside the new day).

MUTATIONS (quoted in the hand-back): M1 drop `business_date=` at the post_sale call site
-> test_night_journals_are_dated_on_the_business_day RED; M2 make `_gl_business_date`
return the calendar day -> same test RED, and the P&L-equals-report test RED.

Run (one file per process):
    pytest products/retail/tests/retail_gl_business_day_test.py -v
"""
import sys
import uuid
from datetime import datetime as _real_dt, date
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import retail_accountant_regate_test as H  # noqa: E402  (builds the isolated app + data dir)
from api import retail_api as ra  # noqa: E402
from core.retail import financial_statements as fin  # noqa: E402
from core.retail import general_ledger as gl  # noqa: E402
from core.retail import metrics  # noqa: E402

API = H.API
DAY = date(2026, 3, 11)          # the calendar day of every frozen clock below
PREV = '2026-03-10'              # the business day a 01:xx moment belongs to at start hour 6
SAME = '2026-03-11'


class _Clock(_real_dt):
    """datetime whose now() is frozen at a settable wall time on DAY."""
    at = (1, 30)

    @classmethod
    def now(cls, tz=None):
        naive = cls(DAY.year, DAY.month, DAY.day, cls.at[0], cls.at[1], 0)
        return naive if tz is None else naive.astimezone().astimezone(tz)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    H.device_context._cached_device = None
    for mod in (ra, gl, metrics):
        monkeypatch.setattr(mod, 'datetime', _Clock)
    yield
    H.device_context._cached_device = None


def _at(hour, minute):
    _Clock.at = (hour, minute)


def _set_start_hour(admin, hour):
    r = admin.post(f'{API}/settings/business-day', json={'business_day_start_hour': hour})
    assert r.status_code == 200, r.get_json()


def _ring_sale(admin, pid, qty=1):
    r = admin.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': qty}], 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _journals(cid, source_types):
    marks = ','.join('?' * len(source_types))
    conn = H.get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(
            f"SELECT source_type, reference, business_date FROM gl_journals "
            f"WHERE company_id=? AND source_type IN ({marks}) ORDER BY id",
            (cid, *source_types)).fetchall()]
    finally:
        conn.close()


def _night_of_activity(admin, pid, cid):
    """Sale 01:30, return 01:40, customer payment 01:50, supplier payment 01:55,
    stock adjustment 01:58 -- all through the real routes."""
    cust = H._customer(admin)
    sup = H._supplier(admin)
    _at(1, 30)
    sale = _ring_sale(admin, pid)
    _at(1, 40)
    r = admin.post(f'{API}/returns', json={
        'sale_id': sale['id'], 'items': [{'product_id': pid, 'quantity': 1}],
        'refund_method': 'cash', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    _at(1, 50)
    r = admin.post(f'{API}/customers/{cust}/payments', json={'amount': 20})
    assert r.status_code == 200, r.get_json()
    _at(1, 55)
    r = admin.post(f'{API}/suppliers/{sup}/payments', json={'amount': 20})
    assert r.status_code == 200, r.get_json()
    _at(1, 58)
    r = admin.post(f'{API}/products/{pid}/stock-adjust', json={'quantity': -1, 'reason': 'night count'})
    assert r.status_code == 200, r.get_json()


# source_type -> how many journals the night above must have produced. The sale and the
# return each post a revenue journal AND a COGS journal (both under their own source_type).
_EXPECTED_COUNTS = {'sale': 2, 'return': 2, 'customer_payment': 1, 'supplier_payment': 1,
                    'stock_adjustment': 1}


def test_night_journals_are_dated_on_the_business_day():
    admin, cid, pid = H._new_shop()
    _set_start_hour(admin, 6)
    _night_of_activity(admin, pid, cid)

    rows = _journals(cid, list(_EXPECTED_COUNTS))
    seen = {}
    for row in rows:
        seen[row['source_type']] = seen.get(row['source_type'], 0) + 1
    # The shape this guards against: asserting dates over an EMPTY list passes forever.
    assert seen == _EXPECTED_COUNTS, (seen, rows)
    wrong = [r for r in rows if r['business_date'] != PREV]
    assert not wrong, f'journals on the calendar day instead of the business day {PREV}: {wrong}'


def test_a_midnight_shop_with_start_hour_zero_keeps_the_calendar_day():
    # ALLOW half: nothing may move for a shop that never configured a trading day.
    admin, cid, pid = H._new_shop()
    _set_start_hour(admin, 0)
    _at(1, 30)
    _ring_sale(admin, pid)
    rows = _journals(cid, ['sale'])
    assert len(rows) == 2 and all(r['business_date'] == SAME for r in rows), rows


def test_a_sale_after_the_day_start_keeps_the_calendar_day():
    # ALLOW half: 07:00 with a 06:00 day start is already inside 2026-03-11.
    admin, cid, pid = H._new_shop()
    _set_start_hour(admin, 6)
    _at(7, 0)
    _ring_sale(admin, pid)
    rows = _journals(cid, ['sale'])
    assert len(rows) == 2 and all(r['business_date'] == SAME for r in rows), rows


def test_the_profit_and_loss_for_a_business_day_equals_the_sales_report_revenue():
    admin, cid, pid = H._new_shop(price=100.0)
    _set_start_hour(admin, 6)
    _at(1, 30)
    _ring_sale(admin, pid, qty=2)            # 200.000, on business day 2026-03-10
    conn = H.get_retail_conn()
    try:
        report_prev = metrics.revenue(conn, cid, metrics.period_for_day(PREV), currency='JOD')
        report_same = metrics.revenue(conn, cid, metrics.period_for_day(SAME), currency='JOD')
        pl_prev = fin.income_statement(conn, cid, PREV, PREV)
        pl_same = fin.income_statement(conn, cid, SAME, SAME)
    finally:
        conn.close()
    assert float(report_prev) == 200.0 and float(report_same) == 0.0, (report_prev, report_same)
    # The books say what the reports say, day by day.
    assert pl_prev['revenue'] == float(report_prev)
    assert pl_same['revenue'] == float(report_same)
    assert pl_prev['has_activity'] is True and pl_same['has_activity'] is False
