"""Aura Retail -- schema v38 general ledger, wired into the real money-path
routes (create_sale, create_return, customer_payment, supplier_payment,
pay_purchase_order, create_purchase_order). HTTP-driven: every test here
calls the REAL Flask route, exactly as the till/back-office UI does, and
then reads `core/retail/general_ledger.py::trial_balance` back -- this is
the "run the real artefact" level CLAUDE.md's engineering standard demands,
one level above retail_gl_posting_test.py's direct-function-call coverage.

THE CENTRAL CLAIM THIS FILE PROVES: "a trial balance that ties back to the
AR/AP subledger" (ROADMAP.md's v38 entry) is not just true of `post_journal`
in isolation -- it holds after a REAL sequence of real documents, each one
driving GL posting through retail_api.py's own wiring, with the two ledgers
(gl_journal_lines and customers/suppliers.credit_balance) checked to agree
after EVERY step, not just at the end.

Self-contained bootstrap, matching retail_sale_money_precision_test.py's
own convention (no shared conftest.py exists for products/retail/tests/).
One company per test via the `client` fixture, so balance/statement
assertions never see another test's rows.

Run:
    pytest products/retail/tests/retail_gl_integration_test.py -q
"""
import os
import shutil
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_gl_integration_"))
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

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    email = f'gltest-{uuid.uuid4().hex[:8]}@test.local'
    password = 'GlIntegrationPW1'
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
    payload = {'name': 'GL Widget', 'sku': f'GLW-{uuid.uuid4().hex[:8]}', 'cost_price': 1.0,
               'sell_price': 10.0, 'tax_rate': 0, 'initial_stock': 100}
    payload.update(kw)
    r = c.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_customer(c):
    r = c.post(f'{API}/customers', json={'name': f'GL Customer {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_supplier(c):
    r = c.post(f'{API}/suppliers', json={'name': f'GL Supplier {uuid.uuid4().hex[:6]}'})
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


def _customers_credit_sum(company_id):
    conn = get_retail_conn()
    try:
        return conn.execute(
            "SELECT COALESCE(SUM(credit_balance),0) FROM customers WHERE company_id=?",
            (company_id,)).fetchone()[0]
    finally:
        conn.close()


def _suppliers_credit_sum(company_id):
    conn = get_retail_conn()
    try:
        return conn.execute(
            "SELECT COALESCE(SUM(credit_balance),0) FROM suppliers WHERE company_id=?",
            (company_id,)).fetchone()[0]
    finally:
        conn.close()


def _account(tb, code):
    return [a for a in tb['accounts'] if a['code'] == code][0]


def _trial_balance(company_id):
    conn = get_retail_conn()
    try:
        return gl.trial_balance(conn, company_id)
    finally:
        conn.close()


def _assert_ar_ties(company_id):
    tb = _trial_balance(company_id)
    assert tb['is_balanced'], tb
    ar = _account(tb, gl.ACCOUNT_AR)['balance']
    sub = _customers_credit_sum(company_id)
    assert abs(ar - sub) < 0.005, f"GL AR {ar} != subledger SUM(credit_balance) {sub}"
    return tb


def _assert_ap_ties(company_id):
    tb = _trial_balance(company_id)
    assert tb['is_balanced'], tb
    ap = _account(tb, gl.ACCOUNT_AP)['balance']
    sub = _suppliers_credit_sum(company_id)
    assert abs(ap - sub) < 0.005, f"GL AP {ap} != subledger SUM(credit_balance) {sub}"
    return tb


# ── 1. Walk-in cash sale ──────────────────────────────────────────────────

def test_walk_in_cash_sale_posts_a_balanced_journal(client):
    cid = client.test_company_id
    pid = _seed_product(client, sell_price=10.0, tax_rate=10)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 2}],
        'payment_method': 'cash', 'amount_paid': 22.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    tb = _trial_balance(cid)
    assert tb['is_balanced']
    assert _account(tb, gl.ACCOUNT_CASH)['balance'] == 22.0
    assert _account(tb, gl.ACCOUNT_SALES_REVENUE)['balance'] == 20.0
    assert _account(tb, gl.ACCOUNT_TAX_PAYABLE)['balance'] == 2.0
    assert _account(tb, gl.ACCOUNT_AR)['balance'] == 0.0

    # Traceability: the journal must carry source_type/source_id pointing
    # back at this exact sale.
    conn = get_retail_conn()
    sale_id = conn.execute(
        "SELECT id FROM sales WHERE company_id=?", (cid,)).fetchone()[0]
    journal = conn.execute(
        "SELECT source_type, source_id, reference FROM gl_journals "
        "WHERE company_id=? AND source_type='sale'", (cid,)).fetchone()
    conn.close()
    assert journal['source_id'] == sale_id
    assert journal['source_type'] == 'sale'


