"""Aura Retail -- MONEY-5 verifier-fix round (2026-10-13). One section per finding.

H1  CUMULATIVE RETURN ROUNDING. The cash handed back for a sale is rounded on the RUNNING TOTAL of
    cash refunded for that sale, not per return. Measured by the verifier before the fix: 50 x 0.010
    sold for 0.500 cash ('up', 0.050); ten single-unit returns (goods worth 0.100) had paid back ALL
    0.500. Same shape in-process with 'nearest': 20 of 40 units paid back the whole 1.000.
    DENY half  : a piece-by-piece return is never ahead of the goods by more than one coin interval,
                 and never exceeds the cash the sale took.
    ALLOW half : once everything is back the cash handed back is EXACTLY what the sale took, and
                 4950 nets the sale's own rounding to zero (a full return in parts loses nothing).
    MUTATION   : round each return on its own (the old code) -> RED.

Real routes, read back from the real database. One file per process:
    pytest products/retail/tests/retail_money5_verifier_fixes_test.py -v
"""
import random
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import retail_accountant_regate_test as H  # noqa: E402  (builds the isolated app + data dir)
from core.retail import cash_rounding as cr  # noqa: E402
from core.retail import general_ledger as gl  # noqa: E402

API = H.API
D = Decimal
Q = D('0.001')


@pytest.fixture(autouse=True)
def _reset_device_cache(monkeypatch):
    H.device_context._cached_device = None
    import api.retail_api as _ra
    monkeypatch.setattr(_ra, '_sync_nudge', lambda *a, **k: None)
    yield
    H.device_context._cached_device = None


