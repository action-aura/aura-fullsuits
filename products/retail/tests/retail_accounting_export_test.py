"""
Aura Retail -- launch-readiness "the till scans mixed-case codes" +
"no journal export, no CSV dump" fixes.

TWO INDEPENDENT CHANGES, one file, because both touch the same route file
(api/retail_api.py) in the same sitting and both need EXPLAIN QUERY PLAN /
real-app fixtures this suite already has a convention for.

1. `GET /products/lookup?code=` (schema v21, dfc0ef0) matched `barcode`/
   `sku` with a bare `=`. Android's existing `findProductByCode`
   (barcode/ProductLookup.kt) matches case-INsensitively
   (`equals(code, ignoreCase = true)`) and is about to be pointed at this
   endpoint -- an exact-match server would silently regress scanning for
   any shop whose SKUs are mixed-case. Fixed by matching the input against
   a small, FIXED set of its own case variants (`code`, `code.upper()`,
   `code.lower()`) with `IN (...)`, which SQLite still plans as index
   seeks -- proven below with real `EXPLAIN QUERY PLAN` output, the same
   convention retail_product_lookup_test.py already uses for this route's
   exact-match half.

   HONEST LIMIT, matched to what is actually tested here: this resolves a
   lowercase code against an uppercase-stored value and vice versa, not
   arbitrary mixed-case-against-mixed-case (stored 'AbC-1' found by
   searching 'aBc-1') -- see lookup_product's own docstring for why that
   would need either an unbounded set of permutations or a schema change
   (a COLLATE NOCASE index) nobody authorized here.

2. Company-scoped, date-ranged, streamed CSV exports for sales (with their
   line items), payments, and cash-session Z-reports -- a finance
   department's own ERP/auditor import, not a report screen. Date bounds
   are the SAME half-open, sargable shape `recent_sales` uses (`col >=
   date(?) AND col < date(?, '+1 day')`, schema v21), gated on CAP_REPORTS
   like every other report route, and every money cell is formatted
   through core/retail/pricing.py's `_money` (imported here as
   `tax_engine._money`) -- the ledger's own rounding rule, not a
   re-derived one.

Self-contained bootstrap, matching retail_product_lookup_test.py and
retail_route_capability_matrix_test.py (no shared conftest.py exists here).

Run:
    pytest products/retail/tests/retail_accounting_export_test.py -v
"""
import csv
import io
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_acctexport_"))
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
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers (deliberately duplicated per-file convention -- see
#    retail_product_lookup_test.py's own module docstring for why nothing
#    here is shared via a conftest.py) ───────────────────────────────────

def _new_shop(role='admin'):
    """A fresh company, one branch, one logged-in user. Each test gets its
    OWN company -- company scoping is exactly what several tests below
    exist to pin, so a shared company would make one test's row visible to
    another's "never appears" assertion."""
    email = f"acx-{uuid.uuid4().hex[:10]}@test.local"
    password = "AcctExportPW1"
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

    rconn = get_retail_conn()
    cur = rconn.cursor()
    cur.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = cur.lastrowid
    rconn.commit()
    rconn.close()

    return {'company_id': company_id, 'branch_id': branch_id, 'user_id': user_id, 'client': client}


@pytest.fixture
def shop():
    return _new_shop()


def _make_cashier(company_id):
    """A cashier in an EXISTING company. retail.reports is a manager-and-
    above capability (user_accounts.ROLE_CAPABILITIES), so this session has
    everything the export routes' OTHER decorators need (login, the retail
    subsystem grant) but not CAP_REPORTS -- built the same way
    retail_route_capability_matrix_test.py's own `_make_user` helper does.
    role='admin' would bypass `mt_require_capability` entirely (see that
    decorator's own 'ADMIN BYPASS' comment in commercial_runtime/identity/
    mt_auth.py) and could not exercise a denial at all."""
    email = f"acx-cashier-{uuid.uuid4().hex[:10]}@test.local"
    password = "AcctExportPW1"
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), "cashier", "active"),
    )
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    user_accounts.seed_capabilities_for_user(conn, user_id, "cashier")
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client


