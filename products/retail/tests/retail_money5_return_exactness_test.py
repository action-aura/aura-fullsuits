"""Aura Retail -- MONEY-5 item 4: partial returns refund EXACTLY what was paid.

THE DEFECT (UI-3a verifier, LOW item L4, measured: 33 of 156 fuzzed sales). Each partial return
re-priced its share of a line from the stored per-line % and rounded it on its own, so a line
returned in parts refunded 1-3 fils LESS than the customer paid (an amount discount stores an odd %
per line, and every part rounds independently).

THE FIX. Per sale line, the return that brings the line to fully returned takes the REMAINDER
(what the customer paid for the line minus what earlier returns already refunded for it) -- the
same idea as `returns.tax_refunded`'s completing-return rule. A part that is not the last is capped
at that remainder, so a rounded-up part can never over-refund.

WHAT THIS FILE PROVES, on the real routes:
  * 156 seeded carts (amount discounts, 'make the total X', mixed 0% / 16% tax lines, weighed
    quantities), every line returned in 1-3 parts: for EVERY line the parts sum to exactly what the
    line cost, and the sale's refunds sum to exactly the sale total. (Before the fix: ~21% short.)
  * NEVER over-refund: at no point does a line's cumulative refund exceed what it cost.
  * A line returned in ONE piece is unchanged (the control: the remainder rule is a no-op there).

MUTATION (quoted in the commit): remove the remainder rule (always price the part by calculate_line)
-> the exactness test goes RED while the never-over-refund test stays green.

Run (one file per process):
    pytest products/retail/tests/retail_money5_return_exactness_test.py -v
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
from core.retail import pricing  # noqa: E402

API = H.API
D = Decimal
Q = D('0.001')
SEED = 20261013
N_SALES = 156


@pytest.fixture(autouse=True)
def _reset_device_cache():
    H.device_context._cached_device = None
    yield
    H.device_context._cached_device = None


def _rows(sql, params=()):
    conn = H.get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _product(admin, name, price, tax):
    r = admin.post(f'{API}/products', json={
        'name': name, 'sku': f'RX-{uuid.uuid4().hex[:8]}', 'cost_price': price / 2,
        'sell_price': price, 'tax_rate': tax, 'initial_stock': 100000})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _shop():
    admin, cid, _pid = H._new_shop(price=1.0)
    specs = [('a', 3.5, 16), ('b', 2.75, 16), ('c', 0.181, 16), ('d', 1.0, 0),
             ('e', 0.001, 16), ('f', 0.999, 0), ('g', 7.333, 16)]
    ids = {k: _product(admin, f'Rx {k}', price, tax) for k, price, tax in specs}
    return admin, cid, ids, {k: (price, tax) for k, price, tax in specs}


def _paid_for(line):
    """What the customer paid for one sale line, tax included -- the sale's own pricing function
    on the stored figures (the verifier's `check_sale` proved header == sum of these)."""
    mode = 'after_discount'
    c = pricing.calculate_line(line['unit_price'], line['quantity'], line['discount_pct'] or 0,
                               line['tax_rate'] or 0, mode=mode, currency='JOD')
    return D(str(c['total'])).quantize(Q)


def _ring(admin, ids, specs, rng):
    keys = list(ids)
    items = []
    for k in rng.sample(keys, rng.randint(1, 4)):
        qty = round(rng.uniform(0.001, 2.5), 3) if k == 'g' else rng.randint(1, 4)
        items.append((k, qty))
    gross = D(0)
    cur_total = D(0)
    for k, qty in items:
        price, tax = specs[k]
        c = pricing.calculate_line(price, qty, 0, tax, mode='after_discount', currency='JOD')
        gross += D(str(c['gross'])); cur_total += D(str(c['total']))
    if rng.choice(['target', 'amount']) == 'target':
        lo = max(D('0.001'), (cur_total * D('0.05')).quantize(Q))
        tgt = (lo + (cur_total - lo) * D(str(rng.random()))).quantize(Q)
        kw = {'cart_target_total': float(tgt)}
    else:
        kw = {'cart_discount_amount': float(max(D('0.001'), (gross * D(str(rng.random()))).quantize(Q)))}
    body = {'idempotency_key': str(uuid.uuid4()), 'payment_method': 'cash', 'amount_paid': 1000,
            'items': [{'product_id': ids[k], 'quantity': q} for k, q in items],
            'discount_reason': 'fuzz', **kw}
    r = admin.post(f'{API}/sales', json=body)
    return r


def _return_in_parts(admin, sale_id, line, rng, ledger):
    q = D(str(line['quantity']))
    if q == q.to_integral_value() and q > 1 and rng.random() < 0.7:
        cut = rng.randint(1, int(q) - 1)
        parts = [D(cut), q - cut]
    elif q != q.to_integral_value() and rng.random() < 0.7:
        half = (q / 2).quantize(Q)
        parts = [half, q - half] if half > 0 else [q]
    else:
        parts = [q]
    for pq in parts:
        r = admin.post(f'{API}/returns', json={
            'sale_id': sale_id, 'reason': 'fuzz', 'refund_method': 'cash',
            'idempotency_key': str(uuid.uuid4()),
            'items': [{'product_id': line['product_id'], 'quantity': float(pq), 'sale_item_id': line['id']}]})
        assert r.status_code == 200, r.get_json()
        refunded = D(str(r.get_json()['data']['refund_amount'])).quantize(Q)
        ledger.append((line['id'], refunded))
    return len(parts)


def test_every_line_returned_in_parts_refunds_exactly_what_it_cost_and_never_more():
    admin, cid, ids, specs = _shop()
    rng = random.Random(SEED)
    rung = split = 0
    for n in range(N_SALES):
        r = _ring(admin, ids, specs, rng)
        if r.status_code != 200:
            assert r.get_json().get('code') in ('TARGET_NOT_REACHABLE', 'DISCOUNT_NOT_REACHABLE'), r.get_json()
            continue
        rung += 1
        sale_id = r.get_json()['data']['id']
        total = D(str(r.get_json()['data']['total'])).quantize(Q)
        lines = _rows("SELECT * FROM sale_items WHERE sale_id=? ORDER BY id", (sale_id,))
        paid = {ln['id']: _paid_for(ln) for ln in lines}
        ledger = []
        for ln in lines:
            if _return_in_parts(admin, sale_id, ln, rng, ledger) > 1:
                split += 1
        got = {}
        for line_id, refunded in ledger:
            got[line_id] = got.get(line_id, D(0)) + refunded
            assert got[line_id] <= paid[line_id], (
                f'sale {sale_id} line {line_id}: refunded {got[line_id]} > paid {paid[line_id]} (OVER-refund)')
        for line_id in paid:
            assert got[line_id] == paid[line_id], (
                f'sale {sale_id} line {line_id}: refunded {got[line_id]} != paid {paid[line_id]}')
        assert sum(got.values(), D(0)) == total, (sale_id, sum(got.values(), D(0)), total)
    assert rung >= 100 and split >= 40, f'the fuzz did not exercise enough ({rung} sales, {split} split lines)'


def test_a_line_returned_in_one_piece_refunds_what_it_always_did():
    admin, cid, ids, specs = _shop()
    r = admin.post(f'{API}/sales', json={
        'idempotency_key': str(uuid.uuid4()), 'payment_method': 'cash', 'amount_paid': 100,
        'items': [{'product_id': ids['a'], 'quantity': 3}]})
    sale = r.get_json()['data']
    line = _rows("SELECT * FROM sale_items WHERE sale_id=?", (sale['id'],))[0]
    r = admin.post(f'{API}/returns', json={'sale_id': sale['id'], 'idempotency_key': str(uuid.uuid4()),
                                           'items': [{'product_id': ids['a'], 'quantity': 3,
                                                      'sale_item_id': line['id']}]})
    assert D(str(r.get_json()['data']['refund_amount'])) == D(str(sale['total']))


def test_a_known_short_refund_case_now_pays_the_remainder_on_the_last_part():
    """A hand-built case (no fuzz): 3 x 0.181 taxed 16%, a 'make the total 0.500' discount, returned
    1 + 1 + 1. Whatever the per-part rounding, the three parts must add up to the sale total."""
    admin, cid, ids, specs = _shop()
    r = admin.post(f'{API}/sales', json={
        'idempotency_key': str(uuid.uuid4()), 'payment_method': 'cash', 'amount_paid': 100,
        'items': [{'product_id': ids['c'], 'quantity': 3}], 'cart_target_total': 0.5,
        'discount_reason': 'x'})
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']
    line = _rows("SELECT * FROM sale_items WHERE sale_id=?", (sale['id'],))[0]
    parts = []
    for _ in range(3):
        rr = admin.post(f'{API}/returns', json={
            'sale_id': sale['id'], 'idempotency_key': str(uuid.uuid4()),
            'items': [{'product_id': ids['c'], 'quantity': 1, 'sale_item_id': line['id']}]})
        assert rr.status_code == 200, rr.get_json()
        parts.append(D(str(rr.get_json()['data']['refund_amount'])))
    assert sum(parts, D(0)).quantize(Q) == D(str(sale['total'])).quantize(Q), (parts, sale['total'])
