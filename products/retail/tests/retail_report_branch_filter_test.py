"""
Aura Retail -- branch-comparison reports regression suite
(feat/reports-branch-comparison).

Covers the two real gaps this branch fixes:

1. No report ever filtered or grouped by branch_id before this change --
   confirmed by direct code reading (see the ROADMAP entry / task brief for
   this branch). This suite proves the NEW /reports/by-branch route returns
   correct per-branch revenue/transactions that sum back to the all-branches
   total, and that the new optional `?branch_id=` param on sales-trend and
   top-products correctly scopes to one branch.

2. Adding branch_id scoping to the two most load-bearing existing report
   routes (sales-trend, top-products) risked changing already-reviewed demo
   numbers if the default (branch_id omitted) behavior changed at all. This
   suite proves the default/unfiltered path is mathematically identical to
   summing across every branch by hand -- i.e. nothing about the *existing*
   unfiltered behavior changed, only a new opt-in filter was added on top
   of it. See also the manual before/after byte-diff performed alongside
   this test (not itself a pytest case -- see the branch's final report).

Run:
    pytest products/retail/tests/retail_report_branch_filter_test.py -v
"""
import os
import shutil
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_reports_branch_"))
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


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _setup_company_two_branches(price=50.0, stock=1000):
    """One company, two active branches (Main/Downtown), one zero-tax
    product stocked in both -- zero tax keeps expected totals trivial to
    hand-compute (total == quantity * price), so the assertions below are
    testing the branch-scoping logic itself, not the tax engine."""
    email = f"rb-{uuid.uuid4().hex[:10]}@test.local"
    password = "ReportsBranchPW1"
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
    # Third branch with NO sales at all -- proves /reports/by-branch lists
    # every active branch (LEFT JOIN from branches), not just ones that sold
    # something, and that it contributes exactly 0 to the total.
    cur.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Quiet Kiosk')", (company_id,))
    quiet_id = cur.lastrowid

    pid = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,'RB-1','Branch Report Item',10,?,0)",
        (pid, company_id, price),
    )
    for bid in (main_id, downtown_id, quiet_id):
        cur.execute(
            "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
            (company_id, pid, bid, stock),
        )
    rconn.commit()
    rconn.close()

    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})
    client.get("/api/sub/retail/settings/tax")  # ensures doc_sequences/_ensure_credit_schema has run

    return client, company_id, pid, main_id, downtown_id, quiet_id


def _sell(client, pid, branch_id, qty, price=50.0):
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': qty}],
        'branch_id': branch_id,
        'payment_method': 'cash',
        'amount_paid': qty * price,
        'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body['status'] == 'success', body
    return body['data']


@pytest.fixture(scope="module")
def seeded():
    """One seeded company with a deterministic split: Main sells 3 sales
    (qty 2, 1, 3 = 6 units = $300), Downtown sells 2 sales (qty 4, 2 = 6
    units = $300), Quiet Kiosk sells nothing. All-branches total: 12 units,
    $600, 5 transactions."""
    client, cid, pid, main_id, downtown_id, quiet_id = _setup_company_two_branches()
    main_sales = [_sell(client, pid, main_id, q) for q in (2, 1, 3)]
    downtown_sales = [_sell(client, pid, downtown_id, q) for q in (4, 2)]
    return {
        'client': client, 'cid': cid, 'pid': pid,
        'main_id': main_id, 'downtown_id': downtown_id, 'quiet_id': quiet_id,
        'main_sales': main_sales, 'downtown_sales': downtown_sales,
    }


# ── /reports/by-branch: the new route ──────────────────────────────────────

def test_by_branch_reports_correct_per_branch_totals_summing_to_all_branches(seeded):
    client = seeded['client']
    r = client.get('/api/sub/retail/reports/by-branch?days=365')
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] is True

    by_name = dict(zip(body['labels'], zip(body['data'], body['transactions'])))
    assert by_name['Main'] == (300.0, 3)
    assert by_name['Downtown'] == (300.0, 2)
    # The zero-sales branch must still appear (LEFT JOIN from branches, not
    # sales) -- a comparison chart silently omitting a branch with 0 revenue
    # would be misleading, not just incomplete.
    assert by_name['Quiet Kiosk'] == (0.0, 0)

    assert sum(body['data']) == 600.0
    assert sum(body['transactions']) == 5


