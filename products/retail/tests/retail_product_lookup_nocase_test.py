"""
Aura Retail -- schema v22 / "the case-fold lookup" BEHAVIOUR coverage
(ROADMAP.md's 2026-08-30 "retail schema v22 CLAIMED for the case-fold
lookup" entry).

Split out of retail_product_lookup_test.py (which keeps the v21/v22
migration-shape and EXPLAIN QUERY PLAN proofs) purely to stay under this
codebase's 800-line file guideline -- there is no functional reason these
tests could not live in that file too, and this one leans on the same
fixtures and conventions rather than inventing new ones.

WHAT THIS FILE PROVES, in order:

1. THE GAP THIS CLOSES: `lookup_product`'s four-rung ladder (barcode exact
   -> barcode NOCASE -> sku exact -> sku NOCASE, schema v22) resolves a
   value STORED mixed-case, which the v21 three-variant `IN (...)` trick
   could not (ROADMAP.md's 2026-08-29 "the case-insensitive lookup is only
   PARTLY case-insensitive" entry -- 'AbC-123' stored, 'abc-123' typed).

2. DETERMINISM: against a legacy install that already holds a
   case-duplicate (direct-SQL-inserted, bypassing the v22 write guards),
   the lookup resolves to the EXACT match, never an arbitrary one of two
   case-variant rows.

3. The three write doors this read change forced closed -- create_product's
   SKU/barcode dup checks, update_product's barcode dup check, and
   import_api's CSV upsert key -- were made case-insensitive so the read
   and the writes agree on what "the same code" means. Both the deny half
   (a case-variant duplicate is rejected/updated-in-place) and the allow
   half (a genuinely different code, or re-saving one's own unchanged
   value, still succeeds) are proven -- the allow half is not optional: a
   "deny everything" mutation of any of these checks would pass every deny
   test and silently destroy the ability to add or import products at all.

4. The blank-barcode exemption and per-company tenancy both survive the
   COLLATE NOCASE change unchanged.

Self-contained bootstrap, matching retail_product_lookup_test.py and the
rest of this suite (no shared conftest.py exists here). CRITICAL: exactly
ONE pytest process per file -- AURA_APP_DATA resolves at import time, so
two test files sharing one pytest invocation corrupt each other.

Run:
    pytest products/retail/tests/retail_product_lookup_nocase_test.py -v
"""
import io
import csv
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_lookup_nocase_"))
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


# ── Fixtures / helpers (deliberately duplicated per-file convention -- see
#    retail_product_lookup_test.py's own module docstring for why nothing
#    here is shared via a conftest.py) ──────────────────────────────────────

def _new_shop(role='admin'):
    """A fresh company with one logged-in user. Each test gets its OWN
    company -- tenancy is exactly what one of these tests exists to pin, so
    a shared company would make one test's product visible to another's
    "not found" assertion."""
    email = f"lookupnc-{uuid.uuid4().hex[:10]}@test.local"
    password = "LookupNcTestPW1"
    company_id = str(uuid.uuid4())
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
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    client.get(f'{API}/settings/tax')  # forces _ensure_credit_schema/doc_sequences, matching sibling files
    return company_id, client


@pytest.fixture
def shop():
    """One company per test by default -- see _new_shop's own docstring."""
    return _new_shop()


def _create_product(client, name=None, sku=None, barcode=None, initial_stock=0, status=None):
    tag = uuid.uuid4().hex[:8]
    payload = {
        'name': name or f'Lookup nocase item {tag}',
        'sku': sku or f'LKPNC-{tag}',
        'sell_price': 10.0, 'cost_price': 5.0, 'tax_rate': 0,
        'initial_stock': initial_stock,
    }
    if barcode is not None:
        payload['barcode'] = barcode
    r = client.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    pid = r.get_json()['data']['id']
    if status:
        r2 = client.patch(f'{API}/products/{pid}', json={'status': status})
        assert r2.status_code == 200, r2.get_json()
    return pid, payload['sku'], payload.get('barcode')


