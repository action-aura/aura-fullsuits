"""Aura Retail -- MONEY-4b / review defect D2 (retail schema v52): money that leaves the shop
without being a sale reaches the books.

THE DEFECT. A cashier paid 20.000 out of the drawer to the delivery driver. The drawer's expected
cash went down by 20.000 and NO book moved: the balance sheet's Cash and Bank stayed 20.000 too
high and the profit-and-loss never saw the cost. There was also no place at all to record rent,
utilities or salaries.

WHAT THIS FILE PROVES, through the real routes, read back from the real database:
  paid-out (the drawer)
    * paid_out with an account  -> exactly one journal, Dr <account> / Cr 1000, dated on the shop's
      BUSINESS day (start hour 6, clock frozen at 01:30 -> the day before);
    * paid_out to Owner Drawings (3100) -> equity down, NOT an expense (profit unchanged);
    * a paid_out with no account (old desktop build / Android) -> 6900, never refused;
    * an account outside the allowed set (4000, 1100, 1000) -> 400, no movement row, no journal;
    * float_in / float_out -> NO journal (cash moving safe <-> drawer; 1000 is combined);
    * paid_in with 4800 Other Income -> Dr 1000 / Cr 4800; with 3100 (the owner putting his own
      money back) -> Dr 1000 / Cr 3100, equity UP and profit unchanged; no account -> 4800, never
      refused; any other code (4900 Sales Returns, 6xxx, 1100 ...) -> 400 and nothing written;
      retried once -> one journal;
    * posting the same movement twice -> ONE journal;
    * the D2 scenario: 100.000 of cash in the books, 20.000 paid to the driver -> the balance
      sheet's Cash and Bank is 80.000, the trial balance balances, the P&L carries the 20.000.
  expenses
    * create -> Dr <account> / Cr 1000 at the stated business date; list by date range; voided
      rows stay listed, flagged, and drop out of the total;
    * void needs a reason, posts the reversal exactly once, a second void is a 409 and posts
      nothing; the books return to where they were;
    * another company's expenses are invisible (list, void) -- the scope proof;
    * a future date, a bad account, a bad paid_via, a zero amount are all 400;
    * authority: the Accountant (books.record / books.view) is in; a manager and a cashier are out.

MUTATIONS (each performed, red quoted in the hand-back):
  P-a  create_cash_movement: skip `gl.post_drawer_paid_out(...)`
       -> test_paid_out_with_an_account_posts_dr_account_cr_cash_on_the_business_day (and the D2 test)
  P-b  create_cash_movement: post for every type (drop the `mtype == 'paid_out'` guard)
       -> test_float_in_and_float_out_post_no_journal
  P-c  list_expenses: drop `company_id=?` from the SELECT
       -> test_another_companys_expenses_are_invisible_and_cannot_be_voided
  P-d  create_cash_movement: pass no business_date (calendar day)
       -> test_paid_out_with_an_account_posts_dr_account_cr_cash_on_the_business_day
  P-e  general_ledger._post_once existence check removed
       -> test_posting_the_same_movement_twice_makes_one_journal
  I-a  post_drawer_paid_in with the sides swapped (Dr account / Cr 1000)
       -> test_paid_in_to_other_income_posts_dr_cash_cr_4800_on_the_business_day
  I-b  create_cash_movement: post for every type (also float) -> test_float_in_and_float_out_post_no_journal
  I-c  the paid_in default account 4800 -> 6900 (refusal/default broken)
       -> test_a_paid_in_with_no_account_goes_to_other_income_and_is_never_refused
  C-b  create_expense gated on retail.books.view instead of retail.books.record
       -> test_a_reader_of_the_books_can_list_expenses_but_not_record_or_void_them
       (the Accountant/manager/cashier test stayed GREEN under this mutation: none of them holds
       view WITHOUT record; the matrix test also goes red, by name)

Run (one file per process):
    pytest products/retail/tests/retail_money4b_paidout_expense_test.py -v
"""
import sys
import uuid
from datetime import datetime as _real_dt, date, timedelta
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import retail_accountant_regate_test as H  # noqa: E402  (builds the isolated app + data dir)
from api import retail_api as ra  # noqa: E402
from core.retail import general_ledger as gl  # noqa: E402
from core.retail import metrics  # noqa: E402