def _create_product(client, name=None, sku=None, barcode=None):
    tag = uuid.uuid4().hex[:8]
    payload = {
        'name': name or f'Export test item {tag}', 'sku': sku or f'ACX-{tag}',
        'sell_price': 10.0, 'cost_price': 5.0, 'tax_rate': 0, 'initial_stock': 0,
    }
    if barcode is not None:
        payload['barcode'] = barcode
    r = client.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    return data['id'], payload['sku'], payload.get('barcode')


def _seed_sale(shop, product_id, created_at, amount=10.0, sale_number=None):
    """One completed sale plus its one line item, directly inserted so
    `created_at` can be pinned to an exact value for boundary/company/
    money assertions -- matching retail_report_clock_agreement_test.py's
    own `_sale_at_shop_local`, minus that file's shop-clock/timezone
    machinery, which this file has no need of. subtotal/tax/discount are
    kept at a trivial 0/0/amount/amount so every money column on the row
    resolves to the SAME figure, which is exactly what the money-fidelity
    tests below want to assert against."""
    conn = get_retail_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sales (company_id,sale_number,branch_id,customer_id,cashier,subtotal,"
        "discount_amount,tax_amount,total,amount_paid,payment_method,status,created_at,"
        "actor_user_uid,created_at_utc,uid) "
        "VALUES (?,?,?,NULL,'POS',?,0,0,?,?,'cash','completed',?,NULL,?,?)",
        (shop['company_id'], sale_number or f'ACX-{uuid.uuid4().hex[:12]}', shop['branch_id'],
         amount, amount, amount, created_at, created_at, str(uuid.uuid4())),
    )
    sale_id = cur.lastrowid
    cur.execute(
        "INSERT INTO sale_items (sale_id,product_id,quantity,unit_price,discount_pct,tax_rate,line_total) "
        "VALUES (?,?,1,?,0,0,?)",
        (sale_id, product_id, amount, amount),
    )
    conn.commit()
    conn.close()
    return sale_id


def _seed_payment(shop, created_at, amount=10.0, method='cash', direction='in', reference=None):
    """Direct insert into `payments`, same reasoning as `_seed_sale`.
    `direction`/`party_type`/`party_id` already exist by the time this runs
    -- `_new_shop`'s own `GET /settings/tax` call forces `_ensure_credit_
    schema` first, matching every other route in api/retail_api.py that
    reads those lazily-added columns."""
    conn = get_retail_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO payments (company_id, sale_id, method, amount, reference, status, "
        "created_at, direction) VALUES (?,NULL,?,?,?,?,?,?)",
        (shop['company_id'], method, amount, reference or f'ACX-PAY-{uuid.uuid4().hex[:10]}',
         'success', created_at, direction),
    )
    payment_id = cur.lastrowid
    conn.commit()
    conn.close()
    return payment_id


def _seed_cash_session(shop, closed_at, opened_at=None, opening_float=100.0,
                        counted=100.0, expected=100.0, variance=0.0, status='closed'):
    """Direct insert into `cash_sessions`, closed at an exact, pinned
    timestamp -- a Z-report is meaningful only once a drawer has actually
    closed, so every session this helper creates is born already closed."""
    conn = get_retail_conn()
    session_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO cash_sessions (id, company_id, branch_id, opened_by, opened_at, opening_float, "
        "closed_by, closed_at, closing_float_counted, closing_float_expected, variance, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (session_id, shop['company_id'], shop['branch_id'], shop['user_id'], opened_at or closed_at,
         opening_float, shop['user_id'], closed_at, counted, expected, variance, status),
    )
    conn.commit()
    conn.close()
    return session_id


def _fetch_csv(client, path):
    r = client.get(f'{API}{path}')
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.content_type.startswith('text/csv'), r.content_type
    assert 'attachment' in r.headers.get('Content-Disposition', '')
    rows = list(csv.reader(io.StringIO(r.get_data(as_text=True))))
    assert rows, 'CSV response was completely empty -- not even a header row'
    return rows[0], rows[1:]


