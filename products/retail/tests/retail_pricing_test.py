"""
Aura Retail -- tax-calculation-policy and revenue-dashboard regression suite.

Ported from Action Aura Enterprise's tests/retail_pricing_test.py (Retail
Phase 1 hardening pass) -- see docs/migration/retail-extraction-report.md.
Business-logic assertions are unchanged; only the bootstrap (which app module
to boot, which registry/security modules to import from) is adapted to this
product's standalone layout.

Part A exercises core/retail/pricing.py directly (pure unit tests, no app
boot needed).

Part B boots the Flask app once against a throwaway temp app-data directory
and exercises the real HTTP routes: the /settings/tax endpoint, sale + return
creation, and the dashboard's revenue/returns figures end to end.

Run:
    pytest products/retail/tests/retail_pricing_test.py -v
"""
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]        # products/retail
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent                    # aura-fullsuits
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ═════════════════════════════════════════════════════════════════════════════
# Part A -- core/retail/pricing.py (pure unit tests)
# ═════════════════════════════════════════════════════════════════════════════

from core.retail import pricing  # noqa: E402


def test_sale_without_discount_after_mode():
    r = pricing.calculate_line(unit_price=100, quantity=1, discount_pct=0, tax_rate=15, mode=pricing.TAX_AFTER_DISCOUNT)
    assert r['taxable_amount'] == 100.0
    assert r['tax'] == 15.0
    assert r['total'] == 115.0


def test_sale_with_discount_after_mode():
    r = pricing.calculate_line(unit_price=100, quantity=1, discount_pct=10, tax_rate=15, mode=pricing.TAX_AFTER_DISCOUNT)
    assert r['discount_amount'] == 10.0
    assert r['taxable_amount'] == 90.0
    assert r['tax'] == 13.5
    assert r['total'] == 103.5


def test_tax_before_discount_mode():
    r = pricing.calculate_line(unit_price=100, quantity=1, discount_pct=10, tax_rate=15, mode=pricing.TAX_BEFORE_DISCOUNT)
    assert r['taxable_amount'] == 100.0
    assert r['tax'] == 15.0
    assert r['discount_amount'] == 10.0
    assert r['total'] == 105.0


def test_tax_after_discount_mode_is_the_default():
    assert pricing.DEFAULT_MODE == pricing.TAX_AFTER_DISCOUNT


def test_before_and_after_discount_modes_diverge_when_discount_is_nonzero():
    after = pricing.calculate_line(100, 1, discount_pct=20, tax_rate=10, mode=pricing.TAX_AFTER_DISCOUNT)
    before = pricing.calculate_line(100, 1, discount_pct=20, tax_rate=10, mode=pricing.TAX_BEFORE_DISCOUNT)
    assert after['tax'] != before['tax']
    assert after['total'] != before['total']
    assert before['tax'] > after['tax']


def test_before_and_after_discount_modes_agree_when_no_discount():
    after = pricing.calculate_line(100, 1, discount_pct=0, tax_rate=10, mode=pricing.TAX_AFTER_DISCOUNT)
    before = pricing.calculate_line(100, 1, discount_pct=0, tax_rate=10, mode=pricing.TAX_BEFORE_DISCOUNT)
    assert after == before


def test_unknown_mode_falls_back_to_default():
    assert pricing.normalize_mode('nonsense') == pricing.DEFAULT_MODE
    assert pricing.normalize_mode(None) == pricing.DEFAULT_MODE
    assert pricing.normalize_mode('') == pricing.DEFAULT_MODE
    r_unknown = pricing.calculate_line(100, 1, discount_pct=10, tax_rate=15, mode='nonsense')
    r_default = pricing.calculate_line(100, 1, discount_pct=10, tax_rate=15, mode=pricing.DEFAULT_MODE)
    assert r_unknown == r_default


def test_multi_quantity_line():
    r = pricing.calculate_line(unit_price=19.99, quantity=3, discount_pct=5, tax_rate=8, mode=pricing.TAX_AFTER_DISCOUNT)
    gross = 19.99 * 3
    assert r['gross'] == round(gross, 2)
    assert r['total'] == round(round(gross - gross * 0.05, 2) * 1.08, 2)


def test_float_precision_rounding():
    r = pricing.calculate_line(unit_price=10.1, quantity=3, discount_pct=7.5, tax_rate=8.25, mode=pricing.TAX_AFTER_DISCOUNT)
    for key in ('gross', 'discount_amount', 'taxable_amount', 'tax', 'total'):
        val = r[key]
        assert round(val, 2) == val, f'{key}={val!r} has more than 2 decimal places'


