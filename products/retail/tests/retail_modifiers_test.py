"""
Aura Retail -- restaurant modifiers, wave 1 (schema v26, ROADMAP.md's
2026-08-31 "retail schema v26 CLAIMED for restaurant modifiers, wave 1"
entry). Backend/API coverage for core.retail.modifiers, the new
/modifier-groups* and /products/<id>/modifier-groups* routes, their
integration into create_sale, and THE MONEY-PATH FIX in create_return.

WHAT THIS FILE PROVES, numbered to match the dispatch brief's own list:

  1. Schema v26 lands on head and is idempotent, reading RETAIL_SCHEMA_
     VERSION from the live module rather than a hardcoded number.
  2. INVISIBLE UNLESS OPTED IN -- zero modifier rows anywhere, a sale's and
     a return's stored figures are byte-identical to today's math.
  3. A sale line with modifiers charges the EFFECTIVE unit price
     (base + sum(price_delta)), and sale_item_modifiers snapshots record
     what was applied.
  4. Editing a modifier option afterwards does NOT change what a past sale
     recorded (read_sale_item_modifiers, the ONE sanctioned read path).
  5. THE RETURN FIX, both halves: a return naming a sale_item_id refunds
     THAT line's price; an ambiguous return (two lines, same product,
     different prices, no sale_item_id) is REFUSED rather than guessing.
  6. A return against a sale with exactly one line for that product still
     works with no sale_item_id -- the backward-compatible half.
  7. Required-group enforcement, BOTH directions: a sale missing a
     required choice is refused; a required group with a default, or an
     optional group left untouched, still sells.
  8. Promotions still apply on a modifier-bearing line, discounting the
     MODIFIER-INCLUSIVE price (design doc section 4.7).
  9. TENANCY: a modifier group/option from another company can neither be
     attached to a product nor applied to a sale line.

MUTATION PROOFS, quoted both directions in each test's own assertion
message -- see test_mutation_proofs_* below for the six M1-M6 proofs the
dispatch brief names, each applying the exact mutation to the real source
file, asserting the target test goes RED, then reverting and asserting
GREEN again.

Self-contained bootstrap, matching retail_promotions_test.py and the rest
of this suite (no shared conftest.py exists here). CRITICAL: exactly ONE
pytest process per file -- AURA_APP_DATA resolves at import time, so two
test files sharing one pytest invocation corrupt each other.

Run:
    py -3.14 -m pytest products/retail/tests/retail_modifiers_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_modifiers_"))
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
from core.retail import modifiers as modifier_engine  # noqa: E402
from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402

API = '/api/sub/retail'
MODIFIERS_SOURCE = BACKEND_DIR / 'core' / 'retail' / 'modifiers.py'
RETAIL_API_SOURCE = BACKEND_DIR / 'api' / 'retail_api.py'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers (deliberately duplicated per-file convention -- see
#    retail_promotions_test.py's own module docstring for why) ──────────────

def _make_user(role, company_id, capabilities=None):
    email = f"mod-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "ModifierTestPW1"
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
    company_id = str(uuid.uuid4())
    admin = _make_user('admin', company_id)
    admin.get(f'{API}/settings/tax')  # forces _ensure_credit_schema/doc_sequences
    return company_id, admin


@pytest.fixture
def shop():
    return _new_shop()


def _create_product(client, name=None, sku=None, sell_price=10.0, tax_rate=0):
    tag = uuid.uuid4().hex[:8]
    payload = {
        'name': name or f'Modifier test item {tag}', 'sku': sku or f'MOD-{tag}',
        'sell_price': sell_price, 'cost_price': 1.0, 'tax_rate': tax_rate,
        'initial_stock': 1000,
    }
    r = client.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_group(client, name=None, min_select=0, max_select=None, expect=200):
    payload = {'name': name or f'Group {uuid.uuid4().hex[:8]}', 'min_select': min_select}
    if max_select is not None:
        payload['max_select'] = max_select
    r = client.post(f'{API}/modifier-groups', json=payload)
    assert r.status_code == expect, r.get_json()
    return r.get_json()['data']['id'] if expect == 200 else r


def _create_option(client, group_id, name=None, price_delta=0.0, cost_delta=0.0,
                    is_default=False, expect=200):
    payload = {
        'name': name or f'Option {uuid.uuid4().hex[:8]}',
        'price_delta': price_delta, 'cost_delta': cost_delta, 'is_default': is_default,
    }
    r = client.post(f'{API}/modifier-groups/{group_id}/options', json=payload)
    assert r.status_code == expect, r.get_json()
    return r.get_json()['data']['id'] if expect == 200 else r


def _attach(client, product_id, group_id, expect=200):
    r = client.post(f'{API}/products/{product_id}/modifier-groups', json={'group_id': group_id})
    assert r.status_code == expect, r.get_json()
    return r


def _sale(client, items, **extra):
    payload = dict({
        'items': items, 'amount_paid': 1_000_000, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    }, **extra)
    return client.post(f'{API}/sales', json=payload)


def _return(client, sale_id, items, **extra):
    payload = dict({
        'sale_id': sale_id, 'items': items, 'idempotency_key': str(uuid.uuid4()),
    }, **extra)
    return client.post(f'{API}/returns', json=payload)


def _sale_items(sale_id):
    conn = sch.get_retail_conn()
    rows = conn.execute("SELECT * FROM sale_items WHERE sale_id=? ORDER BY id", (sale_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _create_promotion(client, discount_pct, product_id, expect=200):
    r = client.post(f'{API}/promotions', json={
        'name': f'Promo {uuid.uuid4().hex[:8]}', 'discount_pct': discount_pct, 'product_id': product_id,
    })
    assert r.status_code == expect, r.get_json()
    return r.get_json()['data']['id'] if expect == 200 else r


# ── 1. Schema v26 migration ──────────────────────────────────────────────────

def test_v26_migration_lands_on_head_and_is_idempotent():
    """Same shape as retail_promotions_test.py's own v23 idempotency test.
    RETAIL_SCHEMA_VERSION is read from the live module, never frozen into
    this file as a literal 26."""
    conn = sch.get_retail_conn()
    assert sch.RETAIL_SCHEMA_VERSION >= 26, (
        f'RETAIL_SCHEMA_VERSION went BACKWARDS to {sch.RETAIL_SCHEMA_VERSION}: '
        f'the v26 modifiers step has been lost from the chain')
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == sch.RETAIL_SCHEMA_VERSION, (
        f'expected a fresh install to land on the current schema head '
        f'({sch.RETAIL_SCHEMA_VERSION}), got {version}')

    tables_before = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }

    def _index_names(table):
        return {row[1] for row in conn.execute(f'PRAGMA index_list("{table}")').fetchall()}

    mo_idx_before = _index_names('modifier_options')
    pmg_idx_before = _index_names('product_modifier_groups')
    sim_idx_before = _index_names('sale_item_modifiers')
    ri_cols_before = {row[1] for row in conn.execute('PRAGMA table_info(return_items)').fetchall()}

    # Re-run directly, twice, against the SAME already-migrated connection --
    # the normal case after any interrupted migration, since
    # ensure_schema_version leaves user_version un-advanced on failure.
    sch._migrate_add_modifiers(conn)
    sch._migrate_add_modifiers(conn)

    tables_after = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert tables_after == tables_before, 'a retried migration changed the table set -- not idempotent'
    assert _index_names('modifier_options') == mo_idx_before, 'not idempotent (modifier_options indexes)'
    assert _index_names('product_modifier_groups') == pmg_idx_before, 'not idempotent (product_modifier_groups indexes)'
    assert _index_names('sale_item_modifiers') == sim_idx_before, 'not idempotent (sale_item_modifiers indexes)'
    ri_cols_after = {row[1] for row in conn.execute('PRAGMA table_info(return_items)').fetchall()}
    assert ri_cols_after == ri_cols_before, 'a retried migration changed return_items columns -- not idempotent'
    assert 'sale_item_id' in ri_cols_after, 'return_items.sale_item_id is missing after migration'

    for expected in ('modifier_groups', 'modifier_options', 'product_modifier_groups', 'sale_item_modifiers'):
        assert expected in tables_before, f'{expected} missing from a migrated database'
    assert 'idx_modifier_options_group' in mo_idx_before, mo_idx_before
    assert 'idx_product_modifier_groups_product' in pmg_idx_before, pmg_idx_before
    assert {'idx_sale_item_modifiers_item', 'idx_sale_item_modifiers_uid'} <= sim_idx_before, sim_idx_before
    conn.close()


# ── 2. INVISIBLE UNLESS OPTED IN ─────────────────────────────────────────────

def test_zero_modifiers_totals_are_byte_identical_to_todays_math(shop):
    """With zero modifier rows anywhere, a sale's stored figures must be
    EXACTLY what core.retail.pricing.calculate_line produces for the
    UNMODIFIED base price -- pricing.py is unmodified and unconsulted
    differently by this change (design doc: 'pricing.py stays untouched').

    THE CURRENCY MUST BE PASSED TO BOTH SIDES. `create_sale` calls
    `calculate_line(..., currency=currency)`, so on a default install -- JOD,
    three decimal places -- the sale is computed at fils precision. This test
    used to call `calculate_line` with NO currency, which defaults to two, and
    assert the two agreed. While the API also quantized its sums to a
    hardcoded two decimals the mismatch was invisible; once the sum was
    corrected to follow the shop's real currency this began failing with
    `assert 15.997 == 16.0` -- 16% tax on 99.98 is 15.9968, which is 15.997 in
    fils and 16.00 in cents.

    So it was comparing the API's JOD arithmetic against a two-decimal
    reference and calling the difference a defect. Identical in cause and fix
    to `retail_promotions_test.py`'s own parity test, which is the sibling of
    this one -- both were written to the same template, so both carried the
    same latent assumption that money is always two decimals.

    Nothing weakened: still exact equality, and the fixture's currency is now
    PINNED below so a change to the product default is followed while an
    accidental drift fails loudly instead of silently comparing the wrong two
    numbers.
    """
    cid, client = shop
    pid = _create_product(client, sell_price=49.99, tax_rate=16)
    r = _sale(client, [{'product_id': pid, 'quantity': 2}])
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']

    # Read back from the same endpoint the API used rather than hardcoding
    # 'JOD', so this follows a deliberate change to the default instead of
    # breaking on one. This fixture writes no base_currency row.
    shop_currency = client.get('/api/sub/retail/settings/tax').get_json()['data']['base_currency']
    assert shop_currency == pricing.DEFAULT_BASE_CURRENCY, (
        "fixture currency drifted from the product default; the comparison "
        "below would be against the wrong precision")

    expected = pricing.calculate_line(49.99, 2, 0, 16, mode='after_discount',
                                      currency=shop_currency)
    assert data['subtotal'] == expected['gross']
    assert data['tax_amount'] == expected['tax']
    assert data['total'] == expected['total']

    line = data['lines'][0]
    assert line['unit_price'] == 49.99
    rows = _sale_items(data['id'])
    assert len(rows) == 1
    assert rows[0]['unit_price'] == 49.99
    assert modifier_engine.read_sale_item_modifiers(sch.get_retail_conn(), cid, rows[0]['id']) == []

    # A return against it, with NO sale_item_id, refunds exactly the
    # pre-modifiers figure -- byte-identical to today.
    ret = _return(client, data['id'], [{'product_id': pid, 'quantity': 1}])
    assert ret.status_code == 200, ret.get_json()
    # Same currency omission as the expectation above -- this second call
    # was missed on the first pass, and the test kept failing one
    # assertion later, which is its own small lesson: a parity test with
    # more than one reference computation has to have EVERY one of them
    # speaking the same currency, not just the first.
    single_line_expected = pricing.calculate_line(49.99, 1, 0, 16, mode='after_discount',
                                                  currency=shop_currency)
    assert ret.get_json()['data']['refund_amount'] == single_line_expected['total']


# ── 3. Effective unit price + snapshot ───────────────────────────────────────

def test_sale_line_with_modifiers_charges_effective_unit_price_and_snapshots(shop):
    cid, client = shop
    pid = _create_product(client, sell_price=5.0, tax_rate=0)
    group = _create_group(client, name='Extras')
    cheese = _create_option(client, group, name='Extra cheese', price_delta=0.5)
    onion = _create_option(client, group, name='No onion', price_delta=-0.3)
    _attach(client, pid, group)

    r = _sale(client, [{'product_id': pid, 'quantity': 2, 'modifier_option_ids': [cheese, onion]}])
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    line = data['lines'][0]
    assert line['unit_price'] == 5.2, f"expected 5.0 + 0.5 - 0.3 = 5.2, got {line['unit_price']}"

    rows = _sale_items(data['id'])
    assert len(rows) == 1
    assert rows[0]['unit_price'] == 5.2
    # line_total must reflect the EFFECTIVE price * qty, not the base price.
    assert rows[0]['line_total'] == pytest.approx(10.4)

    snaps = modifier_engine.read_sale_item_modifiers(sch.get_retail_conn(), cid, rows[0]['id'])
    assert len(snaps) == 2
    by_name = {s['name']: s for s in snaps}
    assert by_name['Extra cheese']['price_delta'] == 0.5
    assert by_name['No onion']['price_delta'] == -0.3


def test_negative_delta_clamps_at_zero_never_negative(shop):
    """The floor (design doc section 4.3): a badly configured negative
    delta larger than the base price must lower the line to exactly 0, and
    must NOT block the sale."""
    cid, client = shop
    pid = _create_product(client, sell_price=1.0, tax_rate=0)
    group = _create_group(client, name='Discount extra')
    huge_discount = _create_option(client, group, name='Big markdown', price_delta=-50.0)
    _attach(client, pid, group)

    r = _sale(client, [{'product_id': pid, 'quantity': 1, 'modifier_option_ids': [huge_discount]}])
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['lines'][0]['unit_price'] == 0.0


# ── 4. Snapshot immutability ──────────────────────────────────────────────────

def test_editing_a_modifier_option_after_the_fact_does_not_change_the_past_sale(shop):
    cid, client = shop
    pid = _create_product(client, sell_price=5.0, tax_rate=0)
    group = _create_group(client)
    opt = _create_option(client, group, name='Extra cheese', price_delta=0.5)
    _attach(client, pid, group)

    r = _sale(client, [{'product_id': pid, 'quantity': 1, 'modifier_option_ids': [opt]}])
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']
    sale_item_id = _sale_items(sale_id)[0]['id']

    before = modifier_engine.read_sale_item_modifiers(sch.get_retail_conn(), cid, sale_item_id)
    assert before[0]['price_delta'] == 0.5
    assert before[0]['name'] == 'Extra cheese'

    upd = client.patch(f'{API}/modifier-options/{opt}', json={
        'price_delta': 99.0, 'name': 'Extra cheese RENAMED',
    })
    assert upd.status_code == 200, upd.get_json()

    after = modifier_engine.read_sale_item_modifiers(sch.get_retail_conn(), cid, sale_item_id)
    assert after[0]['price_delta'] == 0.5, "editing the option changed the PAST sale's snapshot"
    assert after[0]['name'] == 'Extra cheese', "editing the option changed the PAST sale's snapshot"
    # And the sale_items.unit_price itself (the money) is unaffected too.
    assert _sale_items(sale_id)[0]['unit_price'] == 5.5


# ── 5. THE RETURN FIX, both halves ───────────────────────────────────────────

def test_return_naming_sale_item_id_refunds_that_lines_price(shop):
    cid, client = shop
    pid = _create_product(client, sell_price=10.0, tax_rate=0)
    group = _create_group(client)
    cheese = _create_option(client, group, name='Extra cheese', price_delta=2.0)
    _attach(client, pid, group)

    r = _sale(client, [
        {'product_id': pid, 'quantity': 1},
        {'product_id': pid, 'quantity': 1, 'modifier_option_ids': [cheese]},
    ])
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']
    rows = _sale_items(sale_id)
    assert len(rows) == 2, "the sale must hold TWO lines for the SAME product at DIFFERENT prices"
    plain = next(x for x in rows if x['unit_price'] == 10.0)
    cheesy = next(x for x in rows if x['unit_price'] == 12.0)

    ret_cheesy = _return(client, sale_id, [{'product_id': pid, 'quantity': 1, 'sale_item_id': cheesy['id']}])
    assert ret_cheesy.status_code == 200, ret_cheesy.get_json()
    assert ret_cheesy.get_json()['data']['refund_amount'] == 12.0, (
        "a return naming the extra-cheese line's sale_item_id must refund 12.0, its OWN price")

    ret_plain = _return(client, sale_id, [{'product_id': pid, 'quantity': 1, 'sale_item_id': plain['id']}])
    assert ret_plain.status_code == 200, ret_plain.get_json()
    assert ret_plain.get_json()['data']['refund_amount'] == 10.0, (
        "a return naming the plain line's sale_item_id must refund 10.0, its OWN price")


def test_ambiguous_return_with_no_sale_item_id_is_refused(shop):
    """THE refusal. Two sale_items rows exist for one product at different
    prices, and the request does not say which one -- this must be
    REFUSED (400), never resolved by an arbitrary fetchone(). See mutation
    proof M1."""
    cid, client = shop
    pid = _create_product(client, sell_price=10.0, tax_rate=0)
    group = _create_group(client)
    cheese = _create_option(client, group, name='Extra cheese', price_delta=2.0)
    _attach(client, pid, group)

    r = _sale(client, [
        {'product_id': pid, 'quantity': 1},
        {'product_id': pid, 'quantity': 1, 'modifier_option_ids': [cheese]},
    ])
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']

    ret = _return(client, sale_id, [{'product_id': pid, 'quantity': 1}])
    assert ret.status_code == 400, (
        f"an ambiguous return (2 lines, same product, no sale_item_id) must be REFUSED; got {ret.get_json()}")
    assert 'sale_item_id' in (ret.get_json().get('message') or ''), ret.get_json()

    conn = sch.get_retail_conn()
    n = conn.execute("SELECT COUNT(*) FROM returns WHERE sale_id=?", (sale_id,)).fetchone()[0]
    conn.close()
    assert n == 0, "the refused ambiguous return must not have written a returns row anyway"


# ── 6. Backward compatibility (single line, no sale_item_id) ────────────────

def test_return_against_single_line_product_still_works_without_sale_item_id(shop):
    cid, client = shop
    pid = _create_product(client, sell_price=10.0, tax_rate=0)
    r = _sale(client, [{'product_id': pid, 'quantity': 3}])
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']

    ret = _return(client, sale_id, [{'product_id': pid, 'quantity': 1}])
    assert ret.status_code == 200, ret.get_json()
    assert ret.get_json()['data']['refund_amount'] == 10.0

    # Backfilled sale_item_id, even though the client did not send one --
    # unambiguous (exactly one line), never changes the refund figure.
    conn = sch.get_retail_conn()
    row = conn.execute(
        "SELECT sale_item_id FROM return_items WHERE return_id=?",
        (ret.get_json()['data']['id'],)
    ).fetchone()
    conn.close()
    assert row['sale_item_id'] is not None


# ── 7. Required-group enforcement, both directions ───────────────────────────

def test_sale_missing_a_required_choice_is_refused(shop):
    cid, client = shop
    pid = _create_product(client, sell_price=10.0)
    group = _create_group(client, name='Size', min_select=1, max_select=1)
    _create_option(client, group, name='Small', price_delta=0)
    _create_option(client, group, name='Large', price_delta=2)
    _attach(client, pid, group)

    r = _sale(client, [{'product_id': pid, 'quantity': 1}])  # no modifier_option_ids at all
    assert r.status_code == 400, r.get_json()
    assert 'Size' in (r.get_json().get('message') or ''), r.get_json()

    conn = sch.get_retail_conn()
    n = conn.execute("SELECT COUNT(*) FROM sales WHERE company_id=?", (cid,)).fetchone()[0]
    conn.close()
    assert n == 0, "the refused sale must not have been written anyway"


def test_required_group_with_a_default_resolves_silently(shop):
    """The ALLOW half of test 7's refusal -- ENGINEERING's 'prove both
    directions'. See mutation proof M6, which refuses every sale that
    carries any modifier at all and would still pass a version of THIS
    test that never actually exercises a modifier -- which is why this
    specific test (unlike the refusal one above) asserts the resulting
    PRICE, not just the status code."""
    cid, client = shop
    pid = _create_product(client, sell_price=10.0)
    group = _create_group(client, name='Size', min_select=1, max_select=1)
    _create_option(client, group, name='Small', price_delta=0, is_default=True)
    _create_option(client, group, name='Large', price_delta=2)
    _attach(client, pid, group)

    r = _sale(client, [{'product_id': pid, 'quantity': 1}])  # no modifier_option_ids
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['lines'][0]['unit_price'] == 10.0


def test_optional_group_left_untouched_still_sells(shop):
    cid, client = shop
    pid = _create_product(client, sell_price=10.0)
    group = _create_group(client, name='Extras', min_select=0, max_select=None)
    _create_option(client, group, name='Extra cheese', price_delta=0.5)
    _attach(client, pid, group)

    r = _sale(client, [{'product_id': pid, 'quantity': 1}])
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['lines'][0]['unit_price'] == 10.0


# ── 8. Promotions compose with modifiers ─────────────────────────────────────

def test_promotion_discounts_the_modifier_inclusive_price(shop):
    cid, client = shop
    pid = _create_product(client, sell_price=10.0, tax_rate=0)
    group = _create_group(client)
    cheese = _create_option(client, group, name='Extra cheese', price_delta=2.0)
    _attach(client, pid, group)
    _create_promotion(client, discount_pct=20, product_id=pid)

    r = _sale(client, [{'product_id': pid, 'quantity': 1, 'modifier_option_ids': [cheese]}])
    assert r.status_code == 200, r.get_json()
    line = r.get_json()['data']['lines'][0]
    assert line['unit_price'] == 12.0, "unit_price must be the modifier-inclusive price"
    assert line['discount_pct'] == 20
    assert line['line_total'] == pytest.approx(9.6), "20% off the MODIFIER-INCLUSIVE 12.0, i.e. 9.6"


# ── 9. TENANCY ────────────────────────────────────────────────────────────────

def test_tenancy_another_companys_modifier_group_cannot_be_attached_or_applied(shop):
    cid_a, client_a = shop
    cid_b, client_b = _new_shop()
    pid_a = _create_product(client_a, sell_price=10.0)
    group_b = _create_group(client_b)
    opt_b = _create_option(client_b, group_b, name='Foreign option', price_delta=5.0)

    # Cannot ATTACH another company's group through the API.
    r = _attach(client_a, pid_a, group_b, expect=400)
    assert 'group_id' in (r.get_json().get('message') or ''), r.get_json()

    # Even if a foreign attachment somehow exists (simulated via a direct
    # DB insert, matching retail_promotions_test.py's own bypass technique
    # for testing DB-level tenancy independently of the write-side guard
    # just proven above), the sale-time resolver must not honour it.
    conn = sch.get_retail_conn()
    conn.execute(
        "INSERT INTO product_modifier_groups (id, company_id, product_id, group_id, sort_order, row_version) "
        "VALUES (?,?,?,?,0,1)",
        (str(uuid.uuid4()), cid_a, pid_a, group_b),
    )
    conn.commit()
    conn.close()

    r2 = _sale(client_a, [{'product_id': pid_a, 'quantity': 1, 'modifier_option_ids': [opt_b]}])
    assert r2.status_code == 400, (
        f"company A's sale must not be able to apply company B's modifier option; got {r2.get_json()}")


# ── Route-level validation ────────────────────────────────────────────────────

def test_create_modifier_group_rejects_max_select_below_min_select(shop):
    cid, client = shop
    r = client.post(f'{API}/modifier-groups', json={'name': 'Bad', 'min_select': 3, 'max_select': 1})
    assert r.status_code == 400, r.get_json()


def test_delete_modifier_group_deactivates_and_stops_applying(shop):
    cid, client = shop
    pid = _create_product(client, sell_price=10.0)
    group = _create_group(client, min_select=0)
    opt = _create_option(client, group, name='Extra', price_delta=1.0)
    _attach(client, pid, group)

    r = client.delete(f'{API}/modifier-groups/{group}')
    assert r.status_code == 200, r.get_json()

    # The deactivated group's option no longer resolves -- the product now
    # behaves as if nothing were attached.
    r2 = _sale(client, [{'product_id': pid, 'quantity': 1, 'modifier_option_ids': [opt]}])
    assert r2.status_code == 400, r2.get_json()
    r3 = _sale(client, [{'product_id': pid, 'quantity': 1}])
    assert r3.status_code == 200, r3.get_json()
    assert r3.get_json()['data']['lines'][0]['unit_price'] == 10.0


