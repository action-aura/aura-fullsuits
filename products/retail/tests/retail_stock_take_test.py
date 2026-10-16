"""Aura Retail -- STOCK-TAKE (schema v57): counting the shelf and posting the difference,
through the REAL routes. Every figure is hand-computed (JOD, 3 decimals) from the scenario's own
inputs, never read back from the app's output.

The claims, each pinned in BOTH directions where it has an allow half and a deny half:

  1. a count line captures what the system held when it was FIRST counted; a re-scan ADDS; a typed
     figure SETS; sales rung between count and post survive the correction;
  2. POSTING writes the variance through the existing adjustment path: a SHORTAGE is Dr 5910 and an
     OVERAGE is Cr 5910, at the unit cost HERE; inventory still ties to the stock valuation;
  3. posting is IDEMPOTENT and a posted count is IMMUTABLE (no line added, not cancellable, not
     re-postable into a second movement); products nobody counted are NOT touched;
  4. the movement uid is a pure function of (count, product) -- so two computers posting the same
     count move stock once (the two-computer proof is retail_stock_take_twodevice_test.py);
  5. company scoping: another company cannot read, count into, post or cancel this company's count;
     a cashier (no stock-adjust capability) cannot start, count or post;
  6. (fix round) counts are per-computer CONTRIBUTIONS that add up; only the MAIN computer posts; a
     posted count keeps the cost it booked at; a post reports products it leaves below zero and
     products deleted since they were counted; a merged count cannot be scanned or posted.

MUTATIONS (quoted in the commit): random movement uid instead of the derived one -> test 3b goes RED;
drop the company_id filter in `_stock_count_load` -> test 5a goes RED.

Run:
    pytest products/retail/tests/retail_stock_take_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_stock_take_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from core.retail import general_ledger as gl  # noqa: E402
from core.retail import stock_counts as count_core  # noqa: E402
from commercial_runtime.identity import device_context  # noqa: E402
import api.retail_api as retail_api_module  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture(autouse=True)
def _runs_as_the_main_computer(monkeypatch):
    """Only the main computer may post; the in-process app has no device registry row, so say so."""
    monkeypatch.setattr(device_context, 'local_device_is_admin', lambda conn, cid: True)


def _as_device(monkeypatch, device_id):
    """Make this process act as another computer (the terminal id is the contribution key)."""
    monkeypatch.setattr(retail_api_module, '_stock_count_device_id', lambda: device_id)


# ── bootstrap (same convention as retail_stock_books_routes_test.py) ─────────

def _login_user(role, company_id):
    email = f'st-{role}-{uuid.uuid4().hex[:8]}@test.local'
    password = 'StockTakePW1'  # pragma: allowlist secret -- throwaway fixture value
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f'EMP-{uuid.uuid4().hex[:6]}', email, hash_password(password), role, 'active'))
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, 'retail', 'full'))
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    conn.commit(); conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    c.test_company_id = company_id
    return c


@pytest.fixture
def client():
    return _login_user('admin', str(uuid.uuid4()))


@pytest.fixture
def cashier(client):
    return _login_user('cashier', client.test_company_id)


@pytest.fixture
def other_client():
    return _login_user('admin', str(uuid.uuid4()))


def _seed_product(c, **kw):
    payload = {'name': 'ST Widget', 'sku': f'ST-{uuid.uuid4().hex[:8]}', 'barcode': f'BC{uuid.uuid4().hex[:10]}',
               'cost_price': 2.0, 'sell_price': 5.0, 'tax_rate': 0, 'initial_stock': 10}
    payload.update(kw)
    r = c.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id'], payload['barcode']


def _start(c, expect=200, **body):
    r = c.post(f'{API}/stock-counts', json=body)
    assert r.status_code == expect, r.get_json()
    return r.get_json()['data']['uid'] if expect == 200 else r.get_json()


def _count(c, uid, expect=200, **body):
    r = c.post(f'{API}/stock-counts/{uid}/lines', json=body)
    assert r.status_code == expect, (r.status_code, r.get_json())
    return r.get_json()


def _post(c, uid, expect=200):
    r = c.post(f'{API}/stock-counts/{uid}/post')
    assert r.status_code == expect, (r.status_code, r.get_json())
    return r.get_json()


def _get(c, uid, expect=200):
    r = c.get(f'{API}/stock-counts/{uid}')
    assert r.status_code == expect, (r.status_code, r.get_json())
    return r.get_json()


def _on_hand(cid, pid):
    conn = get_retail_conn()
    try:
        return conn.execute("SELECT SUM(quantity_on_hand) FROM inventory_balances "
                            "WHERE company_id=? AND product_id=?", (cid, pid)).fetchone()[0]
    finally:
        conn.close()


def _bal(cid, code):
    conn = get_retail_conn()
    try:
        return [a for a in gl.trial_balance(conn, cid)['accounts'] if a['code'] == code][0]['balance']
    finally:
        conn.close()


def _tie(c):
    r = c.get(f'{API}/reports/trial-balance')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['inventory']


def _count_movements(cid, count_uid):
    conn = get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT product_id, quantity, uid FROM inventory_movements WHERE company_id=? AND notes=?",
            (cid, f'Stock count {count_uid[:8]}')).fetchall()]
    finally:
        conn.close()


def _sell(c, pid, qty):
    r = c.post(f'{API}/sales', json={'items': [{'product_id': pid, 'quantity': qty}], 'payment_method': 'cash',
                                     'amount_paid': 1000, 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()


# ═════════════════════════════════════════════════════════════════════════════
# 1 -- counting
# ═════════════════════════════════════════════════════════════════════════════

def test_1a_first_count_freezes_the_system_figure_and_a_rescan_adds(client):
    pid, barcode = _seed_product(client, initial_stock=10)
    uid = _start(client)
    first = _count(client, uid, barcode=barcode)                     # one scan = 1
    assert first['data']['counted_qty'] == 1 and first['data']['system_qty_at_count'] == 10
    _count(client, uid, barcode=barcode)
    third = _count(client, uid, barcode=barcode, quantity=3)         # 1 + 1 + 3
    assert third['data']['counted_qty'] == 5 and third['data']['system_qty_at_count'] == 10
    assert third['data']['variance'] == -5

    # The system figure was captured at the FIRST count: a sale now must not move it.
    _sell(client, pid, 2)
    again = _count(client, uid, barcode=barcode)
    assert again['data']['system_qty_at_count'] == 10 and again['data']['counted_qty'] == 6


def test_1b_set_mode_replaces_the_figure_and_zero_is_a_real_count(client):
    pid, _bc = _seed_product(client, initial_stock=4)
    uid = _start(client)
    _count(client, uid, product_id=pid, quantity=9, mode='set')
    out = _count(client, uid, product_id=pid, quantity=0, mode='set')
    assert out['data']['counted_qty'] == 0 and out['data']['variance'] == -4
    # 'add' with zero is nonsense; 'set' with a negative is nonsense.
    _count(client, uid, expect=400, product_id=pid, quantity=0)
    _count(client, uid, expect=400, product_id=pid, quantity=-1, mode='set')
    _count(client, uid, expect=400, product_id=pid, mode='bogus')


def test_1c_unknown_barcode_and_missing_identifier_are_refused(client):
    uid = _start(client)
    _count(client, uid, expect=404, barcode='NO-SUCH-CODE')
    _count(client, uid, expect=400)


def test_1d_one_open_count_per_branch_and_the_refusal_names_it(client):
    uid = _start(client)
    refused = _start(client, expect=409)
    assert refused['code'] == 'STOCK_COUNT_ALREADY_OPEN' and refused['count_uid'] == uid
    # Cancelling frees the branch.
    assert client.post(f'{API}/stock-counts/{uid}/cancel').status_code == 200
    assert _start(client) != uid


def test_1e_a_sale_between_count_and_post_survives_the_correction(client):
    cid = client.test_company_id
    pid, _bc = _seed_product(client, initial_stock=10)
    uid = _start(client)
    _count(client, uid, product_id=pid, quantity=8, mode='set')      # shelf has 8, system said 10 -> -2
    _sell(client, pid, 1)                                            # 9 on hand now
    _post(client, uid)
    assert _on_hand(cid, pid) == 7          # 9 + (8 - 10): the sale is not erased, the shortage is booked


# ═════════════════════════════════════════════════════════════════════════════
# 2 -- posting: the books, both signs
# ═════════════════════════════════════════════════════════════════════════════

def test_2a_overage_credits_5910_shortage_debits_it_and_inventory_still_ties(client):
    cid = client.test_company_id
    a, _ = _seed_product(client, name='Over', cost_price=2.0, initial_stock=10)     # 10 x 2.000 = 20.000
    b, _ = _seed_product(client, name='Short', cost_price=3.0, initial_stock=10)    # 10 x 3.000 = 30.000
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 50.0 and _bal(cid, gl.ACCOUNT_SHRINKAGE) == 0.0

    over = _start(client)
    _count(client, over, product_id=a, quantity=12, mode='set')     # +2 x 2.000 = Cr 5910 4.000
    out = _post(client, over)['data']
    assert out['movements_written'] == 1 and out['totals']['variance_value'] == 4.0
    assert _on_hand(cid, a) == 12
    assert _bal(cid, gl.ACCOUNT_SHRINKAGE) == -4.0                  # credit balance = a gain
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 54.0                  # 12 x 2 + 10 x 3
    assert _tie(client)['status'] == 'ties', _tie(client)

    short = _start(client)
    _count(client, short, product_id=b, quantity=7, mode='set')     # -3 x 3.000 = Dr 5910 9.000
    out = _post(client, short)['data']
    assert out['totals']['variance_value'] == -9.0
    assert _on_hand(cid, b) == 7
    assert _bal(cid, gl.ACCOUNT_SHRINKAGE) == 5.0                   # -4 + 9
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 45.0                  # 12 x 2 + 7 x 3
    assert _tie(client)['status'] == 'ties', _tie(client)


def test_2b_uncounted_products_are_not_touched_and_equal_counts_move_nothing(client):
    cid = client.test_company_id
    counted, _ = _seed_product(client, initial_stock=10)
    exact, _ = _seed_product(client, initial_stock=5)
    untouched, _ = _seed_product(client, initial_stock=6)
    uid = _start(client)
    _count(client, uid, product_id=counted, quantity=9, mode='set')
    _count(client, uid, product_id=exact, quantity=5, mode='set')    # variance 0
    out = _post(client, uid)['data']
    assert out['movements_written'] == 1                             # only the one with a variance
    assert (_on_hand(cid, counted), _on_hand(cid, exact), _on_hand(cid, untouched)) == (9, 5, 6)
    assert [m['product_id'] for m in _count_movements(cid, uid)] == [counted]


def test_2c_a_variance_may_leave_a_balance_negative_because_the_shelf_is_the_truth(client):
    """Documented decision (enforce_floor=False): a sale that landed between the count and the post
    can leave the corrected balance below zero. That is an oversell the shop already made, so the
    post does not refuse it -- unlike a manual adjustment, which never drives a balance negative."""
    cid = client.test_company_id
    pid, _ = _seed_product(client, name='Sold Past Zero', initial_stock=10)
    uid = _start(client)
    _count(client, uid, product_id=pid, quantity=0, mode='set')      # -10
    _sell(client, pid, 3)                                            # 7 on hand
    # The screen can warn BEFORE posting: the count view lists what would end below zero.
    pre = _get(client, uid)['data']['totals']['below_zero']
    assert [(b['name'], b['will_be']) for b in pre] == [('Sold Past Zero', -3.0)]
    out = _post(client, uid)['data']
    assert _on_hand(cid, pid) == -3
    assert [(b['name'], b['will_be']) for b in out['below_zero']] == [('Sold Past Zero', -3.0)]


# ═════════════════════════════════════════════════════════════════════════════
# 3 -- idempotent post, immutable once posted
# ═════════════════════════════════════════════════════════════════════════════

def test_3a_posting_twice_moves_the_stock_once(client):
    cid = client.test_company_id
    pid, _ = _seed_product(client, initial_stock=10)
    uid = _start(client)
    _count(client, uid, product_id=pid, quantity=6, mode='set')
    first = _post(client, uid)['data']
    second = _post(client, uid)['data']
    assert first['already_posted'] is False and second['already_posted'] is True
    assert _on_hand(cid, pid) == 6
    assert len(_count_movements(cid, uid)) == 1
    assert _bal(cid, gl.ACCOUNT_SHRINKAGE) == 8.0                    # 4 x 2.000, not 16
    assert _tie(client)['status'] == 'ties'


def test_3b_the_movement_uid_is_derived_from_count_and_product_not_random(client):
    cid = client.test_company_id
    a, _ = _seed_product(client, initial_stock=10)
    b, _ = _seed_product(client, initial_stock=10)
    uid = _start(client)
    _count(client, uid, product_id=a, quantity=1, mode='set')
    _count(client, uid, product_id=b, quantity=1, mode='set')
    _post(client, uid)
    got = {m['product_id']: m['uid'] for m in _count_movements(cid, uid)}
    assert got == {a: count_core.count_movement_uid(uid, a), b: count_core.count_movement_uid(uid, b)}
    assert got[a] != got[b]


def test_3c_a_posted_count_is_immutable_and_a_cancelled_one_cannot_be_posted(client):
    cid = client.test_company_id
    pid, _ = _seed_product(client, initial_stock=10)
    other, _ = _seed_product(client, initial_stock=10)
    uid = _start(client)
    _count(client, uid, product_id=pid, quantity=7, mode='set')
    _post(client, uid)
    assert _count(client, uid, expect=409, product_id=other, quantity=1)['code'] == 'STOCK_COUNT_CLOSED'
    assert client.post(f'{API}/stock-counts/{uid}/cancel').status_code == 409
    assert _on_hand(cid, other) == 10 and _on_hand(cid, pid) == 7

    dead = _start(client)
    _count(client, dead, product_id=other, quantity=1, mode='set')
    assert client.post(f'{API}/stock-counts/{dead}/cancel').status_code == 200
    assert client.post(f'{API}/stock-counts/{dead}/cancel').status_code == 200      # idempotent
    assert _post(client, dead, expect=409)['code'] == 'STOCK_COUNT_CLOSED'
    assert _on_hand(cid, other) == 10                                # cancelling moved nothing


def test_3d_get_shows_lines_value_and_totals(client):
    a, _ = _seed_product(client, name='Over', cost_price=2.0, initial_stock=10)
    b, _ = _seed_product(client, name='Short', cost_price=3.0, initial_stock=10)
    uid = _start(client, note='aisle 1')
    _count(client, uid, product_id=a, quantity=12, mode='set')
    _count(client, uid, product_id=b, quantity=7, mode='set')
    data = _get(client, uid)['data']
    assert data['count']['status'] == 'open' and data['count']['note'] == 'aisle 1'
    assert data['totals'] == {'lines': 2, 'gain_qty': 2.0, 'short_qty': 3.0, 'variance_value': -5.0,
                              'below_zero': [], 'moved_during_count': []}
    by = {ln['product_id']: ln for ln in data['lines']}
    assert by[a]['variance_value'] == 4.0 and by[b]['variance_value'] == -9.0
    listed = client.get(f'{API}/stock-counts?status=open').get_json()['data']
    assert [c['uid'] for c in listed] == [uid] and listed[0]['line_count'] == 2


# ═════════════════════════════════════════════════════════════════════════════
# 5 -- scoping and authority
# ═════════════════════════════════════════════════════════════════════════════

def test_5a_another_company_cannot_read_count_post_or_cancel_this_ones(client, other_client):
    cid = client.test_company_id
    pid, _ = _seed_product(client, initial_stock=10)
    uid = _start(client)
    _count(client, uid, product_id=pid, quantity=1, mode='set')
    assert other_client.get(f'{API}/stock-counts/{uid}').status_code == 404
    assert other_client.post(f'{API}/stock-counts/{uid}/lines',
                             json={'product_id': pid, 'quantity': 5, 'mode': 'set'}).status_code == 404
    assert other_client.post(f'{API}/stock-counts/{uid}/post').status_code == 404
    assert other_client.post(f'{API}/stock-counts/{uid}/cancel').status_code == 404
    assert other_client.get(f'{API}/stock-counts').get_json()['data'] == []
    # ... and nothing happened to the real count.
    data = _get(client, uid)['data']
    assert data['count']['status'] == 'open' and data['lines'][0]['counted_qty'] == 1
    assert _on_hand(cid, pid) == 10


def test_5b_a_cashier_cannot_start_count_or_post(client, cashier):
    pid, _ = _seed_product(client, initial_stock=10)
    uid = _start(client)
    assert cashier.post(f'{API}/stock-counts', json={}).status_code == 403
    assert cashier.post(f'{API}/stock-counts/{uid}/lines',
                        json={'product_id': pid, 'quantity': 1}).status_code == 403
    assert cashier.post(f'{API}/stock-counts/{uid}/post').status_code == 403
    assert cashier.get(f'{API}/stock-counts/{uid}').status_code == 403
    assert _get(client, uid)['data']['lines'] == []


# ═════════════════════════════════════════════════════════════════════════════
# 6 -- fix round: contributions, main-only post, post cost, deleted products, merged counts
# ═════════════════════════════════════════════════════════════════════════════

def test_6a_two_computers_scanning_the_same_product_ADD_and_the_post_uses_the_sum(client, monkeypatch):
    """A scans 5, B scans 5 (each on its own computer, before they have synced) -> the count says 10,
    not 5. System said 12, so posting moves -2 and leaves 10 on the shelf's books."""
    cid = client.test_company_id
    pid, barcode = _seed_product(client, initial_stock=12)
    uid = _start(client)
    _as_device(monkeypatch, 'dev-A')
    _count(client, uid, barcode=barcode, quantity=5)
    _as_device(monkeypatch, 'dev-B')
    out = _count(client, uid, barcode=barcode, quantity=5)['data']
    assert out['counted_qty'] == 10 and out['this_device_qty'] == 5
    line = _get(client, uid)['data']['lines'][0]
    assert line['counted_qty'] == 10 and line['this_device_qty'] == 5 and line['contributions'] == 2
    # typing a number sets only THIS computer's part
    out = _count(client, uid, product_id=pid, quantity=3, mode='set')['data']
    assert out['counted_qty'] == 8 and out['this_device_qty'] == 3
    _count(client, uid, product_id=pid, quantity=5, mode='set')
    _post(client, uid)
    assert _on_hand(cid, pid) == 10                                   # 12 + (10 - 12)
    assert len(_count_movements(cid, uid)) == 1


