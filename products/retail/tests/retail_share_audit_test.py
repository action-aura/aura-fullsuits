"""
Aura Retail -- P1a "Send on WhatsApp" share audit (crew D, contracts.md's
"Share audit (D builds, T calls)" interface).

POST /api/sub/retail/share-log writes exactly ONE row to the EXISTING
audit_log table (via the existing `_audit()` helper -- no new table, no
schema change) every time a cashier/manager/owner shares a receipt,
statement or reminder over WhatsApp. crew T's share.js (window.AuraShare)
calls this AFTER opening wa.me; this file only proves the route itself:

  1. A signed-in OWNER writes exactly one row naming them (count the check,
     not the outcome -- count audit_log rows before/after, not just the
     'ok' response).
  2. A signed-in CASHIER writes exactly one row naming them (both directions
     of "who is signed in" matter here, not just the admin-bypass path).
  3. No session -> 401, ZERO rows written.
  4. An unknown channel/kind -> 400, ZERO rows written.
  5. A ref over 64 characters -> 400, ZERO rows written.

Fix round (verifier findings F1/F5/F6, 2026-09-28) widened this file to also
cover POST/GET /api/sub/retail/settings/branding's `shop_whatsapp_number`
field -- the SAME feature cluster (this is the number the idle-screen QR and
crew T's Settings field both read/write), and the concrete bug the verifier
found was that a real Settings write could never reach this table at all
(the field was missing from `_BRANDING_TEXT_KEYS` entirely).

Self-contained bootstrap (own AURA_APP_DATA, own Flask app instance),
matching retail_promotions_test.py's own `_make_user`/`_new_shop` pattern --
no shared conftest.py exists in this suite. CRITICAL: exactly ONE pytest
process per file.

Run:
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest products/retail/tests/retail_share_audit_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_share_audit_"))
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
from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers (deliberately duplicated per-file -- see
#    retail_product_lookup_nocase_test.py's own module docstring for why
#    nothing here is shared via a conftest.py) ──────────────────────────────

def _make_user(role, company_id, capabilities=None):
    """A real account with real capability rows, seeded through the SAME
    production helper account creation uses -- matches
    retail_promotions_test.py's own `_make_user`."""
    email = f"share-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "ShareTestPW1"
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


def _new_shop():
    company_id = str(uuid.uuid4())
    admin, admin_id = _make_user('admin', company_id)
    admin.get(f'{API}/settings/tax')  # forces _ensure_credit_schema/doc_sequences, matching sibling files
    return company_id, admin, admin_id


@pytest.fixture
def shop():
    return _new_shop()


def _audit_rows(company_id):
    conn = sch.get_retail_conn()
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE company_id=? ORDER BY id", (company_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _share(client, channel='whatsapp', kind='receipt', ref='SALE-000124'):
    return client.post(f'{API}/share-log', json={'channel': channel, 'kind': kind, 'ref': ref})


# ── 1/2. Signed-in owner/cashier each write EXACTLY one row naming them ────

def test_signed_in_owner_writes_exactly_one_row_naming_them(shop):
    company_id, owner, owner_id = shop
    before = _audit_rows(company_id)

    r = _share(owner, kind='receipt', ref='SALE-000124')
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {'ok': True}

    after = _audit_rows(company_id)
    # COUNT THE CHECK, not the outcome: a 200 alone cannot tell "one row was
    # written" from "the route silently wrote nothing and returned ok
    # anyway" -- see ENGINEERING.md's "asserts an outcome where it should
    # assert the check ran". This counts rows before/after.
    assert len(after) == len(before) + 1
    new_row = [r for r in after if r not in before][0]
    assert new_row['user_id'] == owner_id, "the audit row must name the SIGNED-IN owner, not 'system' or blank"
    assert new_row['entity'] == 'whatsapp'
    # Fix round F6: `ref` lives in `details` (a TEXT column), never
    # `entity_id` (a real INTEGER column that would silently truncate a
    # zero-padded ref like '000124' to 124 -- see test_ref_with_leading_
    # zeros_is_not_truncated below for the mutation proof of exactly that),
    # and the owner's own audit-log viewer (subsystem-retail.js's
    # _renderAuditLog) only ever renders `details` -- entity_id is dropped
    # from that screen entirely (item #17).
    assert new_row['entity_id'] is None
    assert 'receipt' in (new_row['details'] or '')
    assert 'SALE-000124' in (new_row['details'] or ''), (
        f"the audit row's details must name WHAT was shared (the ref) -- got {new_row['details']!r}"
    )