EXPORT_PATHS = (
    '/reports/export/sales',
    '/reports/export/payments',
    '/reports/export/cash-sessions',
)


# ── 1. GET /products/lookup -- case-insensitive on both columns ───────────

def test_lookup_resolves_a_lowercase_code_for_an_uppercase_barcode(shop):
    client = shop['client']
    barcode = f'UP-{uuid.uuid4().hex[:10].upper()}'
    pid, _sku, _bc = _create_product(client, barcode=barcode)
    r = client.get(f'{API}/products/lookup?code={barcode.lower()}')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['id'] == pid


def test_lookup_resolves_an_uppercase_code_for_a_lowercase_barcode(shop):
    client = shop['client']
    barcode = f'lo-{uuid.uuid4().hex[:10].lower()}'
    pid, _sku, _bc = _create_product(client, barcode=barcode)
    r = client.get(f'{API}/products/lookup?code={barcode.upper()}')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['id'] == pid


def test_lookup_case_insensitivity_also_covers_sku(shop):
    """The SKU branch gets the identical treatment as barcode -- both
    columns, not just the scan path's primary key. Written in ONE
    consistent case (all-upper), the documented, tested shape -- not an
    arbitrary mixed-case SKU like 'MiXeD-1a2b3c4d', which the 3-variant
    (code/upper/lower) match deliberately does NOT cover; see lookup_
    product's own 'HONEST LIMIT' docstring paragraph."""
    client = shop['client']
    sku = f'CASESKU{uuid.uuid4().hex[:8]}'.upper()
    r = client.post(f'{API}/products', json={
        'name': 'Case SKU item', 'sku': sku,
        'sell_price': 10.0, 'tax_rate': 0, 'initial_stock': 0,
    })
    assert r.status_code == 200, r.get_json()
    pid = r.get_json()['data']['id']
    r2 = client.get(f'{API}/products/lookup?code={sku.lower()}')
    assert r2.status_code == 200, r2.get_json()
    assert r2.get_json()['data']['id'] == pid


# ── 2. EXPLAIN QUERY PLAN -- the case-insensitive lookup stays index-backed ─
#
# The exact SELECT lookup_product() issues for a THREE-variant code (the
# realistic mixed-case shape: `code`, `code.upper()`, `code.lower()` all
# differ) -- {col} is either 'barcode' or 'sku', matching that route's own
# select.format(col=...). No fixture/shop needed: EXPLAIN QUERY PLAN only
# needs the schema and its indexes, not any rows, exactly like retail_
# product_lookup_test.py's own exact-match EXPLAIN tests.

_CASE_INSENSITIVE_LOOKUP_SELECT = (
    "SELECT p.*, c.name as category_name, "
    "COALESCE(SUM(b.quantity_on_hand), 0) as total_stock "
    "FROM products p "
    "LEFT JOIN categories c ON p.category_id=c.id AND c.deleted_at_utc IS NULL "
    "LEFT JOIN inventory_balances b ON p.id=b.product_id AND b.company_id=p.company_id "
    "WHERE p.company_id=? AND p.status='active' AND p.deleted_at_utc IS NULL "
    "AND p.{col} IN (?,?,?) "
    "GROUP BY p.id"
)


def _plan_lines(conn, sql, params):
    return [row[3] for row in conn.execute('EXPLAIN QUERY PLAN ' + sql, params).fetchall()]


def test_explain_query_plan_case_insensitive_barcode_lookup_still_uses_the_index():
    conn = sch.get_retail_conn()
    lines = _plan_lines(
        conn, _CASE_INSENSITIVE_LOOKUP_SELECT.format(col='barcode'), (1, 'Abc123', 'ABC123', 'abc123'),
    )
    conn.close()
    products_line = next(l for l in lines if l.split()[1] == 'p')
    assert 'USING INDEX idx_products_company_barcode' in products_line, lines
    assert not products_line.startswith('SCAN'), lines


