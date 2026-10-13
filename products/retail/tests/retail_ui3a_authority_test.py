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
from core.retail import pin_throttle  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
import api.retail_api as retail_api  # noqa: E402

API = '/api/sub/retail'
PIN = '4321'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture(autouse=True)
def _no_push(monkeypatch):
    # (The PIN counter is in the database and keyed by company+approver; every test's `shop` is a
    # fresh company, so nothing needs resetting between tests.)
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


def test_an_approver_without_the_authority_is_refused_with_the_same_answer_as_a_wrong_pin(shop):
    # Verifier LOW-3: the endpoint used to answer APPROVER_NOT_AUTHORISED only AFTER the PIN
    # verified, so for any non-manager it confirmed whether a guessed PIN was right. Now the
    # answer for "right PIN, not allowed" is byte-identical to "wrong PIN". What this can no
    # longer catch: the old distinct code. What it catches instead: the oracle coming back.
    cid, owner, cashier, manager, pids = shop
    _set_limit(owner, '5')
    other_cashier = _make_user('cashier', cid, pin='5555')
    before = _state()
    right_but_not_allowed = _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='x',
                                                 approval=_approval(other_cashier, pin='5555')))
    wrong_and_not_allowed = _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='x',
                                                 approval=_approval(other_cashier, pin='1212')))
    wrong_for_a_manager = _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='x',
                                               approval=_approval(manager, pin='1212')))
    bodies = [r.get_json() for r in (right_but_not_allowed, wrong_and_not_allowed, wrong_for_a_manager)]
    assert all(r.status_code == 403 for r in (right_but_not_allowed, wrong_and_not_allowed, wrong_for_a_manager))
    assert bodies[0] == bodies[1] == bodies[2] and bodies[0]['code'] == 'APPROVAL_REJECTED'
    assert _state() == before


def test_an_approver_from_another_company_is_refused(shop):          # verifier survivor V1
    _, owner, cashier, _, pids = shop
    _set_limit(owner, '5')
    foreign_manager = _make_user('manager', str(uuid.uuid4()), pin='8888')
    before = _state()
    r = _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='x',
                             approval=_approval(foreign_manager, pin='8888')))
    assert r.status_code == 403 and r.get_json()['code'] == 'APPROVAL_REJECTED'
    assert _state() == before


def test_a_disabled_or_deleted_approver_is_refused(shop):            # verifier survivor V2
    cid, owner, cashier, manager, pids = shop
    _set_limit(owner, '5')
    assert _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='x',
                                approval=_approval(manager))).status_code == 200    # control: allowed while active
    for status in ('disabled', 'deleted'):
        conn = registry_conn()
        conn.execute("UPDATE users SET status=? WHERE id=?", (status, manager.user_id))
        conn.commit(); conn.close()
        before = _state()
        r = _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='x',
                                 approval=_approval(manager)))
        assert r.status_code == 403 and r.get_json()['code'] == 'APPROVAL_REJECTED', (status, r.get_json())
        assert _state() == before


def test_an_approved_per_line_percent_over_the_limit_needs_a_reason(shop):     # verifier survivor V3
    _, owner, cashier, manager, pids = shop
    _set_limit(owner, '5')
    body = _cart(pids, approval=_approval(manager))
    for it in body['items']:
        it['discount_pct'] = 20
    before = _state()
    r = _post(cashier, body)
    assert r.status_code == 400 and r.get_json()['code'] == 'DISCOUNT_REASON_REQUIRED'
    assert _state() == before
    body['discount_reason'] = 'Damaged'
    body['idempotency_key'] = str(uuid.uuid4())
    assert _post(cashier, body).status_code == 200


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


# ── who the till may offer in the "ask a manager" dialog ────────────────────────────

def test_the_approver_list_names_only_pin_holding_managers_and_leaks_nothing(shop):
    cid, owner, cashier, manager, pids = shop
    pin_cashier = _make_user('cashier', cid, pin='7777')      # has a PIN but no authority
    r = cashier.get(f'{API}/discount/approvers')
    assert r.status_code == 200, r.get_json()
    rows = r.get_json()['data']
    refs = {x['ref'] for x in rows}
    assert manager.user_uid in refs                            # a manager with a PIN is offered
    assert pin_cashier.user_uid not in refs                    # a cashier with a PIN is not
    assert owner.user_uid not in refs                          # an owner with no PIN is not
    assert all(set(x) == {'ref', 'label'} for x in rows)       # nothing but a reference and a name
    assert manager.email not in json.dumps(rows)
    # the asker never sees themselves, even a manager asking
    own = manager.get(f'{API}/discount/approvers').get_json()['data']
    assert manager.user_uid not in {x['ref'] for x in own}
    # and what it offers is accepted by the approval
    _set_limit(owner, '5')
    ok = _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='x',
                              approval={'approver': rows[0]['ref'], 'pin': PIN}))
    assert ok.status_code == 200, ok.get_json()


