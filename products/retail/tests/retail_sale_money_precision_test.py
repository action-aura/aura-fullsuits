"""Aura Retail -- create_sale/create_return/hold_sale/cash-movement/statement
routes keep the shop's real currency precision, not a hardcoded 2dp default.

WHY THIS EXISTS

`create_sale` already read the shop's `base_currency` at ~line 4058 and fed it
into the PER-LINE pricing calls (`tax_engine.calculate_line(..., currency=
currency)`) -- see that call site's own comment on why JOD's three decimal
places (fils) matter. What it did NOT do is carry that same currency into the
step immediately after: summing the per-line `subtotal`/`discount`/`tax`/
`total` Decimals and re-quantizing the SUM with a hardcoded `Decimal('0.01')`.
Every line was computed correctly to fils; the sale's own persisted total then
threw that precision away. `paid`/`change`/`balance_due` (the tender, the cash
handed back, and the AR balance) had the identical hardcoded-2dp bug one step
further down -- `change` is physical cash returned to a customer, so this one
shorted or overpaid a JOD customer by up to 5 fils on their change.

Converted in this pass (api/retail_api.py):
  * create_sale       -- subtotal/discount/tax/total sum-quantize, plus
                          paid/change/balance_due (~line 4346-4360)
  * create_return      -- refund sum-quantize (~5723), and original_balance_due
                          (~5867), which shares create_return's own `currency`
                          (read once near the top of the function, same as
                          create_sale's own)
  * hold_sale           -- subtotal/total (client-submitted, display-only, but
                          still shown at the wrong precision without this fix)
  * create_cash_movement -- float_in/float_out/paid_in/paid_out, real drawer
                          cash that was missed when the REST of the drawer
                          (_cash_session_report's `expected`, _adjust_credit)
                          was converted in an earlier pass
  * customer_statement / supplier_statement -- the running_balance loop's
                          `run.quantize(Decimal('0.01'))` predates `_money()`
                          entirely, so a `_money()`-only search for the
                          original defect would never have found it

TWO MORE hardcoded-2dp sites were found and fixed while writing this file's
tests, both downstream of an already-listed site and both real -- a fix that
computes the right number and then re-rounds it on the way out is not fixed:
  * create_sale's own response payload silently did `'change': round(change, 2),
    'total': round(total, 2)` -- AFTER `change`/`total` had already been
    correctly quantized to the shop's currency and persisted at that
    precision in the INSERT just above. The DATABASE row was already right;
    only the number handed back to the till was wrong.
  * create_return's response payload did the identical thing:
    `'refund_amount': round(refund, 2)`.
Both are now `'change': change, 'total': total` / `'refund_amount': refund` --
the already-quantized Python values, not a second, currency-blind rounding.

NOT touched, and deliberately so: `_adjust_credit(conn, 'customers',
customer_id, cid, balance_due)` inside create_sale (~line 4724, its own
credit-sale AR posting) and the identical call inside create_return
(~line 5893, its AR-credit-on-refund posting). Both omit the `currency`
argument on purpose -- `_adjust_credit`'s own docstring already documents
these two exact call sites as "still out of scope for this pass", a decision
made by an earlier conversion wave, not something this file's scope
(retail_api.py sites 1-5 above) reopens. `test_create_return_original_
balance_due_uses_currency_precision...` below isolates `original_balance_due`
from those two undone sites rather than depending on their eventual fix.

Every money test below proves BOTH halves this defect class requires:
  (a) DEFAULT INSTALL -- no explicit `base_currency` row. `_company_currency`/
      `_settings()` both answer 'JOD' (`tax_engine.DEFAULT_BASE_CURRENCY`),
      and this is the COMMON case a fresh install is actually in, not an edge
      one -- see `_company_currency`'s own docstring for the earlier test that
      missed exactly this by always writing an explicit currency row.
  (b) THE ALLOW-HALF -- a company with `base_currency` explicitly 'USD' still
      rounds to cents. Proves the fix is currency-DRIVEN, not "always three
      decimals now" -- a fix that only proves fils survive cannot be told
      apart from one that hardcoded 3dp instead of 2dp, and the second would
      silently wrong every 2-decimal-currency shop the moment it shipped.

Self-contained, one process per file (AURA_APP_DATA binds at import) -- no
shared conftest.py exists for products/retail/tests/. Boot block copied
verbatim from retail_shift_close_email_test.py.

Run alone, from the repo root:
    py -3.14 -m pytest products/retail/tests/retail_sale_money_precision_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_sale_money_precision_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    """A fresh, logged-in admin client for its OWN newly-created company --
    one company per test, matching every other money-route test file in this
    suite (retail_payment_money_precision_test.py, retail_ar_ap_totals_test.py)
    -- so currency/balance/session assertions never see another test's rows.
    'admin' holds CAP_SELL/CAP_STOCK_ADJUST/CAP_CASH_CLOSE, the union of
    capabilities every route this file exercises is gated behind."""
    email = f'saleprec-{uuid.uuid4().hex[:8]}@test.local'
    password = 'SalePrecisionPW1'
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


def _set_currency(company_id, code):
    """Writes an EXPLICIT base_currency row -- used only by the (b) tests.
    The (a) tests deliberately never call this (see module docstring)."""
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO retail_settings (company_id, skey, svalue) VALUES (?,?,?) "
        "ON CONFLICT(company_id,skey) DO UPDATE SET svalue=excluded.svalue",
        (company_id, 'base_currency', code))
    conn.commit(); conn.close()


def _seed_product(c, **kw):
    payload = {
        'name': 'Precision Widget', 'sku': f'SALEPREC-{uuid.uuid4().hex[:8]}',
        'cost_price': 1.0, 'sell_price': 10.0, 'tax_rate': 0, 'initial_stock': 100,
    }
    payload.update(kw)
    r = c.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_customer(c, name='Precision Customer'):
    r = c.post(f'{API}/customers', json={'name': f'{name} {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_supplier(c, name='Precision Supplier'):
    r = c.post(f'{API}/suppliers', json={'name': f'{name} {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _set_customer_credit(cust_id, mode='unlimited', limit=1000000):
    """Direct DB write, bypassing the customer-creation/PATCH routes -- same
    testing technique `_set_currency` above already uses. `credit_mode` has
    no other legitimate way to reach 'unlimited' through this file's own
    endpoints, and this file is not the place to add one."""
    conn = get_retail_conn()
    conn.execute("UPDATE customers SET credit_mode=?, credit_limit=? WHERE id=?",
                 (mode, limit, cust_id))
    conn.commit(); conn.close()


def _party_balance(table, party_id):
    conn = get_retail_conn()
    try:
        row = conn.execute(f"SELECT credit_balance FROM {table} WHERE id=?", (party_id,)).fetchone()
    finally:
        conn.close()
    return row['credit_balance']


def _sale_row(sale_id):
    conn = get_retail_conn()
    try:
        row = conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    finally:
        conn.close()
    return dict(row)


def _return_row(return_id):
    conn = get_retail_conn()
    try:
        row = conn.execute("SELECT * FROM returns WHERE id=?", (return_id,)).fetchone()
    finally:
        conn.close()
    return dict(row)


def _held_sale_row(held_id):
    conn = get_retail_conn()
    try:
        row = conn.execute("SELECT * FROM held_sales WHERE id=?", (held_id,)).fetchone()
    finally:
        conn.close()
    return dict(row)


def _cash_movement_row(movement_id):
    conn = get_retail_conn()
    try:
        row = conn.execute("SELECT * FROM cash_movements WHERE id=?", (movement_id,)).fetchone()
    finally:
        conn.close()
    return dict(row)


# ═════════════════════════════════════════════════════════════════════════
# 1. create_sale -- subtotal/discount/tax/total sum-quantize, plus
#    paid/change/balance_due (POST /sales)
#
#    Two lines (10.001 + 2.344 = 12.345), NOT one -- proves the SUM step,
#    not just calculate_line's own already-currency-aware per-line rounding
#    (see that function's docstring: it already quantizes each line to the
#    given currency before this route ever sums them).
# ═════════════════════════════════════════════════════════════════════════

def _two_line_sale(c, currency_hint=None, amount_paid=15.0):
    pid_a = _seed_product(c, name='Precision Widget A', sell_price=10.001)
    pid_b = _seed_product(c, name='Precision Widget B', sell_price=2.344)
    r = c.post(f'{API}/sales', json={
        'items': [{'product_id': pid_a, 'quantity': 1}, {'product_id': pid_b, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': amount_paid,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def test_create_sale_default_install_sums_lines_at_full_fils_precision(client):
    """THE DEFAULT INSTALL -- no explicit base_currency row. Two lines whose
    gross totals (10.001 + 2.344) sum to a genuinely 3-decimal figure that a
    hardcoded `Decimal('0.01')` sum-quantize would coarsen to 12.35 (2dp,
    ROUND_HALF_UP on the .345 boundary) even though each LINE was already
    computed correctly. `change` (15.0 tendered - 12.345 total = 2.655) is
    the SAME class of bug one step further down -- physical cash owed back
    to the customer."""
    body = _two_line_sale(client, amount_paid=15.0)

    assert body['subtotal'] == 12.345, (
        "the sale's own subtotal must keep fils on a default (JOD) install, "
        f"got {body['subtotal']!r}")
    assert body['total'] == 12.345, f"got {body['total']!r}"
    assert body['change'] == 2.655, (
        "change is physical cash handed back to the customer -- must be "
        f"currency-quantized, not hardcoded 2dp, got {body['change']!r}")
    assert body['amount_paid'] == 15.0
    # balance_due is NOT clamped at 0 (unlike paid/change) -- an overpaid
    # cash sale legitimately reports a negative balance_due here; the point
    # under test is that it is quantized to the shop's currency, not 2dp.
    assert body['balance_due'] == -2.655, f"got {body['balance_due']!r}"

    row = _sale_row(body['id'])
    assert row['subtotal'] == 12.345, "PERSISTED subtotal, not just the response echo"
    assert row['total'] == 12.345
    assert row['change_amount'] == 2.655
    assert row['amount_paid'] == 15.0


def test_create_sale_two_decimal_currency_still_gets_two(client):
    """THE ALLOW-HALF: same two products (10.001 + 2.344), this company's
    base_currency is explicitly 'USD'. Each LINE rounds to cents inside
    calculate_line first (10.001 -> 10.00, 2.344 -> 2.34), so the correctly-
    fixed sum is 12.34 -- NOT 12.345, and also not 12.35 (which is what
    directly quantizing the un-rounded 12.345 sum to cents would give, and
    is not what this route actually computes). Proves the fix reads the
    real per-company currency rather than always keeping three decimals."""
    _set_currency(client.test_company_id, 'USD')
    body = _two_line_sale(client, amount_paid=15.0)

    assert body['subtotal'] == 12.34, f"got {body['subtotal']!r}"
    assert body['total'] == 12.34, f"got {body['total']!r}"
    assert body['change'] == 2.66, f"got {body['change']!r}"
    assert body['amount_paid'] == 15.0

    row = _sale_row(body['id'])
    assert row['subtotal'] == 12.34
    assert row['total'] == 12.34
    assert row['change_amount'] == 2.66


# ═════════════════════════════════════════════════════════════════════════
# 2. create_return -- refund sum-quantize (POST /returns), plus
#    original_balance_due (the AR-credit cap computed from the ORIGINAL
#    sale's own total/amount_paid)
# ═════════════════════════════════════════════════════════════════════════

def test_create_return_default_install_keeps_fils(client):
    """Full return of both lines from a fresh default-install (JOD) sale --
    refund_total sums the SAME two currency-quantized per-line `calc['total']`
    figures create_sale's own test above does, through the identical
    hardcoded-2dp-sum bug class, one route over."""
    sale = _two_line_sale(client, amount_paid=999999)  # cash overpay, no customer needed
    assert sale['total'] == 12.345  # precondition, proved by the create_sale test above

    r = client.post(f'{API}/returns', json={
        'sale_id': sale['id'],
        'items': [{'product_id': item['product_id'], 'quantity': item['quantity']} for item in sale['lines']],
    })
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']

    assert body['refund_amount'] == 12.345, (
        "a default-install (JOD) full return must refund fils, not round them away, "
        f"got {body['refund_amount']!r}")
    row = _return_row(body['id'])
    assert row['refund_amount'] == 12.345, "PERSISTED refund_amount, not just the response echo"


def test_create_return_two_decimal_currency_still_gets_two(client):
    _set_currency(client.test_company_id, 'USD')
    sale = _two_line_sale(client, amount_paid=999999)
    assert sale['total'] == 12.34  # precondition, proved by create_sale's own USD test above

    r = client.post(f'{API}/returns', json={
        'sale_id': sale['id'],
        'items': [{'product_id': item['product_id'], 'quantity': item['quantity']} for item in sale['lines']],
    })
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']

    assert body['refund_amount'] == 12.34, f"got {body['refund_amount']!r}"
    row = _return_row(body['id'])
    assert row['refund_amount'] == 12.34


def test_create_return_original_balance_due_uses_currency_precision_not_hardcoded_2dp(client):
    """Isolates `original_balance_due` (~line 5867) from the two
    `_adjust_credit` call sites this pass deliberately leaves alone (see
    module docstring) -- neither of those accepts a currency, so any test
    that reads the resulting `credit_balance` back would be proving THEIR
    rounding, not this variable's.

    The isolation: a sale total of 0.006 JOD with amount_paid=0.001 leaves a
    real difference of EXACTLY 0.005. At JOD's real 3dp precision,
    `_money(0.005, 'JOD') == 0.005`, which is NOT strictly greater than the
    `if original_balance_due > 0.005:` gate immediately below it in
    create_return -- so the AR-credit branch must not fire AT ALL, and the
    customer's credit_balance must not move by even one unit. Under a
    hardcoded-2dp bug, `_money(0.005)` (no currency) rounds HALF UP to 0.01,
    which IS > 0.005 -- the branch fires, and the balance DOES move. Whether
    it moves at all, not by how much, is what isolates this one variable's
    own precision from the two untouched call sites downstream of it."""
    cust = _seed_customer(client)
    _set_customer_credit(cust, mode='unlimited')
    pid = _seed_product(client, sell_price=0.006, tax_rate=0)

    sale = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'credit', 'amount_paid': 0.001, 'customer_id': cust,
        'idempotency_key': str(uuid.uuid4())})
    assert sale.status_code == 200, sale.get_json()
    sale_body = sale.get_json()['data']
    assert sale_body['total'] == 0.006 and sale_body['amount_paid'] == 0.001, (
        "precondition: the original sale must itself carry the exact fils this "
        f"test depends on, got total={sale_body['total']!r} paid={sale_body['amount_paid']!r}")

    balance_before_return = _party_balance('customers', cust)

    r = client.post(f'{API}/returns', json={
        'sale_id': sale_body['id'], 'items': [{'product_id': pid, 'quantity': 1}]})
    assert r.status_code == 200, r.get_json()

    balance_after_return = _party_balance('customers', cust)
    assert balance_after_return == balance_before_return, (
        "original_balance_due (0.005 at JOD's real 3dp precision) is not > 0.005, "
        "so the AR-credit branch must not fire at all -- credit_balance changed from "
        f"{balance_before_return!r} to {balance_after_return!r}, which means "
        "original_balance_due was computed at a hardcoded 2dp (0.005 -> 0.01, "
        "which IS > 0.005) instead of the shop's own currency precision")


# ═════════════════════════════════════════════════════════════════════════
# 3. hold_sale -- subtotal/total (POST /held-sales). Client-submitted,
#    display-only figures (never fed into a financial record), but still
#    shown/stored at the wrong precision without this fix.
# ═════════════════════════════════════════════════════════════════════════

def test_hold_sale_default_install_keeps_fils(client):
    r = client.post(f'{API}/held-sales', json={
        'items': [{'product_id': 'irrelevant', 'line_total': 12.345, 'quantity': 1}]})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']

    assert body['subtotal'] == 12.345, f"got {body['subtotal']!r}"
    assert body['total'] == 12.345, f"got {body['total']!r}"
    row = _held_sale_row(body['id'])
    assert row['subtotal'] == 12.345
    assert row['total'] == 12.345


def test_hold_sale_two_decimal_currency_still_gets_two(client):
    _set_currency(client.test_company_id, 'USD')
    r = client.post(f'{API}/held-sales', json={
        'items': [{'product_id': 'irrelevant', 'line_total': 12.345, 'quantity': 1}]})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']

    # 12.345 quantized directly to cents (ROUND_HALF_UP) -> 12.35 -- this is
    # the RAW client-submitted sum, unlike create_sale's own per-line-then-
    # summed 12.34 above; hold_sale never routes through calculate_line.
    assert body['subtotal'] == 12.35, f"got {body['subtotal']!r}"
    assert body['total'] == 12.35, f"got {body['total']!r}"
    row = _held_sale_row(body['id'])
    assert row['subtotal'] == 12.35
    assert row['total'] == 12.35


# ═════════════════════════════════════════════════════════════════════════
# 4. create_cash_movement -- float_in/float_out/paid_in/paid_out
#    (POST /cash-sessions/<id>/movements). Real drawer cash, missed when the
#    rest of the drawer (_cash_session_report, _adjust_credit) was converted
#    in an earlier pass.
# ═════════════════════════════════════════════════════════════════════════

def _open_session(c, opening=100.0):
    r = c.post(f'{API}/cash-sessions/open', json={'opening_float': opening})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def test_cash_movement_default_install_keeps_fils(client):
    sid = _open_session(client)
    r = client.post(f'{API}/cash-sessions/{sid}/movements',
                    json={'type': 'float_in', 'amount': 12.345})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']

    assert body['amount'] == 12.345, (
        "a default-install (JOD) float_in must persist fils, not round them away, "
        f"got {body['amount']!r}")
    row = _cash_movement_row(body['id'])
    assert row['amount'] == 12.345


def test_cash_movement_two_decimal_currency_still_gets_two(client):
    _set_currency(client.test_company_id, 'USD')
    sid = _open_session(client)
    r = client.post(f'{API}/cash-sessions/{sid}/movements',
                    json={'type': 'paid_out', 'amount': 12.345})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']

    assert body['amount'] == 12.35, f"got {body['amount']!r}"
    row = _cash_movement_row(body['id'])
    assert row['amount'] == 12.35


# ═════════════════════════════════════════════════════════════════════════
# 5. customer_statement / supplier_statement -- 'amount', 'running_balance'
#    and 'balance' (GET .../statement). `running_balance` is a raw
#    `run.quantize(Decimal('0.01'))` that predates `_money()` entirely -- a
#    `_money()`-only search for the original defect would never find it.
#    Built on top of customer_payment/supplier_payment, already converted in
#    an earlier pass -- this isolates the STATEMENT's OWN quantization, not
#    the payment route's.
# ═════════════════════════════════════════════════════════════════════════

def test_customer_statement_default_install_keeps_fils(client):
    cust = _seed_customer(client)
    pay = client.post(f'{API}/customers/{cust}/payments', json={'amount': 12.345})
    assert pay.status_code == 200, pay.get_json()

    r = client.get(f'{API}/customers/{cust}/statement')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']

    assert len(data['events']) == 1
    assert data['events'][0]['amount'] == 12.345
    assert data['events'][0]['running_balance'] == -12.345, (
        "running_balance must keep fils on a default (JOD) install, "
        f"got {data['events'][0]['running_balance']!r}")
    assert data['balance'] == -12.345


def test_customer_statement_two_decimal_currency_still_gets_two(client):
    _set_currency(client.test_company_id, 'USD')
    cust = _seed_customer(client)
    pay = client.post(f'{API}/customers/{cust}/payments', json={'amount': 12.345})
    assert pay.status_code == 200, pay.get_json()

    r = client.get(f'{API}/customers/{cust}/statement')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']

    assert data['events'][0]['running_balance'] == -12.35, f"got {data['events'][0]['running_balance']!r}"
    assert data['balance'] == -12.35


def test_supplier_statement_default_install_keeps_fils(client):
    sup = _seed_supplier(client)
    pay = client.post(f'{API}/suppliers/{sup}/payments', json={'amount': 12.345})
    assert pay.status_code == 200, pay.get_json()

    r = client.get(f'{API}/suppliers/{sup}/statement')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']

    assert len(data['events']) == 1
    assert data['events'][0]['amount'] == 12.345
    assert data['events'][0]['running_balance'] == -12.345, f"got {data['events'][0]['running_balance']!r}"
    assert data['balance'] == -12.345


def test_supplier_statement_two_decimal_currency_still_gets_two(client):
    _set_currency(client.test_company_id, 'USD')
    sup = _seed_supplier(client)
    pay = client.post(f'{API}/suppliers/{sup}/payments', json={'amount': 12.345})
    assert pay.status_code == 200, pay.get_json()

    r = client.get(f'{API}/suppliers/{sup}/statement')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']

    assert data['events'][0]['running_balance'] == -12.35, f"got {data['events'][0]['running_balance']!r}"
    assert data['balance'] == -12.35


# ── Dashboard metrics: the last `round(x, 2)` on real customer money ────────
#
# These figures lived in a plain `round(x, 2)` in the jsonify payload, which is
# why they outlived the whole money-precision pass: an audit of `_money()` and
# `.quantize()` call sites structurally cannot see a bare `round()`. Found by
# grepping the OPERATION rather than the helper -- the same way `create_sale`'s
# `round(change, 2)` response-boundary defect was found.

def _dashboard(c):
    r = c.get(f'{API}/dashboard/stats')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def test_dashboard_default_install_reports_sales_in_fils(client):
    """A JOD shop's dashboard must agree with its own receipts.

    Rings a sale totalling 12.345 on a default install (no base_currency row).
    Under the old `round(today_sales, 2)` the dashboard reported 12.35 while
    the sale itself stored 12.345 -- so an owner totalling receipts against
    the first screen they open would find it short, with nothing to explain
    the difference. Display-only, but it was the one remaining place the
    product visibly disagreed with itself.
    """
    _two_line_sale(client)
    data = _dashboard(client)
    assert float(data['today_sales']) == 12.345, (
        "dashboard coarsened a JOD total to cents: today_sales=%r"
        % (data['today_sales'],))


def test_dashboard_two_decimal_currency_still_gets_two(client):
    """THE ALLOW-HALF: currency-DRIVEN, not 'always three decimals'.

    A USD shop's dashboard must still report cents. Without this, a fix that
    simply hardcoded three decimals would pass the test above and be just as
    wrong as what it replaced, in the other direction.
    """
    _set_currency(client.test_company_id, 'USD')
    pid = _seed_product(client, name='Dollar Widget', sell_price=12.34)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 12.34,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    data = _dashboard(client)
    assert float(data['today_sales']) == 12.34, (
        "a dollar shop must report cents: today_sales=%r" % (data['today_sales'],))


# ═════════════════════════════════════════════════════════════════════════
# 6. Reports page -- /reports/payment-methods and /reports/sales-trend.
#
#    core/retail/metrics.py carried its OWN THIRD `_money`, hardcoded to two
#    decimals, entirely separate from this file's own defect class
#    (api/retail_api.py's `_money`) and from core/retail/pricing.py's
#    `format_money`. Every metrics.py breakdown -- revenue_by_payment_method,
#    revenue_by_day, revenue_by_hour, revenue_by_employee, revenue_by_branch,
#    top_products, summary -- flowed through it, so JOD reports disagreed
#    with the receipts, drawer counts and statements underneath them exactly
#    as the dashboard tests above did, on every Reports-page widget at once.
#
#    Fixing only api/retail_api.py's OWN `_money` (as dashboard_stats's own
#    fix does for its jsonify payload) would NOT have fixed these two
#    routes: neither re-quantizes metrics.py's return value through that
#    helper at all -- the metrics figure IS the response body. A route-level
#    fix here would have re-quantized an already-2dp-coarsened value to 3dp
#    and restored nothing, the same "arrives already coarsened" failure mode
#    the module docstring above describes for the dashboard.
#
#    Two of the module's several money-returning surfaces, picked to be
#    structurally different from each other and from the dashboard tests
#    above: one is a dict-of-rows breakdown (`revenue_by_payment_method`),
#    the other a day-bucketed list summed across buckets (`revenue_by_day`).
# ═════════════════════════════════════════════════════════════════════════

def test_report_payment_methods_default_install_reports_fils(client):
    """A JOD shop's payment-method breakdown must agree with the sale that
    fed it -- core/retail/metrics.py::revenue_by_payment_method()."""
    _two_line_sale(client)
    data = client.get(f'{API}/reports/payment-methods?days=1').get_json()['data']
    cash = next(row for row in data if row['payment_method'] == 'cash')
    assert cash['revenue'] == 12.345, (
        "payment-methods report coarsened a JOD total to cents: "
        f"revenue={cash['revenue']!r}")


def test_report_payment_methods_two_decimal_currency_still_gets_two(client):
    """THE ALLOW-HALF: a USD shop's payment-method breakdown must still
    report cents, proving the fix reads the shop's real currency rather
    than always keeping three decimals."""
    _set_currency(client.test_company_id, 'USD')
    _two_line_sale(client)
    data = client.get(f'{API}/reports/payment-methods?days=1').get_json()['data']
    cash = next(row for row in data if row['payment_method'] == 'cash')
    assert cash['revenue'] == 12.34, f"got {cash['revenue']!r}"


def test_report_sales_trend_default_install_reports_fils(client):
    """A JOD shop's sales-trend chart must agree with the sale that fed it
    -- core/retail/metrics.py::revenue_by_day()."""
    _two_line_sale(client)
    body = client.get(f'{API}/reports/sales-trend?days=1').get_json()
    assert sum(body['data']) == 12.345, (
        "sales-trend coarsened a JOD total to cents: data=%r" % (body['data'],))


def test_report_sales_trend_two_decimal_currency_still_gets_two(client):
    """THE ALLOW-HALF: a USD shop's sales-trend chart must still report
    cents."""
    _set_currency(client.test_company_id, 'USD')
    _two_line_sale(client)
    body = client.get(f'{API}/reports/sales-trend?days=1').get_json()
    assert sum(body['data']) == 12.34, f"got {body['data']!r}"


# ═════════════════════════════════════════════════════════════════════════
# 7. /reports/top-products -- core/retail/metrics.py::top_products()'s
#    `profit` field, which is revenue MINUS `quantity_sold * products.
#    cost_price`.
#
#    WHY THIS SURFACE, SPECIFICALLY: every test above (dashboard, payment-
#    methods, sales-trend) sums or echoes a `sales.total` that
#    create_sale() ALREADY quantized to the shop's currency the moment the
#    sale was rung -- so the number arriving at metrics.py is, by
#    construction, already exact at that currency's own precision, and a
#    SUM of already-exact values stays exact. That makes those three
#    surfaces provably correct for the fils-survive half, but structurally
#    UNABLE to catch a metrics.py that quantizes to the WRONG fixed
#    precision on a 2-decimal-currency company: 12.34 quantized to three
#    decimals is 12.340, and float(12.340) == 12.34 -- the extra digit is
#    always a trailing zero, invisible at the JSON/float boundary.
#
#    `cost_price` breaks that coincidence: it is a raw product field with
#    NO currency precision of its own (nothing quantizes it on write), so
#    `quantity_sold * cost_price` can and does need genuine rounding
#    regardless of which currency the shop uses. 0.333 has no clean
#    2-decimal form to accidentally agree with -- this is what makes this
#    pair the one that actually DISTINGUISHES "quantize to the currency's
#    real precision" from "quantize to a different hardcoded precision",
#    both directions, rather than merely re-confirming the SUM-based
#    surfaces above.
# ═════════════════════════════════════════════════════════════════════════

def _cost_precision_sale(c):
    pid = _seed_product(c, name='Cost Precision Widget',
                         sell_price=100.0, cost_price=0.333, tax_rate=0)
    r = c.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 100.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()


def test_report_top_products_default_install_reports_fils(client):
    """A JOD shop's top-products profit column must reflect the real cost,
    not a 2dp-coarsened one: 1 unit at 100.00 revenue, cost_price 0.333 (no
    rounding needed at JOD's own 3dp precision) -> profit == 99.667."""
    _cost_precision_sale(client)
    rows = client.get(f'{API}/reports/top-products?days=1').get_json()
    idx = rows['labels'].index('Cost Precision Widget')
    assert rows['profit'][idx] == 99.667, (
        "top-products profit coarsened cost_price to cents: "
        f"profit={rows['profit'][idx]!r}")


def test_report_top_products_two_decimal_currency_still_gets_two(client):
    """THE ALLOW-HALF: a USD shop must round cost_price to cents (0.333 ->
    0.33) BEFORE subtracting it from revenue, giving profit == 99.67 -- not
    99.667, which is what a metrics.py hardcoded to three decimals
    regardless of currency would report for a USD shop too."""
    _set_currency(client.test_company_id, 'USD')
    _cost_precision_sale(client)
    rows = client.get(f'{API}/reports/top-products?days=1').get_json()
    idx = rows['labels'].index('Cost Precision Widget')
    assert rows['profit'][idx] == 99.67, f"got {rows['profit'][idx]!r}"
