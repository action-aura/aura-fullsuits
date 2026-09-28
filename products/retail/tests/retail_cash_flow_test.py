"""Aura Retail -- the cash flow statement (core/retail/financial_statements.py
::cash_flow, retail-hardware-viewports), driven through REAL documents and
REAL routes exactly like retail_gl_integration_test.py drives the trial
balance -- the "run the real artefact" level CLAUDE.md's engineering
standard demands, one level above calling `cash_flow` against hand-built
ledger rows.

THE CENTRAL CLAIM THIS FILE PROVES, in order:

  1. A cash sale increases cash in. A CREDIT sale does not -- no cash moved,
     and that is the whole point of a direct-method cash flow statement: it
     must be able to tell a shopkeeper "you sold something, but you have not
     been paid for it yet", which an accrual figure (revenue) cannot.
  2. A later customer payment against that same debt DOES move cash, and a
     supplier payment moves cash the other way.
  3. `opening_cash + net_change_in_cash == closing_cash`, and `closing_cash`
     agrees with `balance_sheet()`'s own Cash and Bank figure for the same
     date -- the reconciliation `cash_flow` itself reports, verified here
     independently rather than trusted.
  4. Period boundaries are respected (both ends inclusive, nothing outside
     the window counted), and an empty period reads as "nothing moved", not
     a tidy set of zeros.

Self-contained bootstrap, matching retail_gl_integration_test.py's own
convention (no shared conftest.py exists for products/retail/tests/). One
company per test via the `client` fixture, so statement assertions never see
another test's rows.

MUTATION PROOFS -- performed BY HAND against financial_statements.py during
development, matching retail_balance_sheet_test.py's own established
convention for this backend (a textual, single-anchor edit via this
project's Edit tool, never a whole-file rewrite -- surgical, so a
concurrently-live agent editing any OTHER file in this repo is unaffected),
each restored and reconfirmed green immediately after capturing the RED
output quoted below. Every quoted line is real pytest output from this exact
file, not a prediction.

  M1 -- forced `period_params = [cid, gl.ACCOUNT_CASH, cid, start_date,
        end_date]` to `period_params = [cid, gl.ACCOUNT_AR, cid, start_date,
        end_date]` (the direct-method query reads Accounts Receivable
        instead of Cash -- the shape a "credit sale counts as cash in" bug
        would actually take: the wrong account joined into the window
        query). RAN:
        `pytest retail_cash_flow_test.py::test_credit_sale_does_not_count_as_cash_in -q`
        RED (real output, 1 failed in 8.86s):
          E       AssertionError: a purely credit sale must not register as cash activity --
                  it posted no leg on the cash account at all
          E       assert True is False
        Restored the exact original line; RE-RAN the full file: 18 passed.

  M2 -- forced `day_before_start = date.fromordinal(start.toordinal() -
        1).isoformat()` to `... - 2).isoformat()` (an off-by-one in the ONE
        piece of nontrivial arithmetic the reconciliation actually depends
        on -- see test_opening_cash_boundary_excludes_start_date_includes_
        the_day_before's own docstring for why `opening_cash + net_change_
        in_cash == closing_cash` is otherwise true BY CONSTRUCTION for any
        ledger state, balanced or corrupted, and cannot be made to
        genuinely disagree any other way: both `opening_cash`/`closing_
        cash` are read through the SAME `balance_sheet()` call, so a lone
        unmatched debit inserted directly into `gl_journal_lines` --
        retail_balance_sheet_test.py's own M1 technique for ITS balance_
        check -- moves opening and closing by the identical amount and
        proves nothing here). RAN:
        `pytest retail_cash_flow_test.py::test_opening_cash_boundary_excludes_start_date_includes_the_day_before -q`
        RED (real output, 1 failed in 7.66s):
          E       assert 0.0 == 40.0
        (a payment dated the day before the window vanished from opening_
        cash -- exactly what an off-by-one boundary bug does.)
        Restored the exact original line; RE-RAN the full file: 18 passed.

  A THIRD AND FOURTH PROOF, at the UI layer where they actually belong (this
  screen only renders whatever the server already computed, so "the
  reconciliation badge always shows green" and "the empty-period state is
  skipped" are frontend concerns, not backend ones) -- see
  retail_cash_flow_ui_test.js's own testEveryGuardIsProvenByBreakingIt,
  mutations #3 and #4, both proved with real console output on every run of
  that file.

Run:
    pytest products/retail/tests/retail_cash_flow_test.py -q
"""
import os
import shutil
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