# ── the PIN lockout: counted BEFORE the KDF, kept in the database, audited ───────────────
# Verifier HIGH-1: 15 wrong PINs were evaluated in one burst against a design of 5; a success wiped
# failures; a restart cleared the lock; nothing was audited.

def _attempt_row(cid, approver_uid):
    rows = _q("SELECT failures, locked_until FROM pin_approval_attempts WHERE company_id=? AND approver_uid=?",
              cid, approver_uid)
    return rows[0] if rows else None


def _wrong(cashier, manager, pids, pin='0000'):
    return _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='x',
                                approval=_approval(manager, pin=pin)))


def test_a_parallel_burst_of_wrong_pins_verifies_at_most_five(shop, monkeypatch):
    """The measurement the verifier quoted (15 in a burst on waitress), against the real route.

    The wrapper makes each PIN check slow (as PBKDF2 is) so, if the attempt were counted AFTER
    the check, every thread in flight would reach the check before any failure was recorded."""
    import threading
    import time
    cid, owner, cashier, manager, pids = shop
    _set_limit(owner, '5')
    real = retail_api._verify_user_pin
    seen = {'n': 0}
    guard = threading.Lock()

    def slow_counting_verify(reg, user_id, pin):
        with guard:
            seen['n'] += 1
        time.sleep(0.15)
        return real(reg, user_id, pin)

    monkeypatch.setattr(retail_api, '_verify_user_pin', slow_counting_verify)
    clients = []
    for _ in range(12):
        # same person, 12 browser windows: log in again as the cashier for each
        c = app.test_client()
        r = c.post('/api/auth/login', json={'email': cashier.email, 'password': 'UiThreeAPW1'})
        assert r.status_code == 200
        clients.append(c)
    results, before = [], _state()

    def fire(c):
        results.append(c.post(f'{API}/sales', json=_cart(
            pids, cart_discount_amount=0.65, discount_reason='x', approval=_approval(manager, pin='0000'))).status_code)

    threads = [threading.Thread(target=fire, args=(clients[i % 12],)) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 40
    print('PARALLEL BURST: verified=%d  403=%d  429=%d' % (seen['n'], results.count(403), results.count(429)))
    assert seen['n'] <= pin_throttle.MAX_FAILURES, seen
    assert results.count(403) == pin_throttle.MAX_FAILURES and results.count(429) == 40 - pin_throttle.MAX_FAILURES
    assert _state() == before
    row = _attempt_row(cid, manager.user_uid)
    assert row['failures'] == pin_throttle.MAX_FAILURES and row['locked_until']


def test_the_lock_is_in_the_database_so_a_restart_cannot_clear_it(shop):
    """No in-process object holds the lock: it is a row, read fresh by every request. A restart
    (a new process) sees exactly the same row. Simulated by a connection that shares nothing."""
    cid, owner, cashier, manager, pids = shop
    _set_limit(owner, '5')
    for _ in range(5):
        assert _wrong(cashier, manager, pids).status_code == 403
    assert not hasattr(retail_api, '_APPROVAL_PIN_THROTTLE')           # nothing in memory to lose
    row = _attempt_row(cid, manager.user_uid)
    assert row['failures'] == 5 and row['locked_until']
    fresh = get_retail_conn()                                          # what a restarted process would open
    try:
        allowed, retry, _ = pin_throttle.reserve_attempt(fresh, cid, manager.user_uid)
    finally:
        fresh.close()
    assert allowed is False and retry > 0
    locked = _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='x',
                                  approval=_approval(manager)))                  # the RIGHT pin
    assert locked.status_code == 429 and locked.get_json()['code'] == 'PIN_LOCKED'


def test_a_right_pin_undoes_only_its_own_attempt_never_the_failures_before_it(shop):
    cid, owner, cashier, manager, pids = shop
    _set_limit(owner, '5')
    for _ in range(3):
        assert _wrong(cashier, manager, pids).status_code == 403
    assert _attempt_row(cid, manager.user_uid)['failures'] == 3
    ok = _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='x', approval=_approval(manager)))
    assert ok.status_code == 200, ok.get_json()
    assert _attempt_row(cid, manager.user_uid)['failures'] == 3        # NOT wiped to 0
    assert _wrong(cashier, manager, pids).status_code == 403            # 4th
    assert _wrong(cashier, manager, pids).status_code == 403            # 5th -> locks
    assert _wrong(cashier, manager, pids).status_code == 429


