"""Aura Retail -- core/retail/financial_statements.py::income_statement,
proven HTTP-driven through the real Flask routes (create_sale, create_
returns, purchase-orders, customers/payments) the same level retail_gl_
integration_test.py already proves schema v38's trial balance at, and
retail_gl_cogs_test.py proves schema v39's COGS posting at. This file does
not call `income_statement` against a hand-built journal anywhere -- every
figure it asserts came out of a real document ringing through a real route,
then read back through `financial_statements.income_statement` directly
(there is no HTTP round trip for the read side in most tests here, matching
retail_gl_integration_test.py's own `_trial_balance` helper's posture: the
route itself -- `report_profit_and_loss` -- gets its OWN dedicated coverage
in the "route / capability" section near the bottom of this file, separate
from the accounting-correctness proofs above it).

THE CENTRAL CLAIMS THIS FILE PROVES, one section each:
  1. Tax is excluded from revenue (it is a liability, 2100, never summed).
  2. cost_of_goods_sold is exactly the ledger's own COGS balance, and
     gross_profit = revenue - cost_of_goods_sold, hand-computed.
  3. A return reduces revenue (via the 4900 contra-revenue account) AND
     reduces cost_of_goods_sold (via the COGS mirror), and gross_profit
     lands exactly where hand arithmetic over the SETTLED figures says.
  4. A loyalty redemption is an EXPENSE line, not a discount folded into
     revenue -- revenue stays at the full pre-redemption figure.
  5. Period boundaries: `business_date` strictly outside [start,end] is
     excluded; a document dated ON either boundary is INCLUDED (both ends
     inclusive, matching `_export_date_range`'s own convention elsewhere in
     this codebase).
  6. The pre-v39/unknown-cost-basis honesty flag fires precisely when a
     sale in the period has an unresolved cost snapshot, and does not fire
     when every sale in the period carries a real one.
  7. An empty period reports net_profit=0 with `has_activity=False` --
     "nothing was posted here", never mistaken for "the shop broke even".
  8. The by-account-TYPE derivation is real: a brand-new expense account,
     added to the chart in this file's own fixture (never in financial_
     statements.py), appears in `operating_expenses` with zero changes to
     the module under test.
  9. The route itself: shape, capability gate, and period/branch_id
     validation.

MUTATION PROOFS (performed by hand against financial_statements.py during
development, each restored and reconfirmed green -- see this task's own
report for the real RED/GREEN output quoted from each):
  M1 -- `test_revenue_is_net_of_returns_via_contra_revenue_account`: the
        contra-revenue sign. Mutating the TYPE-canonical net to instead use
        each account's OWN normal_balance (trial_balance's own convention)
        makes 4900 ADD to revenue instead of subtract -- this test catches
        it (expects 0.0 net revenue after a full return; a broken sign
        reports 100.0, double the gross).
  M2 -- `test_cogs_equals_sum_of_cost_snapshots_and_gross_profit_is_revenue_minus_cogs`:
        flipping `gross_profit = revenue - cogs` to `+ cogs`.
  M3 -- `test_loyalty_redemption_appears_as_an_expense_not_a_revenue_discount`:
        hardcoding a skip for `gl.ACCOUNT_LOYALTY_EXPENSE` in the opex loop
        (the exact "frozen list drifts" defect class this module's design
        exists to avoid) makes this test's operating_expenses assertion
        fail.
  M4 -- `test_cost_basis_incomplete_flag_fires_for_unknown_cost_sale`:
        forcing `cost_basis_incomplete`/count to always report clean.

Self-contained bootstrap, matching retail_gl_integration_test.py's own
convention (no shared conftest.py exists for products/retail/tests/). One
company per test via the `client` fixture.

CRITICAL: exactly ONE pytest process per file (AUDIT-010) -- these test
files pollute each other in a shared pytest process; see products/
run_all_tests.py.

Run:
    pytest products/retail/tests/retail_profit_and_loss_test.py -q
"""
import datetime
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

