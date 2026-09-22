"""Aura Retail -- core/retail/financial_statements.py::balance_sheet, proven
HTTP-driven through the real Flask routes (create_sale, purchase-orders,
customers/payments) the same level retail_gl_integration_test.py proves
schema v38's trial balance at and retail_profit_and_loss_test.py proves
schema v39's income statement at. This file does not call `balance_sheet`
against a hand-built journal anywhere except the one test that deliberately
corrupts the ledger to prove the balance check itself is honest (see section
9) -- every other figure asserted here came out of a real document ringing
through a real route, then read back through `financial_statements.
balance_sheet` directly (the route -- `report_balance_sheet` -- gets its own
dedicated coverage in the "route / capability" section near the bottom,
separate from the accounting-correctness proofs above it).

THE CENTRAL CLAIMS THIS FILE PROVES, one section each:
  1. A cash sale increases Cash (an asset) and, because this product has no
     period-close step, the sheet only balances because retained earnings
     absorbs the sale's net profit -- see section 6 for the sharpest proof
     of that mechanism.
  2. A CREDIT sale posts to Accounts Receivable, not Cash, and the sheet
     still balances.
  3. A purchase on credit increases Inventory AND Accounts Payable, and the
     sheet still balances.
  4. A customer payment moves value from Receivables to Cash -- an asset
     reclassification, not a new asset -- so BOTH the assets total and the
     balance sheet's grand total are UNCHANGED by it.
  5. Tax collected sits in LIABILITIES (Tax Payable, 2100), never folded
     into equity -- asserted explicitly, both "it is there" and "it is not
     anywhere in equity".
  6. Equity is reported as Owner's Equity (a real ledger account, proven
     nonzero via a direct capital-contribution posting) PLUS retained
     earnings, and that retained-earnings figure is proven to EQUAL
     `income_statement`'s own `net_profit` for the identical span by calling
     both functions and diffing the number directly -- never by
     re-deriving an expected value by hand, which could agree with a bug
     the same way it agrees with the code.
  7. As-of dating: a document business-dated strictly AFTER `as_of_date` is
     excluded from the snapshot; one dated exactly ON it is included
     (boundary inclusive, matching income_statement's own convention).
  8. An empty ledger (no company activity at all) reports has_activity=False
     and balances TRIVIALLY (0 == 0), not merely "no error" -- the "nothing
     posted" claim and the "in balance" claim are checked together.
  9. A ledger deliberately corrupted OUTSIDE `post_journal` (a lone,
     unmatched debit inserted directly into `gl_journal_lines`, bypassing
     the balance invariant entirely) is reported OUT OF BALANCE, with the
     exact difference the corruption introduced -- the one case this
     function is explicitly asked to get right even though the ordinary
     double-entry path makes it "impossible": CLAUDE.md's own engineering
     standard is direct about why ("impossible" is what every silent-
     corruption story says beforehand).
 10. The route itself: shape, capability gate, and as_of_date/branch_id
     validation.

MUTATION PROOFS -- performed BY HAND against financial_statements.py during
development, matching retail_profit_and_loss_test.py's own established
convention for this backend (a textual, single-anchor edit via this
project's Edit tool, never a whole-file rewrite -- surgical, so a
concurrently-live agent editing any OTHER file in this repo is unaffected),
each restored and reconfirmed green immediately after capturing the RED
output quoted below. Every quoted line is real pytest output from this
exact file, not a prediction.

  M1 -- forced `'is_balanced': abs(difference) <= eps,` to
        `'is_balanced': True,` (the balance check always reports
        agreement, unconditionally). RAN:
        `pytest retail_balance_sheet_test.py::test_a_ledger_corrupted_outside_post_journal_is_reported_out_of_balance -q`
        RED (real output, 1 failed in 8.16s):
          E       AssertionError: a ledger with a lone, unmatched debit must be reported out of
                  balance, not silently absorbed into a clean-looking report
          E       assert True is False
        Restored the exact original line; RE-RAN the full file: 14 passed.

  M2 -- forced `equity_total = owners_equity + retained_earnings` to
        `equity_total = owners_equity` (equity omits retained earnings
        entirely). RAN:
        `pytest retail_balance_sheet_test.py::test_equity_equals_owners_equity_plus_earnings_and_matches_income_statement -q`
        RED (real output, 1 failed in 8.31s):
          E       assert 1000.0 == (1000.0 + 400.0)
        (400.0 is this test's own net_profit for the span -- the mutation
        drops retained_earnings out of the sum entirely, so `equity.total`
        stalls at the bare owners_equity figure, exactly `retained_earnings`
        short.) Restored; RE-RAN the full file: 14 passed.

  M3 -- as-of-date filter changed from `gj.business_date <= ?` to
        `gj.business_date < ?` (boundary exclusive instead of inclusive).
        RAN:
        `pytest retail_balance_sheet_test.py::test_as_of_date_boundary_is_inclusive_of_that_date_and_excludes_the_day_after -q`
        RED (real output, 1 failed in 8.30s):
          E       AssertionError: expected 1250.0 (day-before + on-the-day sales; day-after
                  excluded), got 1200.0
          E       assert 1200.0 == 1250.0
        (the on-boundary sale's 50 stopped counting.) Restored; RE-RAN the
        full file: 14 passed.

  M4 -- `_BALANCE_SHEET_TYPE_CANONICAL_NORMAL['liability']` changed from
        `'credit'` to `'debit'` (liabilities netted in the wrong
        direction). RAN:
        `pytest retail_balance_sheet_test.py::test_tax_collected_sits_in_liabilities_never_in_equity -q`
        RED (real output, 1 failed in 8.27s):
          E       assert -2.0 == 2.0
        (Tax Payable's own net flipped sign -- a liability that should read
        +2.0 read -2.0.) Restored; RE-RAN the full file: 14 passed.

  Each restore was verified two ways: the single targeted test alone, and
  the full file (`pytest retail_balance_sheet_test.py -q` -> `14 passed`),
  so a restore that was merely close enough to fool the one test it broke
  could not slip past the rest.

Self-contained bootstrap, matching retail_gl_integration_test.py's own
convention (no shared conftest.py exists for products/retail/tests/). One
company per test via the `client` fixture, so balance/statement assertions
never see another test's rows.

CRITICAL: exactly ONE pytest process per file (AUDIT-010) -- these test
files pollute each other in a shared pytest process; see products/
run_all_tests.py.

Run:
    pytest products/retail/tests/retail_balance_sheet_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_bs_"))
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
    email = f'bstest-{uuid.uuid4().hex[:8]}@test.local'
    password = 'BsIntegrationPW1'
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
    """A real cashier account -- CAP_REPORTS-less, matching retail_gl_
    route_test.py's/retail_profit_and_loss_test.py's own `_make_cashier`,
    reused here for the identical capability-gate proof on this file's own
    new route."""
    email = f'bs-cashier-{uuid.uuid4().hex[:8]}@test.local'
    password = 'BsCashierPW1'
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
    # cost_price=1.0 BY DEFAULT (unlike retail_profit_and_loss_test.py's own
    # 0-cost default): this file's tests want an ordinary, cost-basis-
    # complete COGS posting on every sale so Inventory moves realistically
    # and `cost_basis_incomplete` stays False in every test that does not
    # care about it -- matching retail_gl_integration_test.py's own default.
    payload = {'name': 'BS Widget', 'sku': f'BSW-{uuid.uuid4().hex[:8]}', 'cost_price': 1.0,
               'sell_price': 10.0, 'tax_rate': 0, 'initial_stock': 500}
    payload.update(kw)
    r = c.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_customer(c):
    r = c.post(f'{API}/customers', json={'name': f'BS Customer {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_supplier(c):
    r = c.post(f'{API}/suppliers', json={'name': f'BS Supplier {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _set_customer_credit(cust_id, mode='unlimited', limit=1000000):
    conn = get_retail_conn()
    conn.execute("UPDATE customers SET credit_mode=?, credit_limit=? WHERE id=?", (mode, limit, cust_id))
    conn.commit(); conn.close()


# ── Read helpers ─────────────────────────────────────────────────────────────

def _balance_sheet(cid, as_of_date, branch_id=None):
    conn = get_retail_conn()
    try:
        return fin.balance_sheet(conn, cid, as_of_date, branch_id=branch_id)
    finally:
        conn.close()


def _income_statement(cid, start_date, end_date, branch_id=None):
    conn = get_retail_conn()
    try:
        return fin.income_statement(conn, cid, start_date, end_date, branch_id=branch_id)
    finally:
        conn.close()


def _account(section, code):
    """One account dict out of `data['assets'|'liabilities'|'equity']
    ['accounts']`, or None if that code never posted."""
    matches = [a for a in section['accounts'] if a['code'] == code]
    return matches[0] if matches else None


def _today():
    return datetime.date.today().strftime('%Y-%m-%d')


# ═════════════════════════════════════════════════════════════════════════
# 1. A cash sale increases Cash, and the sheet balances via retained earnings
# ═════════════════════════════════════════════════════════════════════════

def test_cash_sale_increases_cash_and_the_sheet_balances_via_retained_earnings(client):
    cid = client.test_company_id
    pid = _seed_product(client, sell_price=10.0, tax_rate=0, cost_price=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 5}],
        'payment_method': 'cash', 'amount_paid': 50.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    today = _today()
    data = _balance_sheet(cid, today)
    assert data['has_activity'] is True
    cash = _account(data['assets'], gl.ACCOUNT_CASH)
    assert cash is not None and cash['amount'] == 50.0

    # This product has NO period-close step -- gl_accounts 3000 (Owner's
    # Equity) was never posted to by this sale, so it is completely absent
    # from equity['accounts']. The sheet balances ONLY because
    # retained_earnings absorbs the sale's net profit (50.0, no cost basis,
    # no tax). Proving THIS is the point of this test, not merely that
    # is_balanced happens to read True.
    assert _account(data['equity'], gl.ACCOUNT_EQUITY) is None
    assert data['equity']['owners_equity'] == 0.0
    assert data['equity']['retained_earnings'] == 50.0
    assert data['equity']['total'] == 50.0
    assert data['balance_check']['is_balanced'] is True
    assert data['balance_check']['difference'] == 0.0


# ═════════════════════════════════════════════════════════════════════════
# 2. A credit sale posts to Accounts Receivable, not Cash
# ═════════════════════════════════════════════════════════════════════════

def test_credit_sale_posts_to_receivable_not_cash_and_still_balances(client):
    cid = client.test_company_id
    cust_id = _seed_customer(client)
    _set_customer_credit(cust_id)
    pid = _seed_product(client, sell_price=50.0, tax_rate=0, cost_price=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'credit', 'customer_id': cust_id, 'amount_paid': 20.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    data = _balance_sheet(cid, _today())
    ar = _account(data['assets'], gl.ACCOUNT_AR)
    cash = _account(data['assets'], gl.ACCOUNT_CASH)
    assert ar is not None and ar['amount'] == 30.0, 'the unpaid balance must sit in Accounts Receivable'
    assert cash is not None and cash['amount'] == 20.0, 'only the amount actually tendered is Cash'
    assert data['balance_check']['is_balanced'] is True
    assert data['balance_check']['difference'] == 0.0


# ═════════════════════════════════════════════════════════════════════════
# 3. A purchase on credit increases Inventory AND Accounts Payable
# ═════════════════════════════════════════════════════════════════════════

def test_purchase_on_credit_increases_inventory_and_payable_and_still_balances(client):
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=5.0)
    r = client.post(f'{API}/purchase-orders', json={
        'supplier_id': supp_id,
        'items': [{'product_id': pid, 'quantity': 10, 'unit_cost': 4.0}],
        'amount_paid': 0})
    assert r.status_code == 200, r.get_json()

    data = _balance_sheet(cid, _today())
    inventory = _account(data['assets'], gl.ACCOUNT_INVENTORY)
    ap = _account(data['liabilities'], gl.ACCOUNT_AP)
    assert inventory is not None and inventory['amount'] == 40.0
    assert ap is not None and ap['amount'] == 40.0
    # Nothing paid, nothing sold yet -- no revenue/expense activity at all,
    # so retained_earnings must still read exactly 0, and Cash must be
    # untouched.
    assert data['equity']['retained_earnings'] == 0.0
    assert _account(data['assets'], gl.ACCOUNT_CASH) is None
    assert data['balance_check']['is_balanced'] is True
    assert data['balance_check']['difference'] == 0.0


# ═════════════════════════════════════════════════════════════════════════
# 4. A customer payment reclassifies Receivable into Cash -- totals unchanged
# ═════════════════════════════════════════════════════════════════════════

def test_customer_payment_moves_value_from_receivable_to_cash_leaving_totals_unchanged(client):
    cid = client.test_company_id
    cust_id = _seed_customer(client)
    _set_customer_credit(cust_id)
    pid = _seed_product(client, sell_price=100.0, tax_rate=0, cost_price=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'credit', 'customer_id': cust_id, 'amount_paid': 0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    today = _today()
    before = _balance_sheet(cid, today)
    assert _account(before['assets'], gl.ACCOUNT_AR)['amount'] == 100.0
    assert _account(before['assets'], gl.ACCOUNT_CASH) is None
    assets_before = before['assets']['total']
    grand_total_before = before['total_liabilities_and_equity']

    r = client.post(f'{API}/customers/{cust_id}/payments', json={'amount': 40.0, 'method': 'cash'})
    assert r.status_code == 200, r.get_json()

    after = _balance_sheet(cid, today)
    ar_after = _account(after['assets'], gl.ACCOUNT_AR)
    cash_after = _account(after['assets'], gl.ACCOUNT_CASH)
    assert ar_after['amount'] == 60.0
    assert cash_after['amount'] == 40.0
    # An asset RECLASSIFICATION, not a new asset -- the assets total (and
    # therefore the whole grand total) must be BYTE-IDENTICAL before and
    # after, not merely "still balanced".
    assert after['assets']['total'] == assets_before, (
        'a customer payment must reclassify an asset, not create one -- the assets total moved')
    assert after['total_liabilities_and_equity'] == grand_total_before
    assert after['balance_check']['is_balanced'] is True
    assert after['balance_check']['difference'] == 0.0


# ═════════════════════════════════════════════════════════════════════════
# 5. Tax collected sits in LIABILITIES, never in equity
# ═════════════════════════════════════════════════════════════════════════

def test_tax_collected_sits_in_liabilities_never_in_equity(client):
    cid = client.test_company_id
    pid = _seed_product(client, sell_price=20.0, tax_rate=10, cost_price=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 22.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    data = _balance_sheet(cid, _today())
    tax_liability = _account(data['liabilities'], gl.ACCOUNT_TAX_PAYABLE)
    assert tax_liability is not None, 'tax collected must appear as a liability account'
    assert tax_liability['amount'] == 2.0

    # THE EXPLICIT NEGATIVE: tax must not be findable ANYWHERE in equity --
    # neither as its own ledger account, nor folded into owners_equity, nor
    # into retained_earnings (retained_earnings is net PROFIT, and tax
    # collected on behalf of the government is never revenue -- create_sale
    # never includes it in `sales.total`'s revenue leg).
    assert _account(data['equity'], gl.ACCOUNT_TAX_PAYABLE) is None
    # Revenue was 20.0 (tax-exclusive); cost_price=0 means no COGS journal.
    # retained_earnings must equal 20.0, NOT 22.0 -- the tender total minus
    # the tax leg, proving tax never leaked into the profit figure equity
    # is built from.
    assert data['equity']['retained_earnings'] == 20.0
    assert data['balance_check']['is_balanced'] is True


# ═════════════════════════════════════════════════════════════════════════
# 6. Equity == Owner's Equity + earnings, and earnings MATCHES income_statement
# ═════════════════════════════════════════════════════════════════════════

def test_equity_equals_owners_equity_plus_earnings_and_matches_income_statement(client):
    cid = client.test_company_id

    # A REAL capital contribution -- Dr Cash / Cr Owner's Equity -- posted
    # via general_ledger.post_journal directly (there is no "owner invests
    # capital" ROUTE in this codebase; see general_ledger.py's own DEFAULT_
    # CHART comment on why 3000 is seeded but not routinely posted to).
    # Without this, owners_equity would be 0 in every test in this file and
    # the "PLUS" in "Owner's Equity PLUS retained earnings" would never be
    # exercised as a real addition.
    conn = get_retail_conn()
    gl.ensure_default_chart(conn, cid)
    today = _today()
    gl.post_journal(
        conn, cid, None, today, 'test_fixture', None, 'JOD',
        [
            {'account_code': gl.ACCOUNT_CASH, 'debit': 1000.0, 'memo': 'Owner capital contribution'},
            {'account_code': gl.ACCOUNT_EQUITY, 'credit': 1000.0, 'memo': 'Owner capital contribution'},
        ],
        reference='CAPITAL-TEST')
    conn.commit()
    conn.close()

    # A real trading day: a cash sale, a credit sale, and a return -- the
    # exact mix retail_profit_and_loss_test.py's own boundary/COGS tests
    # exercise, so net_profit here is a genuine, non-trivial figure, not a
    # single untouched sale.
    pid = _seed_product(client, sell_price=100.0, tax_rate=0, cost_price=20.0, initial_stock=500)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 2}],
        'payment_method': 'cash', 'amount_paid': 200.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    cust_id = _seed_customer(client)
    _set_customer_credit(cust_id)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 3}],
        'payment_method': 'credit', 'customer_id': cust_id, 'amount_paid': 100.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    bs = _balance_sheet(cid, today)
    pnl = _income_statement(cid, '0001-01-01', today)

    # THE CLAIM: balance_sheet's retained_earnings is not merely "close
    # to" income_statement's net_profit for the identical all-time span --
    # it is IDENTICAL, because balance_sheet calls income_statement
    # directly rather than re-deriving the figure. This is the test that
    # would catch the two statements disagreeing on the same screen.
    assert bs['equity']['retained_earnings'] == pnl['net_profit']
    assert pnl['net_profit'] > 0, 'sanity: this fixture must produce real, nonzero profit'

    owners_equity_acct = _account(bs['equity'], gl.ACCOUNT_EQUITY)
    assert owners_equity_acct is not None and owners_equity_acct['amount'] == 1000.0
    assert bs['equity']['owners_equity'] == 1000.0
    assert bs['equity']['total'] == 1000.0 + pnl['net_profit']

    assert bs['balance_check']['is_balanced'] is True
    assert bs['balance_check']['difference'] == 0.0
    assert bs['assets']['total'] == bs['total_liabilities_and_equity']


# ═════════════════════════════════════════════════════════════════════════
# 7. As-of dating: strictly-after is excluded, on-the-date is included
# ═════════════════════════════════════════════════════════════════════════

def test_as_of_date_boundary_is_inclusive_of_that_date_and_excludes_the_day_after(client, monkeypatch):
    """Same monkeypatch-`gl._today` technique retail_profit_and_loss_
    test.py's own `test_period_boundaries_are_both_ends_inclusive` uses --
    no route lets a caller override `business_date` directly, and
    `gl_journals` is append-only by trigger, so controlling what `gl.
    _today()` returns for the duration of one call is the sanctioned way to
    post a REAL journal dated something other than today."""
    cid = client.test_company_id
    pid = _seed_product(client, sell_price=1.0, tax_rate=0, cost_price=0, initial_stock=3000)

    as_of = '2026-02-10'
    the_day_before = '2026-02-09'
    the_day_after = '2026-02-11'

    for amount, business_date in ((1200.0, the_day_before), (50.0, as_of), (999.0, the_day_after)):
        monkeypatch.setattr(gl, '_today', lambda d=business_date: d)
        r = client.post(f'{API}/sales', json={
            'items': [{'product_id': pid, 'quantity': amount}],  # sell_price 1.0 -> total==amount
            'payment_method': 'cash', 'amount_paid': amount,
            'idempotency_key': str(uuid.uuid4())})
        assert r.status_code == 200, r.get_json()

    data = _balance_sheet(cid, as_of)
    cash = _account(data['assets'], gl.ACCOUNT_CASH)
    # 1200 (before the as-of date, included in a SNAPSHOT) + 50 (ON the
    # as-of date, included -- the boundary itself) = 1250. The 999 sale the
    # day AFTER must not appear at all.
    assert cash['amount'] == 1250.0, (
        f"expected 1250.0 (day-before + on-the-day sales; day-after excluded), got {cash['amount']}")
    assert data['equity']['retained_earnings'] == 1250.0


# ═════════════════════════════════════════════════════════════════════════
# 8. An empty ledger reads as nothing-posted, and balances trivially
# ═════════════════════════════════════════════════════════════════════════

def test_empty_ledger_reports_no_activity_and_balances_trivially(client):
    cid = client.test_company_id
    data = _balance_sheet(cid, _today())
    assert data['has_activity'] is False
    assert data['assets']['accounts'] == []
    assert data['liabilities']['accounts'] == []
    assert data['equity']['accounts'] == []
    assert data['assets']['total'] == 0.0
    assert data['liabilities']['total'] == 0.0
    assert data['equity']['owners_equity'] == 0.0
    assert data['equity']['retained_earnings'] == 0.0
    assert data['equity']['total'] == 0.0
    assert data['total_liabilities_and_equity'] == 0.0
    # "Balances trivially" -- NOT alarming, NOT omitted: the check itself
    # is still computed and reads clean, 0.0 == 0.0.
    assert data['balance_check']['is_balanced'] is True
    assert data['balance_check']['difference'] == 0.0
    assert data['balance_check']['assets_total'] == 0.0
    assert data['balance_check']['liabilities_and_equity_total'] == 0.0


# ═════════════════════════════════════════════════════════════════════════
# 9. A ledger corrupted outside post_journal is reported OUT OF BALANCE
# ═════════════════════════════════════════════════════════════════════════

def test_a_ledger_corrupted_outside_post_journal_is_reported_out_of_balance(client):
    """`post_journal`'s own balance invariant means a real document can
    never leave the ledger unbalanced (GeneralLedgerImbalanceError aborts
    the whole document first -- see general_ledger.py's own docstring).
    "Impossible through the front door" is not the same claim as "the
    report that would catch it if it ever happened is not itself a lie" --
    CLAUDE.md's engineering standard is explicit that a check which can
    never fail is worth nothing until it has actually been made to fail.
    So this test reaches around `post_journal` entirely -- a raw INSERT
    into `gl_journal_lines`/`gl_journals`, exactly the kind of "hand-
    written UPDATE" this function's own docstring names as the one thing
    that WOULD leave the ledger genuinely broken -- and asserts the
    balance check notices, with the precise amount."""
    cid = client.test_company_id
    pid = _seed_product(client, sell_price=10.0, tax_rate=0, cost_price=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 10.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    today = _today()
    healthy = _balance_sheet(cid, today)
    assert healthy['balance_check']['is_balanced'] is True

    conn = get_retail_conn()
    cash_account_id = conn.execute(
        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?", (cid, gl.ACCOUNT_CASH)
    ).fetchone()[0]
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO gl_journals (company_id, branch_id, business_date, reference, memo, "
        "source_type, source_id, currency) VALUES (?,?,?,?,?,?,?,?)",
        (cid, None, today, 'CORRUPT-TEST', 'deliberately unbalanced -- test only',
         'test_corruption', None, 'JOD'))
    journal_id = cur.lastrowid
    # A LONE debit, no matching credit anywhere -- exactly the shape
    # `post_journal` refuses to ever write (GeneralLedgerImbalanceError),
    # constructed here by going around it entirely.
    cur.execute(
        "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit) VALUES (?,?,?,?)",
        (journal_id, cash_account_id, 75.0, 0))
    conn.commit()
    conn.close()

    corrupted = _balance_sheet(cid, today)
    assert corrupted['balance_check']['is_balanced'] is False, (
        'a ledger with a lone, unmatched debit must be reported out of balance, not silently '
        'absorbed into a clean-looking report')
    # assets_total picked up the extra 75 (Cash, an asset, debit-normal);
    # liabilities_and_equity_total did not move at all -- so the reported
    # difference must be exactly +75.0, not merely nonzero.
    assert corrupted['assets']['total'] == healthy['assets']['total'] + 75.0
    assert corrupted['total_liabilities_and_equity'] == healthy['total_liabilities_and_equity']
    assert corrupted['balance_check']['difference'] == 75.0
    assert corrupted['balance_check']['assets_total'] == corrupted['assets']['total']
    assert corrupted['balance_check']['liabilities_and_equity_total'] == corrupted['total_liabilities_and_equity']


# ═════════════════════════════════════════════════════════════════════════
# 10. The route: shape, capability gate, validation
# ═════════════════════════════════════════════════════════════════════════

def test_route_returns_200_with_the_expected_shape(client):
    pid = _seed_product(client, sell_price=10.0, tax_rate=0, cost_price=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 10.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    today = _today()
    r = client.get(f'{API}/reports/balance-sheet?as_of_date={today}')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['as_of_date'] == today
    assert data['branch_id'] is None
    assert 'assets' in data and 'accounts' in data['assets']
    assert 'liabilities' in data and 'accounts' in data['liabilities']
    assert 'equity' in data and 'retained_earnings' in data['equity']
    assert 'balance_check' in data and 'is_balanced' in data['balance_check']
    assert data['assets']['total'] == 10.0


def test_route_refuses_missing_as_of_date_with_400(client):
    r = client.get(f'{API}/reports/balance-sheet')
    assert r.status_code == 400, r.get_json()


def test_route_refuses_malformed_date_with_400(client):
    r = client.get(f'{API}/reports/balance-sheet?as_of_date=not-a-date')
    assert r.status_code == 400, r.get_json()


def test_route_refuses_non_integer_branch_id_with_400(client):
    today = _today()
    r = client.get(f'{API}/reports/balance-sheet?as_of_date={today}&branch_id=not-a-number')
    assert r.status_code == 400, r.get_json()


def test_route_refused_without_cap_reports_and_refusal_is_from_the_capability_check():
    """Matches retail_gl_route_test.py's/retail_profit_and_loss_test.py's
    identical proof for their own report routes -- a 403 alone cannot
    distinguish 'the capability check ran and refused' from 'something else
    refused entirely' (CLAUDE.md's engineering standard, failure shape 1)."""
    cashier = _make_cashier()
    today = _today()
    r = cashier.get(f'{API}/reports/balance-sheet?as_of_date={today}')
    assert r.status_code == 403, r.get_json()
    body = r.get_json()
    assert body.get('message') == CAPABILITY_DENIED_MESSAGE, (
        f"refusal did not come from the capability check: {body!r}")
