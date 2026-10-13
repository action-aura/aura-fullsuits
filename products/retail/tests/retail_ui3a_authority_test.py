"""UI-3a: amount discounts, "make the total X", price override and manager-PIN approval,
driven through the real POST /sales route (Flask test client, real registry, real PINs).

Both directions of everything that both denies and allows:
  cashier within limit  -> allowed        | over limit            -> 403 DISCOUNT_OVER_LIMIT
  over limit + manager  -> allowed, approver recorded (sales.discount_approved_by)
  wrong PIN             -> 403, nothing written | 5 wrong -> 429 PIN_LOCKED even for the RIGHT PIN
  approver w/o authority-> 403 | approver == cashier -> 403
  price override        -> denied without the capability, allowed with it, allowed with a PIN
  list_price is snapshotted on every line (sold-below-list is reportable)

Run: pytest products/retail/tests/retail_ui3a_authority_test.py -q
"""
import json
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

DATA = Path(tempfile.mkdtemp(prefix="aura_ui3a_auth_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA), AURA_SITE_RELAY_ENABLED="0")
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from core.retail.pin_throttle import PinThrottle  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
import api.retail_api as retail_api  # noqa: E402

API = '/api/sub/retail'
PIN = '4321'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture(autouse=True)
def _fresh_throttle_and_no_push(monkeypatch):
    monkeypatch.setattr(retail_api, '_APPROVAL_PIN_THROTTLE', PinThrottle())
    monkeypatch.setattr(retail_api, '_sync_nudge', lambda: None)


def _make_user(role, company_id, pin=None, capabilities=None):
    email = f"u3-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "UiThreeAPW1"  # pragma: allowlist secret
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change, uid) VALUES (?,?,?,?,?,?,?,0,?)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), role,
         "active", str(uuid.uuid4())))
    conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
                 (str(uuid.uuid4()), user_id, "retail", "full"))
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    for code, level in (capabilities or {}).items():
        conn.execute("UPDATE user_permissions SET access_level=? WHERE user_id=? AND subsystem=?",
                     (level, user_id, code))
    if pin:
        user_accounts.set_user_pin(conn, user_id, pin)
    uid = conn.execute("SELECT uid FROM users WHERE id=?", (user_id,)).fetchone()[0]
    conn.commit(); conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    c.user_id, c.user_uid, c.email = user_id, uid, email
    return c


@pytest.fixture
def shop():
    cid = str(uuid.uuid4())
    owner = _make_user('admin', cid)
    owner.get(f'{API}/settings/tax')
    pids = []
    for name, price in (('Widget A', 3.5), ('Widget B', 2.75), ('Widget C', 0.181)):
        r = owner.post(f'{API}/products', json={
            'name': name, 'sku': f'U3-{uuid.uuid4().hex[:8]}', 'cost_price': 0.1,
            'sell_price': price, 'tax_rate': 16, 'initial_stock': 1000})
        assert r.status_code == 200, r.get_json()
        pids.append(r.get_json()['data']['id'])
    cashier = _make_user('cashier', cid)
    manager = _make_user('manager', cid, pin=PIN)
    return cid, owner, cashier, manager, pids


def _cart(pids, **extra):
    items = [{'product_id': pids[0], 'quantity': 1}, {'product_id': pids[1], 'quantity': 2},
             {'product_id': pids[2], 'quantity': 1}]
    return {'items': items, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()), **extra}


def _post(c, body):
    return c.post(f'{API}/sales', json=body)


def _q(sql, *args):
    conn = get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _state():
    return (_q("SELECT COUNT(*) AS n FROM sales")[0]['n'], _q("SELECT COUNT(*) AS n FROM sync_outbox")[0]['n'],
            _q("SELECT COUNT(*) AS n FROM inventory_movements")[0]['n'])


def _set_limit(owner, v):
    r = owner.post(f'{API}/settings/credit', json={'cashier_discount_limit_pct': v})
    assert r.status_code == 200, r.get_json()


def _approval(manager, pin=PIN, ref=None):
    return {'approver': ref or manager.user_uid, 'pin': pin}


# ── the money lands exactly ────────────────────────────────────────────────────────