DATA = Path(tempfile.mkdtemp(prefix="aura_pnl_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.mt_auth import CAPABILITY_DENIED_MESSAGE  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from core.retail import general_ledger as gl  # noqa: E402
from core.retail import financial_statements as fin  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    email = f'pnltest-{uuid.uuid4().hex[:8]}@test.local'
    password = 'PnlIntegrationPW1'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'))
    conn.commit(); conn.close()

    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    c.test_company_id = company_id
    return c


def _make_cashier():
    """A real cashier account -- CAP_REPORTS-less, matching retail_gl_route_
    test.py's own `_make_cashier`, reused here for the identical capability-
    gate proof on this file's own new route."""
    email = f'pnl-cashier-{uuid.uuid4().hex[:8]}@test.local'
    password = 'PnlCashierPW1'
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, 'EMP-0001', email, hash_password(password), 'cashier', 'active'))
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, 'retail', 'full'))
    user_accounts.seed_capabilities_for_user(conn, user_id, 'cashier')
    conn.commit(); conn.close()

    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return c


# ── Seed helpers ─────────────────────────────────────────────────────────────

def _seed_product(c, **kw):
    # `cost_price=0` BY DEFAULT -- unlike retail_gl_integration_test.py's own
    # `_seed_product` (cost_price=1.0). A P&L test's whole point is usually
    # revenue/returns/loyalty math, and a nonzero cost_price would silently
    # post a COGS journal (create_sale's own avg_cost-else-cost_price
    # fallback) that entangles gross_profit into every test that does not
    # actually care about it. Tests that DO care about COGS set a real cost
    # basis explicitly via a PO receipt (`_po_receipt` below), matching
    # retail_gl_cogs_test.py's own convention.
    payload = {'name': 'PNL Widget', 'sku': f'PNLW-{uuid.uuid4().hex[:8]}', 'cost_price': 0,
               'sell_price': 10.0, 'tax_rate': 0, 'initial_stock': 100}
    payload.update(kw)
    r = c.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_customer(c):
    r = c.post(f'{API}/customers', json={'name': f'PNL Customer {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_supplier(c):
    r = c.post(f'{API}/suppliers', json={'name': f'PNL Supplier {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _set_customer_credit(cust_id, mode='unlimited', limit=1000000):
    conn = get_retail_conn()
    conn.execute("UPDATE customers SET credit_mode=?, credit_limit=? WHERE id=?", (mode, limit, cust_id))
    conn.commit(); conn.close()


def _set_loyalty_point_value(c, value):
    r = c.post(f'{API}/settings/credit', json={'loyalty_point_value': value})
    assert r.status_code == 200, r.get_json()


def _grant_ledger_points(company_id, customer_id, points):
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type,created_by) "
        "VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), company_id, customer_id, points, 'adjust', 'test-fixture'))
    conn.commit(); conn.close()


def _create_po(c, supplier_id, pid, qty, unit_cost, amount_paid=0):
    r = c.post(f'{API}/purchase-orders', json={
        'supplier_id': supplier_id,
        'items': [{'product_id': pid, 'quantity': qty, 'unit_cost': unit_cost}],
        'amount_paid': amount_paid})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _receive_po(c, po_id):
    r = c.post(f'{API}/purchase-orders/{po_id}/receive')
    assert r.status_code == 200, r.get_json()


def _po_receipt(c, supplier_id, pid, qty, unit_cost, amount_paid=0):
    """Create + receive in one call -- `products.avg_cost` is maintained
    ONLY by receive_purchase_order (see retail_gl_cogs_test.py's own
    `_po_receipt` docstring for the full reasoning); every test here that
    needs a REAL cost basis goes through both steps."""
    po_id = _create_po(c, supplier_id, pid, qty, unit_cost, amount_paid)
    _receive_po(c, po_id)
    return po_id


# ── Read helpers ─────────────────────────────────────────────────────────────

def _income_statement(cid, start_date, end_date, branch_id=None):
    conn = get_retail_conn()
    try:
        return fin.income_statement(conn, cid, start_date, end_date, branch_id=branch_id)
    finally:
        conn.close()


def _sale_item_id(c, sale_id, index=0):
    r = c.get(f'{API}/sales/{sale_id}')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['items'][index]['id']


def _trial_balance(cid):
    conn = get_retail_conn()
    try:
        return gl.trial_balance(conn, cid)
    finally:
        conn.close()


def _account(tb, code):
    return [a for a in tb['accounts'] if a['code'] == code][0]


def _today():
    return datetime.date.today().strftime('%Y-%m-%d')


# ── Test-only direct writes -- see each call site's own comment for why no
#    route can produce the state being constructed (matching retail_gl_cogs_
#    test.py's own established convention for this class of fixture) ───────

def _force_sale_item_unit_cost(sale_item_id, unit_cost):
    """Simulates a PRE-v39 `sale_items` row whose `unit_cost` sits at the
    column default 0 because it predates the migration that started
    snapshotting one at all -- identical to retail_gl_cogs_test.py's own
    helper of the same name; see that file's docstring for the full
    reasoning. Used here to prove the honesty flag detects the REAL
    pre-v39 shape (a line that DID have a cost basis at sale time, forced
    back to the sentinel), not merely the "never had one" shape the
    default `_seed_product(cost_price=0)` already produces."""
    conn = get_retail_conn()
    conn.execute("UPDATE sale_items SET unit_cost=? WHERE id=?", (unit_cost, sale_item_id))
    conn.commit(); conn.close()


# ═════════════════════════════════════════════════════════════════════════
# 1. Tax is a liability, never revenue
# ═════════════════════════════════════════════════════════════════════════

def test_taxed_cash_sale_reports_revenue_net_of_tax(client):
    cid = client.test_company_id
    pid = _seed_product(client, sell_price=10.0, tax_rate=10)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 2}],
        'payment_method': 'cash', 'amount_paid': 22.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    # The tax really posted (2100 Tax Payable, a LIABILITY) -- proven via
    # the trial balance, not merely assumed -- so this test's revenue
    # assertion below is meaningful (a route that posted no tax at all
    # would trivially also report revenue==subtotal).
    tb = _trial_balance(cid)
    assert _account(tb, gl.ACCOUNT_TAX_PAYABLE)['balance'] == 2.0

    today = _today()
    data = _income_statement(cid, today, today)
    assert data['revenue'] == 20.0, (
        f"revenue included tax: got {data['revenue']}, expected 20.0 "
        f"(subtotal only -- the 2.00 tax is a liability, not income)")
    assert data['gross_revenue'] == 20.0
    assert data['sales_returns'] == 0.0


# ═════════════════════════════════════════════════════════════════════════
# 2. COGS == sum of cost snapshots; gross_profit = revenue - COGS
# ═════════════════════════════════════════════════════════════════════════

def test_cogs_equals_sum_of_cost_snapshots_and_gross_profit_is_revenue_minus_cogs(client):
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=10.0, tax_rate=0, initial_stock=0)
    _po_receipt(client, supp_id, pid, qty=10, unit_cost=4.0, amount_paid=40.0)

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 3}],
        'payment_method': 'cash', 'amount_paid': 30.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    # Hand arithmetic: revenue = 3*10.00 = 30.00; cogs = 3*4.00 = 12.00;
    # gross_profit = 30.00 - 12.00 = 18.00; gross_margin = 18/30 = 0.6.
    today = _today()
    data = _income_statement(cid, today, today)
    assert data['revenue'] == 30.0
    assert data['cost_of_goods_sold'] == 12.0
    assert data['gross_profit'] == 18.0
    assert data['gross_margin'] == pytest.approx(0.6)
    assert data['cost_basis_incomplete'] is False
    assert data['cost_basis_incomplete_sale_count'] == 0