def test_zero_tax_rate():
    r = pricing.calculate_line(50, 2, discount_pct=10, tax_rate=0, mode=pricing.TAX_AFTER_DISCOUNT)
    assert r['tax'] == 0.0
    assert r['total'] == r['taxable_amount']


def test_calculate_invoice_matches_calculate_line_for_a_single_line():
    line = pricing.calculate_line(100, 2, discount_pct=15, tax_rate=12, mode=pricing.TAX_BEFORE_DISCOUNT)
    inv = pricing.calculate_invoice(subtotal=200, discount_amount=line['discount_amount'],
                                     tax_rate_pct=12, mode=pricing.TAX_BEFORE_DISCOUNT)
    assert inv['tax'] == line['tax']
    assert inv['total'] == line['total']


# ═════════════════════════════════════════════════════════════════════════════
# Part B -- integration (real HTTP routes, isolated temp DB)
# ═════════════════════════════════════════════════════════════════════════════

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_pricing_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin_and_client():
    email = f"pricing-{uuid.uuid4().hex[:10]}@test.local"
    password = "PricingTestPW1"
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

    rconn = get_retail_conn()
    rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = rconn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)
    ).fetchone()[0]
    rconn.execute(
        "INSERT INTO products (company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?, 'PRC-1','Priced Item',5,100,15)",
        (company_id,),
    )
    product_id = rconn.execute(
        "SELECT id FROM products WHERE company_id=? AND sku='PRC-1'", (company_id,)
    ).fetchone()[0]
    rconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,100)",
        (company_id, product_id, branch_id),
    )
    rconn.commit()
    rconn.close()

    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})

    import random as _random
    client.get("/api/sub/retail/settings/tax")  # ensures _ensure_credit_schema() has run (creates doc_sequences)
    dconn = get_retail_conn()
    dconn.execute(
        "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
        (company_id, _random.randint(1, 5_000_000)),
    )
    dconn.commit()
    dconn.close()

    return client, company_id, product_id, branch_id