API = H.API
DAY = date(2026, 3, 11)
PREV = '2026-03-10'
SAME = '2026-03-11'


class _Clock(_real_dt):
    """datetime whose now() is frozen at a settable wall time on DAY."""
    at = (12, 0)

    @classmethod
    def now(cls, tz=None):
        naive = cls(DAY.year, DAY.month, DAY.day, cls.at[0], cls.at[1], 0)
        return naive if tz is None else naive.astimezone().astimezone(tz)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    H.device_context._cached_device = None
    _Clock.at = (12, 0)
    for mod in (ra, gl, metrics):
        monkeypatch.setattr(mod, 'datetime', _Clock)
    yield
    H.device_context._cached_device = None


def _at(hour, minute):
    _Clock.at = (hour, minute)


def _set_start_hour(admin, hour):
    r = admin.post(f'{API}/settings/business-day', json={'business_day_start_hour': hour})
    assert r.status_code == 200, r.get_json()


def _drawer(admin):
    r = H._open(admin, 100.0)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _move(admin, sid, mtype, amount, **extra):
    return admin.post(f'{API}/cash-sessions/{sid}/movements',
                      json={'type': mtype, 'amount': amount, **extra})


def _journals(cid, source_type):
    """[{'date', 'lines': {code: (debit, credit)}}] for every journal of `source_type`."""
    conn = H.get_retail_conn()
    try:
        out = []
        for j in conn.execute(
                "SELECT id, business_date, source_uid, reversed_journal_id FROM gl_journals "
                "WHERE company_id=? AND source_type=? ORDER BY id", (cid, source_type)).fetchall():
            lines = {}
            for ln in conn.execute(
                    "SELECT a.code AS code, l.debit AS d, l.credit AS c FROM gl_journal_lines l "
                    "JOIN gl_accounts a ON a.id=l.account_id WHERE l.journal_id=?", (j['id'],)).fetchall():
                lines[ln['code']] = (ln['d'], ln['c'])
            out.append({'id': j['id'], 'date': j['business_date'], 'lines': lines,
                        'source_uid': j['source_uid'], 'reversed': j['reversed_journal_id']})
        return out
    finally:
        conn.close()


def _movement_rows(cid, mtype=None):
    conn = H.get_retail_conn()
    try:
        sql = ("SELECT m.* FROM cash_movements m JOIN cash_sessions s ON s.id=m.session_id "
               "WHERE s.company_id=?")
        params = [cid]
        if mtype:
            sql += " AND m.type=?"; params.append(mtype)
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _seed_cash_in_books(cid, amount, business_date):
    """100.000 of cash that really is in the books (Dr 1000 / Cr 4000), the way a cash sale
    leaves it, so 'cash drops by the paid-out amount' is measured against a real balance."""
    conn = H.get_retail_conn()
    try:
        gl.post_journal(conn, cid, None, business_date, 'sale', None, 'JOD',
                        [{'account_code': '1000', 'debit': amount},
                         {'account_code': '4000', 'credit': amount}],
                        reference='seed-sale')
        conn.commit()
    finally:
        conn.close()


def _balance_sheet(admin, as_of):
    r = admin.get(f'{API}/reports/balance-sheet?as_of_date={as_of}')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _cash_balance(bs):
    for a in bs['assets']['accounts']:
        if a['code'] == '1000':
            return a['amount']
    return 0.0


def _trial_balance(admin):
    r = admin.get(f'{API}/reports/trial-balance')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


# ── the drawer ─────────────────────────────────────────────────────────────────

