"""
Aura Retail -- schema v21 / "the POS scale fix" AND schema v22 / "the
case-fold lookup" regression coverage (ROADMAP.md's 2026-08-29 "retail
schema v21 CLAIMED for the POS scale fix" entry and 2026-08-30 "retail
schema v22 CLAIMED for the case-fold lookup" entry).

WHAT THIS FILE PROVES, in order:

1. `GET /products/lookup?code=` (api/retail_api.py::lookup_product) resolves
   ONE product by barcode or SKU, and applies the SAME visibility rules
   `list_products` does -- `status='active'` AND `deleted_at_utc IS NULL`.
   Both conditions exist because of real, previously-shipped bugs
   (launch-readiness Phase 6/7), so a NEW query resurrecting either is
   exactly the failure mode this file exists to catch (see the mutation
   proofs this change's own report quotes: dropping either condition, or
   the company scope, must turn a green test red here).

2. `GET /products` (list_products) grew two OPTIONAL parameters, `?q=` and
   `?limit=`, and the load-bearing guarantee is that OMITTING both keeps
   the response byte-for-byte what it was before this change -- the
   frontend still calls this route with no parameters at all and expects
   the full, unbounded array back until its own rewrite (a separate, later
   change) lands.

3. Schema v21 (database/schema.py::_migrate_add_lookup_indexes) added five
   indexes and no table/column changes; it is idempotent and a fresh
   install lands on `database.schema.RETAIL_SCHEMA_VERSION` as that module
   defines it TODAY, never a number frozen into this file (see every other
   migration test in this suite for why -- `retail_category_delete_fk_
   sync_test.py`'s own `_assert_landed_on_head` names the exact failure
   mode a hardcoded version number produces).

4. The indexes are not just present, they are USED -- proven with REAL
   `EXPLAIN QUERY PLAN` output against the real schema, not asserted from
   reading the migration. A query that merely returns the right rows
   proves correctness, not speed; this is the only place that proves the
   speed half of this change actually did something.

   ONE DELIBERATE, DOCUMENTED GAP: this file does NOT assert that
   `recent_sales`' rewritten `date_from`/`date_to` predicate is
   index-backed, because it genuinely is not, and asserting otherwise
   would be exactly the kind of test ENGINEERING.md forbids -- one that
   passes for the wrong reason. Schema v13's `idx_sales_created_at_utc`
   covers `sales.created_at_utc`, a DIFFERENT column from the plain
   `sales.created_at` that route's date filter actually reads; no index
   on `sales.created_at` exists in this claim (see this change's own
   report for the full reasoning). What IS proven here instead, for that
   same route, is the part that genuinely is index-backed: its
   `sale_items` LEFT JOIN uses the new `idx_sale_items_sale_id`.

5. Schema v22 (database/schema.py::_migrate_add_nocase_lookup_indexes) adds
   two NOCASE-collated indexes, idempotent and version-checked the same way
   v21 is above, and the barcode/sku NOCASE rungs of `lookup_product`'s
   four-rung ladder are proven index-backed by NAME in section 4's EXPLAIN
   QUERY PLAN block, not merely "not a scan".

   The BEHAVIOUR half of v22 -- the mixed-case-stored gap actually closing,
   determinism against a legacy case-duplicate, and the three write doors
   (create_product's SKU/barcode checks, update_product's barcode check,
   import_api's CSV upsert key) agreeing with the read side, both the deny
   half AND the allow half -- lives in the sibling file
   retail_product_lookup_nocase_test.py, split out purely to stay under
   this codebase's 800-line file guideline (see that file's own module
   docstring). See this change's own report for why the allow half is not
   optional there: a "deny everything" mutation of any of those checks
   passes every deny test and would silently destroy the ability to add or
   import products at all.

Self-contained bootstrap, matching retail_page_limit_clamp_test.py and
retail_route_capability_matrix_test.py (no shared conftest.py exists here).

Run:
    pytest products/retail/tests/retail_product_lookup_test.py -v
    pytest products/retail/tests/retail_product_lookup_nocase_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_lookup_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

import api.retail_api as retail_api  # noqa: E402
import database.schema as sch  # noqa: E402
from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers (deliberately duplicated per-file convention -- see
#    the module docstrings this file's sibling migration tests cite for why
#    nothing here is shared via a conftest.py) ─────────────────────────────

def _new_shop(role='admin'):
    """A fresh company with one logged-in user. Each test gets its OWN
    company -- lookup's company-scoping is exactly what this file exists to
    pin, so a shared company would make one test's product visible to
    another's "not found" assertion."""
    email = f"lookup-{uuid.uuid4().hex[:10]}@test.local"
    password = "LookupTestPW1"
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
        'name': name or f'Lookup test item {tag}',
        'sku': sku or f'LKP-{tag}',
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