# ═════════════════════════════════════════════════════════════════════════
# 3. A return reduces revenue (4900) AND cost_of_goods_sold (COGS mirror)
# ═════════════════════════════════════════════════════════════════════════

def test_revenue_is_net_of_returns_via_contra_revenue_account(client):
    """Isolated from COGS entirely (cost_price=0, the default) so this test
    proves ONLY the contra-revenue arithmetic. A full return of the sale's
    one line must bring net revenue back to exactly 0.00 -- gross_revenue
    and sales_returns must each independently show the real figures, not
    cancel silently to a coincidental 0 from unrelated code."""
    cid = client.test_company_id
    pid = _seed_product(client, sell_price=50.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 2}],
        'payment_method': 'cash', 'amount_paid': 100.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']

    item_id = _sale_item_id(client, sale['id'])
    r = client.post(f'{API}/returns', json={
        'sale_id': sale['id'],
        'items': [{'product_id': pid, 'quantity': 2, 'sale_item_id': item_id}],
        'reason': 'pnl contra-revenue test', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    today = _today()
    data = _income_statement(cid, today, today)
    assert data['gross_revenue'] == 100.0
    assert data['sales_returns'] == 100.0
    assert data['revenue'] == 0.0, (
        f"a full return must bring net revenue to 0.00; got {data['revenue']} "
        f"-- the contra-revenue account subtracted instead of adding, or "
        f"vice versa")


def test_partial_return_reduces_both_revenue_and_cogs_and_gross_profit_lands_exactly(client):
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=10.0, tax_rate=0, initial_stock=0)
    _po_receipt(client, supp_id, pid, qty=10, unit_cost=4.0, amount_paid=40.0)

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 4}],
        'payment_method': 'cash', 'amount_paid': 40.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']

    item_id = _sale_item_id(client, sale['id'])
    r = client.post(f'{API}/returns', json={
        'sale_id': sale['id'],
        'items': [{'product_id': pid, 'quantity': 1, 'sale_item_id': item_id}],
        'reason': 'pnl partial return test', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    # Hand arithmetic: gross_revenue = 4*10=40.00, sales_returns = 1*10=10.00
    # -> revenue = 30.00. cogs = 4*4=16.00 sold, return restocks 1*4=4.00
    # -> cost_of_goods_sold = 12.00. gross_profit = 30.00 - 12.00 = 18.00.
    today = _today()
    data = _income_statement(cid, today, today)
    assert data['gross_revenue'] == 40.0
    assert data['sales_returns'] == 10.0
    assert data['revenue'] == 30.0
    assert data['cost_of_goods_sold'] == 12.0
    assert data['gross_profit'] == 18.0


# ═════════════════════════════════════════════════════════════════════════
# 4. Loyalty redemption is an EXPENSE, not a discount against revenue
# ═════════════════════════════════════════════════════════════════════════

def test_loyalty_redemption_appears_as_an_expense_not_a_revenue_discount(client):
    cid = client.test_company_id
    cust_id = _seed_customer(client)
    _set_loyalty_point_value(client, 1)  # 1 point = 1 currency unit
    _grant_ledger_points(cid, cust_id, 20)
    pid = _seed_product(client, sell_price=100.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'customer_id': cust_id,
        'points_redeemed': 15, 'amount_paid': 100.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['points_redeemed_amount'] == 15.0

    today = _today()
    data = _income_statement(cid, today, today)
    # Revenue is the FULL pre-redemption figure -- redemption never touches
    # 4000/4900 at all (see general_ledger.post_sale's own docstring).
    assert data['revenue'] == 100.0, (
        f"revenue was reduced by the redemption ({data['revenue']}) -- "
        f"loyalty redemption must be booked as an EXPENSE, never folded "
        f"into revenue as a discount")
    loyalty_lines = [a for a in data['operating_expenses']['accounts']
                      if a['code'] == gl.ACCOUNT_LOYALTY_EXPENSE]
    assert len(loyalty_lines) == 1, data['operating_expenses']
    assert loyalty_lines[0]['amount'] == 15.0
    assert data['operating_expenses']['total'] == 15.0
    assert data['net_profit'] == 100.0 - 15.0  # no COGS (cost_price=0 default)


# ═════════════════════════════════════════════════════════════════════════
# 5. Period boundaries: outside excluded, on-boundary included, inside included
# ═════════════════════════════════════════════════════════════════════════

def test_period_boundaries_are_both_ends_inclusive(client, monkeypatch):
    """No route lets a caller override `business_date` -- every real call
    site in retail_api.py calls `gl.post_sale`/`gl.post_cogs` with no
    `business_date` argument, so it always defaults to `general_ledger.
    _today()` (see that module's own `post_journal`). `gl_journals` is also
    append-only by TRIGGER (a raw test-only `UPDATE ... SET business_date`
    was tried first and rejected outright by
    `trg_gl_journals_no_update` -- see general_ledger.py's module
    docstring), so a boundary test cannot construct its dated documents by
    writing around the ledger either.

    The sanctioned way to get a REAL route to post a REAL journal dated
    something other than today is to control what `_today()` itself
    returns for the duration of one call -- `post_sale`/`post_cogs` look
    `_today` up in `general_ledger`'s own module globals at CALL time (it
    is a bare name, not a bound default), so monkeypatching `gl._today`
    reaches every posting helper transitively, exactly as if that sale had
    really rung on that date."""
    cid = client.test_company_id
    # `initial_stock=500` -- the five sales below total 150 units (10+20+30+
    # 40+50), past this file's own default of 100 (_seed_product's own
    # default matches retail_gl_integration_test.py's), which is unrelated
    # to what THIS test is proving and must not mask it with an unrelated
    # insufficient-stock 400.
    pid = _seed_product(client, sell_price=1.0, tax_rate=0, initial_stock=500)

    amounts_and_dates = [
        (10.0, '2026-01-04'),  # one day BEFORE start -- excluded
        (20.0, '2026-01-05'),  # == start_date -- included (boundary)
        (30.0, '2026-01-10'),  # strictly inside -- included
        (40.0, '2026-01-15'),  # == end_date -- included (boundary)
        (50.0, '2026-01-16'),  # one day AFTER end -- excluded
    ]
    for amount, business_date in amounts_and_dates:
        monkeypatch.setattr(gl, '_today', lambda d=business_date: d)
        r = client.post(f'{API}/sales', json={
            'items': [{'product_id': pid, 'quantity': amount}],  # sell_price 1.0 -> total==amount
            'payment_method': 'cash', 'amount_paid': amount,
            'idempotency_key': str(uuid.uuid4())})
        assert r.status_code == 200, r.get_json()

    data = _income_statement(cid, '2026-01-05', '2026-01-15')
    # 20 (start boundary) + 30 (inside) + 40 (end boundary) = 90.00
    assert data['revenue'] == 90.0, (
        f"expected 90.00 (the three in-window sales), got {data['revenue']} "
        f"-- a boundary date was wrongly included or excluded")


def test_non_padded_period_dates_still_find_a_sale_on_a_single_digit_day(client, monkeypatch):
    """P0 round 2 (item B2, adversarial-verifier finding): the guard this
    file's own test above proves is period BOUNDARIES, never that a
    non-zero-padded `start_date`/`end_date` STRING resolves correctly at
    all -- every date it ever passes ('2026-01-04' etc.) is already
    zero-padded, so a caller-side padding bug could ship with this whole
    file green. `_parse_period_date` is deliberately LENIENT about a
    non-padded month/day (`datetime.strptime` parses '2026-9-5' to the same
    calendar date as '2026-09-05'), but `gl_journals.business_date` is
    ALWAYS stored zero-padded ISO (every real posting site dates through
    `general_ledger._today()`, which is always `strftime('%Y-%m-%d')`) and
    SQLite compares that column as TEXT. Binding the ORIGINAL, non-padded
    `start_date`/`end_date` string straight into that comparison -- rather
    than the PARSED date's own zero-padded `.isoformat()` -- silently
    excludes a real, in-period sale: `'2026-09-05' >= '2026-9-5'` is FALSE
    as a text comparison (the 6th character '0' sorts before '9'), even
    though 2026-09-05 the calendar date is well inside [2026-9-5, 2026-9-5].
    Mutation: comment out `start_date, end_date = start.isoformat(),
    end.isoformat()` in `income_statement` and this goes red."""
    cid = client.test_company_id
    pid = _seed_product(client, sell_price=25.0, tax_rate=0, initial_stock=10)

    monkeypatch.setattr(gl, '_today', lambda: '2026-09-05')  # a REAL, padded business_date
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 25.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    # The CALLER passes a non-padded single-digit month and day -- exactly
    # the shape a naive `f'{year}-{month}-{day}'` query-string builder
    # produces, and exactly what `_parse_period_date` accepts without
    # complaint.
    data = _income_statement(cid, '2026-9-5', '2026-9-5')
    assert data['revenue'] == 25.0, (
        f"a sale dated 2026-09-05 must be found by the non-padded period "
        f"'2026-9-5'..'2026-9-5' -- got revenue={data['revenue']} "
        f"(has_activity={data.get('has_activity')}), the exact silent-wrong-"
        f"rows shape a TEXT-lexicographic date comparison produces")
    # The echoed-back period must ALSO be normalized -- a caller that resends
    # `data['period']['start_date']` verbatim must get the SAME rows again,
    # which only holds if it comes back zero-padded.
    assert data['period']['start_date'] == '2026-09-05', data['period']
    assert data['period']['end_date'] == '2026-09-05', data['period']


# ═════════════════════════════════════════════════════════════════════════
# 6. The pre-v39 / unknown-cost-basis honesty flag
# ═════════════════════════════════════════════════════════════════════════

def test_cost_basis_incomplete_flag_fires_for_unknown_cost_sale(client):
    cid = client.test_company_id
    # cost_price=0 (this file's own default) AND no PO receipt -- avg_cost
    # stays 0 too -- is "no cost basis known anywhere for this line",
    # exactly the sentinel general_ledger.post_cogs and create_sale's own
    # cost_basis fallback both already treat specially.
    pid = _seed_product(client, sell_price=10.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 10.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    today = _today()
    data = _income_statement(cid, today, today)
    assert data['cost_basis_incomplete'] is True
    assert data['cost_basis_incomplete_sale_count'] == 1


def test_cost_basis_incomplete_flag_fires_for_a_forced_pre_v39_sentinel(client):
    """The REAL pre-v39 shape: a line that DID have a cost basis at sale
    time (a real PO receipt happened), forced back to the sentinel --
    proving detection is not merely catching 'this product never had a
    cost', but the actual historical-migration scenario the flag exists
    for."""
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=10.0, tax_rate=0, initial_stock=0)
    _po_receipt(client, supp_id, pid, qty=10, unit_cost=4.0, amount_paid=40.0)

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 10.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']
    item_id = _sale_item_id(client, sale_id)
    _force_sale_item_unit_cost(item_id, 0)  # forced back to the pre-v39 sentinel

    today = _today()
    data = _income_statement(cid, today, today)
    assert data['cost_basis_incomplete'] is True
    assert data['cost_basis_incomplete_sale_count'] == 1


def test_cost_basis_incomplete_flag_does_not_fire_when_every_sale_has_a_real_snapshot(client):
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=10.0, tax_rate=0, initial_stock=0)
    _po_receipt(client, supp_id, pid, qty=10, unit_cost=4.0, amount_paid=40.0)

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 2}],
        'payment_method': 'cash', 'amount_paid': 20.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    today = _today()
    data = _income_statement(cid, today, today)
    assert data['cost_basis_incomplete'] is False
    assert data['cost_basis_incomplete_sale_count'] == 0