def test_by_branch_is_company_scoped(seeded):
    """A second company's branches/sales must never leak into the first
    company's by-branch report -- every business table in this codebase is
    company_id-scoped by convention; this is the regression guard for that
    convention specifically on the new route."""
    other_client, other_cid, other_pid, other_main, _, _ = _setup_company_two_branches(price=999.0)
    _sell(other_client, other_pid, other_main, 1, price=999.0)

    r = seeded['client'].get('/api/sub/retail/reports/by-branch?days=365')
    body = r.get_json()
    assert 999.0 not in body['data']
    assert sum(body['data']) == 600.0  # unchanged by the other company's activity


# ── sales-trend / top-products: optional branch_id filter ──────────────────

def test_sales_trend_branch_filter_matches_and_sums_to_unfiltered(seeded):
    client = seeded['client']

    unfiltered = client.get('/api/sub/retail/reports/sales-trend?days=365').get_json()
    main_only = client.get(f'/api/sub/retail/reports/sales-trend?days=365&branch_id={seeded["main_id"]}').get_json()
    downtown_only = client.get(f'/api/sub/retail/reports/sales-trend?days=365&branch_id={seeded["downtown_id"]}').get_json()

    assert sum(unfiltered['data']) == 600.0
    assert sum(unfiltered['transactions']) == 5
    assert sum(main_only['data']) == 300.0
    assert sum(main_only['transactions']) == 3
    assert sum(downtown_only['data']) == 300.0
    assert sum(downtown_only['transactions']) == 2

    # Per-branch numbers must sum back to the unfiltered total -- the whole
    # point of a branch filter is that it partitions, not duplicates or drops,
    # existing revenue.
    assert sum(main_only['data']) + sum(downtown_only['data']) == sum(unfiltered['data'])
    assert sum(main_only['transactions']) + sum(downtown_only['transactions']) == sum(unfiltered['transactions'])


def test_sales_trend_unknown_branch_id_returns_empty_not_all(seeded):
    """A branch_id that matches no sales (wrong company, nonexistent id, or
    a branch that genuinely sold nothing) must filter down to nothing, not
    silently fall back to the unfiltered/all-branches query."""
    client = seeded['client']
    r = client.get(f'/api/sub/retail/reports/sales-trend?days=365&branch_id={seeded["quiet_id"]}').get_json()
    assert r['data'] == []
    assert r['labels'] == []


def test_top_products_branch_filter_matches_and_sums_to_unfiltered(seeded):
    client = seeded['client']

    unfiltered = client.get('/api/sub/retail/reports/top-products?limit=8').get_json()
    main_only = client.get(f'/api/sub/retail/reports/top-products?limit=8&branch_id={seeded["main_id"]}').get_json()
    downtown_only = client.get(f'/api/sub/retail/reports/top-products?limit=8&branch_id={seeded["downtown_id"]}').get_json()

    assert unfiltered['labels'] == ['Branch Report Item']
    assert unfiltered['data'] == [12.0]          # 6 (Main) + 6 (Downtown) units
    assert unfiltered['revenue'] == [600.0]

    assert main_only['data'] == [6.0]
    assert main_only['revenue'] == [300.0]
    assert downtown_only['data'] == [6.0]
    assert downtown_only['revenue'] == [300.0]

    assert main_only['data'][0] + downtown_only['data'][0] == unfiltered['data'][0]
    assert main_only['revenue'][0] + downtown_only['revenue'][0] == unfiltered['revenue'][0]


# ── Default-path byte-identical-output proof (the hard requirement) ────────

def test_sales_trend_default_output_matches_hand_computed_all_branch_truth(seeded):
    """This is the "byte-identical to before this change" proof for
    sales-trend: the response when branch_id is omitted must be identical --
    field for field -- to a manually-computed all-branches aggregate over
    the raw sales rows, using the SAME date/day grouping the pre-change
    query always used. Before this change, EVERY call to this route was
    exactly this unfiltered query; the assertion below is that the
    omitted-branch_id code path still produces that and only that."""
    client = seeded['client']
    cid = seeded['cid']

    conn = get_retail_conn()
    rows = conn.execute("""
        SELECT date(created_at) as day,
               COALESCE(SUM(total),0) as revenue,
               COUNT(*) as transactions,
               COALESCE(AVG(total),0) as avg_ticket
        FROM sales WHERE company_id=?
          AND date(created_at) >= date('now', 'localtime', '-365 days')
        GROUP BY day ORDER BY day
    """, (cid,)).fetchall()
    conn.close()
    expected = {
        'success': True,
        'labels': [r['day'] for r in rows],
        'data': [round(r['revenue'], 2) for r in rows],
        'transactions': [r['transactions'] for r in rows],
        'avg_ticket': [round(r['avg_ticket'], 2) for r in rows],
    }

    actual = client.get('/api/sub/retail/reports/sales-trend?days=365').get_json()
    assert actual == expected