DATA = Path(tempfile.mkdtemp(prefix="aura_cash_flow_test_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from core.retail import general_ledger as gl  # noqa: E402
from core.retail import financial_statements as fin_statements  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    email = f'cftest-{uuid.uuid4().hex[:8]}@test.local'
    password = 'CashFlowTestPW1'
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


def _seed_product(c, **kw):
    payload = {'name': 'CF Widget', 'sku': f'CFW-{uuid.uuid4().hex[:8]}', 'cost_price': 1.0,
               'sell_price': 10.0, 'tax_rate': 0, 'initial_stock': 100}
    payload.update(kw)
    r = c.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_customer(c):
    r = c.post(f'{API}/customers', json={'name': f'CF Customer {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_supplier(c):
    r = c.post(f'{API}/suppliers', json={'name': f'CF Supplier {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _set_customer_credit(cust_id, mode='unlimited', limit=1000000):
    conn = get_retail_conn()
    conn.execute("UPDATE customers SET credit_mode=?, credit_limit=? WHERE id=?", (mode, limit, cust_id))
    conn.commit(); conn.close()


def _today_str():
    return datetime.now().strftime('%Y-%m-%d')


def _offset_str(days):
    return (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')


def _cash_flow(cid, start_date, end_date, branch_id=None):
    conn = get_retail_conn()
    try:
        return fin_statements.cash_flow(conn, cid, start_date, end_date, branch_id=branch_id)
    finally:
        conn.close()


def _balance_sheet(cid, as_of_date, branch_id=None):
    conn = get_retail_conn()
    try:
        return fin_statements.balance_sheet(conn, cid, as_of_date, branch_id=branch_id)
    finally:
        conn.close()


def _cash_in_labels(cf):
    return {l['label']: l['amount'] for l in cf['operating_activities']['cash_in']['lines']}


def _cash_out_labels(cf):
    return {l['label']: l['amount'] for l in cf['operating_activities']['cash_out']['lines']}


# ── 1. A cash sale increases cash in ─────────────────────────────────────

def test_cash_sale_increases_cash_in(client):
    cid = client.test_company_id
    pid = _seed_product(client, sell_price=10.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 2}],
        'payment_method': 'cash', 'amount_paid': 20.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    today = _today_str()
    cf = _cash_flow(cid, today, today)
    assert cf['has_activity'] is True
    assert cf['total_cash_in'] == 20.0
    assert cf['total_cash_out'] == 0.0
    assert cf['net_change_in_cash'] == 20.0
    cash_in = _cash_in_labels(cf)
    assert cash_in.get('Cash Sales Receipts') == 20.0


# ── 2. A CREDIT sale does NOT increase cash in -- the whole point ───────

def test_credit_sale_does_not_count_as_cash_in(client):
    """A pure credit sale (nothing tendered) posts a real, balanced GL
    journal (Dr Accounts Receivable / Cr Sales Revenue) -- but that journal
    has no leg on account 1000 (Cash) at all, so this period's cash flow
    statement must show NOTHING moved. This is the single most important
    behaviour this statement exists to get right: revenue and cash are
    different facts, and a credit sale is the case where they diverge."""
    cid = client.test_company_id
    cust_id = _seed_customer(client)
    _set_customer_credit(cust_id)
    pid = _seed_product(client, sell_price=50.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'credit', 'customer_id': cust_id, 'amount_paid': 0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']
    assert body['balance_due'] == 50.0

    today = _today_str()
    cf = _cash_flow(cid, today, today)
    assert cf['has_activity'] is False, (
        "a purely credit sale must not register as cash activity -- it posted "
        "no leg on the cash account at all")
    assert cf['total_cash_in'] == 0.0
    assert cf['total_cash_out'] == 0.0
    assert cf['net_change_in_cash'] == 0.0
    assert _cash_in_labels(cf) == {}


def test_partially_paid_credit_sale_counts_only_the_tendered_portion(client):
    """A sale that is PART cash, part credit must book exactly the tendered
    amount as cash in -- not the sale total, not the balance due."""
    cid = client.test_company_id
    cust_id = _seed_customer(client)
    _set_customer_credit(cust_id)
    pid = _seed_product(client, sell_price=100.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'credit', 'customer_id': cust_id, 'amount_paid': 35.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']
    assert body['balance_due'] == 65.0

    today = _today_str()
    cf = _cash_flow(cid, today, today)
    assert cf['has_activity'] is True
    assert cf['total_cash_in'] == 35.0
    assert _cash_in_labels(cf).get('Cash Sales Receipts') == 35.0


# ── 3. A later customer payment DOES move cash ───────────────────────────

def test_customer_payment_counts_as_cash_in(client):
    cid = client.test_company_id
    cust_id = _seed_customer(client)
    _set_customer_credit(cust_id)
    pid = _seed_product(client, sell_price=100.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'credit', 'customer_id': cust_id, 'amount_paid': 0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    r = client.post(f'{API}/customers/{cust_id}/payments', json={'amount': 40.0, 'method': 'cash'})
    assert r.status_code == 200, r.get_json()

    today = _today_str()
    cf = _cash_flow(cid, today, today)
    assert cf['has_activity'] is True
    # The credit sale itself contributed nothing (see test 2 above); the
    # payment is the ONLY cash movement this period.
    assert cf['total_cash_in'] == 40.0
    assert _cash_in_labels(cf).get('Customer Account Payments Received') == 40.0


# ── 4. A supplier payment is cash out ────────────────────────────────────

def test_supplier_payment_is_cash_out(client):
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=5.0)
    r = client.post(f'{API}/purchase-orders', json={
        'supplier_id': supp_id,
        'items': [{'product_id': pid, 'quantity': 10, 'unit_cost': 4.0}],
        'amount_paid': 0})
    assert r.status_code == 200, r.get_json()

    r = client.post(f'{API}/suppliers/{supp_id}/payments', json={'amount': 15.0, 'method': 'cash'})
    assert r.status_code == 200, r.get_json()

    today = _today_str()
    cf = _cash_flow(cid, today, today)
    assert cf['has_activity'] is True
    assert cf['total_cash_out'] == 15.0
    assert cf['total_cash_in'] == 0.0
    assert _cash_out_labels(cf).get('Payments to Suppliers') == 15.0


def test_purchase_order_down_payment_and_later_supplier_payment_merge_one_label(client):
    """A PO down payment (post_purchase_order: Dr Inventory / Cr Cash + Cr
    AP) and a later account settlement (post_supplier_payment: Dr AP / Cr
    Cash) are DIFFERENT source_types with DIFFERENT counter-accounts (
    Inventory vs. Accounts Payable) -- so they must NOT be merged with each
    other. But a direct `supplier_payment` and a PO instalment
    (`pay_purchase_order`, source_type 'po_payment') post the IDENTICAL
    entry shape (general_ledger.post_supplier_payment's own docstring: "ONE
    helper for BOTH... the accounting entry is identical in both cases"), so
    those two DO merge onto one 'Payments to Suppliers' line."""
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=5.0)
    r = client.post(f'{API}/purchase-orders', json={
        'supplier_id': supp_id,
        'items': [{'product_id': pid, 'quantity': 10, 'unit_cost': 4.0}],
        'amount_paid': 15.0})
    assert r.status_code == 200, r.get_json()
    po = r.get_json()['data']

    # A second, unrelated supplier gets a direct account payment (source_type
    # 'supplier_payment') in the SAME period.
    supp2_id = _seed_supplier(client)
    r = client.post(f'{API}/purchase-orders', json={
        'supplier_id': supp2_id,
        'items': [{'product_id': pid, 'quantity': 5, 'unit_cost': 4.0}],
        'amount_paid': 0})
    assert r.status_code == 200, r.get_json()
    r = client.post(f'{API}/suppliers/{supp2_id}/payments', json={'amount': 9.0, 'method': 'cash'})
    assert r.status_code == 200, r.get_json()

    # And the first PO's remaining balance is paid off via 'po_payment'.
    r = client.post(f'{API}/purchase-orders/{po["id"]}/pay', json={'amount': 25.0, 'method': 'cash'})
    assert r.status_code == 200, r.get_json()

    today = _today_str()
    cf = _cash_flow(cid, today, today)
    cash_out = _cash_out_labels(cf)
    # 15 (PO1 down payment) stays on its OWN line -- Inventory is the counter
    # account, not Accounts Payable.
    assert cash_out.get('Cash Paid for Inventory Purchases') == 15.0
    # 9 (direct supplier_payment) + 25 (po_payment settling PO1's balance) ==
    # 34, on the SAME 'Payments to Suppliers' line.
    assert cash_out.get('Payments to Suppliers') == 34.0
    assert cf['total_cash_out'] == 49.0

    # Traceability: both raw source_types are listed against the merged line.
    merged = next(l for l in cf['operating_activities']['cash_out']['lines']
                  if l['label'] == 'Payments to Suppliers')
    assert set(merged['source_types']) == {'po_payment', 'supplier_payment'}


# ── 5. A cash return is a cash OUTFLOW ───────────────────────────────────

def test_cash_return_is_cash_out_and_gross_not_netted(client):
    """A full cash sale followed by a full cash return: the DIRECT method
    reports both the gross inflow (the sale) and the gross outflow (the
    refund) as separate lines, never netted into a single, silent zero --
    that is precisely what an indirect/accrual figure would hide."""
    cid = client.test_company_id
    pid = _seed_product(client, sell_price=20.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 20.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']

    r = client.get(f'{API}/sales/{sale["id"]}')
    line = r.get_json()['data']['items'][0]
    r = client.post(f'{API}/returns', json={
        'sale_id': sale['id'],
        'items': [{'product_id': pid, 'quantity': 1, 'sale_item_id': line.get('id')}],
        'reason': 'cash flow test', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    today = _today_str()
    cf = _cash_flow(cid, today, today)
    assert cf['total_cash_in'] == 20.0
    assert cf['total_cash_out'] == 20.0
    assert cf['net_change_in_cash'] == 0.0
    assert _cash_in_labels(cf).get('Cash Sales Receipts') == 20.0
    assert _cash_out_labels(cf).get('Refunds Paid to Customers') == 20.0


def test_opening_cash_boundary_excludes_start_date_includes_the_day_before(client):
    """Pins the ONE piece of nontrivial arithmetic the reconciliation
    actually depends on: `opening_cash` is read `date.fromordinal(start.
    toordinal() - 1)` -- the calendar day immediately BEFORE `start_date`,
    never `start_date` itself.

    WHY THIS TEST, SEPARATELY FROM THE RECONCILIATION TESTS ABOVE: `opening_
    cash + net_change_in_cash == closing_cash` is TRUE BY CONSTRUCTION for
    any correct pair of dates, because both `opening_cash`/`closing_cash`
    are read via the SAME `balance_sheet()` call and `net_change_in_cash`
    is this function's own direct-method delta over the identical window --
    there is no ledger state (balanced or corrupted) that can make the three
    disagree, because the reconciliation does not depend on double-entry
    balance holding, only on the TWO calendar boundaries (`day_before_
    start`/`end_date`) being complementary. The one way to make it
    genuinely disagree is an off-by-one in computing `day_before_start`
    itself -- and this test is built to catch exactly that: a payment dated
    the day BEFORE the window and one dated ON the window's first day, so an
    off-by-one boundary bug either double-counts or drops the earlier one.
    """
    cid = client.test_company_id
    conn = get_retail_conn()
    try:
        # Dated the day BEFORE the window -- must land in opening_cash, and
        # must NOT be counted in this period's own total_cash_in.
        gl.post_customer_payment(conn, cid, None, 'CF-BOUNDARY-BEFORE', 'JOD', 40.0,
                                  business_date='2026-07-09')
        # Dated ON the window's first day -- must be EXCLUDED from opening_
        # cash and MUST be counted in total_cash_in.
        gl.post_customer_payment(conn, cid, None, 'CF-BOUNDARY-ON', 'JOD', 17.0,
                                  business_date='2026-07-10')
        conn.commit()
    finally:
        conn.close()

    cf = _cash_flow(cid, '2026-07-10', '2026-07-20')
    # opening_cash: the 40 posted the day before must be there; closing_cash
    # (all-time as of 2026-07-20) carries both 40 + 17 = 57.
    assert cf['opening_cash'] == 40.0
    assert cf['closing_cash'] == 57.0
    # total_cash_in: ONLY the 17 dated on-or-after the window start.
    assert cf['total_cash_in'] == 17.0
    assert cf['net_change_in_cash'] == 17.0
    assert cf['reconciliation']['is_balanced'] is True
    assert cf['reconciliation']['difference'] == 0.0


# ── 6. opening + net == closing, and closing agrees with balance_sheet ──

def test_reconciliation_holds_and_closing_agrees_with_balance_sheet(client):
    cid = client.test_company_id
    pid = _seed_product(client, sell_price=30.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 30.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    today = _today_str()
    cf = _cash_flow(cid, today, today)
    assert cf['reconciliation']['is_balanced'] is True
    assert cf['reconciliation']['difference'] == 0.0
    assert round(cf['opening_cash'] + cf['net_change_in_cash'], 3) == round(cf['closing_cash'], 3)
    assert cf['reconciliation']['computed_closing_cash'] == cf['closing_cash']

    # Independently-derived, never trusted: read balance_sheet()'s own Cash
    # and Bank figure directly and compare it to cash_flow's closing_cash --
    # this is the check the task itself calls "the one that matters", and it
    # is asserted here against a SEPARATE call, not against cash_flow's own
    # reconciliation block (which could theoretically lie about itself).
    bs = _balance_sheet(cid, today)
    bs_cash = next(a['amount'] for a in bs['assets']['accounts'] if a['code'] == gl.ACCOUNT_CASH)
    assert bs_cash == cf['closing_cash']


def test_reconciliation_holds_across_a_multi_document_session(client):
    """Mirrors retail_gl_integration_test.py's own end-to-end scenario:
    every document type this module reports on, in sequence, with the
    reconciliation checked after EVERY step -- not only at the end."""
    cid = client.test_company_id
    today = _today_str()

    # 1. Cash sale.
    pid1 = _seed_product(client, sell_price=10.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid1, 'quantity': 2}],
        'payment_method': 'cash', 'amount_paid': 20.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    assert _cash_flow(cid, today, today)['reconciliation']['is_balanced']

    # 2. Credit sale (no cash movement).
    cust_id = _seed_customer(client)
    _set_customer_credit(cust_id)
    pid2 = _seed_product(client, sell_price=50.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid2, 'quantity': 1}],
        'payment_method': 'credit', 'customer_id': cust_id, 'amount_paid': 0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    assert _cash_flow(cid, today, today)['reconciliation']['is_balanced']

    # 3. Customer payment.
    r = client.post(f'{API}/customers/{cust_id}/payments', json={'amount': 50.0, 'method': 'cash'})
    assert r.status_code == 200, r.get_json()
    assert _cash_flow(cid, today, today)['reconciliation']['is_balanced']

    # 4. Purchase order with a down payment.
    supp_id = _seed_supplier(client)
    pid3 = _seed_product(client, sell_price=5.0)
    r = client.post(f'{API}/purchase-orders', json={
        'supplier_id': supp_id,
        'items': [{'product_id': pid3, 'quantity': 10, 'unit_cost': 4.0}],
        'amount_paid': 15.0})
    assert r.status_code == 200, r.get_json()
    po = r.get_json()['data']
    assert _cash_flow(cid, today, today)['reconciliation']['is_balanced']

    # 5. Pay off the rest of the PO.
    r = client.post(f'{API}/purchase-orders/{po["id"]}/pay', json={'amount': 25.0, 'method': 'cash'})
    assert r.status_code == 200, r.get_json()
    final = _cash_flow(cid, today, today)
    assert final['reconciliation']['is_balanced']

    # Gross figures: 20 (sale) + 50 (payment) in; 15 + 25 (PO) out.
    assert final['total_cash_in'] == 70.0
    assert final['total_cash_out'] == 40.0
    assert final['net_change_in_cash'] == 30.0

    bs = _balance_sheet(cid, today)
    bs_cash = next(a['amount'] for a in bs['assets']['accounts'] if a['code'] == gl.ACCOUNT_CASH)
    assert bs_cash == final['closing_cash']


# ── 7. Investing/financing are honestly absent, not padded ──────────────

def test_no_investing_or_financing_accounts_reports_none_not_empty_sections(client):
    cid = client.test_company_id
    pid = _seed_product(client, sell_price=10.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 10.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    today = _today_str()
    cf = _cash_flow(cid, today, today)
    assert cf['investing_activities'] is None
    assert cf['financing_activities'] is None
    assert cf['method'] == 'direct'
    assert isinstance(cf.get('investing_and_financing_note'), str) and len(cf['investing_and_financing_note']) > 20


# ── 8. Period boundaries, both ends inclusive ────────────────────────────

def test_period_boundaries_exclude_activity_outside_the_window(client):
    cid = client.test_company_id
    pid = _seed_product(client, sell_price=10.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 10.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    today = _today_str()
    yesterday = _offset_str(-1)
    tomorrow = _offset_str(1)

    # In range: today, both ends set to today.
    cf_today = _cash_flow(cid, today, today)
    assert cf_today['has_activity'] is True
    assert cf_today['total_cash_in'] == 10.0

    # Out of range on both sides.
    assert _cash_flow(cid, yesterday, yesterday)['has_activity'] is False
    assert _cash_flow(cid, tomorrow, tomorrow)['has_activity'] is False

    # Inclusive: a range that has today as its END boundary still catches it.
    cf_ending_today = _cash_flow(cid, yesterday, today)
    assert cf_ending_today['has_activity'] is True
    assert cf_ending_today['total_cash_in'] == 10.0

    # Inclusive: a range that has today as its START boundary still catches it.
    cf_starting_today = _cash_flow(cid, today, tomorrow)
    assert cf_starting_today['has_activity'] is True
    assert cf_starting_today['total_cash_in'] == 10.0


def test_business_date_boundary_is_exact_both_ends_inclusive(client):
    """The route can only ever post 'today' (no client-settable business_date
    on any money-path route) -- so the exact inclusive-boundary arithmetic is
    proven here by calling `general_ledger.post_customer_payment` directly
    with an explicit `business_date`, the same core function
    `customer_payment` (retail_api.py) calls for every real payment. This is
    still the real posting path (post_journal's own balance invariant, the
    real gl_journals/gl_journal_lines tables) -- only the HTTP layer, which
    has no business_date knob to turn, is bypassed."""
    cid = client.test_company_id
    conn = get_retail_conn()
    try:
        gl.post_customer_payment(conn, cid, None, 'CF-BOUNDARY', 'JOD', 12.0,
                                  business_date='2026-06-15')
        conn.commit()
    finally:
        conn.close()

    # Exactly on the boundary, both ends.
    cf = _cash_flow(cid, '2026-06-15', '2026-06-15')
    assert cf['has_activity'] is True
    assert cf['total_cash_in'] == 12.0

    # One day before/after the single posted date: excluded.
    assert _cash_flow(cid, '2026-06-14', '2026-06-14')['has_activity'] is False
    assert _cash_flow(cid, '2026-06-16', '2026-06-16')['has_activity'] is False

    # A window whose END lands exactly on the posted date: included.
    assert _cash_flow(cid, '2026-06-01', '2026-06-15')['total_cash_in'] == 12.0
    # A window whose START lands exactly on the posted date: included.
    assert _cash_flow(cid, '2026-06-15', '2026-06-30')['total_cash_in'] == 12.0
    # A window that ends the day BEFORE the posted date: excluded.
    assert _cash_flow(cid, '2026-06-01', '2026-06-14')['has_activity'] is False


def test_non_padded_period_dates_still_find_activity_on_a_single_digit_day(client):
    """P0 round 2 (item B2, adversarial-verifier finding): every date the
    boundary test above passes ('2026-06-15' etc.) is already zero-padded,
    so it cannot tell "the boundary arithmetic is right" from "the STRING
    happens to already be padded". `_parse_period_date` is deliberately
    lenient about a non-padded month/day, but `gl_journals.business_date` is
    ALWAYS stored zero-padded ISO and SQLite compares it as TEXT. Binding
    the ORIGINAL, non-padded `start_date`/`end_date` string -- rather than
    the parsed dates' own zero-padded `.isoformat()` -- means
    `business_date >= start_date` fails for a real padded date against a
    non-padded single-digit-month target ('2026-09-05' >= '2026-9-5' is
    FALSE as TEXT, since the 6th character '0' sorts before '9'), so a real
    payment silently DISAPPEARS from the report -- the same silent-wrong-
    rows shape `income_statement`'s identical fix (this file's sibling
    guard) closes. Mutation: comment out `start_date, end_date =
    start.isoformat(), end.isoformat()` in `cash_flow` and this goes red."""
    cid = client.test_company_id
    conn = get_retail_conn()
    try:
        gl.post_customer_payment(conn, cid, None, 'CF-NONPADDED', 'JOD', 18.0,
                                  business_date='2026-09-05')
        conn.commit()
    finally:
        conn.close()

    # The CALLER passes a non-padded single-digit month and day -- exactly
    # what `_parse_period_date` accepts without complaint.
    cf = _cash_flow(cid, '2026-9-5', '2026-9-5')
    assert cf['has_activity'] is True, (
        "a real payment dated 2026-09-05 must be found by the non-padded period "
        "'2026-9-5'..'2026-9-5' -- has_activity is False, the exact silent-wrong-rows "
        "shape a TEXT-lexicographic date comparison produces")
    assert cf['total_cash_in'] == 18.0, cf
    # The echoed-back period must ALSO be normalized.
    assert cf['period']['start_date'] == '2026-09-05', cf['period']
    assert cf['period']['end_date'] == '2026-09-05', cf['period']


# ── 9. An empty period reads as nothing moved, not a set of zeros ───────

def test_empty_period_reads_as_nothing_moved(client):
    cid = client.test_company_id
    today = _today_str()
    cf = _cash_flow(cid, today, today)
    assert cf['has_activity'] is False
    assert cf['total_cash_in'] == 0.0
    assert cf['total_cash_out'] == 0.0
    assert cf['net_change_in_cash'] == 0.0
    assert cf['operating_activities']['cash_in']['lines'] == []
    assert cf['operating_activities']['cash_out']['lines'] == []
    # opening/closing are still real, independently-derived figures (both 0
    # here, since nothing has EVER been posted for this fresh company) --
    # the reconciliation must still hold trivially.
    assert cf['reconciliation']['is_balanced'] is True


# ── 10. Invalid periods are refused, never silently swapped/defaulted ───

def test_start_after_end_is_refused(client):
    cid = client.test_company_id
    with pytest.raises(fin_statements.StatementPeriodError):
        _cash_flow(cid, '2026-06-20', '2026-06-01')


def test_malformed_date_is_refused(client):
    cid = client.test_company_id
    with pytest.raises(fin_statements.StatementPeriodError):
        _cash_flow(cid, 'not-a-date', _today_str())


# ── 11. The route itself: refusal shape and envelope ─────────────────────

def test_route_refuses_missing_dates_with_400(client):
    r = client.get(f'{API}/reports/cash-flow')
    assert r.status_code == 400
    assert r.get_json()['status'] == 'error'


def test_route_returns_the_same_figure_the_function_does(client):
    cid = client.test_company_id
    pid = _seed_product(client, sell_price=10.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 10.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    today = _today_str()
    r = client.get(f'{API}/reports/cash-flow?start_date={today}&end_date={today}')
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body['success'] is True
    assert body['data']['total_cash_in'] == 10.0
    assert body['data']['has_activity'] is True