def test_6b_the_variance_is_measured_against_the_EARLIEST_system_figure(client, monkeypatch):
    cid = client.test_company_id
    pid, _ = _seed_product(client, initial_stock=10)
    uid = _start(client)
    _as_device(monkeypatch, 'dev-A')
    _count(client, uid, product_id=pid, quantity=4, mode='set')       # A saw 10
    _sell(client, pid, 1)                                             # now 9
    _as_device(monkeypatch, 'dev-B')
    _count(client, uid, product_id=pid, quantity=4, mode='set')       # B saw 9
    line = _get(client, uid)['data']['lines'][0]
    assert line['system_qty_at_count'] == 10 and line['counted_qty'] == 8 and line['variance'] == -2
    _post(client, uid)
    assert _on_hand(cid, pid) == 7                                    # 9 + (8 - 10)


def test_6c_only_the_main_computer_may_post_and_a_refusal_changes_nothing(client, monkeypatch):
    cid = client.test_company_id
    pid, _ = _seed_product(client, initial_stock=10)
    uid = _start(client)
    _count(client, uid, product_id=pid, quantity=6, mode='set')
    monkeypatch.setattr(device_context, 'local_device_is_admin', lambda conn, cid: False)
    refused = _post(client, uid, expect=409)
    assert refused['code'] == 'STOCK_COUNT_NEEDS_MAIN' and 'main computer' in refused['message']
    assert _on_hand(cid, pid) == 10 and _get(client, uid)['data']['count']['status'] == 'open'
    assert _get(client, uid)['data']['can_post'] is False
    _count(client, uid, product_id=pid, quantity=1)                   # a non-main computer still scans
    monkeypatch.setattr(device_context, 'local_device_is_admin', lambda conn, cid: True)
    assert _get(client, uid)['data']['can_post'] is True
    _post(client, uid)
    assert _on_hand(cid, pid) == 7


