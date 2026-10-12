"""Aura Retail -- MONEY-4a / review D3: a VOUCHER payment must not book phantom cash.

THE DEFECT (review B, D3, verified in code). The till offers a `voucher` tender (F6). `create_sale`
accepts any method, and `general_ledger.post_sale` debited 1000 "Cash and Bank" for the whole
tender retained, whatever the method. A 50.000 sale paid by voucher therefore booked Dr 1000 Cash
50.000 / Cr Revenue: the balance sheet claimed 50 dinars of cash that never arrived, and no
voucher liability had ever been booked either (Aura cannot sell a voucher yet).

THE FIX (decided by the planner): redeeming a voucher debits a LIABILITY account, 2300 "Vouchers
Outstanding", instead of 1000. Until Aura can sell vouchers, 2300 honestly shows a DEBIT balance
equal to the vouchers redeemed but never sold here -- visible, not hidden inside cash. The refund
of a voucher leg is the mirror (Cr 2300, the voucher value goes back to the holder), not Cr 1000.
Account 2300 is seeded like 1250/3900/5910/1150/2050: a `DEFAULT_CHART` row, lazily, no schema claim.
Journals sync as posted facts (`gl_journal`, account metadata travels in the payload), so a peer
posts nothing and needs no rule of its own.

WHAT THIS FILE PROVES (real routes, read back from the real database):
  DENY half
    * pure voucher sale -> Dr 2300, Cr revenue, and NO 1000 line at all;
    * 30 cash + 20 voucher -> 1000 = 30, 2300 = 20, the sale journal balances;
    * refund of the voucher leg (single method, and one leg of a split refund) -> Cr 2300,
      not Cr 1000; the trial balance still balances;
  ALLOW half (the control)
    * a cash-only sale is unchanged: Dr 1000, no 2300 line anywhere;
    * a cash-only split refund (two non-voucher legs) is unchanged: Cr 1000 only;
  SEEDING
    * 2300 exists as a credit-normal liability, and an existing company whose chart lacks it
      gets it back from `ensure_default_chart` (the path an upgraded install takes).

MUTATION (quoted in the hand-back): make post_sale route the voucher part back to 1000 -> the
pure-voucher, mixed and refund tests go RED; the allow-half tests stay green, which is the point.

Run (one file per process):
    pytest products/retail/tests/retail_gl_voucher_tender_test.py -v
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

API = H.API


@pytest.fixture(autouse=True)
def _reset_device_cache():
    H.device_context._cached_device = None
    yield
    H.device_context._cached_device = None


def _sale(admin, pid, **body):
    body.setdefault('items', [{'product_id': pid, 'quantity': 1}])
    body.setdefault('idempotency_key', str(uuid.uuid4()))
    r = admin.post(f'{API}/sales', json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _return(admin, sale, pid, **body):
    r = admin.post(f'{API}/returns', json={
        'sale_id': sale['id'], 'items': [{'product_id': pid, 'quantity': 1}],
        'idempotency_key': str(uuid.uuid4()), **body})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _lines(cid, source_type, source_id):
    """{account code: (debit, credit)} summed over the journals of one document, plus a
    count so an empty result can never read as a pass."""
    conn = H.get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT a.code AS code, SUM(l.debit) AS d, SUM(l.credit) AS c, COUNT(DISTINCT j.id) AS n "
            "FROM gl_journals j JOIN gl_journal_lines l ON l.journal_id=j.id "
            "JOIN gl_accounts a ON a.id=l.account_id "
            "WHERE j.company_id=? AND j.source_type=? AND j.source_id=? "
            "AND a.code NOT IN (?, ?) GROUP BY a.code",
            (cid, source_type, source_id, gl.ACCOUNT_COGS, gl.ACCOUNT_INVENTORY)).fetchall()
        assert rows, f'no {source_type} journal lines for {source_id}'
        return {r['code']: (Decimal(str(r['d'] or 0)), Decimal(str(r['c'] or 0))) for r in rows}
    finally:
        conn.close()


def _trial_balance(cid):
    conn = H.get_retail_conn()
    try:
        return gl.trial_balance(conn, cid)
    finally:
        conn.close()


def test_pure_voucher_sale_debits_vouchers_outstanding_not_cash():
    admin, cid, pid = H._new_shop(price=50.0)
    sale = _sale(admin, pid, payment_method='voucher', amount_paid=50)
    lines = _lines(cid, 'sale', sale['id'])
    assert lines['2300'] == (Decimal('50'), Decimal('0')), lines
    assert lines['4000'] == (Decimal('0'), Decimal('50')), lines
    assert '1000' not in lines, f'a voucher sale must book no cash: {lines}'
    assert _trial_balance(cid)['total_debit'] == pytest.approx(_trial_balance(cid)['total_credit'])


def test_mixed_cash_and_voucher_sale_splits_the_debit_exactly():
    admin, cid, pid = H._new_shop(price=50.0)
    sale = _sale(admin, pid, tenders=[{'method': 'cash', 'amount': 30},
                                      {'method': 'voucher', 'amount': 20}])
    lines = _lines(cid, 'sale', sale['id'])
    assert lines['1000'][0] == Decimal('30'), lines
    assert lines['2300'][0] == Decimal('20'), lines
    assert lines['4000'][1] == Decimal('50'), lines
    # debits == credits across the document, to the fil
    assert sum(d for d, _ in lines.values()) == sum(c for _, c in lines.values())
    tb = _trial_balance(cid)
    assert tb['is_balanced'] is True, tb


def test_refunding_a_voucher_sale_by_voucher_credits_vouchers_outstanding():
    admin, cid, pid = H._new_shop(price=50.0)
    sale = _sale(admin, pid, payment_method='voucher', amount_paid=50)
    ret = _return(admin, sale, pid)                     # default method = the sale's own: voucher
    assert ret['refund_method'] == 'voucher', ret
    lines = _lines(cid, 'return', ret['id'])
    assert lines['2300'] == (Decimal('0'), Decimal('50')), lines
    assert '1000' not in lines, f'giving a voucher back must not credit cash: {lines}'
    # sale + full return net the liability to zero and the books balance
    tb = _trial_balance(cid)
    acct = {a['code']: a for a in tb['accounts']}
    assert acct['2300']['balance'] == 0.0, acct['2300']
    assert tb['is_balanced'] is True


def test_the_voucher_leg_of_a_split_refund_credits_vouchers_outstanding():
    admin, cid, pid = H._new_shop(price=50.0)
    sale = _sale(admin, pid, tenders=[{'method': 'cash', 'amount': 30},
                                      {'method': 'voucher', 'amount': 20}])
    ret = _return(admin, sale, pid, refund_legs=[{'method': 'voucher', 'amount': 20},
                                                  {'method': 'cash', 'amount': 30}])
    lines = _lines(cid, 'return', ret['id'])
    assert lines['2300'][1] == Decimal('20'), lines
    assert lines['1000'][1] == Decimal('30'), lines
    assert _trial_balance(cid)['is_balanced'] is True


def test_a_cash_only_sale_and_split_refund_are_unchanged():
    # ALLOW half: nothing that is not a voucher may move.
    admin, cid, pid = H._new_shop(price=50.0)
    sale = _sale(admin, pid, tenders=[{'method': 'cash', 'amount': 30}, {'method': 'card', 'amount': 20}])
    lines = _lines(cid, 'sale', sale['id'])
    assert lines['1000'][0] == Decimal('50'), lines
    assert '2300' not in lines, lines
    ret = _return(admin, sale, pid, refund_legs=[{'method': 'card', 'amount': 20},
                                                  {'method': 'cash', 'amount': 30}])
    rlines = _lines(cid, 'return', ret['id'])
    assert rlines['1000'][1] == Decimal('50'), rlines
    assert '2300' not in rlines, rlines
    cash = _sale(admin, pid, payment_method='cash', amount_paid=50)
    clines = _lines(cid, 'sale', cash['id'])
    assert clines['1000'][0] == Decimal('50') and '2300' not in clines, clines


def test_vouchers_outstanding_is_a_seeded_credit_normal_liability_and_is_added_to_old_charts():
    admin, cid, pid = H._new_shop(price=50.0)
    _sale(admin, pid, payment_method='cash', amount_paid=50)       # seeds this company's chart
    conn = H.get_retail_conn()
    try:
        row = conn.execute("SELECT account_type, normal_balance, name FROM gl_accounts "
                           "WHERE company_id=? AND code='2300'", (cid,)).fetchone()
        assert (row['account_type'], row['normal_balance']) == ('liability', 'credit'), dict(row)
        # An install that upgraded from before this account existed: the chart lacks it.
        conn.execute("DELETE FROM gl_accounts WHERE company_id=? AND code='2300'", (cid,))
        conn.commit()
        assert conn.execute("SELECT 1 FROM gl_accounts WHERE company_id=? AND code='2300'",
                            (cid,)).fetchone() is None
        gl.ensure_default_chart(conn, cid)
        conn.commit()
        assert conn.execute("SELECT 1 FROM gl_accounts WHERE company_id=? AND code='2300'",
                            (cid,)).fetchone() is not None
    finally:
        conn.close()
