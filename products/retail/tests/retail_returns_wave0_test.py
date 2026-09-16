"""
Aura Retail -- server-authoritative return/refund regression suite
(Wave 0, AUDIT-004).

Covers: full/partial/cumulative returns, rejecting a return that exceeds
what remains returnable (accounting for prior returns), rejecting a return
against a nonexistent sale or a product that wasn't part of the sale,
idempotent resubmission, multi-line returns, tax-inclusive proportional
refund computation, and inventory restoration.

See docs/corrections/wave0/retail-return-correction.md.

Run:
    pytest products/retail/tests/retail_returns_wave0_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_returns_"))
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


def _make_admin_and_product(price=100.0, tax_rate=15.0, stock=50):
    email = f"ret-{uuid.uuid4().hex[:10]}@test.local"
    password = "ReturnsPW1"
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
    rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = rconn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)
    ).fetchone()[0]
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,'RT-1','Return Item',5,?,?)",
        (str(uuid.uuid4()), company_id, price, tax_rate),
    )
    product_id = rconn.execute(
        "SELECT id FROM products WHERE company_id=? AND sku='RT-1'", (company_id,)
    ).fetchone()[0]
    rconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
        (company_id, product_id, branch_id, stock),
    )
    rconn.commit()
    rconn.close()

    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})
    client.get("/api/sub/retail/settings/tax")

    # sales.sale_number carries a bare (not company-scoped) UNIQUE constraint,
    # but _next_ref()'s counter starts at 1 per company -- every test's fresh
    # company would otherwise generate "SALE-000001" as its first sale and
    # collide with every other test's first sale in this shared temp DB.
    import random as _random
    dconn = get_retail_conn()
    dconn.execute(
        "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
        (company_id, _random.randint(1, 5_000_000)),
    )
    dconn.commit()
    dconn.close()

    return client, company_id, product_id, branch_id


def _sell(client, pid, quantity, discount_pct=0):
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': quantity, 'discount_pct': discount_pct}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _return(client, sale_id, pid, quantity, idem=None, reason='test'):
    return client.post('/api/sub/retail/returns', json={
        'sale_id': sale_id,
        'items': [{'product_id': pid, 'quantity': quantity}],
        'reason': reason,
        'idempotency_key': idem or str(uuid.uuid4()),
    })


def test_full_return_refunds_tax_inclusive_amount():
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=15.0, stock=10)
    sale = _sell(client, pid, quantity=1)
    assert sale['total'] == 115.0
    r = _return(client, sale['id'], pid, quantity=1)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['refund_amount'] == 115.0


def test_partial_return_refunds_proportionally():
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=15.0, stock=10)
    sale = _sell(client, pid, quantity=4)  # total = 460.0
    r = _return(client, sale['id'], pid, quantity=1)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['refund_amount'] == 115.0


def test_cumulative_partial_returns_sum_correctly_and_third_is_rejected():
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=15.0, stock=10)
    sale = _sell(client, pid, quantity=2)
    r1 = _return(client, sale['id'], pid, quantity=1)
    assert r1.status_code == 200, r1.get_json()
    r2 = _return(client, sale['id'], pid, quantity=1)
    assert r2.status_code == 200, r2.get_json()
    r3 = _return(client, sale['id'], pid, quantity=1)
    assert r3.status_code == 400
    assert 'remain returnable' in r3.get_json()['message'].lower()


def test_excessive_return_rejected_up_front():
    client, cid, pid, bid = _make_admin_and_product(stock=10)
    sale = _sell(client, pid, quantity=2)
    r = _return(client, sale['id'], pid, quantity=5)
    assert r.status_code == 400
    assert 'remain returnable' in r.get_json()['message'].lower()


def test_return_against_nonexistent_sale_rejected():
    client, cid, pid, bid = _make_admin_and_product()
    r = _return(client, 999999999, pid, quantity=1)
    assert r.status_code == 404


def test_return_of_product_not_in_sale_rejected():
    client, cid, pid, bid = _make_admin_and_product()
    rconn = get_retail_conn()
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,'RT-2','Other Item',5,50,0)",
        (str(uuid.uuid4()), cid),
    )
    other_pid = rconn.execute("SELECT id FROM products WHERE company_id=? AND sku='RT-2'", (cid,)).fetchone()[0]
    rconn.commit()
    rconn.close()

    sale = _sell(client, pid, quantity=1)
    r = _return(client, sale['id'], other_pid, quantity=1)
    assert r.status_code == 400
    assert 'not part of' in r.get_json()['message'].lower()


def test_duplicate_return_idempotency_key_returns_original_not_a_second_refund():
    client, cid, pid, bid = _make_admin_and_product(stock=10)
    sale = _sell(client, pid, quantity=2)
    key = str(uuid.uuid4())
    r1 = _return(client, sale['id'], pid, quantity=1, idem=key)
    r2 = _return(client, sale['id'], pid, quantity=1, idem=key)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.get_json()['data']['id'] == r2.get_json()['data']['id']

    rconn = get_retail_conn()
    count = rconn.execute("SELECT COUNT(*) FROM returns WHERE company_id=? AND idempotency_key=?", (cid, key)).fetchone()[0]
    rconn.close()
    assert count == 1


def test_multi_line_return_refunds_sum_of_lines():
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=15.0, stock=10)
    rconn = get_retail_conn()
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,'RT-3','Second Item',5,50,0)",
        (str(uuid.uuid4()), cid),
    )
    pid2 = rconn.execute("SELECT id FROM products WHERE company_id=? AND sku='RT-3'", (cid,)).fetchone()[0]
    rconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,20)",
        (cid, pid2, bid),
    )
    rconn.commit()
    rconn.close()

    r = client.post('/api/sub/retail/sales', json={
        'items': [
            {'product_id': pid, 'quantity': 1},
            {'product_id': pid2, 'quantity': 1},
        ],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    sale = r.get_json()['data']
    assert sale['total'] == 165.0  # 115 + 50

    ret = client.post('/api/sub/retail/returns', json={
        'sale_id': sale['id'],
        'items': [{'product_id': pid, 'quantity': 1}, {'product_id': pid2, 'quantity': 1}],
        'reason': 'multi-line test', 'idempotency_key': str(uuid.uuid4()),
    })
    assert ret.status_code == 200, ret.get_json()
    assert ret.get_json()['data']['refund_amount'] == 165.0


def test_duplicate_product_lines_in_one_request_do_not_multiply_refund_or_stock():
    # AUDIT: quantity-validation-bypass -- the already-returned check inside
    # the per-line validation loop only sees return_items rows committed
    # BEFORE this request started; it never accounted for other lines in the
    # SAME request (those INSERTs happen in a separate, later loop). A
    # payload with two lines for the same product_id therefore validated
    # each line against the same stale "nothing returned yet" snapshot and
    # let both pass, doubling the refund and crediting back twice the units
    # actually sold. Only 2 units were sold; a single request asking to
    # return 2+2 of the same product must be rejected outright, and nothing
    # (refund, return row, inventory) may be applied partially.
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=15.0, stock=10)
    sale = _sell(client, pid, quantity=2)  # total = 230.0, 2 units sold

    before = get_retail_conn()
    stock_before = before.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
        (cid, pid, bid)
    ).fetchone()[0]
    before.close()

    r = client.post('/api/sub/retail/returns', json={
        'sale_id': sale['id'],
        'items': [{'product_id': pid, 'quantity': 2}, {'product_id': pid, 'quantity': 2}],
        'reason': 'duplicate-line probe', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 400, r.get_json()
    assert 'remain returnable' in r.get_json()['message'].lower()

    rconn = get_retail_conn()
    return_count = rconn.execute("SELECT COUNT(*) FROM returns WHERE company_id=?", (cid,)).fetchone()[0]
    stock_after = rconn.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
        (cid, pid, bid)
    ).fetchone()[0]
    rconn.close()
    assert return_count == 0
    assert stock_after == stock_before  # no partial inventory credit from the rejected request


def test_return_restores_inventory():
    client, cid, pid, bid = _make_admin_and_product(stock=10)
    sale = _sell(client, pid, quantity=3)
    rconn = get_retail_conn()
    after_sale = rconn.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
        (cid, pid, bid)
    ).fetchone()[0]
    rconn.close()
    assert after_sale == 7

    _return(client, sale['id'], pid, quantity=2)
    rconn = get_retail_conn()
    after_return = rconn.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
        (cid, pid, bid)
    ).fetchone()[0]
    rconn.close()
    assert after_return == 9


def test_discounted_sale_return_reverses_discount_and_tax_proportionally():
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=10.0, stock=10)
    sale = _sell(client, pid, quantity=1, discount_pct=20)
    assert sale['total'] == 88.0  # 100 - 20% = 80, +10% tax = 88
    r = _return(client, sale['id'], pid, quantity=1)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['refund_amount'] == 88.0


def _make_credit_customer(cid, name='Credit Customer'):
    """Direct DB insert + credit_mode set to 'unlimited', matching this
    file's own established pattern of setting up fixtures directly via
    get_retail_conn() (see _make_admin_and_product's branch/product/stock
    setup above) rather than exercising unrelated API surface. credit_mode
    defaults to 'none' per customer row (schema.py) regardless of the
    company-wide settings/credit default -- that default only ever applies
    as a fallback when a customer's OWN credit_mode is NULL, which the
    schema default never actually leaves it as."""
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


def _credit_balance(cid, customer_id):
    """There is no GET /customers/<id> route (only PATCH/DELETE) -- read
    the real column directly, same as this file's other DB-verification
    assertions (e.g. the inventory_balances checks above)."""
    rconn = get_retail_conn()
    bal = rconn.execute(
        "SELECT credit_balance FROM customers WHERE id=? AND company_id=?", (customer_id, cid)
    ).fetchone()[0]
    rconn.close()
    return bal


def test_return_against_unpaid_credit_sale_reduces_ar():
    """Real bug, fixed: create_return() never touched credit_balance at all
    -- a customer returning goods bought entirely on credit still owed the
    full original amount afterward. A full return of a fully-unpaid sale
    must credit the customer's AR down to zero, not leave it unchanged."""
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=15.0, stock=10)
    customer_id = _make_credit_customer(cid)

    sale = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'customer_id': customer_id, 'amount_paid': 0, 'payment_method': 'credit',
        'idempotency_key': str(uuid.uuid4()),
    }).get_json()['data']
    assert sale['total'] == 115.0
    assert sale['balance_due'] == 115.0
    assert _credit_balance(cid, customer_id) == 115.0

    r = _return(client, sale['id'], pid, quantity=1)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['refund_amount'] == 115.0

    assert _credit_balance(cid, customer_id) == 0.0, \
        "returning the only item on a fully-unpaid sale must zero out what's owed"


