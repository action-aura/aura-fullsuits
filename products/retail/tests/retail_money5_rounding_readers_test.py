"""Aura Retail -- MONEY-5 verifier M3: every reader of the cash-rounding figure is pinned, plus the
three guards whose mutants survived (negative-stock audit, the "nothing on account" check in the
rounding guard, the read-time check on payment-method account overrides).

The verifier flipped each of these and the whole suite stayed green:
  O2  _statement_events ignores sales.rounding_amount   -> a customer statement shows a phantom charge
  O3  _sale_tender_lines ignores it                      -> the receipt shows a phantom 'credit' tender
  O8  report_whatsapp's aging term ignores it            -> a settled sale ages a customer as overdue
  O9  ALLOW_NEGATIVE_STOCK_CHANGED audit row removed     -> "audited" was not proven
  O11 `credit == 0` dropped from the rounding guard      -> rounded a sale that left part on account
  O14 tender_account trusts any stored override code     -> a bad row could route cash into revenue

Each test drives the real route (or the real function on the real database) and is paired with the
deny half that proves it is not vacuous. MUTATIONS are quoted in the commit.

Run (one file per process):
    pytest products/retail/tests/retail_money5_rounding_readers_test.py -v
"""
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import retail_accountant_regate_test as H  # noqa: E402  (builds the isolated app + data dir)
from core.retail import general_ledger as gl  # noqa: E402
from core.retail import tenders as te  # noqa: E402

API = H.API
D = Decimal


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
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


def _exec(sql, params=()):
    conn = H.get_retail_conn()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _cash_sale(admin, pid, cust, paid):
    r = admin.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}], 'payment_method': 'cash', 'amount_paid': paid,
        'customer_id': cust, 'cash_rounding_ack': True, 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _credit_sale(admin, pid, cust):
    r = admin.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}], 'payment_method': 'credit',
        'customer_id': cust, 'amount_paid': 0, 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _customer(admin):
    r = admin.post(f'{API}/customers', json={'name': f'Readers {uuid.uuid4().hex[:6]}',
                                              'credit_mode': 'unlimited'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _rounded_shop():
    """A shop that rounds to 0.050 'nearest', a named customer, and ONE rounded cash sale:
    3.457 settled with 3.450 (rounding -0.007). Returns (admin, cid, pid, customer, sale)."""
    admin, cid, pid = H._new_shop(price=3.457)
    r = admin.post(f'{API}/settings/credit', json={'cash_rounding_interval': '0.050',
                                                    'cash_rounding_rule': 'nearest'})
    assert r.status_code == 200, r.get_json()
    cust = _customer(admin)
    sale = _cash_sale(admin, pid, cust, 3.45)
    assert sale['rounding_amount'] == -0.007
    return admin, cid, pid, cust, sale


# ── O2: the customer statement ─────────────────────────────────────────────────────────────

def _closing(d):
    return float(d.get('closing_balance', d.get('balance', 0)) or 0)


def test_the_statement_of_a_customer_with_a_rounded_cash_sale_shows_no_charge_and_owes_nothing():
    admin, cid, pid, cust, sale = _rounded_shop()
    r = admin.get(f'{API}/customers/{cust}/statement')
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert 'charge' not in str(d), d                                 # no phantom 0.007 charge event
    assert abs(_closing(d)) < 0.0005, d


def test_the_statement_still_charges_a_sale_that_really_left_a_balance():
    """The deny half: the rounding term must not hide real debt."""
    admin, cid, pid, cust, _ = _rounded_shop()
    _cash_sale(admin, pid, cust, 1.0)                                # 2.457 really left on account
    d = admin.get(f'{API}/customers/{cust}/statement').get_json()['data']
    assert 'charge' in str(d) and abs(_closing(d) - 2.457) < 0.0005, d


# ── O3: receipt / returns-picker tender lines ─────────────────────────────────────────────

def _split_sale(admin, pid, cust, tenders):
    r = admin.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}], 'tenders': tenders, 'customer_id': cust,
        'cash_rounding_ack': True, 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def test_the_receipt_tender_lines_of_a_rounded_split_sale_have_no_phantom_credit_line():
    """card 1.000 + exactly the 2.450 of coins for the remaining 2.457 -> rounding -0.007 (amount_paid 3.450, no change). Only a sale paid in
    parts carries `tenders`; the 0.007 the shop conceded must not appear as a part on account."""
    admin, cid, pid, cust, _ = _rounded_shop()
    sale = _split_sale(admin, pid, cust, [{'method': 'card', 'amount': 1.0}, {'method': 'cash', 'amount': 2.45}])
    assert sale['rounding_amount'] == -0.007
    for tenders in (sale['tenders'],
                    admin.get(f'{API}/sales/{sale["id"]}').get_json()['data']['sale'].get('tenders')):
        assert [t['method'] for t in tenders] == ['card', 'cash'], tenders   # was card, cash + 'credit'
        assert [t['amount'] for t in tenders] == [1.0, 2.45]


def test_the_receipt_tender_lines_still_show_a_real_part_on_account():
    admin, cid, pid, cust, _ = _rounded_shop()
    sale = _split_sale(admin, pid, cust, [{'method': 'card', 'amount': 1.0}, {'method': 'credit', 'amount': 2.457}])
    assert [t['method'] for t in sale['tenders']] == ['card', 'credit'], sale['tenders']
    assert sale['tenders'][1]['amount'] == 2.457