def test_paid_out_with_an_account_posts_dr_account_cr_cash_on_the_business_day():
    admin, cid, _pid = H._new_shop()
    _set_start_hour(admin, 6)
    sid = _drawer(admin)
    _at(1, 30)                                   # business day is still 2026-03-10
    r = _move(admin, sid, 'paid_out', 20, reason='delivery driver', gl_account_code='6300')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['gl_account_code'] == '6300'
    js = _journals(cid, 'cash_movement')
    # The shape this guards against: asserting a date over an EMPTY list passes forever.
    assert len(js) == 1, js
    assert js[0]['lines'] == {'6300': (20.0, 0.0), '1000': (0.0, 20.0)}, js[0]
    assert js[0]['date'] == PREV, js[0]
    mv = _movement_rows(cid, 'paid_out')
    assert len(mv) == 1 and js[0]['source_uid'] == mv[0]['id']


def test_paid_out_to_owner_drawings_is_equity_not_an_expense():
    admin, cid, _pid = H._new_shop()
    sid = _drawer(admin)
    _seed_cash_in_books(cid, 100.0, SAME)
    equity_before = _balance_sheet(admin, SAME)['equity']['total']   # the fixture's opening stock is in here
    r = _move(admin, sid, 'paid_out', 30, reason='owner took cash home', gl_account_code='3100')
    assert r.status_code == 200, r.get_json()
    js = _journals(cid, 'cash_movement')
    assert len(js) == 1 and js[0]['lines'] == {'3100': (30.0, 0.0), '1000': (0.0, 30.0)}, js
    pl = admin.get(f'{API}/reports/profit-and-loss?start_date={SAME}&end_date={SAME}').get_json()['data']
    assert pl['operating_expenses']['total'] == 0.0, 'owner drawings must not reduce profit'
    assert pl['net_profit'] == 100.0
    bs = _balance_sheet(admin, SAME)
    assert bs['balance_check']['is_balanced'] is True, bs['balance_check']
    assert round(bs['equity']['total'] - equity_before, 3) == -30.0, (equity_before, bs['equity'])


def test_a_paid_out_with_no_account_goes_to_other_expenses_and_is_never_refused():
    admin, cid, _pid = H._new_shop()
    sid = _drawer(admin)
    r = _move(admin, sid, 'paid_out', 5, reason='old client')     # no gl_account_code at all
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['gl_account_code'] == '6900'
    js = _journals(cid, 'cash_movement')
    assert len(js) == 1 and js[0]['lines'] == {'6900': (5.0, 0.0), '1000': (0.0, 5.0)}, js
    # blank string is "none" too
    r = _move(admin, sid, 'paid_out', 6, gl_account_code='  ')
    assert r.status_code == 200 and r.get_json()['data']['gl_account_code'] == '6900'


@pytest.mark.parametrize('bad', ['4000', '1100', '1000', '4900', '9999', 'rent'])
def test_an_account_outside_the_allowed_set_is_refused_and_nothing_is_written(bad):
    admin, cid, _pid = H._new_shop()
    sid = _drawer(admin)
    r = _move(admin, sid, 'paid_out', 7, gl_account_code=bad)
    assert r.status_code == 400, r.get_json()
    assert r.get_json()['code'] == 'INVALID_GL_ACCOUNT'
    assert _movement_rows(cid, 'paid_out') == [], 'a refused paid-out left a movement behind'
    assert _journals(cid, 'cash_movement') == []


def test_float_in_and_float_out_post_no_journal():
    admin, cid, _pid = H._new_shop()
    sid = _drawer(admin)
    assert _move(admin, sid, 'float_in', 50).status_code == 200
    assert _move(admin, sid, 'float_out', 30, gl_account_code='6300').status_code == 200
    # ALLOW half of the pair: the movements are recorded and move the drawer ...
    mv = _movement_rows(cid)
    assert sorted(m['type'] for m in mv) == ['float_in', 'float_out'], mv
    # ... and the books are untouched, and a stray account on a float is not stored.
    assert _journals(cid, 'cash_movement') == []
    assert all(m['gl_account_code'] is None for m in mv), mv
    x = admin.get(f'{API}/cash-sessions/{sid}/x-report').get_json()['data']
    assert x['expected_cash'] == 120.0, x       # 100 + 50 - 30: the drawer DID move


