"""Aura Retail -- MONEY-5 item 1 (review #8): ONE GL ACCOUNT PER WAY OF GETTING PAID.

BEFORE: every tender landed in 1000 "Cash and Bank". The balance sheet could not say how much was
in the drawer, how much the card company still owed the shop, or how much was at the bank.
AFTER (retail v55, only journals posted from now on): cash -> 1010 Cash in Drawer, card -> 1030
Card Clearing, mobile / CliQ -> 1040, transfer -> 1020 Bank, voucher -> 2300 (unchanged), anything
else -> 1000. Refund legs credit the account their method lands in. Customer / supplier payments use
their method; drawer paid-outs / paid-ins -> 1010; an expense paid via 'bank' -> 1020, via
'cash_office' stays in 1000. Journals already posted are NEVER rewritten.

Real routes, read back from the real database. Both halves are tested: what moved AND what must not.

MUTATIONS (quoted in the commit): (M1) tender_account returns 1000 for everything -> the split,
refund and payment tests go RED; (M2) the 'card' map entry points at 1010 -> only the card
assertions go RED; (M3) post_expense ignores paid_via -> the bank-expense test goes RED while the
cash_office control stays green.

Run (one file per process):
    pytest products/retail/tests/retail_money5_tender_accounts_test.py -v
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
D = Decimal


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


def _lines(cid, source_type, source_id=None, exclude_stock=True):
    """{account code: (debit, credit)} summed over the journals of one document type."""
    conn = H.get_retail_conn()
    try:
        sql = ("SELECT a.code AS code, SUM(l.debit) AS d, SUM(l.credit) AS c "
               "FROM gl_journals j JOIN gl_journal_lines l ON l.journal_id=j.id "
               "JOIN gl_accounts a ON a.id=l.account_id "
               "WHERE j.company_id=? AND j.source_type=? ")
        params = [cid, source_type]
        if source_id is not None:
            sql += "AND j.source_id=? "; params.append(source_id)
        if exclude_stock:
            sql += "AND a.code NOT IN (?, ?) "; params += [gl.ACCOUNT_COGS, gl.ACCOUNT_INVENTORY]
        sql += "GROUP BY a.code"
        rows = conn.execute(sql, params).fetchall()
        assert rows, f'no {source_type} journal lines'
        return {r['code']: (D(str(r['d'] or 0)), D(str(r['c'] or 0))) for r in rows}
    finally:
        conn.close()


def _tb(cid):
    conn = H.get_retail_conn()
    try:
        return gl.trial_balance(conn, cid)
    finally:
        conn.close()


def _balance_of(tb, code):
    for a in tb['accounts']:
        if a['code'] == code:
            return a['balance']
    return None


# ── sales: one debit line per method ───────────────────────────────────────────────────────

def test_a_split_cash_and_card_sale_debits_the_drawer_and_card_clearing_separately():
    admin, cid, pid = H._new_shop(price=50.0)
    sale = _sale(admin, pid, tenders=[{'method': 'cash', 'amount': 30}, {'method': 'card', 'amount': 20}])
    lines = _lines(cid, 'sale', sale['id'])
    assert lines['1010'][0] == D('30') and lines['1030'][0] == D('20'), lines
    assert '1000' not in lines, f'no tender may land in the old combined account any more: {lines}'
    assert lines['4000'][1] == D('50'), lines
    tb = _tb(cid)
    assert tb['is_balanced'] is True
    assert _balance_of(tb, '1010') == 30.0 and _balance_of(tb, '1030') == 20.0


def test_mobile_and_transfer_tenders_land_in_the_wallet_and_the_bank():
    admin, cid, pid = H._new_shop(price=50.0)
    s1 = _sale(admin, pid, payment_method='mobile', amount_paid=50)
    s2 = _sale(admin, pid, payment_method='transfer', amount_paid=50)
    assert _lines(cid, 'sale', s1['id'])['1040'][0] == D('50')
    assert _lines(cid, 'sale', s2['id'])['1020'][0] == D('50')
    assert _tb(cid)['is_balanced'] is True


def test_a_voucher_still_lands_in_2300_and_never_in_a_cash_like_account():
    admin, cid, pid = H._new_shop(price=50.0)
    sale = _sale(admin, pid, tenders=[{'method': 'cash', 'amount': 30}, {'method': 'voucher', 'amount': 20}])
    lines = _lines(cid, 'sale', sale['id'])
    assert lines['2300'][0] == D('20') and lines['1010'][0] == D('30'), lines
    assert sum(1 for c in gl.CASH_LIKE_CODES if c in lines) == 1, lines     # only 1010


def test_a_credit_sale_posts_no_tender_line_for_the_unpaid_part():
    # ALLOW/control: AR is untouched by the tender split.
    admin, cid, pid = H._new_shop(price=50.0)
    cust = H._customer(admin)
    conn = H.get_retail_conn()
    conn.execute("UPDATE customers SET credit_mode='unlimited', credit_limit=1000000 WHERE id=?", (cust,))
    conn.commit(); conn.close()
    sale = _sale(admin, pid, customer_id=cust, tenders=[{'method': 'cash', 'amount': 20},
                                                        {'method': 'credit', 'amount': 30}])
    lines = _lines(cid, 'sale', sale['id'])
    assert lines['1010'][0] == D('20') and lines['1100'][0] == D('30'), lines
    assert sum(1 for c in gl.CASH_LIKE_CODES if c in lines) == 1, lines


# ── refunds: each leg credits its own method's account ─────────────────────────────────────

def test_refund_legs_credit_the_account_of_their_own_method():
    admin, cid, pid = H._new_shop(price=50.0)
    sale = _sale(admin, pid, tenders=[{'method': 'cash', 'amount': 30}, {'method': 'card', 'amount': 20}])
    ret = _return(admin, sale, pid, refund_legs=[{'method': 'card', 'amount': 20},
                                                  {'method': 'cash', 'amount': 30}])
    lines = _lines(cid, 'return', ret['id'])
    assert lines['1030'][1] == D('20') and lines['1010'][1] == D('30'), lines
    assert '1000' not in lines, lines
    tb = _tb(cid)
    assert tb['is_balanced'] is True
    # the sale and its full refund net each tender account to zero
    assert _balance_of(tb, '1010') == 0.0 and _balance_of(tb, '1030') == 0.0


def test_a_single_cash_refund_credits_the_drawer_not_the_old_account():
    admin, cid, pid = H._new_shop(price=50.0)
    sale = _sale(admin, pid, payment_method='cash', amount_paid=50)
    ret = _return(admin, sale, pid)
    lines = _lines(cid, 'return', ret['id'])
    assert lines['1010'][1] == D('50'), lines
    assert '1000' not in lines, lines


# ── payments, drawer movements, expenses ────────────────────────────────────────────────────

def test_customer_payments_and_supplier_payments_use_their_methods_account():
    admin, cid, pid = H._new_shop(price=50.0)
    cust = H._customer(admin)
    r = admin.post(f'{API}/customers/{cust}/payments', json={'amount': 10, 'method': 'card'})
    assert r.status_code == 200, r.get_json()
    r = admin.post(f'{API}/customers/{cust}/payments', json={'amount': 7, 'method': 'cash'})
    assert r.status_code == 200, r.get_json()
    cl = _lines(cid, 'customer_payment')
    assert cl['1030'][0] == D('10') and cl['1010'][0] == D('7'), cl
    sup = H._supplier(admin)
    r = admin.post(f'{API}/suppliers/{sup}/payments', json={'amount': 4, 'method': 'transfer'})
    assert r.status_code == 200, r.get_json()
    sl = _lines(cid, 'supplier_payment')
    assert sl['1020'][1] == D('4'), sl
    assert _tb(cid)['is_balanced'] is True


def test_a_drawer_paid_out_and_paid_in_move_the_drawer_account():
    admin, cid, _pid = H._new_shop()
    sid = admin.post(f'{API}/cash-sessions/open', json={'opening_float': 100}).get_json()['data']['id']
    r = admin.post(f'{API}/cash-sessions/{sid}/movements',
                   json={'type': 'paid_out', 'amount': 20, 'reason': 'driver', 'gl_account_code': '6300'})
    assert r.status_code == 200, r.get_json()
    r = admin.post(f'{API}/cash-sessions/{sid}/movements',
                   json={'type': 'paid_in', 'amount': 5, 'reason': 'found', 'gl_account_code': '4800'})
    assert r.status_code == 200, r.get_json()
    lines = _lines(cid, 'cash_movement')
    assert lines['1010'] == (D('5'), D('20')), lines
    assert '1000' not in lines, lines


def test_an_expense_paid_via_bank_leaves_1020_and_office_cash_stays_in_1000():
    admin, cid, _pid = H._new_shop()
    for via, amount in (('bank', 250), ('cash_office', 40)):
        r = admin.post(f'{API}/expenses', json={'gl_account_code': '6000', 'amount': amount,
                                                'paid_via': via, 'memo': via})
        assert r.status_code == 200, r.get_json()
    lines = _lines(cid, 'expense')
    assert lines['1020'] == (D('0'), D('250')), lines          # DENY: the bank expense is not in 1000
    assert lines['1000'] == (D('0'), D('40')), lines           # ALLOW: office cash is exactly as MONEY-4b booked it


# ── per-shop override ─────────────────────────────────────────────────────────────────────

def test_a_shop_can_point_a_payment_method_at_another_cash_like_account_and_only_that():
    admin, cid, pid = H._new_shop(price=50.0)
    ok = admin.post(f'{API}/payment-methods', json={'name': 'Visa', 'type': 'card', 'gl_account_code': '1020'})
    assert ok.status_code == 200, ok.get_json()
    for bad in ('4000', '1100', '2300', '9999'):
        r = admin.post(f'{API}/payment-methods', json={'name': 'Evil', 'gl_account_code': bad})
        assert r.status_code == 400 and r.get_json()['code'] == 'INVALID_GL_ACCOUNT', (bad, r.get_json())
    sale = _sale(admin, pid, payment_method='visa', amount_paid=50)
    assert _lines(cid, 'sale', sale['id'])['1020'][0] == D('50')       # the override applied
    other = _sale(admin, pid, payment_method='card', amount_paid=50)
    assert _lines(cid, 'sale', other['id'])['1030'][0] == D('50')      # the built-in map is untouched
    listed = admin.get(f'{API}/payment-methods').get_json()['data']
    assert any(m['name'] == 'Visa' and m['gl_account_code'] == '1020' for m in listed)


# ── the books around it ───────────────────────────────────────────────────────────────────────

def test_the_new_accounts_are_seeded_asset_accounts_and_4950_is_revenue():
    admin, cid, pid = H._new_shop(price=50.0)
    _sale(admin, pid, payment_method='cash', amount_paid=50)           # seeds the chart
    conn = H.get_retail_conn()
    try:
        rows = {r['code']: (r['account_type'], r['normal_balance'], r['name']) for r in conn.execute(
            "SELECT code, account_type, normal_balance, name FROM gl_accounts WHERE company_id=? "
            "AND code IN ('1010','1020','1030','1040','4950')", (cid,)).fetchall()}
    finally:
        conn.close()
    assert rows == {
        '1010': ('asset', 'debit', 'Cash in Drawer'), '1020': ('asset', 'debit', 'Bank'),
        '1030': ('asset', 'debit', 'Card Clearing'), '1040': ('asset', 'debit', 'CliQ / Wallet'),
        '4950': ('revenue', 'credit', 'Cash Rounding')}, rows


def test_the_balance_sheet_and_cash_flow_see_the_new_accounts_and_still_reconcile():
    admin, cid, pid = H._new_shop(price=50.0)
    _sale(admin, pid, tenders=[{'method': 'cash', 'amount': 30}, {'method': 'card', 'amount': 20}])
    today = __import__('datetime').date.today().strftime('%Y-%m-%d')
    bs = admin.get(f'{API}/reports/balance-sheet?as_of_date={today}').get_json()['data']
    held = {a['code']: a['amount'] for a in bs['assets']['accounts']}
    assert held['1010'] == 30.0 and held['1030'] == 20.0 and '1000' not in held, held
    assert bs['balance_check']['is_balanced'] is True
    cf = admin.get(f'{API}/reports/cash-flow?start_date={today}&end_date={today}').get_json()['data']
    assert cf['total_cash_in'] == 50.0 and cf['closing_cash'] == 50.0, cf
    assert cf['reconciliation']['is_balanced'] is True


def test_existing_journals_are_not_rewritten_by_the_upgrade_or_the_new_chart():
    """An old install: cash already booked to 1000. Adding the new accounts and running the v55
    step must leave every old line, balance and account byte-identical."""
    admin, cid, _pid = H._new_shop()
    conn = H.get_retail_conn()
    try:
        gl.ensure_default_chart(conn, cid)
        gl.post_journal(conn, cid, None, '2026-03-01', 'sale', None, 'JOD',
                        [{'account_code': '1000', 'debit': 80}, {'account_code': '4000', 'credit': 80}],
                        reference='old-era-sale')
        conn.commit()

        def snap():
            return conn.execute(
                "SELECT j.id, a.code, l.debit, l.credit FROM gl_journals j "
                "JOIN gl_journal_lines l ON l.journal_id=j.id JOIN gl_accounts a ON a.id=l.account_id "
                "WHERE j.company_id=? ORDER BY j.id, l.id", (cid,)).fetchall()
        before = [tuple(r) for r in snap()]
        import database.schema as schema
        schema._migrate_add_money5_tender_columns(conn)
        conn.execute("DELETE FROM gl_accounts WHERE company_id=? AND code IN ('1010','1020','1030','1040','4950') "
                     "AND id NOT IN (SELECT account_id FROM gl_journal_lines)", (cid,))
        gl.ensure_default_chart(conn, cid)            # re-seeds the five: an upgraded install's first post
        conn.commit()
        assert [tuple(r) for r in snap()] == before
        tb = gl.trial_balance(conn, cid)
        assert _balance_of(tb, '1000') == 80.0 and _balance_of(tb, '1010') == 0.0
        assert tb['is_balanced'] is True
    finally:
        conn.close()