def _create_sale(client, product_id, quantity, unit_price=100.0, discount_pct=0, tax_rate=15.0, mode=pricing.TAX_AFTER_DISCOUNT):
    calc = pricing.calculate_line(unit_price, quantity, discount_pct, tax_rate, mode=mode)
    payload = {
        "items": [{
            "product_id": product_id, "quantity": quantity, "unit_price": unit_price,
            "discount_pct": discount_pct, "tax_rate": tax_rate,
            "line_total": round(calc['gross'] - calc['discount_amount'], 2),
        }],
        "subtotal": round(calc['gross'] - calc['discount_amount'], 2),
        "discount_amount": calc['discount_amount'],
        "tax_amount": calc['tax'],
        "total": calc['total'],
        "amount_paid": calc['total'],
        "payment_method": "cash",
        "idempotency_key": str(uuid.uuid4()),
    }
    r = client.post("/api/sub/retail/sales", json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"], calc


def _create_return(client, sale_id, product_id, quantity, unit_price, line_total, reason="test"):
    r = client.post("/api/sub/retail/returns", json={
        "sale_id": sale_id,
        "items": [{"product_id": product_id, "quantity": quantity, "unit_price": unit_price, "line_total": line_total}],
        "reason": reason,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


def _dashboard(client):
    r = client.get("/api/sub/retail/dashboard/stats")
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


# ── Tax settings API ─────────────────────────────────────────────────────────

def test_tax_settings_default_is_after_discount():
    client, _cid, _pid, _bid = _make_admin_and_client()
    r = client.get("/api/sub/retail/settings/tax")
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["tax_calculation_mode"] == "after_discount"
    assert set(data["available_modes"]) == {"after_discount", "before_discount"}


def test_tax_settings_can_be_changed_and_persists():
    client, _cid, _pid, _bid = _make_admin_and_client()
    r = client.post("/api/sub/retail/settings/tax", json={"tax_calculation_mode": "before_discount"})
    assert r.status_code == 200
    assert r.get_json()["data"]["tax_calculation_mode"] == "before_discount"

    r2 = client.get("/api/sub/retail/settings/tax")
    assert r2.get_json()["data"]["tax_calculation_mode"] == "before_discount"


def test_tax_settings_rejects_missing_field():
    client, _cid, _pid, _bid = _make_admin_and_client()
    r = client.post("/api/sub/retail/settings/tax", json={})
    assert r.status_code == 400


def test_tax_settings_normalizes_invalid_value():
    client, _cid, _pid, _bid = _make_admin_and_client()
    r = client.post("/api/sub/retail/settings/tax", json={"tax_calculation_mode": "garbage"})
    assert r.status_code == 200
    assert r.get_json()["data"]["tax_calculation_mode"] == "after_discount"


def test_tax_settings_scoped_per_company():
    client_a, cid_a, _pid_a, _bid = _make_admin_and_client()
    client_b, cid_b, _pid_b, _bid = _make_admin_and_client()
    client_a.post("/api/sub/retail/settings/tax", json={"tax_calculation_mode": "before_discount"})
    r_b = client_b.get("/api/sub/retail/settings/tax")
    assert r_b.get_json()["data"]["tax_calculation_mode"] == "after_discount"


# ── Sale / return regression coverage ───────────────────────────────────────

def test_sale_without_discount():
    client, _cid, pid, _bid = _make_admin_and_client()
    sale, calc = _create_sale(client, pid, quantity=1, discount_pct=0)
    assert calc['total'] == 115.0
    assert sale['total'] == 115.0


def test_sale_with_discount():
    client, _cid, pid, _bid = _make_admin_and_client()
    sale, calc = _create_sale(client, pid, quantity=1, discount_pct=10)
    assert calc['total'] == 103.5
    assert sale['total'] == 103.5


def test_sale_before_discount_mode_end_to_end():
    client, cid, pid, _bid = _make_admin_and_client()
    client.post("/api/sub/retail/settings/tax", json={"tax_calculation_mode": "before_discount"})
    sale, calc = _create_sale(client, pid, quantity=1, discount_pct=10, mode=pricing.TAX_BEFORE_DISCOUNT)
    assert calc['total'] == 105.0
    assert sale['total'] == 105.0


def test_sale_after_discount_mode_end_to_end():
    client, cid, pid, _bid = _make_admin_and_client()
    client.post("/api/sub/retail/settings/tax", json={"tax_calculation_mode": "after_discount"})
    sale, calc = _create_sale(client, pid, quantity=1, discount_pct=10, mode=pricing.TAX_AFTER_DISCOUNT)
    assert calc['total'] == 103.5
    assert sale['total'] == 103.5


def test_partial_return():
    client, cid, pid, bid = _make_admin_and_client()
    sale, calc = _create_sale(client, pid, quantity=4, discount_pct=0)
    rconn = get_retail_conn()
    before = rconn.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?", (cid, pid, bid)
    ).fetchone()[0]
    rconn.close()

    ret = _create_return(client, sale['id'], pid, quantity=1, unit_price=100.0, line_total=100.0)
    # Wave 0 / AUDIT-004 intentional rule change: refund_amount is now
    # recomputed server-side from the ORIGINAL sale_items row (unit_price,
    # discount_pct, tax_rate) and includes tax, since the customer paid tax
    # on this line -- a full refund must return the tax too, not just the
    # pre-tax taxable amount. The sold item here has tax_rate=15, so 1 unit
    # at $100 refunds $115, not $100. See
    # docs/corrections/wave0/retail-return-correction.md.
    assert ret['refund_amount'] == 115.0

    rconn = get_retail_conn()
    after = rconn.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?", (cid, pid, bid)
    ).fetchone()[0]
    rconn.close()
    assert after == before + 1


def test_full_return():
    client, cid, pid, _bid = _make_admin_and_client()
    sale, calc = _create_sale(client, pid, quantity=2, discount_pct=0)
    ret = _create_return(client, sale['id'], pid, quantity=2, unit_price=100.0, line_total=200.0)
    # Wave 0 / AUDIT-004: tax-inclusive refund (2 * $115), see test_partial_return.
    assert ret['refund_amount'] == 230.0


def test_multi_line_return():
    client, cid, pid, bid = _make_admin_and_client()
    rconn = get_retail_conn()
    rconn.execute("INSERT INTO products (company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?, 'PRC-2','Second Item',2,50,15)", (cid,))
    pid2 = rconn.execute("SELECT id FROM products WHERE company_id=? AND sku='PRC-2'", (cid,)).fetchone()[0]
    rconn.execute("INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,50)", (cid, pid2, bid))
    rconn.commit(); rconn.close()

    calc1 = pricing.calculate_line(100, 1, 0, 15)
    calc2 = pricing.calculate_line(50, 2, 0, 15)
    payload = {
        "items": [
            {"product_id": pid, "quantity": 1, "unit_price": 100, "discount_pct": 0, "tax_rate": 15, "line_total": calc1['taxable_amount']},
            {"product_id": pid2, "quantity": 2, "unit_price": 50, "discount_pct": 0, "tax_rate": 15, "line_total": calc2['taxable_amount']},
        ],
        "subtotal": calc1['taxable_amount'] + calc2['taxable_amount'],
        "discount_amount": 0,
        "tax_amount": calc1['tax'] + calc2['tax'],
        "total": calc1['total'] + calc2['total'],
        "amount_paid": calc1['total'] + calc2['total'],
        "payment_method": "cash",
        "idempotency_key": str(uuid.uuid4()),
    }
    r = client.post("/api/sub/retail/sales", json=payload)
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()["data"]

    r2 = client.post("/api/sub/retail/returns", json={
        "sale_id": sale['id'],
        "items": [
            {"product_id": pid, "quantity": 1, "unit_price": 100, "line_total": 100.0},
            {"product_id": pid2, "quantity": 2, "unit_price": 50, "line_total": 100.0},
        ],
        "reason": "multi-line test",
    })
    assert r2.status_code == 200, r2.get_json()
    # Wave 0 / AUDIT-004: tax-inclusive refund -- line 1 ($100 * 1, 15% tax)
    # refunds $115, line 2 ($50 * 2, 15% tax) refunds $115, total $230.
    assert r2.get_json()["data"]["refund_amount"] == 230.0


# ── Dashboard / revenue regression coverage ─────────────────────────────────

def test_month_revenue_calculation():
    client, cid, pid, _bid = _make_admin_and_client()
    _create_sale(client, pid, quantity=1, discount_pct=0)
    _create_sale(client, pid, quantity=2, discount_pct=0)
    d = _dashboard(client)
    assert d["month_sales"] == pytest.approx(345.0, abs=0.01)
    assert d["today_sales"] == pytest.approx(345.0, abs=0.01)


def test_revenue_becoming_negative_is_not_clamped():
    client, cid, pid, _bid = _make_admin_and_client()
    sale, _calc = _create_sale(client, pid, quantity=1, discount_pct=0)
    time.sleep(1.1)
    _create_return(client, sale['id'], pid, quantity=1, unit_price=100.0, line_total=100.0)
    rconn = get_retail_conn()
    today = time.strftime('%Y-%m-%d')
    rconn.execute(
        "INSERT INTO returns (company_id,return_number,branch_id,cashier,reason,refund_method,refund_amount,status,created_at) "
        "VALUES (?,?,1,'test','extra','cash',?, 'completed', ?)",
        (cid, f'RET-EXTRA-{uuid.uuid4().hex[:8]}', 500.0, f'{today} 12:00:00'),
    )
    rconn.commit()
    rconn.close()

    d = _dashboard(client)
    assert d["today_sales"] < 0, d
    # Wave 0 / AUDIT-004: the real return above now refunds $115 (tax-inclusive,
    # see test_partial_return), not $100 -- so today_sales = 115 (sale) - 115
    # (real return) - 500 (raw-inserted extra return) = -500.
    assert d["today_sales"] == pytest.approx(115.0 - 115.0 - 500.0, abs=0.01)


def test_dashboard_breakdown_internally_consistent():
    client, cid, pid, _bid = _make_admin_and_client()
    _create_sale(client, pid, quantity=1, discount_pct=0)
    time.sleep(1.1)
    sale2, _ = _create_sale(client, pid, quantity=1, discount_pct=0)
    _create_return(client, sale2['id'], pid, quantity=1, unit_price=100.0, line_total=100.0)

    d = _dashboard(client)
    gross_from_net_plus_returns = d["today_sales"] + d["today_returns"]
    rconn = get_retail_conn()
    today = time.strftime('%Y-%m-%d')
    true_gross = rconn.execute(
        "SELECT COALESCE(SUM(total),0) FROM sales WHERE company_id=? AND date(created_at)=?", (cid, today)
    ).fetchone()[0]
    rconn.close()
    assert gross_from_net_plus_returns == pytest.approx(true_gross, abs=0.01)
    # Wave 0 / AUDIT-004: tax-inclusive refund, see test_partial_return.
    assert d["today_returns"] == pytest.approx(115.0, abs=0.01)
