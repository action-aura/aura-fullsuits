"""DAY-1d: a return gives back its share of the TAX in the books.

Before: post_return debited ALL of the settled refund to Sales Returns, but the
refund is tax-inclusive and the sale credited Tax Payable for the tax. GL Tax
Payable therefore stayed at the full collected tax while the tax report
(metrics.refunded_tax) netted the returns -- the two disagreed, and P&L revenue
was understated by the tax inside every refund. Now Dr Tax Payable (pro-rata) /
Dr Sales Returns (the rest) and the journal still balances.

Run:
    pytest products/retail/tests/retail_day1_return_tax_gl_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_day1d_"))
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
    email = f'day1d-{uuid.uuid4().hex[:8]}@test.local'
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
from core.retail import general_ledger as gl  # noqa: E402


def _tb(c):
    r = c.get(f'{API}/reports/trial-balance')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _acct(data, code):
    return [a for a in data['accounts'] if a['code'] == code][0]


def _tax_report(c):
    r = c.get(f'{API}/reports/summary?days=1')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['tax_collected']


def _return_one(c, sale, pid):
    ret = c.post(f'{API}/returns', json={
        'sale_id': sale['id'], 'items': [{'product_id': pid, 'quantity': 1}],
        'refund_method': 'cash', 'idempotency_key': str(uuid.uuid4())})
    assert ret.status_code == 200, ret.get_json()
    return ret.get_json()['data']


def test_gl_tax_payable_equals_the_tax_report_after_a_sale_and_a_partial_return(client):
    pid = _product(client, 100.0, tax_rate=16)
    s = _sale(client, pid, 'cash', None, qty=2)            # 2 x 100 + 16% = 232, tax 32
    assert s.status_code == 200, s.get_json()
    sale = s.get_json()['data']
    assert sale['tax_amount'] == 32.0, sale
    assert _return_one(client, sale, pid)['refund_amount'] == 116.0

    tb = _tb(client)
    assert tb['is_balanced'] is True
    tax_acct = _acct(tb, gl.ACCOUNT_TAX_PAYABLE)
    # 32 collected - 16 inside the 116 refund = 16 still owed to the authority.
    # SIGNED (DAY-1 review F4): Tax Payable is credit-normal, so a healthy
    # balance is +16.0. The old abs() would also pass a Tax Payable that had
    # flipped to a DEBIT of 16 (e.g. the return posting its tax on the wrong
    # side), which is a real defect this test must be able to see.
    assert tax_acct['balance'] == 16.0, tax_acct
    assert _tax_report(client) == 16.0
    assert tax_acct['balance'] == _tax_report(client), 'books and tax report must agree'
    assert _acct(tb, gl.ACCOUNT_SALES_REVENUE)['balance'] == 200.0, _acct(tb, gl.ACCOUNT_SALES_REVENUE)
    assert _acct(tb, gl.ACCOUNT_SALES_RETURNS)['balance'] == 100.0, \
        'returns booked net of tax (100), not the tax-inclusive 116'


def test_untaxed_return_books_the_whole_refund_to_sales_returns(client):
    pid = _product(client, 100.0, tax_rate=0)
    sale = _sale(client, pid, 'cash', None, qty=2).get_json()['data']
    _return_one(client, sale, pid)
    tb = _tb(client)
    assert tb['is_balanced'] is True
    assert _acct(tb, gl.ACCOUNT_SALES_RETURNS)['balance'] == 100.0
    assert abs(_acct(tb, gl.ACCOUNT_TAX_PAYABLE)['balance']) == 0.0


def _post_two(client, items):
    s = client.post(f'{API}/sales', json={'items': items, 'payment_method': 'cash',
                                           'idempotency_key': str(uuid.uuid4())})
    assert s.status_code == 200, s.get_json()
    return s.get_json()['data']


def _return_n(c, sale, pid, qty):
    ret = c.post(f'{API}/returns', json={
        'sale_id': sale['id'], 'items': [{'product_id': pid, 'quantity': qty}],
        'refund_method': 'cash', 'idempotency_key': str(uuid.uuid4())})
    assert ret.status_code == 200, ret.get_json()
    return ret.get_json()['data']


def test_mixed_rate_partial_returns_across_two_returns_gl_equals_report_and_stored_figure(client):
    """DAY-1 review F1: 2 x 0% (200) + 2 x 16% (232) = 432, tax 32. Return 1:
    one 16% unit (tax 16). Return 2: one 0% unit (tax 0). The books, the tax
    report and the STORED returns.tax_refunded must all say 16 reversed / 16
    still owed -- one figure, three readers."""
    p0 = _product(client, 100.0, tax_rate=0)
    p16 = _product(client, 100.0, tax_rate=16)
    sale = _post_two(client, [{'product_id': p0, 'quantity': 2}, {'product_id': p16, 'quantity': 2}])
    assert sale['tax_amount'] == 32.0, sale
    _return_n(client, sale, p16, 1)
    _return_n(client, sale, p0, 1)
    tb = _tb(client)
    assert tb['is_balanced'] is True
    assert _acct(tb, gl.ACCOUNT_TAX_PAYABLE)['balance'] == 16.0
    assert _tax_report(client) == 16.0
    stored = _count("SELECT COALESCE(SUM(tax_refunded),0) FROM returns WHERE sale_id=?", (sale['id'],))
    assert stored == 16.0, stored
    # per-return: 16 then 0 (not a blended 7.4 each)
    rows = [r[0] for r in get_retail_conn().execute(
        "SELECT tax_refunded FROM returns WHERE sale_id=? ORDER BY id", (sale['id'],)).fetchall()]
    assert rows == [16.0, 0.0], rows


def test_a_return_with_null_tax_refunded_falls_back_to_the_old_ratio_in_the_report(client):
    """A pre-v43 return (tax_refunded NULL) cannot be reconstructed: the report
    keeps the old refund * tax/total rule for exactly those rows. A MIXED-rate
    sale is used so the two rules give different answers (a single-rate sale
    would hide which one was read): 0% 100 + 16% 100 (tax 16, total 216), the
    16% line returned. Stored figure -> 0 still owed. NULL -> the ratio reverses
    only 116*16/216 = 8.593, so 7.41 is still reported owed."""
    p0 = _product(client, 100.0, tax_rate=0)
    p16 = _product(client, 100.0, tax_rate=16)
    sale = _post_two(client, [{'product_id': p0, 'quantity': 1}, {'product_id': p16, 'quantity': 1}])
    _return_n(client, sale, p16, 1)
    assert _tax_report(client) == 0.0, 'stored line-exact figure: all 16 reversed'
    conn = get_retail_conn()
    conn.execute("UPDATE returns SET tax_refunded=NULL WHERE sale_id=?", (sale['id'],))
    conn.commit(); conn.close()
    assert abs(_tax_report(client) - 7.41) < 0.011, _tax_report(client)


def test_returning_every_unit_one_at_a_time_strands_no_tax_from_rounding_down(client):
    """DAY-1 review F1, the OTHER rounding direction. 3 x 0.103 @16% (JOD, 3dp):
    the whole line carries tax 0.049, but one unit's tax rounds DOWN to 0.016,
    so three line-exact returns would reverse only 0.048 and strand 0.001 of
    tax payable on a sale that is fully returned. The return that completes the
    sale takes the exact remainder, so Tax Payable ends at exactly 0 and the
    report agrees. (The over-reversal direction is the reviewer's ten-unit test.)"""
    pid = _product(client, 0.103, tax_rate=16)
    sale = _post_two(client, [{'product_id': pid, 'quantity': 3}])
    assert sale['tax_amount'] == 0.049, sale
    for _ in range(3):
        _return_n(client, sale, pid, 1)
    tb = _tb(client)
    assert tb['is_balanced'] is True
    assert _acct(tb, gl.ACCOUNT_TAX_PAYABLE)['balance'] == 0.0, _acct(tb, gl.ACCOUNT_TAX_PAYABLE)
    assert _tax_report(client) == 0.0
