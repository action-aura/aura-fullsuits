"""DAY-1c: cash a customer pays on account reaches the drawer (retail v43).

Before: the X/Z expected cash summed only related_type='sale' payments, so a
customer paying 20.000 off their account at the till put real notes in the
drawer that no figure counted. v43 stamps payments.cash_session_id at write
time (same terminal-scoped resolver as the sale path) and the report adds
`customer_cash_in`. Both directions: a payment taken with a drawer open is in
the drawer; a back-office payment with NO drawer open, and a card payment, are
not.

Run:
    pytest products/retail/tests/retail_day1_customer_cash_drawer_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_day1c_"))
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
    email = f'day1c-{uuid.uuid4().hex[:8]}@test.local'
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


def _owing_customer(c, pid):
    cust = _customer(c)
    assert _sale(c, pid, 'credit', 0, customer_id=cust).status_code == 200
    return cust


def _pay(c, cust, amount, method='cash'):
    return c.post(f'{API}/customers/{cust}/payments', json={'amount': amount, 'method': method})


def _open(c, float_=100.0):
    r = c.post(f'{API}/cash-sessions/open', json={'opening_float': float_})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _x(c, sid):
    r = c.get(f'{API}/cash-sessions/{sid}/x-report')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def test_customer_cash_payment_with_drawer_open_is_in_expected_cash(client):
    pid = _product(client, 100.0)
    cust = _owing_customer(client, pid)             # owes 100
    sid = _open(client, 100.0)
    r = _pay(client, cust, 20.0)
    assert r.status_code == 200, r.get_json()
    rep = _x(client, sid)
    assert rep['customer_cash_in'] == 20.0, rep
    assert rep['cash_sales'] == 0.0
    assert rep['expected_cash'] == 120.0, rep       # 100 float + 20 customer cash
    assert _count("SELECT COUNT(*) FROM payments WHERE cash_session_id=? AND method='cash'", (sid,)) == 1, \
        'the payment row itself must carry the session id'


def test_back_office_and_card_payments_are_not_in_the_drawer(client):
    pid = _product(client, 100.0)
    cust = _owing_customer(client, pid)
    # Back-office: NO drawer open when the cash arrives.
    assert _pay(client, cust, 10.0).status_code == 200
    assert _count("SELECT COUNT(*) FROM payments WHERE direction='in' AND method='cash' "
                  "AND cash_session_id IS NULL AND party_id=?", (cust,)) == 1
    sid = _open(client, 100.0)
    assert _pay(client, cust, 5.0, method='card').status_code == 200   # not cash
    # The STAMP rule itself, not only the report filter: a card payment is not
    # drawer cash, so it must carry no session id (otherwise a later change to
    # the report's method filter would silently pull it into the drawer).
    assert _count("SELECT COUNT(*) FROM payments WHERE method='card' AND party_id=? "
                  "AND cash_session_id IS NOT NULL", (cust,)) == 0
    rep = _x(client, sid)
    assert rep['customer_cash_in'] == 0.0, rep
    assert rep['expected_cash'] == 100.0, 'neither the earlier back-office cash nor a card payment belongs to this drawer'


def test_a_sales_own_cash_payment_is_not_stamped_as_customer_cash(client):
    """The sale's cash already reaches the drawer through sales.session_id /
    cash_sales. Stamping it here too would count it twice."""
    pid = _product(client, 100.0)
    sid = _open(client, 100.0)
    assert _sale(client, pid, 'cash', 100.0).status_code == 200
    assert _count("SELECT COUNT(*) FROM payments WHERE related_type='sale' "
                  "AND cash_session_id IS NOT NULL") == 0
    rep = _x(client, sid)
    assert rep['cash_sales'] == 100.0 and rep['customer_cash_in'] == 0.0
    assert rep['expected_cash'] == 200.0


def test_close_variance_is_zero_when_customer_cash_is_counted(client):
    pid = _product(client, 100.0)
    cust = _owing_customer(client, pid)
    sid = _open(client, 100.0)
    assert _pay(client, cust, 20.0).status_code == 200
    r = client.post(f'{API}/cash-sessions/{sid}/close', json={'closing_float_counted': 120.0})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['session']['variance'] == 0.0


def test_customer_cash_payment_is_not_synced_with_a_session_id():
    """cash_session_id is device-local: the sync payload for a `payment` event
    must not carry it, and the apply side's INSERT must not write it."""
    import inspect
    from commercial_runtime.sync import sync_service
    src = inspect.getsource(sync_service)
    assert 'cash_session_id' not in src, 'sync must neither emit nor apply the device-local drawer stamp'
    from api import retail_api
    rec = inspect.getsource(retail_api._record_payment)
    payload = rec[rec.index("_queue_sync_event(conn, 'payment'"):]
    assert 'cash_session_id' not in payload, 'the payment sync payload must not carry the session id'
