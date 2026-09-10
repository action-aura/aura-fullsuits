"""
Aura Retail -- loyalty redemption, wave 1 (schema v27, ROADMAP.md's
2026-08-31 "retail schema v27 CLAIMED for loyalty redemption" entry).
Backend/API coverage for the redemption path inside create_sale, the
`loyalty_ledger` earn/redeem writes, the `loyalty_point_value` setting, and
the new GET /customers/<id>/loyalty balance route.

REDEMPTION IS A TENDER, NOT A DISCOUNT -- points settle part of what is
owed without shrinking the taxable base, and must never be counted as cash
actually taken (the "cash trap": the daily cash summary / drawer Z-report
would otherwise fail on every redemption). See api/retail_api.py's own
comments at create_sale's redemption block for the full design; this file
proves it holds.

WHAT THIS FILE PROVES, in order (numbered to match this feature's own
report):

  1. Redeeming more points than the ledger balance is refused, and no sale
     is written.
  2. Redeeming without CAP_DISCOUNT is refused (403), and no sale is
     written -- both as ordinary behaviour (a real cashier) AND as an
     explicit mutation proof (forcing session_has_capability to always
     grant / always deny and watching the outcome track it, the same
     technique retail_route_capability_matrix_test.py's
     `test_void_payment_authority_outcome_tracks_the_live_capability_verdict`
     already uses).
  3. Redeeming on a walk-in (no customer_id) is refused, regardless of
     capability or configuration.
  4. With `loyalty_point_value` at its default of 0, redemption is refused
     -- a shop that has never opened this setting cannot be made to give
     money away by accident.
  5. A successful redemption: `total`/`tax_amount` are byte-identical to an
     INDEPENDENT pricing.calculate_line call (i.e. unaffected by the
     redemption), `points_redeemed_amount` is exactly right, a NEGATIVE
     `loyalty_ledger` row exists linked to the sale, and the ledger balance
     drops by EXACTLY the points redeemed (isolated from same-sale earn by
     using a product cheap enough that this sale earns zero points of its
     own).
  6. THE CASH TRAP: the money actually recorded as retained (the payments
     ledger `_record_payment` writes, which feeds the drawer/Z-report) is
     `total - points_redeemed_amount`, never `total` -- a redemption must
     never inflate what the drawer expects to hold.
  7. THE DOUBLE-SPEND CASE: the same points cannot be redeemed twice --
     spending a balance to zero in one sale makes an identical second
     request refused for insufficient balance.
  8. Redeemed value never exceeds the sale total -- a customer with far
     more points than one sale is worth only spends as many as the sale
     can absorb; the rest survives in their balance.
  9. MUTATION PROOF, balance check reads the LEDGER, never
     `customers.loyalty_points`: forced BOTH ways -- the column set HIGH
     while the real ledger sum is LOW must still refuse, and the column set
     LOW while the real ledger sum is HIGH must still allow. Either
     direction failing would mean the column, not the ledger, is deciding
     -- the exact double-spend `_migrate_add_loyalty_ledger` exists to
     avoid reopening.

Self-contained bootstrap, matching retail_promotions_test.py and the rest
of this suite (no shared conftest.py exists here). CRITICAL: exactly ONE
pytest process per file -- AURA_APP_DATA resolves at import time, so two
test files sharing one pytest invocation corrupt each other.

Run:
    py -3.14 -m pytest products/retail/tests/retail_loyalty_redemption_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_loyalty_redeem_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

import database.schema as sch  # noqa: E402
from core.retail import pricing  # noqa: E402
from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
import api.retail_api as retail_api_module  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers (deliberately duplicated per-file convention -- see
#    retail_product_lookup_nocase_test.py's own module docstring for why
#    nothing here is shared via a conftest.py) ──────────────────────────────

def _make_user(role, company_id, capabilities=None):
    """A real account with real capability rows -- matches
    retail_promotions_test.py's own `_make_user`."""
    email = f"loyalty-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "LoyaltyTestPW1"
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
    return client