def _open_cash_session(client, opening_float=0.0):
    """Open this test client's cash session on its (sole) branch, matching
    the live-till reproduction's own first step. Returns the session id."""
    r = client.post('/api/sub/retail/cash-sessions/open', json={'opening_float': opening_float})
    assert r.status_code == 200, r.get_json()
    # `_cash_session_public` starts from `dict(sess)` -- the raw
    # `cash_sessions` row -- so the primary key keeps its own column name,
    # `id`, not a renamed `session_id`.
    return r.get_json()['data']['id']


def _x_report(client, session_id):
    """GET the live X-report for one session -- the same route the close-out
    modal and this task's own reproduction both read."""
    r = client.get(f'/api/sub/retail/cash-sessions/{session_id}/x-report')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def test_return_against_partially_paid_sale_reduces_ar_by_the_outstanding_portion_only():
    """A sale that was PARTLY paid in cash, partly on credit -- the return
    must only credit back the still-outstanding portion, never more than
    what was actually left unpaid (the cash portion isn't AR to begin
    with).

    DEFECT 1 fix: also pins the drawer side of this exact fixture -- this
    is the review's own verified regression case (a nominally 'credit'
    sale whose real cash deposit must still be counted as a cash outflow
    on refund). See core/retail/returns_settlement.py / create_return's
    'credit'->'cash' refund_method coercion.
    """
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=15.0, stock=10)
    customer_id = _make_credit_customer(cid)
    session_id = _open_cash_session(client, opening_float=0.0)

    # Total 115: $65 paid now, $50 left on credit.
    sale = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'customer_id': customer_id, 'amount_paid': 65.0, 'payment_method': 'credit',
        'idempotency_key': str(uuid.uuid4()),
    }).get_json()['data']
    assert sale['balance_due'] == 50.0
    assert _credit_balance(cid, customer_id) == 50.0

    r = _return(client, sale['id'], pid, quantity=1)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['refund_amount'] == 115.0  # full refund figure, unrelated to AR crediting
    # DEFECT 1 fix: the real split. tender_refund_amount must be exactly the
    # $65 that was actually collected in cash -- not the full $115 refund
    # figure, and not 0 (refund_method defaults to the sale's raw 'credit'
    # label only AFTER the create_sale-matching 'credit'->'cash' coercion;
    # without that coercion this would default to 'cash_refunds' filtering
    # on refund_method='credit', which never matches, silently undercounting
    # the real cash outflow to 0 -- see the review's own finding).
    assert d['tender_refund_amount'] == 65.0, \
        f"expected the $65 actually collected in cash, got {d['tender_refund_amount']}"
    assert d['ar_forgiven_amount'] == 50.0, \
        f"expected exactly the $50 that was owed, got {d['ar_forgiven_amount']}"
    assert d['store_credit_amount'] == 0.0

    assert _credit_balance(cid, customer_id) == 0.0, \
        "must credit exactly the $50 that was actually owed, not the full $115 refund"

    # THE DRAWER ITSELF, not just the returns row -- DEFECT 1's exact shape.
    # Before this fix, `_cash_session_report` summed `refund_amount` (115)
    # under refund_method='cash', overstating the real cash outflow by the
    # $50 that was ALSO separately forgiven as AR -- a double-count of the
    # unpaid portion. `tender_refund_amount` (65) is the true figure.
    report = _x_report(client, session_id)
    assert report['cash_sales'] == 65.0
    assert report['cash_refunds'] == 65.0, \
        f"cash_refunds must read the real $65 collected, not the full $115 refund_amount, got {report['cash_refunds']}"
    assert report['expected_cash'] == 0.0, \
        f"a sale's own $65 cash deposit fully refunded in cash must net to 0, got {report['expected_cash']}"