def test_6d_a_posted_count_keeps_showing_the_cost_it_booked_at(client):
    cid = client.test_company_id
    pid, _ = _seed_product(client, cost_price=2.0, initial_stock=10)
    uid = _start(client)
    _count(client, uid, product_id=pid, quantity=7, mode='set')       # -3 x 2.000
    assert _post(client, uid)['data']['totals']['variance_value'] == -6.0
    conn = get_retail_conn()
    try:                                                              # the product is re-costed afterwards
        conn.execute("UPDATE products SET avg_cost=9, cost_price=9 WHERE id=?", (pid,))
        conn.execute("UPDATE inventory_balances SET avg_cost=9 WHERE product_id=?", (pid,))
        # ... and even the correction movement's own cost is tampered with: the stored post cost wins.
        conn.execute("UPDATE inventory_movements SET unit_cost=9 WHERE notes=?", (f'Stock count {uid[:8]}',))
        conn.commit()
    finally:
        conn.close()
    data = _get(client, uid)['data']
    assert data['lines'][0]['unit_cost'] == 2.0 and data['totals']['variance_value'] == -6.0
    assert _bal(cid, gl.ACCOUNT_SHRINKAGE) == 6.0                     # the journal says the same


def test_6e_a_product_deleted_since_it_was_counted_is_skipped_and_reported(client):
    cid = client.test_company_id
    gone, _ = _seed_product(client, name='Gone Widget', initial_stock=10)
    kept, _ = _seed_product(client, name='Kept Widget', initial_stock=10)
    uid = _start(client)
    _count(client, uid, product_id=gone, quantity=1, mode='set')
    _count(client, uid, product_id=kept, quantity=1, mode='set')
    conn = get_retail_conn()
    try:
        conn.execute("UPDATE products SET deleted_at_utc=? WHERE id=?", ('2026-10-16T00:00:00Z', gone))
        conn.commit()
    finally:
        conn.close()
    out = _post(client, uid)['data']
    assert out['skipped_deleted'] == ['Gone Widget'] and out['movements_written'] == 1
    assert _on_hand(cid, gone) == 10 and _on_hand(cid, kept) == 1


