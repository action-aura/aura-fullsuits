"""
Aura Retail -- cross-screen revenue consistency suite.

Every assertion here is an AGREEMENT assertion: two or more figures that a
user sees on ONE screen at the SAME time must be derived from the same
definition, so they cannot contradict each other. That is the whole content
of core/retail/metrics.py's contract, expressed as tests.

Before core/retail/metrics.py existed, "revenue" was hand-written roughly
seven times across api/retail_api.py and the two corrections that mattered
-- netting refunds out, and scoping to a branch -- had each been retrofitted
onto only some of the copies. Concretely, every test below FAILED:

  * dashboard_stats() netted refunds out of today_sales but served a gross
    SUM(total) hourly chart and a gross payment-method breakdown in the
    SAME response.
  * /reports/sales-trend and /reports/payment-methods never subtracted
    returns while /reports/summary and /reports/by-branch did.
  * /reports/top-products had NO date filter at all, so its "top product"
    could out-earn the 30-day total printed on the same page.
  * "average ticket" was AVG(sales.total) on the trend route and
    net-revenue/transactions on the summary route.
  * branch_id reached sales-trend and top-products only -- summary,
    payment-methods took no branch_id, so the dropdown moved the charts and
    left the KPI cards showing all branches.

Run:
    pytest products/retail/tests/retail_metrics_consistency_test.py -v
"""
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_metrics_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from core.retail import metrics  # noqa: E402

PRICE = 50.0
COST = 10.0
# NON-ZERO ON PURPOSE -- see _setup_company()'s docstring. Every expected
# figure below is stated against UNIT_TOTAL, the tax-INCLUSIVE amount that
# actually changes hands, because that is the basis core/retail/metrics.py
# measures in (its decision #7).
TAX_RATE = 15.0
UNIT_TAXABLE = PRICE                       # 50.00 -- what sale_items.line_total stores
UNIT_TOTAL = round(PRICE * (1 + TAX_RATE / 100), 2)   # 57.50 -- what sales.total stores


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _setup_company():
    """One company, two active branches, one product at a REAL 15% tax rate,
    stocked in both.

    The tax rate is the whole point and must never be set back to zero. An
    earlier version of this fixture seeded tax_rate=0 "to keep the expected
    totals hand-computable", and that single decision made
    test_gross_profit_agrees_between_the_summary_card_and_the_product_column
    pass over a genuinely broken implementation: top_products computed
    revenue as SUM(sale_items.line_total) - SUM(return_items.line_total),
    and those two identically-named columns are NOT the same quantity --
    create_sale() writes the taxable amount (tax excluded) into the first
    while create_return() writes the total (tax included) into the second.
    At tax_rate=0 the two coincide exactly, so the assertion could not
    distinguish a correct implementation from a wrong one; it certified the
    bug as fixed. At 15% the difference is exactly the tax on the refund and
    every agreement assertion in this file discriminates.

    Totals stay hand-computable anyway -- 50.00 + 15% is 57.50 -- so these
    assertions still test the revenue DEFINITIONS rather than the tax engine
    (products/retail/tests/retail_pricing_test.py owns that)."""
    email = f"mx-{uuid.uuid4().hex[:10]}@test.local"
    password = "MetricsConsistPW1"
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()

    rconn = get_retail_conn()
    cur = rconn.cursor()
    cur.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    main_id = cur.lastrowid
    cur.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Downtown')", (company_id,))
    downtown_id = cur.lastrowid

    pid = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) "
        "VALUES (?,?,?,'Metrics Item',?,?,?)",
        (pid, company_id, f"MX-{uuid.uuid4().hex[:6]}", COST, PRICE, TAX_RATE),
    )
    for bid in (main_id, downtown_id):
        cur.execute(
            "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,5000)",
            (company_id, pid, bid),
        )
    rconn.commit()
    rconn.close()

    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})
    client.get("/api/sub/retail/settings/tax")  # runs doc_sequences/_ensure_credit_schema

    return client, company_id, pid, main_id, downtown_id