def _direct_insert_product(company_id, sku=None, barcode=None, name=None, pid=None):
    """Inserts a product with a raw SQL INSERT, bypassing create_product's
    dup check entirely -- simulates a LEGACY install that already holds a
    case-duplicate from before schema v22's write-side guards existed (the
    v22 migration itself is non-unique for exactly this reason -- see
    _migrate_add_nocase_lookup_indexes' own docstring in database/schema.py).
    Minimal column set, matching retail_import_export_test.py's own
    direct-insert helper: id/company_id/sku/barcode/name/cost_price/
    sell_price get real values, everything else takes the table's default.

    `pid` is settable (default random) so a caller can control the sort
    order `lookup_product`'s `GROUP BY p.id` imposes among otherwise-tied
    rows -- see test_lookup_is_deterministic_against_a_legacy_case_
    duplicate's own docstring for why this control is load-bearing, not
    cosmetic."""
    conn = sch.get_retail_conn()
    pid = pid or str(uuid.uuid4())
    tag = uuid.uuid4().hex[:8]
    conn.execute(
        "INSERT INTO products (id,company_id,sku,barcode,name,cost_price,sell_price) "
        "VALUES (?,?,?,?,?,1,2)",
        (pid, company_id, sku or f'DIRECT-{tag}', barcode, name or f'Direct insert {tag}'),
    )
    conn.commit()
    conn.close()
    return pid


def _csv_bytes(rows, headers):
    """Matches retail_import_export_test.py's own helper of the same name --
    duplicated per this suite's own no-shared-conftest convention."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=headers)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode('utf-8')


# ── 1. THE GAP THIS CLOSES: resolving a mixed-case STORED value ────────────

def test_lookup_resolves_a_mixed_case_stored_barcode(shop):
    """THE GAP THIS CLOSES. The v21 lookup built three variants of the
    TYPED code (as-typed/.upper()/.lower()) and matched with IN(...) --
    none of which can match a value stored mixed-case. Store 'AbC-...',
    look up the lowercase form: must resolve via the new NOCASE rung. See
    this change's mutation proof #1, which reverts to the v21 IN(...)
    mechanism and turns this red."""
    _cid, client = shop
    barcode = f'AbC-{uuid.uuid4().hex[:8]}'
    pid, sku, _ = _create_product(client, barcode=barcode)
    r = client.get(f'{API}/products/lookup?code={barcode.lower()}')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['id'] == pid


def test_lookup_resolves_a_mixed_case_stored_sku(shop):
    """Same gap as the barcode case above, for SKU -- the column the
    ROADMAP.md measurement actually used ('AbC-123' typed 'abc-123')."""
    _cid, client = shop
    sku = f'SkU-{uuid.uuid4().hex[:8]}'
    pid, sku_actual, _ = _create_product(client, sku=sku)
    assert sku_actual == sku
    r = client.get(f'{API}/products/lookup?code={sku.lower()}')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['id'] == pid


# ── 2. Determinism against a legacy case-duplicate ──────────────────────────

def test_lookup_is_deterministic_against_a_legacy_case_duplicate():
    """DETERMINISM. Two products, direct-SQL-inserted (simulating a legacy
    install that predates the v22 write-side guards, so it already holds a
    case-duplicate on disk), holding barcodes 'ABC' and 'abc' for the SAME
    company. Looking up 'abc' must return the row whose barcode is EXACTLY
    'abc' -- the exact rung must win over the NOCASE rung, not pick
    arbitrarily between two case-duplicates.

    IDs are DELIBERATELY CONTROLLED, not left random, and this is
    load-bearing: `lookup_product`'s SELECT ends in `GROUP BY p.id`, and an
    early version of this test that let both rows get random UUIDs was
    FLAKY under mutation proof #2 -- empirically ~50% green even with the
    exact rungs removed, because `fetchone()` among two NOCASE-tied rows
    returns whichever has the alphabetically-smaller `id`, which has
    nothing to do with which one is the actual typed-case match. Assigning
    the WRONG (uppercase) row the alphabetically SMALLER id here means: if
    the exact rungs are removed, the NOCASE-only tie-break deterministically
    returns the WRONG row every time (proven below), and with the exact
    rungs present the wrong row can never be returned at all -- its barcode
    fails a BINARY equality against 'abc' outright, so the id ordering
    never even gets a vote. See this change's mutation proof #2."""
    cid, client = _new_shop()
    wrong_pid = '00000000-0000-4000-8000-000000000001'  # sorts FIRST
    correct_pid = 'ffffffff-ffff-4fff-8fff-ffffffffffff'  # sorts LAST
    _direct_insert_product(cid, barcode='ABC', pid=wrong_pid)
    _direct_insert_product(cid, barcode='abc', pid=correct_pid)
    r = client.get(f'{API}/products/lookup?code=abc')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['id'] == correct_pid
    assert r.get_json()['data']['id'] != wrong_pid


# ── 3a. Write-side dup guards -- deny half ──────────────────────────────────