# ═════════════════════════════════════════════════════════════════════════
# 7. An empty period is "nothing posted", never "you broke even"
# ═════════════════════════════════════════════════════════════════════════

def test_empty_period_reports_zero_net_profit_and_no_activity_flag(client):
    cid = client.test_company_id  # fresh company, nothing rung at all
    today = _today()
    data = _income_statement(cid, today, today)
    assert data['net_profit'] == 0.0
    assert data['revenue'] == 0.0
    assert data['gross_profit'] == 0.0
    assert data['cost_of_goods_sold'] == 0.0
    assert data['operating_expenses'] == {'total': 0.0, 'accounts': []}
    assert data['has_activity'] is False, (
        "an empty period must report has_activity=False, distinguishing "
        "'nothing was posted' from a real zero-profit result")
    assert data['gross_margin'] is None, (
        "gross_margin must be None (undefined) when there is no revenue to "
        "measure against, never a misleading 0.0")
    assert data['cost_basis_incomplete'] is False
    assert data['cost_basis_incomplete_sale_count'] == 0


# ═════════════════════════════════════════════════════════════════════════
# 8. The by-account-TYPE derivation is real -- a new chart account, zero
#    changes to financial_statements.py
# ═════════════════════════════════════════════════════════════════════════

def test_a_newly_added_expense_account_appears_in_opex_without_touching_the_module(client):
    """Direct proof of the module's own design claim: operating_expenses is
    derived generically by account_type=='expense', never from an
    enumerated code list. A brand-new expense account ('5100 Rent
    Expense') is added to THIS company's own chart directly (no create-
    account route exists in v38/v39 -- see general_ledger.py's own "NOT
    BUILT" note) and a real balanced journal is posted against it via
    `general_ledger.post_journal` directly (Dr Rent Expense / Cr Cash --
    the same primitive every route in this codebase already goes
    through). financial_statements.py is touched NOT AT ALL. If the new
    account shows up in operating_expenses anyway, the by-type derivation
    is proven, not merely documented."""
    cid = client.test_company_id
    conn = get_retail_conn()
    gl.ensure_default_chart(conn, cid)
    conn.execute(
        "INSERT INTO gl_accounts (company_id, code, name, account_type, normal_balance) "
        "VALUES (?,?,?,?,?)",
        (cid, '5100', 'Rent Expense', 'expense', 'debit'))
    conn.commit()
    today = _today()
    gl.post_journal(
        conn, cid, None, today, 'test_fixture', None, 'JOD',
        [
            {'account_code': '5100', 'debit': 75.0, 'memo': 'Monthly rent'},
            {'account_code': gl.ACCOUNT_CASH, 'credit': 75.0, 'memo': 'Rent paid'},
        ],
        reference='RENT-TEST')
    conn.commit()
    conn.close()

    data = _income_statement(cid, today, today)
    rent_lines = [a for a in data['operating_expenses']['accounts'] if a['code'] == '5100']
    assert len(rent_lines) == 1, data['operating_expenses']
    assert rent_lines[0]['amount'] == 75.0
    assert rent_lines[0]['name'] == 'Rent Expense'
    assert data['operating_expenses']['total'] == 75.0
    assert data['net_profit'] == -75.0


