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
# Part A2 -- money-arithmetic reconciliation (Wave 0 correction).
#
# THE DEFECT this section guards: `calculate_line` used to compute `total`
# from FULL-PRECISION `taxable_amount`/`tax`, independently of the ALREADY-
# ROUNDED `discount_amount` a caller actually sees and sums -- so
# `gross - discount_amount + tax == total` was not guaranteed. Measured on a
# real running till: a 7.5% discount on a 2.50 JOD item (3 units) persisted
# total=8.048 while subtotal-discount+tax=8.047 -- an ordinary, not an edge,
# transaction. Every test below uses Decimal-exact equality (no
# pytest.approx/tolerance): the whole point is that the identity holds
# EXACTLY, not approximately.
# ═════════════════════════════════════════════════════════════════════════════
from decimal import Decimal as _Decimal, ROUND_HALF_UP as _ROUND_HALF_UP  # noqa: E402


def _reconciles(calc):
    """gross - discount_amount + tax == total, as exact Decimals (never as
    floats -- float subtraction of two already-rounded money values can
    itself introduce a spurious last-bit mismatch that has nothing to do
    with the defect under test)."""
    lhs = (_Decimal(str(calc['gross'])) - _Decimal(str(calc['discount_amount']))
           + _Decimal(str(calc['tax'])))
    return lhs == _Decimal(str(calc['total']))


def test_calculate_line_reconciles_with_discount():
    """THE measured failing case (SALE-000018 in the live-till reproduction):
    2.50 JOD x 3, 7.5% discount, 16% tax. Before the fix: gross=7.500,
    discount=0.563, tax=1.110 (sum 8.047) but total was independently rounded
    to 8.048."""
    r = pricing.calculate_line(2.5, 3, discount_pct=7.5, tax_rate=16, currency='JOD')
    assert r['gross'] == 7.5
    assert r['discount_amount'] == 0.563
    assert r['tax'] == 1.11
    assert r['total'] == 8.047, f"got {r['total']!r}"
    assert _reconciles(r)


def test_calculate_line_reconciles_awkward_discount():
    """THE measured +0.001 case (SALE-000019): 10.0 JOD x 1, 3.3333%
    discount, 16% tax -- the opposite sign from the test above, proving the
    fix is not a one-direction coincidence."""
    r = pricing.calculate_line(10.0, 1, discount_pct=3.3333, tax_rate=16, currency='JOD')
    assert r['discount_amount'] == 0.333
    assert r['total'] == 11.214, f"got {r['total']!r}"
    assert _reconciles(r)


def test_calculate_line_many_lines_sum_reconciles():
    """THE measured case (SALE-000020): two lines, each individually
    reconciling (guards a fix applied only at the header) AND the SALE-level
    sum reconciling too (guards a fix that reconciles each line but somehow
    still breaks under summation -- this is the identity
    create_sale()/`_resolve_quotation_lines_for_write` rely on to need no
    code changes of their own: Sigma(gross_i - discount_i + tax_i) ==
    Sigma(gross_i) - Sigma(discount_i) + Sigma(tax_i) when each line's own
    identity holds exactly)."""
    line1 = pricing.calculate_line(0.25, 11, discount_pct=5, tax_rate=16, currency='JOD')
    line2 = pricing.calculate_line(0.75, 13, discount_pct=5, tax_rate=16, currency='JOD')
    assert _reconciles(line1)
    assert _reconciles(line2)

    gross = _Decimal(str(line1['gross'])) + _Decimal(str(line2['gross']))
    discount = _Decimal(str(line1['discount_amount'])) + _Decimal(str(line2['discount_amount']))
    tax = _Decimal(str(line1['tax'])) + _Decimal(str(line2['tax']))
    total = _Decimal(str(line1['total'])) + _Decimal(str(line2['total']))
    assert gross - discount + tax == total
    assert total == _Decimal('13.774'), f"got {total!r}"


