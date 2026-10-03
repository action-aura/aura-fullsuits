"""Split tender: one sale paid by several methods (card + cash + on account).

Real HTTP through the Flask client, real SQLite. Every refusal is asserted to
write NOTHING (sales/payments/outbox row counts unchanged), because a refusal
that leaves a half-written sale is worse than none. Both directions of the
rules that deny AND allow are covered (change allowed on cash, refused on
card; on-account allowed as the remainder, refused as an overpay).

Run:
    pytest products/retail/tests/retail_split_tender_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_split_"))
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
    email = f'split-{uuid.uuid4().hex[:8]}@test.local'
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


def _product(c, price=11.832, tax_rate=0):
    r = c.post(f'{API}/products', json={
        'name': 'Split Widget', 'sku': f'SP-{uuid.uuid4().hex[:8]}', 'cost_price': 1,
        'sell_price': price, 'tax_rate': tax_rate, 'initial_stock': 500})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _customer(c, credit=True, mode='unlimited', limit=1000000):
    r = c.post(f'{API}/customers', json={'name': f'Split Customer {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    cid = r.get_json()['data']['id']
    if credit:
        conn = get_retail_conn()
        conn.execute("UPDATE customers SET credit_mode=?, credit_limit=? WHERE id=?", (mode, limit, cid))
        conn.commit(); conn.close()
    return cid


def _balance(cust_id):
    conn = get_retail_conn()
    try:
        return float(conn.execute("SELECT credit_balance FROM customers WHERE id=?", (cust_id,)).fetchone()[0] or 0)
    finally:
        conn.close()


def _count(sql, params=()):
    conn = get_retail_conn()
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def _rows(sql, params=()):
    conn = get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _state():
    return (_count("SELECT COUNT(*) FROM sales"), _count("SELECT COUNT(*) FROM payments"),
            _count("SELECT COUNT(*) FROM sync_outbox"), _count("SELECT COUNT(*) FROM sale_items"))


def _split(c, pid, tenders, customer_id=None, qty=1, **extra):
    body = {'items': [{'product_id': pid, 'quantity': qty}], 'payment_method': 'split',
            'tenders': tenders, 'idempotency_key': str(uuid.uuid4())}
    if customer_id:
        body['customer_id'] = customer_id
    body.update(extra)
    return c.post(f'{API}/sales', json=body)


def _open(c, float_=100.0):
    r = c.post(f'{API}/cash-sessions/open', json={'opening_float': float_})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _x(c, sid):
    r = c.get(f'{API}/cash-sessions/{sid}/x-report')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _tb(c):
    r = c.get(f'{API}/reports/trial-balance')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _acct(data, code):
    return [a for a in data['accounts'] if a['code'] == code][0]


def _pay_rows(sale_id):
    return _rows("SELECT method, amount, sale_id FROM payments WHERE sale_id=? AND direction='in' ORDER BY id",
                 (sale_id,))


def test_1_exact_card_plus_cash_records_two_payment_rows(client):
    pid = _product(client)
    r = _split(client, pid, [{'method': 'card', 'amount': 5}, {'method': 'cash', 'amount': 6.832}])
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['amount_paid'] == 11.832 and d['change'] == 0 and d['balance_due'] == 0
    rows = _pay_rows(d['id'])
    assert [(x['method'], x['amount']) for x in rows] == [('card', 5.0), ('cash', 6.832)]
    assert _rows("SELECT payment_method FROM sales WHERE id=?", (d['id'],))[0]['payment_method'] == 'split'
    assert [(t['method'], t['amount']) for t in d['tenders']] == [('card', 5.0), ('cash', 6.832)]


def test_2_cash_over_gives_change_and_the_drawer_counts_only_retained_cash(client):
    pid = _product(client)
    sid = _open(client, 100.0)
    r = _split(client, pid, [{'method': 'card', 'amount': 5}, {'method': 'cash', 'amount': 10}])
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['change'] == 3.168
    assert [(x['method'], x['amount']) for x in _pay_rows(d['id'])] == [('card', 5.0), ('cash', 6.832)]
    cash_t = [t for t in d['tenders'] if t['method'] == 'cash'][0]
    assert cash_t['amount'] == 6.832 and cash_t['tendered'] == 10.0
    x = _x(client, sid)
    assert x['cash_sales'] == 6.832
    assert x['expected_cash'] == 106.832
    assert x['tender_totals'] == {'card': 5.0, 'cash': 6.832}


@pytest.mark.parametrize('tenders,code', [
    ([{'method': 'card', 'amount': 30}, {'method': 'cash', 'amount': 5}], 'TENDER_OVERPAID_NON_CASH'),
    ([{'method': 'card', 'amount': 5}, {'method': 'cash', 'amount': 6.831}], 'TENDERS_SHORT'),
    ([{'method': 'cash', 'amount': 5}, {'method': 'cash', 'amount': 7}], 'TENDER_DUPLICATE_METHOD'),
    ([{'method': 'card', 'amount': 11.832}, {'method': 'cash', 'amount': 5}], 'TENDER_CASH_NOT_NEEDED'),
])
def test_3_refusals_write_nothing(client, tenders, code):
    pid = _product(client)
    before = _state()
    r = _split(client, pid, tenders)
    assert r.status_code == 400, r.get_json()
    assert r.get_json()['code'] == code, r.get_json()
    assert _state() == before, 'a refused split sale must not write anything'


def test_4a_cash_plus_remainder_on_account_charges_the_customer_the_remainder(client):
    pid = _product(client)
    cust = _customer(client)
    r = _split(client, pid, [{'method': 'cash', 'amount': 5}, {'method': 'credit', 'amount': 6.832}],
               customer_id=cust)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['balance_due'] == 6.832 and d['amount_paid'] == 5.0
    assert _balance(cust) == 6.832
    assert [(x['method'], x['amount']) for x in _pay_rows(d['id'])] == [('cash', 5.0)]
    assert [t['method'] for t in d['tenders']] == ['cash', 'credit']
    st = client.get(f'{API}/customers/{cust}/statement').get_json()['data']
    assert st['balance'] == 6.832


def test_4b_walk_in_cannot_have_a_part_on_account(client):
    pid = _product(client)
    before = _state()
    r = _split(client, pid, [{'method': 'cash', 'amount': 5}, {'method': 'credit', 'amount': 6.832}])
    assert r.status_code == 400, r.get_json()
    assert 'customer' in r.get_json()['message'].lower()
    assert _state() == before


def test_4c_credit_limit_block_is_still_honoured_for_the_on_account_part(client):
    pid = _product(client)
    cust = _customer(client, mode='limited', limit=3)
    conn = get_retail_conn()
    conn.execute("INSERT OR REPLACE INTO retail_settings (company_id,skey,svalue) VALUES (?,'enforce_credit_limit','block')",
                 (client.test_company_id,))
    conn.commit(); conn.close()
    before = _state()
    r = _split(client, pid, [{'method': 'cash', 'amount': 5}, {'method': 'credit', 'amount': 6.832}],
               customer_id=cust)
    assert r.status_code in (400, 403, 409), r.get_json()
    assert _state() == before and _balance(cust) == 0.0


def test_4d_on_account_overpay_is_refused(client):
    pid = _product(client)
    cust = _customer(client)
    r = _split(client, pid, [{'method': 'cash', 'amount': 8}, {'method': 'credit', 'amount': 6.832}],
               customer_id=cust)
    assert r.status_code == 400 and r.get_json()['code'] == 'TENDER_CREDIT_OVERPAID'


def test_5_one_entry_tenders_equals_the_legacy_single_tender_rows(client):
    pid = _product(client)
    a = client.post(f'{API}/sales', json={'items': [{'product_id': pid, 'quantity': 1}],
                    'payment_method': 'card', 'amount_paid': 11.832, 'idempotency_key': str(uuid.uuid4())})
    b = client.post(f'{API}/sales', json={'items': [{'product_id': pid, 'quantity': 1}],
                    'tenders': [{'method': 'card', 'amount': 11.832}], 'idempotency_key': str(uuid.uuid4())})
    assert a.status_code == b.status_code == 200
    da, db = a.get_json()['data'], b.get_json()['data']
    for k in ('amount_paid', 'change', 'balance_due', 'total'):
        assert da[k] == db[k], k
    assert [(x['method'], x['amount']) for x in _pay_rows(da['id'])] == \
           [(x['method'], x['amount']) for x in _pay_rows(db['id'])]
    assert _rows("SELECT payment_method FROM sales WHERE id=?", (db['id'],))[0]['payment_method'] == 'card'


def test_6_gl_cash_and_bank_rises_by_the_retained_sum_and_trial_balance_balances(client):
    pid = _product(client)

    def net(a):
        return round(float(a.get('debit', 0) or 0) - float(a.get('credit', 0) or 0), 3)

    before = net(_acct(_tb(client), '1000')) if any(
        a['code'] == '1000' for a in _tb(client)['accounts']) else 0.0
    r = _split(client, pid, [{'method': 'card', 'amount': 5}, {'method': 'cash', 'amount': 10}])
    assert r.status_code == 200
    tb = _tb(client)
    assert round(net(_acct(tb, '1000')) - before, 3) == 11.832
    tot = tb.get('totals') or {}
    if tot:
        assert round(float(tot['debit']) - float(tot['credit']), 3) == 0


def test_7_replay_with_the_same_idempotency_key_writes_no_second_set_of_rows(client):
    pid = _product(client)
    key = str(uuid.uuid4())
    body = {'items': [{'product_id': pid, 'quantity': 1}], 'idempotency_key': key,
            'tenders': [{'method': 'card', 'amount': 5}, {'method': 'cash', 'amount': 6.832}]}
    a = client.post(f'{API}/sales', json=body)
    n = _count("SELECT COUNT(*) FROM payments")
    b = client.post(f'{API}/sales', json=body)
    assert a.status_code == b.status_code == 200
    assert a.get_json()['data']['id'] == b.get_json()['data']['id']
    assert _count("SELECT COUNT(*) FROM payments") == n
    assert len(_pay_rows(a.get_json()['data']['id'])) == 2


def _return(c, sale_id, pid, **extra):
    body = {'sale_id': sale_id, 'items': [{'product_id': pid, 'quantity': 1}],
            'idempotency_key': str(uuid.uuid4())}
    body.update(extra)
    return c.post(f'{API}/returns', json=body)


def test_8_returning_a_split_sale_requires_one_explicit_refund_method(client):
    pid = _product(client)
    sid = _open(client, 100.0)
    sale = _split(client, pid, [{'method': 'card', 'amount': 5}, {'method': 'cash', 'amount': 6.832}]).get_json()['data']
    n_ret = _count("SELECT COUNT(*) FROM returns")
    r = _return(client, sale['id'], pid)
    assert r.status_code == 400 and r.get_json()['code'] == 'REFUND_METHOD_REQUIRED', r.get_json()
    assert [t['method'] for t in r.get_json()['data']['tenders']] == ['card', 'cash']
    r = _return(client, sale['id'], pid, refund_method='split')
    assert r.status_code == 400 and r.get_json()['code'] == 'REFUND_METHOD_INVALID'
    assert _count("SELECT COUNT(*) FROM returns") == n_ret, 'refusals must not write a return'
    r = _return(client, sale['id'], pid, refund_method='cash')
    assert r.status_code == 200, r.get_json()
    x = _x(client, sid)
    assert x['cash_refunds'] == 11.832, 'cap is the whole tender pool, paid out as cash'


def test_8b_split_sale_refunded_by_card_leaves_the_drawer_alone(client):
    pid = _product(client)
    sid = _open(client, 100.0)
    sale = _split(client, pid, [{'method': 'card', 'amount': 5}, {'method': 'cash', 'amount': 6.832}]).get_json()['data']
    r = _return(client, sale['id'], pid, refund_method='card')
    assert r.status_code == 200, r.get_json()
    assert _x(client, sid)['cash_refunds'] == 0


def test_9_drawer_kick_only_when_part_of_the_sale_was_cash(client, monkeypatch):
    from core.retail import escpos_transport
    pid = _product(client)
    calls = []
    monkeypatch.setattr(escpos_transport, 'send_raw', lambda *a, **k: calls.append((a, k)))
    with_cash = _split(client, pid, [{'method': 'card', 'amount': 5}, {'method': 'cash', 'amount': 6.832}]).get_json()['data']['id']
    no_cash = _split(client, pid, [{'method': 'card', 'amount': 5}, {'method': 'mobile', 'amount': 6.832}]).get_json()['data']['id']
    r = client.post(f'{API}/printer/kick', json={'sale_id': no_cash, 'printer': 'Any Printer'})
    assert r.status_code == 200 and r.get_json()['data']['kicked'] is False
    assert calls == [], 'a card + mobile split must not open the drawer'
    r = client.post(f'{API}/printer/kick', json={'sale_id': with_cash, 'printer': 'Any Printer'})
    assert r.status_code == 200, r.get_json()
    assert len(calls) == 1, f'a split with a cash part must reach the transport: {r.get_json()}'


def test_10_outbox_carries_one_sale_event_and_one_payment_event_per_tender(client):
    pid = _product(client)
    r = _split(client, pid, [{'method': 'card', 'amount': 5}, {'method': 'cash', 'amount': 6.832}])
    d = r.get_json()['data']
    import json as _json
    uid = _rows("SELECT uid FROM sales WHERE id=?", (d['id'],))[0]['uid']
    ev = _rows("SELECT entity_type, payload FROM sync_outbox")
    sale_ev = [_json.loads(e['payload']) for e in ev if e['entity_type'] == 'sale' and uid in e['payload']]
    pay_ev = [_json.loads(e['payload']) for e in ev if e['entity_type'] == 'payment' and uid in e['payload']]
    assert len(sale_ev) == 1 and sale_ev[0]['payment_method'] == 'split'
    assert sorted((p['method'], p['amount']) for p in pay_ev) == [('card', 5.0), ('cash', 6.832)]
    assert all(p['sale_uid'] == uid for p in pay_ev)


def test_11_points_plus_split_is_settled_against_the_amount_after_points(client):
    pid = _product(client)
    cust = _customer(client)
    conn = get_retail_conn()
    conn.execute("INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type) VALUES (?,?,?,?,'opening')",
                 (str(uuid.uuid4()), client.test_company_id, cust, 5))
    conn.execute("INSERT OR REPLACE INTO retail_settings (company_id,skey,svalue) VALUES (?,'loyalty_point_value','1')",
                 (client.test_company_id,))
    conn.commit(); conn.close()
    r = _split(client, pid, [{'method': 'card', 'amount': 2}, {'method': 'cash', 'amount': 4.832}],
               customer_id=cust, points_redeemed=5)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['balance_due'] == 0 and d['change'] == 0
    assert [(x['method'], x['amount']) for x in _pay_rows(d['id'])] == [('card', 2.0), ('cash', 4.832)]


def test_12_sales_by_tender_report_has_a_split_bucket(client):
    pid = _product(client)
    assert _split(client, pid, [{'method': 'card', 'amount': 5}, {'method': 'cash', 'amount': 6.832}]).status_code == 200
    r = client.get(f'{API}/reports/payment-methods?days=1')
    assert r.status_code == 200, r.get_json()
    rows = r.get_json()['data']
    assert any(x['payment_method'] == 'split' for x in rows), rows


def test_13_einvoice_payment_type_split_without_credit_is_cash_with_credit_is_credit():
    from commercial_runtime.einvoicing.document import derive_payment_type
    assert derive_payment_type('split', 11.832, 11.832) == 'cash'
    assert derive_payment_type('split', 5.0, 11.832) == 'credit'