def test_create_product_barcode_dup_check_is_case_insensitive(shop):
    """DUP GUARDS, deny half. With 'abc...' stored, POST a product with the
    UPPERCASE form of the same barcode -> 409. The write side must fold
    case the same way the read side now does, or two case-variant barcodes
    can coexist on disk and the case-folding lookup resolves to an
    arbitrary one of them -- AUDIT (2026-08-14)'s failure, reopened through
    a different door. See this change's mutation proof #3, which drops
    COLLATE NOCASE from this check and turns this red."""
    _cid, client = shop
    tag = uuid.uuid4().hex[:8]
    _create_product(client, barcode=f'abc-dup-{tag}')
    r = client.post(f'{API}/products', json={
        'name': 'Dup barcode attempt', 'sku': f'SKU-{uuid.uuid4().hex[:8]}',
        'barcode': f'ABC-DUP-{tag}', 'sell_price': 1, 'cost_price': 1,
    })
    assert r.status_code == 409, r.get_json()


def test_create_product_sku_dup_check_is_case_insensitive(shop):
    """Same deny-half proof as the barcode check above, for SKU."""
    _cid, client = shop
    _, sku, _ = _create_product(client, sku=f'sku-dup-{uuid.uuid4().hex[:8]}')
    r = client.post(f'{API}/products', json={
        'name': 'Dup sku attempt', 'sku': sku.upper(),
        'sell_price': 1, 'cost_price': 1,
    })
    assert r.status_code == 409, r.get_json()


def test_update_product_barcode_dup_check_is_case_insensitive(shop):
    """Same deny-half proof via PATCH -- update_product's own barcode
    check, not create_product's."""
    _cid, client = shop
    tag = uuid.uuid4().hex[:8]
    _create_product(client, barcode=f'patch-dup-{tag}')
    other_pid, _, _ = _create_product(client, barcode=f'other-{tag}')
    r = client.patch(f'{API}/products/{other_pid}', json={'barcode': f'PATCH-DUP-{tag}'})
    assert r.status_code == 409, r.get_json()


# ── 3b. Write-side dup guards -- allow half (MANDATORY) ─────────────────────
#
# Without these, a "deny everything" mutation of any check above would pass
# every deny test in 3a and silently destroy the ability to add or update
# products at all. See this change's mutation proof #4.

def test_create_product_allows_a_genuinely_different_barcode(shop):
    _cid, client = shop
    _create_product(client, barcode=f'allow-half-{uuid.uuid4().hex[:8]}')
    r = client.post(f'{API}/products', json={
        'name': 'Genuinely new', 'sku': f'SKU-{uuid.uuid4().hex[:8]}',
        'barcode': f'GENUINELY-DIFFERENT-{uuid.uuid4().hex[:8]}',
        'sell_price': 1, 'cost_price': 1,
    })
    assert r.status_code == 200, r.get_json()


def test_create_product_allows_a_genuinely_different_sku(shop):
    _cid, client = shop
    _create_product(client, sku=f'allow-half-sku-{uuid.uuid4().hex[:8]}')
    r = client.post(f'{API}/products', json={
        'name': 'Genuinely new sku', 'sku': f'SKU-{uuid.uuid4().hex[:8]}',
        'sell_price': 1, 'cost_price': 1,
    })
    assert r.status_code == 200, r.get_json()


def test_update_product_allows_a_genuinely_new_unique_barcode(shop):
    _cid, client = shop
    pid, _, _ = _create_product(client, barcode=f'orig-{uuid.uuid4().hex[:8]}')
    r = client.patch(f'{API}/products/{pid}', json={'barcode': f'NEW-UNIQUE-{uuid.uuid4().hex[:8]}'})
    assert r.status_code == 200, r.get_json()


def test_update_product_allows_resaving_its_own_unchanged_barcode(shop):
    """The `id<>?` self-exclusion still works under COLLATE NOCASE -- both
    re-saving the identical value and re-saving it in a DIFFERENT case must
    succeed, since both cases are still self-exclusion against the same
    row, not a comparison against another product."""
    _cid, client = shop
    barcode = f'self-{uuid.uuid4().hex[:8]}'
    pid, _, _ = _create_product(client, barcode=barcode)
    r = client.patch(f'{API}/products/{pid}', json={'barcode': barcode})
    assert r.status_code == 200, r.get_json()
    r2 = client.patch(f'{API}/products/{pid}', json={'barcode': barcode.upper()})
    assert r2.status_code == 200, r2.get_json()


# ── 4a. Blank exemption still holds ─────────────────────────────────────────

