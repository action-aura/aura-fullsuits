"""Aura Retail -- `customer_statement` must agree with `customers.credit_balance`.

`GET /api/sub/retail/customers/<id>/statement` (retail_api.py) renders one
customer's AR ledger: a list of events plus a `running_balance` beside each,
and the live `credit_balance` as `balance`. Android's StatementSheet
(RetailExtraScreens.kt) prints the two side by side, so a customer looking at
their own statement sees both numbers at once.

They did not agree. This file pins the three separate reasons they did not,
each reproduced through the REAL routes (a real sale, a real return, a real
payment), never by writing a row by hand:

  1. A SALE'S OWN TENDER WAS COUNTED AS AN ACCOUNT PAYMENT.
     create_sale calls `_record_payment(..., party_type='customer',
     party_id=customer_id, related_type='sale', ...)` for the cash it
     actually retained (retail_api.py, `net_received`), and the statement's
     `receipts` query selects every `payments` row for the party with no
     filter on `related_type`. Meanwhile `charges` computed `total -
     amount_paid` -- ALREADY net of that same tender. So the tender was
     subtracted twice: once inside the charge, once as a payment line. A
     customer who only ever pays cash carries no AR at all and still
     accumulated a large NEGATIVE running balance, one line per visit,
     beside a `balance` of 0.

  2. THE CHARGE IGNORED REDEEMED LOYALTY POINTS.
     create_sale bills `amount_due_after_points = total - points_
     redeemed_amount` and deliberately leaves `sales.total` whole so the
     invoice reads right (see its own "THE CASH TRAP" comment). `total -
     amount_paid` therefore overstates the charge by exactly the redeemed
     value -- the same missing term that
     retail_returns_points_settlement_test.py pins on create_return, in a
     second place nobody had checked.

  3. A RETURN'S AR FORGIVENESS AND STORE CREDIT NEVER APPEARED AT ALL.
     create_return moves `credit_balance` through `_adjust_credit` for both
     buckets and writes NO `payments` row for either (retail_api.py's
     "AR forgiveness + store credit" block), so neither has ever been an
     event here. This route's own closing NOTE has named this in writing
     since the A-PAR wave, and ffa221a5's commit message left it as one of
     three items "for their own passes".

WHY THE FIX DROPS THE TENDER LINE rather than billing the whole sale and
keeping it. Both shapes make 1 reconcile, and the deciding question was
whether an excluded row could later move `credit_balance` behind the
statement's back -- void_payment reverses a customer payment with
`_adjust_credit(+amount)`. It cannot: that route refuses a receipt tied to a
sale outright ("This receipt belongs to a sale. Process a return against
that sale instead", 409). That refusal is what makes the exclusion safe, so
it is pinned below rather than assumed -- if it is ever relaxed, this
statement stops reconciling and that test is where it says so.

THE SUPPLIER MIRROR. supplier_statement carries the same duplicate-counting
defect and is fixed the other way round, because `purchase_orders.
amount_paid` is MUTABLE where `sales.amount_paid` is not: its charge is the
PO's live outstanding balance, so the PO's own payment rows are the half
that has to go. Pinned here too -- the two routes' comments cite each other,
so the tests belong together.

The customer-side writers of `credit_balance` are exactly five (verified by
reading every `_adjust_credit(` call site in retail_api.py): create_sale's
balance_due, create_return's ar_forgiven and store_credit, customer_payment,
`_apply_cheque_event`'s money leg, and void_payment's reversal. Every one of
them now has a corresponding statement event, which is why these tests can
assert the reconciliation as an equality. They do NOT assert it as a
route-level invariant -- see the route's own closing NOTE for what still
cannot be derived from events (an imported opening balance, and v36's
best-effort backfill of pre-split historical returns).

Self-contained bootstrap, per this directory's convention (no conftest.py --
see retail_ar_ap_totals_test.py's docstring).

Run:
    pytest products/retail/tests/retail_customer_statement_reconciliation_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_stmt_recon_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
# AURA_SITE_RELAY_ENABLED="0" -- this file seeds a WINDOWS-platform active
# licence below, and "licensed AND Windows" is exactly the condition
# config.py::site_relay_should_start uses to elect a till the LAN hub. Without
# this the app boot below starts four background threads and a TLS listener
# inside a test about one customer's ledger. Same fix, same reason, as
# retail_returns_prior_claims_test.py and retail_einvoicing_regression_test.py.
os.environ.update(
    AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
    AURA_SITE_RELAY_ENABLED="0",
)
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── harness ───────────────────────────────────────────────────────────────────

def _make_admin(price=100.0, stock=50):
    """New company + admin + branch + one zero-tax product + a logged-in
    client. Same shape as retail_returns_prior_claims_test.py's own
    `_make_admin`, narrowed to a single product line -- every scenario below
    is about ONE sale's money, not about splitting a basket."""
    email = f"stmt-{uuid.uuid4().hex[:10]}@test.local"
    password = "StmtReconPW1"
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()

    rconn = get_retail_conn()
    rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = rconn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)
    ).fetchone()[0]
    sku = f'ST-{uuid.uuid4().hex[:8]}'
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, sku, sku, price / 2, price),
    )
    pid = rconn.execute(
        "SELECT id FROM products WHERE company_id=? AND sku=?", (company_id, sku)
    ).fetchone()[0]
    rconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
        (company_id, pid, branch_id, stock),
    )
    rconn.commit()
    rconn.close()

    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    client.get("/api/sub/retail/settings/tax")

    # sale_number / return_number carry BARE (not company-scoped) UNIQUE
    # constraints while _next_ref()'s counter restarts at 1 per company, so
    # two test functions' first documents collide in this shared temp DB.
    # Same trick, same reason, as retail_returns_prior_claims_test.py.
    import random as _random
    dconn = get_retail_conn()
    for _doc_type in ('sale', 'return'):
        dconn.execute(
            "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,?,?)",
            (company_id, _doc_type, _random.randint(1, 5_000_000)),
        )
    dconn.commit()
    dconn.close()

    return client, company_id, branch_id, pid


