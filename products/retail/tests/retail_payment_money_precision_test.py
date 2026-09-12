"""Aura Retail -- customer/supplier/PO payment routes keep the shop's real
currency precision, not a hardcoded 2dp default.

WHY THIS EXISTS

`_money()` in api/retail_api.py used to hardcode `Decimal('0.01')` -- fine
for a 2-decimal currency, silently wrong for the Jordanian dinar (JOD, this
product's default), which has THREE decimal places (1000 fils). The cash
DRAWER side of this bug (opening_float, closing_float_counted, variance,
_cash_session_report) was already fixed and is covered by
retail_cash_drawer_test.py / retail_drawer_money_sweep_test.py /
retail_shift_close_email_test.py. This file covers the other half: the
CUSTOMER, SUPPLIER and PURCHASE-ORDER payment paths, which is where a real
shop's money actually moves day to day.

Converted in this pass (api/retail_api.py):
  * customer_payment          -- POST /customers/<id>/payments
  * supplier_payment          -- POST /suppliers/<id>/payments
  * pay_purchase_order        -- POST /purchase-orders/<id>/pay
  * create_purchase_order     -- POST /purchase-orders (its own amount_paid
                                  down-payment at creation time)
  * accept_reorder_request    -- POST /reorder-requests/<id>/accept (the PO
                                  total it drafts, a SECOND mint site for the
                                  same purchase_orders.total column
                                  create_purchase_order writes)
  * void_payment              -- POST /payments/<id>/void (the balance it
                                  reverses)
  * _record_payment / _adjust_credit -- the two shared helpers every one of
    the routes above funnels through. `_adjust_credit` had its OWN
    independent hardcoded `Decimal('0.01')` (a second, easy-to-miss copy of
    the exact same bug, one layer below `_money()`) -- converting only the
    payment amount and leaving the BALANCE it is computed from hardcoded
    would have looked fixed while still losing fils the moment a payment
    landed in `customers.credit_balance` / `suppliers.credit_balance`.

Every test below proves BOTH halves the file's own defect class requires:
  (a) DEFAULT install -- no explicit `base_currency` row. `_company_currency`
      answers 'JOD' from `tax_engine.DEFAULT_BASE_CURRENCY`, and this is the
      COMMON case, not an edge one (see `_company_currency`'s own docstring
      for the prior test that missed exactly this by always writing an
      explicit currency row).
  (b) THE ALLOW-HALF -- a company with `base_currency` explicitly 'USD'
      still rounds to 2dp. Same input (12.345) as (a), a DIFFERENT stored
      result (12.35, not 12.345): this is what proves the fix is
      currency-DRIVEN, not "always three decimals now". A test that only
      proves 12.345 survives cannot tell "reads the real currency" apart
      from "hardcoded 3dp instead of 2dp" -- and the second would silently
      wrong every 2-decimal-currency shop the moment it shipped.
  (c) where a balance is computed FROM the payment (customers/suppliers
      .credit_balance, purchase_orders.amount_paid), that DERIVED figure is
      asserted too -- proving the payment stored fils is not the same as
      proving the balance did (this is exactly the `_adjust_credit` gap
      described above).

Self-contained, one process per file -- AURA_APP_DATA binds at import, no
shared conftest.py exists for products/retail/tests/. Copied verbatim from
retail_shift_close_email_test.py's own boot block.

Run:
    py -3.14 -m pytest products/retail/tests/retail_payment_money_precision_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_payment_money_precision_"))
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
    one company per test, so credit_balance/currency assertions never see
    another test's rows. 'admin' matches every other money-route test file
    in this suite (retail_ar_ap_totals_test.py, retail_po_number_uniqueness_
    test.py): it holds CAP_SELL/CAP_EMPLOYEES/CAP_STOCK_ADJUST, the union of
    capabilities every route this file exercises is gated behind."""
    email = f'moneyprec-{uuid.uuid4().hex[:8]}@test.local'
    password = 'MoneyPrecisionPW1'
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


