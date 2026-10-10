"""MONEY-3 M2 (retail v51): paying a supplier in cash takes it out of the till drawer.

BEFORE: a cash supplier payment moved AP and credited the GL cash account, but the
terminal's open cash session never learned of it. Expected cash stayed too high by that
amount, so every day with such a payment closed "over". NOW: `payments.paid_from`
('drawer' | 'office') says where the notes came from; 'drawer' writes a paid-out row in this
terminal's open session (the drawer's EXISTING paid_out movement -- no second formula) and
the Z/X report lists it under "Supplier payments" with the supplier's name.

Both directions are pinned, because this change DENIES (office, card, no drawer, legacy rows
never touch a drawer) as well as ALLOWS (the drawer case is the whole point). Mutations this
file owns are listed in the module-level MUTATIONS block and were each performed (red quoted
in the crew report).

MUTATIONS (retail_api.py unless noted):
  M2-a  skip `_post_supplier_drawer_paid_out(...)` in supplier_payment
        -> test_cash_payment_with_open_drawer_comes_out_of_expected_cash
  M2-b  `_resolve_supplier_paid_from`: ignore `requested == 'office'` (always drawer)
        -> test_office_choice_leaves_the_drawer_alone
  M2-c  `_resolve_supplier_paid_from`: 'drawer' with no session falls back to office
        -> test_drawer_choice_with_no_open_session_is_refused_409
  M2-d  `_resolve_supplier_paid_from`: drop the `method != 'cash'` early return
        -> test_card_payment_never_touches_the_drawer
  M2-e  supplier void: drop the `_post_missing_drawer_reversals` call
        -> test_cancelling_the_payment_puts_the_cash_back_exactly_once
  M2-f  `_post_missing_drawer_reversals`: drop the NOT EXISTS guard
        -> test_cancelling_the_payment_puts_the_cash_back_exactly_once
  M2-g  `_post_missing_drawer_reversals`: drop `m.type = 'paid_out'` join (any payment)
        -> test_cancelling_a_legacy_null_paid_from_payment_never_touches_a_drawer

Run:
    pytest products/retail/tests/retail_money3_supplier_drawer_test.py -q
"""
import os
import shutil
import sqlite3
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

DATA = Path(tempfile.mkdtemp(prefix="aura_money3_m2_"))
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

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    email = f'm3a-{uuid.uuid4().hex[:8]}@test.local'
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


def _supplier(c, name='Acme Supply'):
    sid = str(uuid.uuid4())
    conn = get_retail_conn()
    conn.execute("INSERT INTO suppliers (id,company_id,name,credit_balance) VALUES (?,?,?,0)",
                 (sid, c.test_company_id, name))
    conn.commit(); conn.close()
    return sid


def _open(c, float_=100.0):
    r = c.post(f'{API}/cash-sessions/open', json={'opening_float': float_})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _x(c, sid):
    r = c.get(f'{API}/cash-sessions/{sid}/x-report')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _pay(c, sid, amount, **extra):
    body = {'amount': amount}
    body.update(extra)
    return c.post(f'{API}/suppliers/{sid}/payments', json=body)


def _row(sql, params=()):
    conn = get_retail_conn()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _drawer_rows(supplier, *types):
    """The cash_movements rows tied to THIS supplier's payments (the test DB is shared
    by every test in the module, so a global COUNT would count other tests' rows)."""
    marks = ','.join('?' * len(types)) if types else None
    q = ("SELECT m.type AS type FROM cash_movements m JOIN payments p ON p.uid = m.payment_uid "
         "WHERE p.party_id=?")
    if types:
        q += f" AND m.type IN ({marks})"
    return _row(q, (supplier, *types))


def _sweep_again(client, supplier):
    """Run the drawer-reversal sweep once more the way production does: every payment void
    (and every sync pull) sweeps ALL voided payments, so paying and cancelling a second,
    unrelated amount re-visits the first one. (Calling the helper directly needs a request
    context for `_stamp()`.)"""
    assert _pay(client, supplier, 1.0, paid_from='office').status_code == 200
    assert client.post(f'{API}/payments/{_payment_id(supplier)}/void', json={'reason': 'sweep'}).status_code == 200


