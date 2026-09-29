"""
Aura Retail -- product COST is withheld from sell-only sessions (FINAL polish
4b / F2 server side, crew D).

GET /api/sub/retail/products used to return `cost_price`, `avg_cost` and
`cost_basis` to EVERY signed-in retail user (the row is `SELECT p.*`). A
cashier's till never shows them, but they were in the network response.

The rule under test: cost is returned only to a session that holds
retail.reports (the gate of /products/stock-value) OR retail.stock.adjust
(needed to edit a product / build a purchase order -- the edit modal pre-fills
and re-saves cost, so withholding it there would save cost 0). Everyone else
gets the row WITHOUT those keys. The barcode-scan route /products/lookup
returns the same row shape and goes through the same filter.

BOTH directions are pinned:
  DENY : a plain cashier gets no cost keys on the list or on a scan.
  ALLOW: owner, a cashier granted retail.reports, and a cashier granted
         retail.stock.adjust all still get cost -- and a cashier still gets
         everything ELSE (name, sell_price, stock) so the till keeps working.

Self-contained bootstrap (own AURA_APP_DATA, own Flask app), matching
retail_share_audit_test.py. ONE pytest process per file.

Run:
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest products/retail/tests/retail_products_cost_visibility_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_cost_vis_"))
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

API = '/api/sub/retail'
COST_KEYS = ('cost_price', 'avg_cost', 'cost_basis')
SKU = 'COSTVIS-1'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_user(role, company_id, capabilities=None):
    email = f"costvis-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "CostVisPW1"
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), role, "active"),
    )
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    if capabilities:
        for code, level in capabilities.items():
            conn.execute(
                "UPDATE user_permissions SET access_level=? WHERE user_id=? AND subsystem=?",
                (level, user_id, code),
            )
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client, user_id


@pytest.fixture
def shop():
    company_id = str(uuid.uuid4())
    admin, _ = _make_user('admin', company_id)
    admin.get(f'{API}/settings/tax')
    r = admin.post(f'{API}/products', json={
        'name': 'Cost Vis Widget', 'sku': SKU, 'cost_price': 7.5, 'sell_price': 12.0,
    })
    assert r.status_code in (200, 201), r.get_json()
    return company_id, admin


def _row(client):
    r = client.get(f'{API}/products')
    assert r.status_code == 200, r.get_json()
    rows = [p for p in r.get_json()['data'] if p['sku'] == SKU]
    # Count the check: exactly one row must have matched, or every assertion
    # below would be vacuous (an empty list can never contain a cost key).
    assert len(rows) == 1, f"expected exactly one {SKU} row, got {len(rows)}"
    return rows[0]


def _scan(client):
    r = client.get(f'{API}/products/lookup', query_string={'code': SKU})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


# ── ALLOW half: authorities that legitimately work with cost keep it ────────

def test_owner_still_gets_cost_on_list_and_scan(shop):
    _cid, admin = shop
    row = _row(admin)
    assert row['cost_price'] == 7.5
    assert 'avg_cost' in row and 'cost_basis' in row
    assert _scan(admin)['cost_price'] == 7.5


def test_cashier_granted_reports_still_gets_cost(shop):
    cid, _ = shop
    c, _ = _make_user('cashier', cid, capabilities={'retail.reports': 'full'})
    assert _row(c)['cost_price'] == 7.5
    assert _scan(c)['cost_price'] == 7.5


def test_cashier_granted_stock_adjust_still_gets_cost_so_product_edit_cannot_save_zero(shop):
    cid, _ = shop
    c, _ = _make_user('cashier', cid, capabilities={'retail.stock.adjust': 'full'})
    assert _row(c)['cost_price'] == 7.5
    assert _scan(c)['cost_price'] == 7.5


# ── DENY half: a plain cashier gets no cost, but everything else ────────────

def test_plain_cashier_gets_no_cost_keys_on_list(shop):
    cid, _ = shop
    c, _ = _make_user('cashier', cid)
    row = _row(c)
    for k in COST_KEYS:
        assert k not in row, f"a sell-only session must not receive {k}"
    # ...and the till still gets what it sells with.
    assert row['name'] == 'Cost Vis Widget'
    assert row['sell_price'] == 12.0
    assert 'total_stock' in row and 'has_modifiers' in row


def test_plain_cashier_gets_no_cost_keys_on_barcode_scan(shop):
    cid, _ = shop
    c, _ = _make_user('cashier', cid)
    row = _scan(c)
    for k in COST_KEYS:
        assert k not in row, f"the scan route must not re-open the leak: {k}"
    assert row['sell_price'] == 12.0


def test_withholding_cost_does_not_change_which_rows_come_back(shop):
    # The filter drops KEYS, never rows: same product count for both roles.
    cid, admin = shop
    c, _ = _make_user('cashier', cid)
    a = admin.get(f'{API}/products').get_json()['data']
    b = c.get(f'{API}/products').get_json()['data']
    assert len(a) == len(b) >= 1