def test_explain_query_plan_case_insensitive_sku_lookup_still_uses_the_index():
    conn = sch.get_retail_conn()
    lines = _plan_lines(
        conn, _CASE_INSENSITIVE_LOOKUP_SELECT.format(col='sku'), (1, 'Abc123', 'ABC123', 'abc123'),
    )
    conn.close()
    products_line = next(l for l in lines if l.split()[1] == 'p')
    assert 'USING INDEX idx_products_company_sku' in products_line, lines
    assert not products_line.startswith('SCAN'), lines


# ── 3. Well-formed CSV, header row included ────────────────────────────────

def test_export_sales_csv_is_well_formed_with_a_header_row():
    shop = _new_shop()
    pid, _sku, _bc = _create_product(shop['client'])
    _seed_sale(shop, pid, '2026-07-01 10:00:00', amount=15.0, sale_number='ACX-WELLFORMED')
    header, rows = _fetch_csv(shop['client'], '/reports/export/sales?date_from=2026-07-01&date_to=2026-07-01')
    assert header == [
        'sale_id', 'sale_number', 'created_at', 'branch_id', 'customer_id', 'customer_name',
        'cashier', 'payment_method', 'sale_status', 'product_id', 'product_name', 'sku',
        'quantity', 'unit_price', 'discount_pct', 'tax_rate', 'line_total',
        'sale_subtotal', 'sale_discount_amount', 'sale_tax_amount', 'sale_total', 'sale_amount_paid',
    ]
    assert len(rows) == 1, rows
    assert len(rows[0]) == len(header)


def test_export_payments_csv_is_well_formed_with_a_header_row():
    shop = _new_shop()
    _seed_payment(shop, '2026-07-02 10:00:00', amount=25.0, reference='ACX-PAY-WELLFORMED')
    header, rows = _fetch_csv(shop['client'], '/reports/export/payments?date_from=2026-07-02&date_to=2026-07-02')
    assert header == [
        'payment_id', 'created_at', 'direction', 'method', 'amount', 'currency',
        'party_type', 'party_id', 'sale_id', 'related_type', 'related_id',
        'reference', 'status', 'notes',
    ]
    assert len(rows) == 1, rows
    assert len(rows[0]) == len(header)


def test_export_cash_sessions_csv_is_well_formed_with_a_header_row():
    shop = _new_shop()
    _seed_cash_session(shop, closed_at='2026-07-03 18:00:00')
    header, rows = _fetch_csv(
        shop['client'], '/reports/export/cash-sessions?date_from=2026-07-03&date_to=2026-07-03',
    )
    assert header == [
        'session_id', 'branch_id', 'opened_by', 'opened_at', 'closed_by', 'closed_at',
        'opening_float', 'closing_float_counted', 'closing_float_expected', 'variance', 'status',
    ]
    assert len(rows) == 1, rows
    assert len(rows[0]) == len(header)


def test_export_csv_with_nothing_in_range_is_still_a_header_only_csv():
    """An empty result is a normal, well-formed export -- a finance
    department pulling a quiet month should get a valid file with just a
    header, not an error."""
    shop = _new_shop()
    for path in EXPORT_PATHS:
        header, rows = _fetch_csv(shop['client'], f'{path}?date_from=2019-01-01&date_to=2019-01-01')
        assert header, path
        assert rows == [], (path, rows)


# ── 4. Company scoping -- another company's rows never appear ─────────────

def test_export_sales_csv_is_company_scoped():
    shop_a, shop_b = _new_shop(), _new_shop()
    pid_a, _s, _b = _create_product(shop_a['client'])
    pid_b, _s, _b = _create_product(shop_b['client'])
    _seed_sale(shop_a, pid_a, '2026-06-01 10:00:00', amount=20.0, sale_number='ACX-COMPANY-A')
    _seed_sale(shop_b, pid_b, '2026-06-01 10:00:00', amount=30.0, sale_number='ACX-COMPANY-B')
    header, rows = _fetch_csv(shop_b['client'], '/reports/export/sales?date_from=2026-06-01&date_to=2026-06-01')
    numbers = {row[header.index('sale_number')] for row in rows}
    assert 'ACX-COMPANY-B' in numbers
    assert 'ACX-COMPANY-A' not in numbers