# ═════════════════════════════════════════════════════════════════════════
# 9. The route: shape, capability gate, validation
# ═════════════════════════════════════════════════════════════════════════

def test_route_returns_200_with_the_expected_shape(client):
    pid = _seed_product(client, sell_price=10.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 10.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    today = _today()
    r = client.get(f'{API}/reports/profit-and-loss?start_date={today}&end_date={today}')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['revenue'] == 10.0
    assert data['period'] == {'start_date': today, 'end_date': today, 'branch_id': None}
    assert 'operating_expenses' in data and 'accounts' in data['operating_expenses']


def test_route_refuses_missing_dates_with_400(client):
    r = client.get(f'{API}/reports/profit-and-loss')
    assert r.status_code == 400, r.get_json()


def test_route_refuses_malformed_date_with_400(client):
    r = client.get(f'{API}/reports/profit-and-loss?start_date=not-a-date&end_date=2026-01-31')
    assert r.status_code == 400, r.get_json()


def test_route_refuses_start_after_end_with_400(client):
    r = client.get(f'{API}/reports/profit-and-loss?start_date=2026-02-01&end_date=2026-01-01')
    assert r.status_code == 400, r.get_json()


def test_route_refuses_non_integer_branch_id_with_400(client):
    today = _today()
    r = client.get(
        f'{API}/reports/profit-and-loss?start_date={today}&end_date={today}&branch_id=not-a-number')
    assert r.status_code == 400, r.get_json()


def test_route_refused_without_cap_reports_and_refusal_is_from_the_capability_check():
    """Matches retail_gl_route_test.py's identical proof for /reports/
    trial-balance -- a 403 alone cannot distinguish 'the capability check
    ran and refused' from 'something else refused entirely'
    (CLAUDE.md's engineering standard, failure shape 1)."""
    cashier = _make_cashier()
    today = _today()
    r = cashier.get(f'{API}/reports/profit-and-loss?start_date={today}&end_date={today}')
    assert r.status_code == 403, r.get_json()
    body = r.get_json()
    assert body.get('message') == CAPABILITY_DENIED_MESSAGE, (
        f"refusal did not come from the capability check: {body!r}")