def test_every_refusal_is_an_audit_row_without_the_pin_and_a_lockout_is_one_too(shop):
    cid, owner, cashier, manager, pids = shop
    _set_limit(owner, '5')
    other = _make_user('cashier', cid, pin='5656')                    # not allowed to approve
    for _ in range(4):
        assert _wrong(cashier, manager, pids, pin='7391').status_code == 403
    assert _wrong(cashier, other, pids, pin='5656').status_code == 403
    refused = _q("SELECT user_id, entity_id, details FROM audit_log WHERE action='DISCOUNT_APPROVAL_PIN_REFUSED' "
                 "AND entity_id IN (?,?)", manager.user_uid, other.user_uid)
    assert len([r for r in refused if r['entity_id'] == manager.user_uid]) == 4
    assert len([r for r in refused if r['entity_id'] == other.user_uid]) == 1
    for r in refused:
        assert r['user_id'] == cashier.user_id and 'cashier=%s' % cashier.user_id in r['details']
        assert '7391' not in r['details'] and '5656' not in r['details']
    assert _q("SELECT COUNT(*) AS n FROM audit_log WHERE action='DISCOUNT_APPROVAL_LOCKED' AND entity_id=?",
              manager.user_uid)[0]['n'] == 0
    assert _wrong(cashier, manager, pids, pin='7391').status_code == 403      # the 5th: locks
    locked = _q("SELECT details FROM audit_log WHERE action='DISCOUNT_APPROVAL_LOCKED' AND entity_id=?",
                manager.user_uid)
    assert len(locked) == 1 and '7391' not in locked[0]['details']
    assert _wrong(cashier, manager, pids, pin='7391').status_code == 429      # refused while locked: no new rows
    assert _q("SELECT COUNT(*) AS n FROM audit_log WHERE action='DISCOUNT_APPROVAL_LOCKED' AND entity_id=?",
              manager.user_uid)[0]['n'] == 1


def test_guessing_at_unknown_names_cannot_grow_the_table(shop):
    cid, owner, cashier, _, pids = shop
    _set_limit(owner, '5')
    for i in range(12):
        r = _post(cashier, _cart(pids, cart_discount_amount=0.65, discount_reason='x',
                                 approval={'approver': 'ghost-%d@nowhere' % i, 'pin': PIN}))
        assert r.status_code in (403, 429)
    rows = _q("SELECT approver_uid FROM pin_approval_attempts WHERE company_id=?", cid)
    assert [r['approver_uid'] for r in rows] == [pin_throttle.UNKNOWN_APPROVER]


def test_the_counter_rolls_over_when_the_window_or_the_lock_runs_out():
    """Pure counter, injected clock (no sleeping). Both directions: held inside the window,
    released after it."""
    conn = get_retail_conn()
    try:
        pin_throttle.ensure_table(conn); conn.commit()
        co, who, t0 = 'c-%s' % uuid.uuid4().hex, 'a-1', 1_000_000.0
        for i in range(4):
            assert pin_throttle.reserve_attempt(conn, co, who, now=t0 + i) == (True, 0, False)
        assert pin_throttle.reserve_attempt(conn, co, who, now=t0 + 5) == (True, 0, True)      # 5th locks
        allowed, retry, _ = pin_throttle.reserve_attempt(conn, co, who, now=t0 + 6)
        assert allowed is False and 0 < retry <= pin_throttle.LOCK_SECONDS + 1
        assert pin_throttle.reserve_attempt(conn, co, who, now=t0 + 5 + pin_throttle.LOCK_SECONDS - 1)[0] is False
        assert pin_throttle.reserve_attempt(conn, co, who, now=t0 + 5 + pin_throttle.LOCK_SECONDS + 1) == (True, 0, False)
        # a window that has run out forgets old failures
        co2 = 'c-%s' % uuid.uuid4().hex
        for i in range(4):
            pin_throttle.reserve_attempt(conn, co2, who, now=t0 + i)
        assert pin_throttle.reserve_attempt(conn, co2, who, now=t0 + pin_throttle.WINDOW_SECONDS + 10) == (True, 0, False)
        row = conn.execute("SELECT failures FROM pin_approval_attempts WHERE company_id=?", (co2,)).fetchone()
        assert row[0] == 1
        # release floors at 0
        pin_throttle.release_attempt(conn, co2, who); pin_throttle.release_attempt(conn, co2, who)
        assert conn.execute("SELECT failures FROM pin_approval_attempts WHERE company_id=?", (co2,)).fetchone()[0] == 0
    finally:
        conn.close()