def test_export_payments_csv_is_company_scoped():
    shop_a, shop_b = _new_shop(), _new_shop()
    _seed_payment(shop_a, '2026-06-02 10:00:00', amount=40.0, reference='ACX-PAY-COMPANY-A')
    _seed_payment(shop_b, '2026-06-02 10:00:00', amount=50.0, reference='ACX-PAY-COMPANY-B')
    header, rows = _fetch_csv(shop_b['client'], '/reports/export/payments?date_from=2026-06-02&date_to=2026-06-02')
    refs = {row[header.index('reference')] for row in rows}
    assert 'ACX-PAY-COMPANY-B' in refs
    assert 'ACX-PAY-COMPANY-A' not in refs


def test_export_cash_sessions_csv_is_company_scoped():
    shop_a, shop_b = _new_shop(), _new_shop()
    sid_a = _seed_cash_session(shop_a, closed_at='2026-06-03 18:00:00')
    sid_b = _seed_cash_session(shop_b, closed_at='2026-06-03 18:00:00')
    header, rows = _fetch_csv(
        shop_b['client'], '/reports/export/cash-sessions?date_from=2026-06-03&date_to=2026-06-03',
    )
    ids = {row[header.index('session_id')] for row in rows}
    assert sid_b in ids
    assert sid_a not in ids


# ── 5. Date range boundary -- half-open, both ends ─────────────────────────

def test_export_sales_csv_date_range_boundary_is_half_open():
    shop = _new_shop()
    pid, _s, _b = _create_product(shop['client'])
    _seed_sale(shop, pid, '2026-03-10 08:00:00', sale_number='ACX-IN-START')
    _seed_sale(shop, pid, '2026-03-12 23:59:00', sale_number='ACX-IN-END')
    _seed_sale(shop, pid, '2026-03-13 00:00:01', sale_number='ACX-OUT-AFTER')
    _seed_sale(shop, pid, '2026-03-09 23:59:59', sale_number='ACX-OUT-BEFORE')
    header, rows = _fetch_csv(shop['client'], '/reports/export/sales?date_from=2026-03-10&date_to=2026-03-12')
    numbers = {row[header.index('sale_number')] for row in rows}
    assert numbers == {'ACX-IN-START', 'ACX-IN-END'}, numbers


def test_export_payments_csv_date_range_boundary_is_half_open():
    shop = _new_shop()
    _seed_payment(shop, '2026-05-10 08:00:00', reference='ACX-PAY-IN-START')
    _seed_payment(shop, '2026-05-12 23:59:00', reference='ACX-PAY-IN-END')
    _seed_payment(shop, '2026-05-13 00:00:01', reference='ACX-PAY-OUT-AFTER')
    _seed_payment(shop, '2026-05-09 23:59:59', reference='ACX-PAY-OUT-BEFORE')
    header, rows = _fetch_csv(shop['client'], '/reports/export/payments?date_from=2026-05-10&date_to=2026-05-12')
    refs = {row[header.index('reference')] for row in rows}
    assert refs == {'ACX-PAY-IN-START', 'ACX-PAY-IN-END'}, refs


def test_export_cash_sessions_csv_date_range_boundary_is_half_open():
    shop = _new_shop()
    in_start = _seed_cash_session(shop, closed_at='2026-04-05 08:00:00')
    in_end = _seed_cash_session(shop, closed_at='2026-04-07 23:59:00')
    out_after = _seed_cash_session(shop, closed_at='2026-04-08 00:00:01')
    out_before = _seed_cash_session(shop, closed_at='2026-04-04 23:59:59')
    header, rows = _fetch_csv(
        shop['client'], '/reports/export/cash-sessions?date_from=2026-04-05&date_to=2026-04-07',
    )
    ids = {row[header.index('session_id')] for row in rows}
    assert ids == {in_start, in_end}, (ids, {'in_start': in_start, 'in_end': in_end,
                                              'out_after': out_after, 'out_before': out_before})


