"""Aura Retail -- MONEY-5 item 3 (review #9 part 1): ALLOW NEGATIVE STOCK, a setting.

A grocer's delivery is on the shelf but not typed in yet; the till refused the sale ("Insufficient
stock") and the cashier sold off the books. The owner can now set `allow_negative_stock` to '1'
(Settings; owner-only): the refusal in create_sale takes the SAME path `behind_on_sync` takes -- the
sale is allowed, flagged `oversold_past_recorded_stock`, and the stock exception row is written so
the owner can find and fix it. '0' (the default) keeps the hard refusal byte for byte.

DENY half  : default '0' refuses, no sale and no stock movement; a cashier cannot turn it on; a bad
             value is refused; switching it back to '0' refuses again.
ALLOW half : '1' sells past the recorded stock, the balance goes negative, the response says
             oversold, and a stock_exceptions row exists; a sale WITHIN stock is untouched.

MUTATION (quoted in the commit): ignore the setting in create_sale -> the allow test goes RED; make
the refusal unconditional-allow (drop the `not allow_negative_stock` guard's default) -> the deny tests
go RED.

Run (one file per process):
    pytest products/retail/tests/retail_money5_negative_stock_test.py -v
"""
import sys
import uuid
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import retail_accountant_regate_test as H  # noqa: E402  (builds the isolated app + data dir)

API = H.API


@pytest.fixture(autouse=True)
def _reset_device_cache(monkeypatch):
    H.device_context._cached_device = None
    import api.retail_api as _ra
    monkeypatch.setattr(_ra, '_sync_nudge', lambda *a, **k: None)
    yield
    H.device_context._cached_device = None


def _shop_with_stock(stock):
    admin, cid, _pid = H._new_shop()
    r = admin.post(f'{API}/products', json={
        'name': 'Short Stock Widget', 'sku': f'NS-{uuid.uuid4().hex[:8]}', 'cost_price': 1.0,
        'sell_price': 2.0, 'tax_rate': 0, 'initial_stock': stock})
    assert r.status_code == 200, r.get_json()
    return admin, cid, r.get_json()['data']['id']


def _sell(admin, pid, qty):
    return admin.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': qty}], 'payment_method': 'cash',
        'amount_paid': 1000, 'idempotency_key': str(uuid.uuid4())})


def _rows(sql, params=()):
    conn = H.get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _on_hand(cid, pid):
    return _rows("SELECT SUM(quantity_on_hand) AS q FROM inventory_balances WHERE company_id=? AND product_id=?",
                 (cid, pid))[0]['q']


def _set(admin, value):
    return admin.post(f'{API}/settings/credit', json={'allow_negative_stock': value})


def test_default_off_refuses_a_sale_past_the_recorded_stock_and_changes_nothing():
    admin, cid, pid = _shop_with_stock(2)
    assert admin.get(f'{API}/settings/credit').get_json()['data']['allow_negative_stock'] == '0'
    n_sales = len(_rows("SELECT id FROM sales WHERE company_id=?", (cid,)))
    r = _sell(admin, pid, 5)
    assert r.status_code == 400 and 'Insufficient stock' in r.get_json()['message'], r.get_json()
    assert _on_hand(cid, pid) == 2
    assert len(_rows("SELECT id FROM sales WHERE company_id=?", (cid,))) == n_sales


def test_on_allows_the_sale_flags_it_oversold_and_writes_the_stock_exception():
    admin, cid, pid = _shop_with_stock(2)
    assert _set(admin, '1').status_code == 200
    r = _sell(admin, pid, 5)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['oversold_past_recorded_stock'] is True
    assert _on_hand(cid, pid) == -3
    exc = _rows("SELECT * FROM stock_exceptions WHERE company_id=? AND product_id=?", (cid, pid))
    assert len(exc) == 1, exc


def test_a_sale_within_stock_is_untouched_by_the_setting():
    admin, cid, pid = _shop_with_stock(10)
    _set(admin, '1')
    r = _sell(admin, pid, 4)
    assert r.status_code == 200 and r.get_json()['data']['oversold_past_recorded_stock'] is False
    assert _on_hand(cid, pid) == 6
    assert _rows("SELECT * FROM stock_exceptions WHERE company_id=? AND product_id=?", (cid, pid)) == []


def test_switching_it_back_off_refuses_again():
    admin, cid, pid = _shop_with_stock(1)
    _set(admin, '1')
    assert _sell(admin, pid, 2).status_code == 200
    _set(admin, '0')
    r = _sell(admin, pid, 2)
    assert r.status_code == 400 and 'Insufficient stock' in r.get_json()['message']


def test_a_bad_value_is_refused_and_nothing_is_stored():
    admin, cid, pid = _shop_with_stock(1)
    r = _set(admin, 'maybe')
    assert r.status_code == 400 and r.get_json()['code'] == 'ALLOW_NEGATIVE_STOCK_INVALID', r.get_json()
    assert admin.get(f'{API}/settings/credit').get_json()['data']['allow_negative_stock'] == '0'


def test_a_cashier_cannot_turn_it_on():
    admin, cid, pid = _shop_with_stock(1)
    cashier, _uid = H._make_user('cashier', cid)
    r = _set(cashier, '1')
    assert r.status_code in (401, 403), (r.status_code, r.get_json())
    assert admin.get(f'{API}/settings/credit').get_json()['data']['allow_negative_stock'] == '0'
    assert _sell(admin, pid, 5).status_code == 400
