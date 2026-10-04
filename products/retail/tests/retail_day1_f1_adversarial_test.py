"""Adversarial verifier round 2 for DAY-1 review F1 (returns.tax_refunded).
Target: 251c1aa1 + 12f5b09b. Each test named *_RED_* was RED on 12f5b09b (a
gap); DAY-1d round 3 (R2, R3) turned four of them green. The one that stays
red, the two-device offline rounding case, is a strict xfail (residual R4).
The rest are probes that held.

Run (repo venv, every AURA_* env var removed), one file per process:
    pytest products/retail/tests/retail_day1_f1_adversarial_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_day1f1adv_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA), AURA_SITE_RELAY_ENABLED="0")
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from core.retail import general_ledger as gl  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    email = f'f1adv-{uuid.uuid4().hex[:8]}@test.local'
    password = 'F1AdvPW12345'  # pragma: allowlist secret
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


def _product(c, price=100.0, tax_rate=0):
    r = c.post(f'{API}/products', json={
        'name': 'F1 Widget', 'sku': f'F1-{uuid.uuid4().hex[:8]}', 'cost_price': price / 2,
        'sell_price': price, 'tax_rate': tax_rate, 'initial_stock': 500})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _customer(c):
    r = c.post(f'{API}/customers', json={'name': f'F1 Customer {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    cid = r.get_json()['data']['id']
    conn = get_retail_conn()
    conn.execute("UPDATE customers SET credit_mode='unlimited', credit_limit=1000000 WHERE id=?", (cid,))
    conn.commit(); conn.close()
    return cid


def _sale(c, items, **extra):
    body = dict({'items': items, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4())}, **extra)
    r = c.post(f'{API}/sales', json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _return(c, sale_id, items, method='cash'):
    r = c.post(f'{API}/returns', json={'sale_id': sale_id, 'items': items,
                                       'refund_method': method, 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _tb(c):
    r = c.get(f'{API}/reports/trial-balance')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _acct(data, code):
    rows = [a for a in data['accounts'] if a['code'] == code]
    return rows[0]['balance'] if rows else 0.0


def _tax_report(c):
    r = c.get(f'{API}/reports/summary?days=1')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['tax_collected']


def _loyalty(c, cust):
    r = c.get(f'{API}/customers/{cust}/loyalty')
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    return d.get('balance', d.get('points', d))


def _stored(sale_id):
    conn = get_retail_conn()
    try:
        return [r[0] for r in conn.execute(
            "SELECT tax_refunded FROM returns WHERE sale_id=? ORDER BY id", (sale_id,)).fetchall()]
    finally:
        conn.close()


# ── FINDING A: a NULL (pre-extension / older-peer) prior return counts as 0 ──

def test_RED_pre_upgrade_partial_return_then_completing_return_does_not_over_reverse(client):
    """2 x 100 @16% (232, tax 32). Return 1 unit; that row is what every return
    made BEFORE this build looks like (tax_refunded NULL -- no backfill, by
    design). After upgrade the 2nd unit comes back. create_return's prior =
    SUM(COALESCE(tax_refunded,0)) treats the NULL row as 'reversed nothing', so
    the completing return takes the WHOLE 32, while the report counts the NULL
    row by its fallback (16) on top: 48 reversed of a 32 tax, report -16."""
    pid = _product(client, 100.0, tax_rate=16)
    sale = _sale(client, [{'product_id': pid, 'quantity': 2}])
    assert sale['tax_amount'] == 32.0
    _return(client, sale['id'], [{'product_id': pid, 'quantity': 1}])
    conn = get_retail_conn()
    conn.execute("UPDATE returns SET tax_refunded=NULL WHERE sale_id=?", (sale['id'],))
    conn.commit(); conn.close()
    _return(client, sale['id'], [{'product_id': pid, 'quantity': 1}])
    stored = _stored(sale['id'])
    report = _tax_report(client)
    assert report >= 0.0 and abs(report) < 0.001, (
        f'fully returned sale: tax report must be 0, got {report}; stored per return {stored} '
        f'(the completing return took the whole 32 although 16 was already reversed by the NULL row)')


def _sale_uid(sale_id):
    conn = get_retail_conn()
    try:
        return conn.execute("SELECT uid FROM sales WHERE id=?", (sale_id,)).fetchone()[0]
    finally:
        conn.close()


def _branch(sale_id):
    conn = get_retail_conn()
    try:
        bid = conn.execute("SELECT branch_id FROM sales WHERE id=?", (sale_id,)).fetchone()[0]
        buid = conn.execute("SELECT uid FROM branches WHERE id=?", (bid,)).fetchone()[0]
        return bid, buid
    finally:
        conn.close()


def _apply(cid, ev):
    service = SyncService(client_factory=lambda: None, get_conn=get_retail_conn,
                          local_company_id_provider=lambda: cid)
    conn = get_retail_conn()
    try:
        service._apply_event(conn, ev, local_company_id=cid)
        conn.commit()
    finally:
        conn.close()


def _apply_older_peer_return(c, sale, pid, qty, refund, with_tax_key=None):
    """The real SyncService._apply_event, 'return' + 'return_item' events, as an
    OLDER device (no tax_refunded key) or a current one (with_tax_key)."""
    bid, buid = _branch(sale['id'])
    ruid = str(uuid.uuid4())
    payload = {'uid': ruid, 'sale_uid': _sale_uid(sale['id']), 'return_number': f'RET-PEER-{ruid[:8]}',
               'branch_id': bid, 'branch_uid': buid, 'cashier': 'POS', 'reason': 'peer',
               'refund_method': 'cash', 'refund_amount': refund, 'tender_refund_amount': refund,
               'ar_forgiven_amount': 0.0, 'store_credit_amount': 0.0,
               'status': 'completed', 'created_at': '2026-10-03 10:00:00',
               'actor_user_uid': None, 'terminal_id': None, 'created_at_utc': '2026-10-03T07:00:00Z'}
    if with_tax_key is not None:
        payload['tax_refunded'] = with_tax_key
    _apply(c.test_company_id, {'entity_type': 'return', 'event_type': 'create', 'payload': payload})
    _apply(c.test_company_id, {'entity_type': 'return_item', 'event_type': 'create', 'payload': {
        'uid': str(uuid.uuid4()), 'return_uid': ruid, 'product_id': pid, 'quantity': qty,
        'unit_price': refund / qty, 'line_total': refund}})
    return ruid


def test_RED_older_peer_partial_return_then_local_completing_return_does_not_over_reverse(client):
    """Same shape via sync: device A (older build) returned 1 of 2 units (no
    tax_refunded key -> NULL here). This device rings the other unit: it is the
    completing return, prior counts A's row as 0, it reverses all 32."""
    pid = _product(client, 100.0, tax_rate=16)
    sale = _sale(client, [{'product_id': pid, 'quantity': 2}])
    _apply_older_peer_return(client, sale, pid, 1, 116.0)
    _return(client, sale['id'], [{'product_id': pid, 'quantity': 1}])
    stored = _stored(sale['id'])
    report = _tax_report(client)
    assert abs(report) < 0.001, f'fully returned sale must report 0 tax, got {report}; stored {stored}'