def test_6f_a_merged_count_cannot_be_scanned_posted_or_cancelled(client):
    pid, _ = _seed_product(client, initial_stock=10)
    uid = _start(client)
    winner = str(uuid.uuid4())
    conn = get_retail_conn()
    try:
        conn.execute("UPDATE stock_counts SET status='merged', merged_into=? WHERE uid=?", (winner, uid))
        conn.commit()
    finally:
        conn.close()
    for r in (client.post(f'{API}/stock-counts/{uid}/lines', json={'product_id': pid, 'quantity': 1}),
              client.post(f'{API}/stock-counts/{uid}/post'), client.post(f'{API}/stock-counts/{uid}/cancel')):
        assert r.status_code == 409 and r.get_json()['code'] == 'STOCK_COUNT_MERGED'
        assert r.get_json()['merged_into'] == winner


# ═════════════════════════════════════════════════════════════════════════════
# 7 -- round 3: joined counts are followed, late scans after a post, movement during a count
# ═════════════════════════════════════════════════════════════════════════════

def _insert_joined_loser(cid, winner_uid):
    """A second count of the branch that the sync apply has already joined into `winner_uid`."""
    conn = get_retail_conn()
    try:
        branch = conn.execute("SELECT branch_id FROM stock_counts WHERE uid=?", (winner_uid,)).fetchone()[0]
        loser = str(uuid.uuid4())
        conn.execute("INSERT INTO stock_counts (uid, company_id, branch_id, status, merged_into, started_at) "
                     "VALUES (?,?,?,'merged',?,'2026-10-16T00:00:00+00:00')", (loser, cid, branch, winner_uid))
        conn.commit()
        return loser
    finally:
        conn.close()