def test_return_against_fully_paid_cash_sale_never_touches_credit_balance():
    """A plain cash sale (no customer, or a customer who paid in full) has
    no AR to reduce -- this must be a true no-op on credit_balance, not
    just 'doesn't crash'."""
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=15.0, stock=10)
    customer_id = _make_credit_customer(cid)

    sale = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'customer_id': customer_id, 'amount_paid': 115.0, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    }).get_json()['data']
    assert sale['balance_due'] == 0.0

    r = _return(client, sale['id'], pid, quantity=1)
    assert r.status_code == 200, r.get_json()

    assert _credit_balance(cid, customer_id) == 0.0


def test_return_cash_payout_never_exceeds_cash_actually_collected():
    """DEFECT 1 -- THE TASK'S OWN LIVE-TILL REPRODUCTION, reproduced here at
    the HTTP level: sale total 100.000, amount_paid 20.000 cash,
    balance_due 80.000 credit; refund_method OMITTED on a full return (the
    exact shape the POS's own pre-fix default -- hardcoded 'cash' -- used
    to hand a cashier with one careless click). `expected_cash` must never
    go negative, and the drawer must move by only the $20 actually
    collected.

    Mutation that must turn this red: restore the pre-fix code (refund the
    FULL `refund_amount` as cash regardless of what was collected, and
    default `refund_method` to a hardcoded 'cash') -- `tender_refund_amount`
    would read 100.0 instead of 20.0, and `expected_cash` would go NEGATIVE
    (opening 0 + cash_sales 20 - cash_refunds 100 = -80) exactly as in the
    bug report, while `refund_amount` (unchanged) would still read 100.0.
    """
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=0.0, stock=10)
    # balance_due > 0 requires a named customer regardless of payment_method
    # (create_sale's own credit-sale rule) even though this sale is tendered
    # in cash, not 'credit' -- see is_credit's own `(pm=='credit') or
    # (balance_due>0.005)` definition.
    customer_id = _make_credit_customer(cid)
    session_id = _open_cash_session(client, opening_float=0.0)

    sale = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'customer_id': customer_id, 'amount_paid': 20.0, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    }).get_json()['data']
    assert sale['total'] == 100.0
    assert sale['balance_due'] == 80.0
    assert _credit_balance(cid, customer_id) == 80.0

    # `refund_method` OMITTED, matching the task's own reproduction and the
    # POS UI's pre-fix default.
    r = client.post('/api/sub/retail/returns', json={
        'sale_id': sale['id'],
        'items': [{'product_id': pid, 'quantity': 1}],
        'reason': 'full return, method omitted',
        'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['refund_amount'] == 100.0
    assert d['tender_refund_amount'] == 20.0, \
        f"cash payout must be capped at the $20 actually collected, got {d['tender_refund_amount']}"
    assert d['ar_forgiven_amount'] == 80.0, \
        f"the unpaid $80 must be forgiven as AR, got {d['ar_forgiven_amount']}"
    assert d['store_credit_amount'] == 0.0
    assert d['refund_method'] == 'cash'  # the sale's own method, defaulted correctly

    assert _credit_balance(cid, customer_id) == 0.0

    report = _x_report(client, session_id)
    assert report['expected_cash'] >= 0.0, \
        f"expected_cash must never go negative, got {report['expected_cash']}"
    assert report['expected_cash'] == report['opening_float'] + report['cash_sales'] - 20.0, (
        f"the drawer must move by only the $20 actually collected, not the full $100 refund. "
        f"report={report}"
    )
    assert report['cash_refunds'] == 20.0


def test_change_given_on_walk_in_sale_is_never_refundable():
    """A walk-in tenders MORE than the total and gets change back -- the
    refundable tender pool must be capped at what actually stayed in the
    drawer (`payments.amount`, i.e. net_received), never at the raw
    `amount_paid` figure, which includes the customer's own change.

    Mutation that must turn this red: compute `tender_collected` from
    `sales.amount_paid` instead of summing the sale's own `payments` rows
    -- `tender_refund_amount` would read 50.0 (the raw tender) instead of
    42.0 (what actually stayed in the drawer), handing the customer's own
    change back to them a second time as if it were shop money.
    """
    client, cid, pid, bid = _make_admin_and_product(price=42.0, tax_rate=0.0, stock=10)
    session_id = _open_cash_session(client, opening_float=0.0)

    sale = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'amount_paid': 50.0, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    }).get_json()['data']
    assert sale['total'] == 42.0
    assert sale['change'] == 8.0
    # Overpayment, not clamped at 0 (only `paid`/`change` are) -- negative
    # `balance_due` means "overpaid", and `is_credit`'s own
    # `balance_due > 0.005` check correctly reads this as NOT a credit sale.
    assert sale['balance_due'] == -8.0

    r = _return(client, sale['id'], pid, quantity=1, reason='overpaid walk-in return')
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['refund_amount'] == 42.0
    assert d['tender_refund_amount'] == 42.0, \
        f"must never refund the customer's own change (50.0), got {d['tender_refund_amount']}"

    report = _x_report(client, session_id)
    assert report['cash_sales'] == 42.0  # net_received, not the raw 50.0 tendered
    assert report['cash_refunds'] == 42.0
    assert report['expected_cash'] == 0.0