def _sell(client, pid, branch_id, qty, payment_method='cash'):
    # Pay the TAX-INCLUSIVE total. Paying qty * PRICE would leave
    # balance_due == the tax and turn every sale in this file into a credit
    # sale (create_sale: is_credit when balance_due > 0.005), which walk-ins
    # are not allowed to make.
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': qty}],
        'branch_id': branch_id,
        'payment_method': payment_method,
        'amount_paid': round(qty * UNIT_TOTAL, 2),
        'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _refund(client, sale_id, pid, qty, refund_method='cash'):
    r = client.post('/api/sub/retail/returns', json={
        'sale_id': sale_id,
        'items': [{'product_id': pid, 'quantity': qty}],
        'refund_method': refund_method,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


@pytest.fixture(scope="module")
def seeded():
    """Deterministic, hand-computable ledger, all dated today. Every unit is
    50.00 + 15% tax == 57.50 tendered:

        Main      3 sales, cash:  115.00 + 57.50 + 172.50 = 345.00  (6 units)
        Downtown  2 sales:        230.00 cash + 115.00 card = 345.00  (6 units)
        Refund    1 unit off Main's first sale, cash:  -57.50

        gross sales 690.00   refunds 57.50   REVENUE 632.50   5 transactions
        by branch:  Main 287.50 / 3 txns,  Downtown 345.00 / 2 txns
        by tender:  cash 517.50 (575.00 - 57.50),  card 115.00
        product:    11 net units, 632.50 net revenue, 110.00 cost, 522.50 profit

    The TAXABLE base of the same ledger is 600.00 sold / 50.00 refunded. Those
    numbers appear nowhere in the assertions below except where a test names
    them explicitly to prove they are NOT what gets reported -- they are the
    wrong-basis figures the old top_products produced.
    """
    client, cid, pid, main_id, downtown_id = _setup_company()
    main_sales = [_sell(client, pid, main_id, q) for q in (2, 1, 3)]
    _sell(client, pid, downtown_id, 4)
    _sell(client, pid, downtown_id, 2, payment_method='card')
    refund = _refund(client, main_sales[0]['id'], pid, 1)
    assert refund['refund_amount'] == 57.5
    return {'client': client, 'cid': cid, 'pid': pid,
            'main_id': main_id, 'downtown_id': downtown_id}


# ── 1. One screen, one number: the dashboard ──────────────────────────────────

def test_dashboard_kpi_hourly_chart_and_payment_breakdown_all_agree(seeded):
    """The KPI card, the hourly bar chart and the payment-method doughnut
    are three renderings of THE SAME period on ONE screen. Any refund today
    used to split them: the card netted the refund out, the other two did
    not. All three must now report 632.50."""
    body = seeded['client'].get('/api/sub/retail/dashboard/stats').get_json()['data']

    assert body['today_sales'] == 632.5
    assert body['today_returns'] == 57.5
    assert body['today_transactions'] == 5

    assert round(sum(body['hourly_data']), 2) == 632.5
    assert round(sum(m['revenue'] for m in body['payment_methods'].values()), 2) == 632.5

    # ...and the refund lands on the tender it was paid back in, so the two
    # tender buckets are not merely "some split that adds up".
    assert body['payment_methods']['cash']['revenue'] == 517.5
    assert body['payment_methods']['card']['revenue'] == 115.0


# ── 2. One screen, one number: the reports page ───────────────────────────────

def test_reports_trend_summary_payment_and_branch_charts_all_agree(seeded):
    """Every widget on the Reports page is scoped to the same `days`
    control, so their revenue totals must be equal. sales-trend and
    payment-methods used to omit returns entirely while summary and
    by-branch subtracted them -- the trend line summed higher than the
    Revenue KPI printed directly beside it."""
    client = seeded['client']
    summary = client.get('/api/sub/retail/reports/summary?days=365').get_json()['data']
    trend = client.get('/api/sub/retail/reports/sales-trend?days=365').get_json()
    pay = client.get('/api/sub/retail/reports/payment-methods?days=365').get_json()
    by_branch = client.get('/api/sub/retail/reports/by-branch?days=365').get_json()
    top = client.get('/api/sub/retail/reports/top-products?days=365&limit=8').get_json()

    assert summary['revenue'] == 632.5
    assert round(sum(trend['data']), 2) == 632.5
    assert round(sum(r['revenue'] for r in pay['data']), 2) == 632.5
    assert round(sum(by_branch['data']), 2) == 632.5
    # One product in this company, so its net revenue IS the page total --
    # which also means top-products has to be on the same TAX basis as the
    # KPI card. It reported 542.50 (a tax-exclusive sales base with a
    # tax-inclusive refund taken off it) until that was fixed.
    assert round(sum(top['revenue']), 2) == 632.5

    assert summary['transactions'] == 5
    assert sum(trend['transactions']) == 5
    assert sum(by_branch['transactions']) == 5


def test_average_ticket_is_one_definition_across_trend_summary_and_branches(seeded):
    """Two "average ticket" numbers used to be printed on one page: the
    trend/by-branch routes returned AVG(sales.total) (gross, ignoring
    refunds) and summary returned net revenue / transactions. Canonically
    it is net revenue / transactions, everywhere. Every sale here is dated
    today, so the single trend day must equal the page-level figure."""
    client = seeded['client']
    summary = client.get('/api/sub/retail/reports/summary?days=365').get_json()['data']
    trend = client.get('/api/sub/retail/reports/sales-trend?days=365').get_json()
    by_branch = client.get('/api/sub/retail/reports/by-branch?days=365').get_json()

    assert summary['avg_ticket'] == 126.5          # 632.50 / 5, NOT 690 / 5 == 138
    assert trend['avg_ticket'] == [126.5]
    branch_avg = dict(zip(by_branch['labels'], by_branch['avg_ticket']))
    assert branch_avg['Main'] == round(287.5 / 3, 2)
    assert branch_avg['Downtown'] == 172.5


def test_gross_profit_agrees_between_the_summary_card_and_the_product_column(seeded):
    """Revenue is net of returns, so COGS must be too -- otherwise
    gross_profit is understated by the full cost of every returned unit and
    the Reports summary card disagrees with the per-product profit column
    rendered beside it.

    11 net units at cost 10.00 == 110.00 COGS; 632.50 - 110.00 == 522.50,
    and BOTH widgets must say 522.50. Asserted as literal numbers, not as
    `gross_profit == revenue - cogs` -- that identity holds even when cogs
    itself is wrong, so it proves nothing.

    This test used to run against a zero-tax fixture, which is why it passed
    while top_products was subtracting a tax-INCLUSIVE refund from a
    tax-EXCLUSIVE sales base. At 15% that bug shows up here as exactly the
    tax on the refund: the product column reported 432.50 profit against the
    card's 522.50, a 90.00 gap. See _setup_company()."""
    client = seeded['client']
    summary = client.get('/api/sub/retail/reports/summary?days=365').get_json()['data']
    top = client.get('/api/sub/retail/reports/top-products?days=365&limit=8').get_json()

    assert top['data'] == [11.0]         # 12 units sold, 1 returned
    assert summary['cogs'] == 110.0      # NOT 120.00 -- the returned unit went back on the shelf
    assert summary['gross_profit'] == 522.5
    assert top['profit'] == [522.5]
    assert summary['gross_profit'] == sum(top['profit'])
    assert summary['margin_pct'] == round(522.5 / 632.5 * 100, 1)


def test_top_products_revenue_uses_the_same_tax_basis_as_every_other_figure(seeded):
    """The defect this pins down directly, independent of COGS.

    `sale_items.line_total` and `return_items.line_total` share a name and
    are NOT the same quantity: create_sale() writes calc['taxable_amount']
    (tax EXCLUDED) into the first, create_return() writes calc['total'] (tax
    INCLUDED) into the second. top_products did
    SUM(sale_items.line_total) - SUM(return_items.line_total), mixing the
    two, so its revenue was wrong by exactly the tax on every refund.

    The three candidate numbers for this ledger are all distinct at a 15%
    tax rate, which is the entire reason the fixture has one:

        690.00 - 57.50 == 632.50   tax-inclusive both sides   <- CORRECT
        600.00 - 57.50 == 542.50   mixed basis                <- the bug
        600.00 - 50.00 == 550.00   tax-exclusive both sides

    Asserted against the database's own raw columns rather than restating a
    constant, so this stays true if the fixture ledger is ever changed."""
    client, cid = seeded['client'], seeded['cid']
    top = client.get('/api/sub/retail/reports/top-products?days=365&limit=8').get_json()
    summary = client.get('/api/sub/retail/reports/summary?days=365').get_json()['data']

    conn = get_retail_conn()
    taxable_sold = conn.execute(
        "SELECT COALESCE(SUM(si.line_total),0) FROM sale_items si "
        "JOIN sales s ON si.sale_id=s.id WHERE s.company_id=?", (cid,)).fetchone()[0]
    total_sold = conn.execute(
        "SELECT COALESCE(SUM(total),0) FROM sales WHERE company_id=?", (cid,)).fetchone()[0]
    refunded = conn.execute(
        "SELECT COALESCE(SUM(refund_amount),0) FROM returns WHERE company_id=?", (cid,)).fetchone()[0]
    conn.close()

    # The fixture really does put the two bases far apart -- if this ever
    # stops holding, the test below has stopped discriminating.
    assert round(total_sold - taxable_sold, 2) == 90.0, (
        'sale_items.line_total must be the TAXABLE base and sales.total the '
        'tax-inclusive one; if they are equal this test proves nothing.')

    reported = round(sum(top['revenue']), 2)
    assert reported == round(total_sold - refunded, 2) == 632.5
    assert reported != round(taxable_sold - refunded, 2)   # 542.50, the mixed-basis bug
    assert reported == summary['revenue']


# ── 3. The branch dropdown moves the cards AND the charts ─────────────────────

def test_branch_filter_moves_kpi_cards_and_charts_together(seeded):
    """Picking a branch used to filter sales-trend and top-products while
    summary and payment-methods silently kept showing all branches, so the
    KPI cards and the charts below them described different businesses."""
    client = seeded['client']
    main, downtown = seeded['main_id'], seeded['downtown_id']

    def page(branch_id):
        qs = f'&branch_id={branch_id}' if branch_id else ''
        summary = client.get(f'/api/sub/retail/reports/summary?days=365{qs}').get_json()['data']
        trend = client.get(f'/api/sub/retail/reports/sales-trend?days=365{qs}').get_json()
        pay = client.get(f'/api/sub/retail/reports/payment-methods?days=365{qs}').get_json()
        top = client.get(f'/api/sub/retail/reports/top-products?days=365&limit=8{qs}').get_json()
        return (summary['revenue'], round(sum(trend['data']), 2),
                round(sum(r['revenue'] for r in pay['data']), 2), round(sum(top['revenue']), 2))

    assert page(main) == (287.5, 287.5, 287.5, 287.5)          # 345.00 - 57.50 refund
    assert page(downtown) == (345.0, 345.0, 345.0, 345.0)
    assert page(None) == (632.5, 632.5, 632.5, 632.5)

    # The filter partitions revenue -- it never duplicates or drops any.
    assert page(main)[0] + page(downtown)[0] == page(None)[0]

    # Transactions follow the same predicate as revenue.
    main_summary = client.get(f'/api/sub/retail/reports/summary?days=365&branch_id={main}').get_json()['data']
    assert main_summary['transactions'] == 3


def test_branch_filter_applies_to_refunds_not_only_to_sales(seeded):
    """The branch predicate has to hit returns.branch_id too. If only the
    sales side were filtered, Downtown's revenue would have Main's $50
    refund deducted from it -- one branch eating another's refund."""
    client = seeded['client']
    downtown = client.get(
        f'/api/sub/retail/reports/summary?days=365&branch_id={seeded["downtown_id"]}').get_json()['data']
    assert downtown['revenue'] == 345.0   # NOT 287.50


# ── 4. top-products lives in the same window as its siblings ─────────────────

def test_top_products_respects_the_same_period_as_its_siblings():
    """top-products had no date filter at all -- it was an all-time query
    sitting inside a days-scoped page, so a single old blockbuster sale
    could make the "top product" out-earn the page's own total. Uses its own
    company: the backdated row below must not leak into the shared fixture."""
    client, cid, pid, main_id, _ = _setup_company()
    _sell(client, pid, main_id, 2)                     # today: 2 units, 115.00 inc. tax

    # A sale 100 days old -- inside a 365-day window, outside a 30-day one.
    # Written raw with tax_rate/discount_pct left at their column defaults of
    # 0, so its 500.00 line is BOTH the taxable base and the tax-inclusive
    # total; top_products re-prices sale lines through pricing.calculate_line
    # (see metrics decision #7) and must come back to the same 500.00 that
    # sales.total carries.
    old_day = (datetime.now() - timedelta(days=100)).strftime('%Y-%m-%d %H:%M:%S')
    conn = get_retail_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sales (company_id,sale_number,branch_id,cashier,subtotal,total,amount_paid,"
        "payment_method,status,idempotency_key,created_at) "
        "VALUES (?,?,?,'POS',500,500,500,'cash','completed',?,?)",
        (cid, f"OLD-{uuid.uuid4().hex[:8]}", main_id, str(uuid.uuid4()), old_day),
    )
    cur.execute(
        "INSERT INTO sale_items (sale_id,product_id,quantity,unit_price,line_total) VALUES (?,?,10,?,500)",
        (cur.lastrowid, pid, PRICE),
    )
    conn.commit()
    conn.close()

    short = client.get('/api/sub/retail/reports/top-products?days=30&limit=8').get_json()
    short_summary = client.get('/api/sub/retail/reports/summary?days=30').get_json()['data']
    long = client.get('/api/sub/retail/reports/top-products?days=365&limit=8').get_json()
    long_summary = client.get('/api/sub/retail/reports/summary?days=365').get_json()['data']

    # The 100-day-old $500 sale is outside the 30-day window for BOTH the
    # widget and the KPI card, and inside it for both at 365 days.
    assert short['revenue'] == [115.0]
    assert short['data'] == [2.0]
    assert short_summary['revenue'] == 115.0
    assert long['revenue'] == [615.0]
    assert long['data'] == [12.0]
    assert long_summary['revenue'] == 615.0

    # The invariant that was actually visible to users: a single product can
    # never report more revenue than the whole page it is drawn on.
    assert sum(short['revenue']) <= short_summary['revenue']


# ── The period boundary, stated once and asserted ────────────────────────────

def test_period_end_is_inclusive_of_today_and_start_is_inclusive_too():
    """`days=n` means "the last n days AND today", both ends inclusive --
    n + 1 calendar dates. A sale exactly n days old is IN; one n+1 days old
    is OUT. This is the boundary every route now shares."""
    client, cid, pid, main_id, _ = _setup_company()

    conn = get_retail_conn()
    cur = conn.cursor()
    for age, total in ((7, 70.0), (8, 80.0)):
        stamp = (datetime.now() - timedelta(days=age)).strftime('%Y-%m-%d %H:%M:%S')
        cur.execute(
            "INSERT INTO sales (company_id,sale_number,branch_id,cashier,subtotal,total,amount_paid,"
            "payment_method,status,idempotency_key,created_at) "
            "VALUES (?,?,?,'POS',?,?,?,'cash','completed',?,?)",
            (cid, f"BND-{uuid.uuid4().hex[:8]}", main_id, total, total, total, str(uuid.uuid4()), stamp),
        )
    conn.commit()
    conn.close()

    _sell(client, pid, main_id, 1)   # today, 57.50 -- proves the end is inclusive

    week = client.get('/api/sub/retail/reports/summary?days=7').get_json()['data']
    assert week['revenue'] == 127.5        # today's 57.50 + the 7-day-old 70, not the 8-day-old 80

    eight = client.get('/api/sub/retail/reports/summary?days=8').get_json()['data']
    assert eight['revenue'] == 207.5       # 57.50 + 70 + 80


# ── A refund query that FAILS must not be reported as "no refunds" ───────────

def _hand_built_db(path, with_refund_amount=True):
    """A tiny hand-built retail database with one sale and (optionally) a
    well-formed `returns` table. Plain sqlite3: these tests are about
    metrics' own error handling, not about the Flask app, so they create
    only the two tables the functions under test read."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    refund_col = 'refund_amount REAL,' if with_refund_amount else ''
    conn.executescript(f"""
        CREATE TABLE sales (id INTEGER PRIMARY KEY, company_id TEXT, branch_id INTEGER,
                            total REAL, payment_method TEXT, created_at TIMESTAMP);
        CREATE TABLE returns (id INTEGER PRIMARY KEY, company_id TEXT, branch_id INTEGER,
                              {refund_col} refund_method TEXT, created_at TIMESTAMP);
    """)
    today = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute("INSERT INTO sales (id,company_id,branch_id,total,payment_method,created_at) "
                 "VALUES (1,'C1',1,690.0,'cash',?)", (today,))
    if with_refund_amount:
        conn.execute("INSERT INTO returns (id,company_id,branch_id,refund_amount,refund_method,created_at) "
                     "VALUES (1,'C1',1,57.5,'cash',?)", (today,))
    conn.commit()
    return conn


def test_a_missing_returns_table_is_tolerated_but_logged(tmp_path, caplog):
    """The ONE case the tolerance exists for: a hand-built or
    partially-migrated database with no `returns` table at all. metrics
    degrades to "no refunds known" rather than 500-ing the dashboard -- but
    it must say so in the log, because the number it then prints is not the
    number the business made."""
    conn = _hand_built_db(str(tmp_path / 'partial.db'))
    conn.execute("DROP TABLE returns")
    conn.commit()

    period = metrics.period_days(1, datetime.now())
    with caplog.at_level(logging.WARNING):
        assert metrics.revenue(conn, 'C1', period) == 690.0
    conn.close()

    assert any('refund query skipped' in r.getMessage() for r in caplog.records), \
        'Degrading to "no refunds" on a financial figure must leave a WARNING behind, not be silent.'


def test_a_broken_returns_query_raises_instead_of_reporting_gross_as_net(tmp_path):
    """The case the tolerance must NOT cover, and the reason it was narrowed.

    `returns` exists but has no `refund_amount` column -- exactly what a
    half-applied migration or an older backup restored mid-upgrade looks
    like. A blanket `except sqlite3.Error: return []` swallows that and
    reports 690.00: the GROSS figure, printed under a label that says net of
    returns. A real 57.50 refund disappears from a financial screen with no
    error and no log line. Since this helper now sits on the refund leg of
    cogs(), all four bucketed breakdowns and top_products -- paths that
    previously issued no refund query at all -- broadening the swallow along
    with the query would have been a real regression.

    Nothing here may be caught."""
    conn = _hand_built_db(str(tmp_path / 'broken.db'), with_refund_amount=False)

    period = metrics.period_days(1, datetime.now())
    with pytest.raises(sqlite3.OperationalError):
        metrics.revenue(conn, 'C1', period)

    # Same requirement on a bucketed breakdown, which previously issued no
    # refund query at all and so could not have hidden anything.
    with pytest.raises(sqlite3.OperationalError):
        metrics.revenue_by_payment_method(conn, 'C1', period)
    conn.close()


# ── The clock seam is closed, structurally ───────────────────────────────────

def test_period_constructors_refuse_to_read_the_clock_themselves():
    """metrics must never call datetime.now() on its own behalf. It used to
    have its own `from datetime import datetime`, and a test that freezes
    api.retail_api.datetime does not freeze that copy -- so a route which let
    metrics read the clock silently measured the wall clock while the test
    believed it was frozen. That is how the hourly-chart regression got
    through, and only dashboard_stats had been fixed. `now` is a REQUIRED
    argument now, so forgetting it on the next route is an immediate, loud
    TypeError rather than a quietly wrong window."""
    with pytest.raises(TypeError):
        metrics.period_days(30)
    for ctor in (metrics.period_today, metrics.period_yesterday,
                 metrics.period_all_time, metrics.period_month_to_date):
        with pytest.raises(TypeError):
            ctor()

    assert not hasattr(metrics, 'datetime'), (
        'core/retail/metrics.py must not import `datetime` at all -- importing it '
        'is what makes reading the clock in here possible in the first place.')


def test_every_report_route_honours_a_frozen_clock():
    """The seam, end to end. With api.retail_api.datetime frozen to a date
    the fixture's sales are nowhere near, EVERY period-taking report route
    must return an empty window. Any route that still let metrics read the
    real clock would report today's sales here and give itself away."""
    client, cid, pid, main_id, _ = _setup_company()
    _sell(client, pid, main_id, 2)

    import api.retail_api as retail_api_module

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2019, 6, 15, 12, 0, 0)

    original = retail_api_module.datetime
    retail_api_module.datetime = _FrozenDateTime
    try:
        summary = client.get('/api/sub/retail/reports/summary?days=30').get_json()['data']
        trend = client.get('/api/sub/retail/reports/sales-trend?days=30').get_json()
        pay = client.get('/api/sub/retail/reports/payment-methods?days=30').get_json()
        top = client.get('/api/sub/retail/reports/top-products?days=30&limit=8').get_json()
        by_branch = client.get('/api/sub/retail/reports/by-branch?days=30').get_json()
    finally:
        retail_api_module.datetime = original

    assert summary['revenue'] == 0, 'report_summary ignored the frozen clock'
    assert trend['data'] == [], 'report_sales_trend ignored the frozen clock'
    assert pay['data'] == [], 'report_payment_methods ignored the frozen clock'
    assert top['revenue'] == [], 'report_top_products ignored the frozen clock'
    assert sum(by_branch['data']) == 0, 'report_by_branch ignored the frozen clock'
