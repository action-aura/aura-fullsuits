"""
Aura Retail -- persisted-money reconciliation, at the two call sites the
pure-unit tests in retail_pricing_test.py cannot reach on their own: the real
create_sale() HTTP route writing a real sqlite row, and database/schema.py's
own demo-data seeder (_seed_retail), which reimplements the aggregation by
hand instead of calling into the shared per-line-then-sum pattern
create_sale()/`_resolve_quotation_lines_for_write` use.

WHY THIS EXISTS, SEPARATELY FROM retail_pricing_test.py's OWN reconciliation
tests: those prove the ENGINE (`core/retail/pricing.py`) is right in
isolation. They do NOT prove the number that engine computes actually reaches
a persisted row unmolested, or that _seed_retail -- which does not call
create_sale at all -- inherited the fix rather than continuing to reimplement
the old, broken aggregation by hand. "A helper returns the right number" and
"the right number reaches the database" are different claims; this file is
the second one, matching retail_currency_precision_test.py's own stated
division of labour between its unit tests and the money suites that boot the
real app.

Self-contained, one process per file (AURA_APP_DATA binds at import) -- no
shared conftest.py exists for products/retail/tests/.

Run alone, from the repo root:
    py -3.14 -m pytest products/retail/tests/retail_money_reconciliation_test.py -q
"""
import os
import shutil
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_money_reconciliation_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database import schema  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    """One company per test, matching every other money-route test file in
    this suite -- see retail_sale_money_precision_test.py's identical
    fixture and its own rationale."""
    email = f'moneyrec-{uuid.uuid4().hex[:8]}@test.local'
    password = 'MoneyRecPW1'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'))
    conn.commit(); conn.close()

    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    c.test_company_id = company_id
    return c


def _seed_product(c, **kw):
    payload = {
        'name': 'Reconciliation Widget', 'sku': f'MONREC-{uuid.uuid4().hex[:8]}',
        'cost_price': 1.0, 'sell_price': 2.5, 'tax_rate': 16, 'initial_stock': 100,
    }
    payload.update(kw)
    r = c.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _sale_row(sale_id):
    conn = get_retail_conn()
    try:
        row = conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    finally:
        conn.close()
    return dict(row)


def _reconciles(row):
    return (Decimal(str(row['subtotal'])) - Decimal(str(row['discount_amount']))
            + Decimal(str(row['tax_amount'])) == Decimal(str(row['total'])))


# ═════════════════════════════════════════════════════════════════════════
# 1. create_sale -- the PERSISTED row (not the JSON echo) must reconcile.
#    A fresh install has no explicit base_currency row, so this is the
#    COMMON case, not an edge one (see _company_currency's own docstring on
#    why an explicit-currency fixture would hide exactly this defect).
# ═════════════════════════════════════════════════════════════════════════

def test_create_sale_persists_reconciling_total_on_a_discounted_basket(client):
    """THE measured live-till reproduction, rung through the real route: 2.50
    JOD x 3, 7.5% discount, 16% tax. Before the fix this persisted
    total=8.048 while subtotal-discount+tax=8.047 -- an ordinary discounted
    sale, not an edge case (3 of 3 discounted baskets failed this way on the
    real till). Asserts the PERSISTED ROW read back from sqlite, not the
    HTTP response echo -- the two can drift independently if a response
    formatter re-rounds on the way out (a real, separately-fixed bug this
    same wave found in create_sale's own payload, see
    retail_sale_money_precision_test.py's module docstring)."""
    pid = _seed_product(client, sell_price=2.5, tax_rate=16)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 3, 'discount_pct': 7.5}],
        'payment_method': 'cash', 'amount_paid': 100.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']

    row = _sale_row(body['id'])
    assert row['total'] == 8.047, f"got {row['total']!r}"
    assert _reconciles(row), (
        f"persisted row does not reconcile: subtotal={row['subtotal']} "
        f"discount_amount={row['discount_amount']} tax_amount={row['tax_amount']} "
        f"total={row['total']}")


def test_create_sale_persists_reconciling_total_when_undiscounted(client):
    """THE allow-half: an undiscounted sale must remain exactly as correct
    as it already was -- the fix must not regress the case that was never
    broken."""
    pid = _seed_product(client, sell_price=100.0, tax_rate=15)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 200.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']

    row = _sale_row(body['id'])
    assert row['total'] == 115.0, f"got {row['total']!r}"
    assert _reconciles(row)


# ═════════════════════════════════════════════════════════════════════════
# 2. database/schema.py::_seed_retail -- the demo-data seeder that reimplements
#    the aggregation by hand instead of calling into the shared pattern.
#    Direct call, not through the gated /demo/seed HTTP route -- this file is
#    about the SEEDER's own arithmetic, not the route's authorization.
# ═════════════════════════════════════════════════════════════════════════