def _payment_id(supplier, direction='out'):
    return _row("SELECT id FROM payments WHERE party_id=? AND direction=? ORDER BY id DESC",
                (supplier, direction))[0]['id']


# ── the allow half: the drawer case is the whole point ──────────────────────────

def test_cash_payment_with_open_drawer_comes_out_of_expected_cash(client):
    sup = _supplier(client, 'Delta Dairy')
    sid = _open(client, 100.0)
    r = _pay(client, sup, 30.0)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['paid_from'] == 'drawer'
    rep = _x(client, sid)
    assert rep['movements']['paid_out'] == 30.0, rep
    assert rep['expected_cash'] == 70.0, rep          # 100 float - 30 paid to the supplier
    sp = rep['supplier_payments']
    assert sp['paid'] == 30.0 and sp['returned'] == 0.0, sp
    assert [i['supplier'] for i in sp['items']] == ['Delta Dairy'], sp
    assert sp['items'][0]['reference'] and sp['items'][0]['kind'] == 'paid', sp
    rows = _row("SELECT paid_from FROM payments WHERE party_id=?", (sup,))
    assert [r_['paid_from'] for r_ in rows] == ['drawer']
    # A hand-entered paid-out (a delivery driver, say) is in the total but is NOT a supplier
    # payment: the breakdown must not list it (verifier addition -- dropping the
    # `payment_uid IS NOT NULL` filter went unnoticed without this).
    mv = client.post(f'{API}/cash-sessions/{sid}/movements', json={'type': 'paid_out', 'amount': 5.0, 'reason': 'driver'})
    assert mv.status_code == 200, mv.get_json()
    rep = _x(client, sid)
    assert rep['movements']['paid_out'] == 35.0 and rep['expected_cash'] == 65.0, rep
    assert rep['supplier_payments']['paid'] == 30.0 and len(rep['supplier_payments']['items']) == 1, rep
    mv = client.post(f'{API}/cash-sessions/{sid}/movements', json={'type': 'paid_in', 'amount': 5.0, 'reason': 'driver back'})
    assert mv.status_code == 200, mv.get_json()
    # And the Z close agrees: counting 70 is a zero variance.
    z = client.post(f'{API}/cash-sessions/{sid}/close', json={'closing_float_counted': 70.0})
    assert z.status_code == 200, z.get_json()
    assert z.get_json()['data']['session']['variance'] == 0.0


def test_a_po_instalment_in_cash_comes_out_of_the_drawer_too(client):
    """pay_purchase_order is the other route that pays a supplier."""
    sup = _supplier(client)
    p = client.post(f'{API}/products', json={'name': 'W', 'sku': f'W-{uuid.uuid4().hex[:6]}',
                                             'cost_price': 5, 'sell_price': 10, 'initial_stock': 0})
    assert p.status_code == 200, p.get_json()
    pid = p.get_json()['data']['id']
    po = client.post(f'{API}/purchase-orders', json={
        'supplier_id': sup, 'amount_paid': 0, 'items': [{'product_id': pid, 'quantity': 10, 'unit_cost': 5}]})
    assert po.status_code == 200, po.get_json()
    po_id = po.get_json()['data']['id']
    sid = _open(client, 100.0)
    r = client.post(f'{API}/purchase-orders/{po_id}/pay', json={'amount': 12.0})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['paid_from'] == 'drawer'
    assert _x(client, sid)['expected_cash'] == 88.0
    # explicit office on the same route leaves the drawer alone
    r = client.post(f'{API}/purchase-orders/{po_id}/pay', json={'amount': 5.0, 'paid_from': 'office'})
    assert r.status_code == 200, r.get_json()
    assert _x(client, sid)['expected_cash'] == 88.0


# ── the deny half: nothing else may touch a drawer ──────────────────────────────

def test_office_choice_leaves_the_drawer_alone(client):
    sup = _supplier(client)
    sid = _open(client, 100.0)
    r = _pay(client, sup, 30.0, paid_from='office')
    assert r.status_code == 200, r.get_json()
    rep = _x(client, sid)
    assert rep['expected_cash'] == 100.0 and rep['movements']['paid_out'] == 0.0, rep
    assert rep['supplier_payments']['items'] == []
    assert _row("SELECT paid_from FROM payments WHERE party_id=?", (sup,))[0]['paid_from'] == 'office'
    assert _drawer_rows(sup) == []


