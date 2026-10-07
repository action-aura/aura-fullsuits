"""Owner-set cashier discount limit.

A session WITHOUT retail.discount (a cashier) used to be refused any discount.
The owner can now allow up to N percent (setting cashier_discount_limit_pct,
default 0 = unchanged behaviour). Managers/owner hold retail.discount and are
never limited. Every refusal must write nothing; the setting itself is
owner-only, validated before any write, and FAILS CLOSED on garbage.

Run:
    pytest products/retail/tests/retail_cashier_discount_limit_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_cashdisc_"))
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
from database.schema import get_retail_conn  # noqa: E402
import api.retail_api as retail_api  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_user(role, company_id, capabilities=None):
    email = f"cd-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "CashDiscPW1"
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), role, "active"))
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"))
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    for code, level in (capabilities or {}).items():
        conn.execute("UPDATE user_permissions SET access_level=? WHERE user_id=? AND subsystem=?",
                     (level, user_id, code))
    conn.commit(); conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return c


@pytest.fixture
def shop():
    company_id = str(uuid.uuid4())
    owner = _make_user('admin', company_id)
    owner.get(f'{API}/settings/tax')
    r = owner.post(f'{API}/products', json={
        'name': 'Disc Widget', 'sku': f'CD-{uuid.uuid4().hex[:8]}', 'cost_price': 1,
        'sell_price': 100.0, 'tax_rate': 0, 'initial_stock': 1000})
    assert r.status_code == 200, r.get_json()
    pid = r.get_json()['data']['id']
    cashier = _make_user('cashier', company_id)
    return company_id, owner, cashier, pid


def _set_limit(owner, v):
    return owner.post(f'{API}/settings/credit', json={'cashier_discount_limit_pct': v})


def _sell(c, pid, pcts):
    items = [{'product_id': pid, 'quantity': 1, 'discount_pct': p} for p in pcts]
    return c.post(f'{API}/sales', json={'items': items, 'payment_method': 'cash',
                                        'idempotency_key': str(uuid.uuid4())})


def _count(sql, params=()):
    conn = get_retail_conn()
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def _state():
    return (_count("SELECT COUNT(*) FROM sales"), _count("SELECT COUNT(*) FROM payments"),
            _count("SELECT COUNT(*) FROM sync_outbox"))


def _audit_actions(owner):
    r = owner.get(f'{API}/audit-log?limit=200')
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    rows = d if isinstance(d, list) else d.get('rows') or d.get('entries') or []
    return rows


def test_1_default_zero_refuses_with_the_old_message_and_no_code(shop):
    _, _, cashier, pid = shop
    before = _state()
    r = _sell(cashier, pid, [5])
    assert r.status_code == 403
    body = r.get_json()
    assert body['message'] == retail_api.DISCOUNT_DENIED_MESSAGE and 'code' not in body
    assert _state() == before


def test_2_limit_set_allows_a_discount_within_it_and_audits_it(shop):
    _, owner, cashier, pid = shop
    assert _set_limit(owner, '10').status_code == 200
    r = _sell(cashier, pid, [5])
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['discount_amount'] == 5.0
    acts = [a.get('action') for a in _audit_actions(owner)]
    assert 'SALE_DISCOUNT_BY_CASHIER' in acts, acts


def test_3_exactly_the_limit_is_allowed(shop):
    _, owner, cashier, pid = shop
    _set_limit(owner, '10')
    assert _sell(cashier, pid, [10]).status_code == 200


def test_4_just_over_the_limit_is_refused_with_data_and_writes_nothing(shop):
    _, owner, cashier, pid = shop
    _set_limit(owner, '10')
    before = _state()
    r = _sell(cashier, pid, [10.5])
    assert r.status_code == 403
    b = r.get_json()
    assert b['code'] == 'DISCOUNT_OVER_LIMIT' and b['data']['limit_pct'] == 10
    assert '10%' in b['message']
    assert _state() == before


def test_5_the_largest_line_decides(shop):
    _, owner, cashier, pid = shop
    _set_limit(owner, '10')
    assert _sell(cashier, pid, [5, 15]).status_code == 403


def test_6_manager_and_owner_are_not_limited(shop):
    company_id, owner, _, pid = shop
    _set_limit(owner, '10')
    manager = _make_user('manager', company_id)
    assert _sell(manager, pid, [50]).status_code == 200
    assert _sell(owner, pid, [80]).status_code == 200


def test_7_only_the_owner_can_set_the_limit(shop):
    company_id, owner, cashier, _ = shop
    assert _set_limit(cashier, '50').status_code == 403
    branch_manager = _make_user('manager', company_id, {'retail.employees': 'full'})
    r = _set_limit(branch_manager, '50')
    assert r.status_code == 403 and r.get_json().get('code') == 'OWNER_ONLY'
    n = _count("SELECT COUNT(*) FROM sync_outbox WHERE entity_type='retail_setting'")
    assert _set_limit(owner, '12.5').status_code == 200
    assert _count("SELECT COUNT(*) FROM sync_outbox WHERE entity_type='retail_setting'") == n + 1
    assert owner.get(f'{API}/settings/credit').get_json()['data']['cashier_discount_limit_pct'] == '12.5'


@pytest.mark.parametrize('bad', ['-1', '101', 'abc', 'nan', 'inf', ''])
def test_8_bad_values_are_refused_and_nothing_is_stored(shop, bad):
    _, owner, _, _ = shop
    r = _set_limit(owner, bad)
    assert r.status_code == 400 and r.get_json()['code'] == 'INVALID_DISCOUNT_LIMIT'
    assert owner.get(f'{API}/settings/credit').get_json()['data']['cashier_discount_limit_pct'] == '0'


def test_9_garbage_in_the_table_fails_closed(shop):
    company_id, _, cashier, pid = shop
    conn = get_retail_conn()
    conn.execute("INSERT OR REPLACE INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)",
                 (company_id, 'cashier_discount_limit_pct', 'banana'))
    conn.commit(); conn.close()
    assert _sell(cashier, pid, [1]).status_code == 403


def test_10_loyalty_redemption_is_a_tender_a_cashier_may_take(shop):
    """INVERTED 2026-10-07 (rehearsal fix B4, cfbf883f). This used to pin that
    a cashier is refused 403 when a customer pays with points, because
    redemption borrowed the discount capability. The two-till rehearsal
    showed a cashier could not take a points payment at all. Points are a
    way of PAYING, not a discount: any seller may take them, and they must
    not count against the cashier's own discount limit (50% here, a 5-point
    payment on this sale would not trip it anyway -- what is pinned is the
    200 and the ledger row). WHAT THIS CAN NO LONGER CATCH: a redemption
    accidentally re-gated on retail.discount is caught here only as a 403
    instead of 200 -- still red, so nothing is lost."""
    company_id, owner, cashier, pid = shop
    _set_limit(owner, '50')
    cust = owner.post(f'{API}/customers', json={'name': 'Pts Customer'}).get_json()['data']['id']
    conn = get_retail_conn()
    conn.execute("INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type) VALUES (?,?,?,?,'opening')",
                 (str(uuid.uuid4()), company_id, cust, 5))
    conn.execute("INSERT OR REPLACE INTO retail_settings (company_id,skey,svalue) VALUES (?,'loyalty_point_value','1')",
                 (company_id,))
    conn.commit(); conn.close()
    body = {'items': [{'product_id': pid, 'quantity': 1}], 'payment_method': 'cash',
            'customer_id': cust, 'points_redeemed': 5}
    r = cashier.post(f'{API}/sales', json=dict(body, idempotency_key=str(uuid.uuid4())))
    assert r.status_code == 200, r.get_json()
    conn = get_retail_conn()
    try:
        spent = conn.execute(
            "SELECT COALESCE(SUM(points_delta),0) FROM loyalty_ledger "
            "WHERE company_id=? AND customer_id=? AND entry_type='redeem'", (company_id, cust)).fetchone()[0]
    finally:
        conn.close()
    assert spent == -5, f'the cashier\'s points payment must be recorded as -5 redeemed, got {spent}'


def test_11_setting_change_is_audited(shop):
    _, owner, _, _ = shop
    _set_limit(owner, '15')
    acts = [a.get('action') for a in _audit_actions(owner)]
    assert 'CASHIER_DISCOUNT_LIMIT_CHANGED' in acts, acts
