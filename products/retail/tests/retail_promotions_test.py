"""
Aura Retail -- promotions, wave 1 (schema v23, ROADMAP.md's 2026-08-30
"retail schema v23 CLAIMED for promotions, wave 1" entry). Backend/API
coverage for core.retail.promotions and the five new /promotions* routes
plus their integration into create_sale.

WHAT THIS FILE PROVES, in order (numbered to match this change's own
report):

  1. INVISIBLE UNLESS OPTED IN -- zero promotion rows, a sale's stored
     figures are byte-identical to today's pre-promotions math.
  2. A product promotion discounts that line and no other line.
  3. A category promotion discounts every product in it.
  4. Product-specific beats category on the same product, regardless of
     which percentage is larger.
  5. BEST PRICE WINS -- promo + manual is never summed.
  6. THE CAPABILITY RULE, BOTH HALVES: a cashier without CAP_DISCOUNT is
     refused a MANUAL discount, but CAN sell a PROMOTED line.
  7. SNAPSHOT -- sale_item_promotions records what was applied, and editing
     the promotion afterwards does not change what the sale recorded.
  8. RETURNS -- a promoted line refunds what was actually paid, not list
     price, with zero changes to create_return itself.
  9. WINDOW -- a promotion whose window has not opened, or has closed,
     does not apply.
 10. TENANCY -- company A's promotion never touches company B's sale, and
     creating a promotion against another company's product/category/
     branch is refused.
 11. Schema v23 lands on head and is idempotent.
 12. PARENT TIER (launch-readiness "product variants" follow-up, one of the
     two things the variants commit named as deliberately deferred rather
     than missed -- schema v25, no schema of its own): a promotion
     configured on a variant's PARENT product discounts the variant's sale
     line; an exact-variant promotion beats a parent one, and a parent one
     beats a category one, in both cases regardless of which percentage is
     larger -- resolve_line_discount_pct's new middle tier, exercised here
     the same way section 4 exercises the original two.

Self-contained bootstrap, matching retail_product_lookup_nocase_test.py and
the rest of this suite (no shared conftest.py exists here). CRITICAL:
exactly ONE pytest process per file -- AURA_APP_DATA resolves at import
time, so two test files sharing one pytest invocation corrupt each other.

Run:
    py -3.14 -m pytest products/retail/tests/retail_promotions_test.py -v
"""
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_promotions_"))
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

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers (deliberately duplicated per-file convention -- see
#    retail_product_lookup_nocase_test.py's own module docstring for why
#    nothing here is shared via a conftest.py) ──────────────────────────────

def _make_user(role, company_id, capabilities=None):
    """A real account with real capability rows, seeded through the SAME
    production helper account creation uses -- matches
    retail_route_capability_matrix_test.py's own `_make_user`. `capabilities`
    overrides one or more access_level rows after the role default is
    seeded (e.g. `{'retail.discount': 'none'}` to strip a manager's default
    grant, though the tests below only ever need the CASHIER default, which
    already excludes retail.discount -- see user_accounts.DEFAULT_
    CAPABILITIES)."""
    email = f"promo-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "PromoTestPW1"
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
    capability gate -- see mt_auth's admin-bypass design) -- used to set up
    products/categories/promotions. Each test gets its OWN company: tenancy
    is exactly what test (10) pins, so a shared company would make one
    test's promotion visible to another's "not found" assertion."""
    company_id = str(uuid.uuid4())
    admin = _make_user('admin', company_id)
    admin.get(f'{API}/settings/tax')  # forces _ensure_credit_schema/doc_sequences, matching sibling files
    return company_id, admin


@pytest.fixture
def shop():
    """One company per test by default -- see _new_shop's own docstring."""
    return _new_shop()


def _create_product(client, name=None, sku=None, category_id=None, sell_price=100.0, tax_rate=0,
                     parent_product_id=None, variant_label=None):
    tag = uuid.uuid4().hex[:8]
    payload = {
        'name': name or f'Promo test item {tag}',
        'sku': sku or f'PROMO-{tag}',
        'sell_price': sell_price, 'cost_price': 1.0, 'tax_rate': tax_rate,
        'initial_stock': 1000,
    }
    if category_id is not None:
        payload['category_id'] = category_id
    # launch-readiness "product variants" follow-up (schema v25, section 12
    # below): lets this file's own helper build a variant -- a product whose
    # `parent_product_id` names another product -- the same way
    # retail_product_variants_test.py's own `_create_product` does.
    if parent_product_id is not None:
        payload['parent_product_id'] = parent_product_id
    if variant_label is not None:
        payload['variant_label'] = variant_label
    r = client.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_category(client, name=None):
    r = client.post(f'{API}/categories', json={'name': name or f'Promo category {uuid.uuid4().hex[:8]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_promotion(client, discount_pct=10, product_id=None, category_id=None,
                       branch_id=None, starts_at=None, ends_at=None, name=None, expect=200):
    payload = {'name': name or f'Promo {uuid.uuid4().hex[:8]}', 'discount_pct': discount_pct}
    if product_id is not None:
        payload['product_id'] = product_id
    if category_id is not None:
        payload['category_id'] = category_id
    if branch_id is not None:
        payload['branch_id'] = branch_id
    if starts_at is not None:
        payload['starts_at'] = starts_at
    if ends_at is not None:
        payload['ends_at'] = ends_at
    r = client.post(f'{API}/promotions', json=payload)
    assert r.status_code == expect, r.get_json()
    return r.get_json()['data']['id'] if expect == 200 else r


def _sale(client, items, **extra):
    payload = dict({
        'items': items, 'amount_paid': 1_000_000, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    }, **extra)
    return client.post(f'{API}/sales', json=payload)


def _sale_items(sale_id):
    conn = sch.get_retail_conn()
    rows = conn.execute("SELECT * FROM sale_items WHERE sale_id=? ORDER BY id", (sale_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _sale_item_promotions(sale_item_id):
    conn = sch.get_retail_conn()
    rows = conn.execute(
        "SELECT * FROM sale_item_promotions WHERE sale_item_id=?", (sale_item_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _fmt(dt):
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def _future(**kw):
    return _fmt(datetime.now() + timedelta(**kw))


def _past(**kw):
    return _fmt(datetime.now() - timedelta(**kw))


# ── 1. INVISIBLE UNLESS OPTED IN ─────────────────────────────────────────────

def test_zero_promotions_totals_are_byte_identical_to_todays_math(shop):
    """With zero promotion rows anywhere, a sale's stored figures must be
    EXACTLY what core.retail.pricing.calculate_line produces for the same
    manual discount -- not approximately, since pricing.py is unmodified
    and unconsulted differently by this change. Compared against an
    INDEPENDENT call into pricing.py, not against the API's own internals."""
    cid, client = shop
    pid = _create_product(client, sell_price=99.99, tax_rate=16)
    r = _sale(client, [{'product_id': pid, 'quantity': 3, 'discount_pct': 15}])
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']

    expected = pricing.calculate_line(99.99, 3, 15, 16, mode='after_discount')
    assert data['subtotal'] == expected['gross']
    assert data['discount_amount'] == expected['discount_amount']
    assert data['tax_amount'] == expected['tax']
    assert data['total'] == expected['total']

    line = data['lines'][0]
    assert line['discount_pct'] == 15
    assert line['applied_promotion'] is None
    rows = _sale_items(data['id'])
    assert len(rows) == 1
    assert rows[0]['discount_pct'] == 15
    assert _sale_item_promotions(rows[0]['id']) == []


# ── 2/3/4. Product vs. category resolution ───────────────────────────────────

def test_a_product_promotion_discounts_that_line_and_no_other(shop):
    cid, client = shop
    promoted = _create_product(client, sell_price=100.0)
    other = _create_product(client, sell_price=100.0)
    _create_promotion(client, discount_pct=20, product_id=promoted)

    r = _sale(client, [
        {'product_id': promoted, 'quantity': 1},
        {'product_id': other, 'quantity': 1},
    ])
    assert r.status_code == 200, r.get_json()
    lines = r.get_json()['data']['lines']
    by_pid = {l['product_id']: l for l in lines}
    assert by_pid[promoted]['discount_pct'] == 20
    assert by_pid[promoted]['line_total'] == 80.0
    assert by_pid[other]['discount_pct'] == 0
    assert by_pid[other]['line_total'] == 100.0


def test_a_category_promotion_discounts_every_product_in_it(shop):
    cid, client = shop
    cat = _create_category(client)
    other_cat = _create_category(client)
    p1 = _create_product(client, category_id=cat, sell_price=50.0)
    p2 = _create_product(client, category_id=cat, sell_price=200.0)
    outside = _create_product(client, category_id=other_cat, sell_price=50.0)
    _create_promotion(client, discount_pct=10, category_id=cat)

    r = _sale(client, [
        {'product_id': p1, 'quantity': 1},
        {'product_id': p2, 'quantity': 1},
        {'product_id': outside, 'quantity': 1},
    ])
    assert r.status_code == 200, r.get_json()
    by_pid = {l['product_id']: l for l in r.get_json()['data']['lines']}
    assert by_pid[p1]['discount_pct'] == 10
    assert by_pid[p2]['discount_pct'] == 10
    assert by_pid[outside]['discount_pct'] == 0


def test_product_specific_beats_category_regardless_of_percentage(shop):
    """The category rule is the LARGER percentage (50%) and must still
    lose to the product-specific rule (10%) -- see mutation proof M3."""
    cid, client = shop
    cat = _create_category(client)
    pid = _create_product(client, category_id=cat, sell_price=100.0)
    _create_promotion(client, discount_pct=50, category_id=cat)
    _create_promotion(client, discount_pct=10, product_id=pid)

    r = _sale(client, [{'product_id': pid, 'quantity': 1}])
    assert r.status_code == 200, r.get_json()
    line = r.get_json()['data']['lines'][0]
    assert line['discount_pct'] == 10, (
        f"product-specific (10%) must beat category (50%). Got {line['discount_pct']}%")
    assert line['line_total'] == 90.0


# ── 12. PARENT TIER (launch-readiness "product variants" follow-up) ─────────
#
# resolve_line_discount_pct's new middle tier: exact-variant > parent >
# category. `_create_product(..., parent_product_id=parent)` builds a variant
# exactly like retail_product_variants_test.py's own helper does -- a product
# whose `parent_product_id` names another product, schema v25, no new schema
# added by THIS wave.

def test_a_promotion_on_the_parent_discounts_a_variants_sale_line(shop):
    """Requirement (6). A shop running "20% off T-Shirts" configures ONE
    promotion on the parent ("T-Shirt") and it must discount a variant's
    sale line ("T-Shirt -- Red / L") with no promotion configured on the
    variant itself -- see mutation proof M6 (a parent tier that never
    matches anything would fail this)."""
    cid, client = shop
    parent = _create_product(client, name='T-Shirt', sell_price=100.0)
    variant = _create_product(client, name='T-Shirt', sell_price=100.0,
                               parent_product_id=parent, variant_label='Red / L')
    _create_promotion(client, discount_pct=20, product_id=parent)

    r = _sale(client, [{'product_id': variant, 'quantity': 1}])
    assert r.status_code == 200, r.get_json()
    line = r.get_json()['data']['lines'][0]
    assert line['discount_pct'] == 20, (
        f"a promotion on the PARENT must discount the variant's line. Got {line}")
    assert line['line_total'] == 80.0


def test_exact_variant_promotion_beats_parent_promotion_even_when_parents_pct_is_higher(shop):
    """Requirement (7). Specificity wins over size, exactly like section 4's
    product-vs-category proof -- the parent's own rule is the BIGGER
    percentage (60%) and must still lose to the exact-variant rule (5%).
    See mutation proof M3."""
    cid, client = shop
    parent = _create_product(client, name='T-Shirt', sell_price=100.0)
    variant = _create_product(client, name='T-Shirt', sell_price=100.0,
                               parent_product_id=parent, variant_label='Red / L')
    _create_promotion(client, discount_pct=60, product_id=parent)
    _create_promotion(client, discount_pct=5, product_id=variant)

    r = _sale(client, [{'product_id': variant, 'quantity': 1}])
    assert r.status_code == 200, r.get_json()
    line = r.get_json()['data']['lines'][0]
    assert line['discount_pct'] == 5, (
        f"exact-variant (5%) must beat parent (60%), regardless of which percentage is larger. Got {line}")
    assert line['line_total'] == 95.0


def test_a_parent_promotion_beats_a_category_promotion_even_when_categorys_pct_is_higher(shop):
    """Requirement (8). The category rule is the BIGGER percentage (40%)
    and must still lose to the parent rule (15%) -- see mutation proofs M4
    (category allowed to beat parent) and M6 (parent tier never matches, so
    category wins by default)."""
    cid, client = shop
    cat = _create_category(client)
    parent = _create_product(client, name='T-Shirt', category_id=cat, sell_price=100.0)
    variant = _create_product(client, name='T-Shirt', category_id=cat, sell_price=100.0,
                               parent_product_id=parent, variant_label='Red / L')
    _create_promotion(client, discount_pct=40, category_id=cat)
    _create_promotion(client, discount_pct=15, product_id=parent)

    r = _sale(client, [{'product_id': variant, 'quantity': 1}])
    assert r.status_code == 200, r.get_json()
    line = r.get_json()['data']['lines'][0]
    assert line['discount_pct'] == 15, (
        f"parent (15%) must beat category (40%), regardless of which percentage is larger. Got {line}")
    assert line['line_total'] == 85.0


def test_with_no_parent_promotions_resolution_is_byte_identical_to_before_the_parent_tier(shop):
    """Requirement (9). A variant line with NO promotion on its parent (only
    a category promotion, exactly like section 4's non-variant fixture)
    must resolve identically to a plain, non-variant product -- the parent
    tier is invisible when it has nothing to match. `parent_id=None` on an
    ordinary product and `parent_id=<real id, no promo there>` on a variant
    must produce the SAME number when the only other rule in play is a
    category promotion."""
    cid, client = shop
    cat = _create_category(client)
    parent = _create_product(client, name='T-Shirt', category_id=cat, sell_price=100.0)
    variant = _create_product(client, name='T-Shirt', category_id=cat, sell_price=100.0,
                               parent_product_id=parent, variant_label='Red / L')
    plain = _create_product(client, category_id=cat, sell_price=100.0)
    _create_promotion(client, discount_pct=30, category_id=cat)

    r = _sale(client, [
        {'product_id': variant, 'quantity': 1},
        {'product_id': plain, 'quantity': 1},
    ])
    assert r.status_code == 200, r.get_json()
    by_pid = {l['product_id']: l for l in r.get_json()['data']['lines']}
    assert by_pid[variant]['discount_pct'] == 30
    assert by_pid[plain]['discount_pct'] == 30
    assert by_pid[variant]['discount_pct'] == by_pid[plain]['discount_pct'], (
        "a variant with no PARENT promotion must resolve the category tier exactly like an "
        f"ordinary non-variant product. Got {by_pid}")


# ── 5. BEST PRICE WINS -- never summed ───────────────────────────────────────

def test_best_price_wins_promo_20_manual_30_charges_30(shop):
    cid, client = shop
    pid = _create_product(client, sell_price=100.0)
    _create_promotion(client, discount_pct=20, product_id=pid)
    r = _sale(client, [{'product_id': pid, 'quantity': 1, 'discount_pct': 30}])
    assert r.status_code == 200, r.get_json()
    line = r.get_json()['data']['lines'][0]
    assert line['discount_pct'] == 30, f"manual 30% must beat promo 20%. Got {line['discount_pct']}"
    assert line['line_total'] == 70.0
    # The manual discount won, so nothing was snapshotted -- see M5's own
    # reasoning for why this half of the proof matters just as much.
    assert line['applied_promotion'] is None


def test_best_price_wins_promo_40_manual_10_charges_40_never_50(shop):
    cid, client = shop
    pid = _create_product(client, sell_price=100.0)
    _create_promotion(client, discount_pct=40, product_id=pid)
    r = _sale(client, [{'product_id': pid, 'quantity': 1, 'discount_pct': 10}])
    assert r.status_code == 200, r.get_json()
    line = r.get_json()['data']['lines'][0]
    assert line['discount_pct'] == 40, f"promo 40% must beat manual 10%. Got {line['discount_pct']}"
    assert line['line_total'] == 60.0
    assert line['discount_pct'] != 50, "40% + 10% must NEVER be summed to 50%."


# ── 6. THE CAPABILITY RULE, BOTH HALVES ──────────────────────────────────────

def test_cashier_without_cap_discount_is_refused_a_manual_discount(shop):
    """DENY HALF. A plain cashier (default capabilities carry no
    retail.discount -- user_accounts.DEFAULT_CAPABILITIES) submitting a
    manual discount on an UNPROMOTED line is refused, exactly as today."""
    cid, client = shop
    pid = _create_product(client, sell_price=100.0)
    cashier = _make_user('cashier', cid)
    r = _sale(cashier, [{'product_id': pid, 'quantity': 1, 'discount_pct': 20}])
    assert r.status_code == 403, r.get_json()


def test_cashier_without_cap_discount_can_still_sell_a_promoted_line(shop):
    """ALLOW HALF -- the whole point of this feature, and the case a
    "deny everything" mutation of the CAP_DISCOUNT gate would still pass.
    The SAME cashier from the deny-half test above, with NO manual
    discount typed at all, must be able to ring a sale on a line an
    ADMIN-configured promotion discounts automatically."""
    cid, client = shop
    pid = _create_product(client, sell_price=100.0)
    _create_promotion(client, discount_pct=25, product_id=pid)
    cashier = _make_user('cashier', cid)

    r = _sale(cashier, [{'product_id': pid, 'quantity': 1}])
    assert r.status_code == 200, (
        f"a cashier with no CAP_DISCOUNT must still be able to sell a PROMOTED line -- "
        f"the shop's own weekend offer must not lock its own till. Got: {r.get_json()}")
    line = r.get_json()['data']['lines'][0]
    assert line['discount_pct'] == 25
    assert line['line_total'] == 75.0


# ── 7. SNAPSHOT ───────────────────────────────────────────────────────────────

def test_snapshot_records_what_was_applied_and_survives_a_later_edit(shop):
    cid, client = shop
    pid = _create_product(client, sell_price=100.0, tax_rate=0)
    promo_id = _create_promotion(client, discount_pct=30, product_id=pid, name='Original Name')

    r = _sale(client, [{'product_id': pid, 'quantity': 2}])
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']
    rows = _sale_items(sale_id)
    assert len(rows) == 1
    snaps = _sale_item_promotions(rows[0]['id'])
    assert len(snaps) == 1, snaps
    snap = snaps[0]
    assert snap['promotion_id'] == promo_id
    assert snap['name_snapshot'] == 'Original Name'
    assert snap['discount_pct_snapshot'] == 30
    assert snap['discount_amount_snapshot'] == 60.0  # 30% of (100*2)
    assert snap['company_id'] == cid

    # Now edit the promotion's name AND percentage -- the snapshot must not move.
    r2 = client.patch(f'{API}/promotions/{promo_id}', json={'name': 'Renamed Later', 'discount_pct': 90})
    assert r2.status_code == 200, r2.get_json()

    snaps_after = _sale_item_promotions(rows[0]['id'])
    assert snaps_after == snaps, (
        "editing a promotion after the fact must not change what an already-recorded sale shows "
        f"was applied. Before={snaps} after={snaps_after}")


# ── 8. RETURNS ────────────────────────────────────────────────────────────────

def test_returning_one_unit_of_a_promoted_line_refunds_what_was_paid(shop):
    """create_return is UNCHANGED by this feature -- it already recomputes
    the refund from the original sale_items row proportionally to the
    quantity returned. This test pins that promotions inherit that
    correctness rather than assuming it: 2 units at $100, 50% promo -> $100
    paid for the line ($50/unit); returning 1 unit must refund $50, not the
    $100 list price."""
    cid, client = shop
    pid = _create_product(client, sell_price=100.0, tax_rate=0)
    _create_promotion(client, discount_pct=50, product_id=pid)

    r = _sale(client, [{'product_id': pid, 'quantity': 2}])
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']
    assert sale['lines'][0]['line_total'] == 100.0  # 2 * 100 * (1-0.5)

    ret = client.post(f'{API}/returns', json={
        'sale_id': sale['id'], 'items': [{'product_id': pid, 'quantity': 1}],
        'idempotency_key': str(uuid.uuid4()),
    })
    assert ret.status_code == 200, ret.get_json()
    refund = ret.get_json()['data']['refund_amount']
    assert refund == 50.0, f"must refund the $50 actually paid for one unit, not the $100 list price. Got {refund}"


# ── 9. WINDOW ─────────────────────────────────────────────────────────────────

def test_a_promotion_whose_window_has_not_opened_does_not_apply(shop):
    cid, client = shop
    pid = _create_product(client, sell_price=100.0)
    _create_promotion(client, discount_pct=50, product_id=pid, starts_at=_future(days=1))
    r = _sale(client, [{'product_id': pid, 'quantity': 1}])
    assert r.status_code == 200, r.get_json()
    line = r.get_json()['data']['lines'][0]
    assert line['discount_pct'] == 0, f"a promotion that has not started must not apply. Got {line}"


def test_a_promotion_whose_window_has_closed_does_not_apply(shop):
    cid, client = shop
    pid = _create_product(client, sell_price=100.0)
    _create_promotion(client, discount_pct=50, product_id=pid,
                       starts_at=_past(days=10), ends_at=_past(days=1))
    r = _sale(client, [{'product_id': pid, 'quantity': 1}])
    assert r.status_code == 200, r.get_json()
    line = r.get_json()['data']['lines'][0]
    assert line['discount_pct'] == 0, f"a promotion that has already ended must not apply. Got {line}"


def test_a_promotion_inside_its_window_applies(shop):
    """The allow half of the window checks above -- without this, a
    mutation that refused EVERY promotion regardless of window would pass
    both tests above for the wrong reason."""
    cid, client = shop
    pid = _create_product(client, sell_price=100.0)
    _create_promotion(client, discount_pct=50, product_id=pid,
                       starts_at=_past(days=1), ends_at=_future(days=1))
    r = _sale(client, [{'product_id': pid, 'quantity': 1}])
    assert r.status_code == 200, r.get_json()
    line = r.get_json()['data']['lines'][0]
    assert line['discount_pct'] == 50, f"a promotion inside its window must apply. Got {line}"


# ── 10. TENANCY ───────────────────────────────────────────────────────────────

def test_company_as_promotion_never_touches_company_bs_sale():
    """DB-level tenancy on the READ side (`load_active_promotions`), tested
    INDEPENDENTLY of the write-side validation `create_promotion` enforces
    (see the sibling test below for that half). A raw SQL insert bypasses
    the route entirely, exactly like retail_product_lookup_nocase_test.py's
    own `_direct_insert_product` bypasses create_product's dup check to
    simulate a legacy/out-of-band row.

    This independence is load-bearing, not a style choice: a version of
    this test that used the API to create company A's promotion would never
    get a row into the database at all -- create_promotion's own tenancy
    check already refuses a cross-company product_id (see the sibling test
    below) -- so it would pass mutation proof M6 (dropping the company_id
    scope from load_active_promotions) for the wrong reason: there would be
    nothing left in the database to leak."""
    cid_a, _client_a = _new_shop()
    cid_b, client_b = _new_shop()
    pid_b = _create_product(client_b, sell_price=100.0)

    conn = sch.get_retail_conn()
    conn.execute(
        "INSERT INTO promotions (company_id, name, discount_pct, product_id, status) "
        "VALUES (?,?,?,?, 'active')",
        (cid_a, 'Leaked promo (company A)', 90, pid_b),
    )
    conn.commit()
    conn.close()

    r = _sale(client_b, [{'product_id': pid_b, 'quantity': 1}])
    assert r.status_code == 200, r.get_json()
    line = r.get_json()['data']['lines'][0]
    assert line['discount_pct'] == 0, (
        f"company A's promotion (company_id={cid_a}) must never apply to company B's sale. Got {line}")


def test_creating_a_promotion_against_another_companys_product_is_refused():
    _cid_a, client_a = _new_shop()
    cid_b, client_b = _new_shop()
    pid_b = _create_product(client_b, sell_price=100.0)
    r = _create_promotion(client_a, discount_pct=10, product_id=pid_b, expect=400)
    assert 'product_id' in (r.get_json().get('message') or ''), r.get_json()


def test_creating_a_promotion_against_another_companys_category_is_refused():
    _cid_a, client_a = _new_shop()
    _cid_b, client_b = _new_shop()
    cat_b = _create_category(client_b)
    r = _create_promotion(client_a, discount_pct=10, category_id=cat_b, expect=400)
    assert 'category_id' in (r.get_json().get('message') or ''), r.get_json()


def test_creating_a_promotion_against_another_companys_branch_is_refused():
    _cid_a, client_a = _new_shop()
    cid_b, client_b = _new_shop()
    pid_a = _create_product(client_a, sell_price=100.0)
    # `_default_branch` self-heals a "Main Branch" row the first time ANY
    # write needs one (create_product calls it unconditionally, ~line 1583)
    # -- company B has no branch until something creates one, so a product
    # is created here purely to get a real branch_id to attempt.
    _create_product(client_b, sell_price=100.0)
    branch_b = client_b.get(f'{API}/branches').get_json()['data'][0]['id']
    r = _create_promotion(client_a, discount_pct=10, product_id=pid_a, branch_id=branch_b, expect=400)
    assert 'branch_id' in (r.get_json().get('message') or ''), r.get_json()


# ── Route-level validation (create/update) ───────────────────────────────────

def test_create_promotion_requires_exactly_one_of_product_or_category(shop):
    cid, client = shop
    r1 = client.post(f'{API}/promotions', json={'name': 'Neither', 'discount_pct': 10})
    assert r1.status_code == 400, r1.get_json()
    pid = _create_product(client)
    cat = _create_category(client)
    r2 = client.post(f'{API}/promotions', json={
        'name': 'Both', 'discount_pct': 10, 'product_id': pid, 'category_id': cat,
    })
    assert r2.status_code == 400, r2.get_json()


def test_create_promotion_rejects_discount_pct_out_of_range(shop):
    cid, client = shop
    pid = _create_product(client)
    for bad in (0, -5, 101, 250):
        r = client.post(f'{API}/promotions', json={
            'name': 'Bad pct', 'discount_pct': bad, 'product_id': pid,
        })
        assert r.status_code == 400, (bad, r.get_json())


def test_create_promotion_rejects_ends_at_before_starts_at(shop):
    cid, client = shop
    pid = _create_product(client)
    r = _create_promotion(client, product_id=pid, starts_at=_future(days=5), ends_at=_future(days=1), expect=400)
    assert r.status_code == 400, r.get_json()


def test_delete_promotion_deactivates_and_active_promotions_stop_applying(shop):
    cid, client = shop
    pid = _create_product(client, sell_price=100.0)
    promo_id = _create_promotion(client, discount_pct=50, product_id=pid)

    r = client.delete(f'{API}/promotions/{promo_id}')
    assert r.status_code == 200, r.get_json()

    sale = _sale(client, [{'product_id': pid, 'quantity': 1}])
    assert sale.status_code == 200, sale.get_json()
    assert sale.get_json()['data']['lines'][0]['discount_pct'] == 0


def test_get_promotions_active_omits_branch_id_and_resolves_server_side(shop):
    """The shipped frontend (commit 3096bc4) calls GET /promotions/active
    with NO branch_id -- this pins that the route still resolves correctly
    with none supplied, via _default_branch, exactly like create_sale
    itself does when branch_id is omitted."""
    cid, client = shop
    pid = _create_product(client, sell_price=100.0)
    _create_promotion(client, discount_pct=15, product_id=pid)
    r = client.get(f'{API}/promotions/active')
    assert r.status_code == 200, r.get_json()
    rows = r.get_json()['data']
    assert any(row['product_id'] == pid and row['discount_pct'] == 15 for row in rows), rows


# ── 11. Schema v23 migration ─────────────────────────────────────────────────

def test_v23_migration_lands_on_head_and_is_idempotent():
    """Same shape as retail_product_lookup_test.py's own
    test_v22_migration_lands_on_head_and_is_idempotent. RETAIL_SCHEMA_
    VERSION is read from the live module, never frozen into this file as a
    literal 23."""
    conn = sch.get_retail_conn()
    assert sch.RETAIL_SCHEMA_VERSION >= 23, (
        f'RETAIL_SCHEMA_VERSION went BACKWARDS to {sch.RETAIL_SCHEMA_VERSION}: '
        f'the v23 promotions step has been lost from the chain')
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == sch.RETAIL_SCHEMA_VERSION, (
        f'expected a fresh install to land on the current schema head '
        f'({sch.RETAIL_SCHEMA_VERSION}), got {version}')

    tables_before = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }

    def _index_names(table):
        return {row[1] for row in conn.execute(f'PRAGMA index_list("{table}")').fetchall()}

    promo_idx_before = _index_names('promotions')
    sip_idx_before = _index_names('sale_item_promotions')

    # Re-run directly, twice, against the SAME already-migrated connection --
    # the normal case after any interrupted migration, since
    # ensure_schema_version leaves user_version un-advanced on failure.
    sch._migrate_add_promotions(conn)
    sch._migrate_add_promotions(conn)

    tables_after = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert tables_after == tables_before, 'a retried migration changed the table set -- not idempotent'
    assert _index_names('promotions') == promo_idx_before, 'a retried migration changed the index set -- not idempotent'
    assert _index_names('sale_item_promotions') == sip_idx_before, 'a retried migration changed the index set -- not idempotent'
    for expected in ('idx_promotions_company_live',):
        assert expected in promo_idx_before, promo_idx_before
    for expected in ('idx_sale_item_promotions_item',):
        assert expected in sip_idx_before, sip_idx_before
    conn.close()