def _delete_product(client, pid):
    r = client.delete(f'{API}/products/{pid}')
    assert r.status_code == 200, r.get_json()


# ── 1. GET /products/lookup ─────────────────────────────────────────────────

def test_lookup_by_barcode_returns_the_product(shop):
    _cid, client = shop
    barcode = f'BC-{uuid.uuid4().hex[:10]}'
    pid, sku, _ = _create_product(client, barcode=barcode, initial_stock=7)
    r = client.get(f'{API}/products/lookup?code={barcode}')
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body['status'] == 'success'
    assert body['data']['id'] == pid
    assert body['data']['sku'] == sku
    assert body['data']['barcode'] == barcode
    # Same row shape a POS tile needs -- total_stock included, matching
    # list_products' own SELECT exactly.
    assert body['data']['total_stock'] == 7


def test_lookup_by_sku_returns_the_product(shop):
    _cid, client = shop
    pid, sku, _ = _create_product(client)
    r = client.get(f'{API}/products/lookup?code={sku}')
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body['data']['id'] == pid
    assert body['data']['sku'] == sku


def test_lookup_prefers_a_barcode_match_over_a_different_products_sku():
    """A code that collides with both a barcode on one product and a
    different product's SKU resolves to the barcode match -- lookup_product's
    own documented precedence, since barcode is the scan path's primary
    key."""
    _cid, client = _new_shop()
    collision = f'COLLIDE-{uuid.uuid4().hex[:8]}'
    barcode_pid, _, _ = _create_product(client, barcode=collision)
    sku_pid, sku_sku, _ = _create_product(client, sku=collision)
    assert sku_sku == collision
    r = client.get(f'{API}/products/lookup?code={collision}')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['id'] == barcode_pid
    assert r.get_json()['data']['id'] != sku_pid


def test_lookup_requires_a_code_parameter(shop):
    _cid, client = shop
    r = client.get(f'{API}/products/lookup')
    assert r.status_code == 400, r.get_json()


def test_lookup_returns_404_when_nothing_matches(shop):
    _cid, client = shop
    r = client.get(f'{API}/products/lookup?code=no-such-code-{uuid.uuid4().hex}')
    assert r.status_code == 404, r.get_json()


def test_lookup_excludes_a_deleted_product(shop):
    """The Phase 6 stage 6b-iii-a tombstone rule (`deleted_at_utc IS NULL`).
    A deleted product must never resolve, at the till or anywhere else --
    see this change's mutation proof #1, which drops this exact condition
    and turns this test red."""
    _cid, client = shop
    barcode = f'DEL-{uuid.uuid4().hex[:10]}'
    pid, sku, _ = _create_product(client, barcode=barcode)
    _delete_product(client, pid)
    r_bc = client.get(f'{API}/products/lookup?code={barcode}')
    assert r_bc.status_code == 404, r_bc.get_json()
    r_sku = client.get(f'{API}/products/lookup?code={sku}')
    assert r_sku.status_code == 404, r_sku.get_json()


