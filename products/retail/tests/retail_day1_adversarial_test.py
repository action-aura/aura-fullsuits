"""Adversarial verifier for DAY-1a..1d (job f8252b48). Each test here is a gap
the fixes leave open; each is expected RED against 84737b09.

Run (repo venv, every AURA_* env var removed):
    pytest products/retail/tests/retail_day1_adversarial_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_day1adv_"))
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
from core.retail import general_ledger as gl  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    email = f'day1adv-{uuid.uuid4().hex[:8]}@test.local'
    password = 'Day1PW12345'  # pragma: allowlist secret
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
        'name': 'Adv Widget', 'sku': f'ADV-{uuid.uuid4().hex[:8]}', 'cost_price': price / 2,
        'sell_price': price, 'tax_rate': tax_rate, 'initial_stock': 500})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _customer(c):
    r = c.post(f'{API}/customers', json={'name': f'Adv Customer {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    cid = r.get_json()['data']['id']
    conn = get_retail_conn()
    conn.execute("UPDATE customers SET credit_mode='unlimited', credit_limit=1000000 WHERE id=?", (cid,))
    conn.commit(); conn.close()
    return cid


def _sale(c, items, **extra):
    body = dict({'items': items, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4())}, **extra)
    r = c.post(f'{API}/sales', json=body)
    return r


def _return(c, sale_id, items, method='cash'):
    r = c.post(f'{API}/returns', json={'sale_id': sale_id, 'items': items,
                                       'refund_method': method, 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _tb(c):
    r = c.get(f'{API}/reports/trial-balance')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _acct(data, code):
    rows = [a for a in data['accounts'] if a['code'] == code]
    return rows[0]['balance'] if rows else 0.0


def _tax_report(c):
    r = c.get(f'{API}/reports/summary?days=1')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['tax_collected']


# ── DAY-1d ────────────────────────────────────────────────────────────────────

def test_returning_the_only_taxed_line_of_a_mixed_rate_sale_reverses_all_its_tax(client):
    """0% line 100 + 16% line 100 (=116). Sale tax 16. The 16% line comes back:
    tax still owed must be 0. The blended ratio (16/216) reverses only 8.593.
    create_return already holds the exact line tax (resolved_items[*]['tax_amount'],
    computed from the sale line's own snapshotted sale_items.tax_rate)."""
    p0 = _product(client, 100.0, tax_rate=0)
    p16 = _product(client, 100.0, tax_rate=16)
    s = _sale(client, [{'product_id': p0, 'quantity': 1}, {'product_id': p16, 'quantity': 1}])
    assert s.status_code == 200, s.get_json()
    sale = s.get_json()['data']
    assert sale['tax_amount'] == 16.0 and sale['total'] == 216.0, sale
    _return(client, sale['id'], [{'product_id': p16, 'quantity': 1}])
    tb = _tb(client)
    assert tb['is_balanced'] is True
    assert abs(_acct(tb, gl.ACCOUNT_TAX_PAYABLE)) == 0.0, (
        f"only the 0% item is still sold, nothing is owed; GL Tax Payable = {_acct(tb, gl.ACCOUNT_TAX_PAYABLE)}")


def test_returning_the_untaxed_line_of_a_mixed_rate_sale_reverses_no_tax(client):
    """Mirror: the 0% line comes back. The 16 of tax on the kept 16% line is
    still owed; the blended ratio reverses 7.407 of it."""
    p0 = _product(client, 100.0, tax_rate=0)
    p16 = _product(client, 100.0, tax_rate=16)
    sale = _sale(client, [{'product_id': p0, 'quantity': 1}, {'product_id': p16, 'quantity': 1}]).get_json()['data']
    _return(client, sale['id'], [{'product_id': p0, 'quantity': 1}])
    tb = _tb(client)
    assert abs(_acct(tb, gl.ACCOUNT_TAX_PAYABLE)) == 16.0, _acct(tb, gl.ACCOUNT_TAX_PAYABLE)


def test_points_paid_sale_returned_gl_tax_equals_the_tax_report(client):
    """116 sale (tax 16), 16 paid in loyalty points, 100 cash. Full return.
    settled (100) < refund (116), so post_return reverses 100*16/116 = 13.793
    while metrics.refunded_tax reverses 116*16/116 = 16. Books != tax report."""
    cust = _customer(client)
    r = client.post(f'{API}/settings/credit', json={'loyalty_point_value': 1})
    assert r.status_code == 200, r.get_json()
    conn = get_retail_conn()
    conn.execute("INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type,created_by) "
                 "VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), client.test_company_id, cust, 16, 'adjust', 'adv'))
    conn.commit(); conn.close()
    pid = _product(client, 100.0, tax_rate=16)
    s = _sale(client, [{'product_id': pid, 'quantity': 1}], customer_id=cust, points_redeemed=16)
    assert s.status_code == 200, s.get_json()
    sale = s.get_json()['data']
    assert sale['total'] == 116.0 and sale['tax_amount'] == 16.0, sale
    _return(client, sale['id'], [{'product_id': pid, 'quantity': 1}])
    tb = _tb(client)
    assert tb['is_balanced'] is True
    assert abs(_acct(tb, gl.ACCOUNT_TAX_PAYABLE)) == _tax_report(client), (
        f"GL Tax Payable {_acct(tb, gl.ACCOUNT_TAX_PAYABLE)} vs tax report {_tax_report(client)}")


def test_ten_single_unit_returns_never_reverse_more_tax_than_the_sale_collected(client):
    """JOD, 10 x 0.105 @16%: line tax 0.168, total 1.218. Returned one unit at
    a time, each return's tax_part rounds 0.0168 -> 0.017, so the books
    reverse 0.170 of a 0.168 tax, and GL Tax Payable ends on a DEBIT balance
    (-0.002) while the tax report says 0.000."""
    pid = _product(client, 0.105, tax_rate=16)
    s = _sale(client, [{'product_id': pid, 'quantity': 10}])
    assert s.status_code == 200, s.get_json()
    sale = s.get_json()['data']
    assert sale['tax_amount'] == 0.168, sale
    for _ in range(10):
        _return(client, sale['id'], [{'product_id': pid, 'quantity': 1}])
    conn = get_retail_conn()
    try:
        reversed_tax = conn.execute(
            "SELECT COALESCE(SUM(l.debit),0) FROM gl_journal_lines l JOIN gl_journals j ON j.id=l.journal_id "
            "JOIN gl_accounts a ON a.id=l.account_id "
            "WHERE j.company_id=? AND j.source_type='return' AND a.code=?",
            (client.test_company_id, gl.ACCOUNT_TAX_PAYABLE)).fetchone()[0]
    finally:
        conn.close()
    tb = _tb(client)
    assert tb['is_balanced'] is True
    assert round(reversed_tax, 3) <= 0.168, (
        f'reversed {reversed_tax} of a 0.168 tax; GL Tax Payable {_acct(tb, gl.ACCOUNT_TAX_PAYABLE)}, '
        f'tax report {_tax_report(client)}')
    assert _acct(tb, gl.ACCOUNT_TAX_PAYABLE) == _tax_report(client), (
        f"GL {_acct(tb, gl.ACCOUNT_TAX_PAYABLE)} vs report {_tax_report(client)}")


# ── DAY-1a ────────────────────────────────────────────────────────────────────

def test_a_credit_sale_fully_covered_by_points_is_not_refused(client):
    """Customer picks 'on account', points cover the whole 100. Nothing is
    owed, nothing is cash -- the pre-fix server recorded exactly that. The
    DAY-1a guard now 400s it with a message ('the amount paid covers the
    whole sale') that is false: amount_paid is 0."""
    cust = _customer(client)
    client.post(f'{API}/settings/credit', json={'loyalty_point_value': 1})
    conn = get_retail_conn()
    conn.execute("INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type,created_by) "
                 "VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), client.test_company_id, cust, 500, 'adjust', 'adv'))
    conn.commit(); conn.close()
    pid = _product(client, 100.0, tax_rate=0)
    r = _sale(client, [{'product_id': pid, 'quantity': 1}], customer_id=cust,
              payment_method='credit', amount_paid=0, points_redeemed=100)
    assert r.status_code == 200, r.get_json()


# ── DAY-1c ────────────────────────────────────────────────────────────────────

def test_voiding_a_customer_cash_payment_after_close_does_not_rewrite_the_closed_drawer(client):
    """Stamped customer cash is VOIDABLE (/payments/<id>/void allows a
    customer receipt); a sale's payment is not. So after DAY-1c a closed
    drawer's recomputed X report can move under its stored count."""
    pid = _product(client, 100.0)
    cust = _customer(client)
    assert _sale(client, [{'product_id': pid, 'quantity': 1}], customer_id=cust,
                 payment_method='credit', amount_paid=0).status_code == 200
    r = client.post(f'{API}/cash-sessions/open', json={'opening_float': 100.0})
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()['data']['id']
    assert client.post(f'{API}/customers/{cust}/payments', json={'amount': 20.0, 'method': 'cash'}).status_code == 200
    r = client.post(f'{API}/cash-sessions/{sid}/close', json={'closing_float_counted': 120.0})
    assert r.status_code == 200, r.get_json()
    stored_expected = r.get_json()['data']['session']['closing_float_expected']
    assert stored_expected == 120.0
    conn = get_retail_conn()
    pay_id = conn.execute("SELECT id FROM payments WHERE cash_session_id=?", (sid,)).fetchone()[0]
    conn.close()
    v = client.post(f'{API}/payments/{pay_id}/void', json={'reason': 'wrong customer'})
    # DAY-1 review F3: the reviewer's original line asserted the void SUCCEEDS
    # (200) and then that the report did not move -- which pins the old,
    # unfixed contract. The chosen fix refuses the void (409
    # PAYMENT_IN_CLOSED_DRAWER), so the precondition is updated; the final
    # assertion (a closed drawer's report == its stored count) is unchanged
    # and is the one that proves the drawer was not rewritten.
    assert v.status_code == 409 and v.get_json().get('code') == 'PAYMENT_IN_CLOSED_DRAWER', v.get_json()
    rep = client.get(f'{API}/cash-sessions/{sid}/x-report').get_json()['data']
    assert rep['expected_cash'] == stored_expected, (
        f"closed drawer's report now says {rep['expected_cash']}, its stored count said {stored_expected}")


def test_customer_cash_is_never_stamped_with_another_terminals_open_drawer(client):
    """GUARD GAP, not a product defect: the builder's suite stays green when
    _this_terminal_open_session_id's terminal predicate is deleted (measured:
    5 passed under that mutation). This test pins it: another till's drawer
    is open in the same company, THIS till has none -> the payment is in no
    drawer. Expected GREEN on 84737b09; RED when the predicate is dropped."""
    pid = _product(client, 100.0)
    cust = _customer(client)
    assert _sale(client, [{'product_id': pid, 'quantity': 1}], customer_id=cust,
                 payment_method='credit', amount_paid=0).status_code == 200
    other = str(uuid.uuid4())
    conn = get_retail_conn()
    bid = conn.execute("SELECT branch_id FROM sales WHERE company_id=? LIMIT 1",
                       (client.test_company_id,)).fetchone()[0]
    conn.execute("INSERT INTO cash_sessions (id,company_id,branch_id,opened_by,opened_at,opening_float,status,terminal_id) "
                 "VALUES (?,?,?,?,datetime('now','+1 hour'),0,'open',?)",
                 (other, client.test_company_id, bid, 'someone', 'OTHER-TERMINAL-' + other[:8]))
    conn.commit(); conn.close()
    assert client.post(f'{API}/customers/{cust}/payments', json={'amount': 20.0, 'method': 'cash'}).status_code == 200
    conn = get_retail_conn()
    try:
        stamped = conn.execute("SELECT cash_session_id FROM payments WHERE party_id=? AND related_type IS NULL",
                               (cust,)).fetchall()
    finally:
        conn.close()
    assert [r[0] for r in stamped] == [None], f'payment landed in another till\'s drawer: {list(map(tuple, stamped))}'


def test_allow_half_one_fil_owed_on_account_is_accepted_and_the_deposit_reaches_the_drawer(client):
    """ALLOW-HALF probe (expected GREEN): JOD 10.000 on account with a 9.999
    deposit leaves exactly one fil owed -- legitimate, must not be refused --
    and the 9.999 really handed over is counted in this drawer's cash_sales."""
    pid = _product(client, 10.0)
    cust = _customer(client)
    sid = client.post(f'{API}/cash-sessions/open', json={'opening_float': 0}).get_json()['data']['id']
    r = _sale(client, [{'product_id': pid, 'quantity': 1}], customer_id=cust,
              payment_method='credit', amount_paid=9.999)
    assert r.status_code == 200, r.get_json()
    conn = get_retail_conn()
    bal = conn.execute("SELECT credit_balance FROM customers WHERE id=?", (cust,)).fetchone()[0]
    conn.close()
    assert bal == 0.001, bal
    rep = client.get(f'{API}/cash-sessions/{sid}/x-report').get_json()['data']
    assert rep['cash_sales'] == 9.999 and rep['customer_cash_in'] == 0.0, rep


@pytest.mark.parametrize('shape', ['credit_ar_forgiven', 'discounted_partial'])
def test_probe_gl_tax_equals_report_on_ar_and_discount_paths(client, shape):
    """Probes expected GREEN: an on-account sale returned (AR-forgiven path),
    and a 10%-discounted 2-unit sale partially returned."""
    pid = _product(client, 100.0, tax_rate=16)
    if shape == 'credit_ar_forgiven':
        cust = _customer(client)
        s = _sale(client, [{'product_id': pid, 'quantity': 1}], customer_id=cust,
                  payment_method='credit', amount_paid=0)
        qty_back = 1
    else:
        s = _sale(client, [{'product_id': pid, 'quantity': 2, 'discount_pct': 10}])
        qty_back = 1
    assert s.status_code == 200, s.get_json()
    sale = s.get_json()['data']
    _return(client, sale['id'], [{'product_id': pid, 'quantity': qty_back}])
    tb = _tb(client)
    assert tb['is_balanced'] is True
    assert abs(_acct(tb, gl.ACCOUNT_TAX_PAYABLE)) == _tax_report(client), (sale, _acct(tb, gl.ACCOUNT_TAX_PAYABLE))