def test_signed_in_cashier_writes_exactly_one_row_naming_them(shop):
    company_id, _owner, _owner_id = shop
    cashier, cashier_id = _make_user('cashier', company_id)
    before = _audit_rows(company_id)

    r = _share(cashier, kind='statement', ref='CUST-42')
    assert r.status_code == 200, r.get_json()

    after = _audit_rows(company_id)
    assert len(after) == len(before) + 1
    new_row = [r for r in after if r not in before][0]
    # BOTH directions of "who is signed in" matter (ENGINEERING.md "prove
    # both directions") -- the owner case above proves the admin-bypass
    # path stamps correctly; this proves an ordinary employee session does
    # too, not just whichever path happens to be exercised first.
    assert new_row['user_id'] == cashier_id
    assert new_row['user_id'] != _owner_id
    assert 'statement' in (new_row['details'] or '')
    assert 'CUST-42' in (new_row['details'] or '')


# Fix round F5 (verifier finding): 'reminder' -- the third of the three
# `_SHARE_LOG_KINDS` -- had no exactly-one-row test at all (only 'receipt'
# and 'statement' did, above).
def test_signed_in_owner_writes_exactly_one_row_for_a_reminder(shop):
    company_id, owner, owner_id = shop
    before = _audit_rows(company_id)

    r = _share(owner, kind='reminder', ref='CUST-77')
    assert r.status_code == 200, r.get_json()

    after = _audit_rows(company_id)
    assert len(after) == len(before) + 1
    new_row = [r for r in after if r not in before][0]
    assert new_row['user_id'] == owner_id
    assert 'reminder' in (new_row['details'] or '')
    assert 'CUST-77' in (new_row['details'] or '')


# Fix round F6 mutation proof: the exact bug named in the finding
# (SQLite's INTEGER-affinity column silently converting a zero-padded
# numeric-looking ref, e.g. '000124', into the number 124) would have been
# invisible to test_signed_in_owner_writes_exactly_one_row_naming_them above
# because that test's ref ('SALE-000124') is not purely numeric -- SQLite
# only coerces a TEXT value that looks EXACTLY like an integer literal. This
# test uses a purely-numeric ref specifically to pin the leading-zero case.
def test_ref_with_leading_zeros_is_not_truncated(shop):
    company_id, owner, _owner_id = shop
    r = _share(owner, kind='receipt', ref='000124')
    assert r.status_code == 200, r.get_json()
    rows = _audit_rows(company_id)
    new_row = rows[-1]
    assert '000124' in (new_row['details'] or ''), (
        f"a zero-padded ref must survive intact in `details` -- got {new_row['details']!r}"
    )


# ── 3. Unauthenticated -> 401, zero rows ────────────────────────────────────

def test_unauthenticated_returns_401_and_writes_zero_rows(shop):
    company_id, _owner, _owner_id = shop
    anon = app.test_client()  # a fresh cookie jar -- no session at all
    before = _audit_rows(company_id)

    r = _share(anon)
    assert r.status_code == 401, r.get_json()

    after = _audit_rows(company_id)
    assert len(after) == len(before)


# ── 4. Bad channel/kind -> 400, zero rows ───────────────────────────────────

def test_bad_channel_returns_400_and_writes_zero_rows(shop):
    company_id, owner, _owner_id = shop
    before = _audit_rows(company_id)

    r = _share(owner, channel='sms')
    assert r.status_code == 400, r.get_json()

    after = _audit_rows(company_id)
    assert len(after) == len(before)


def test_bad_kind_returns_400_and_writes_zero_rows(shop):
    company_id, owner, _owner_id = shop
    before = _audit_rows(company_id)

    r = _share(owner, kind='invoice')
    assert r.status_code == 400, r.get_json()

    after = _audit_rows(company_id)
    assert len(after) == len(before)


# ── 5. ref over 64 chars -> 400, zero rows ──────────────────────────────────

