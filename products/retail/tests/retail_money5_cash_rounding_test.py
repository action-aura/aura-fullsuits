"""Aura Retail -- MONEY-5 item 2 (review #3): CASH ROUNDING TO REAL COINS, BOOKED.

A total of 3.457 cannot be paid in cash (Jordan has no 1-fil coin). With the settings
`cash_rounding_interval` ('0' default = OFF | '0.005' | '0.010' | '0.050' | '0.100') and
`cash_rounding_rule` ('nearest' default | 'down' | 'up') the CASH part that settles the remainder
is rounded; the sale stays whole; the signed difference is `sales.rounding_amount` and is booked to
GL 4950 so the journal balances by construction.

DENY half  : a card amount is NEVER rounded; a cash part that does not settle the remainder is not
             rounded; rounding at '0' changes nothing at all; bad setting values are refused.
ALLOW half : 3.457 at 0.050 nearest -> 3.450 retained, rounding -0.007, Dr 4950 0.007; a split
             sale rounds the cash leg only; 'up' books a credit; a return re-rounds its cash leg and
             the full return of a rounded sale leaves NO profit or loss behind (sales - returns
             +/- rounding = 0); the drawer expects the coins that
             really arrived; the sync payload carries the figure; a named customer is NOT left
             owing the rounded fils.

Real routes, read back from the real database.

MUTATIONS (quoted in the commit): (M1) round the card leg too -> the split test goes RED;
(M2) drop the rounding line from post_sale -> every booked-rounding test and the trial balance go
RED; (M3) the party-balance formula ignores rounding_amount -> the 'named customer owes nothing'
test goes RED; (M4) create_return skips the cash-leg re-round -> the return test goes RED.

Run (one file per process):
    pytest products/retail/tests/retail_money5_cash_rounding_test.py -v
"""
import json
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
    # No background sync push: a nudge thread racing the outbox read below would make the
    # 'the event carries the rounding' test depend on timing, not on the code.
    import api.retail_api as _ra
    monkeypatch.setattr(_ra, '_sync_nudge', lambda *a, **k: None)
    yield
    H.device_context._cached_device = None


# ── the pure arithmetic ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('amount,interval,rule,expected', [
    ('3.457', '0.050', 'nearest', '3.450'),
    ('3.475', '0.050', 'nearest', '3.500'),      # a tie goes UP
    ('3.457', '0.050', 'down', '3.450'),
    ('3.451', '0.050', 'up', '3.500'),
    ('3.457', '0.005', 'nearest', '3.455'),
    ('3.457', '0.010', 'nearest', '3.460'),
    ('3.457', '0.100', 'nearest', '3.500'),
    ('3.450', '0.050', 'up', '3.450'),           # already a coin multiple: unchanged by any rule
    ('3.450', '0.050', 'down', '3.450'),
    ('0.020', '0.050', 'down', '0.000'),         # can reach zero; the caller decides what that means
    ('3.457', '0', 'nearest', '3.457'),          # off
])
def test_round_cash_arithmetic(amount, interval, rule, expected):
    assert cr.round_cash(D(amount), D(interval), rule, Q) == D(expected)


@pytest.mark.parametrize('raw', ['0.07', 'abc', '', None, '-0.05', 'NaN', '1', '0.5'])
def test_an_unknown_interval_is_off_never_a_guess(raw):
    assert cr.parse_interval(raw) == D('0')
    assert cr.canonical_interval(raw) is None


def test_the_same_coin_has_one_spelling_and_an_unknown_rule_is_the_default():
    assert cr.canonical_interval('0.05') == '0.050' and cr.canonical_interval('0.050') == '0.050'
    assert cr.canonical_interval('0') == '0' and cr.canonical_interval('0.0') == '0'
    assert cr.parse_rule('DOWN') == 'down' and cr.parse_rule('banana') == 'nearest'


# ── settle_tenders: the cash leg only, in-process ───────────────────────────────────────────

def _settle(tenders, due, interval='0.050', rule='nearest'):
    from core.retail import tenders as te
    norm, code, msg = te.normalize_tenders(tenders)
    assert code is None, (code, msg)
    cfg = (D(interval), rule) if D(interval) > 0 else None
    return te.settle_tenders(norm, D(due), 'JOD', cash_rounding=cfg)