def test_7a_a_scan_on_a_joined_count_is_recorded_on_the_winner_and_says_so(client):
    cid = client.test_company_id
    pid, _ = _seed_product(client, initial_stock=10)
    winner = _start(client)
    loser = _insert_joined_loser(cid, winner)
    out = _count(client, loser, product_id=pid, quantity=4, mode='set')['data']
    assert out['count_uid'] == winner and out['counted_qty'] == 4
    data = _get(client, winner)['data']
    assert data['lines'][0]['counted_qty'] == 4
    assert _get(client, loser)['data']['lines'] == [], 'nothing was filed under the joined count'


def test_7b_a_scan_redirected_into_a_POSTED_count_books_a_late_correction_on_the_main_computer(client, monkeypatch):
    """A posted a count (6 of 10 -> -4). Another computer's scan reaches a count that was joined into
    it: dev-B counted 2 more. Total 8 against the earliest figure 10 = -2, already posted -4 -> +2."""
    cid = client.test_company_id
    pid, _ = _seed_product(client, cost_price=2.0, initial_stock=10)
    winner = _start(client)
    _as_device(monkeypatch, 'dev-A')
    _count(client, winner, product_id=pid, quantity=6, mode='set')
    _post(client, winner)
    assert _on_hand(cid, pid) == 6 and _bal(cid, gl.ACCOUNT_SHRINKAGE) == 8.0
    loser = _insert_joined_loser(cid, winner)
    _as_device(monkeypatch, 'dev-B')
    out = _count(client, loser, product_id=pid, quantity=2)['data']
    assert out['count_uid'] == winner
    assert _on_hand(cid, pid) == 8, 'the late scan corrected the stock on the main computer'
    assert _bal(cid, gl.ACCOUNT_SHRINKAGE) == 4.0                                # -4 then +2, x 2.000
    assert _tie(client)['status'] == 'ties', _tie(client)
    _count(client, loser, product_id=pid, quantity=1)                            # another late scan: +1
    assert _on_hand(cid, pid) == 9
    # a count addressed DIRECTLY at the posted uid is still closed
    _count(client, winner, expect=409, product_id=pid, quantity=1)