def _rows(sql, params=()):
    conn = H.get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _settings(admin, **kv):
    r = admin.post(f'{API}/settings/credit', json=kv)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _sale(admin, pid, **body):
    body.setdefault('items', [{'product_id': pid, 'quantity': 1}])
    body.setdefault('idempotency_key', str(uuid.uuid4()))
    body.setdefault('cash_rounding_ack', True)     # M4: a rounding-aware client says so
    r = admin.post(f'{API}/sales', json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _ret(admin, sale_id, items, **kw):
    return admin.post(f'{API}/returns', json={'sale_id': sale_id, 'items': items,
                                              'idempotency_key': str(uuid.uuid4()), **kw})


def _bal(cid, code):
    conn = H.get_retail_conn()
    try:
        tb = gl.trial_balance(conn, cid)
    finally:
        conn.close()
    assert tb['is_balanced'] is True
    for a in tb['accounts']:
        if a['code'] == code:
            return D(str(a['balance']))
    return D(0)


def _sale_cash_taken(sale_id):
    r = _rows("SELECT COALESCE(SUM(amount),0) AS a FROM payments WHERE sale_id=? AND method='cash' "
              "AND direction='in'", (sale_id,))
    return D(str(r[0]['a'])).quantize(Q)


def _return_one_by_one(admin, sale_id, pid, qty, unit_qty=1):
    """Return the goods `unit_qty` at a time; yield the CUMULATIVE cash handed back after each."""
    got = D(0)
    for _ in range(int(qty / unit_qty)):
        r = _ret(admin, sale_id, [{'product_id': pid, 'quantity': unit_qty}], refund_method='cash')
        assert r.status_code == 200, r.get_json()
        got += D(str(r.get_json()['data']['tender_refund_amount']))
        yield got.quantize(Q)


# ── H1 ──────────────────────────────────────────────────────────────────────────────────────

def test_h1_up_rule_50_pieces_of_0_010_pay_back_the_goods_plus_at_most_one_coin_each_step():
    """The verifier's real-app case. Sale 0.500 (already a coin multiple: sale rounding 0)."""
    admin, cid, pid = H._new_shop(price=0.010)
    _settings(admin, cash_rounding_interval='0.050', cash_rounding_rule='up')
    s = _sale(admin, pid, items=[{'product_id': pid, 'quantity': 50}], payment_method='cash',
              amount_paid=0.5)
    assert D(str(s['total'])) == D('0.500') and _sale_cash_taken(s['id']) == D('0.500')
    for k, cum in enumerate(_return_one_by_one(admin, s['id'], pid, 50), start=1):
        worth = D('0.010') * k
        # EXACT: the cash back so far is the running total of the goods, rounded once.
        assert cum == cr.round_cash(worth, D('0.050'), 'up', Q), (k, cum)
        assert cum <= worth + D('0.050'), (k, cum, worth)            # never a coin ahead
        assert cum <= D('0.500'), (k, cum)                           # never more than the sale took
    assert cum == D('0.500')                                         # all 50 back -> all 0.500 back
    assert _bal(cid, '4950') == D(0), 'a full return in parts must net the sale rounding to zero'


def test_h1_the_old_per_return_rounding_paid_everything_after_ten_pieces_this_pins_that_it_does_not():
    admin, cid, pid = H._new_shop(price=0.010)
    _settings(admin, cash_rounding_interval='0.050', cash_rounding_rule='up')
    s = _sale(admin, pid, items=[{'product_id': pid, 'quantity': 50}], payment_method='cash',
              amount_paid=0.5)
    cums = list(_return_one_by_one(admin, s['id'], pid, 10))
    assert cums[-1] == D('0.100'), cums          # 10 units are worth 0.100, not the whole 0.500


def test_h1_nearest_rule_20_of_40_units_no_longer_pays_back_the_whole_1_000():
    admin, cid, pid = H._new_shop(price=0.025)
    _settings(admin, cash_rounding_interval='0.050', cash_rounding_rule='nearest')
    s = _sale(admin, pid, items=[{'product_id': pid, 'quantity': 40}], payment_method='cash',
              amount_paid=1.0)
    taken = _sale_cash_taken(s['id'])
    assert taken == D('1.000')
    for k, cum in enumerate(_return_one_by_one(admin, s['id'], pid, 40), start=1):
        worth = D('0.025') * k
        assert cum == cr.round_cash(worth, D('0.050'), 'nearest', Q), (k, cum)
        if k == 20:
            assert cum == D('0.500'), cum        # was 1.000 before the fix
        assert cum <= worth + D('0.050')
    assert cum == taken and _bal(cid, '4950') == D(0)


def test_h1_a_sale_that_was_itself_rounded_is_netted_exactly_by_its_parts():
    """3 x 1.157 = 3.471 -> 3.450 paid (nearest/0.050), rounding -0.021. Returned one at a time the
    cash back ends at 3.450 and 4950 is zero: the part-returns reverse the sale's own rounding."""
    admin, cid, pid = H._new_shop(price=1.157)
    _settings(admin, cash_rounding_interval='0.050', cash_rounding_rule='nearest')
    s = _sale(admin, pid, items=[{'product_id': pid, 'quantity': 3}], payment_method='cash',
              amount_paid=10)
    assert D(str(s['rounding_amount'])) == D('-0.021') and _sale_cash_taken(s['id']) == D('3.450')
    assert _bal(cid, '4950') == D('-0.021')
    cums = list(_return_one_by_one(admin, s['id'], pid, 3))
    assert cums[-1] == D('3.450'), cums
    assert _bal(cid, '4950') == D(0)


@pytest.mark.parametrize('rule', ['nearest', 'down', 'up'])
@pytest.mark.parametrize('interval', ['0.005', '0.010', '0.050', '0.100'])
def test_h1_fuzz_cash_back_never_exceeds_cash_taken_and_equals_it_after_a_full_return(rule, interval):
    rng = random.Random(f'm5-h1-{rule}-{interval}')
    admin, cid, pid = H._new_shop(price=1.000)
    _settings(admin, cash_rounding_interval=interval, cash_rounding_rule=rule)
    ran = 0
    for _ in range(6):
        price = D(rng.randint(1, 4000)) / D(1000) + D('0.100')
        pr = admin.post(f'{API}/products', json={
            'name': 'fz', 'sku': f'FZ-{uuid.uuid4().hex[:8]}', 'cost_price': 0.01,
            'sell_price': float(price), 'tax_rate': 0, 'initial_stock': 500}).get_json()['data']['id']
        qty = rng.randint(1, 9)
        before = _bal(cid, '4950')
        s = _sale(admin, pr, items=[{'product_id': pr, 'quantity': qty}], payment_method='cash',
                  amount_paid=1000)
        taken = _sale_cash_taken(s['id'])
        assert taken > 0, (price, qty, rule, interval)
        cuts, left = [], qty
        while left:
            c = rng.randint(1, left)
            cuts.append(c)
            left -= c
        got = D(0)
        for c in cuts:
            r = _ret(admin, s['id'], [{'product_id': pr, 'quantity': c}], refund_method='cash')
            assert r.status_code == 200, r.get_json()
            got += D(str(r.get_json()['data']['tender_refund_amount']))
            assert 0 <= got <= taken, (price, qty, cuts, got, taken)
        assert got.quantize(Q) == taken, (price, qty, cuts, got, taken)
        after = _bal(cid, '4950')
        # this sale's rounding and its parts' rounding cancel: 4950 is back to before the sale
        assert after == before, (price, qty, cuts, before, after)
        ran += 1
    assert ran == 6
