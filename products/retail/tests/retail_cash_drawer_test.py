"""Aura Retail -- shift / cash-drawer management (feat/shift-cash-drawer,
schema v10). Real, end-to-end coverage: open -> sell -> movement -> X report
-> return -> close -> Z report, plus the regression guarantee this feature's
spec explicitly calls out -- create_sale/create_return's response must be
byte-for-byte unaffected by whether a cash session is open, mirroring
retail_reorder_hook_regression_test.py's and
retail_einvoicing_regression_test.py's own frozen-response-keys technique.

Bootstrap pattern copied from retail_reorder_hook_regression_test.py.

Run:
    pytest products/retail/tests/retail_cash_drawer_test.py -v
"""
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_cashdrawer_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# Frozen literal -- captured live from the real app, identical to
# retail_reorder_hook_regression_test.py's own SALE_RESPONSE_KEYS. Cash-
# drawer linkage NEVER adds a key to the checkout response, same contract.
SALE_RESPONSE_KEYS = [
    'amount_paid', 'balance_due', 'calculation_version', 'change', 'currency',
    'discount_amount', 'id', 'idempotency_key', 'lines', 'sale_number',
    'subtotal', 'tax_amount', 'total', 'warning',
]
RETURN_RESPONSE_KEYS = [
    'calculation_version', 'id', 'idempotency_key', 'items', 'refund_amount',
    'return_number',
]


def _make_admin_and_product(price=100.0, tax_rate=0.0, stock=500):
    """New company + admin user + one product, logged-in test client -- same
    shape as retail_reorder_hook_regression_test.py's own helper."""
    email = f"cashdrawer-{uuid.uuid4().hex[:10]}@test.local"
    password = "CashDrawerPW1"
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()

    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})

    resp = client.post('/api/sub/retail/products', json={
        'name': 'Cash Drawer Widget', 'sku': f'CD-{uuid.uuid4().hex[:8]}',
        'cost_price': price / 2, 'sell_price': price, 'tax_rate': tax_rate,
        'initial_stock': stock,
    })
    assert resp.status_code == 200, resp.get_json()
    product_id = resp.get_json()['data']['id']
    return client, company_id, product_id


def _sell_cash(client, product_id, qty=1, amount_paid=None, unit_price=100.0):
    total = unit_price * qty if amount_paid is None else amount_paid
    return client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': product_id, 'quantity': qty}],
        'amount_paid': total, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })


def _open_shift(client, opening_float=100.0):
    return client.post('/api/sub/retail/cash-sessions/open', json={'opening_float': opening_float})


def _movement(client, session_id, mtype, amount, reason=''):
    return client.post(f'/api/sub/retail/cash-sessions/{session_id}/movements',
                        json={'type': mtype, 'amount': amount, 'reason': reason})


def _x_report(client, session_id):
    return client.get(f'/api/sub/retail/cash-sessions/{session_id}/x-report')


def _close(client, session_id, counted):
    return client.post(f'/api/sub/retail/cash-sessions/{session_id}/close',
                        json={'closing_float_counted': counted})


# ── Full round trip: open -> sell -> movements -> X report -> return -> close -> Z report ──