def test_owner_amount_discount_lands_exactly_and_is_stored_truthfully(shop):
    _, owner, _, _, pids = shop
    r = _post(owner, _cart(pids, cart_discount_amount=0.65, discount_reason='Damaged'))
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['subtotal'] == 9.181 and d['discount_amount'] == 0.65
    assert d['total'] == pytest.approx(10.650 - 0.65 * 1.16, abs=0.0015)
    row = _q("SELECT discount_amount, total, discount_reason, discount_approved_by FROM sales WHERE id=?", d['id'])[0]
    assert row['discount_amount'] == 0.65 and row['discount_reason'] == 'Damaged'
    assert row['discount_approved_by'] is None          # an owner needs nobody's approval
    lines = _q("SELECT discount_pct, line_total FROM sale_items WHERE sale_id=?", d['id'])
    assert all(0 < l['discount_pct'] < 100 for l in lines)      # truthful per-line pct, no new money column
    assert round(sum(l['line_total'] for l in lines), 3) == pytest.approx(9.181 - 0.65, abs=1e-9)


def test_make_the_total_ten_is_exactly_ten_for_the_owner(shop):
    _, owner, _, _, pids = shop
    r = _post(owner, _cart(pids, cart_target_total=10.0, discount_reason='Round down'))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['total'] == 10.0
    assert _q("SELECT total FROM sales WHERE id=?", r.get_json()['data']['id'])[0]['total'] == 10.0


def test_unreachable_target_is_refused_with_the_nearest_totals_and_writes_nothing(shop):
    from decimal import Decimal
    from core.retail import cart_discount as cd, pricing
    _, owner, _, _, pids = shop
    lines = [(3.5, 1, 16), (2.75, 2, 16), (0.181, 1, 16)]
    quant = pricing.currency_quantum('JOD')
    calc = lambda p, q, pct, r: pricing.calculate_line(p, q, pct, r, currency='JOD')
    skipped = None
    for fils in range(9000, 10650):
        try:
            cd.solve_target_total(lines, Decimal(fils) * quant, quant, calc)
        except cd.CartDiscountError as e:
            skipped = fils / 1000
            break
    assert skipped is not None, 'a 16% tax cart must skip some totals'
    before = _state()
    r = _post(owner, _cart(pids, cart_target_total=skipped, discount_reason='x'))
    assert r.status_code == 400 and r.get_json()['code'] == 'TARGET_NOT_REACHABLE'
    data = r.get_json()['data']
    assert data['nearest_below'] < skipped < data['nearest_above']
    assert _state() == before


def test_a_cart_discount_needs_a_reason(shop):
    _, owner, _, _, pids = shop
    before = _state()
    r = _post(owner, _cart(pids, cart_discount_amount=0.5))
    assert r.status_code == 400 and r.get_json()['code'] == 'DISCOUNT_REASON_REQUIRED'
    r = _post(owner, _cart(pids, cart_discount_amount=0.5, discount_reason='x' * 121))
    assert r.status_code == 400 and r.get_json()['code'] == 'DISCOUNT_REASON_TOO_LONG'
    assert _state() == before


def test_ambiguous_combinations_are_refused(shop):
    _, owner, _, _, pids = shop
    before = _state()
    both = _post(owner, _cart(pids, cart_discount_amount=0.5, cart_target_total=10.0, discount_reason='x'))
    assert both.status_code == 400 and both.get_json()['code'] == 'DISCOUNT_AMBIGUOUS'
    body = _cart(pids, cart_discount_amount=0.5, discount_reason='x')
    body['items'][0]['discount_pct'] = 5
    mixed = _post(owner, body)
    assert mixed.status_code == 400 and mixed.get_json()['code'] == 'DISCOUNT_COMBINATION_NOT_ALLOWED'
    assert _state() == before
    for bad in (0, -1, 99, 'abc', 0.6501):
        r = _post(owner, _cart(pids, cart_discount_amount=bad, discount_reason='x'))
        assert r.status_code == 400, (bad, r.get_json())
    assert _state() == before


# ── authority ─────────────────────────────────────────────────────────────────────

def test_cashier_within_the_limit_is_allowed_and_audited(shop):
    _, owner, cashier, _, pids = shop
    _set_limit(owner, '10')                       # 0.65 / 9.181 = 7.08 %
    r = _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='Loyal customer'))
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']
    row = _q("SELECT discount_reason, discount_approved_by FROM sales WHERE id=?", sale_id)[0]
    assert row['discount_reason'] == 'Loyal customer' and row['discount_approved_by'] is None
    actions = {a['action'] for a in _q("SELECT action FROM audit_log WHERE entity_id=?", sale_id)}
    assert {'SALE_DISCOUNT_BY_CASHIER', 'SALE_CART_DISCOUNT'} <= actions