# ── 6. Exported money matches the stored figure exactly ───────────────────

def test_export_sales_csv_money_matches_the_stored_figure_exactly():
    shop = _new_shop()
    pid, _s, _b = _create_product(shop['client'])
    sale_id = _seed_sale(shop, pid, '2026-08-01 10:00:00', amount=49.99, sale_number='ACX-MONEY')
    conn = get_retail_conn()
    stored = conn.execute("SELECT total, amount_paid FROM sales WHERE id=?", (sale_id,)).fetchone()
    conn.close()
    header, rows = _fetch_csv(shop['client'], '/reports/export/sales?date_from=2026-08-01&date_to=2026-08-01')
    row = rows[0]
    assert float(row[header.index('sale_total')]) == stored['total']
    assert float(row[header.index('sale_amount_paid')]) == stored['amount_paid']
    assert float(row[header.index('unit_price')]) == 49.99
    assert float(row[header.index('line_total')]) == 49.99


def test_export_payments_csv_money_matches_the_stored_figure_exactly():
    shop = _new_shop()
    payment_id = _seed_payment(shop, '2026-08-02 10:00:00', amount=76.54, reference='ACX-PAY-MONEY')
    conn = get_retail_conn()
    stored = conn.execute("SELECT amount FROM payments WHERE id=?", (payment_id,)).fetchone()
    conn.close()
    header, rows = _fetch_csv(shop['client'], '/reports/export/payments?date_from=2026-08-02&date_to=2026-08-02')
    row = rows[0]
    assert float(row[header.index('amount')]) == stored['amount']


def test_export_cash_sessions_csv_money_matches_the_stored_figure_exactly():
    shop = _new_shop()
    session_id = _seed_cash_session(
        shop, closed_at='2026-08-03 18:00:00',
        opening_float=100.0, counted=142.37, expected=140.0, variance=2.37,
    )
    conn = get_retail_conn()
    stored = conn.execute(
        "SELECT opening_float, closing_float_counted, closing_float_expected, variance "
        "FROM cash_sessions WHERE id=?", (session_id,),
    ).fetchone()
    conn.close()
    header, rows = _fetch_csv(
        shop['client'], '/reports/export/cash-sessions?date_from=2026-08-03&date_to=2026-08-03',
    )
    row = rows[0]
    assert float(row[header.index('opening_float')]) == stored['opening_float']
    assert float(row[header.index('closing_float_counted')]) == stored['closing_float_counted']
    assert float(row[header.index('closing_float_expected')]) == stored['closing_float_expected']
    assert float(row[header.index('variance')]) == stored['variance']


# ── 7. Capability gating -- refused without CAP_REPORTS ────────────────────

@pytest.mark.parametrize('path', EXPORT_PATHS)
def test_export_denied_without_cap_reports(path):
    shop = _new_shop()
    cashier = _make_cashier(shop['company_id'])
    r = cashier.get(f'{API}{path}?date_from=2026-01-01&date_to=2026-01-31')
    assert r.status_code == 403, r.get_data(as_text=True)


# ── 8. Both date bounds are required ────────────────────────────────────────

@pytest.mark.parametrize('path', EXPORT_PATHS)
def test_export_requires_both_date_bounds(path):
    shop = _new_shop()
    r_missing_both = shop['client'].get(f'{API}{path}')
    assert r_missing_both.status_code == 400, r_missing_both.get_json()
    r_missing_to = shop['client'].get(f'{API}{path}?date_from=2026-01-01')
    assert r_missing_to.status_code == 400, r_missing_to.get_json()
    r_missing_from = shop['client'].get(f'{API}{path}?date_to=2026-01-31')
    assert r_missing_from.status_code == 400, r_missing_from.get_json()