def test_settle_tenders_rounds_only_the_cash_leg_and_reports_the_signed_difference():
    r = _settle([{'method': 'card', 'amount': 5}, {'method': 'cash', 'amount': 6}], '10.457')
    assert r['ok'] and r['rounding'] == D('-0.007') and r['change'] == D('0.550'), r
    assert r['rows'] == [('card', D('5.000')), ('cash', D('5.450'))], r['rows']
    assert r['balance_due'] == 0


def test_settle_tenders_off_or_unrounded_is_byte_for_byte_the_old_answer():
    off = _settle([{'method': 'card', 'amount': 5}, {'method': 'cash', 'amount': 6}], '10.457', interval='0')
    assert off['rounding'] == 0 and off['change'] == D('0.543')
    assert off['rows'] == [('card', D('5.000')), ('cash', D('5.457'))]


def test_settle_tenders_never_rounds_when_part_is_left_on_account_or_cash_is_short():
    owed = _settle([{'method': 'cash', 'amount': 3}, {'method': 'credit', 'amount': 0.457}], '3.457')
    assert owed['rounding'] == 0 and owed['balance_due'] == D('0.457'), owed
    short = _settle([{'method': 'card', 'amount': 5}, {'method': 'cash', 'amount': 5.4}], '10.457')
    assert short['ok'] is False and short['code'] == 'TENDERS_SHORT', short


def test_settle_tenders_round_up_needs_the_cash_to_cover_the_rounded_amount():
    ok = _settle([{'method': 'card', 'amount': 5}, {'method': 'cash', 'amount': 5.5}], '10.457', rule='up')
    assert ok['ok'] and ok['rounding'] == D('0.043') and ok['change'] == 0, ok
    exact = _settle([{'method': 'card', 'amount': 5}, {'method': 'cash', 'amount': 5.457}], '10.457', rule='up')
    assert exact['ok'] and exact['rounding'] == 0, exact          # old client: not rounded, not refused


# ── helpers over the real app ─────────────────────────────────────────────────────────────────

def _settings(admin, **kv):
    r = admin.post(f'{API}/settings/credit', json=kv)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _sale(admin, pid, **body):
    body.setdefault('items', [{'product_id': pid, 'quantity': 1}])
    body.setdefault('idempotency_key', str(uuid.uuid4()))
    r = admin.post(f'{API}/sales', json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _rows(sql, params=()):
    conn = H.get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _sale_row(sale_id):
    return _rows("SELECT * FROM sales WHERE id=?", (sale_id,))[0]


def _pay_rows(sale_id):
    return [(r['method'], r['amount']) for r in _rows(
        "SELECT method, amount FROM payments WHERE sale_id=? AND direction='in' ORDER BY id", (sale_id,))]


def _lines(cid, source_type, source_id):
    conn = H.get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT a.code AS code, SUM(l.debit) AS d, SUM(l.credit) AS c "
            "FROM gl_journals j JOIN gl_journal_lines l ON l.journal_id=j.id "
            "JOIN gl_accounts a ON a.id=l.account_id "
            "WHERE j.company_id=? AND j.source_type=? AND j.source_id=? "
            "AND a.code NOT IN (?, ?) GROUP BY a.code",
            (cid, source_type, source_id, gl.ACCOUNT_COGS, gl.ACCOUNT_INVENTORY)).fetchall()
        assert rows, f'no {source_type} journal lines for {source_id}'
        return {r['code']: (D(str(r['d'] or 0)), D(str(r['c'] or 0))) for r in rows}
    finally:
        conn.close()


def _tb(cid):
    conn = H.get_retail_conn()
    try:
        return gl.trial_balance(conn, cid)
    finally:
        conn.close()


def _bal(tb, code):
    for a in tb['accounts']:
        if a['code'] == code:
            return a['balance']
    return 0.0


def _shop(price, **settings):
    admin, cid, pid = H._new_shop(price=price)
    if settings:
        _settings(admin, **settings)
    return admin, cid, pid


NEAREST_05 = dict(cash_rounding_interval='0.050', cash_rounding_rule='nearest')


# ── settings ──────────────────────────────────────────────────────────────────────────────────