def test_cashier_over_the_limit_is_refused_and_writes_nothing(shop):
    _, owner, cashier, _, pids = shop
    _set_limit(owner, '5')                        # 7.08 % is over
    before = _state()
    r = _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='Damaged'))
    assert r.status_code == 403 and r.get_json()['code'] == 'DISCOUNT_OVER_LIMIT'
    assert _state() == before


def test_cashier_with_a_zero_limit_is_refused_an_amount_discount_too(shop):
    _, _, cashier, _, pids = shop
    before = _state()
    r = _post(cashier, _cart(pids, cart_discount_amount=0.10, discount_reason='Damaged'))
    assert r.status_code == 403 and r.get_json()['code'] == 'DISCOUNT_OVER_LIMIT'
    assert _state() == before


def test_over_the_limit_with_a_valid_manager_pin_is_allowed_and_the_approver_recorded(shop):
    _, owner, cashier, manager, pids = shop
    _set_limit(owner, '5')
    r = _post(cashier, _cart(pids, cart_target_total=10.0, discount_reason='Round down',
                             approval=_approval(manager)))
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['total'] == 10.0 and d['discount_approved_by'] == manager.user_uid
    row = _q("SELECT discount_approved_by, discount_reason FROM sales WHERE id=?", d['id'])[0]
    assert row == {'discount_approved_by': manager.user_uid, 'discount_reason': 'Round down'}
    actions = {a['action'] for a in _q("SELECT action FROM audit_log WHERE entity_id=?", d['id'])}
    assert 'SALE_DISCOUNT_APPROVED' in actions
    # the approver may be named by login e-mail too
    r2 = _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='Damaged',
                              approval=_approval(manager, ref=manager.email.upper())))
    assert r2.status_code == 200, r2.get_json()


def test_an_unneeded_pin_is_not_recorded_on_the_sale(shop):
    _, owner, cashier, manager, pids = shop
    _set_limit(owner, '10')                       # within limit: no approval needed
    r = _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='Damaged',
                             approval=_approval(manager)))
    assert r.status_code == 200, r.get_json()
    assert _q("SELECT discount_approved_by FROM sales WHERE id=?", r.get_json()['data']['id'])[0][
        'discount_approved_by'] is None


def test_per_line_percent_over_the_limit_also_accepts_a_manager_pin(shop):
    _, owner, cashier, manager, pids = shop
    _set_limit(owner, '5')
    body = _cart(pids, discount_reason='Damaged')
    for it in body['items']:
        it['discount_pct'] = 20
    assert _post(cashier, body).status_code == 403
    body['approval'] = _approval(manager)
    body['idempotency_key'] = str(uuid.uuid4())
    r = _post(cashier, body)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['discount_approved_by'] == manager.user_uid


def test_wrong_pin_is_refused_and_five_wrong_lock_the_approver_even_for_the_right_pin(shop):
    _, owner, cashier, manager, pids = shop
    _set_limit(owner, '5')
    before = _state()
    for _ in range(5):
        r = _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='x',
                                 approval=_approval(manager, pin='0000')))
        assert r.status_code == 403 and r.get_json()['code'] == 'APPROVAL_REJECTED'
    locked = _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='x',
                                  approval=_approval(manager)))             # the RIGHT pin
    assert locked.status_code == 429 and locked.get_json()['code'] == 'PIN_LOCKED'
    assert locked.get_json()['data']['retry_after_seconds'] > 0
    assert _state() == before


def test_an_unknown_approver_and_a_wrong_pin_look_the_same(shop):
    _, owner, cashier, manager, pids = shop
    _set_limit(owner, '5')
    a = _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='x',
                             approval={'approver': 'nobody@nowhere', 'pin': PIN}))
    b = _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='x',
                             approval=_approval(manager, pin='1111')))
    assert (a.status_code, a.get_json()['code']) == (b.status_code, b.get_json()['code']) == (403, 'APPROVAL_REJECTED')