def test_calculate_line_undiscounted_unchanged():
    """THE allow-half (ENGINEERING.md section 1's 'prove both directions of
    anything that both denies and allows'): the fix must DENY (change) the
    discounted case above while still ALLOWING (leaving untouched) the
    already-correct undiscounted case for realistic catalogue prices (at or
    under the currency's own decimal scale, which is what every real product
    price actually is -- this is not the corner case of a price entered at
    MORE precision than the shop's currency, which forces `gross` itself to
    round and can perturb even a zero-discount total; that corner case is
    pre-existing on the `gross`-rounding step and orthogonal to this
    correction, which is why it's excluded here rather than silently
    baked into this pin)."""
    cases = [
        (100, 1, 15, pricing.TAX_AFTER_DISCOUNT, None),
        (19.99, 3, 8, pricing.TAX_AFTER_DISCOUNT, None),
        (2.375, 1, 16, pricing.TAX_AFTER_DISCOUNT, 'JOD'),
        (50, 2, 0, pricing.TAX_BEFORE_DISCOUNT, None),
    ]
    for price, qty, tax_rate, mode, currency in cases:
        r = pricing.calculate_line(price, qty, discount_pct=0, tax_rate=tax_rate, mode=mode, currency=currency)
        assert r['discount_amount'] == 0.0
        assert r['taxable_amount'] == r['gross']
        assert _reconciles(r)
        # Byte-identical to the straightforward pre-fix formula for the
        # zero-discount path: taxable_amount==gross exactly (no discount to
        # subtract), so total is just gross+tax computed the ordinary way --
        # unaffected by which of the two rounding orders is used, because
        # there is no separate "already-rounded discount" for the two orders
        # to disagree about. Each operand goes through Decimal(str(...))
        # BEFORE multiplying, exactly like pricing.py's own `_d()` -- doing
        # `price * qty` as raw floats first (19.99 has no exact binary
        # representation) would make THIS TEST's own arithmetic diverge from
        # what pricing.py computes, which is a bug in the test, not evidence
        # of one in the fix.
        q = pricing.currency_quantum(currency)
        gross_full = _Decimal(str(price)) * _Decimal(str(qty))
        expected_gross = float(gross_full.quantize(q, rounding=_ROUND_HALF_UP))
        tax_full = _Decimal(str(expected_gross)) * _Decimal(str(tax_rate)) / 100
        expected_tax = float(tax_full.quantize(q, rounding=_ROUND_HALF_UP))
        assert r['gross'] == expected_gross
        assert r['tax'] == expected_tax
        expected_total = float((_Decimal(str(expected_gross)) + _Decimal(str(expected_tax))).quantize(q, rounding=_ROUND_HALF_UP))
        assert r['total'] == expected_total


def test_calculate_invoice_reconciles():
    """Same reconciliation identity, applied to `calculate_invoice` -- no
    current caller, but this module's own docstring blesses it as one of
    only two formula-writers, so a landmine closed before the first caller
    ever lands.

    NOTE ON THE CHOICE OF NUMBERS: `discount_amount` here is already a
    currency AMOUNT (not a percentage the function itself multiplies out),
    so a "clean" discount like 7.5 against subtotal=100 never forces a
    rounding step at all -- verified by running it against the reverted
    pre-fix formula, where it reconciled anyway (the same coincidence-trap
    noted on the other tests in this file). The divergence needs the
    discount amount itself to carry more precision than the currency's own
    scale (33.335 at a 2dp quantum) -- a caller can legitimately do this,
    e.g. a already-summed multi-line discount total. Verified BY RUNNING IT
    against the reverted pre-fix formula: taxable_amount=66.67, tax=10.67,
    total=77.33 -- 66.67+10.67=77.34 != 77.33, a real reconciliation
    failure in the OUTPUT DICT's own fields (the total itself happened to
    still land on 77.33 either way here, which is exactly why the identity
    check below -- not a hardcoded expected total -- is what this test
    pins)."""
    r = pricing.calculate_invoice(subtotal=100, discount_amount=33.335, tax_rate_pct=16,
                                   mode=pricing.TAX_AFTER_DISCOUNT)
    assert _Decimal(str(r['taxable_amount'])) + _Decimal(str(r['tax'])) == _Decimal(str(r['total'])), (
        f"taxable_amount {r['taxable_amount']} + tax {r['tax']} != total {r['total']}")