def test_paid_in_to_other_income_posts_dr_cash_cr_4800_on_the_business_day():
    admin, cid, _pid = H._new_shop()
    _set_start_hour(admin, 6)
    sid = _drawer(admin)
    _at(1, 30)                                   # business day is still 2026-03-10
    r = _move(admin, sid, 'paid_in', 15, reason='found in the safe', gl_account_code='4800')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['gl_account_code'] == '4800'
    js = _journals(cid, 'cash_movement')
    assert len(js) == 1, js                      # a count: a date over an empty list passes forever
    assert js[0]['lines'] == {'1000': (15.0, 0.0), '4800': (0.0, 15.0)}, js[0]
    assert js[0]['date'] == PREV, js[0]
    mv = _movement_rows(cid, 'paid_in')
    assert len(mv) == 1 and js[0]['source_uid'] == mv[0]['id']
    # it shows up as revenue, and cash is up by the same amount
    _at(12, 0)
    pl = admin.get(f'{API}/reports/profit-and-loss?start_date={PREV}&end_date={PREV}').get_json()['data']
    assert {a['code']: a['amount'] for a in pl['revenue_accounts']}.get('4800') == 15.0, pl['revenue_accounts']
    assert _cash_balance(_balance_sheet(admin, SAME)) == 15.0
    assert _trial_balance(admin)['is_balanced'] is True


def test_paid_in_by_the_owner_is_equity_up_not_income():
    admin, cid, _pid = H._new_shop()
    sid = _drawer(admin)
    _seed_cash_in_books(cid, 100.0, SAME)
    equity_before = _balance_sheet(admin, SAME)['equity']['total']
    r = _move(admin, sid, 'paid_in', 25, reason='owner put money back', gl_account_code='3100')
    assert r.status_code == 200, r.get_json()
    js = _journals(cid, 'cash_movement')
    assert len(js) == 1 and js[0]['lines'] == {'1000': (25.0, 0.0), '3100': (0.0, 25.0)}, js
    pl = admin.get(f'{API}/reports/profit-and-loss?start_date={SAME}&end_date={SAME}').get_json()['data']
    assert pl['net_profit'] == 100.0, 'the owner putting money back is not income'
    bs = _balance_sheet(admin, SAME)
    assert bs['balance_check']['is_balanced'] is True, bs['balance_check']
    assert _cash_balance(bs) == 125.0
    assert round(bs['equity']['total'] - equity_before, 3) == 25.0, (equity_before, bs['equity'])


def test_a_paid_in_with_no_account_goes_to_other_income_and_is_never_refused():
    admin, cid, _pid = H._new_shop()
    sid = _drawer(admin)
    r = _move(admin, sid, 'paid_in', 40)          # an old build / the Android app: no account at all
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['gl_account_code'] == '4800'
    js = _journals(cid, 'cash_movement')
    assert len(js) == 1 and js[0]['lines'] == {'1000': (40.0, 0.0), '4800': (0.0, 40.0)}, js
    r = _move(admin, sid, 'paid_in', 6, gl_account_code='  ')
    assert r.status_code == 200 and r.get_json()['data']['gl_account_code'] == '4800'


@pytest.mark.parametrize('bad', ['4900', '4000', '1100', '1000', '6300', '6900', '9999', 'income'])
def test_a_paid_in_account_outside_the_allowed_set_is_refused_and_nothing_is_written(bad):
    admin, cid, _pid = H._new_shop()
    sid = _drawer(admin)
    r = _move(admin, sid, 'paid_in', 7, gl_account_code=bad)
    assert r.status_code == 400, r.get_json()
    assert r.get_json()['code'] == 'INVALID_GL_ACCOUNT'
    assert _movement_rows(cid, 'paid_in') == [], 'a refused paid-in left a movement behind'
    assert _journals(cid, 'cash_movement') == []