def test_no_drawer_open_defaults_to_office(client):
    sup = _supplier(client)
    r = _pay(client, sup, 30.0)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['paid_from'] == 'office'


def test_drawer_choice_with_no_open_session_is_refused_409(client):
    sup = _supplier(client)
    r = _pay(client, sup, 30.0, paid_from='drawer')
    assert r.status_code == 409, r.get_json()
    body = r.get_json()
    assert body['code'] == 'NO_OPEN_DRAWER' and 'drawer' in body['message'].lower(), body
    # refused means NOTHING was written: no payment row, no balance moved
    assert _row("SELECT COUNT(*) AS n FROM payments WHERE party_id=?", (sup,))[0]['n'] == 0
    assert _row("SELECT credit_balance FROM suppliers WHERE id=?", (sup,))[0]['credit_balance'] == 0


def test_an_unknown_paid_from_value_is_a_400(client):
    sup = _supplier(client)
    _open(client)
    assert _pay(client, sup, 5.0, paid_from='safe').status_code == 400


def test_card_payment_never_touches_the_drawer(client):
    sup = _supplier(client)
    sid = _open(client, 100.0)
    r = _pay(client, sup, 30.0, method='card')
    assert r.status_code == 200, r.get_json()
    assert _x(client, sid)['expected_cash'] == 100.0
    assert _row("SELECT paid_from FROM payments WHERE party_id=?", (sup,))[0]['paid_from'] is None
    # a non-cash method ignores the field instead of 409-ing on it
    assert _pay(client, sup, 1.0, method='card', paid_from='drawer').status_code == 200
    assert _x(client, sid)['expected_cash'] == 100.0


# ── cancelling ──────────────────────────────────────────────────────────────────

def test_cancelling_the_payment_puts_the_cash_back_exactly_once(client):
    sup = _supplier(client)
    sid = _open(client, 100.0)
    assert _pay(client, sup, 30.0).status_code == 200
    assert _x(client, sid)['expected_cash'] == 70.0
    pid = _payment_id(sup)
    v = client.post(f'{API}/payments/{pid}/void', json={'reason': 'typo'})
    assert v.status_code == 200, v.get_json()
    rep = _x(client, sid)
    assert rep['expected_cash'] == 100.0, rep
    assert rep['supplier_payments']['paid'] == 30.0 and rep['supplier_payments']['returned'] == 30.0
    # a second cancel is refused, and re-running the sweep (a pull does) adds nothing
    assert client.post(f'{API}/payments/{pid}/void', json={'reason': 'again'}).status_code == 409
    _sweep_again(client, sup)
    _sweep_again(client, sup)
    assert _x(client, sid)['expected_cash'] == 100.0
    assert len(_drawer_rows(sup, 'paid_in')) == 1 and len(_drawer_rows(sup, 'paid_out')) == 1


def test_cancelling_after_the_drawer_closed_lands_in_the_current_drawer(client):
    """A closed, counted report must not move. The money comes back in the drawer that is
    open NOW; with none open it is recorded as office and no later drawer absorbs it."""
    sup = _supplier(client)
    s1 = _open(client, 100.0)
    assert _pay(client, sup, 30.0).status_code == 200
    assert client.post(f'{API}/cash-sessions/{s1}/close', json={'closing_float_counted': 70.0}).status_code == 200
    closed_before = _x(client, s1)['expected_cash']
    assert closed_before == 70.0
    pid = _payment_id(sup)
    s2 = _open(client, 50.0)
    assert client.post(f'{API}/payments/{pid}/void', json={'reason': 'late'}).status_code == 200
    assert _x(client, s1)['expected_cash'] == closed_before, 'the closed drawer moved'
    assert _x(client, s2)['expected_cash'] == 80.0         # 50 float + 30 back