def test_lookup_excludes_an_inactive_product(shop):
    """The `status='active'` half of list_products' own visibility filter,
    exercised independently of deleted_at_utc (PATCH status=inactive never
    touches the tombstone column -- see delete_product's own comment for
    why the two are separate axes since Phase 6 stage 6b-iii-a)."""
    _cid, client = shop
    barcode = f'INA-{uuid.uuid4().hex[:10]}'
    pid, sku, _ = _create_product(client, barcode=barcode, status='inactive')
    r_bc = client.get(f'{API}/products/lookup?code={barcode}')
    assert r_bc.status_code == 404, r_bc.get_json()
    r_sku = client.get(f'{API}/products/lookup?code={sku}')
    assert r_sku.status_code == 404, r_sku.get_json()


def test_lookup_is_company_scoped():
    """Another company's barcode returns nothing -- see this change's
    mutation proof #2, which drops company scoping and turns this red."""
    _cid_a, client_a = _new_shop()
    _cid_b, client_b = _new_shop()
    barcode = f'XCO-{uuid.uuid4().hex[:10]}'
    _create_product(client_a, barcode=barcode)
    r = client_b.get(f'{API}/products/lookup?code={barcode}')
    assert r.status_code == 404, r.get_json()


# ── 2. GET /products -- ?q=, ?limit=, and backward compatibility ───────────

def test_list_products_q_filters_by_name_sku_or_barcode(shop):
    _cid, client = shop
    needle = uuid.uuid4().hex[:10]
    match_pid, match_sku, _ = _create_product(client, name=f'Findable {needle} widget')
    _create_product(client, name='Unrelated product')
    r = client.get(f'{API}/products?q={needle}')
    assert r.status_code == 200, r.get_json()
    rows = r.get_json()['data']
    ids = {row['id'] for row in rows}
    assert match_pid in ids
    assert len(rows) == 1, rows


def test_list_products_limit_clamps_to_its_maximum(shop, monkeypatch):
    """Proven with a REAL, controlled ceiling rather than by creating
    thousands of rows: PRODUCTS_LIST_MAX_LIMIT is patched down to a small
    number, more rows than that are seeded, and the response must never
    exceed the ceiling regardless of what ?limit= asks for. The route reads
    the module global at call time (clamp_page_limit(raw_limit,
    PRODUCTS_LIST_DEFAULT_LIMIT, PRODUCTS_LIST_MAX_LIMIT)), so patching the
    module attribute exercises the exact same code path a real oversized
    ceiling would."""
    _cid, client = shop
    monkeypatch.setattr(retail_api, 'PRODUCTS_LIST_MAX_LIMIT', 3)
    for _ in range(6):
        _create_product(client)
    r = client.get(f'{API}/products?limit=999999')
    assert r.status_code == 200, r.get_json()
    rows = r.get_json()['data']
    assert len(rows) == 3, f'expected the clamped ceiling (3), got {len(rows)}'


def test_list_products_with_no_parameters_stays_byte_compatible(shop, monkeypatch):
    """THE backwards-compatibility guarantee. PRODUCTS_LIST_MAX_LIMIT is
    patched down to a ceiling smaller than the seeded row count -- if
    omitting ?limit= silently applied that ceiling the way an oversized
    explicit ?limit= does, this would fail exactly the way test_list_
    products_limit_clamps_to_its_maximum above proves the clamp DOES apply
    when the parameter is present. The only thing that may differ between
    the two tests is whether `limit=` appears in the query string at all."""
    _cid, client = shop
    monkeypatch.setattr(retail_api, 'PRODUCTS_LIST_MAX_LIMIT', 3)
    created = [_create_product(client)[0] for _ in range(6)]
    r = client.get(f'{API}/products')
    assert r.status_code == 200, r.get_json()
    rows = r.get_json()['data']
    ids = {row['id'] for row in rows}
    assert len(rows) == 6, (
        f'GET /products with no parameters returned {len(rows)} rows for 6 '
        f'seeded products -- the no-parameter path must never apply a LIMIT '
        f'at all, or the frontend (which still calls this route bare until '
        f'its own rewrite lands) silently loses products from the POS grid.'
    )
    assert ids == set(created)


# ── 3. Schema v21 migration ─────────────────────────────────────────────────