def _new_shop():
    """A fresh company with one logged-in ADMIN user (bypasses every
    capability gate) -- each test gets its OWN company so ledger balances
    and settings from one test never leak into another's assertions."""
    company_id = str(uuid.uuid4())
    admin = _make_user('admin', company_id)
    admin.get(f'{API}/settings/tax')  # forces _ensure_credit_schema/doc_sequences, matching sibling files
    return company_id, admin


@pytest.fixture
def shop():
    return _new_shop()


def _create_product(client, sell_price=100.0, tax_rate=0):
    tag = uuid.uuid4().hex[:8]
    payload = {
        'name': f'Loyalty test item {tag}', 'sku': f'LOY-{tag}',
        'sell_price': sell_price, 'cost_price': 1.0, 'tax_rate': tax_rate,
        'initial_stock': 1000,
    }
    r = client.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_customer(client):
    payload = {'name': f'Loyalty Customer {uuid.uuid4().hex[:6]}'}
    r = client.post(f'{API}/customers', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _sale(client, items, **extra):
    """No default `amount_paid` -- unlike sibling files' helpers, several
    tests below depend on OMITTING it entirely so create_sale falls back to
    `amount_due_after_points` rather than the sale's full `total`."""
    payload = dict({'items': items, 'payment_method': 'cash',
                     'idempotency_key': str(uuid.uuid4())}, **extra)
    return client.post(f'{API}/sales', json=payload)


def _set_loyalty_point_value(client, value):
    r = client.post(f'{API}/settings/credit', json={'loyalty_point_value': value})
    assert r.status_code == 200, r.get_json()


def _grant_ledger_points(company_id, customer_id, points, entry_type='adjust'):
    """Writes an immutable ledger row directly -- the same shape a real
    goodwill grant or the v27 backfill would write -- rather than going
    through create_sale's earn path, so a test's starting balance is exact
    and independent of the earn formula."""
    conn = sch.get_retail_conn()
    conn.execute(
        "INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type,created_by) "
        "VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), company_id, customer_id, points, entry_type, 'test-fixture'))
    conn.commit()
    conn.close()


def _ledger_balance(company_id, customer_id):
    conn = sch.get_retail_conn()
    row = conn.execute(
        "SELECT COALESCE(SUM(points_delta),0) AS bal FROM loyalty_ledger WHERE company_id=? AND customer_id=?",
        (company_id, customer_id)).fetchone()
    conn.close()
    return row['bal']


def _ledger_rows(company_id, customer_id):
    conn = sch.get_retail_conn()
    rows = conn.execute(
        "SELECT * FROM loyalty_ledger WHERE company_id=? AND customer_id=? ORDER BY id",
        (company_id, customer_id)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_sale(sale_id):
    conn = sch.get_retail_conn()
    row = conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _cash_in_amount_for_sale(company_id, sale_id):
    conn = sch.get_retail_conn()
    row = conn.execute(
        "SELECT amount FROM payments WHERE company_id=? AND related_type='sale' AND related_id=? "
        "AND direction='in'", (company_id, sale_id)).fetchone()
    conn.close()
    return row['amount'] if row else None


def _sales_count(company_id):
    conn = sch.get_retail_conn()
    n = conn.execute("SELECT COUNT(*) AS n FROM sales WHERE company_id=?", (company_id,)).fetchone()['n']
    conn.close()
    return n


def _set_customer_accumulator(customer_id, value):
    """Directly overwrites `customers.loyalty_points` -- the fast-cache
    column the redemption balance check must NEVER read -- so tests 9a/9b
    can prove that column has zero influence on the redemption decision in
    EITHER direction."""
    conn = sch.get_retail_conn()
    conn.execute("UPDATE customers SET loyalty_points=? WHERE id=?", (value, customer_id))
    conn.commit()
    conn.close()


def _shop_currency(client):
    return client.get(f'{API}/settings/tax').get_json()['data']['base_currency']


# ── 1. Redeeming more than the balance is refused ────────────────────────────

def test_redeeming_more_points_than_balance_is_refused(shop):
    cid, admin = shop
    pid = _create_product(admin, sell_price=100.0)
    customer_id = _create_customer(admin)
    _set_loyalty_point_value(admin, 1)
    _grant_ledger_points(cid, customer_id, 5)
    before = _sales_count(cid)

    r = _sale(admin, [{'product_id': pid, 'quantity': 1}],
              customer_id=customer_id, points_redeemed=6, amount_paid=100.0)
    assert r.status_code == 400, r.get_json()
    assert 'insufficient' in r.get_json()['message'].lower(), r.get_json()
    assert _ledger_balance(cid, customer_id) == 5, "a refused redemption must not touch the ledger"
    assert _sales_count(cid) == before, "a refused redemption must not create a sale"


# ── 2. CAP_DISCOUNT, both the real behaviour and the mutation proof ─────────

def test_redeeming_without_cap_discount_is_refused_and_no_sale_created(shop):
    """A real cashier -- default capabilities carry no retail.discount
    (user_accounts.DEFAULT_CAPABILITIES) -- is refused a redemption even
    though the customer has ample balance and the shop has configured a
    point value."""
    cid, admin = shop
    pid = _create_product(admin, sell_price=100.0)
    customer_id = _create_customer(admin)
    _set_loyalty_point_value(admin, 1)
    _grant_ledger_points(cid, customer_id, 20)
    cashier = _make_user('cashier', cid)
    before = _sales_count(cid)

    r = _sale(cashier, [{'product_id': pid, 'quantity': 1}],
              customer_id=customer_id, points_redeemed=5, amount_paid=100.0)
    assert r.status_code == 403, r.get_json()
    assert _ledger_balance(cid, customer_id) == 20, "a refused redemption must not touch the ledger"
    assert _sales_count(cid) == before, "a refused redemption must not create a sale"


def test_capability_check_outcome_tracks_the_live_verdict_both_directions(shop, monkeypatch):
    """MUTATION PROOF (the technique retail_route_capability_matrix_test.py's
    `test_void_payment_authority_outcome_tracks_the_live_capability_verdict`
    already uses): force session_has_capability to a fixed answer and
    confirm redemption's outcome is DRIVEN by it, for both a cashier (no
    real grant) and an admin (whose normal pass comes from role-bypass
    INSIDE session_has_capability, not from a second exemption in
    create_sale).

    Forced True must let even a bare cashier redeem; forced False must
    refuse even an admin. Either direction disagreeing would mean a second,
    undocumented gate is doing the real work.
    """
    cid, admin = shop
    pid = _create_product(admin, sell_price=5.0)  # cheap: total < 10, so pts earned == 0
    customer_id = _create_customer(admin)
    _set_loyalty_point_value(admin, 1)
    cashier = _make_user('cashier', cid)

    def _always(value):
        def stub(code):
            return value
        return stub

    _grant_ledger_points(cid, customer_id, 3)
    monkeypatch.setattr(retail_api_module, 'session_has_capability', _always(True))
    r = _sale(cashier, [{'product_id': pid, 'quantity': 1}],
              customer_id=customer_id, points_redeemed=1, amount_paid=100.0)
    assert r.status_code == 200, ('forced True must let a cashier redeem', r.get_json())
    monkeypatch.undo()

    monkeypatch.setattr(retail_api_module, 'session_has_capability', _always(False))
    r = _sale(admin, [{'product_id': pid, 'quantity': 1}],
              customer_id=customer_id, points_redeemed=1, amount_paid=100.0)
    assert r.status_code == 403, ('forced False must refuse even an admin', r.get_json())
    monkeypatch.undo()


# ── 3. Walk-in refusal ───────────────────────────────────────────────────────

def test_redeeming_on_a_walkin_is_refused(shop):
    cid, admin = shop
    pid = _create_product(admin, sell_price=100.0)
    _set_loyalty_point_value(admin, 1)
    before = _sales_count(cid)

    r = _sale(admin, [{'product_id': pid, 'quantity': 1}], points_redeemed=1, amount_paid=100.0)
    assert r.status_code == 400, r.get_json()
    assert 'walk-in' in r.get_json()['message'].lower(), r.get_json()
    assert _sales_count(cid) == before


# ── 4. Setting at 0 (the default) refuses redemption ─────────────────────────

def test_redemption_refused_when_point_value_is_unconfigured(shop):
    """Never calls _set_loyalty_point_value -- the fixture's default IS the
    product's own default (_DEFAULT_SETTINGS['loyalty_point_value'] == '0'),
    which is the exact scenario this test protects: an install that has
    never opened this setting."""
    cid, admin = shop
    pid = _create_product(admin, sell_price=100.0)
    customer_id = _create_customer(admin)
    _grant_ledger_points(cid, customer_id, 5)
    before = _sales_count(cid)

    r = _sale(admin, [{'product_id': pid, 'quantity': 1}],
              customer_id=customer_id, points_redeemed=1, amount_paid=100.0)
    assert r.status_code == 400, r.get_json()
    assert 'not configured' in r.get_json()['message'].lower(), r.get_json()
    assert _ledger_balance(cid, customer_id) == 5
    assert _sales_count(cid) == before


# ── 5/6. A successful redemption: total/tax unchanged, ledger row, balance,
#         and THE CASH TRAP ──────────────────────────────────────────────────

def test_successful_redemption_leaves_total_and_tax_untouched_and_writes_the_ledger(shop):
    cid, admin = shop
    currency = _shop_currency(admin)
    assert currency == pricing.DEFAULT_BASE_CURRENCY, "fixture currency drifted from the product default"

    # Cheap enough (< 10) that this sale's OWN earn accrual is exactly zero
    # -- isolates the redemption's effect on the ledger balance from the
    # unrelated earn effect, which is proven separately in the roadmap's
    # own pre-existing accumulator tests.
    pid = _create_product(admin, sell_price=5.0, tax_rate=16)
    customer_id = _create_customer(admin)
    _set_loyalty_point_value(admin, 1)  # 1 currency unit per point
    _grant_ledger_points(cid, customer_id, 20)

    r = _sale(admin, [{'product_id': pid, 'quantity': 1}], customer_id=customer_id, points_redeemed=3)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']

    expected = pricing.calculate_line(5.0, 1, 0, 16, mode='after_discount', currency=currency)
    assert data['total'] == expected['total'], "redemption must never change the invoice total"
    assert data['tax_amount'] == expected['tax'], "redemption must never change the tax the invoice reports"
    assert data['subtotal'] == expected['gross']

    assert data['points_redeemed'] == 3
    assert data['points_redeemed_amount'] == 3.0

    sale_row = _get_sale(data['id'])
    assert sale_row['points_redeemed_amount'] == 3.0
    assert sale_row['total'] == expected['total']

    ledger = _ledger_rows(cid, customer_id)
    redeem_rows = [row for row in ledger if row['entry_type'] == 'redeem']
    assert len(redeem_rows) == 1, ledger
    assert redeem_rows[0]['points_delta'] == -3
    assert redeem_rows[0]['sale_id'] == data['id']

    earn_rows = [row for row in ledger if row['entry_type'] == 'earn']
    assert earn_rows == [], (
        "this product's total is under 10, so int(total/10) == 0 -- an earn "
        "row here would mean the isolation this test relies on has broken")

    assert _ledger_balance(cid, customer_id) == 20 - 3, "balance must drop by EXACTLY the points redeemed"

    # THE CASH TRAP: `amount_paid` was omitted, so it defaulted to
    # `amount_due_after_points`, not `total` -- and the money actually
    # recorded as RETAINED (what feeds the drawer/Z-report) must match that
    # same reduced figure, never the full invoice total.
    amount_due_after_points = round(expected['total'] - 3.0, 3)
    assert data['amount_paid'] == amount_due_after_points
    assert data['change'] == 0
    assert data['balance_due'] == 0
    cash_amount = _cash_in_amount_for_sale(cid, data['id'])
    assert cash_amount == amount_due_after_points, (
        f"the drawer ledger recorded {cash_amount}, expected {amount_due_after_points} -- "
        f"a redemption must never be counted as cash the till actually took")
    assert cash_amount != data['total'], "the cash trap: points must never be recorded as cash"


def test_cash_trap_when_customer_tenders_the_pre_redemption_total(shop):
    """THE scenario that actually exercises the cash trap: the OTHER
    successful-redemption test above omits `amount_paid`, which defaults to
    `amount_due_after_points` -- so `paid` never exceeds it and `min(paid,
    total)` and `min(paid, amount_due_after_points)` happen to agree by
    coincidence, hiding the defect (confirmed by deliberately reverting
    `net_received` to `min(paid, total)` during development: that test kept
    passing). Here the customer tenders the FULL pre-redemption total in
    cash -- a realistic "forgot the points already covered part of it"
    case -- which only the correct formula prices right.
    """
    cid, admin = shop
    currency = _shop_currency(admin)
    pid = _create_product(admin, sell_price=5.0, tax_rate=16)
    customer_id = _create_customer(admin)
    _set_loyalty_point_value(admin, 1)
    _grant_ledger_points(cid, customer_id, 20)
    expected = pricing.calculate_line(5.0, 1, 0, 16, mode='after_discount', currency=currency)

    r = _sale(admin, [{'product_id': pid, 'quantity': 1}], customer_id=customer_id,
              points_redeemed=3, amount_paid=expected['total'])
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']

    amount_due_after_points = round(expected['total'] - 3.0, 3)
    assert data['amount_paid'] == expected['total']
    # The customer handed over the full total in cash; points already
    # covered 3.0 of it, so the till must hand back exactly that as change.
    assert data['change'] == round(expected['total'] - amount_due_after_points, 3) == 3.0
    # Overpaid (by exactly the change above) relative to what was still due
    # after points, so `balance_due` is negative rather than zero -- the
    # same "negative means overpaid" contract this route already carried
    # before redemption existed.
    assert data['balance_due'] == -3.0

    cash_amount = _cash_in_amount_for_sale(cid, data['id'])
    assert cash_amount == amount_due_after_points, (
        f"the drawer ledger recorded {cash_amount} as retained, expected "
        f"{amount_due_after_points} -- counting the tendered total instead "
        f"would overstate cash in the drawer by exactly the points' value "
        f"and fail the till's own Z-report")
    assert cash_amount != expected['total'], "the cash trap: the full tendered total must never be the retained figure"


# ── 7. THE DOUBLE-SPEND CASE ─────────────────────────────────────────────────

def test_the_same_points_cannot_be_redeemed_twice(shop):
    cid, admin = shop
    pid = _create_product(admin, sell_price=5.0)  # total < 10 -> zero earn, exact balances
    customer_id = _create_customer(admin)
    _set_loyalty_point_value(admin, 1)
    _grant_ledger_points(cid, customer_id, 5)

    r1 = _sale(admin, [{'product_id': pid, 'quantity': 1}], customer_id=customer_id, points_redeemed=5)
    assert r1.status_code == 200, r1.get_json()
    assert _ledger_balance(cid, customer_id) == 0

    sales_before_retry = _sales_count(cid)
    r2 = _sale(admin, [{'product_id': pid, 'quantity': 1}], customer_id=customer_id, points_redeemed=5)
    assert r2.status_code == 400, ('the same 5 points must not spend twice', r2.get_json())
    assert 'insufficient' in r2.get_json()['message'].lower(), r2.get_json()
    assert _ledger_balance(cid, customer_id) == 0, "the failed re-spend must not further alter the balance"
    assert _sales_count(cid) == sales_before_retry


# ── 8. Redeemed value never exceeds the sale total ───────────────────────────

def test_redeemed_value_never_exceeds_the_sale_total(shop):
    cid, admin = shop
    pid = _create_product(admin, sell_price=5.0, tax_rate=0)  # total == 5.0 exactly
    customer_id = _create_customer(admin)
    _set_loyalty_point_value(admin, 1)
    _grant_ledger_points(cid, customer_id, 10_000)  # far more than this one sale is worth

    r = _sale(admin, [{'product_id': pid, 'quantity': 1}], customer_id=customer_id, points_redeemed=10_000)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']

    assert data['total'] == 5.0
    assert data['points_redeemed_amount'] <= data['total'], "redeemed value must never exceed the sale total"
    assert data['points_redeemed_amount'] == 5.0
    assert data['points_redeemed'] == 5, (
        "only 5 points (worth the full $5 sale) should be CONSUMED -- the "
        "other 9,995 must survive in the balance rather than being burned")
    assert data['balance_due'] == 0

    assert _ledger_balance(cid, customer_id) == 10_000 - 5, (
        "a customer asking to over-redeem must keep the points the sale could not absorb")


# ── 9. MUTATION PROOF: the balance check reads the LEDGER, never the column ──

def test_balance_check_refuses_when_ledger_is_short_even_if_the_column_says_otherwise(shop):
    """(a) THE DEFEATING SHAPE. `customers.loyalty_points` set to a huge
    value while the real ledger sum is small -- if the balance check ever
    regressed to reading that column (the exact simplification
    _migrate_add_loyalty_ledger's own docstring names as the bug this
    table exists to prevent), this redemption would wrongly succeed."""
    cid, admin = shop
    pid = _create_product(admin, sell_price=100.0)
    customer_id = _create_customer(admin)
    _set_loyalty_point_value(admin, 1)
    _grant_ledger_points(cid, customer_id, 5)          # the TRUE balance
    _set_customer_accumulator(customer_id, 1_000)       # a wildly wrong cache

    r = _sale(admin, [{'product_id': pid, 'quantity': 1}],
              customer_id=customer_id, points_redeemed=500, amount_paid=100.0)
    assert r.status_code == 400, (
        'the balance check must refuse based on the LEDGER (5), not the '
        'inflated customers.loyalty_points column (1000)', r.get_json())
    assert 'insufficient' in r.get_json()['message'].lower(), r.get_json()


def test_balance_check_allows_when_ledger_is_sufficient_even_if_the_column_says_otherwise(shop):
    """(b) THE PAIRED PROOF, other direction -- required so (a) cannot be
    satisfied by a check that simply refuses everything. `customers.
    loyalty_points` set LOW while the real ledger sum is HIGH: the
    redemption must still be ALLOWED, proving the column has no influence
    in either direction."""
    cid, admin = shop
    # 9.99 keeps this sale's OWN earn at zero (int(9.99/10) == 0) while
    # still being worth enough points, at a 0.5-per-point value, that the
    # (e) sale-total CLAMP never engages (floor(9.99/0.5) == 19 >= 15) --
    # this test is about the BALANCE check specifically, not the clamp.
    pid = _create_product(admin, sell_price=9.99)
    customer_id = _create_customer(admin)
    _set_loyalty_point_value(admin, 0.5)
    _grant_ledger_points(cid, customer_id, 20)          # the TRUE balance
    _set_customer_accumulator(customer_id, 2)           # a wildly wrong, LOW cache

    r = _sale(admin, [{'product_id': pid, 'quantity': 1}], customer_id=customer_id, points_redeemed=15)
    assert r.status_code == 200, (
        'the balance check must allow based on the LEDGER (20), not the '
        'understated customers.loyalty_points column (2)', r.get_json())
    assert _ledger_balance(cid, customer_id) == 20 - 15


# ── The balance route itself ─────────────────────────────────────────────────

def test_customer_loyalty_balance_route_reads_the_ledger_and_is_company_scoped(shop):
    cid, admin = shop
    customer_id = _create_customer(admin)
    _grant_ledger_points(cid, customer_id, 7)
    _grant_ledger_points(cid, customer_id, -2, entry_type='adjust')

    r = admin.get(f'{API}/customers/{customer_id}/loyalty')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['balance'] == 5
    assert len(data['entries']) == 2

    other_company_id, other_admin = _new_shop()
    r2 = other_admin.get(f'{API}/customers/{customer_id}/loyalty')
    assert r2.status_code == 404, ('a customer id from another company must not be readable', r2.get_json())