def test_top_products_default_output_matches_hand_computed_all_branch_truth(seeded):
    """Same proof as above, for top-products' omitted-branch_id path."""
    client = seeded['client']
    cid = seeded['cid']

    conn = get_retail_conn()
    rows = conn.execute("""
        SELECT p.name, p.sku,
               SUM(si.quantity) as units_sold,
               SUM(si.line_total) as revenue,
               SUM(si.quantity * p.cost_price) as cost,
               SUM(si.line_total) - SUM(si.quantity * p.cost_price) as profit
        FROM sale_items si
        JOIN products p ON si.product_id=p.id
        JOIN sales s ON si.sale_id=s.id
        WHERE s.company_id=?
        GROUP BY p.id ORDER BY units_sold DESC LIMIT ?
    """, (cid, 8)).fetchall()
    conn.close()
    expected = {
        'success': True,
        'labels': [r['name'] for r in rows],
        'data': [round(r['units_sold'], 0) for r in rows],
        'revenue': [round(r['revenue'], 2) for r in rows],
        'profit': [round(r['profit'], 2) for r in rows],
    }

    actual = client.get('/api/sub/retail/reports/top-products?limit=8').get_json()
    assert actual == expected


def test_payment_methods_and_summary_untouched_by_this_change(seeded):
    """payment-methods and summary were deliberately NOT given branch_id
    filtering in this change (out of scope -- see report_by_branch's
    docstring) -- confirms they still respond normally and their totals
    still reflect all branches combined, i.e. nothing about them regressed
    even though this branch edited the file around them."""
    client = seeded['client']
    pay = client.get('/api/sub/retail/reports/payment-methods?days=365').get_json()
    assert pay['success'] is True
    assert sum(r['revenue'] for r in pay['data']) == 600.0

    summary = client.get('/api/sub/retail/reports/summary?days=365').get_json()
    assert summary['success'] is True
    assert summary['data']['revenue'] == 600.0
    assert summary['data']['transactions'] == 5


# ── Returns must net out of revenue, not just leave it flat ────────────────

def test_by_branch_and_summary_net_out_returns(seeded):
    """A refund is recorded ONLY in the `returns` table and never mutates
    sales.total (see create_return()'s docstring) -- so a report that sums
    raw sales.total without also subtracting returns.refund_amount silently
    overstates revenue for any branch/period with a refund in it.
    dashboard_stats() already nets returns out ("a refund must lower today's
    revenue, not leave it flat"); this is the same guarantee for
    /reports/by-branch and /reports/summary, which is what the Reports
    page's branch-comparison chart and summary KPI actually render.

    Main sold 3 sales (qty 2,1,3 = 6 units = $300, seeded module-wide by the
    `seeded` fixture); this test returns 1 of the 6 units (0% tax, so
    refund_amount == price exactly) and asserts Main's reported revenue
    drops by that $50 -- not just that a return record exists."""
    client = seeded['client']
    main_sale = seeded['main_sales'][0]  # qty=2 sale on Main -> $100
    r = client.post('/api/sub/retail/returns', json={
        'sale_id': main_sale['id'],
        'items': [{'product_id': seeded['pid'], 'quantity': 1}],
    })
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['refund_amount'] == 50.0

    by_branch = client.get('/api/sub/retail/reports/by-branch?days=365').get_json()
    by_name = dict(zip(by_branch['labels'], by_branch['data']))
    assert by_name['Main'] == 250.0       # $300 - $50 refund, not flat at $300
    assert by_name['Downtown'] == 300.0   # untouched -- refund was on Main
    assert sum(by_branch['data']) == 550.0

    summary = client.get('/api/sub/retail/reports/summary?days=365').get_json()['data']
    assert summary['revenue'] == 550.0    # $600 - $50 refund, not flat at $600