def test_the_two_directions_do_not_share_accounts():
    """ALLOW/DENY pair across directions: 4800 is a paid_in account only, a 6xxx expense is a
    paid_out account only, 3100 is both (a drawing out, money back in)."""
    admin, cid, _pid = H._new_shop()
    sid = _drawer(admin)
    assert _move(admin, sid, 'paid_out', 3, gl_account_code='4800').status_code == 400
    assert _move(admin, sid, 'paid_in', 3, gl_account_code='6300').status_code == 400
    assert _move(admin, sid, 'paid_out', 3, gl_account_code='3100').status_code == 200
    assert _move(admin, sid, 'paid_in', 3, gl_account_code='3100').status_code == 200
    assert len(_journals(cid, 'cash_movement')) == 2


def test_a_paid_in_posted_twice_makes_one_journal():
    admin, cid, _pid = H._new_shop()
    sid = _drawer(admin)
    assert _move(admin, sid, 'paid_in', 12, gl_account_code='4800').status_code == 200
    mv = _movement_rows(cid, 'paid_in')[0]
    conn = H.get_retail_conn()
    try:
        first = gl.post_drawer_paid_in(conn, cid, 1, mv['id'], 12, '4800', 'JOD', business_date=SAME)
        second = gl.post_drawer_paid_in(conn, cid, 1, mv['id'], 12, '4800', 'JOD', business_date=SAME)
        conn.commit()
    finally:
        conn.close()
    assert first == second, 'the retry must hand back the journal that already exists'
    assert len(_journals(cid, 'cash_movement')) == 1


def test_4900_is_still_sales_returns_and_4800_is_other_income():
    codes = {c: (n, t, nb) for c, n, t, nb, _p in gl.DEFAULT_CHART}
    assert codes['4900'] == ('Sales Returns and Allowances', 'revenue', 'debit')
    assert codes['4800'] == ('Other Income', 'revenue', 'credit')
    assert '4900' not in gl.PAID_OUT_ACCOUNT_CODES and '4900' not in gl.PAID_IN_ACCOUNT_CODES
    assert gl.PAID_IN_ACCOUNT_CODES == ('4800', '3100')
    # every account the design seeds, with the right normal side
    assert codes['3100'] == ('Owner Drawings', 'equity', 'debit')
    for code in ('6000', '6100', '6200', '6300', '6400', '6500', '6900'):
        assert codes[code][1:] == ('expense', 'debit'), code


def test_posting_the_same_movement_twice_makes_one_journal():
    admin, cid, _pid = H._new_shop()
    sid = _drawer(admin)
    assert _move(admin, sid, 'paid_out', 12, gl_account_code='6100').status_code == 200
    mv = _movement_rows(cid, 'paid_out')[0]
    conn = H.get_retail_conn()
    try:
        first = gl.post_drawer_paid_out(conn, cid, 1, mv['id'], 12, '6100', 'JOD', business_date=SAME)
        second = gl.post_drawer_paid_out(conn, cid, 1, mv['id'], 12, '6100', 'JOD', business_date=SAME)
        conn.commit()
    finally:
        conn.close()
    assert first == second, 'the retry must hand back the journal that already exists'
    assert len(_journals(cid, 'cash_movement')) == 1


def test_d2_the_driver_scenario_cash_drops_and_the_books_balance():
    admin, cid, _pid = H._new_shop()
    sid = _drawer(admin)
    _seed_cash_in_books(cid, 100.0, SAME)
    before = _cash_balance(_balance_sheet(admin, SAME))
    assert before == 100.0
    assert _move(admin, sid, 'paid_out', 20, reason='delivery driver',
                 gl_account_code='6300').status_code == 200
    bs = _balance_sheet(admin, SAME)
    assert _cash_balance(bs) == 80.0, bs['assets']
    assert bs['balance_check']['is_balanced'] is True, bs['balance_check']
    tb = _trial_balance(admin)
    assert tb['is_balanced'] is True, tb
    pl = admin.get(f'{API}/reports/profit-and-loss?start_date={SAME}&end_date={SAME}').get_json()['data']
    opex = {a['code']: a['amount'] for a in pl['operating_expenses']['accounts']}
    assert opex == {'6300': 20.0}, pl['operating_expenses']
    assert pl['net_profit'] == 80.0


# ── expenses ───────────────────────────────────────────────────────────────────