# ── O8: the WhatsApp overdue-receivables alert ───────────────────────────────────────────

def _whatsapp_alert(monkeypatch, admin):
    import api.retail_api as _ra
    captured = {}
    monkeypatch.setattr(_ra._whatsapp_settings, 'is_enabled', lambda *a, **k: True)
    monkeypatch.setattr(_ra._whatsapp_settings, 'template_name_for', lambda *a, **k: 'tpl')

    def _queue(conn, **kw):
        captured.update(kw)
        return 1
    monkeypatch.setattr(_ra._whatsapp_hook, 'queue_ar_overdue_alert', _queue)
    r = admin.post(f'{API}/reports/whatsapp', json={'report_type': 'ar_overdue_alert'})
    assert r.status_code == 200, r.get_json()
    return captured


def test_an_old_rounded_cash_sale_does_not_age_a_customer_whose_only_debt_is_new(monkeypatch):
    admin, cid, pid, cust, sale = _rounded_shop()
    # the rounded sale is ancient; today the customer buys on account (owes 3.457, current)
    _exec("UPDATE sales SET created_at='2000-01-01 10:00:00' WHERE id=?", (sale['id'],))
    _credit_sale(admin, pid, cust)
    got = _whatsapp_alert(monkeypatch, admin)
    assert got['customer_count'] == 0 and got['overdue_total'] == 0 and got['bucket_90_plus'] == 0, got


def test_the_whatsapp_alert_still_ages_a_really_unpaid_old_sale(monkeypatch):
    """The deny half: a genuinely old unpaid sale IS overdue, so the test above is not vacuous."""
    admin, cid, pid, cust, _ = _rounded_shop()
    old = _credit_sale(admin, pid, cust)
    _exec("UPDATE sales SET created_at='2000-01-01 10:00:00' WHERE id=?", (old['id'],))
    got = _whatsapp_alert(monkeypatch, admin)
    assert got['customer_count'] == 1 and got['bucket_90_plus'] > 3.4, got


# ── O9: the negative-stock setting is audited ────────────────────────────────────────────

def test_changing_allow_negative_stock_writes_one_audit_row_and_a_no_op_writes_none():
    admin, cid, pid = H._new_shop(price=1.0)

    def _count():
        return len(_rows("SELECT id FROM audit_log WHERE company_id=? AND action='ALLOW_NEGATIVE_STOCK_CHANGED'",
                         (cid,)))
    assert _count() == 0
    assert admin.post(f'{API}/settings/credit', json={'allow_negative_stock': '1'}).status_code == 200
    assert _count() == 1
    row = _rows("SELECT details FROM audit_log WHERE company_id=? AND action='ALLOW_NEGATIVE_STOCK_CHANGED'",
                (cid,))[0]
    assert '0 -> 1' in row['details'], row
    assert admin.post(f'{API}/settings/credit', json={'allow_negative_stock': '1'}).status_code == 200
    assert _count() == 1                                              # unchanged value: nothing audited
    assert admin.post(f'{API}/settings/credit', json={'allow_negative_stock': '0'}).status_code == 200
    assert _count() == 2


# ── O11: the rounding guard's "nothing left on account" check ────────────────────────────

def _settle(tenders, due):
    norm, code, msg = te.normalize_tenders(tenders)
    assert code is None, (code, msg)
    return te.settle_tenders(norm, D(due), 'JOD', cash_rounding=(D('0.050'), 'nearest'))


def test_a_sale_with_a_part_on_account_is_never_rounded_even_when_the_cash_covers_the_coin():
    """cash 10.450 + 0.007 on account = 10.457. Rounding the cash to 10.450 AND leaving 0.007 on
    account would charge the customer the fils twice; the part on account must switch rounding off."""
    r = _settle([{'method': 'cash', 'amount': 10.45}, {'method': 'credit', 'amount': 0.007}], '10.457')
    assert r['ok'], r
    assert r['rounding'] == 0 and r['balance_due'] == D('0.007') and r['paid'] == D('10.450'), r


def test_the_same_cash_with_nothing_on_account_is_rounded():
    """The allow half of the same guard."""
    r = _settle([{'method': 'cash', 'amount': 10.45}], '10.457')
    assert r['ok'] and r['rounding'] == D('-0.007') and r['balance_due'] == 0, r


# ── O14: a stored payment-method account override is checked WHEN READ ───────────────────

def _method_row(cid, name, code):
    _exec("INSERT INTO payment_methods (company_id, name, type, is_active, gl_account_code) "
          "VALUES (?,?,?,1,?)", (cid, name, 'other', code))


def test_a_stored_override_pointing_at_a_non_cash_account_is_ignored_when_read():
    """The only writer validates the code, but rows also arrive by sync / restore / hand edit. A
    tender routed to 4000 Sales would book the cash as revenue twice."""
    admin, cid, pid = H._new_shop(price=1.0)
    _method_row(cid, 'Gizmo', '4000')
    _method_row(cid, 'Pouch', '1020')
    conn = H.get_retail_conn()
    try:
        assert gl.tender_account(conn, cid, 'gizmo') == '1000'        # built-in default, not 4000
        assert gl.tender_account(conn, cid, 'pouch') == '1020'         # a valid override is honoured
        assert gl.tender_account(conn, cid, 'cash') == '1010'          # untouched built-ins
    finally:
        conn.close()