def test_cancelling_after_close_with_no_drawer_open_is_recorded_as_office(client):
    sup = _supplier(client)
    s1 = _open(client, 100.0)
    assert _pay(client, sup, 30.0).status_code == 200
    assert client.post(f'{API}/cash-sessions/{s1}/close', json={'closing_float_counted': 70.0}).status_code == 200
    pid = _payment_id(sup)
    assert client.post(f'{API}/payments/{pid}/void', json={'reason': 'late'}).status_code == 200
    assert _x(client, s1)['expected_cash'] == 70.0
    notes = _drawer_rows(sup, 'void_note')
    assert len(notes) == 1, 'the office decision must be remembered as a row'
    # tomorrow's drawer must not quietly absorb it
    s3 = _open(client, 40.0)
    _sweep_again(client, sup)
    assert _x(client, s3)['expected_cash'] == 40.0


def test_cancelling_a_legacy_null_paid_from_payment_never_touches_a_drawer(client):
    """A cash supplier payment recorded before v51 has paid_from NULL = office. Cancelling it
    with a drawer open must not put money into that drawer (it never lost any)."""
    sup = _supplier(client)
    sid = _open(client, 100.0)
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO payments (company_id,reference,party_type,party_id,direction,amount,currency,fx_rate,"
        "method,status,uid,created_at) VALUES (?,?,?,?,?,?,?,1,?,?,?,datetime('now'))",
        (client.test_company_id, 'LEG-1', 'supplier', sup, 'out', 30.0, 'JOD', 'cash', 'active',
         str(uuid.uuid4())))
    conn.commit(); conn.close()
    pid = _payment_id(sup)
    v = client.post(f'{API}/payments/{pid}/void', json={'reason': 'legacy'})
    assert v.status_code == 200, v.get_json()
    assert _x(client, sid)['expected_cash'] == 100.0
    assert _drawer_rows(sup) == []


def test_a_drawer_payment_made_elsewhere_has_no_paid_out_here_so_cancelling_it_moves_nothing(client):
    """The shape a synced payment has on the OTHER computer: paid_from='drawer' but no
    paid-out row in any local session. Cancelling it with a drawer open here must not put
    money in this drawer."""
    sup = _supplier(client)
    sid = _open(client, 100.0)
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO payments (company_id,reference,party_type,party_id,direction,amount,currency,fx_rate,"
        "method,status,uid,paid_from,created_at) VALUES (?,?,?,?,?,?,?,1,?,?,?,?,datetime('now'))",
        (client.test_company_id, 'EL-1', 'supplier', sup, 'out', 30.0, 'JOD', 'cash', 'active',
         str(uuid.uuid4()), 'drawer'))
    conn.commit(); conn.close()
    pid = _payment_id(sup)
    assert client.post(f'{API}/payments/{pid}/void', json={'reason': 'x'}).status_code == 200
    assert _x(client, sid)['expected_cash'] == 100.0


def test_v51_shape_and_the_step_replays_as_a_no_op():
    """The head tripwire the older migration tests handed on (they now say 51 and point here):
    the supplier-drawer step adds exactly two nullable columns, leaves history NULL, and a replay
    changes nothing."""
    import database.schema as sch
    assert sch.RETAIL_SCHEMA_VERSION == 51
    conn = get_retail_conn()
    try:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 51
        pay = {r[1]: r for r in conn.execute('PRAGMA table_info(payments)').fetchall()}
        mov = {r[1]: r for r in conn.execute('PRAGMA table_info(cash_movements)').fetchall()}
        assert 'paid_from' in pay and pay['paid_from'][2] == 'TEXT' and pay['paid_from'][3] == 0
        assert 'payment_uid' in mov and mov['payment_uid'][2] == 'TEXT'
        before = (set(pay), set(mov))
        sch._migrate_add_supplier_cash_drawer(conn)
        sch._migrate_add_supplier_cash_drawer(conn)
        after = ({r[1] for r in conn.execute('PRAGMA table_info(payments)').fetchall()},
                 {r[1] for r in conn.execute('PRAGMA table_info(cash_movements)').fetchall()})
        assert after == before
        # a database without either table is left alone
        bare = sqlite3.connect(':memory:')
        sch._migrate_add_supplier_cash_drawer(bare)
        bare.close()
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-x"]))
