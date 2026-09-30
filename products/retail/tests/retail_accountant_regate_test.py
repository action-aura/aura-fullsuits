"""Aura Retail -- P2S / registry v9: the ACCOUNTANT re-gates, both directions,
plus AUDIT-032 enforced at the point of use.

WHAT CHANGED (retail_api.py) AND WHY THIS FILE EXISTS
-----------------------------------------------------
Before registry v9 the money-out and books routes were owner-only or sat on
retail.employees, so a bookkeeper had to be given the keys to the whole shop.
The Accountant preset (`user_accounts.PRESET_ACCOUNTANT`) now holds
retail.books.view, retail.books.record, retail.reports and retail.cash.approve
-- and deliberately NOT retail.sell, retail.stock.adjust or retail.cash.close.
The routes were re-gated to match:

  customer_payment            retail.sell  OR  retail.books.record
  list_cash_sessions          retail.cash.close  OR  retail.cash.approve
  get_cash_session            retail.cash.close  OR  retail.cash.approve
  supplier_payment            retail.books.record
  pay_purchase_order          retail.books.record
  create_cheque (and the other cheque writes)   retail.books.record
  void_payment                retail.refund OR retail.books.record; the
                              supplier / PO half is books.record ONLY
  audit-log                   the owner, OR anyone holding retail.books.view
                              (read-only); manager and cashier stay OWNER_ONLY

Each one is proven twice: the person who must be let in is let in AND the
side effect is read back, and the person who must stay out is refused AND
nothing was written. A test that only asserts "the Accountant gets 200" would
pass against a route that has no gate at all.

AUDIT-032 AT THE POINT OF USE
-----------------------------
"A non-admin never holds retail.cash.close and retail.cash.approve together"
is checked when a permission is GRANTED. A permission that arrives by sync from
an older or forged computer never passes through that check. So the two drawer
routes enforce it where the permission is USED:

  * approve_cash_variance refuses 403 SELF_APPROVAL_FORBIDDEN when the caller
    is the person who ended the drawer and is not the owner.
  * close_cash_session never lets a non-owner's own close go straight to
    'closed' -- the variance stays pending ('ended').

The permission rows are PLANTED directly in the registry (exactly what a sync
apply does), never granted through the checked route.

Run (one file per process):
    pytest products/retail/tests/retail_accountant_regate_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix='aura_retail_acct_regate_'))
(DATA / 'database' / 'subsystems').mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE='1', AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop('AURA_DEV', None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code='AURA_RETAIL', platform='WINDOWS')
os.environ.pop('AURA_RETAIL_DEMO_MODE', None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config['TESTING'] = True
app.config['PROPAGATE_EXCEPTIONS'] = False

from commercial_runtime.identity import device_context, user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import (  # noqa: E402
    get_retail_conn, V16_SUPERSEDED_BRANCH_INDEX, V16_TERMINAL_INDEX,
)

API = '/api/sub/retail'
PASSWORD = 'AcctRegatePW1'

#: user_id -> login email, so a planted permission can be followed by a fresh
#: login (a session may carry its capability snapshot from login time).
_EMAILS = {}


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_device_cache():
    device_context._cached_device = None
    yield
    device_context._cached_device = None


@pytest.fixture(scope='module', autouse=True)
def _terminal_scoped_drawer_index():
    """Same scaffolding retail_drawer_terminal_scope_test.py uses: the branch-wide
    open-drawer index would make two drawers in one process impossible. A
    migrated database already carries the terminal index and this does nothing."""
    conn = get_retail_conn()
    try:
        names = {row[1] for row in conn.execute("PRAGMA index_list('cash_sessions')").fetchall()}
        if V16_TERMINAL_INDEX not in names:
            conn.execute(f'DROP INDEX IF EXISTS {V16_SUPERSEDED_BRANCH_INDEX}')
            conn.execute(
                f'CREATE UNIQUE INDEX IF NOT EXISTS {V16_TERMINAL_INDEX} '
                "ON cash_sessions(company_id, terminal_id) WHERE status='open'")
            conn.commit()
    finally:
        conn.close()


# -- harness -----------------------------------------------------------------

def _insert_user(conn, role, company_id, prefix):
    email = f'{prefix}-{role}-{uuid.uuid4().hex[:10]}@test.local'
    user_id = str(uuid.uuid4())
    conn.execute(
        'INSERT INTO users (id, uid, company_id, employee_id, email, password_hash, role, status, '
        'require_password_change) VALUES (?,?,?,?,?,?,?,?,0)',
        (user_id, str(uuid.uuid4()), company_id, f'EMP-{uuid.uuid4().hex[:6].upper()}', email,
         hash_password(PASSWORD), role, 'active'))
    conn.execute('INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)',
                 (str(uuid.uuid4()), user_id, 'retail', 'full'))
    _EMAILS[user_id] = email
    return user_id


def _login(user_id):
    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': _EMAILS[user_id], 'password': PASSWORD})
    assert r.status_code == 200, r.get_data(as_text=True)
    return client


def _make_user(role, company_id, capabilities=None):
    """(client, user_id). `capabilities` overrides individual capability rows
    AFTER the production seeding helper wrote the role defaults."""
    conn = registry_conn()
    try:
        user_id = _insert_user(conn, role, company_id, 'acct')
        user_accounts.seed_capabilities_for_user(conn, user_id, role)
        for code, level in (capabilities or {}).items():
            conn.execute('UPDATE user_permissions SET access_level=? WHERE user_id=? AND subsystem=?',
                         (level, user_id, code))
        conn.commit()
    finally:
        conn.close()
    return _login(user_id), user_id


def _make_accountant(company_id):
    """A real Accountant: a manager account put through the production
    `apply_preset`, then a fresh login. Returns (client, user_id)."""
    conn = registry_conn()
    try:
        user_id = _insert_user(conn, 'manager', company_id, 'acct')
        user_accounts.seed_capabilities_for_user(conn, user_id, 'manager')
        user_accounts.apply_preset(conn, user_id, user_accounts.PRESET_ACCOUNTANT, emit_sync=False)
        conn.commit()
    finally:
        conn.close()
    return _login(user_id), user_id


def _plant(user_id, code, level='full'):
    """Write a capability row straight into the registry, bypassing the
    AUDIT-032 grant-time check -- what a permission arriving by sync does."""
    conn = registry_conn()
    try:
        conn.execute('DELETE FROM user_permissions WHERE user_id=? AND subsystem=?', (user_id, code))
        conn.execute('INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)',
                     (str(uuid.uuid4()), user_id, code, level))
        conn.commit()
    finally:
        conn.close()


def _new_shop(price=100.0):
    """A company with an admin, a product and stock. Returns (admin, cid, pid)."""
    company_id = str(uuid.uuid4())
    admin, _uid = _make_user('admin', company_id)
    r = admin.post(f'{API}/products', json={
        'name': 'Regate Widget', 'sku': f'RG-{uuid.uuid4().hex[:8]}',
        'cost_price': price / 2, 'sell_price': price, 'tax_rate': 0, 'initial_stock': 5000})
    assert r.status_code == 200, r.get_json()
    return admin, company_id, r.get_json()['data']['id']


def _customer(admin):
    r = admin.post(f'{API}/customers', json={'name': f'Regate Customer {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _supplier(admin):
    r = admin.post(f'{API}/suppliers', json={'name': f'Regate Supplier {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _purchase_order(admin, pid, supplier_id):
    r = admin.post(f'{API}/purchase-orders', json={
        'supplier_id': supplier_id,
        'items': [{'product_id': pid, 'quantity': 1, 'unit_cost': 15}]})
    assert r.status_code == 200, r.get_json()
    conn = get_retail_conn()
    try:
        row = conn.execute('SELECT id FROM purchase_orders WHERE supplier_id=? ORDER BY id DESC LIMIT 1',
                           (supplier_id,)).fetchone()
    finally:
        conn.close()
    return row['id']


def _open(client, opening_float=100.0):
    return client.post(f'{API}/cash-sessions/open', json={'opening_float': opening_float})


def _sell_cash(client, pid, amount):
    return client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': amount / 100.0}],
        'amount_paid': amount, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4())})


def _end(client, sid, counted):
    return client.post(f'{API}/cash-sessions/{sid}/close', json={'closing_float_counted': counted})


def _approve(client, sid):
    return client.post(f'{API}/cash-sessions/{sid}/approve', json={})


def _session_row(sid):
    conn = get_retail_conn()
    try:
        return dict(conn.execute('SELECT * FROM cash_sessions WHERE id=?', (sid,)).fetchone())
    finally:
        conn.close()


def _count(table, company_id, where='1=1', params=()):
    conn = get_retail_conn()
    try:
        return conn.execute(f'SELECT COUNT(*) FROM {table} WHERE company_id=? AND {where}',
                            (company_id, *params)).fetchone()[0]
    finally:
        conn.close()


def _cashier_ended_drawer_with_variance(admin, company_id, pid):
    """A cashier (close yes, approve no) opens a drawer, rings a 100 cash sale
    and ends it counting 50 short. Returns (cashier_client, session_id). The
    variance is what makes 'pending approval' a real state."""
    cashier, _ = _make_user('cashier', company_id, {'retail.cash.close': 'full', 'retail.cash.approve': 'none'})
    r = _open(cashier)
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()['data']['id']
    assert _sell_cash(cashier, pid, 100).status_code == 200
    r = _end(cashier, sid, 150.0)
    assert r.status_code == 200, r.get_json()
    assert _session_row(sid)['status'] == 'ended', 'precondition: a cashier who cannot approve leaves the variance pending'
    return cashier, sid


# -- 0. the fixture itself ---------------------------------------------------

def test_the_accountant_fixture_holds_the_books_authorities_and_none_of_the_shop_floor_ones():
    """ANTI-VACUITY for every test below: if apply_preset did nothing, the
    'Accountant is refused' tests would be a manager being refused and the
    'Accountant is let in' tests would be a manager being let in."""
    _admin, cid, _pid = _new_shop()
    _client, uid = _make_accountant(cid)
    conn = registry_conn()
    try:
        held = user_accounts.granted_capability_codes(conn, uid)
    finally:
        conn.close()
    for code in (user_accounts.CAP_BOOKS_VIEW, user_accounts.CAP_BOOKS_RECORD,
                 user_accounts.CAP_REPORTS, user_accounts.CAP_CASH_APPROVE):
        assert code in held, (code, sorted(held))
    for code in (user_accounts.CAP_SELL, user_accounts.CAP_STOCK_ADJUST, user_accounts.CAP_CASH_CLOSE,
                 user_accounts.CAP_EMPLOYEES):
        assert code not in held, (code, sorted(held))


# -- 1. customer_payment: sell OR books.record ------------------------------

def test_customer_payment_lets_the_accountant_in_and_keeps_out_someone_with_neither():
    admin, cid, _pid = _new_shop()
    accountant, _ = _make_accountant(cid)
    nobody, _ = _make_user('cashier', cid, {'retail.sell': 'none'})
    cust = _customer(admin)

    before = _count('payments', cid, "party_type='customer' AND party_id=?", (cust,))
    r = nobody.post(f'{API}/customers/{cust}/payments', json={'amount': 20})
    assert r.status_code == 403, r.get_json()
    assert _count('payments', cid, "party_type='customer' AND party_id=?", (cust,)) == before, \
        'a refused request must write nothing'

    r = accountant.post(f'{API}/customers/{cust}/payments', json={'amount': 20})
    assert r.status_code == 200, r.get_json()
    assert _count('payments', cid, "party_type='customer' AND party_id=?", (cust,)) == before + 1


# -- 2. cash-session reads: close OR approve ---------------------------------

def test_cash_session_reads_let_the_accountant_in_and_keep_out_someone_with_neither():
    admin, cid, pid = _new_shop()
    _cashier, sid = _cashier_ended_drawer_with_variance(admin, cid, pid)
    accountant, _ = _make_accountant(cid)
    neither, _ = _make_user('cashier', cid, {'retail.cash.close': 'none', 'retail.cash.approve': 'none'})

    for path in (f'{API}/cash-sessions', f'{API}/cash-sessions/{sid}'):
        r = neither.get(path)
        assert r.status_code == 403, (path, r.get_json())
        r = accountant.get(path)
        assert r.status_code == 200, (path, r.get_json())


# -- 3. approve: the Accountant approves a cashier's variance ----------------

def test_the_accountant_approves_a_cashiers_variance_and_a_cashier_cannot():
    admin, cid, pid = _new_shop()
    cashier, sid = _cashier_ended_drawer_with_variance(admin, cid, pid)
    accountant, _ = _make_accountant(cid)

    r = _approve(cashier, sid)
    assert r.status_code == 403, r.get_json()
    assert _session_row(sid)['status'] == 'ended', 'the refused approve must not close the drawer'

    r = _approve(accountant, sid)
    assert r.status_code == 200, r.get_json()
    assert _session_row(sid)['status'] == 'closed'


# -- 4. supplier money out: books.record only --------------------------------

def test_supplier_payment_lets_the_accountant_in_and_keeps_a_manager_out():
    admin, cid, _pid = _new_shop()
    accountant, _ = _make_accountant(cid)
    manager, _ = _make_user('manager', cid)
    sup = _supplier(admin)

    before = _count('payments', cid, "party_type='supplier' AND party_id=?", (sup,))
    r = manager.post(f'{API}/suppliers/{sup}/payments', json={'amount': 20})
    assert r.status_code == 403, r.get_json()
    assert _count('payments', cid, "party_type='supplier' AND party_id=?", (sup,)) == before

    r = accountant.post(f'{API}/suppliers/{sup}/payments', json={'amount': 20})
    assert r.status_code == 200, r.get_json()
    assert _count('payments', cid, "party_type='supplier' AND party_id=?", (sup,)) == before + 1


def test_pay_purchase_order_lets_the_accountant_in_and_keeps_a_manager_out():
    admin, cid, pid = _new_shop()
    accountant, _ = _make_accountant(cid)
    manager, _ = _make_user('manager', cid)
    po = _purchase_order(admin, pid, _supplier(admin))

    def _paid():
        conn = get_retail_conn()
        try:
            return conn.execute('SELECT COALESCE(amount_paid,0) FROM purchase_orders WHERE id=?', (po,)).fetchone()[0]
        finally:
            conn.close()

    r = manager.post(f'{API}/purchase-orders/{po}/pay', json={'amount': 5})
    assert r.status_code == 403, r.get_json()
    assert _paid() == 0, 'a refused request must not move money'

    r = accountant.post(f'{API}/purchase-orders/{po}/pay', json={'amount': 5})
    assert r.status_code == 200, r.get_json()
    assert _paid() == 5


def test_cheques_let_the_accountant_in_and_keep_a_manager_out():
    admin, cid, _pid = _new_shop()
    accountant, _ = _make_accountant(cid)
    manager, _ = _make_user('manager', cid)
    cust = _customer(admin)

    def _body():
        return {'direction': 'in', 'party_id': cust, 'amount': 25, 'due_date': '2027-01-15',
                'cheque_number': f'AC-{uuid.uuid4().hex[:6]}'}

    before = _count('cheques', cid)
    r = manager.post(f'{API}/cheques', json=_body())
    assert r.status_code == 403, r.get_json()
    assert _count('cheques', cid) == before

    r = accountant.post(f'{API}/cheques', json=_body())
    assert r.status_code == 201, r.get_json()
    assert _count('cheques', cid) == before + 1


# -- 5. void_payment: refund OR books.record; the supplier half books only ----

def test_void_payment_supplier_half_is_books_record_only_and_the_customer_half_keeps_refund():
    admin, cid, _pid = _new_shop()
    accountant, _ = _make_accountant(cid)
    refund_only, _ = _make_user('cashier', cid, {'retail.refund': 'full'})
    sup, cust = _supplier(admin), _customer(admin)

    def _payment(url):
        r = admin.post(url, json={'amount': 20})
        assert r.status_code == 200, r.get_json()
        conn = get_retail_conn()
        try:
            return conn.execute('SELECT id FROM payments WHERE company_id=? AND reference=?',
                                (cid, r.get_json()['data']['reference'])).fetchone()['id']
        finally:
            conn.close()

    def _status(pay_id):
        conn = get_retail_conn()
        try:
            return conn.execute('SELECT status FROM payments WHERE id=?', (pay_id,)).fetchone()['status']
        finally:
            conn.close()

    supplier_pay = _payment(f'{API}/suppliers/{sup}/payments')
    r = refund_only.post(f'{API}/payments/{supplier_pay}/void', json={'reason': 'refund-only must not undo money paid out'})
    assert r.status_code == 403, r.get_json()
    assert _status(supplier_pay) == 'active'
    r = accountant.post(f'{API}/payments/{supplier_pay}/void', json={'reason': 'accountant reverses a payment'})
    assert r.status_code == 200, r.get_json()
    assert _status(supplier_pay) != 'active'

    customer_pay = _payment(f'{API}/customers/{cust}/payments')
    r = refund_only.post(f'{API}/payments/{customer_pay}/void', json={'reason': 'refund authority still voids a receipt'})
    assert r.status_code == 200, r.get_json()
    assert _status(customer_pay) != 'active'


# -- 6. what the Accountant must NOT be able to do ---------------------------

def test_the_accountant_is_refused_the_shop_floor_and_the_permission_editor():
    admin, cid, pid = _new_shop()
    cashier, sid = _cashier_ended_drawer_with_variance(admin, cid, pid)
    _target, cashier_uid = _make_user('cashier', cid)
    accountant, _ = _make_accountant(cid)

    before_sales = _count('sales', cid)
    r = _sell_cash(accountant, pid, 100)
    assert r.status_code == 403, ('POST sales', r.get_json())
    assert _count('sales', cid) == before_sales

    r = accountant.post(f'{API}/products/{pid}/stock-adjust', json={'quantity': 5, 'reason': 'accountant'})
    assert r.status_code == 403, ('stock-adjust', r.get_json())

    assert _open(accountant).status_code == 403, 'cash-sessions/open'
    r = _end(accountant, sid, 150.0)
    assert r.status_code == 403, ('cash-sessions/<id>/close', r.get_json())
    assert _session_row(sid)['status'] == 'ended'

    r = accountant.post(f'/api/admin/employees/{cashier_uid}/permissions',
                        json={'subsystem': 'retail.employees', 'access_level': 'full'})
    assert r.status_code == 403, ('permissions editor', r.get_json())


# -- 7. AUDIT-032 at the point of use ----------------------------------------

def test_approve_refuses_the_person_who_ended_the_drawer_and_lets_somebody_else_in():
    """The permission is PLANTED after the drawer was ended, the way a synced
    grant arrives. Person P ended the drawer and now holds approve: refused with
    SELF_APPROVAL_FORBIDDEN (a code the capability decorator's 403 does not
    carry, so this cannot be the decorator masquerading as the guard), and the
    drawer is still pending. A DIFFERENT approver then gets 200."""
    admin, cid, pid = _new_shop()
    p, p_uid = _make_user('manager', cid, {'retail.cash.close': 'full', 'retail.cash.approve': 'none'})
    r = _open(p)
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()['data']['id']
    assert _sell_cash(p, pid, 100).status_code == 200
    assert _end(p, sid, 150.0).status_code == 200
    row = _session_row(sid)
    assert row['status'] == 'ended', row

    _plant(p_uid, user_accounts.CAP_CASH_APPROVE, 'full')
    p = _login(p_uid)
    r = _approve(p, sid)
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'SELF_APPROVAL_FORBIDDEN', r.get_json()
    assert _session_row(sid)['status'] == 'ended', 'the refused self-approval must leave the variance pending'

    other, _ = _make_accountant(cid)
    r = _approve(other, sid)
    assert r.status_code == 200, r.get_json()
    assert _session_row(sid)['status'] == 'closed'


def test_the_owner_may_still_close_and_approve_their_own_drawer():
    """The exemption. Owner closes: straight to 'closed'. The row is then put
    back to 'ended' (ended_by stays the owner) so the approve route itself is
    exercised for the owner: still 200."""
    admin, cid, pid = _new_shop()
    r = _open(admin)
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()['data']['id']
    assert _sell_cash(admin, pid, 100).status_code == 200
    r = _end(admin, sid, 150.0)
    assert r.status_code == 200, r.get_json()
    assert _session_row(sid)['status'] == 'closed'

    conn = get_retail_conn()
    try:
        conn.execute("UPDATE cash_sessions SET status='ended', closed_by=NULL, closed_at=NULL WHERE id=?", (sid,))
        conn.commit()
    finally:
        conn.close()
    r = _approve(admin, sid)
    assert r.status_code == 200, r.get_json()
    assert _session_row(sid)['status'] == 'closed'


def test_a_non_owner_holding_close_and_approve_cannot_close_straight_to_closed():
    """The CLOSE half. Both permissions planted (a synced grant that skipped the
    grant-time check). The person closes their own drawer with a variance: the
    drawer must stay 'ended' and the report must say the variance is not yet
    verified. The same person's approve is then refused, a different approver's
    is accepted."""
    admin, cid, pid = _new_shop()
    p, p_uid = _make_user('manager', cid, {'retail.cash.close': 'full', 'retail.cash.approve': 'none'})
    _plant(p_uid, user_accounts.CAP_CASH_APPROVE, 'full')
    p = _login(p_uid)
    r = _open(p)
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()['data']['id']
    assert _sell_cash(p, pid, 100).status_code == 200

    r = _end(p, sid, 150.0)
    assert r.status_code == 200, r.get_json()
    report = r.get_json()['data']['report']
    assert _session_row(sid)['status'] == 'ended', (
        'a non-owner must never self-approve their own close', _session_row(sid)['status'])
    assert not report.get('self_approved'), report

    assert report.get('variance_status') != 'approved', report
    r = _approve(p, sid)
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'SELF_APPROVAL_FORBIDDEN', r.get_json()
    other, _ = _make_accountant(cid)
    assert _approve(other, sid).status_code == 200
    assert _session_row(sid)['status'] == 'closed'
    # The allow half (owner's own close settles) is pinned by
    # test_the_owner_may_still_close_and_approve_their_own_drawer above.


def test_approve_refuses_the_person_who_OPENED_the_drawer_though_somebody_else_closed_it():
    """AUDIT-032, the opener half (F1/M4). P opens the drawer and rings the
    sale; Q counts it 50 short; P (holding approve by a synced grant) is asked
    to accept the shortfall of P's OWN drawer. `_same_actor` used to look at the
    ender alone, so P sailed through as "not the closer". Refused now, the
    variance stays pending, and the allow-half still works: a third person
    approves, and the response says self_approved is False for them."""
    admin, cid, pid = _new_shop()
    p, p_uid = _make_user('manager', cid, {'retail.cash.close': 'full', 'retail.cash.approve': 'none'})
    q, _q_uid = _make_user('manager', cid, {'retail.cash.close': 'full', 'retail.cash.approve': 'none'})
    r = _open(p)
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()['data']['id']
    assert _sell_cash(p, pid, 100).status_code == 200
    r = _end(q, sid, 150.0)
    assert r.status_code == 200, r.get_json()
    row = _session_row(sid)
    assert row['status'] == 'ended', row
    assert str(row['opened_by']) == str(p_uid) and str(row['opened_by']) != str(row['closed_by']), (
        'precondition: opener and ender are different people', dict(row))

    _plant(p_uid, user_accounts.CAP_CASH_APPROVE, 'full')
    p = _login(p_uid)
    r = _approve(p, sid)
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'SELF_APPROVAL_FORBIDDEN', r.get_json()
    assert _session_row(sid)['status'] == 'ended', 'the refused self-approval must leave the variance pending'

    other, _ = _make_accountant(cid)
    r = _approve(other, sid)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['self_approved'] is False, r.get_json()
    assert _session_row(sid)['status'] == 'closed'


def test_the_owner_may_approve_a_drawer_they_opened_that_somebody_else_closed():
    """Allow-half of the opener rule: the owner stays exempt (a one-person
    shop has no second authority), and the response still surfaces
    self_approved=True."""
    admin, cid, pid = _new_shop()
    q, _q_uid = _make_user('manager', cid, {'retail.cash.close': 'full', 'retail.cash.approve': 'none'})
    r = _open(admin)
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()['data']['id']
    assert _sell_cash(admin, pid, 100).status_code == 200
    assert _end(q, sid, 150.0).status_code == 200
    assert _session_row(sid)['status'] == 'ended'
    r = _approve(admin, sid)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['self_approved'] is True, r.get_json()
    assert _session_row(sid)['status'] == 'closed'