def test_v21_migration_lands_on_head_and_is_idempotent():
    """'Landed on head' means PRAGMA user_version reached
    `database.schema.RETAIL_SCHEMA_VERSION` as the module defines it TODAY --
    read from the live module, never frozen into this file as a literal 21
    (see retail_category_delete_fk_sync_test.py's own `_assert_landed_on_
    head` for why a hardcoded number is exactly the anti-pattern this
    avoids)."""
    conn = sch.get_retail_conn()
    assert sch.RETAIL_SCHEMA_VERSION >= 21, (
        f'RETAIL_SCHEMA_VERSION went BACKWARDS to {sch.RETAIL_SCHEMA_VERSION}: '
        f'the v21 lookup-indexes step has been lost from the chain')
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == sch.RETAIL_SCHEMA_VERSION, (
        f'expected a fresh install to land on the current schema head '
        f'({sch.RETAIL_SCHEMA_VERSION}), got {version}')

    def _index_names(table):
        return {row[1] for row in conn.execute(f'PRAGMA index_list("{table}")').fetchall()}

    before = {
        'products': _index_names('products'),
        'sale_items': _index_names('sale_items'),
        'sales': _index_names('sales'),
    }
    # Re-run directly, twice, against the SAME already-migrated connection --
    # the normal case after any interrupted migration, since
    # ensure_schema_version leaves user_version un-advanced on failure.
    sch._migrate_add_lookup_indexes(conn)
    sch._migrate_add_lookup_indexes(conn)
    after = {
        'products': _index_names('products'),
        'sale_items': _index_names('sale_items'),
        'sales': _index_names('sales'),
    }
    assert before == after, 'a retried migration changed the index set -- not idempotent'
    for expected in ('idx_products_company_barcode', 'idx_products_company_sku'):
        assert expected in after['products'], after['products']
    assert 'idx_sale_items_sale_id' in after['sale_items']
    assert 'idx_sales_customer_id' in after['sales']
    conn.close()


def test_v21_migration_guards_the_payments_party_index_on_column_existence():
    """payments(party_type, party_id) is the one index of the five that is
    NOT created unconditionally -- those two columns are not part of
    payments' base CREATE TABLE, only added lazily by _ensure_credit_schema
    at request time (see _migrate_add_lookup_indexes' own docstring). This
    pins that the guard does not silently swallow a REAL column-mismatch
    error for the other four, which are always safe to create."""
    conn = sch.get_retail_conn()
    cols = {row[1] for row in conn.execute('PRAGMA table_info(payments)').fetchall()}
    idx = {row[1] for row in conn.execute('PRAGMA index_list("payments")').fetchall()}
    if 'party_type' in cols and 'party_id' in cols:
        assert 'idx_payments_party' in idx
    else:
        assert 'idx_payments_party' not in idx
    conn.close()


# ── 4. EXPLAIN QUERY PLAN -- proving the indexes are actually used ─────────
#
# Real SQLite EXPLAIN QUERY PLAN text: `SEARCH <table> USING (COVERING )?
# INDEX <name> ...` means indexed; `SCAN <table>` (no USING) means a full
# table scan. Same convention the KMP mobile suite's own EXPLAIN QUERY PLAN
# tests already use (mobile/aura-retail-unified/.../ProductInventoryQueryPlanTest.kt,
# ReportingQueryPlanTest.kt) -- matched here rather than invented fresh.

def _plan_lines(conn, sql, params):
    return [row[3] for row in conn.execute('EXPLAIN QUERY PLAN ' + sql, params).fetchall()]


# The exact SELECT lookup_product() issues (api/retail_api.py) -- {col} is
# either 'barcode' or 'sku', matching that route's own select.format(col=...).
_LOOKUP_SELECT = (
    "SELECT p.*, c.name as category_name, "
    "COALESCE(SUM(b.quantity_on_hand), 0) as total_stock "
    "FROM products p "
    "LEFT JOIN categories c ON p.category_id=c.id AND c.deleted_at_utc IS NULL "
    "LEFT JOIN inventory_balances b ON p.id=b.product_id AND b.company_id=p.company_id "
    "WHERE p.company_id=? AND p.status='active' AND p.deleted_at_utc IS NULL AND p.{col}=? "
    "GROUP BY p.id"
)