def _expense(admin, **kw):
    body = {'gl_account_code': '6000', 'amount': 250, 'paid_via': 'bank', 'memo': 'March rent'}
    body.update(kw)
    return admin.post(f'{API}/expenses', json=body)


def test_an_expense_posts_dr_account_cr_cash_at_its_business_date_and_lists():
    admin, cid, _pid = H._new_shop()
    r = _expense(admin, business_date='2026-03-05')
    assert r.status_code == 200, r.get_json()
    e = r.get_json()['data']
    assert e['uid'] and e['voided'] is False and e['gl_account_code'] == '6000'
    js = _journals(cid, 'expense')
    assert len(js) == 1, js
    assert js[0]['lines'] == {'6000': (250.0, 0.0), '1000': (0.0, 250.0)} and js[0]['date'] == '2026-03-05'
    assert js[0]['source_uid'] == e['uid']
    # a second, later expense for the list filters
    assert _expense(admin, gl_account_code='6100', amount=40, business_date=SAME,
                    memo='electricity').status_code == 200
    lst = admin.get(f'{API}/expenses?from=2026-03-10&to=2026-03-31').get_json()['data']
    assert [x['gl_account_code'] for x in lst['expenses']] == ['6100'], lst
    assert lst['total'] == 40.0
    whole = admin.get(f'{API}/expenses').get_json()['data']
    assert len(whole['expenses']) == 2 and whole['total'] == 290.0
    only_rent = admin.get(f'{API}/expenses?account=6000').get_json()['data']
    assert [x['gl_account_code'] for x in only_rent['expenses']] == ['6000']
    assert only_rent['expenses'][0]['account_name'] == 'Rent'
    tb = _trial_balance(admin)
    assert tb['is_balanced'] is True