def test_full_shift_round_trip_with_correct_expected_cash_and_variance_math():
    client, cid, pid = _make_admin_and_product(price=100.0)

    # Open with a $100 float.
    r = _open_shift(client, 100.0)
    assert r.status_code == 200, r.get_json()
    session = r.get_json()['data']
    assert session['status'] == 'open'
    assert session['opening_float'] == 100.0
    sid = session['id']

    # One $100 cash sale.
    sale = _sell_cash(client, pid, qty=1, amount_paid=100.0).get_json()['data']

    # float_out $20 (bank excess cash), float_in $10, paid_in $3, paid_out $5.
    assert _movement(client, sid, 'float_out', 20.0, 'bank drop').status_code == 200
    assert _movement(client, sid, 'float_in', 10.0, 'extra float').status_code == 200
    assert _movement(client, sid, 'paid_in', 3.0, 'misc cash in').status_code == 200
    assert _movement(client, sid, 'paid_out', 5.0, 'delivery COD').status_code == 200

    # X report before the return: 100 + 100 - 0 + 10 - 20 + 3 - 5 = 188.
    xr = _x_report(client, sid)
    assert xr.status_code == 200, xr.get_json()
    report = xr.get_json()['data']
    assert report['status'] == 'open'
    assert report['opening_float'] == 100.0
    assert report['cash_sales'] == 100.0
    assert report['cash_refunds'] == 0.0
    assert report['movements'] == {'float_in': 10.0, 'float_out': 20.0, 'paid_in': 3.0, 'paid_out': 5.0}
    assert report['expected_cash'] == 188.0

    # Partial cash refund of $10 against that sale.
    ret = client.post('/api/sub/retail/returns', json={
        'sale_id': sale['id'], 'items': [{'product_id': pid, 'quantity': 0.1}],
        'refund_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert ret.status_code == 200, ret.get_json()
    refund_amount = ret.get_json()['data']['refund_amount']
    assert refund_amount == 10.0  # 0.1 * $100 unit price, zero tax

    # X report after the return: 188 - 10 = 178.
    xr2 = _x_report(client, sid).get_json()['data']
    assert xr2['cash_refunds'] == 10.0
    assert xr2['expected_cash'] == 178.0

    # Close with a counted amount BELOW expected -> negative variance.
    close_resp = _close(client, sid, 175.0)
    assert close_resp.status_code == 200, close_resp.get_json()
    data = close_resp.get_json()['data']
    assert data['session']['status'] == 'closed'
    assert data['session']['closing_float_counted'] == 175.0
    assert data['session']['closing_float_expected'] == 178.0
    assert data['session']['variance'] == -3.0
    assert data['report']['expected_cash'] == 178.0

    # Closed session rejects new movements.
    blocked = _movement(client, sid, 'float_in', 1.0)
    assert blocked.status_code == 409

    # No open session remains for this branch.
    current = client.get('/api/sub/retail/cash-sessions/current').get_json()['data']
    assert current is None


def test_variance_sign_is_counted_minus_expected():
    """No sales/movements at all -- expected cash is just the opening float,
    so variance is pure counted-vs-opening-float arithmetic, unambiguous."""
    client, cid, pid = _make_admin_and_product()

    sid1 = _open_shift(client, 50.0).get_json()['data']['id']
    over = _close(client, sid1, 55.0).get_json()['data']
    assert over['session']['closing_float_expected'] == 50.0
    assert over['session']['variance'] == 5.0

    sid2 = _open_shift(client, 50.0).get_json()['data']['id']
    under = _close(client, sid2, 45.0).get_json()['data']
    assert under['session']['closing_float_expected'] == 50.0
    assert under['session']['variance'] == -5.0

    sid3 = _open_shift(client, 50.0).get_json()['data']['id']
    exact = _close(client, sid3, 50.0).get_json()['data']
    assert exact['session']['variance'] == 0.0


def test_cannot_open_two_sessions_on_the_same_branch():
    client, cid, pid = _make_admin_and_product()
    first = _open_shift(client, 20.0)
    assert first.status_code == 200
    second = _open_shift(client, 30.0)
    assert second.status_code == 409
    # The original session is still the one reported as current.
    current = client.get('/api/sub/retail/cash-sessions/current').get_json()['data']
    assert current['id'] == first.get_json()['data']['id']
    assert current['opening_float'] == 20.0


def test_company_scoping_sessions_and_movements_never_leak_across_companies():
    client_a, cid_a, pid_a = _make_admin_and_product()
    client_b, cid_b, pid_b = _make_admin_and_product()

    sid_a = _open_shift(client_a, 40.0).get_json()['data']['id']
    _open_shift(client_b, 60.0)

    # Company B cannot read company A's session by id.
    cross = client_b.get(f'/api/sub/retail/cash-sessions/{sid_a}')
    assert cross.status_code == 404

    # Company B cannot record a movement against company A's session.
    cross_mv = _movement(client_b, sid_a, 'float_in', 5.0)
    assert cross_mv.status_code == 404

    # Company A's list of sessions never includes company B's.
    list_a = client_a.get('/api/sub/retail/cash-sessions').get_json()['data']
    ids_a = {s['id'] for s in list_a}
    assert sid_a in ids_a
    list_b_ids = {s['id'] for s in client_b.get('/api/sub/retail/cash-sessions').get_json()['data']}
    assert ids_a.isdisjoint(list_b_ids)


def test_sale_made_before_any_session_is_not_attributed_to_a_later_session():
    """Proves the linkage is a direct FK stamp at write time, not a
    branch+time-range lookup: a sale made with NO session open must never
    show up in a session opened afterward's cash_sales figure, even though
    both share the same branch."""
    client, cid, pid = _make_admin_and_product(price=50.0)

    # Sell BEFORE any session exists.
    presale = _sell_cash(client, pid, qty=1, amount_paid=50.0)
    assert presale.status_code == 200

    conn = get_retail_conn()
    row = conn.execute("SELECT session_id FROM sales WHERE id=?", (presale.get_json()['data']['id'],)).fetchone()
    conn.close()
    assert row['session_id'] is None

    # Now open a session and sell again -- only the second sale counts.
    sid = _open_shift(client, 0.0).get_json()['data']['id']
    _sell_cash(client, pid, qty=1, amount_paid=50.0)

    report = _x_report(client, sid).get_json()['data']
    assert report['cash_sales'] == 50.0  # only the post-open sale, not the pre-open one


def test_movement_type_validation_and_closed_session_rejects_movements():
    client, cid, pid = _make_admin_and_product()
    sid = _open_shift(client, 10.0).get_json()['data']['id']

    bad_type = _movement(client, sid, 'not_a_real_type', 5.0)
    assert bad_type.status_code == 400

    bad_amount = _movement(client, sid, 'float_in', 0)
    assert bad_amount.status_code == 400

    negative = _movement(client, sid, 'float_in', -5.0)
    assert negative.status_code == 400

    ok = _movement(client, sid, 'float_in', 5.0)
    assert ok.status_code == 200


# ── The strongest requirement: create_sale/create_return are unaffected ──

def test_create_sale_response_byte_for_byte_unchanged_whether_or_not_a_cash_session_is_open():
    client_open, _, pid_open = _make_admin_and_product(price=75.0)
    client_closed, _, pid_closed = _make_admin_and_product(price=75.0)

    _open_shift(client_open, 25.0)
    # client_closed deliberately never opens a session.

    data_open = _sell_cash(client_open, pid_open, qty=1, amount_paid=75.0).get_json()['data']
    data_closed = _sell_cash(client_closed, pid_closed, qty=1, amount_paid=75.0).get_json()['data']

    assert sorted(data_open.keys()) == sorted(data_closed.keys()) == sorted(SALE_RESPONSE_KEYS)
    excluded = {'id', 'sale_number', 'idempotency_key', 'lines'}
    for key in SALE_RESPONSE_KEYS:
        if key in excluded:
            continue
        assert data_open[key] == data_closed[key], f"divergence on {key!r}"

    line_open = data_open['lines'][0]
    line_closed = data_closed['lines'][0]
    for key in ('quantity', 'unit_price', 'discount_pct', 'tax_rate', 'line_total'):
        assert line_open[key] == line_closed[key], f"line divergence on {key!r}"


def test_create_return_response_unaffected_by_cash_session_state():
    client_open, _, pid_open = _make_admin_and_product(price=40.0)
    client_closed, _, pid_closed = _make_admin_and_product(price=40.0)

    _open_shift(client_open, 15.0)

    sale_open = _sell_cash(client_open, pid_open, qty=1, amount_paid=40.0).get_json()['data']
    sale_closed = _sell_cash(client_closed, pid_closed, qty=1, amount_paid=40.0).get_json()['data']

    ret_open = client_open.post('/api/sub/retail/returns', json={
        'sale_id': sale_open['id'], 'items': [{'product_id': pid_open, 'quantity': 1}],
        'reason': 'cash drawer regression', 'idempotency_key': str(uuid.uuid4()),
    })
    ret_closed = client_closed.post('/api/sub/retail/returns', json={
        'sale_id': sale_closed['id'], 'items': [{'product_id': pid_closed, 'quantity': 1}],
        'reason': 'cash drawer regression', 'idempotency_key': str(uuid.uuid4()),
    })
    assert ret_open.status_code == 200, ret_open.get_json()
    assert ret_closed.status_code == 200, ret_closed.get_json()
    data_open = ret_open.get_json()['data']
    data_closed = ret_closed.get_json()['data']
    assert sorted(data_open.keys()) == sorted(data_closed.keys()) == sorted(RETURN_RESPONSE_KEYS)
    excluded = {'id', 'return_number', 'idempotency_key', 'items'}
    for key in RETURN_RESPONSE_KEYS:
        if key in excluded:
            continue
        assert data_open[key] == data_closed[key], f"divergence on {key!r}"


def test_sale_and_return_are_stamped_with_the_open_session_id_when_one_exists():
    """Direct DB check that the FK stamp actually happened -- the response-
    shape tests above deliberately never expose session_id, so this is the
    only place that verifies the write itself."""
    client, cid, pid = _make_admin_and_product(price=20.0)
    sid = _open_shift(client, 5.0).get_json()['data']['id']
    sale = _sell_cash(client, pid, qty=1, amount_paid=20.0).get_json()['data']

    conn = get_retail_conn()
    row = conn.execute("SELECT session_id FROM sales WHERE id=?", (sale['id'],)).fetchone()
    assert row['session_id'] == sid

    ret = client.post('/api/sub/retail/returns', json={
        'sale_id': sale['id'], 'items': [{'product_id': pid, 'quantity': 1}],
        'idempotency_key': str(uuid.uuid4()),
    }).get_json()['data']
    rrow = conn.execute("SELECT session_id FROM returns WHERE id=?", (ret['id'],)).fetchone()
    assert rrow['session_id'] == sid
    conn.close()


def test_no_background_thread_exists_from_cash_session_routes():
    """Mirrors retail_reorder_hook_regression_test.py's identical assertion --
    every cash-drawer route runs synchronously, inline, no worker thread."""
    import threading
    client, cid, pid = _make_admin_and_product()
    baseline = {t.name for t in threading.enumerate()}
    sid = _open_shift(client, 10.0).get_json()['data']['id']
    _sell_cash(client, pid, qty=1, amount_paid=100.0)
    _movement(client, sid, 'float_in', 5.0)
    _x_report(client, sid)
    _close(client, sid, 15.0)
    names = {t.name for t in threading.enumerate()}
    assert names.issubset(baseline | {'MainThread'})