def test_calculate_line_reconciles_at_two_decimal_currency_too():
    """The defect and the fix are not JOD-specific -- a plain 2dp (default,
    no currency passed) quantum hits the identical independent-rounding gap.

    NOTE ON THE CHOICE OF NUMBERS: an earlier version of this test used
    (19.99, 3, discount_pct=5, tax_rate=8) -- values that, verified by
    running them against the reverted pre-fix formula, happen to produce the
    SAME total (61.53) both ways. A reconciliation test built on numbers
    like that would pass whether or not the fix existed -- exactly
    ENGINEERING.md's 'the pass condition is the bug signature' trap, and the
    same coincidence the code-review noted about this file's OWN pre-
    existing test_multi_quantity_line/test_float_precision_rounding cases.
    (10.0, 1, discount_pct=33.3333, tax_rate=16) was verified BY RUNNING IT
    against the reverted pre-fix formula to give total=7.73 while
    taxable+tax=6.67+1.07=7.74 -- a genuine 2dp-scale mismatch -- before
    being adopted here, specifically so this test cannot make the same
    mistake."""
    r = pricing.calculate_line(10.0, 1, discount_pct=33.3333, tax_rate=16)
    assert r['total'] == 7.74, f"got {r['total']!r}"
    assert _reconciles(r)


# ═════════════════════════════════════════════════════════════════════════════
# Part B -- integration (real HTTP routes, isolated temp DB)
# ═════════════════════════════════════════════════════════════════════════════

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_pricing_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
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
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,'PRC-1','Priced Item',5,100,15)",
        (str(uuid.uuid4()), company_id),
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
    rconn.execute("INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,'PRC-2','Second Item',2,50,15)", (str(uuid.uuid4()), cid))
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


def test_hourly_chart_zero_fills_quiet_hours_instead_of_dropping_them(monkeypatch):
    """Regression for the dashboard's "Revenue Today (by Hour)" chart:
    dashboard_stats() used to GROUP BY hr over sales rows only, so any hour
    with zero revenue was dropped from hourly_labels/hourly_data entirely
    instead of appearing as an explicit zero. Chart.js renders a plain
    category axis (one bar per array entry -- subsystem-retail.js), so
    dropping quiet hours made e.g. a sale at 09:00 and nothing again until
    15:00 render as two ADJACENT bars, indistinguishable from sales at 09:00
    and 10:00 -- a business day with real gaps looked continuous.

    "Now" is frozen to 15:30 so the expected hour range (00:00..15:00) is
    deterministic regardless of when the suite actually runs.
    """
    client, cid, pid, bid = _make_admin_and_client()

    import api.retail_api as retail_api_module
    import datetime as _dt_module

    class _FrozenDateTime(_dt_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt_module.datetime(2026, 1, 1, 15, 30, 0)

    monkeypatch.setattr(retail_api_module, 'datetime', _FrozenDateTime)

    rconn = get_retail_conn()
    # The exact scenario from the bug report: one sale at 09:00, the next
    # not until 15:00 -- everything in between (and before/after) must still
    # show up as a real zero, not be missing from the arrays.
    for hr, total in (('09', 120.0), ('15', 80.0)):
        rconn.execute(
            "INSERT INTO sales (company_id,sale_number,branch_id,total,payment_method,idempotency_key,created_at) "
            "VALUES (?,?,?,?, 'cash', ?, ?)",
            (cid, f'SALE-HR-{hr}-{uuid.uuid4().hex[:8]}', bid, total, str(uuid.uuid4()), f'2026-01-01 {hr}:00:00'),
        )
    rconn.commit()
    rconn.close()

    d = _dashboard(client)

    # 00:00 through 15:00 inclusive == 16 entries, not just the 2 hours that
    # actually had a sale.
    assert d['hourly_labels'] == [f'{h:02d}:00' for h in range(16)]
    assert len(d['hourly_data']) == 16

    by_label = dict(zip(d['hourly_labels'], d['hourly_data']))
    assert by_label['09:00'] == 120.0
    assert by_label['15:00'] == 80.0
    for quiet_hr in ('00:00', '05:00', '10:00', '12:00', '14:00'):
        assert by_label[quiet_hr] == 0.0, f'{quiet_hr} should be a real zero, not dropped'