def test_ref_over_64_chars_returns_400_and_writes_zero_rows(shop):
    company_id, owner, _owner_id = shop
    before = _audit_rows(company_id)

    r = _share(owner, ref='X' * 65)
    assert r.status_code == 400, r.get_json()

    after = _audit_rows(company_id)
    assert len(after) == len(before)


def test_ref_at_64_chars_is_accepted(shop):
    """The boundary itself must still work -- a test that only proves 65 is
    refused could pass even if the real limit silently became 10."""
    company_id, owner, _owner_id = shop
    before = _audit_rows(company_id)

    r = _share(owner, ref='X' * 64)
    assert r.status_code == 200, r.get_json()

    after = _audit_rows(company_id)
    assert len(after) == len(before) + 1


# ═════════════════════════════════════════════════════════════════════════
# Fix round F1 -- POST/GET /api/sub/retail/settings/branding's
# shop_whatsapp_number. The verifier's own probe measured the concrete bug:
# a real Settings POST returned 200 but silently dropped the field
# (`post_echo_has_key: false`), so a real shop could set the number over and
# over and the idle-screen QR would never appear (`get_qr_after_real_
# settings_post: null`) -- retail_p1a_display_e2e.py's own D1/D1c scenarios
# had only ever exercised this by writing directly into retail_settings,
# which is exactly why they never caught it.
# ═════════════════════════════════════════════════════════════════════════

def _post_branding(client, **fields):
    return client.post(f'{API}/settings/branding', json=fields)


def _get_branding(client):
    return client.get(f'{API}/settings/branding')


# Every input shape share.js's own normalizeJoPhone accepts (contract:
# "same rules as the contract's normalizeJoPhone") must normalise to the
# SAME stored value -- both sides of this product must agree on one number
# for one shop.
@pytest.mark.parametrize('raw, expected', [
    ('0791234567', '+962791234567'),        # 07XXXXXXXX
    ('791234567', '+962791234567'),         # 7XXXXXXXX
    ('00962791234567', '+962791234567'),    # 00962XXXXXXXXX
    ('+962791234567', '+962791234567'),     # +9627XXXXXXXX
    ('+962 79 123 4567', '+962791234567'),  # spaces
    ('+962-79-123-4567', '+962791234567'),  # dashes
    ('٠٧٩١٢٣٤٥٦٧', '+962791234567'),         # Arabic-Indic digits, 07XXXXXXXX shape
])
def test_owner_can_set_and_read_back_shop_whatsapp_number(shop, raw, expected):
    _company_id, owner, _owner_id = shop
    post = _post_branding(owner, shop_whatsapp_number=raw)
    assert post.status_code == 200, post.get_json()
    post_body = post.get_json()
    # F1: the POST response must ECHO the saved (normalised) number back --
    # the verifier's probe found `post_echo_has_key: false`.
    assert post_body['data'].get('shop_whatsapp_number') == expected, post_body

    get = _get_branding(owner)
    assert get.status_code == 200, get.get_json()
    get_body = get.get_json()
    assert get_body['data'].get('shop_whatsapp_number') == expected, (
        f"GET must return the exact key 'shop_whatsapp_number' with the normalised value -- got {get_body['data']!r}"
    )
    # The idle-screen QR must now exist too (F1's whole point: a real Settings
    # write must actually reach the row the QR is rendered from).
    assert get_body['data'].get('whatsapp_qr_svg_data_uri'), (
        "a real branding POST with a valid number must make the QR appear on the next GET"
    )


@pytest.mark.parametrize('bad', ['12345', '0621234567', 'not-a-phone', '962123456789999'])
def test_invalid_shop_whatsapp_number_returns_400_and_does_not_save(shop, bad):
    """0621234567 is a Jordanian LANDLINE shape (06, not 07) -- a real,
    plausible typo, not just garbage, and it must still be refused: a
    WhatsApp number is a MOBILE number by definition."""
    _company_id, owner, _owner_id = shop
    before = _get_branding(owner).get_json()['data'].get('shop_whatsapp_number')

    r = _post_branding(owner, shop_whatsapp_number=bad)
    assert r.status_code == 400, r.get_json()

    after = _get_branding(owner).get_json()['data'].get('shop_whatsapp_number')
    assert after == before, "an invalid number must not partially save"