def test_synced_return_with_tax_key_makes_this_devices_report_equal_the_senders(client):
    """PROBE (expected GREEN): B's report reads A's stored figure, mixed rate."""
    p0 = _product(client, 100.0, tax_rate=0)
    p16 = _product(client, 100.0, tax_rate=16)
    sale = _sale(client, [{'product_id': p0, 'quantity': 1}, {'product_id': p16, 'quantity': 1}])
    _apply_older_peer_return(client, sale, p16, 1, 116.0, with_tax_key=16.0)
    assert _tax_report(client) == 0.0
    _return(client, sale['id'], [{'product_id': p0, 'quantity': 1}])
    assert _stored(sale['id'])[-1] == 0.0
    assert _tax_report(client) == 0.0


# ── FINDING B: the settled cap strands tax; result depends on return ORDER ───

def _points_customer(c, points):
    cust = _customer(c)
    r = c.post(f'{API}/settings/credit', json={'loyalty_point_value': 1})
    assert r.status_code == 200, r.get_json()
    conn = get_retail_conn()
    conn.execute("INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type,created_by) "
                 "VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), c.test_company_id, cust, points, 'adjust', 'f1'))
    conn.commit(); conn.close()
    return cust


@pytest.mark.parametrize('order', ['taxed_first', 'untaxed_first'])
def test_RED_full_return_of_points_and_cash_sale_leaves_no_tax_whatever_the_order(client, order):
    """16% line 116 + 0% line 100 = 216, tax 16; 116 paid in points, 100 cash.
    Both lines come back; the points are given back and the cash refunded, so
    the sale is fully undone. Taxed-line-first: tender covers it, 16 reversed.
    Untaxed-line-first: the 0% line drains the tender pool, the taxed line then
    settles 0 and its tax is capped to 0 -> 16 of Tax Payable (and of the tax
    report) stays owed forever on a sale that no longer exists."""
    cust = _points_customer(client, 116)
    p16 = _product(client, 100.0, tax_rate=16)
    p0 = _product(client, 100.0, tax_rate=0)
    sale = _sale(client, [{'product_id': p16, 'quantity': 1}, {'product_id': p0, 'quantity': 1}],
                 customer_id=cust, points_redeemed=116, amount_paid=100)
    assert sale['total'] == 216.0 and sale['tax_amount'] == 16.0, sale
    seq = [p16, p0] if order == 'taxed_first' else [p0, p16]
    for p in seq:
        _return(client, sale['id'], [{'product_id': p, 'quantity': 1}])
    tb = _tb(client)
    assert tb['is_balanced'] is True
    assert _acct(tb, gl.ACCOUNT_TAX_PAYABLE) == _tax_report(client)  # agreement holds either way
    assert _acct(tb, gl.ACCOUNT_TAX_PAYABLE) == 0.0, (
        f'[{order}] fully returned (points given back: ledger now {_loyalty(client, cust)}), '
        f'yet GL Tax Payable {_acct(tb, gl.ACCOUNT_TAX_PAYABLE)} / report {_tax_report(client)}; '
        f'stored per return {_stored(sale["id"])}')
    # DAY-1d round 3 R3: the points the sale redeemed were expensed at sale time;
    # giving them back must book the matching credit, so a fully undone sale
    # leaves NO loyalty expense (measured, not just read, per the round-3 plan).
    assert _acct(tb, gl.ACCOUNT_LOYALTY_EXPENSE) == 0.0, (
        f'[{order}] Loyalty Program Expense {_acct(tb, gl.ACCOUNT_LOYALTY_EXPENSE)} after full return')


def test_RED_fully_points_paid_sale_fully_returned_leaves_no_tax(client):
    """116 (tax 16) paid entirely in points; full return gives the points back.
    settled = 0, so tax_refunded = 0: Tax Payable and the report keep 16."""
    cust = _points_customer(client, 116)
    pid = _product(client, 100.0, tax_rate=16)
    sale = _sale(client, [{'product_id': pid, 'quantity': 1}], customer_id=cust,
                 points_redeemed=116, amount_paid=0)
    assert sale['total'] == 116.0
    _return(client, sale['id'], [{'product_id': pid, 'quantity': 1}])
    tb = _tb(client)
    assert _acct(tb, gl.ACCOUNT_TAX_PAYABLE) == _tax_report(client)
    assert _acct(tb, gl.ACCOUNT_TAX_PAYABLE) == 0.0, (
        f'points back ({_loyalty(client, cust)}), goods back, Tax Payable still '
        f'{_acct(tb, gl.ACCOUNT_TAX_PAYABLE)}; stored {_stored(sale["id"])}')
    assert tb['is_balanced'] is True
    assert _acct(tb, gl.ACCOUNT_LOYALTY_EXPENSE) == 0.0, (
        f'Loyalty Program Expense {_acct(tb, gl.ACCOUNT_LOYALTY_EXPENSE)} after the points were given back')


# ── PROBES expected GREEN (what held) ─────────────────────────────────────────

@pytest.mark.parametrize('seq', [
    [('t', 1), ('z', 2), ('o', 1), ('t', 2), ('z', 1)],
    [('z', 3), ('o', 1), ('t', 3)],
    [('o', 1), ('t', 1), ('t', 1), ('z', 1), ('t', 1), ('z', 2)],
])
def test_mixed_rate_three_lines_pieces_any_order_ends_exact(client, seq):
    """0% x3 @7.30, 16% x3 @3.33, 8% x1 @11.11 (USD 2dp): returned in pieces in
    several orders. Every intermediate: GL == report, never negative; end: 0."""
    pz = _product(client, 7.30, tax_rate=0)
    pt = _product(client, 3.33, tax_rate=16)
    po = _product(client, 11.11, tax_rate=8)
    ids = {'z': pz, 't': pt, 'o': po}
    sale = _sale(client, [{'product_id': pz, 'quantity': 3}, {'product_id': pt, 'quantity': 3},
                          {'product_id': po, 'quantity': 1}])
    for k, q in seq:
        _return(client, sale['id'], [{'product_id': ids[k], 'quantity': q}])
        tb = _tb(client)
        assert tb['is_balanced'] is True
        g = _acct(tb, gl.ACCOUNT_TAX_PAYABLE)
        assert g == _tax_report(client) and g >= 0, (g, _tax_report(client), _stored(sale['id']))
    assert _acct(_tb(client), gl.ACCOUNT_TAX_PAYABLE) == 0.0, _stored(sale['id'])


def test_jod_tiny_prices_many_returns_exact(client):
    """JOD 3dp, 7 x 0.013 @16% and 5 x 0.007 @16%, returned one unit at a time."""
    a = _product(client, 0.013, tax_rate=16)
    b = _product(client, 0.007, tax_rate=16)
    sale = _sale(client, [{'product_id': a, 'quantity': 7}, {'product_id': b, 'quantity': 5}])
    for p, n in ((a, 7), (b, 5)):
        for _ in range(n):
            _return(client, sale['id'], [{'product_id': p, 'quantity': 1}])
            g = _acct(_tb(client), gl.ACCOUNT_TAX_PAYABLE)
            assert g == _tax_report(client) and g >= 0
    assert _acct(_tb(client), gl.ACCOUNT_TAX_PAYABLE) == 0.0, _stored(sale['id'])


def test_a_return_beyond_what_was_sold_is_refused(client):
    pid = _product(client, 10.0, tax_rate=16)
    sale = _sale(client, [{'product_id': pid, 'quantity': 2}])
    _return(client, sale['id'], [{'product_id': pid, 'quantity': 1}])
    r = client.post(f'{API}/returns', json={'sale_id': sale['id'], 'items': [{'product_id': pid, 'quantity': 2}],
                                            'refund_method': 'cash', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 400


def test_credit_sale_paid_off_elsewhere_store_credit_path_reverses_full_tax(client):
    """On account 116 (tax 16), customer pays 116 on account, then returns:
    settled via store credit = 116, so all 16 reversed."""
    cust = _customer(client)
    pid = _product(client, 100.0, tax_rate=16)
    sale = _sale(client, [{'product_id': pid, 'quantity': 1}], customer_id=cust,
                 payment_method='credit', amount_paid=0)
    assert client.post(f'{API}/customers/{cust}/payments', json={'amount': 116.0, 'method': 'cash'}).status_code == 200
    _return(client, sale['id'], [{'product_id': pid, 'quantity': 1}])
    tb = _tb(client)
    assert _acct(tb, gl.ACCOUNT_TAX_PAYABLE) == _tax_report(client) == 0.0, _stored(sale['id'])


# ── FINDING D (LOW): two devices each return part of a sale while apart ───────

@pytest.mark.xfail(
    strict=True,
    reason="residual R4 (DAY-1d round 3, LOW, not fixed): two tills each returning part of "
           "one sale while offline cannot know the sale is complete (completion is only "
           "known locally), so 1 minor unit of tax can be stranded or over-reversed. "
           "strict=True: this test must start FAILING-AS-XPASS the day it is fixed, so the "
           "marker gets removed. See ROADMAP.")
def test_RED_two_devices_each_returning_half_offline_strand_no_rounding_residue(client):
    """JOD 2 x 0.103 @16%: line tax 0.033, one unit's tax rounds DOWN to 0.016.
    This till returns one unit (not completing in its view: 0.016). Another
    till, apart, returned the other unit -- also not completing in ITS view --
    and its return arrives by sync carrying 0.016. Both units are back, 0.032 of
    0.033 reversed: 1 fil of tax stays owed on a fully returned sale, and no later
    return can ever claim it (the completing-return remainder only fires on the
    device that rings the last unit while already knowing about the others)."""
    pid = _product(client, 0.103, tax_rate=16)
    sale = _sale(client, [{'product_id': pid, 'quantity': 2}])
    assert sale['tax_amount'] == 0.033, sale
    _return(client, sale['id'], [{'product_id': pid, 'quantity': 1}])
    _apply_older_peer_return(client, sale, pid, 1, 0.119, with_tax_key=0.016)
    assert abs(_tax_report(client)) < 0.0005, (_tax_report(client), _stored(sale['id']))
