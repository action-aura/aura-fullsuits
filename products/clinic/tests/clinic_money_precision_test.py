"""Aura Clinic -- billing money must be rounded at the SHOP'S CURRENCY's
minor unit, not at a hardcoded two decimals.

Clinic had no currency-precision layer at all: no equivalent of Retail's
core/retail/pricing.py, and `git grep -i currency -- products/clinic`
returned only the e-invoicing adapter and the e-invoicing form's Currency
field. So `create_invoice` computed `round(taxable * tax_rate, 2)` and
`round(taxable + tax, 2)`, and `record_payment` quantized the caller's
amount at Decimal('0.01') on INGEST -- the value actually INSERTed into
clinic_payments.amount_paid.

Jordan's dinar has THREE decimals (1000 fils) and is the default currency,
so this was live on every clinic invoice, with no e-invoicing provider, no
worker and no configuration needed. Measured on the arithmetic itself
before the fix, with the real 16% VAT rate on a 12.345 JOD service:

    round(12.345 * 0.16, 2)     -> 1.98    exact: 1.975
    round(12.345 + 1.98,  2)    -> 14.33   exact: 14.320
    stored subtotal (unrounded) -> 12.345

    ... so the persisted row disagreed with ITSELF: 12.345 + 1.98 = 14.325,
    while the total column said 14.33.

    float(Decimal('12.345').quantize(Decimal('0.01'), ROUND_HALF_UP)) -> 12.35
    f'{99.999:.2f}'                                                   -> '100.00'

A 2-decimal regression answers 1.98 / 14.33 / 12.35 to the assertions
below; none of those values is reachable from a correct implementation, so
these tests cannot be satisfied by a coincidence.

BOTH DIRECTIONS. A fix that simply hardcoded THREE decimals instead of two
passes every JOD assertion here and is exactly as broken for the currencies
that have two, so `test_a_two_decimal_currency_still_rounds_to_two` sets the
company's currency to USD and pins the 2dp half.

Run on its own (every file under products/*/tests boots its own app):

    python -m pytest products/clinic/tests/clinic_money_precision_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_clinic_money_precision_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_CLINIC", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.einvoicing import settings as einvoicing_settings  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_clinic_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin_client():
    email = f'money-{uuid.uuid4().hex[:10]}@test.local'
    password = 'MoneyPW1'
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, 'ADMIN-0001', email, hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return c, company_id


def _set_currency(company_id, code):
    """Clinic has no settings table of its own; `einvoice_settings.currency`
    is the only place a clinic records this, which is exactly why
    clinic_api._company_currency reads it (see that docstring)."""
    conn = get_clinic_conn()
    einvoicing_settings.set_setting(conn, company_id, 'currency', code)
    conn.commit()
    conn.close()


def _invoice_row(inv_id):
    conn = get_clinic_conn()
    row = conn.execute(
        "SELECT subtotal, discount, tax, total FROM clinic_invoices WHERE id=?", (inv_id,)
    ).fetchone()
    conn.close()
    return row


# ── create_invoice ───────────────────────────────────────────────────────────

def test_jod_invoice_keeps_its_fils():
    """The headline case. 12.345 JOD at 16% VAT.

    A 2-decimal implementation answers tax 1.98 and total 14.33 here.
    """
    client, cid = _make_admin_client()
    r = client.post('/api/sub/clinic/invoices', json={
        'items': [{'description': 'Consultation', 'qty': 1, 'unit_price': 12.345}],
        'tax_rate': 0.16,
    })
    assert r.status_code == 200, r.get_json()
    inv_id = r.get_json()['data']['id']

    row = _invoice_row(inv_id)
    assert row['subtotal'] == 12.345, 'the subtotal must persist at the dinar\'s own precision'
    assert row['tax'] == 1.975, f'exact tax is 1.975; a 2dp round() answers 1.98 (got {row["tax"]})'
    assert row['total'] == 14.320, f'exact total is 14.320; a 2dp round() answers 14.33 (got {row["total"]})'


def test_the_stored_invoice_row_reconciles_against_itself():
    """The defect was not only imprecision: `subtotal` was persisted
    UNROUNDED while `tax`/`total` were rounded to cents, so the columns
    disagreed with each other (12.345 + 1.98 = 14.325, total said 14.33).
    A row nobody can add up is worse than a row that is a fils off."""
    client, cid = _make_admin_client()
    r = client.post('/api/sub/clinic/invoices', json={
        'items': [{'description': 'Consultation', 'qty': 1, 'unit_price': 12.345}],
        'tax_rate': 0.16,
    })
    inv_id = r.get_json()['data']['id']
    row = _invoice_row(inv_id)
    assert round(row['subtotal'] - row['discount'] + row['tax'], 6) == row['total']


def test_line_totals_sum_exactly_to_the_header_subtotal():
    """The e-invoicing document renders each line's LineExtensionAmount
    beside the document's own; if the header were summed from unrounded
    products while the lines were stored rounded, the filed document would
    not add up."""
    client, cid = _make_admin_client()
    r = client.post('/api/sub/clinic/invoices', json={
        'items': [
            {'description': 'A', 'qty': 3, 'unit_price': 4.115},
            {'description': 'B', 'qty': 1, 'unit_price': 0.335},
        ],
        'tax_rate': 0.16,
    })
    inv_id = r.get_json()['data']['id']
    conn = get_clinic_conn()
    lines = conn.execute(
        "SELECT line_total FROM clinic_invoice_items WHERE invoice_id=?", (inv_id,)).fetchall()
    conn.close()
    row = _invoice_row(inv_id)
    assert round(sum(r_['line_total'] for r_ in lines), 6) == row['subtotal']


def test_a_two_decimal_currency_still_rounds_to_two():
    """The allow-half a JOD-only test cannot see: hardcoding 3 decimals
    instead of 2 would pass every assertion above and be just as wrong for
    the currencies that have 2."""
    client, cid = _make_admin_client()
    _set_currency(cid, 'USD')
    r = client.post('/api/sub/clinic/invoices', json={
        'items': [{'description': 'Consultation', 'qty': 1, 'unit_price': 12.345}],
        'tax_rate': 0.16,
    })
    inv_id = r.get_json()['data']['id']
    row = _invoice_row(inv_id)
    # 12.345 -> 12.35 (ROUND_HALF_UP), tax 12.35*0.16 = 1.976 -> 1.98,
    # total 14.33. All three are what a USD shop should see.
    assert (row['subtotal'], row['tax'], row['total']) == (12.35, 1.98, 14.33)


def test_a_malformed_currency_setting_does_not_block_billing():
    """`currency_quantum`'s never-raise posture, end to end: a clinic that
    cannot bill because someone typoed a currency code would be a far worse
    defect than rounding at the wrong precision."""
    client, cid = _make_admin_client()
    _set_currency(cid, 'NOT-A-CURRENCY')
    r = client.post('/api/sub/clinic/invoices', json={
        'items': [{'description': 'Consultation', 'qty': 1, 'unit_price': 12.345}],
        'tax_rate': 0.16,
    })
    assert r.status_code == 200, r.get_json()
    row = _invoice_row(r.get_json()['data']['id'])
    assert row['total'] == 14.33, 'an unknown code degrades to the 2-decimal default'


# ── record_payment ───────────────────────────────────────────────────────────

def _jod_invoice(client, unit_price=100.0):
    r = client.post('/api/sub/clinic/invoices', json={
        'items': [{'description': 'Service', 'qty': 1, 'unit_price': unit_price}],
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def test_a_fils_payment_is_stored_as_typed_not_rounded_to_cents():
    """The ingest quantization. Before the fix the receptionist typed 12.345
    and 12.35 went into clinic_payments.amount_paid -- 5 fils the patient
    never handed over."""
    client, cid = _make_admin_client()
    inv_id = _jod_invoice(client)
    r = client.post('/api/sub/clinic/payments', json={'invoice_id': inv_id, 'amount': 12.345})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['amount'] == 12.345

    conn = get_clinic_conn()
    stored = conn.execute(
        "SELECT amount_paid FROM clinic_payments WHERE invoice_id=?", (inv_id,)).fetchone()[0]
    invoice_paid = conn.execute(
        "SELECT amount_paid FROM clinic_invoices WHERE id=?", (inv_id,)).fetchone()[0]
    conn.close()
    assert stored == 12.345, f'persisted payment must be what was tendered (got {stored})'
    assert invoice_paid == 12.345
    assert r.get_json()['data']['outstanding_balance'] == 87.655


def test_the_overpayment_message_quotes_the_amount_at_the_right_precision():
    """`f'{amount:.2f}'` rendered a rejected 99.999 JOD payment as '100.00'
    -- an amount the customer never offered, in the one sentence whose whole
    job is to tell them what went wrong."""
    client, cid = _make_admin_client()
    inv_id = _jod_invoice(client, unit_price=50.0)
    r = client.post('/api/sub/clinic/payments', json={'invoice_id': inv_id, 'amount': 99.999})
    assert r.status_code == 400
    message = r.get_json()['message']
    assert '99.999' in message, f'the sentence must quote what was typed; got: {message}'
    assert '100.00 ' not in message, 'a 2dp format renders 99.999 as 100.00 -- an amount nobody offered'
    assert '50.000' in message, 'the outstanding balance must be quoted at the same precision'


def test_a_fils_overpayment_is_still_refused():
    """The deny-half of the tolerance change: widening `+ 0.005` to half a
    FILS must not turn into "close enough, accept it". 100.002 against a
    100.000 invoice is over by two fils and this product has no
    customer-credit ledger."""
    client, cid = _make_admin_client()
    inv_id = _jod_invoice(client)
    r = client.post('/api/sub/clinic/payments', json={'invoice_id': inv_id, 'amount': 100.002})
    assert r.status_code == 400, r.get_json()


def test_paying_the_exact_fils_balance_settles_the_invoice():
    """The allow-half: an invoice whose total carries fils must still be
    settleable to 'paid'. If the tolerance had stayed at half a CENT while
    the amounts moved to fils, this would have stuck at 'partial' forever
    and no clinic could close the account."""
    client, cid = _make_admin_client()
    r = client.post('/api/sub/clinic/invoices', json={
        'items': [{'description': 'Consultation', 'qty': 1, 'unit_price': 12.345}],
        'tax_rate': 0.16,
    })
    inv_id = r.get_json()['data']['id']
    assert _invoice_row(inv_id)['total'] == 14.320

    r = client.post('/api/sub/clinic/payments', json={'invoice_id': inv_id, 'amount': 14.320})
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['invoice_status'] == 'paid'
    assert data['outstanding_balance'] == 0.0


@pytest.mark.parametrize('code,expected_amount', [('USD', 12.35), ('JOD', 12.345)])
def test_payment_precision_follows_the_company_currency(code, expected_amount):
    """One parametrized pair rather than two lookalike tests: the SAME
    tendered value must land differently under the two currencies, which is
    the whole claim. A hardcoded quantum of either size fails one half."""
    client, cid = _make_admin_client()
    _set_currency(cid, code)
    inv_id = _jod_invoice(client)
    r = client.post('/api/sub/clinic/payments', json={'invoice_id': inv_id, 'amount': 12.345})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['amount'] == expected_amount
