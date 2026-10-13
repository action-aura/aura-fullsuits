"""MONEY-3 M1 (retail v51): a sale paid in parts is refunded in parts.

BEFORE: a return was refunded through ONE method the cashier picked, so a sale paid 30 cash +
70 card and refunded in full gave all 100 back through one channel: the drawer lost 100 (or
nothing) instead of 30. NOW: a return carries LEGS (`return_refund_legs`) -- (method, amount) rows
summing to the money that goes back (`tender_refund_amount`). Only CASH legs leave the drawer;
the on-account part is never a leg (it reduces AR through ar_forgiven / store_credit).

Both directions are pinned: the split is right AND the refusals (legs that do not add up, a method
the sale never used, a method asked for more than it has left) write nothing. Mutations this file
owns (each performed, red/green quoted in the crew report):

  M1-a  `_cash_session_report`: count EVERY leg as cash (drop `AND l.method='cash'`)
        -> test_full_refund_of_a_30_cash_70_card_sale_...
  M1-b  `return_legs.default_legs`: give the whole tender to the first method
        -> test_full_refund_of_a_30_cash_70_card_sale_...
  M1-c  `return_legs.validate_legs`: drop the sum check
        -> test_edited_legs_that_do_not_add_up_are_refused_and_write_nothing
  M1-d  `return_legs.validate_legs`: drop the method-on-sale check
        -> test_a_method_the_sale_was_not_paid_with_is_refused
  M1-e  `return_legs.validate_legs`: drop the remainder check
        -> test_partial_refunds_over_two_returns_never_exceed_what_each_method_took
  M1-f  create_return: forget earlier returns' legs (`_already_by_method = {}`)
        -> test_partial_refunds_over_two_returns_never_exceed_what_each_method_took
  M1-g  create_return: skip writing the leg rows
        -> test_full_refund_of_a_30_cash_70_card_sale_...
  M1-h  create_return: default legs split the WHOLE refund, not the money-back part
        -> test_the_on_account_part_reduces_ar_and_never_the_drawer

Run:
    pytest products/retail/tests/retail_money3_split_refund_test.py -q
"""
import os
import shutil
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_money3_m1_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA), AURA_SITE_RELAY_ENABLED="0")
os.environ.pop("AURA_DEV", None)
for _k in ("AURA_SYNC_RELAY_URL", "AURA_OWNER_LICENSING_URL"):
    os.environ.pop(_k, None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from core.retail import return_legs  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    email = f'm1-{uuid.uuid4().hex[:8]}@test.local'
    password = 'Money3PW12345'
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


def _product(c, price=100.0):
    r = c.post(f'{API}/products', json={
        'name': 'Split Widget', 'sku': f'SR-{uuid.uuid4().hex[:8]}', 'cost_price': 1,
        'sell_price': price, 'tax_rate': 0, 'initial_stock': 500})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _customer(c):
    r = c.post(f'{API}/customers', json={'name': f'M1 Customer {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    cid = r.get_json()['data']['id']
    conn = get_retail_conn()
    conn.execute("UPDATE customers SET credit_mode='unlimited', credit_limit=1000000 WHERE id=?", (cid,))
    conn.commit(); conn.close()
    return cid


def _balance(cust_id):
    from core.retail import party_balance
    conn = get_retail_conn()
    try:
        return float(party_balance.customer_balance(conn, _company_of_customer(conn, cust_id), cust_id, 'JOD'))
    finally:
        conn.close()


def _company_of_customer(conn, cust_id):
    return conn.execute("SELECT company_id FROM customers WHERE id=?", (cust_id,)).fetchone()[0]


def _rows(sql, params=()):
    conn = get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _count(sql, params=()):
    return _rows(sql, params)[0].popitem()[1]


def _split_sale(c, pid, tenders, qty=1, customer_id=None):
    body = {'items': [{'product_id': pid, 'quantity': qty}], 'payment_method': 'split',
            'tenders': tenders, 'idempotency_key': str(uuid.uuid4())}
    if customer_id:
        body['customer_id'] = customer_id
    r = c.post(f'{API}/sales', json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _return(c, sale_id, pid, qty=1, **extra):
    body = {'sale_id': sale_id, 'items': [{'product_id': pid, 'quantity': qty}],
            'idempotency_key': str(uuid.uuid4())}
    body.update(extra)
    return c.post(f'{API}/returns', json=body)


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


def _legs_of(ret_id):
    return {r['method']: r['amount'] for r in _rows(
        "SELECT method, amount FROM return_refund_legs WHERE return_id=? ORDER BY id", (ret_id,))}


def _as_dict(legs):
    return {lg['method']: lg['amount'] for lg in legs}


# -- the whole point: 30 cash + 70 card ------------------------------------------

def test_full_refund_of_a_30_cash_70_card_sale_pays_back_30_cash_and_70_card(client):
    pid = _product(client, 100.0)
    sid = _open(client, 100.0)
    sale = _split_sale(client, pid, [{'method': 'cash', 'amount': 30}, {'method': 'card', 'amount': 70}])
    assert _x(client, sid)['expected_cash'] == 130.0
    r = _return(client, sale['id'], pid, refund_legs='default')
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert _as_dict(d['legs']) == {'cash': 30.0, 'card': 70.0}, d
    assert d['refund_method'] == 'split'
    assert _legs_of(d['id']) == {'cash': 30.0, 'card': 70.0}
    x = _x(client, sid)
    assert x['cash_refunds'] == 30.0, x                 # NOT 100: the card part never touched the drawer
    assert x['expected_cash'] == 100.0, x               # 100 float + 30 cash sale - 30 cash refund
    # The books: one journal, a line per leg, trial balance balanced. MONEY-5: each leg credits
    # the account ITS METHOD lands in (cash -> 1010, card -> 1030), the mirror of post_sale.
    # (a second 'return' journal exists: the COGS/Inventory reversal. The refund's own journal is
    # the one that credits the cash-like accounts.)
    j = _rows("SELECT DISTINCT j.id AS id FROM gl_journals j JOIN gl_journal_lines l ON l.journal_id=j.id "
              "JOIN gl_accounts a ON a.id=l.account_id "
              "WHERE j.source_type='return' AND j.source_id=? AND a.code IN ('1010','1030') AND l.credit>0", (d['id'],))
    assert len(j) == 1
    lines = _rows("SELECT a.code AS code, l.credit AS credit, l.debit AS debit, l.memo AS memo "
                  "FROM gl_journal_lines l JOIN gl_accounts a ON a.id=l.account_id WHERE l.journal_id=? "
                  "ORDER BY l.id", (j[0]['id'],))
    cash_credits = sorted((ln['memo'], ln['code'], ln['credit']) for ln in lines
                          if ln['code'] in ('1000', '1010', '1030'))
    assert cash_credits == [('Refunded via card', '1030', 70.0),
                            ('Refunded via cash', '1010', 30.0)], lines
    tot = (_tb(client).get('totals') or {})
    if tot:
        assert round(float(tot['debit']) - float(tot['credit']), 3) == 0
    # the single-method sync shape is still readable by an older till: refund_method is 'split'
    # and tender_refund_amount is the SUM of the legs
    row = _rows("SELECT refund_method, tender_refund_amount FROM returns WHERE id=?", (d['id'],))[0]
    assert row == {'refund_method': 'split', 'tender_refund_amount': 100.0}


def test_a_bare_split_return_is_still_refused_but_now_carries_the_default_legs(client):
    pid = _product(client)
    sale = _split_sale(client, pid, [{'method': 'cash', 'amount': 30}, {'method': 'card', 'amount': 70}])
    n = _count("SELECT COUNT(*) FROM returns")
    r = _return(client, sale['id'], pid)
    assert r.status_code == 400 and r.get_json()['code'] == 'REFUND_METHOD_REQUIRED', r.get_json()
    body = r.get_json()['data']
    assert _as_dict(body['default_legs']) == {'cash': 30.0, 'card': 70.0}, body
    assert _count("SELECT COUNT(*) FROM returns") == n


def test_dry_run_shows_the_legs_and_writes_nothing(client):
    pid = _product(client)
    sale = _split_sale(client, pid, [{'method': 'cash', 'amount': 30}, {'method': 'card', 'amount': 70}])
    before = (_count("SELECT COUNT(*) FROM returns"), _count("SELECT COUNT(*) FROM return_refund_legs"),
              _count("SELECT COUNT(*) FROM sync_outbox"), _count("SELECT COUNT(*) FROM gl_journals"))
    r = _return(client, sale['id'], pid, dry_run=True)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['dry_run'] is True and _as_dict(d['legs']) == {'cash': 30.0, 'card': 70.0}
    assert {x['method']: x['amount'] for x in d['refundable']} == {'cash': 30.0, 'card': 70.0}
    after = (_count("SELECT COUNT(*) FROM returns"), _count("SELECT COUNT(*) FROM return_refund_legs"),
             _count("SELECT COUNT(*) FROM sync_outbox"), _count("SELECT COUNT(*) FROM gl_journals"))
    assert after == before


def test_partial_refunds_over_two_returns_never_exceed_what_each_method_took(client):
    pid = _product(client, 50.0)
    sale = _split_sale(client, pid, [{'method': 'cash', 'amount': 30}, {'method': 'card', 'amount': 70}], qty=2)
    # return 1: the cashier hands back mostly cash
    r1 = _return(client, sale['id'], pid, refund_legs=[{'method': 'cash', 'amount': 30},
                                                        {'method': 'card', 'amount': 20}])
    assert r1.status_code == 200, r1.get_json()
    # return 2 asks for more cash than is left (0): refused, nothing written
    n = _count("SELECT COUNT(*) FROM returns")
    r2 = _return(client, sale['id'], pid, refund_legs=[{'method': 'cash', 'amount': 5},
                                                        {'method': 'card', 'amount': 45}])
    assert r2.status_code == 409 and r2.get_json()['code'] == 'RETURN_LEG_EXCEEDS_REMAINDER', r2.get_json()
    assert _count("SELECT COUNT(*) FROM returns") == n
    # the default adapts to what is left: all of it on the card
    r3 = _return(client, sale['id'], pid, refund_legs='default')
    assert r3.status_code == 200, r3.get_json()
    assert _as_dict(r3.get_json()['data']['legs']) == {'card': 50.0}
    # in total, neither method ever gave back more than it took
    tot = {r['method']: r['t'] for r in _rows(
        "SELECT l.method AS method, SUM(l.amount) AS t FROM return_refund_legs l JOIN returns r ON r.id=l.return_id "
        "WHERE r.sale_id=? GROUP BY l.method", (sale['id'],))}
    assert tot == {'cash': 30.0, 'card': 70.0}, tot


def test_edited_legs_that_do_not_add_up_are_refused_and_write_nothing(client):
    pid = _product(client)
    sale = _split_sale(client, pid, [{'method': 'cash', 'amount': 30}, {'method': 'card', 'amount': 70}])
    before = (_count("SELECT COUNT(*) FROM returns"), _count("SELECT COUNT(*) FROM return_refund_legs"),
              _count("SELECT COUNT(*) FROM gl_journals"))
    r = _return(client, sale['id'], pid, refund_legs=[{'method': 'cash', 'amount': 30},
                                                       {'method': 'card', 'amount': 60}])
    assert r.status_code == 409 and r.get_json()['code'] == 'RETURN_LEGS_SUM', r.get_json()
    assert 'add up' in r.get_json()['message']
    assert before == (_count("SELECT COUNT(*) FROM returns"), _count("SELECT COUNT(*) FROM return_refund_legs"),
                      _count("SELECT COUNT(*) FROM gl_journals"))
    # the allow half: the same legs corrected go through
    ok = _return(client, sale['id'], pid, refund_legs=[{'method': 'cash', 'amount': 30},
                                                        {'method': 'card', 'amount': 70}])
    assert ok.status_code == 200, ok.get_json()


def test_a_method_the_sale_was_not_paid_with_is_refused(client):
    pid = _product(client)
    sale = _split_sale(client, pid, [{'method': 'cash', 'amount': 30}, {'method': 'card', 'amount': 70}])
    n = _count("SELECT COUNT(*) FROM returns")
    r = _return(client, sale['id'], pid, refund_legs=[{'method': 'cash', 'amount': 30},
                                                       {'method': 'mobile', 'amount': 70}])
    assert r.status_code == 409 and r.get_json()['code'] == 'RETURN_LEG_METHOD_NOT_ON_SALE', r.get_json()
    assert _count("SELECT COUNT(*) FROM returns") == n


def test_the_on_account_part_reduces_ar_and_never_the_drawer(client):
    """40 card + 30 cash + 30 left on account. A full refund pays back 70 as money (30 cash, 40 card)
    and forgives the 30 of debt -- the debt is not a leg and does not touch the drawer."""
    pid = _product(client, 100.0)
    cust = _customer(client)
    sid = _open(client, 100.0)
    sale = _split_sale(client, pid, [{'method': 'cash', 'amount': 30}, {'method': 'card', 'amount': 40},
                                     {'method': 'credit', 'amount': 30}], customer_id=cust)
    assert _balance(cust) == 30.0
    r = _return(client, sale['id'], pid, refund_legs='default')
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert _as_dict(d['legs']) == {'cash': 30.0, 'card': 40.0}, d
    assert d['tender_refund_amount'] == 70.0 and d['ar_forgiven_amount'] == 30.0
    assert _balance(cust) == 0.0
    x = _x(client, sid)
    assert x['cash_refunds'] == 30.0 and x['expected_cash'] == 100.0, x
    # the debt is not a leg: no 'credit' leg exists
    assert 'credit' not in _legs_of(d['id'])


# -- the older shapes keep working --------------------------------------------------

def test_an_explicit_single_method_keeps_its_historic_one_leg_meaning(client):
    """Android (no split tender) and every older caller send `refund_method` alone."""
    pid = _product(client)
    sid = _open(client, 100.0)
    r = client.post(f'{API}/sales', json={'items': [{'product_id': pid, 'quantity': 1}],
                                         'payment_method': 'cash', 'amount_paid': 100,
                                         'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']
    rr = _return(client, sale_id, pid, refund_method='cash')
    assert rr.status_code == 200, rr.get_json()
    d = rr.get_json()['data']
    # the response shape of a single-method return is pinned elsewhere (no `legs` key unless asked)
    assert d['refund_method'] == 'cash' and 'legs' not in d
    assert _legs_of(d['id']) == {'cash': 100.0}
    assert _x(client, sid)['cash_refunds'] == 100.0


def test_legs_and_store_credit_do_not_mix(client):
    pid = _product(client)
    cust = _customer(client)
    sale = _split_sale(client, pid, [{'method': 'cash', 'amount': 30}, {'method': 'card', 'amount': 70}],
                       customer_id=cust)
    r = _return(client, sale['id'], pid, refund_method='store_credit', refund_legs='default')
    assert r.status_code == 400 and r.get_json()['code'] == 'REFUND_METHOD_INVALID'


def test_the_legs_ride_inside_the_return_sync_event(client):
    import json as _json
    pid = _product(client)
    sale = _split_sale(client, pid, [{'method': 'cash', 'amount': 30}, {'method': 'card', 'amount': 70}])
    d = _return(client, sale['id'], pid, refund_legs='default').get_json()['data']
    ret_uid = _rows("SELECT uid FROM returns WHERE id=?", (d['id'],))[0]['uid']
    ev = [_json.loads(e['payload']) for e in _rows(
        "SELECT payload FROM sync_outbox WHERE entity_type='return'") if ret_uid in e['payload']]
    assert len(ev) == 1
    legs = ev[0]['legs']
    assert sorted((lg['method'], lg['amount']) for lg in legs) == [('card', 70.0), ('cash', 30.0)]
    from database.schema import return_refund_leg_uid
    assert {lg['uid'] for lg in legs} == {return_refund_leg_uid(ret_uid, 'cash'),
                                          return_refund_leg_uid(ret_uid, 'card')}


# -- the pure rounding rule ---------------------------------------------------------

def test_default_legs_put_the_rounding_remainder_on_the_largest_leg_and_sum_exactly():
    rem = [('cash', Decimal('0.334')), ('card', Decimal('0.333')), ('mobile', Decimal('0.333'))]
    legs = return_legs.default_legs(0.5, rem, 'JOD', 0.0005)
    assert round(sum(lg['amount'] for lg in legs), 3) == 0.5
    assert _as_dict(legs) == {'cash': 0.166, 'card': 0.167, 'mobile': 0.167}, legs
    # a refund bigger than everything left cannot be spread: the caller gets None, not a guess
    assert return_legs.default_legs(2, rem, 'JOD', 0.0005) is None


def test_default_legs_in_a_three_decimal_currency_sum_exactly_to_the_money_going_back(client):
    pid = _product(client, 11.832)
    sale = _split_sale(client, pid, [{'method': 'card', 'amount': 5}, {'method': 'cash', 'amount': 6.832}])
    r = _return(client, sale['id'], pid, refund_legs='default')
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert round(sum(lg['amount'] for lg in d['legs']), 3) == d['tender_refund_amount'] == 11.832
    assert _as_dict(d['legs']) == {'card': 5.0, 'cash': 6.832}


def test_the_rounding_residue_lands_on_the_largest_remainder_not_the_smallest():
    """0.002 over remainders cash 1 / card 3 on a 3-decimal currency: the shares round to 0.001
    (0.0005 half-up) and 0.002 (0.0015 half-up), 0.003 in all, so 0.001 of residue must come off.
    It comes off the LARGEST remainder (card): cash 0.001 + card 0.001. Putting it on the smallest
    (cash) leaves cash 0 and card 0.002 -- same total, different legs, which is why the sum check
    alone could never catch it."""
    rem = [('cash', Decimal('1')), ('card', Decimal('3'))]
    legs = return_legs.default_legs(0.002, rem, 'JOD', 0.0005)
    assert _as_dict(legs) == {'cash': 0.001, 'card': 0.001}, legs


# -- an explicit single method on a multi-tender sale is capped like a leg ---------

def test_single_method_on_a_split_sale_is_capped_at_what_that_method_took(client):
    """Android always sends `refund_method`. Returning a desktop 30 cash + 70 card sale as 'cash'
    used to take the whole refund out of a drawer that only ever took 30."""
    pid = _product(client, 10.0)
    sid = _open(client, 100.0)
    sale = _split_sale(client, pid, [{'method': 'cash', 'amount': 30}, {'method': 'card', 'amount': 70}], qty=10)
    before = (_count("SELECT COUNT(*) FROM returns"), _count("SELECT COUNT(*) FROM return_refund_legs"),
              _count("SELECT COUNT(*) FROM gl_journals"), _count("SELECT COUNT(*) FROM sync_outbox"))
    cash_before = _x(client, sid)['cash_refunds']
    # deny half: 40 as cash when the sale only took 30 cash
    r = _return(client, sale['id'], pid, qty=4, refund_method='cash')
    assert r.status_code == 409 and r.get_json()['code'] == 'RETURN_LEG_EXCEEDS_REMAINDER', r.get_json()
    assert '30' in r.get_json()['message']
    dl = r.get_json()['data']['default_legs']
    assert _as_dict(dl) == {'cash': 12.0, 'card': 28.0}, dl     # the proportional split to echo back
    assert before == (_count("SELECT COUNT(*) FROM returns"), _count("SELECT COUNT(*) FROM return_refund_legs"),
                      _count("SELECT COUNT(*) FROM gl_journals"), _count("SELECT COUNT(*) FROM sync_outbox"))
    assert _x(client, sid)['cash_refunds'] == cash_before
    # allow half: exactly what cash took goes back as one cash leg, the drawer drops by 30
    ok = _return(client, sale['id'], pid, qty=3, refund_method='cash')
    assert ok.status_code == 200, ok.get_json()
    d = ok.get_json()['data']
    assert d['refund_method'] == 'cash' and _legs_of(d['id']) == {'cash': 30.0}
    assert _x(client, sid)['cash_refunds'] == cash_before + 30.0
    # and cash is now spent: another cash-only return is refused, card still takes the rest
    again = _return(client, sale['id'], pid, qty=1, refund_method='cash')
    assert again.status_code == 409 and again.get_json()['code'] == 'RETURN_LEG_EXCEEDS_REMAINDER'
    card = _return(client, sale['id'], pid, qty=7, refund_method='card')
    assert card.status_code == 200, card.get_json()


def test_single_method_on_a_single_tender_sale_is_not_capped(client):
    """Unchanged behaviour: a sale paid ONE way keeps today's meaning of `refund_method` (no
    per-method cap), e.g. a card sale handed back in cash."""
    pid = _product(client, 100.0)
    sid = _open(client, 100.0)
    r = client.post(f'{API}/sales', json={'items': [{'product_id': pid, 'quantity': 1}],
                                         'payment_method': 'card', 'amount_paid': 100,
                                         'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    rr = _return(client, r.get_json()['data']['id'], pid, refund_method='cash')
    assert rr.status_code == 200, rr.get_json()
    assert _legs_of(rr.get_json()['data']['id']) == {'cash': 100.0}
    assert _x(client, sid)['cash_refunds'] == 100.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-x"]))