def test_empty_shop_whatsapp_number_clears_it(shop):
    _company_id, owner, _owner_id = shop
    assert _post_branding(owner, shop_whatsapp_number='+962791234567').status_code == 200
    assert _get_branding(owner).get_json()['data']['shop_whatsapp_number'] == '+962791234567'

    r = _post_branding(owner, shop_whatsapp_number='')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['shop_whatsapp_number'] == ''

    get_body = _get_branding(owner).get_json()['data']
    assert get_body['shop_whatsapp_number'] == ''
    assert get_body.get('whatsapp_qr_svg_data_uri') is None, "clearing the number must clear the QR too"


def test_invalid_number_does_not_clobber_other_branding_fields_in_the_same_post(shop):
    """Validate-before-write, all or nothing: an invalid phone number in the
    SAME request as a valid business_name must not let the business_name
    half silently save while the phone half is refused."""
    _company_id, owner, _owner_id = shop
    r = _post_branding(owner, branding_business_name='Should Not Save', shop_whatsapp_number='bad-number')
    assert r.status_code == 400, r.get_json()
    get_body = _get_branding(owner).get_json()['data']
    assert get_body.get('branding_business_name') != 'Should Not Save', (
        "an invalid shop_whatsapp_number must refuse the WHOLE request, not save sibling fields"
    )


def test_cashier_cannot_set_shop_whatsapp_number_owner_can(shop):
    """Both directions: cashier 403, owner 200 -- same owner-only gate every
    other branding field already carries (_require_company_admin)."""
    company_id, owner, _owner_id = shop
    cashier, _cashier_id = _make_user('cashier', company_id)

    r_cashier = _post_branding(cashier, shop_whatsapp_number='+962791234567')
    assert r_cashier.status_code == 403, r_cashier.get_json()
    assert _get_branding(cashier).get_json()['data'].get('shop_whatsapp_number') in (None, ''), (
        "a refused cashier write must not have saved anything"
    )

    r_owner = _post_branding(owner, shop_whatsapp_number='+962791234567')
    assert r_owner.status_code == 200, r_owner.get_json()
    assert _get_branding(owner).get_json()['data']['shop_whatsapp_number'] == '+962791234567'


def test_delegated_branch_manager_cannot_set_shop_whatsapp_number(shop):
    """The SHARPER version of the owner-only proof: this route is ALSO
    behind `@mt_require_capability(CAP_EMPLOYEES)`, which a delegated branch
    manager legitimately holds (`retail.employees=full`, the branch-staff-
    management delegation toggle) -- so a cashier's 403 alone cannot prove
    `_require_company_admin()` itself is doing anything, only that the
    capability decorator is. This user HOLDS CAP_EMPLOYEES via delegation
    but is not the owner (`_require_company_admin`'s own docstring: "the
    toggle's own copy promises 'own-branch staff' only" -- company-wide
    settings must still refuse it), so a 403 here can only come from
    `_require_company_admin()` itself."""
    company_id, _owner, _owner_id = shop
    manager, _manager_id = _make_user('manager', company_id, capabilities={'retail.employees': 'full'})
    r = _post_branding(manager, shop_whatsapp_number='+962791234567')
    assert r.status_code == 403, r.get_json()


def test_anonymous_branding_get_and_post_return_401(shop):
    anon = app.test_client()
    assert _get_branding(anon).status_code == 401
    assert _post_branding(anon, shop_whatsapp_number='+962791234567').status_code == 401


# ═════════════════════════════════════════════════════════════════════════
# F7 follow-up (coordinator instruction, P1a idle-screen fix round):
# GET /promotions/active now enriches each row with `category_name`/
# `product_name` -- a company-scoped lookup added directly to
# list_active_promotions (retail_api.py), NOT to core.retail.promotions.
# load_active_promotions() (that function is shared with create_sale's own
# money path and is not this crew's file). customer-display.js reads these
# two keys to show a promotion WITH its scope instead of hiding every real
# promotion (every one is category- or product-scoped by
# create_promotion's own "exactly one of the two" rule).
# ═════════════════════════════════════════════════════════════════════════