def test_voiding_an_expense_posts_the_reversal_once_and_restores_the_books():
    admin, cid, _pid = H._new_shop()
    _seed_cash_in_books(cid, 500.0, SAME)
    uid = _expense(admin, business_date=SAME).get_json()['data']['uid']
    assert _cash_balance(_balance_sheet(admin, SAME)) == 250.0
    # a reason is mandatory
    assert admin.post(f'{API}/expenses/{uid}/void', json={}).status_code == 400
    assert admin.post(f'{API}/expenses/{uid}/void', json={'reason': '   '}).status_code == 400
    assert _journals(cid, 'reversal') == []
    r = admin.post(f'{API}/expenses/{uid}/void', json={'reason': 'entered twice'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['voided'] is True and r.get_json()['data']['void_reason'] == 'entered twice'
    rev = _journals(cid, 'reversal')
    assert len(rev) == 1 and rev[0]['lines'] == {'6000': (0.0, 250.0), '1000': (250.0, 0.0)}, rev
    # a second void is refused and posts nothing
    again = admin.post(f'{API}/expenses/{uid}/void', json={'reason': 'again'})
    assert again.status_code == 409 and again.get_json()['code'] == 'ALREADY_VOIDED'
    assert len(_journals(cid, 'reversal')) == 1
    assert _cash_balance(_balance_sheet(admin, SAME)) == 500.0
    assert _trial_balance(admin)['is_balanced'] is True
    lst = admin.get(f'{API}/expenses').get_json()['data']
    assert len(lst['expenses']) == 1 and lst['expenses'][0]['voided'] is True
    assert lst['total'] == 0.0, 'a voided expense must not count in the total'


def test_another_companys_expenses_are_invisible_and_cannot_be_voided():
    mine, my_cid, _p = H._new_shop()
    theirs, their_cid, _q = H._new_shop()
    assert _expense(theirs).status_code == 200
    their_uid = theirs.get(f'{API}/expenses').get_json()['data']['expenses'][0]['uid']
    # nothing of theirs shows in my list, even by account filter
    assert mine.get(f'{API}/expenses').get_json()['data']['expenses'] == []
    assert mine.get(f'{API}/expenses?account=6000').get_json()['data']['expenses'] == []
    # and my void of their uid is a 404 that posts nothing
    r = mine.post(f'{API}/expenses/{their_uid}/void', json={'reason': 'not mine'})
    assert r.status_code == 404
    assert _journals(their_cid, 'reversal') == [] and _journals(my_cid, 'reversal') == []
    # control: theirs still sees its own
    assert len(theirs.get(f'{API}/expenses').get_json()['data']['expenses']) == 1


@pytest.mark.parametrize('body,code', [
    ({'gl_account_code': '4000'}, 'INVALID_GL_ACCOUNT'),
    ({'gl_account_code': '3100'}, 'INVALID_GL_ACCOUNT'),      # drawings are a till choice, not an "expense"
    ({'gl_account_code': ''}, 'INVALID_GL_ACCOUNT'),
    ({'paid_via': 'crypto'}, 'INVALID_PAID_VIA'),
    ({'business_date': '2026-03-12'}, 'FUTURE_DATE'),        # the frozen day is 2026-03-11
    ({'business_date': 'yesterday'}, 'INVALID_DATE'),
])
def test_a_bad_expense_is_refused_and_posts_nothing(body, code):
    admin, cid, _pid = H._new_shop()
    r = _expense(admin, **body)
    assert r.status_code == 400, r.get_json()
    assert r.get_json()['code'] == code
    assert _journals(cid, 'expense') == []
    assert admin.get(f'{API}/expenses').get_json()['data']['expenses'] == []


@pytest.mark.parametrize('amount', [0, -5, 'abc', None])
def test_a_zero_or_bad_amount_is_refused(amount):
    admin, cid, _pid = H._new_shop()
    r = _expense(admin, amount=amount)
    assert r.status_code == 400, r.get_json()
    assert _journals(cid, 'expense') == []


def test_the_default_paid_via_and_date_are_office_cash_and_today():
    admin, cid, _pid = H._new_shop()
    r = admin.post(f'{API}/expenses', json={'gl_account_code': '6400', 'amount': 12.5})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['paid_via'] == 'cash_office' and d['business_date'] == SAME


def test_expense_authority_accountant_in_manager_and_cashier_out():
    admin, cid, _pid = H._new_shop()
    acct, _u = H._make_accountant(cid)
    mgr, _m = H._make_user('manager', cid)
    cashier, _c = H._make_user('cashier', cid)
    r = _expense(acct)
    assert r.status_code == 200, r.get_json()
    uid = r.get_json()['data']['uid']
    assert acct.get(f'{API}/expenses').status_code == 200
    assert acct.post(f'{API}/expenses/{uid}/void', json={'reason': 'x'}).status_code == 200
    for who in (mgr, cashier):
        assert who.get(f'{API}/expenses').status_code == 403
        assert _expense(who).status_code == 403
        assert who.post(f'{API}/expenses/{uid}/void', json={'reason': 'x'}).status_code == 403
    # the refusals wrote nothing: one expense, voided once
    assert len(_journals(cid, 'expense')) == 1 and len(_journals(cid, 'reversal')) == 1


def test_a_reader_of_the_books_can_list_expenses_but_not_record_or_void_them():
    """The deny half the Accountant/manager/cashier test cannot see: the Accountant holds BOTH
    books codes, a manager and a cashier hold NEITHER, so none of them separates `books.view`
    (read) from `books.record` (write). A user holding ONLY books.view must list and nothing else
    -- found by mutating create_expense's gate to books.view, which that test left green."""
    admin, cid, _pid = H._new_shop()
    uid_expense = _expense(admin).get_json()['data']['uid']
    reader, reader_id = H._make_user('manager', cid)
    H._plant(reader_id, 'retail.books.view')
    assert reader.get(f'{API}/expenses').status_code == 200
    assert _expense(reader).status_code == 403
    assert reader.post(f'{API}/expenses/{uid_expense}/void', json={'reason': 'x'}).status_code == 403
    # control: the same person, once given the record code, can write
    H._plant(reader_id, 'retail.books.record')
    assert _expense(reader).status_code == 200
    assert reader.post(f'{API}/expenses/{uid_expense}/void', json={'reason': 'x'}).status_code == 200
