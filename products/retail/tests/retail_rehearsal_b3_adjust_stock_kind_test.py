"""REHEARSAL-1007 B3: "Adjust stock" must not turn bought stock into profit.

THE DEFECT, measured in the rehearsal: raising a product's stock through the Adjust
dialog posted Dr 1200 Inventory / Cr 5910 Shrinkage-and-Adjustments, and 5910 is an
expense-type account, so a CREDIT to it is a negative expense -- the owner's own money
spent on stock showed up as profit in the P&L.

THE RULE (plan B3): the route takes an optional `reason` kind --
    'added' -- new stock bought outside a purchase order: Dr 1200 / Cr 3900 (the owner's
               money put into stock, the same journal as opening stock at product create)
    'count' -- a stock-count correction: Dr/Cr 5910 (gain or loss), as before
    absent  -- positive quantity -> 'added', negative -> 'count'
    a REDUCTION is always 5910.

Both directions are proven: 'added' must NOT touch 5910/P&L, and 'count' MUST still
(otherwise a count correction would silently stop reaching the books).

Run (one pytest process per file):
    pytest products/retail/tests/retail_rehearsal_b3_adjust_stock_kind_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_rehearsal_b3_"))
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
from core.retail import general_ledger as gl  # noqa: E402
from core.retail import financial_statements as fs  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    company_id = str(uuid.uuid4())
    email = f'b3-{uuid.uuid4().hex[:8]}@test.local'
    password = 'B3PW12345678'  # pragma: allowlist secret
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f'EMP-{uuid.uuid4().hex[:6]}', email, hash_password(password), 'admin', 'active'))
    conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
                 (str(uuid.uuid4()), user_id, 'retail', 'full'))
    user_accounts.seed_capabilities_for_user(conn, user_id, 'admin')
    conn.commit(); conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    c.test_company_id = company_id
    return c


def _product(c, cost=2.0, stock=10):
    r = c.post(f'{API}/products', json={'name': 'B3 Widget', 'sku': f'B3-{uuid.uuid4().hex[:8]}',
                                        'cost_price': cost, 'sell_price': 5.0, 'tax_rate': 0,
                                        'initial_stock': stock})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _adjust(c, pid, qty, **extra):
    return c.post(f'{API}/products/{pid}/stock-adjust', json={'quantity': qty, **extra})


def _bal(cid, code):
    conn = get_retail_conn()
    try:
        return [a for a in gl.trial_balance(conn, cid)['accounts'] if a['code'] == code][0]['balance']
    finally:
        conn.close()


def _net_profit(cid):
    conn = get_retail_conn()
    try:
        return fs.income_statement(conn, cid, '2000-01-01', '2999-12-31')['net_profit']
    finally:
        conn.close()


def test_added_increase_goes_to_owner_equity_and_leaves_profit_alone(client):
    cid = client.test_company_id
    pid = _product(client)                       # 10 x 2.000 opening -> 1200 = 20, 3900 = 20
    assert _bal(cid, gl.ACCOUNT_OPENING_EQUITY) == 20.0
    profit_before = _net_profit(cid)
    r = _adjust(client, pid, 5, reason='added')
    assert r.status_code == 200, r.get_json()
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 30.0           # 15 x 2.000
    assert _bal(cid, gl.ACCOUNT_OPENING_EQUITY) == 30.0      # Cr 3900 10
    assert _bal(cid, gl.ACCOUNT_SHRINKAGE) == 0.0, '5910 must not move for bought stock'
    assert _net_profit(cid) == profit_before, 'bought stock must never show up as profit'


def test_count_increase_is_still_a_gain_on_5910(client):
    cid = client.test_company_id
    pid = _product(client)
    r = _adjust(client, pid, 5, reason='count')
    assert r.status_code == 200, r.get_json()
    assert _bal(cid, gl.ACCOUNT_INVENTORY) == 30.0
    assert _bal(cid, gl.ACCOUNT_SHRINKAGE) == -10.0          # a credit-balance gain
    assert _bal(cid, gl.ACCOUNT_OPENING_EQUITY) == 20.0      # untouched


def test_default_kind_follows_the_sign(client):
    cid = client.test_company_id
    pid = _product(client)
    assert _adjust(client, pid, 5).status_code == 200          # absent + positive -> added
    assert _bal(cid, gl.ACCOUNT_OPENING_EQUITY) == 30.0 and _bal(cid, gl.ACCOUNT_SHRINKAGE) == 0.0
    assert _adjust(client, pid, -3).status_code == 200         # absent + negative -> count
    assert _bal(cid, gl.ACCOUNT_SHRINKAGE) == 6.0 and _bal(cid, gl.ACCOUNT_OPENING_EQUITY) == 30.0
    # a free-text note (the pre-B3 meaning of `reason`) keeps working and uses the default
    assert _adjust(client, pid, 2, reason='Opening stock, shared branch').status_code == 200
    assert _bal(cid, gl.ACCOUNT_OPENING_EQUITY) == 34.0


def test_a_reduction_is_always_5910_and_cannot_be_called_added(client):
    cid = client.test_company_id
    pid = _product(client)
    bad = _adjust(client, pid, -2, reason='added')
    assert bad.status_code == 400, bad.get_json()
    assert _bal(cid, gl.ACCOUNT_SHRINKAGE) == 0.0
    assert _adjust(client, pid, -2, reason='count').status_code == 200
    assert _bal(cid, gl.ACCOUNT_SHRINKAGE) == 4.0


def test_books_still_tie_to_the_shelf_after_added_stock(client):
    cid = client.test_company_id
    pid = _product(client)
    assert _adjust(client, pid, 5, reason='added').status_code == 200
    conn = get_retail_conn()
    try:
        tb = gl.trial_balance(conn, cid)
        tie = gl.inventory_tie(conn, cid, tb)
    finally:
        conn.close()
    assert tb['is_balanced']
    assert tie['status'] == 'ties', tie