def test_explain_query_plan_lookup_by_barcode_uses_the_new_index():
    conn = sch.get_retail_conn()
    lines = _plan_lines(conn, _LOOKUP_SELECT.format(col='barcode'), (1, 'ABC'))
    conn.close()
    products_line = next(l for l in lines if l.split()[1] == 'p')
    assert 'USING INDEX idx_products_company_barcode' in products_line, lines
    assert not products_line.startswith('SCAN'), lines


def test_explain_query_plan_lookup_by_sku_uses_the_new_index():
    conn = sch.get_retail_conn()
    lines = _plan_lines(conn, _LOOKUP_SELECT.format(col='sku'), (1, 'ABC'))
    conn.close()
    products_line = next(l for l in lines if l.split()[1] == 'p')
    assert 'USING INDEX idx_products_company_sku' in products_line, lines
    assert not products_line.startswith('SCAN'), lines


# The exact SELECT lookup_product() issues for the two NOCASE rungs (schema
# v22) -- {col} is either 'barcode' or 'sku', same shape as _LOOKUP_SELECT
# above with ` COLLATE NOCASE` appended, matching that route's own
# select.format(col=..., collate=' COLLATE NOCASE').
_LOOKUP_SELECT_NOCASE = (
    "SELECT p.*, c.name as category_name, "
    "COALESCE(SUM(b.quantity_on_hand), 0) as total_stock "
    "FROM products p "
    "LEFT JOIN categories c ON p.category_id=c.id AND c.deleted_at_utc IS NULL "
    "LEFT JOIN inventory_balances b ON p.id=b.product_id AND b.company_id=p.company_id "
    "WHERE p.company_id=? AND p.status='active' AND p.deleted_at_utc IS NULL AND p.{col}=? COLLATE NOCASE "
    "GROUP BY p.id"
)


def test_explain_query_plan_lookup_by_barcode_nocase_uses_the_nocase_index():
    """PLAN PROOF (schema v22), the barcode NOCASE rung. Asserts on the
    INDEX NAME, not merely 'not SCAN' -- 'not SCAN' alone would also pass if
    this fell back to the v21 PLAIN index, which cannot serve a COLLATE
    NOCASE comparison (SQLite will not use an index whose collation differs
    from the comparison's), so the name is the only thing that actually
    proves this rung is backed by the RIGHT index. See this change's
    mutation proof #6, which drops idx_products_company_sku_nocase from the
    migration and turns the sku half of this pair red with a plan showing
    SCAN or a different index."""
    conn = sch.get_retail_conn()
    lines = _plan_lines(conn, _LOOKUP_SELECT_NOCASE.format(col='barcode'), (1, 'ABC'))
    conn.close()
    products_line = next(l for l in lines if l.split()[1] == 'p')
    assert 'idx_products_company_barcode_nocase' in products_line, lines
    assert not products_line.startswith('SCAN'), lines


def test_explain_query_plan_lookup_by_sku_nocase_uses_the_nocase_index():
    """Same proof as the barcode NOCASE rung above, for sku. See this
    change's mutation proof #6."""
    conn = sch.get_retail_conn()
    lines = _plan_lines(conn, _LOOKUP_SELECT_NOCASE.format(col='sku'), (1, 'ABC'))
    conn.close()
    products_line = next(l for l in lines if l.split()[1] == 'p')
    assert 'idx_products_company_sku_nocase' in products_line, lines
    assert not products_line.startswith('SCAN'), lines


# The exact SELECT recent_sales() issues (api/retail_api.py), rewritten date
# predicate included -- matched to that route's own SQL shape.
_RECENT_SALES_SELECT = (
    "SELECT s.*, COALESCE(c.name,'Walk-in') as customer_name, COUNT(si.id) as item_count "
    "FROM sales s "
    "LEFT JOIN customers c ON s.customer_id=c.id "
    "LEFT JOIN sale_items si ON s.id=si.sale_id "
    "WHERE s.company_id=? AND s.created_at >= date(?) AND s.created_at < date(?, '+1 day') "
    "GROUP BY s.id ORDER BY s.created_at DESC LIMIT ?"
)