def test_an_approver_without_the_authority_is_refused(shop):
    cid, owner, cashier, _, pids = shop
    _set_limit(owner, '5')
    other_cashier = _make_user('cashier', cid, pin='5555')
    before = _state()
    r = _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='x',
                             approval=_approval(other_cashier, pin='5555')))
    assert r.status_code == 403 and r.get_json()['code'] == 'APPROVER_NOT_AUTHORISED'
    assert _state() == before


def test_the_cashier_cannot_approve_themselves(shop):
    cid, owner, _, _, pids = shop
    _set_limit(owner, '5')
    me = _make_user('cashier', cid, pin='6666')
    r = _post(me, _cart(pids, cart_discount_amount=0.65, discount_reason='x',
                        approval=_approval(me, pin='6666')))
    assert r.status_code == 403 and r.get_json()['code'] == 'APPROVER_IS_CASHIER'


# ── price override ──────────────────────────────────────────────────────────────────

def test_list_price_is_snapshotted_on_every_line(shop):
    _, owner, _, _, pids = shop
    r = _post(owner, _cart(pids))
    assert r.status_code == 200, r.get_json()
    rows = _q("SELECT unit_price, list_price FROM sale_items WHERE sale_id=? ORDER BY id", r.get_json()['data']['id'])
    assert [x['list_price'] for x in rows] == [3.5, 2.75, 0.181]
    assert [x['unit_price'] for x in rows] == [3.5, 2.75, 0.181]


def test_price_override_denied_without_the_capability_allowed_with_it_or_a_pin(shop):
    _, owner, cashier, manager, pids = shop
    body = _cart(pids)
    body['items'][0]['unit_price_override'] = 3.0
    before = _state()
    denied = _post(cashier, body)
    assert denied.status_code == 403 and denied.get_json()['code'] == 'PRICE_OVERRIDE_DENIED'
    assert _state() == before
    # a manager (holds the authority) may
    body['idempotency_key'] = str(uuid.uuid4())
    ok = _post(manager, body)
    assert ok.status_code == 200, ok.get_json()
    li = _q("SELECT unit_price, list_price FROM sale_items WHERE sale_id=? ORDER BY id", ok.get_json()['data']['id'])[0]
    assert li == {'unit_price': 3.0, 'list_price': 3.5}                 # sold below list is reportable
    assert _q("SELECT discount_approved_by FROM sales WHERE id=?", ok.get_json()['data']['id'])[0][
        'discount_approved_by'] is None
    # the cashier with a manager's PIN may, and the approver is recorded
    body['idempotency_key'] = str(uuid.uuid4())
    body['approval'] = _approval(manager)
    via_pin = _post(cashier, body)
    assert via_pin.status_code == 200, via_pin.get_json()
    assert _q("SELECT discount_approved_by FROM sales WHERE id=?", via_pin.get_json()['data']['id'])[0][
        'discount_approved_by'] == manager.user_uid
    assert 'SALE_PRICE_OVERRIDE' in {a['action'] for a in _q(
        "SELECT action FROM audit_log WHERE entity_id=?", via_pin.get_json()['data']['id'])}


def test_a_bad_override_is_refused(shop):
    _, owner, _, _, pids = shop
    for bad in (-1, 'abc', 1.00012):
        body = _cart(pids)
        body['items'][0]['unit_price_override'] = bad
        r = _post(owner, body)
        assert r.status_code == 400 and r.get_json()['code'] == 'PRICE_OVERRIDE_INVALID', (bad, r.get_json())


# ── sync payload ────────────────────────────────────────────────────────────────────

def test_the_sale_and_its_lines_carry_the_new_fields_on_the_wire(shop):
    _, owner, _, _, pids = shop
    body = _cart(pids, cart_discount_amount=0.65, discount_reason='Damaged')
    body['items'][0]['unit_price_override'] = 3.0
    r = _post(owner, body)
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']
    uid = _q("SELECT uid FROM sales WHERE id=?", sale_id)[0]['uid']
    sale_ev = [json.loads(e['payload']) for e in _q(
        "SELECT payload FROM sync_outbox WHERE entity_type='sale' AND entity_id=?", uid)][0]
    assert sale_ev['discount_reason'] == 'Damaged' and 'discount_approved_by' in sale_ev
    item_evs = [json.loads(e['payload']) for e in _q(
        "SELECT payload FROM sync_outbox WHERE entity_type='sale_item' ORDER BY rowid DESC LIMIT 3")]
    assert sorted(e['list_price'] for e in item_evs) == [0.181, 2.75, 3.5]