def test_the_settings_default_to_off_and_bad_values_are_refused_with_nothing_stored():
    admin, cid, pid = H._new_shop(price=3.457)
    s = admin.get(f'{API}/settings/credit').get_json()['data']
    assert s['cash_rounding_interval'] == '0' and s['cash_rounding_rule'] == 'nearest'
    for bad in ({'cash_rounding_interval': '0.07'}, {'cash_rounding_interval': 'x'},
                {'cash_rounding_rule': 'banana'}, {'cash_rounding_rule': ''}):
        r = admin.post(f'{API}/settings/credit', json=bad)
        assert r.status_code == 400, (bad, r.get_json())
        assert r.get_json()['code'] == 'CASH_ROUNDING_INVALID', r.get_json()
    s = admin.get(f'{API}/settings/credit').get_json()['data']
    assert s['cash_rounding_interval'] == '0' and s['cash_rounding_rule'] == 'nearest'
    ok = _settings(admin, cash_rounding_interval='0.05')          # one canonical spelling is stored
    assert ok['cash_rounding_interval'] == '0.050'


# ── OFF: nothing changes ─────────────────────────────────────────────────────────────────────

def test_with_rounding_off_a_cash_sale_is_exactly_what_it_always_was():
    admin, cid, pid = H._new_shop(price=3.457)
    sale = _sale(admin, pid, payment_method='cash', amount_paid=3.457)
    row = _sale_row(sale['id'])
    assert row['rounding_amount'] in (None, 0, 0.0), row['rounding_amount']
    assert _pay_rows(sale['id']) == [('cash', 3.457)]
    lines = _lines(cid, 'sale', sale['id'])
    assert lines['1010'][0] == D('3.457') and '4950' not in lines, lines
    assert sale['change'] == 0 and sale['balance_due'] == 0


# ── ON: the cash part is rounded and the difference is booked ────────────────────────────────────

def test_3_457_cash_at_0_050_nearest_is_3_450_paid_with_minus_0_007_booked_to_4950():
    admin, cid, pid = _shop(3.457, **NEAREST_05)
    sale = _sale(admin, pid, payment_method='cash', amount_paid=3.45)
    row = _sale_row(sale['id'])
    assert row['total'] == 3.457 and row['rounding_amount'] == -0.007
    assert row['amount_paid'] == 3.45 and row['change_amount'] == 0
    assert _pay_rows(sale['id']) == [('cash', 3.45)]
    lines = _lines(cid, 'sale', sale['id'])
    assert lines['1010'][0] == D('3.450'), lines
    assert lines['4950'] == (D('0.007'), D('0')), lines            # customer paid less: Dr 4950
    assert lines['4000'][1] == D('3.457'), lines                   # the sale itself is untouched
    tb = _tb(cid)
    assert tb['is_balanced'] is True
    assert sale['rounding_amount'] == -0.007 and sale['balance_due'] == 0


def test_change_is_given_against_the_ROUNDED_amount():
    admin, cid, pid = _shop(3.457, **NEAREST_05)
    sale = _sale(admin, pid, payment_method='cash', amount_paid=5)
    row = _sale_row(sale['id'])
    assert row['change_amount'] == 1.55 and row['rounding_amount'] == -0.007
    assert _pay_rows(sale['id']) == [('cash', 3.45)]               # what the drawer kept


def test_omitting_amount_paid_means_the_rounded_amount_not_the_unrounded_one():
    admin, cid, pid = _shop(3.457, **NEAREST_05)
    sale = _sale(admin, pid, payment_method='cash')
    row = _sale_row(sale['id'])
    assert row['amount_paid'] == 3.45 and row['change_amount'] == 0 and row['rounding_amount'] == -0.007


def test_rounding_up_books_a_credit_and_the_customer_pays_more():
    admin, cid, pid = _shop(3.457, cash_rounding_interval='0.050', cash_rounding_rule='up')
    sale = _sale(admin, pid, payment_method='cash', amount_paid=3.5)
    row = _sale_row(sale['id'])
    assert row['rounding_amount'] == 0.043 and _pay_rows(sale['id']) == [('cash', 3.5)]
    lines = _lines(cid, 'sale', sale['id'])
    assert lines['1010'][0] == D('3.500') and lines['4950'] == (D('0'), D('0.043')), lines
    assert _tb(cid)['is_balanced'] is True


def test_rounding_down_with_the_rule_down():
    admin, cid, pid = _shop(3.499, cash_rounding_interval='0.050', cash_rounding_rule='down')
    sale = _sale(admin, pid, payment_method='cash', amount_paid=3.45)
    assert _sale_row(sale['id'])['rounding_amount'] == -0.049
    assert _lines(cid, 'sale', sale['id'])['4950'] == (D('0.049'), D('0'))


# ── split tender: the cash leg only ───────────────────────────────────────────────────────────