def test_explain_query_plan_recent_sales_sale_items_join_uses_its_new_index():
    """The genuinely index-backed half of recent_sales' query, proven
    directly: the sale_items LEFT JOIN uses idx_sale_items_sale_id (schema
    v21). See this change's mutation proof #3, which removes the v21
    indexes and turns this red."""
    conn = sch.get_retail_conn()
    lines = _plan_lines(conn, _RECENT_SALES_SELECT, (1, '2026-01-01', '2026-01-31', 50))
    conn.close()
    si_line = next(l for l in lines if l.split()[1] == 'si')
    assert 'USING COVERING INDEX idx_sale_items_sale_id' in si_line, lines


def test_explain_query_plan_recent_sales_date_predicate_is_sargable_but_not_index_backed():
    """HONEST, not aspirational: this predicate is now sargable (the column
    is bare -- `s.created_at >= date(?)`, not `date(s.created_at) >= ?`),
    but no index on `sales.created_at` exists in this claim (schema v13's
    idx_sales_created_at_utc covers the DIFFERENT `created_at_utc` column),
    so the `sales` table itself is still scanned. Asserting "no SCAN" here
    would be false and exactly the shape of test ENGINEERING.md forbids --
    one that passes for the wrong reason. Recorded plainly rather than
    hidden behind a weaker assertion; see this change's own report."""
    conn = sch.get_retail_conn()
    lines = _plan_lines(conn, _RECENT_SALES_SELECT, (1, '2026-01-01', '2026-01-31', 50))
    conn.close()
    sales_line = next(l for l in lines if l.split()[1] == 's')
    assert sales_line == 'SCAN s', (
        f'sales table plan changed to {sales_line!r} -- if this now names an '
        f'index, update the report/docstring above: sargability alone was '
        f'not expected to change this without a matching index.'
    )


# ── 5. Schema v22 -- the case-fold lookup ───────────────────────────────────

def test_v22_migration_lands_on_head_and_is_idempotent():
    """Same shape as test_v21_migration_lands_on_head_and_is_idempotent
    above, for the two NOCASE indexes. RETAIL_SCHEMA_VERSION is read from
    the live module, never frozen into this file as a literal 22."""
    conn = sch.get_retail_conn()
    assert sch.RETAIL_SCHEMA_VERSION >= 22, (
        f'RETAIL_SCHEMA_VERSION went BACKWARDS to {sch.RETAIL_SCHEMA_VERSION}: '
        f'the v22 nocase-lookup-indexes step has been lost from the chain')
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == sch.RETAIL_SCHEMA_VERSION, (
        f'expected a fresh install to land on the current schema head '
        f'({sch.RETAIL_SCHEMA_VERSION}), got {version}')

    def _index_names():
        return {row[1] for row in conn.execute('PRAGMA index_list("products")').fetchall()}

    before = _index_names()
    # Re-run directly, twice, against the SAME already-migrated connection --
    # the normal case after any interrupted migration, since
    # ensure_schema_version leaves user_version un-advanced on failure.
    sch._migrate_add_nocase_lookup_indexes(conn)
    sch._migrate_add_nocase_lookup_indexes(conn)
    after = _index_names()
    assert before == after, 'a retried migration changed the index set -- not idempotent'
    for expected in ('idx_products_company_barcode_nocase', 'idx_products_company_sku_nocase'):
        assert expected in after, after
    conn.close()


# The case-fold BEHAVIOUR coverage (the gap closed, determinism against a
# legacy case-duplicate, the three write-side dup-check doors -- deny half
# AND allow half, blank exemption, tenancy, and the import upsert-key
# change) lives in the sibling file retail_product_lookup_nocase_test.py,
# split out purely to stay under this codebase's 800-line file guideline --
# see that file's own module docstring.
