"""DAY-1a/1b: a sale ON ACCOUNT must leave an amount owed.

The old till posted payment_method='credit' with amount_paid = the whole
amount. create_sale computed balance_due = 0, so the customer was charged
NOTHING and the "paid" amount landed in `payments` as a cash deposit (feeds the
drawer's cash_sales). The server now refuses that shape with a 400 BEFORE any
write; the till (subsystem-retail.js) sends amount_paid 0 for credit. Both
directions are covered: the refused old payload, and the credit shapes that
must still work (fixed payload, partial deposit) plus cash untouched.

The client half is proven by retail_day1_credit_payload_test.js.

Run:
    pytest products/retail/tests/retail_day1_credit_sale_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_day1a_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA), AURA_SITE_RELAY_ENABLED="0")
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    email = f'day1a-{uuid.uuid4().hex[:8]}@test.local'
    password = 'Day1PW12345'
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


def _product(c, price=100.0, tax_rate=0):
    r = c.post(f'{API}/products', json={
        'name': 'Day1 Widget', 'sku': f'D1-{uuid.uuid4().hex[:8]}', 'cost_price': price / 2,
        'sell_price': price, 'tax_rate': tax_rate, 'initial_stock': 500})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _customer(c, credit=True):
    r = c.post(f'{API}/customers', json={'name': f'Day1 Customer {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    cid = r.get_json()['data']['id']
    if credit:
        conn = get_retail_conn()
        conn.execute("UPDATE customers SET credit_mode='unlimited', credit_limit=1000000 WHERE id=?", (cid,))
        conn.commit(); conn.close()
    return cid


def _balance(cust_id):
    conn = get_retail_conn()
    try:
        return float(conn.execute("SELECT credit_balance FROM customers WHERE id=?", (cust_id,)).fetchone()[0] or 0)
    finally:
        conn.close()


def _sale(c, pid, method, amount_paid, customer_id=None, qty=1):
    body = {'items': [{'product_id': pid, 'quantity': qty}], 'payment_method': method,
            'idempotency_key': str(uuid.uuid4())}
    if amount_paid is not None:
        body['amount_paid'] = amount_paid
    if customer_id:
        body['customer_id'] = customer_id
    return c.post(f'{API}/sales', json=body)


def _count(sql, params=()):
    conn = get_retail_conn()
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def test_old_till_payload_credit_with_full_amount_paid_is_refused_before_any_write(client):
    pid = _product(client, 100.0)
    cust = _customer(client)
    sales_before = _count("SELECT COUNT(*) FROM sales")
    pays_before = _count("SELECT COUNT(*) FROM payments")
    r = _sale(client, pid, 'credit', 100.0, customer_id=cust)   # EXACTLY what the old till sent
    assert r.status_code == 400, r.get_json()
    assert 'must leave an amount owed' in r.get_json()['message']
    assert _count("SELECT COUNT(*) FROM sales") == sales_before, 'a refused sale must not write a sales row'
    assert _count("SELECT COUNT(*) FROM payments") == pays_before, 'a refused sale must not record a payment'
    assert _balance(cust) == 0.0


def test_old_payload_overpaying_credit_is_also_refused(client):
    pid = _product(client, 100.0)
    cust = _customer(client)
    assert _sale(client, pid, 'credit', 150.0, customer_id=cust).status_code == 400
    assert _balance(cust) == 0.0


def test_fixed_till_payload_credit_charges_the_customer_and_takes_no_cash(client):
    pid = _product(client, 100.0)
    cust = _customer(client)
    pays_before = _count("SELECT COUNT(*) FROM payments WHERE method='cash' AND direction='in'")
    r = _sale(client, pid, 'credit', 0, customer_id=cust)       # what the FIXED till sends
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['balance_due'] == 100.0
    assert data['amount_paid'] == 0.0
    assert _balance(cust) == 100.0, 'the customer must actually be charged'
    assert _count("SELECT COUNT(*) FROM payments WHERE method='cash' AND direction='in'") == pays_before, \
        'a pure on-account sale must not record any cash received'


def test_credit_sale_with_a_real_partial_deposit_still_works(client):
    pid = _product(client, 100.0)
    cust = _customer(client)
    r = _sale(client, pid, 'credit', 30.0, customer_id=cust)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['balance_due'] == 70.0
    assert _balance(cust) == 70.0


def test_cash_sale_paying_in_full_is_unaffected(client):
    pid = _product(client, 100.0)
    r = _sale(client, pid, 'cash', 100.0)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['balance_due'] == 0.0
