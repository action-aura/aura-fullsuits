"""
Aura Retail -- P1c: the shop currency is a CHOICE, validated, and a change over
existing sales needs an explicit yes.

THE OWNER'S REPORT: "I could not find where to change the currency." There was
no field anywhere. The only writer, POST /settings/credit, stored whatever
string it was handed ("dollars", "$", "") unvalidated and with no regard for
sales already on the books.

WHAT IS PINNED (real HTTP, real owner, real delegated manager -- the same
fixtures retail_p0_owner_guard_test.py uses, so the OWNER_ONLY fingerprint means
the same thing here):

  1. an unknown code is refused 400 UNKNOWN_CURRENCY and the stored currency is
     untouched (a request that also carries a valid key writes NOTHING);
  2. a lower-case code is normalised to upper case;
  3. with NO completed sale a change just works and the till's money settings
     (symbol, decimals) follow it;
  4. with a completed sale a change is refused 409 CURRENCY_HAS_SALES unless the
     request says confirm_existing_sales: true -- and the refusal changes
     nothing; with the confirmation it succeeds; re-sending the CURRENT
     currency is never refused (it changes nothing);
  5. a change NEVER rewrites a stored amount (the past sale's total is
     byte-identical afterwards);
  6. a non-admin holding the branch-staff delegation is refused 403 OWNER_ONLY
     (the existing guard; the new check must not sit in front of it);
  7. the list offered to the owner covers every code either currency table can
     already format (drift guard), JOD first, each with a name, symbol, decimals.

The sync event for the write is pinned by retail_settings_sync_emit_test.py (the
JOD/USD writes there still pass through this validation unchanged).

Run (ONE FILE PER PROCESS):
    pytest products/retail/tests/retail_currency_setting_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_currency_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
import api.retail_api as retail_api_module  # noqa: E402
from core.retail import pricing as tax_engine  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_user(role, *, company_id=None, branch_scope_uid=None):
    email = f"cur-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "CurrencyPW1"
    company_id = company_id or str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change, branch_scope_uid) VALUES (?,?,?,?,?,?,?,0,?)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password),
         role, "active", branch_scope_uid),
    )
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    conn.commit()
    conn.close()
    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client, company_id, user_id, email, password


@pytest.fixture(autouse=True)
def _runs_as_the_main_computer(monkeypatch):
    from commercial_runtime.identity import device_context
    monkeypatch.setattr(device_context, 'local_device_is_admin', lambda conn, cid: True)


@pytest.fixture(scope='module')
def shop():
    """An owner and a manager delegated "Manage Branch Staff" the way
    employees.js grants it (see retail_p0_owner_guard_test.py for why both
    grant routes run for real)."""
    owner, company_id, _oid, _oe, _op = _make_user('admin')
    owner.get(f'{API}/settings/tax')                       # runs the lazy settings schema
    conn = get_retail_conn()
    branch_id = retail_api_module._default_branch(conn, company_id)
    branch_uid = conn.execute("SELECT uid FROM branches WHERE id=?", (branch_id,)).fetchone()['uid']
    conn.commit()
    conn.close()
    manager, _, manager_id, m_email, m_pw = _make_user('manager', company_id=company_id)
    assert owner.put(f'/api/admin/employees/{manager_id}/branch-scope',
                     json={'branch_scope_uid': branch_uid}).status_code == 200
    assert owner.post(f'/api/admin/employees/{manager_id}/permissions',
                      json={'subsystem': 'retail.employees', 'access_level': 'full'}).status_code == 200
    assert manager.post('/api/auth/login', json={'email': m_email, 'password': m_pw}).status_code == 200
    return {'owner': owner, 'manager': manager, 'company_id': company_id}


def _currency(owner):
    return owner.get(f'{API}/settings/tax').get_json()['data']['base_currency']


def _set(owner, value, **extra):
    return owner.post(f'{API}/settings/credit', json=dict({'base_currency': value}, **extra))


def _sale(owner):
    r = owner.post(f'{API}/products', json={'name': 'Cur Widget', 'sku': f'CUR-{uuid.uuid4().hex[:8]}',
                                           'sell_price': 12.5, 'tax_rate': 0, 'initial_stock': 50})
    assert r.status_code == 200, r.get_json()
    pid = r.get_json()['data']['id']
    r = owner.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 2, 'unit_price': 12.5}],
        'payment_method': 'cash', 'amount_paid': 25, 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


# ── 1. unknown code ──────────────────────────────────────────────────────────
def test_unknown_currency_is_refused_and_nothing_is_written(shop):
    owner = shop['owner']
    before = _currency(owner)
    for bad in ('dollars', '$', '', 'ZZZ', 'US', None):
        r = _set(owner, bad, default_credit_mode='limited')
        assert r.status_code == 400, f'{bad!r} was accepted: {r.get_json()}'
        assert r.get_json()['code'] == 'UNKNOWN_CURRENCY', r.get_json()
    assert _currency(owner) == before, 'a refused currency still changed the stored one'
    # all-or-nothing: the valid sibling key in those requests must NOT have been written
    assert owner.get(f'{API}/settings/credit').get_json()['data'].get('default_credit_mode') != 'limited', \
        'a request refused for its currency still wrote its other keys'


# ── 2/3. normalisation, and a change on a shop with no sales ─────────────────
def test_lowercase_is_normalised_and_the_till_follows_with_no_sales(shop):
    owner = shop['owner']
    r = _set(owner, 'usd')
    assert r.status_code == 200, r.get_json()
    d = owner.get(f'{API}/settings/tax').get_json()['data']
    assert d['base_currency'] == 'USD'
    assert d['currency_decimals'] == 2 and d['currency_symbol'] == '$'
    r = _set(owner, 'JOD')
    assert r.status_code == 200
    d = owner.get(f'{API}/settings/tax').get_json()['data']
    assert d['currency_decimals'] == 3 and d['currency_symbol'] == 'JD'


# ── 4/5. with a completed sale: refuse, then confirm; amounts never move ──────
def test_change_over_existing_sales_needs_the_explicit_confirmation(shop):
    owner = shop['owner']
    assert _currency(owner) == 'JOD'
    sale_id = _sale(owner)
    conn = get_retail_conn()
    total_before = conn.execute("SELECT total FROM sales WHERE id=?", (sale_id,)).fetchone()['total']
    conn.close()

    r = _set(owner, 'USD')
    assert r.status_code == 409 and r.get_json()['code'] == 'CURRENCY_HAS_SALES', r.get_json()
    assert r.get_json()['sales_count'] >= 1
    assert _currency(owner) == 'JOD', 'the refusal changed the currency anyway'

    r = _set(owner, 'USD', confirm_existing_sales=False)
    assert r.status_code == 409, 'a falsy confirmation was treated as a yes'
    r = _set(owner, 'USD', confirm_existing_sales='yes')
    assert r.status_code == 409, 'a truthy-but-not-true confirmation was treated as a yes'
    assert _currency(owner) == 'JOD'

    # the allow half: the same request with the explicit confirmation succeeds
    r = _set(owner, 'USD', confirm_existing_sales=True)
    assert r.status_code == 200, r.get_json()
    assert _currency(owner) == 'USD'

    conn = get_retail_conn()
    total_after = conn.execute("SELECT total FROM sales WHERE id=?", (sale_id,)).fetchone()['total']
    conn.close()
    assert total_after == total_before, 'changing the currency rewrote a stored sale amount'

    # re-sending the CURRENT currency changes nothing, so it is never refused
    r = _set(owner, 'USD')
    assert r.status_code == 200, r.get_json()
    # and going back needs the confirmation again (still a change over sales)
    assert _set(owner, 'JOD').status_code == 409
    assert _set(owner, 'JOD', confirm_existing_sales=True).status_code == 200


# ── 6. the owner-only guard still comes first for a delegated manager ─────────
def test_a_delegated_manager_is_still_refused_as_owner_only(shop):
    manager = shop['manager']
    r = manager.post(f'{API}/settings/credit', json={'base_currency': 'EUR'})
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()
    assert _currency(shop['owner']) == 'JOD'


# ── 7. the offered list ───────────────────────────────────────────────────────
def test_the_offered_list_covers_every_currency_the_server_can_format(shop):
    listed = shop['owner'].get(f'{API}/settings/tax').get_json()['data']['available_currencies']
    codes = [c['code'] for c in listed]
    assert codes[0] == 'JOD', 'the home currency must be first'
    known = set(tax_engine.CURRENCY_MINOR_UNITS) | set(tax_engine.CURRENCY_SYMBOLS)
    assert set(codes) == known, f'the list drifted from the server tables: {set(codes) ^ known}'
    for c in listed:
        assert c['code'] in retail_api_module.SHOP_CURRENCY_NAMES, f"{c['code']} has no display name"
        assert c['name'] and c['symbol'] and isinstance(c['decimals'], int)
    jod = next(c for c in listed if c['code'] == 'JOD')
    assert jod['decimals'] == 3 and jod['symbol'] == 'JD'