def test_second_partial_return_only_draws_remaining_tender_pool():
    """Two partial returns against the SAME sale must not each draw the
    full tender pool -- the second return can only draw what the FIRST
    left behind, both in the tender bucket and the AR bucket.

    Mutation that must turn this red: compute `tender_available` from the
    sale's raw tender collected WITHOUT subtracting prior returns'
    `tender_refund_amount` (i.e. drop the `_prior` query/subtraction in
    create_return) -- the second return would wrongly draw ANOTHER 20.0 of
    tender (40.0 total across both returns, overpaying cash by 20.0),
    instead of the correct 0.0.
    """
    client, cid, pid, bid = _make_admin_and_product(price=50.0, tax_rate=0.0, stock=10)
    rconn = get_retail_conn()
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) "
        "VALUES (?,?,'RT-9','Second Line',5,50,0)",
        (str(uuid.uuid4()), cid),
    )
    pid2 = rconn.execute("SELECT id FROM products WHERE company_id=? AND sku='RT-9'", (cid,)).fetchone()[0]
    rconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,10)",
        (cid, pid2, bid),
    )
    rconn.commit()
    rconn.close()

    customer_id = _make_credit_customer(cid)
    session_id = _open_cash_session(client, opening_float=0.0)

    # Total 100 (two 50-unit lines), $20 paid cash, $80 on credit.
    sale = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}, {'product_id': pid2, 'quantity': 1}],
        'customer_id': customer_id, 'amount_paid': 20.0, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    }).get_json()['data']
    assert sale['total'] == 100.0
    assert sale['balance_due'] == 80.0

    r1 = _return(client, sale['id'], pid, quantity=1, reason='partial 1')
    assert r1.status_code == 200, r1.get_json()
    d1 = r1.get_json()['data']
    assert d1['refund_amount'] == 50.0
    assert d1['tender_refund_amount'] == 20.0, f"first return should drain the whole $20 pool, got {d1}"
    assert d1['ar_forgiven_amount'] == 30.0, f"first return should forgive the remaining $30, got {d1}"
    assert _credit_balance(cid, customer_id) == 50.0

    r2 = _return(client, sale['id'], pid2, quantity=1, reason='partial 2')
    assert r2.status_code == 200, r2.get_json()
    d2 = r2.get_json()['data']
    assert d2['refund_amount'] == 50.0
    assert d2['tender_refund_amount'] == 0.0, \
        f"the tender pool was already drained by the first return, got {d2}"
    assert d2['ar_forgiven_amount'] == 50.0, f"second return should forgive the full remaining $50, got {d2}"
    assert _credit_balance(cid, customer_id) == 0.0

    report = _x_report(client, session_id)
    assert report['cash_refunds'] == 20.0, \
        f"across both returns combined, cash outflow must total exactly the $20 collected, got {report['cash_refunds']}"
    assert report['expected_cash'] == 0.0