def test_seed_retail_demo_sales_reconcile_and_carry_discount():
    """For every seeded sale whose items include a real discount:
      (a) `discount_amount` is actually written (the pre-fix seeder never
          wrote it at all, leaving every seeded discounted sale's own
          discount_amount at the column default while `subtotal` had
          already had the discount folded out of it under the wrong name);
      (b) the persisted row reconciles (subtotal-discount+tax==total) --
          the seeder used to hand-roll `round(x, 2)` independently instead
          of routing through calculate_line()'s now-reconciling formula;
      (c) at least one seeded sale carries a genuinely 3-decimal figure a
          hardcoded `round(x, 2)` would have visibly truncated -- kills the
          JOD-2dp regression the old seeder's hardcoded round(...,2) calls
          reintroduced.

    Runs _seed_retail with a HIGH random seed count implicitly via its own
    fixed loop of 40 sales (~random.choice among a 5-way discount list
    including two nonzero values) -- over 40 sales the odds of at least one
    nonzero-discount line are overwhelming; if this ever flakes, that is
    itself worth knowing; it has not in repeated local runs."""
    from api.retail_api import _ensure_credit_schema

    conn = get_retail_conn()
    cid = f'seedrec-{uuid.uuid4().hex[:10]}'
    # retail_settings is created LAZILY by the API layer's first request
    # (see retail_pricing_test.py's own `_make_admin_and_client` comment: "a
    # settings/tax GET ensures _ensure_credit_schema() has run") -- this test
    # never makes an HTTP request, so it must create the table itself rather
    # than depend on test execution order within this file.
    _ensure_credit_schema(conn)
    conn.execute(
        "INSERT INTO retail_settings (company_id, skey, svalue) VALUES (?,?,?)",
        (cid, 'base_currency', 'JOD'))
    conn.commit()

    cur = conn.cursor()
    schema._seed_retail(conn, cur, company_id=cid)
    conn.commit()

    rows = conn.execute(
        "SELECT * FROM sales WHERE company_id=?", (cid,)).fetchall()
    conn.close()
    assert len(rows) == 40, len(rows)

    discounted = [dict(r) for r in rows if float(r['discount_amount'] or 0) > 0]
    assert discounted, (
        "no seeded sale carried a discount -- the fixture manufactured the "
        "exact state that would hide this test's own defect, see "
        "ENGINEERING.md's 'fixture manufactures the exact state that hides "
        "the bug' failure shape")

    saw_three_decimal = False
    for row in discounted:
        sub = Decimal(str(row['subtotal']))
        disc = Decimal(str(row['discount_amount']))
        tax = Decimal(str(row['tax_amount']))
        total = Decimal(str(row['total']))
        assert sub - disc + tax == total, (
            f"sale {row['sale_number']} does not reconcile: {sub} - {disc} + {tax} != {total}")
        for amt in (sub, disc, tax, total):
            if -amt.as_tuple().exponent >= 3 and amt.as_tuple().digits[-1] != 0:
                saw_three_decimal = True

    assert saw_three_decimal, (
        "no seeded discounted sale carried a genuine 3rd decimal digit -- "
        "cannot distinguish a currency-aware fix from a hardcoded round(x, 2) "
        "regression on this run (JOD-precision claim (c) unproven)")


def test_seed_retail_reads_the_real_currency_setting_key():
    """THE ALLOW-HALF, and the one bug the review found in the design's own
    proposed fix: an earlier draft of this correction looked up the shop's
    currency under `skey='currency'`, a key `api/retail_api.py` never
    writes (the real key, read everywhere else in this codebase, including
    `_company_currency`'s own SELECT, is `base_currency`). That wrong key
    would silently find nothing and fall back to the JOD default for every
    company -- INDISTINGUISHABLE from a correct implementation for a company
    whose real currency also happens to be JOD (the fixture manufacturing
    the exact state that hides the bug, again). This test sets a NON-
    default currency (USD, 2dp) explicitly, so a fix reading the wrong key
    and silently defaulting to JOD (3dp) would seed a 3-decimal figure for a
    USD shop -- wrong precision for that company's real, configured
    currency."""
    from api.retail_api import _ensure_credit_schema

    conn = get_retail_conn()
    cid = f'seedcur-{uuid.uuid4().hex[:10]}'
    # _seed_retail() always numbers its synthetic receipts from a hardcoded
    # `sale_num = 1000` local counter (it has no `doc_sequences` awareness --
    # real sales use `_next_ref`, this seeder does not), and `sales.
    # sale_number` carries a bare, NOT company-scoped, UNIQUE constraint
    # (schema.py: `sale_number TEXT UNIQUE`). So a second `_seed_retail` call
    # in the same physical database -- as this test file's other seed test
    # already made, sharing this same AURA_APP_DATA sqlite file -- collides
    # on 'S-1001' etc. This is a pre-existing property of the seeder
    # (production only ever calls it once per fresh database) surfacing
    # because THIS FILE calls it twice; clearing the rows the previous call
    # left behind is this test's own problem to solve, not a schema change
    # the money-arithmetic fix should make.
    conn.execute("DELETE FROM return_items")
    conn.execute("DELETE FROM returns")
    conn.execute("DELETE FROM payments")
    conn.execute("DELETE FROM sale_items")
    conn.execute("DELETE FROM sales")
    conn.commit()
    _ensure_credit_schema(conn)
    conn.execute(
        "INSERT INTO retail_settings (company_id, skey, svalue) VALUES (?,?,?)",
        (cid, 'base_currency', 'USD'))
    conn.commit()

    cur = conn.cursor()
    schema._seed_retail(conn, cur, company_id=cid)
    conn.commit()

    rows = conn.execute("SELECT * FROM sales WHERE company_id=?", (cid,)).fetchall()
    conn.close()
    assert len(rows) == 40, len(rows)

    for row in rows:
        for field in ('subtotal', 'discount_amount', 'tax_amount', 'total'):
            amt = Decimal(str(row[field] or 0))
            assert -amt.as_tuple().exponent <= 2, (
                f"sale {row['sale_number']}.{field}={row[field]!r} carries more than "
                f"2 decimal places for a USD (2dp) company -- the seeder read the "
                f"wrong currency-setting key and defaulted to JOD's 3dp instead")