def _seed_customer(c, name='Precision Customer'):
    r = c.post(f'{API}/customers', json={'name': f'{name} {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_supplier(c, name='Precision Supplier'):
    r = c.post(f'{API}/suppliers', json={'name': f'{name} {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_product(c, **kw):
    payload = {
        'name': 'Payment Precision Widget', 'sku': f'PAYPREC-{uuid.uuid4().hex[:8]}',
        'cost_price': 5.0, 'sell_price': 10.0,
    }
    payload.update(kw)
    r = c.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_po(c, product_id, **kw):
    payload = {'items': [{'product_id': product_id, 'quantity': 1, 'unit_cost': 1000.0}]}
    payload.update(kw)
    return c.post(f'{API}/purchase-orders', json=payload)


def _payment_amount(company_id, reference):
    """`payments.reference` is minted by `_next_ref`, which counts PER
    COMPANY starting at 1 -- so two different companies' first payment both
    mint "REC-000001", and a lookup that does not also scope by company_id
    can silently read back a DIFFERENT company's row of the same name. Every
    test in this file uses a fresh company, so this bit twice during this
    file's own development before company_id was added here."""
    conn = get_retail_conn()
    try:
        row = conn.execute(
            "SELECT amount FROM payments WHERE company_id=? AND reference=?",
            (company_id, reference)).fetchone()
    finally:
        conn.close()
    assert row is not None, f'no payments row for company_id={company_id!r} reference={reference!r}'
    return row['amount']


def _party_balance(table, party_id):
    conn = get_retail_conn()
    try:
        row = conn.execute(f"SELECT credit_balance FROM {table} WHERE id=?", (party_id,)).fetchone()
    finally:
        conn.close()
    return row['credit_balance']


def _po_row(po_id):
    conn = get_retail_conn()
    try:
        row = conn.execute("SELECT * FROM purchase_orders WHERE id=?", (po_id,)).fetchone()
    finally:
        conn.close()
    return dict(row)


# ═════════════════════════════════════════════════════════════════════════
# 1. customer_payment -- POST /customers/<id>/payments
# ═════════════════════════════════════════════════════════════════════════

def test_customer_payment_default_install_keeps_fils(client):
    """THE DEFAULT INSTALL -- no explicit base_currency row. Both the
    PERSISTED payments.amount and the DERIVED customers.credit_balance must
    keep all three decimals."""
    cust = _seed_customer(client)
    r = client.post(f'{API}/customers/{cust}/payments', json={'amount': 12.345})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']

    assert _payment_amount(client.test_company_id, body['reference']) == 12.345, (
        "a default-install (JOD) customer payment must persist fils, not round them away")
    assert body['new_balance'] == -12.345, (
        "the DERIVED balance (customers.credit_balance, via _adjust_credit) must carry the "
        f"same precision as the payment it was computed from, got {body['new_balance']!r}")
    assert _party_balance('customers', cust) == -12.345


def test_customer_payment_two_decimal_currency_still_gets_two(client):
    """THE ALLOW-HALF: same 12.345 input as the test above, this company's
    base_currency is explicitly 'USD' -- the result must be 12.35 (rounded to
    cents), NOT 12.345. Proves the fix is currency-DRIVEN: a company that has
    never touched currency settings gets fils (test above); a company that
    explicitly chose a 2-decimal currency still gets cents."""
    _set_currency(client.test_company_id, 'USD')
    cust = _seed_customer(client)
    r = client.post(f'{API}/customers/{cust}/payments', json={'amount': 12.345})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']

    assert _payment_amount(client.test_company_id, body['reference']) == 12.35, (
        "a USD customer payment must round to cents, not keep fils")
    assert body['new_balance'] == -12.35
    assert _party_balance('customers', cust) == -12.35


# ═════════════════════════════════════════════════════════════════════════
# 2. supplier_payment -- POST /suppliers/<id>/payments
# ═════════════════════════════════════════════════════════════════════════

def test_supplier_payment_default_install_keeps_fils(client):
    sup = _seed_supplier(client)
    r = client.post(f'{API}/suppliers/{sup}/payments', json={'amount': 12.345})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']

    assert _payment_amount(client.test_company_id, body['reference']) == 12.345
    assert body['new_balance'] == -12.345
    assert _party_balance('suppliers', sup) == -12.345


def test_supplier_payment_two_decimal_currency_still_gets_two(client):
    _set_currency(client.test_company_id, 'USD')
    sup = _seed_supplier(client)
    r = client.post(f'{API}/suppliers/{sup}/payments', json={'amount': 12.345})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']

    assert _payment_amount(client.test_company_id, body['reference']) == 12.35
    assert body['new_balance'] == -12.35
    assert _party_balance('suppliers', sup) == -12.35


# ═════════════════════════════════════════════════════════════════════════
# 3. pay_purchase_order -- POST /purchase-orders/<id>/pay
# ═════════════════════════════════════════════════════════════════════════
# A PO is created with amount_paid=0 (isolating this route from create_
# purchase_order's OWN down-payment path, tested separately below), against
# a supplier, with a round total (1000, unaffected by either 2dp or 3dp
# rounding) so only the `/pay` call's own arithmetic is under test.

def test_pay_purchase_order_default_install_keeps_fils(client):
    sup = _seed_supplier(client)
    pid = _seed_product(client)
    po = _create_po(client, pid, supplier_id=sup)
    assert po.status_code == 200, po.get_json()
    po_id = po.get_json()['data']['id']

    r = client.post(f'{API}/purchase-orders/{po_id}/pay', json={'amount': 12.345})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']

    assert body['amount_paid'] == 12.345
    assert body['payment_status'] == 'partial'
    assert _payment_amount(client.test_company_id, body['reference']) == 12.345
    row = _po_row(po_id)
    assert row['amount_paid'] == 12.345, (
        "purchase_orders.amount_paid was persisted at the wrong precision")
    # DERIVED figure: paying the PO reduces the supplier's payable the same
    # way a direct supplier_payment does, through the same _adjust_credit.
    assert _party_balance('suppliers', sup) == 1000.0 - 12.345


def test_pay_purchase_order_two_decimal_currency_still_gets_two(client):
    _set_currency(client.test_company_id, 'USD')
    sup = _seed_supplier(client)
    pid = _seed_product(client)
    po = _create_po(client, pid, supplier_id=sup)
    assert po.status_code == 200, po.get_json()
    po_id = po.get_json()['data']['id']

    r = client.post(f'{API}/purchase-orders/{po_id}/pay', json={'amount': 12.345})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']

    assert body['amount_paid'] == 12.35
    assert _payment_amount(client.test_company_id, body['reference']) == 12.35
    row = _po_row(po_id)
    assert row['amount_paid'] == 12.35
    assert _party_balance('suppliers', sup) == 1000.0 - 12.35


# ═════════════════════════════════════════════════════════════════════════
# 4. create_purchase_order -- POST /purchase-orders, its own amount_paid
#    down-payment at creation time (a SEPARATE call site from pay_purchase_
#    order above -- both write purchase_orders.amount_paid / suppliers.
#    credit_balance, and both had to be converted independently).
# ═════════════════════════════════════════════════════════════════════════

def test_create_purchase_order_down_payment_default_install_keeps_fils(client):
    sup = _seed_supplier(client)
    pid = _seed_product(client)
    po = _create_po(client, pid, supplier_id=sup, amount_paid=12.345)
    assert po.status_code == 200, po.get_json()
    po_id = po.get_json()['data']['id']

    row = _po_row(po_id)
    assert row['total'] == 1000.0
    assert row['amount_paid'] == 12.345, (
        "create_purchase_order's own down-payment must persist fils on a default (JOD) install")
    assert row['payment_status'] == 'partial'
    # DERIVED: the unpaid balance (total - amount_paid) is pushed onto the
    # supplier's AP ledger via _adjust_credit -- same helper, same fix.
    assert _party_balance('suppliers', sup) == 1000.0 - 12.345


def test_create_purchase_order_down_payment_two_decimal_currency_still_gets_two(client):
    _set_currency(client.test_company_id, 'USD')
    sup = _seed_supplier(client)
    pid = _seed_product(client)
    po = _create_po(client, pid, supplier_id=sup, amount_paid=12.345)
    assert po.status_code == 200, po.get_json()
    po_id = po.get_json()['data']['id']

    row = _po_row(po_id)
    assert row['amount_paid'] == 12.35, (
        "create_purchase_order's own down-payment must round to cents on a USD company")
    assert _party_balance('suppliers', sup) == 1000.0 - 12.35


# ═════════════════════════════════════════════════════════════════════════
# 5. accept_reorder_request -- POST /reorder-requests/<id>/accept
#    A SECOND, independent mint site for purchase_orders.total (the first is
#    create_purchase_order above) -- see retail_po_number_uniqueness_test.py
#    for the identical "two mint sites, both need the identical fix" shape
#    this file's own po_number bug had. amount_paid is always 0 here (this
#    route never takes a payment), so only `total`'s own precision is under
#    test -- no derived balance to assert.
# ═════════════════════════════════════════════════════════════════════════

def _trigger_pending_reorder_request(c, cost_price):
    """Drops a fresh product's stock to its reorder_level through a REAL
    sale -- the only way a reorder_requests row reaches 'pending' outside of
    hand-crafting one (core/retail/reorder_hook.py's post-sale trigger is the
    sole writer). reorder_level=1 keeps the drafted PO's total exactly
    `cost_price * 1`, so this test can pick a currency-revealing cost_price
    (12.345) without a second multiplication rounding the arithmetic itself."""
    pid = _seed_product(
        c, name='Reorder Precision Widget', cost_price=cost_price,
        reorder_level=1, reorder_method='whatsapp', initial_stock=2,
    )
    sale = c.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert sale.status_code == 200, sale.get_json()
    conn = get_retail_conn()
    try:
        row = conn.execute(
            "SELECT id FROM reorder_requests WHERE product_id=? AND status='pending'", (pid,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "the post-sale reorder hook did not create a pending request"
    return row['id']


def test_accept_reorder_request_default_install_keeps_fils(client):
    rid = _trigger_pending_reorder_request(client, cost_price=12.345)
    r = client.post(f'{API}/reorder-requests/{rid}/accept')
    assert r.status_code == 200, r.get_json()
    po_id = r.get_json()['data']['purchase_order_id']

    row = _po_row(po_id)
    assert row['amount_paid'] == 0.0, "this route never takes a payment -- sanity check on the fixture"
    assert row['total'] == 12.345, (
        "a reorder-drafted PO's total must keep fils on a default (JOD) install, "
        f"got {row['total']!r}")


def test_accept_reorder_request_two_decimal_currency_still_gets_two(client):
    _set_currency(client.test_company_id, 'USD')
    rid = _trigger_pending_reorder_request(client, cost_price=12.345)
    r = client.post(f'{API}/reorder-requests/{rid}/accept')
    assert r.status_code == 200, r.get_json()
    po_id = r.get_json()['data']['purchase_order_id']

    row = _po_row(po_id)
    assert row['total'] == 12.35, (
        "a reorder-drafted PO's total must round to cents on a USD company, "
        f"got {row['total']!r}")


# ═════════════════════════════════════════════════════════════════════════
# 6. void_payment -- POST /payments/<id>/void
#    Reverses the balance effect of a customer/supplier-account payment.
#    p['amount'] was already persisted at the shop's real precision by
#    customer_payment (converted above); this proves the REVERSAL is
#    quantized the same way, through the same (also converted) _adjust_
#    credit, rather than silently landing back at 2dp.
# ═════════════════════════════════════════════════════════════════════════

def test_voiding_a_default_install_payment_reverses_its_exact_fils(client):
    cust = _seed_customer(client)
    pay = client.post(f'{API}/customers/{cust}/payments', json={'amount': 12.345})
    assert pay.status_code == 200, pay.get_json()
    ref = pay.get_json()['data']['reference']
    assert _party_balance('customers', cust) == -12.345  # precondition, proved above already

    conn = get_retail_conn()
    try:
        # Scoped by company_id -- see _payment_amount's own comment: `_next_ref`
        # counts per company, so an unscoped lookup here can resolve to a
        # DIFFERENT company's "REC-000001" and then 404 against THIS company's
        # session (void_payment itself scopes `WHERE id=? AND company_id=?`).
        pid = conn.execute(
            "SELECT id FROM payments WHERE company_id=? AND reference=?",
            (client.test_company_id, ref)).fetchone()['id']
    finally:
        conn.close()

    r = client.post(f'{API}/payments/{pid}/void', json={'reason': 'precision test'})
    assert r.status_code == 200, r.get_json()

    # Voiding a 12.345 payment must add back EXACTLY 12.345, landing the
    # customer back at precisely 0.0. Under the old hardcoded-2dp reversal,
    # _money(12.345) -> 12.35 (ROUND_HALF_UP), which would have landed the
    # customer at 0.005 instead of 0.0 -- a phantom five-fils debt invented
    # by the void itself.
    assert _party_balance('customers', cust) == 0.0, (
        "voiding a fils-precise payment must restore the balance to EXACTLY zero, "
        f"got {_party_balance('customers', cust)!r} -- the reversal was quantized at the wrong precision")


# ═════════════════════════════════════════════════════════════════════════
# 7. Sanity: the default-install currency really is JOD (3 decimals), not
#    some other product default -- if this ever changes, every "keeps fils"
#    assertion above needs re-deriving, and this is where that would show up
#    first and cheaply, rather than as a wall of unrelated-looking failures.
# ═════════════════════════════════════════════════════════════════════════

def test_the_products_default_currency_really_is_three_decimal_jod():
    # api/retail_api.py imports this same module `as tax_engine` -- see its
    # own `from core.retail import pricing as tax_engine`.
    from core.retail import pricing as tax_engine
    assert tax_engine.DEFAULT_BASE_CURRENCY == 'JOD'
    assert -tax_engine.currency_quantum('JOD').as_tuple().exponent == 3
    assert -tax_engine.currency_quantum('USD').as_tuple().exponent == 2
