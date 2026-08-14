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


def test_return_against_partially_paid_sale_reduces_ar_by_the_outstanding_portion_only():
    """A sale that was PARTLY paid in cash, partly on credit -- the return
    must only credit back the still-outstanding portion, never more than
    what was actually left unpaid (the cash portion isn't AR to begin
    with)."""
    client, cid, pid, bid = _make_admin_and_product(price=100.0, tax_rate=15.0, stock=10)
    customer_id = _make_credit_customer(cid)

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
    assert r.get_json()['data']['refund_amount'] == 115.0  # full refund figure, unrelated to AR crediting

    assert _credit_balance(cid, customer_id) == 0.0, \
        "must credit exactly the $50 that was actually owed, not the full $115 refund"


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