def test_promotions_active_includes_category_and_product_names(shop):
    _company_id, owner, _owner_id = shop
    cat_resp = owner.post(f'{API}/categories', json={'name': 'Snacks'})
    assert cat_resp.status_code == 200, cat_resp.get_json()
    cat_id = cat_resp.get_json()['data']['id']

    prod_resp = owner.post(f'{API}/products', json={
        'name': 'Cola 500ml', 'sku': 'COLA-500', 'sell_price': 1.0, 'cost_price': 0.5, 'initial_stock': 10,
    })
    assert prod_resp.status_code == 200, prod_resp.get_json()
    prod_id = prod_resp.get_json()['data']['id']

    cat_promo = owner.post(f'{API}/promotions', json={'name': 'Snack Sale', 'category_id': cat_id, 'discount_pct': 15})
    assert cat_promo.status_code == 200, cat_promo.get_json()
    prod_promo = owner.post(f'{API}/promotions', json={'name': 'Cola Deal', 'product_id': prod_id, 'discount_pct': 20})
    assert prod_promo.status_code == 200, prod_promo.get_json()

    active = owner.get(f'{API}/promotions/active')
    assert active.status_code == 200, active.get_json()
    rows = {r['id']: r for r in active.get_json()['data']}

    cat_row = rows[cat_promo.get_json()['data']['id']]
    assert cat_row['category_name'] == 'Snacks', cat_row
    assert cat_row.get('product_name') is None, cat_row

    prod_row = rows[prod_promo.get_json()['data']['id']]
    assert prod_row['product_name'] == 'Cola 500ml', prod_row
    assert prod_row.get('category_name') is None, prod_row


def test_promotions_active_name_lookup_is_company_scoped(shop):
    """A category belonging to a DIFFERENT company must never leak its name
    into this company's promotions response -- AUDIT-032C's rule, applied
    to the new lookup.

    `categories.id` is a single global AUTOINCREMENT sequence (schema.py),
    not scoped per company, so two companies' ids never collide -- which
    means the ORDINARY case (each company reading its own rows) can never
    exercise this filter at all; without it, `id IN (...)` alone already
    finds the right row by pure luck of unique ids. To actually prove the
    filter does something, this constructs the one case the API's own
    create_promotion validation (_promotion_target_error) is supposed to
    prevent from ever existing -- a promotion whose stored category_id
    points at ANOTHER company's category -- by writing it directly into
    the database, bypassing that validation on purpose. If the enrichment
    lookup ever dropped its `company_id=?` filter, this would resolve and
    leak the other company's category name; with it, the lookup correctly
    finds nothing for a category that does not belong to this promotion's
    own company, and `category_name` comes back None."""
    company_id, owner, _owner_id = shop
    other_company_id = str(uuid.uuid4())
    other_admin, _other_admin_id = _make_user('admin', other_company_id)
    other_cat = other_admin.post(f'{API}/categories', json={'name': 'Other Co Category'})
    assert other_cat.status_code == 200, other_cat.get_json()
    other_cat_id = other_cat.get_json()['data']['id']

    own_cat = owner.post(f'{API}/categories', json={'name': 'Own Category'})
    assert own_cat.status_code == 200
    own_cat_id = own_cat.get_json()['data']['id']
    own_promo = owner.post(f'{API}/promotions', json={'name': 'Own Promo', 'category_id': own_cat_id, 'discount_pct': 10})
    assert own_promo.status_code == 200, own_promo.get_json()
    promo_id = own_promo.get_json()['data']['id']

    # Bypass create_promotion's own validation on purpose (see docstring) --
    # repoint this company's promotion at the OTHER company's category id.
    conn = sch.get_retail_conn()
    conn.execute("UPDATE promotions SET category_id=? WHERE id=? AND company_id=?", (other_cat_id, promo_id, company_id))
    conn.commit()
    conn.close()

    active = owner.get(f'{API}/promotions/active')
    assert active.status_code == 200
    rows = {r['id']: r for r in active.get_json()['data']}
    row = rows[promo_id]
    assert row['category_name'] is None, (
        f"a category_id belonging to ANOTHER company must never resolve a name -- leaked: {row}"
    )
