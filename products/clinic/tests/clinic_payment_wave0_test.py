"""
Aura Clinic -- payment recording regression suite (Wave 0, AUDIT-011/012/018).

Worked example for a $100 invoice (0% tax, no discount):
  amount=0.00   -> rejected (must be > 0)
  amount=-10.00 -> rejected (must be > 0)
  amount=40.00  -> accepted, invoice status becomes 'partial'
  amount=40.00 again, same idempotency_key -> returns the ORIGINAL payment,
                 total paid stays 40.00 (not 80.00)
  amount=70.00  -> rejected: exceeds the 60.00 outstanding balance (this
                 product has no customer-credit ledger, so overpayment is
                 not accepted)
  amount=60.00  -> accepted, invoice status becomes 'paid'

See docs/corrections/wave0/clinic-payment-correction.md.

Run:
    pytest products/clinic/tests/clinic_payment_wave0_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_clinic_payment_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_clinic_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin_client():
    email = f'pay-{uuid.uuid4().hex[:10]}@test.local'
    password = 'PaymentPW1'
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


def _make_100_dollar_invoice(client):
    r = client.post('/api/sub/clinic/patients', json={'name': 'Payment Test Patient'})
    pid = r.get_json()['data']['id']
    r = client.post('/api/sub/clinic/invoices', json={
        'patient_id': pid, 'items': [{'qty': 1, 'unit_price': 100}],
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def test_zero_amount_rejected():
    client, cid = _make_admin_client()
    inv_id = _make_100_dollar_invoice(client)
    r = client.post('/api/sub/clinic/payments', json={'invoice_id': inv_id, 'amount': 0.00})
    assert r.status_code == 400


def test_negative_amount_rejected():
    client, cid = _make_admin_client()
    inv_id = _make_100_dollar_invoice(client)
    r = client.post('/api/sub/clinic/payments', json={'invoice_id': inv_id, 'amount': -10.00})
    assert r.status_code == 400


def test_partial_payment_accepted_and_marks_invoice_partial():
    client, cid = _make_admin_client()
    inv_id = _make_100_dollar_invoice(client)
    r = client.post('/api/sub/clinic/payments', json={'invoice_id': inv_id, 'amount': 40.00})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['invoice_status'] == 'partial'


def test_duplicate_idempotency_key_returns_original_payment_only():
    client, cid = _make_admin_client()
    inv_id = _make_100_dollar_invoice(client)
    key = str(uuid.uuid4())
    r1 = client.post('/api/sub/clinic/payments', json={'invoice_id': inv_id, 'amount': 40.00, 'idempotency_key': key})
    r2 = client.post('/api/sub/clinic/payments', json={'invoice_id': inv_id, 'amount': 40.00, 'idempotency_key': key})
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.get_json()['data']['id'] == r2.get_json()['data']['id']

    conn = get_clinic_conn()
    total_paid = conn.execute(
        "SELECT COALESCE(SUM(amount_paid),0) FROM clinic_payments WHERE invoice_id=? AND company_id=?", (inv_id, cid)
    ).fetchone()[0]
    conn.close()
    assert total_paid == 40.00  # not 80.00


def test_overpayment_beyond_outstanding_rejected():
    client, cid = _make_admin_client()
    inv_id = _make_100_dollar_invoice(client)
    client.post('/api/sub/clinic/payments', json={'invoice_id': inv_id, 'amount': 40.00})
    r = client.post('/api/sub/clinic/payments', json={'invoice_id': inv_id, 'amount': 70.00})
    assert r.status_code == 400
    assert 'outstanding' in r.get_json()['message'].lower()


def test_exact_remaining_balance_settles_invoice_as_paid():
    client, cid = _make_admin_client()
    inv_id = _make_100_dollar_invoice(client)
    client.post('/api/sub/clinic/payments', json={'invoice_id': inv_id, 'amount': 40.00})
    r = client.post('/api/sub/clinic/payments', json={'invoice_id': inv_id, 'amount': 60.00})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['invoice_status'] == 'paid'


def test_payment_against_nonexistent_invoice_rejected():
    client, cid = _make_admin_client()
    r = client.post('/api/sub/clinic/payments', json={'invoice_id': 999999999, 'amount': 10.00})
    assert r.status_code == 404


def test_non_numeric_amount_rejected():
    client, cid = _make_admin_client()
    inv_id = _make_100_dollar_invoice(client)
    r = client.post('/api/sub/clinic/payments', json={'invoice_id': inv_id, 'amount': 'not-a-number'})
    assert r.status_code == 400


def test_invoice_creation_is_atomic_no_header_without_lines():
    """A failing line item (invalid type for qty/unit_price) must not leave
    a headerless-but-orphaned invoice row -- the whole insert is one
    transaction (AUDIT-018)."""
    client, cid = _make_admin_client()
    r = client.post('/api/sub/clinic/patients', json={'name': 'Atomic Test Patient'})
    pid = r.get_json()['data']['id']

    conn = get_clinic_conn()
    before = conn.execute("SELECT COUNT(*) FROM clinic_invoices WHERE company_id=?", (cid,)).fetchone()[0]
    conn.close()

    r = client.post('/api/sub/clinic/invoices', json={
        'patient_id': pid,
        'items': [{'qty': 'not-a-number', 'unit_price': 100}],
    })
    assert r.status_code == 500

    conn = get_clinic_conn()
    after = conn.execute("SELECT COUNT(*) FROM clinic_invoices WHERE company_id=?", (cid,)).fetchone()[0]
    conn.close()
    assert after == before, "a failed invoice creation must not leave a partial header row"