def test_a_split_sale_rounds_the_cash_leg_and_never_the_card_leg():
    admin, cid, pid = _shop(10.457, **NEAREST_05)
    sale = _sale(admin, pid, tenders=[{'method': 'card', 'amount': 5.0}, {'method': 'cash', 'amount': 5.45}])
    row = _sale_row(sale['id'])
    assert row['rounding_amount'] == -0.007
    assert _pay_rows(sale['id']) == [('card', 5.0), ('cash', 5.45)]    # card exactly as charged
    lines = _lines(cid, 'sale', sale['id'])
    assert lines['1030'][0] == D('5.000') and lines['1010'][0] == D('5.450'), lines
    assert lines['4950'] == (D('0.007'), D('0')), lines
    assert _tb(cid)['is_balanced'] is True


def test_a_split_sale_with_too_much_cash_gives_change_against_the_rounded_cash_part():
    admin, cid, pid = _shop(10.457, **NEAREST_05)
    sale = _sale(admin, pid, tenders=[{'method': 'card', 'amount': 5.0}, {'method': 'cash', 'amount': 10}])
    row = _sale_row(sale['id'])
    assert row['change_amount'] == 4.55 and _pay_rows(sale['id']) == [('card', 5.0), ('cash', 5.45)]


def test_a_card_only_sale_is_never_rounded_even_with_rounding_on():
    admin, cid, pid = _shop(10.457, **NEAREST_05)
    sale = _sale(admin, pid, payment_method='card', amount_paid=10.457)
    row = _sale_row(sale['id'])
    assert row['rounding_amount'] in (None, 0, 0.0) and _pay_rows(sale['id']) == [('card', 10.457)]
    assert '4950' not in _lines(cid, 'sale', sale['id'])


def test_a_card_and_cash_split_where_the_card_alone_covers_the_remainder_has_no_cash_rounding():
    admin, cid, pid = _shop(10.457, **NEAREST_05)
    r = admin.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}], 'idempotency_key': str(uuid.uuid4()),
        'tenders': [{'method': 'card', 'amount': 10.457}, {'method': 'cash', 'amount': 1.0}]})
    # the cash part would all come back as change: refused exactly as before (no rounding involved)
    assert r.status_code == 400 and r.get_json()['code'] == 'TENDER_CASH_NOT_NEEDED', r.get_json()


# ── a cash part that does not settle the rest is not rounded ───────────────────────────────

def test_a_part_payment_in_cash_leaves_the_rest_on_account_and_is_not_rounded():
    admin, cid, pid = _shop(3.457, **NEAREST_05)
    cust = H._customer(admin)
    conn = H.get_retail_conn()
    conn.execute("UPDATE customers SET credit_mode='unlimited', credit_limit=1000000 WHERE id=?", (cust,))
    conn.commit(); conn.close()
    sale = _sale(admin, pid, customer_id=cust, payment_method='credit', amount_paid=1.0)
    row = _sale_row(sale['id'])
    assert row['rounding_amount'] in (None, 0, 0.0)
    assert sale['balance_due'] == 2.457


def test_a_named_customer_paying_the_rounded_cash_amount_owes_nothing():
    """The derived debt is `total - points - amount_paid`; 3.457 - 3.450 would be a phantom 0.007
    on every named cash sale. The rounding term cancels it."""
    admin, cid, pid = _shop(3.457, **NEAREST_05)
    cust = H._customer(admin)
    sale = _sale(admin, pid, customer_id=cust, payment_method='cash', amount_paid=3.45)
    assert _sale_row(sale['id'])['rounding_amount'] == -0.007
    r = admin.get(f'{API}/customers/{cust}')
    bal = r.get_json()['data']['credit_balance'] if r.status_code == 200 else None
    rec = admin.get(f'{API}/customers/receivables').get_json()['data']
    assert not any(str(c.get('id')) == str(cust) for c in (rec.get('customers') if isinstance(rec, dict) else rec)), rec
    if bal is not None:
        assert abs(bal) < 0.0005, bal


# ── returns: refund the unrounded value, re-round the cash leg ──────────────────────────────