def test_7c_a_late_scan_on_a_computer_that_is_not_main_books_nothing(client, monkeypatch):
    cid = client.test_company_id
    pid, _ = _seed_product(client, initial_stock=10)
    winner = _start(client)
    _count(client, winner, product_id=pid, quantity=6, mode='set')
    _post(client, winner)
    loser = _insert_joined_loser(cid, winner)
    monkeypatch.setattr(device_context, 'local_device_is_admin', lambda conn, cid: False)
    _as_device(monkeypatch, 'dev-B')
    _count(client, loser, product_id=pid, quantity=2)
    assert _on_hand(cid, pid) == 6, 'only the main computer books a correction'


def test_7d_a_sale_between_two_computers_contributions_is_flagged_for_a_recount(client, monkeypatch):
    """Shelf 5 + 5, system 10. Front counted (5), a sale of 2, back counted (3): the variance (-2)
    would end the books at 6 when the shelf holds 8. The product is flagged; no sale -> not flagged."""
    cid = client.test_company_id
    moved, _ = _seed_product(client, name='Sold In The Middle', initial_stock=10)
    calm, _ = _seed_product(client, name='Nothing Moved', initial_stock=10)
    uid = _start(client)
    _as_device(monkeypatch, 'dev-front')
    _count(client, uid, product_id=moved, quantity=5, mode='set')
    _count(client, uid, product_id=calm, quantity=5, mode='set')
    _sell(client, moved, 2)
    _as_device(monkeypatch, 'dev-back')
    _count(client, uid, product_id=moved, quantity=3, mode='set')
    _count(client, uid, product_id=calm, quantity=5, mode='set')
    data = _get(client, uid)['data']
    flags = {ln['product_name']: ln['moved_during_count'] for ln in data['lines']}
    assert flags == {'Sold In The Middle': True, 'Nothing Moved': False}, flags
    assert [m['name'] for m in data['totals']['moved_during_count']] == ['Sold In The Middle']
    out = _post(client, uid)['data']
    assert [m['name'] for m in out['moved_during_count']] == ['Sold In The Middle']
    assert _on_hand(cid, moved) == 6, 'the earliest baseline is kept: the flag is the safety net'