def test_walk_in_sale_with_change_given_back_books_only_net_received(client):
    """`change` (cash physically handed back) must NOT inflate the Cash
    debit -- proves the GL reads net_received, not the raw tendered
    amount."""
    cid = client.test_company_id
    pid = _seed_product(client, sell_price=10.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 50.0,  # tenders way over
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']
    assert body['change'] == 40.0

    tb = _trial_balance(cid)
    assert tb['is_balanced']
    assert _account(tb, gl.ACCOUNT_CASH)['balance'] == 10.0
    assert _account(tb, gl.ACCOUNT_SALES_REVENUE)['balance'] == 10.0


# ── 2. Credit sale ────────────────────────────────────────────────────────

def test_credit_sale_posts_ar_leg_tying_to_customer_subledger(client):
    cid = client.test_company_id
    cust_id = _seed_customer(client)
    _set_customer_credit(cust_id)
    pid = _seed_product(client, sell_price=50.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'credit', 'customer_id': cust_id, 'amount_paid': 20.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']
    assert body['balance_due'] == 30.0

    tb = _assert_ar_ties(cid)
    assert _account(tb, gl.ACCOUNT_AR)['balance'] == 30.0
    assert _account(tb, gl.ACCOUNT_CASH)['balance'] == 20.0
    assert _account(tb, gl.ACCOUNT_SALES_REVENUE)['balance'] == 50.0


def test_credit_sale_refused_for_walk_in_posts_no_journal_at_all(client):
    """`is_credit` without a customer is refused by create_sale itself
    (400, before any row is written) -- proves GL posting is inside that
    SAME atomicity: a refused sale must leave the ledger untouched, not a
    partial or compensating journal."""
    cid = client.test_company_id
    pid = _seed_product(client, sell_price=50.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 10.0,  # leaves 40 unpaid, no customer
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 400, r.get_json()

    tb = _trial_balance(cid)
    assert tb['accounts'] == [] or all(a['debit'] == 0 for a in tb['accounts']), (
        "a refused credit sale must post nothing at all")


# ── 3. Loyalty redemption ────────────────────────────────────────────────

def test_sale_with_loyalty_redemption_posts_loyalty_expense_leg(client):
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
    body = r.get_json()['data']
    assert body['points_redeemed_amount'] == 15.0

    tb = _trial_balance(cid)
    assert tb['is_balanced']
    assert _account(tb, gl.ACCOUNT_LOYALTY_EXPENSE)['balance'] == 15.0
    # net_received = total(100) - points_redeemed_amount(15) = 85, tendered
    # 100 -> change 15 handed back, net_received stays 85.
    assert _account(tb, gl.ACCOUNT_CASH)['balance'] == 85.0
    assert _account(tb, gl.ACCOUNT_SALES_REVENUE)['balance'] == 100.0


# ── 4. Return against a credit sale ──────────────────────────────────────

def test_return_against_credit_sale_reverses_ar_and_ties_to_subledger(client):
    cid = client.test_company_id
    cust_id = _seed_customer(client)
    _set_customer_credit(cust_id)
    pid = _seed_product(client, sell_price=50.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'credit', 'customer_id': cust_id, 'amount_paid': 20.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']
    _assert_ar_ties(cid)

    r = client.get(f'{API}/sales/{sale["id"]}')
    assert r.status_code == 200, r.get_json()
    line = r.get_json()['data']['items'][0]
    r = client.post(f'{API}/returns', json={
        'sale_id': sale['id'],
        'items': [{'product_id': pid, 'quantity': 1, 'sale_item_id': line.get('id')}],
        'reason': 'GL integration test', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    ret = r.get_json()['data']
    assert ret['ar_forgiven_amount'] == 30.0
    assert ret['tender_refund_amount'] == 20.0

    tb = _assert_ar_ties(cid)
    assert _account(tb, gl.ACCOUNT_AR)['balance'] == 0.0
    assert _account(tb, gl.ACCOUNT_SALES_RETURNS)['balance'] == 50.0

    conn = get_retail_conn()
    journal = conn.execute(
        "SELECT source_type, source_id FROM gl_journals WHERE company_id=? AND source_type='return'",
        (cid,)).fetchone()
    return_id = conn.execute(
        "SELECT id FROM returns WHERE company_id=?", (cid,)).fetchone()[0]
    conn.close()
    assert journal['source_id'] == return_id


def test_full_cash_return_with_no_ar_posts_cash_and_returns_only(client):
    """A plain cash sale, fully refunded in cash: no AR leg at all -- the
    return posting must not create a phantom AR line just because the
    helper accepts one."""
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
        'reason': 'full cash return', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    tb = _trial_balance(cid)
    assert tb['is_balanced']
    assert _account(tb, gl.ACCOUNT_CASH)['balance'] == 0.0  # +20 sale, -20 return
    assert _account(tb, gl.ACCOUNT_AR)['balance'] == 0.0
    assert _account(tb, gl.ACCOUNT_SALES_RETURNS)['balance'] == 20.0


# ── 5. Customer / supplier account payments ──────────────────────────────

def test_customer_payment_clears_ar_and_ties_to_subledger(client):
    cid = client.test_company_id
    cust_id = _seed_customer(client)
    _set_customer_credit(cust_id)
    pid = _seed_product(client, sell_price=100.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'credit', 'customer_id': cust_id, 'amount_paid': 0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    _assert_ar_ties(cid)

    r = client.post(f'{API}/customers/{cust_id}/payments', json={'amount': 40.0, 'method': 'cash'})
    assert r.status_code == 200, r.get_json()

    tb = _assert_ar_ties(cid)
    assert _account(tb, gl.ACCOUNT_AR)['balance'] == 60.0
    assert _account(tb, gl.ACCOUNT_CASH)['balance'] == 40.0


def test_supplier_payment_clears_ap_and_ties_to_subledger(client):
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=5.0)
    r = client.post(f'{API}/purchase-orders', json={
        'supplier_id': supp_id,
        'items': [{'product_id': pid, 'quantity': 10, 'unit_cost': 4.0}],
        'amount_paid': 0})
    assert r.status_code == 200, r.get_json()
    _assert_ap_ties(cid)

    r = client.post(f'{API}/suppliers/{supp_id}/payments', json={'amount': 15.0, 'method': 'cash'})
    assert r.status_code == 200, r.get_json()

    tb = _assert_ap_ties(cid)
    assert _account(tb, gl.ACCOUNT_AP)['balance'] == 25.0
    assert _account(tb, gl.ACCOUNT_CASH)['balance'] == -15.0


# ── 6. Purchase orders ────────────────────────────────────────────────────

def test_purchase_order_with_down_payment_posts_inventory_cash_ap(client):
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=5.0)
    r = client.post(f'{API}/purchase-orders', json={
        'supplier_id': supp_id,
        'items': [{'product_id': pid, 'quantity': 10, 'unit_cost': 4.0}],
        'amount_paid': 15.0})
    assert r.status_code == 200, r.get_json()

    tb = _assert_ap_ties(cid)
    # STOCK-BOOKS: an ORDER is not stock -- it books to Goods Ordered Not
    # Received (1260); Inventory (1200) moves only on receipt (see the
    # receive test in retail_stock_books_routes_test.py). This replaces the
    # old `1200 == 40`. What it can no longer catch: nothing here -- the
    # "stock reaches 1200" half is asserted at receipt, and the 1200 == 0
    # line below is what proves the order did NOT inflate Inventory.
    assert _account(tb, gl.ACCOUNT_GOODS_ORDERED)['balance'] == 40.0
    assert _account(tb, gl.ACCOUNT_INVENTORY)['balance'] == 0.0
    assert _account(tb, gl.ACCOUNT_CASH)['balance'] == -15.0
    assert _account(tb, gl.ACCOUNT_AP)['balance'] == 25.0


def test_purchase_order_with_a_negative_amount_paid_does_not_inflate_ap(client):
    """A previously-shipped money bug, found by the GL's balance invariant
    rather than by reading the route.

    `amount_paid: -50` is a deliberately ALLOWED input -- retail_route_
    capability_matrix_test.py's `negative -> allowed` case says so, and it
    is right about the half it was asserting: a negative never reached
    _record_payment, because the capability gate and the payment write both
    sit behind `> _money_epsilon(currency)`. But nothing asserted the OTHER
    half. `balance = _money(total - amount_paid)` turned `40 - (-50)` into
    `90` and _adjust_credit pushed all ninety onto the supplier's AP, on a
    PO worth forty, while paying nothing out. Single-sided bookkeeping had
    no way to notice; posting the same document to the GL raised
    GeneralLedgerImbalanceError because double-entry cannot express
    "inventory worth 40 financed by 90 of supplier credit".

    So this asserts all three surfaces that used to disagree, not just the
    ledger: the supplier subledger, the stored PO row, and the trial
    balance. Asserting only `is_balanced` would pass against a route that
    clamped the GL call alone and left `suppliers.credit_balance` wrong --
    which is the shape of fix this codebase's engineering standard calls
    treating the symptom.
    """
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=5.0)
    r = client.post(f'{API}/purchase-orders', json={
        'supplier_id': supp_id,
        'items': [{'product_id': pid, 'quantity': 10, 'unit_cost': 4.0}],
        'amount_paid': -50.0})
    assert r.status_code == 200, r.get_json()

    conn = get_retail_conn()
    try:
        row = conn.execute(
            "SELECT amount_paid, total FROM purchase_orders WHERE company_id=? "
            "ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        owed = conn.execute(
            "SELECT credit_balance FROM suppliers WHERE id=? AND company_id=?",
            (supp_id, cid)).fetchone()['credit_balance']
    finally:
        conn.close()

    assert row['total'] == 40.0
    assert row['amount_paid'] == 0.0, (
        'a negative down-payment must be stored as nothing paid, not as a '
        'negative that every later subtraction turns into extra debt')
    assert owed == 40.0, (
        f'the shop owes the PO total, not total + |amount_paid|; got {owed}')

    tb = _assert_ap_ties(cid)
    # STOCK-BOOKS: 40 of goods ordered (1260), not yet stock (1200 == 0);
    # nothing is lost -- receipt moves it, asserted in the routes test.
    assert _account(tb, gl.ACCOUNT_GOODS_ORDERED)['balance'] == 40.0
    assert _account(tb, gl.ACCOUNT_INVENTORY)['balance'] == 0.0
    assert _account(tb, gl.ACCOUNT_AP)['balance'] == 40.0
    assert _account(tb, gl.ACCOUNT_CASH)['balance'] == 0.0


def test_purchase_order_paid_in_full_up_front_posts_no_ap_leg(client):
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=5.0)
    r = client.post(f'{API}/purchase-orders', json={
        'supplier_id': supp_id,
        'items': [{'product_id': pid, 'quantity': 10, 'unit_cost': 4.0}],
        'amount_paid': 40.0})
    assert r.status_code == 200, r.get_json()

    tb = _trial_balance(cid)
    assert tb['is_balanced']
    assert _account(tb, gl.ACCOUNT_AP)['balance'] == 0.0
    # STOCK-BOOKS: paid-in-full order -> 1260 (not yet received), 1200 == 0.
    assert _account(tb, gl.ACCOUNT_GOODS_ORDERED)['balance'] == 40.0
    assert _account(tb, gl.ACCOUNT_INVENTORY)['balance'] == 0.0
    assert _account(tb, gl.ACCOUNT_CASH)['balance'] == -40.0


def test_purchase_order_with_no_supplier_still_balances(client):
    """`supplier_id` is OPTIONAL on create_purchase_order -- see
    post_purchase_order's own docstring. A PO with no supplier and an
    unpaid balance must still post a BALANCED journal (Inventory booked
    only at what is actually accounted for), never raise and block PO
    creation."""
    cid = client.test_company_id
    pid = _seed_product(client, sell_price=5.0)
    r = client.post(f'{API}/purchase-orders', json={
        'items': [{'product_id': pid, 'quantity': 10, 'unit_cost': 4.0}],
        'amount_paid': 10.0})  # 30 left unpaid, no supplier to charge it to
    assert r.status_code == 200, r.get_json()

    tb = _trial_balance(cid)
    assert tb['is_balanced']
    # Only the accounted-for amount (the down payment) is booked -- see
    # post_purchase_order's own docstring for why the unpaid 30 is not
    # invented as Inventory the subledger cannot explain either.
    # STOCK-BOOKS: the accounted-for 10 sits in 1260 until received.
    assert _account(tb, gl.ACCOUNT_GOODS_ORDERED)['balance'] == 10.0
    assert _account(tb, gl.ACCOUNT_INVENTORY)['balance'] == 0.0
    assert _account(tb, gl.ACCOUNT_CASH)['balance'] == -10.0
    assert _account(tb, gl.ACCOUNT_AP)['balance'] == 0.0


def test_pay_purchase_order_posts_ap_settlement(client):
    cid = client.test_company_id
    supp_id = _seed_supplier(client)
    pid = _seed_product(client, sell_price=5.0)
    r = client.post(f'{API}/purchase-orders', json={
        'supplier_id': supp_id,
        'items': [{'product_id': pid, 'quantity': 10, 'unit_cost': 4.0}],
        'amount_paid': 15.0})
    assert r.status_code == 200, r.get_json()
    po = r.get_json()['data']

    r = client.post(f'{API}/purchase-orders/{po["id"]}/pay', json={'amount': 25.0, 'method': 'cash'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['payment_status'] == 'paid'

    tb = _assert_ap_ties(cid)
    assert _account(tb, gl.ACCOUNT_AP)['balance'] == 0.0
    assert _account(tb, gl.ACCOUNT_CASH)['balance'] == -40.0


# ── 7. The end-to-end, multi-document session ────────────────────────────

def test_full_session_trial_balance_ties_out_after_every_document_type(client):
    """One company, every document type this module posts from, in
    sequence -- the AR/AP tie-out is checked after EACH step, not only at
    the end, so a document that breaks the invariant is caught at the step
    that broke it. Mirrors the scenario this feature was actually verified
    against during development (see the task's own report)."""
    cid = client.test_company_id

    # 1. Walk-in cash sale.
    pid1 = _seed_product(client, sell_price=10.0, tax_rate=10)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid1, 'quantity': 2}],
        'payment_method': 'cash', 'amount_paid': 22.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    tb = _trial_balance(cid)
    assert tb['is_balanced']

    # 2. Credit sale.
    cust_id = _seed_customer(client)
    _set_customer_credit(cust_id)
    pid2 = _seed_product(client, sell_price=50.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid2, 'quantity': 1}],
        'payment_method': 'credit', 'customer_id': cust_id, 'amount_paid': 20.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    sale2 = r.get_json()['data']
    _assert_ar_ties(cid)

    # 3. Partial return against the credit sale.
    r = client.get(f'{API}/sales/{sale2["id"]}')
    line = r.get_json()['data']['items'][0]
    r = client.post(f'{API}/returns', json={
        'sale_id': sale2['id'],
        'items': [{'product_id': pid2, 'quantity': 1, 'sale_item_id': line.get('id')}],
        'reason': 'full session test', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    _assert_ar_ties(cid)

    # 4. Customer payment (whatever remains, if anything).
    remaining = _customers_credit_sum(cid)
    if remaining > 0.005:
        r = client.post(f'{API}/customers/{cust_id}/payments', json={'amount': remaining, 'method': 'cash'})
        assert r.status_code == 200, r.get_json()
    _assert_ar_ties(cid)

    # 5. Purchase order with a down payment.
    supp_id = _seed_supplier(client)
    pid3 = _seed_product(client, sell_price=5.0)
    r = client.post(f'{API}/purchase-orders', json={
        'supplier_id': supp_id,
        'items': [{'product_id': pid3, 'quantity': 10, 'unit_cost': 4.0}],
        'amount_paid': 15.0})
    assert r.status_code == 200, r.get_json()
    po = r.get_json()['data']
    _assert_ap_ties(cid)

    # 6. Pay off the rest of the PO.
    r = client.post(f'{API}/purchase-orders/{po["id"]}/pay', json={'amount': 25.0, 'method': 'cash'})
    assert r.status_code == 200, r.get_json()
    final_tb = _assert_ap_ties(cid)
    _assert_ar_ties(cid)

    # The whole session, every document type, still balances globally.
    assert final_tb['is_balanced']
    assert final_tb['total_debit'] == final_tb['total_credit']