def test_two_products_with_blank_barcodes_can_both_be_created(shop):
    """BLANK EXEMPTION still holds under COLLATE NOCASE -- create_product's
    dup check is only entered when a barcode is truthy
    (`if data.get('barcode')`), so an empty string never reaches the
    COLLATE NOCASE comparison at all."""
    _cid, client = shop
    r1 = client.post(f'{API}/products', json={
        'name': 'Blank barcode 1', 'sku': f'SKU-{uuid.uuid4().hex[:8]}',
        'sell_price': 1, 'cost_price': 1,
    })
    assert r1.status_code == 200, r1.get_json()
    r2 = client.post(f'{API}/products', json={
        'name': 'Blank barcode 2', 'sku': f'SKU-{uuid.uuid4().hex[:8]}',
        'sell_price': 1, 'cost_price': 1,
    })
    assert r2.status_code == 200, r2.get_json()


# ── 4b. Tenancy -- one company's case-fold never crosses into another's ────
#
# Two separate tests, deliberately not one: a single test that has company B
# create its OWN product with the uppercase barcode and then look up the
# lowercase form would find company B's OWN product either way, proving
# NOTHING about cross-tenant leakage -- caught by actually running this
# during development (see this change's own report). The dup-check half and
# the lookup half are proven independently, each with no competing local
# product to confuse the result.

def test_create_product_dup_check_does_not_cross_tenant_boundaries():
    """TENANCY, dup check half. Company A holding a barcode must NOT block
    company B creating the uppercase form of that SAME code -- the dup
    check is company-scoped, not global."""
    _cid_a, client_a = _new_shop()
    _cid_b, client_b = _new_shop()
    barcode = f'ten-{uuid.uuid4().hex[:8]}'
    _create_product(client_a, barcode=barcode)
    r = client_b.post(f'{API}/products', json={
        'name': 'Company B product', 'sku': f'SKU-{uuid.uuid4().hex[:8]}',
        'barcode': barcode.upper(), 'sell_price': 1, 'cost_price': 1,
    })
    assert r.status_code == 200, r.get_json()


def test_lookup_does_not_cross_tenant_boundaries_under_nocase():
    """TENANCY, lookup half. Company B's case-folding lookup must NOT find
    company A's product. Company B creates nothing matching this code, so a
    200 here can only mean the lookup leaked across the company_id scope."""
    _cid_a, client_a = _new_shop()
    _cid_b, client_b = _new_shop()
    barcode = f'ten2-{uuid.uuid4().hex[:8]}'
    _create_product(client_a, barcode=barcode)
    r_lookup = client_b.get(f'{API}/products/lookup?code={barcode.lower()}')
    assert r_lookup.status_code == 404, r_lookup.get_json()


# ── 5. Import upsert key ─────────────────────────────────────────────────────

def test_import_updates_a_case_duplicate_sku_instead_of_inserting(shop):
    """IMPORT (deliberate behaviour change, recorded in ROADMAP.md's
    2026-08-30 "retail schema v22 CLAIMED" entry). Importing SKU 'abc' when
    'ABC' already exists UPDATES the existing product instead of inserting
    a second one -- pinned by asserting the product COUNT did not increase
    AND the existing row's fields changed, not just that the response
    reported success. See this change's mutation proof #5, which reverts
    import_api.py to a binary `sku=?` and turns this red (both the SELECT
    and the UPDATE, or the upsert selects one row and updates a different
    set)."""
    cid, client = shop
    sku = f'ABC-IMPORT-{uuid.uuid4().hex[:8]}'
    _create_product(client, sku=sku, name='Original name')

    body = _csv_bytes(
        [{'Product Name': 'Updated via import', 'SKU': sku.lower(), 'Selling Price': '42'}],
        ['Product Name', 'SKU', 'Selling Price'],
    )
    r = client.post('/api/import/execute', data={
        'system': 'retail', 'entity': 'products',
        'mapping': '{"name":"Product Name","sku":"SKU","sell_price":"Selling Price"}',
        'file': (io.BytesIO(body), 'p.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    assert r.get_json().get('updated') == 1, r.get_json()

    conn = sch.get_retail_conn()
    rows = conn.execute(
        "SELECT * FROM products WHERE company_id=? AND sku=? COLLATE NOCASE", (cid, sku)
    ).fetchall()
    conn.close()
    assert len(rows) == 1, rows  # count did not increase -- no second row inserted
    assert rows[0]['name'] == 'Updated via import'
    assert rows[0]['sell_price'] == 42.0