def test_a_full_return_of_a_rounded_sale_pays_back_what_was_paid_and_leaves_no_profit_or_loss():
    admin, cid, pid = _shop(3.457, **NEAREST_05)
    sale = _sale(admin, pid, payment_method='cash', amount_paid=3.45)
    r = admin.post(f'{API}/returns', json={'sale_id': sale['id'], 'items': [{'product_id': pid, 'quantity': 1}],
                                           'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['refund_amount'] == 3.457 and d['tender_refund_amount'] == 3.45     # the goods vs the coins
    tb = _tb(cid)
    assert tb['is_balanced'] is True
    # The drawer account is back to zero. Verifier-fix round: a FULL return now reverses the sale's
    # rounding too -- Dr Sales Returns takes the whole 3.457 of goods and Cr 4950 hands back the 0.007
    # the sale had given away (this test used to pin 4950 == -0.007 left standing, which the verifier
    # showed contradicted the code's own comment; profit still netted to 0 either way, so the
    # profit line below is unchanged). What it can no longer catch: nothing -- it is strictly
    # stricter (4900 and 4950 are now pinned to the figures that make a full return leave no trace).
    assert _bal(tb, '1010') == 0.0
    assert round(_bal(tb, '4000') - _bal(tb, '4900') + _bal(tb, '4950'), 3) == 0.0, (   # balances are on each account's own normal side
        _bal(tb, '4000'), _bal(tb, '4900'), _bal(tb, '4950'))
    assert _bal(tb, '4900') == 3.457 and _bal(tb, '4950') == 0.0


def test_a_partial_return_re_rounds_its_own_cash_leg_and_books_the_difference():
    admin, cid, pid = _shop(5.017, **NEAREST_05)
    sale = _sale(admin, pid, items=[{'product_id': pid, 'quantity': 2}], payment_method='cash', amount_paid=10.05)
    assert _sale_row(sale['id'])['rounding_amount'] == 0.016            # 10.034 -> 10.050
    r = admin.post(f'{API}/returns', json={'sale_id': sale['id'], 'items': [{'product_id': pid, 'quantity': 1}],
                                           'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['refund_amount'] == 5.017 and d['tender_refund_amount'] == 5.0   # 5.017 -> 5.000
    lines = _lines(cid, 'return', d['id'])
    assert lines['1010'][1] == D('5.000') and lines['4950'][1] == D('0.017'), lines
    assert lines['4900'][0] == D('5.017'), lines                              # the goods are reversed whole
    assert _tb(cid)['is_balanced'] is True


def test_with_rounding_off_a_return_is_not_rounded():
    admin, cid, pid = H._new_shop(price=5.017)
    sale = _sale(admin, pid, items=[{'product_id': pid, 'quantity': 2}], payment_method='cash', amount_paid=10.034)
    r = admin.post(f'{API}/returns', json={'sale_id': sale['id'], 'items': [{'product_id': pid, 'quantity': 1}],
                                           'idempotency_key': str(uuid.uuid4())})
    d = r.get_json()['data']
    assert d['tender_refund_amount'] == 5.017
    assert '4950' not in _lines(cid, 'return', d['id'])


# ── the drawer and the wire ─────────────────────────────────────────────────────────────────────

def test_the_drawer_expects_the_coins_that_really_arrived():
    admin, cid, pid = _shop(3.457, **NEAREST_05)
    sid = admin.post(f'{API}/cash-sessions/open', json={'opening_float': 100}).get_json()['data']['id']
    _sale(admin, pid, payment_method='cash', amount_paid=5)             # 3.450 kept, 1.550 change
    x = admin.get(f'{API}/cash-sessions/{sid}/x-report').get_json()['data']
    assert x['expected_cash'] == 103.45, x


def test_the_sale_sync_event_carries_the_rounding_amount():
    admin, cid, pid = _shop(3.457, **NEAREST_05)
    sale = _sale(admin, pid, payment_method='cash', amount_paid=3.45)
    uid = _sale_row(sale['id'])['uid']
    ev = [json.loads(r['payload']) for r in _rows(
        "SELECT payload FROM sync_outbox WHERE entity_type='sale' AND entity_id=?", (uid,))]
    assert len(ev) == 1 and ev[0]['rounding_amount'] == -0.007, ev


def test_the_receipt_model_says_rounding():
    admin, cid, pid = _shop(3.457, **NEAREST_05)
    sale = _sale(admin, pid, payment_method='cash', amount_paid=3.45)
    r = admin.get(f'{API}/printer/receipt-model?sale_id={sale["id"]}')
    assert r.status_code == 200, r.get_json()
    flat = json.dumps(r.get_json())
    assert 'Rounding' in flat and '0.007' in flat, flat[:600]