def _make_credit_customer(cid, name='Statement Customer'):
    """credit_mode='unlimited' -- the company default is 'none', which
    refuses any credit sale outright."""
    rconn = get_retail_conn()
    customer_id = str(uuid.uuid4())
    rconn.execute(
        "INSERT INTO customers (id,company_id,name,credit_mode,credit_limit,credit_balance) "
        "VALUES (?,?,?,'unlimited',0,0)",
        (customer_id, cid, name),
    )
    rconn.commit()
    rconn.close()
    return customer_id


def _sell(client, customer_id, pid, amount_paid, payment_method='cash', points_redeemed=0):
    body = {
        'items': [{'product_id': pid, 'quantity': 1}],
        'customer_id': customer_id, 'amount_paid': amount_paid,
        'payment_method': payment_method, 'idempotency_key': str(uuid.uuid4()),
    }
    if points_redeemed:
        body['points_redeemed'] = points_redeemed
    r = client.post('/api/sub/retail/sales', json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _return_all(client, sale_id, pid, refund_method=None):
    body = {
        'sale_id': sale_id, 'items': [{'product_id': pid, 'quantity': 1}],
        'reason': 'statement reconciliation test', 'idempotency_key': str(uuid.uuid4()),
    }
    if refund_method is not None:
        body['refund_method'] = refund_method
    r = client.post('/api/sub/retail/returns', json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _statement(client, customer_id):
    r = client.get(f'/api/sub/retail/customers/{customer_id}/statement')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _final_running(stmt):
    """The last `running_balance` the customer actually sees -- 0.0 when the
    statement is empty, which is the correct reading of "no events"."""
    return stmt['events'][-1]['running_balance'] if stmt['events'] else 0.0


def _credit_balance(cid, customer_id):
    rconn = get_retail_conn()
    bal = rconn.execute(
        "SELECT credit_balance FROM customers WHERE id=? AND company_id=?", (customer_id, cid)
    ).fetchone()[0]
    rconn.close()
    return bal


def _seed_points(cid, customer_id, points, point_value=1.0):
    """An 'opening' loyalty balance plus the shop-level point value, both
    written the way the product itself stores them -- the ledger SUM is what
    create_sale reads (never customers.loyalty_points), and
    loyalty_point_value is OFF (0) by default so redemption refuses without
    it."""
    rconn = get_retail_conn()
    rconn.execute(
        "INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type) VALUES (?,?,?,?,'opening')",
        (str(uuid.uuid4()), cid, customer_id, points),
    )
    rconn.execute(
        "INSERT OR REPLACE INTO retail_settings (company_id,skey,svalue) VALUES (?,'loyalty_point_value',?)",
        (cid, str(point_value)),
    )
    rconn.commit()
    rconn.close()


# ── 1. a sale's own tender is not an account payment ──────────────────────────

def test_a_fully_paid_cash_sale_leaves_the_statement_at_zero_not_owing_the_customer():
    """THE SHAPE THIS CATCHES, and the reason it is first: it needs no credit,
    no return and no points -- an ordinary cash sale to a named regular is
    enough. create_sale wrote a `payments` row (party_type='customer') for the
    100 it retained; `charges` excluded the sale entirely because `total -
    amount_paid` is 0. So the statement showed ONE line, a "Payment" of 100,
    and a running balance of -100 against a real `credit_balance` of 0 -- i.e.
    it told a customer who owes nothing that the shop owes THEM 100, and it
    did so once per visit, cumulatively."""
    client, cid, _bid, pid = _make_admin(price=100.0)
    customer_id = _make_credit_customer(cid)

    _sell(client, customer_id, pid, amount_paid=100.0, payment_method='cash')

    assert _credit_balance(cid, customer_id) == 0.0
    stmt = _statement(client, customer_id)
    assert stmt['balance'] == 0.0
    assert _final_running(stmt) == 0.0, (
        f"statement ends at {_final_running(stmt)} for a customer who owes 0: {stmt['events']}"
    )


def test_a_partially_paid_credit_sale_ends_at_exactly_what_is_owed():
    """Sale 100, 40 tendered in cash, 60 left on credit. `credit_balance` is
    60. The charge (`total - amount_paid` = 60) was already net of the
    tender, and the tender was then subtracted AGAIN as a payment line, so
    the statement ended at 20."""
    client, cid, _bid, pid = _make_admin(price=100.0)
    customer_id = _make_credit_customer(cid)

    _sell(client, customer_id, pid, amount_paid=40.0, payment_method='credit')

    assert _credit_balance(cid, customer_id) == 60.0
    stmt = _statement(client, customer_id)
    assert stmt['balance'] == 60.0
    assert _final_running(stmt) == 60.0, (
        f"statement ends at {_final_running(stmt)}, customer owes 60: {stmt['events']}"
    )


# ── 2. redeemed points are not a debt ─────────────────────────────────────────

def test_a_points_redeemed_credit_sale_is_not_charged_at_its_pre_points_total():
    """Sale 100, 30 paid with loyalty points, nothing tendered, on credit.
    create_sale bills `amount_due_after_points` = 70 and credits exactly that,
    leaving `sales.total` at 100 so the invoice reads whole. The statement's
    charge was `total - amount_paid` = 100 -- the customer's own points read
    back to them as 30 of additional debt."""
    client, cid, _bid, pid = _make_admin(price=100.0)
    customer_id = _make_credit_customer(cid)
    _seed_points(cid, customer_id, points=30, point_value=1.0)

    sale = _sell(client, customer_id, pid, amount_paid=0.0, payment_method='credit', points_redeemed=30)
    assert sale.get('points_redeemed_amount', 30.0) == 30.0

    assert _credit_balance(cid, customer_id) == 70.0
    stmt = _statement(client, customer_id)
    assert stmt['balance'] == 70.0
    assert _final_running(stmt) == 70.0, (
        f"statement ends at {_final_running(stmt)}, customer owes 70: {stmt['events']}"
    )


# ── 3. a return's AR forgiveness and store credit are events ──────────────────

def test_a_return_that_forgives_ar_appears_on_the_statement():
    """Fully unpaid credit sale of 100, returned in full. create_return
    forgives the whole 100 through `_adjust_credit` and writes no `payments`
    row, so `credit_balance` went to 0 while the statement still showed the
    original charge and ended at 100 -- a settled customer reading that they
    still owe for goods they gave back."""
    client, cid, _bid, pid = _make_admin(price=100.0)
    customer_id = _make_credit_customer(cid)

    sale = _sell(client, customer_id, pid, amount_paid=0.0, payment_method='credit')
    _return_all(client, sale['id'], pid)

    assert _credit_balance(cid, customer_id) == 0.0
    stmt = _statement(client, customer_id)
    assert stmt['balance'] == 0.0
    assert _final_running(stmt) == 0.0, (
        f"statement ends at {_final_running(stmt)} after a full return settled the account: {stmt['events']}"
    )
    kinds = [e['kind'] for e in stmt['events']]
    assert 'return_forgiven' in kinds, kinds


def test_a_return_that_issues_store_credit_appears_on_the_statement():
    """The third settlement bucket. A credit sale of 100 is paid off in full
    through the real payment route FIRST, so by the time the goods come back
    there is no debt left to forgive -- create_return issues the whole value
    as store credit instead, driving `credit_balance` to -100 (the shop owes
    the customer). Nothing about that reached the statement either."""
    client, cid, _bid, pid = _make_admin(price=100.0)
    customer_id = _make_credit_customer(cid)

    sale = _sell(client, customer_id, pid, amount_paid=0.0, payment_method='credit')
    pay = client.post(f'/api/sub/retail/customers/{customer_id}/payments', json={'amount': 100.0})
    assert pay.status_code == 200, pay.get_json()
    assert _credit_balance(cid, customer_id) == 0.0

    _return_all(client, sale['id'], pid, refund_method='store_credit')

    assert _credit_balance(cid, customer_id) == -100.0
    stmt = _statement(client, customer_id)
    assert stmt['balance'] == -100.0
    assert _final_running(stmt) == -100.0, (
        f"statement ends at {_final_running(stmt)}, customer holds 100 of store credit: {stmt['events']}"
    )
    kinds = [e['kind'] for e in stmt['events']]
    assert 'return_credit' in kinds, kinds


# ── 4. the case that decides the fix's SHAPE ──────────────────────────────────

def test_a_sales_own_tender_cannot_be_voided_which_is_what_makes_excluding_it_safe():
    """THE LOAD-BEARING ASSUMPTION behind excluding `related_type='sale'`
    rows from the statement, pinned so that relaxing it fails here rather
    than silently un-reconciling every customer's ledger.

    void_payment reverses a customer payment with `_adjust_credit(+amount)`.
    If it accepted a sale's own tender row, `credit_balance` would climb to
    the full sale value with no event on the statement able to move with it.
    It refuses, and this is that refusal. Read the 409 body, not just the
    status: a generic 4xx from some unrelated guard (capability, licence)
    would satisfy a bare status check while proving nothing about THIS one.
    """
    client, cid, _bid, pid = _make_admin(price=100.0)
    customer_id = _make_credit_customer(cid)

    _sell(client, customer_id, pid, amount_paid=100.0, payment_method='cash')

    # The row exists and is tied to the sale -- read straight from the
    # ledger, because the statement (correctly) no longer shows it.
    rconn = get_retail_conn()
    pay_id = rconn.execute(
        "SELECT id FROM payments WHERE company_id=? AND party_type='customer' AND party_id=? "
        "AND related_type='sale' ORDER BY id DESC LIMIT 1", (cid, customer_id)
    ).fetchone()[0]
    rconn.close()

    v = client.post(f"/api/sub/retail/payments/{pay_id}/void", json={'reason': 'recon test'})
    assert v.status_code == 409, v.get_json()
    assert 'belongs to a sale' in v.get_json()['message'], v.get_json()

    # And it is genuinely absent from the statement rather than merely
    # netting out to the same number.
    stmt = _statement(client, customer_id)
    assert [e for e in stmt['events'] if e['kind'] == 'payment'] == [], stmt['events']
    assert _final_running(stmt) == 0.0 == stmt['balance']


# ── 5. the supplier mirror ────────────────────────────────────────────────────

def _make_supplier(cid, name='Statement Supplier'):
    rconn = get_retail_conn()
    supplier_id = str(uuid.uuid4())
    rconn.execute(
        "INSERT INTO suppliers (id,company_id,name,credit_balance) VALUES (?,?,?,0)",
        (supplier_id, cid, name),
    )
    rconn.commit()
    rconn.close()
    return supplier_id


def _supplier_statement(client, supplier_id):
    r = client.get(f'/api/sub/retail/suppliers/{supplier_id}/statement')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _supplier_balance(cid, supplier_id):
    rconn = get_retail_conn()
    bal = rconn.execute(
        "SELECT credit_balance FROM suppliers WHERE id=? AND company_id=?", (supplier_id, cid)
    ).fetchone()[0]
    rconn.close()
    return bal


def test_a_purchase_order_down_payment_is_not_subtracted_twice():
    """PO of 100 with 30 paid up front. create_purchase_order stores
    `amount_paid=30` (so the charge reads 70, already net of it) AND writes a
    `payments` row for the same 30, which the statement then subtracted
    again, landing on 40 against a real payable of 70."""
    client, cid, _bid, pid = _make_admin(price=100.0)
    supplier_id = _make_supplier(cid)

    r = client.post('/api/sub/retail/purchase-orders', json={
        'supplier_id': supplier_id,
        'items': [{'product_id': pid, 'quantity': 1, 'unit_cost': 100.0}],
        'amount_paid': 30.0,
    })
    assert r.status_code == 200, r.get_json()

    assert _supplier_balance(cid, supplier_id) == 70.0
    stmt = _supplier_statement(client, supplier_id)
    assert stmt['balance'] == 70.0
    assert _final_running(stmt) == 70.0, (
        f"supplier statement ends at {_final_running(stmt)}, shop owes 70: {stmt['events']}"
    )


def test_a_fully_paid_purchase_order_does_not_leave_the_supplier_statement_negative():
    """The supplier twin of scenario 1: pay the PO off through the real
    instalment route. pay_purchase_order UPDATEs `amount_paid` (so the charge
    disappears entirely) and writes its own payment row, which was left
    standing alone -- the statement read -100 against a payable of 0."""
    client, cid, _bid, pid = _make_admin(price=100.0)
    supplier_id = _make_supplier(cid)

    r = client.post('/api/sub/retail/purchase-orders', json={
        'supplier_id': supplier_id,
        'items': [{'product_id': pid, 'quantity': 1, 'unit_cost': 100.0}],
        'amount_paid': 0.0,
    })
    assert r.status_code == 200, r.get_json()
    po_id = r.get_json()['data']['id']
    assert _supplier_balance(cid, supplier_id) == 100.0

    p = client.post(f'/api/sub/retail/purchase-orders/{po_id}/pay', json={'amount': 100.0})
    assert p.status_code == 200, p.get_json()

    assert _supplier_balance(cid, supplier_id) == 0.0
    stmt = _supplier_statement(client, supplier_id)
    assert stmt['balance'] == 0.0
    assert _final_running(stmt) == 0.0, (
        f"supplier statement ends at {_final_running(stmt)} with nothing owed: {stmt['events']}"
    )


def test_a_direct_supplier_payment_still_appears_on_the_statement():
    """THE ALLOW-HALF of the same fix, and the reason the exclusion is
    `related_type='po'` rather than "every payment row". A direct account
    payment (supplier_payment) carries related_type NULL, touches no PO's
    `amount_paid`, and is the ONLY thing that accounts for its own effect on
    `credit_balance` -- dropping it would have re-broken the statement in the
    opposite direction, and `NULL <> 'po'` evaluating to NULL in SQL is
    exactly how a bare comparison would have done so silently."""
    client, cid, _bid, pid = _make_admin(price=100.0)
    supplier_id = _make_supplier(cid)

    r = client.post('/api/sub/retail/purchase-orders', json={
        'supplier_id': supplier_id,
        'items': [{'product_id': pid, 'quantity': 1, 'unit_cost': 100.0}],
        'amount_paid': 0.0,
    })
    assert r.status_code == 200, r.get_json()

    p = client.post(f'/api/sub/retail/suppliers/{supplier_id}/payments', json={'amount': 40.0})
    assert p.status_code == 200, p.get_json()

    assert _supplier_balance(cid, supplier_id) == 60.0
    stmt = _supplier_statement(client, supplier_id)
    assert stmt['balance'] == 60.0
    kinds = [e['kind'] for e in stmt['events']]
    assert 'payment' in kinds, kinds
    assert _final_running(stmt) == 60.0, (
        f"supplier statement ends at {_final_running(stmt)}, shop owes 60: {stmt['events']}"
    )
