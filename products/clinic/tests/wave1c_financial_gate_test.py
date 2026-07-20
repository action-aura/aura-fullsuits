"""
Aura Clinic -- Wave 1C release-gate re-audit: independent billing/payment
financial-authority regression suite.

This is a fresh, independently-written verification pass against the
already-shipped Wave 1B backend (commit 157521c / tag
commercial-packaging-wave1b-complete). Every assertion is against the value
the real /api/sub/clinic/invoices and /api/sub/clinic/payments endpoints
actually returned (or the real clinic_invoices/clinic_payments rows), never
hand-derived math asserted in isolation.

Bootstrap pattern, fixtures, and helper shape are deliberately copied from
products/clinic/tests/clinic_payment_wave0_test.py (same isolated
temp-DB-per-process app instance).

Covers 8 gate cases:
  1. Server-computed invoice total is authoritative; a client-submitted
     'total' in the payload is ignored.
  2. Zero payment amount -> rejected.
  3. Negative payment amount -> rejected.
  4. Overpayment beyond invoice balance -> rejected.
  5. Partial payment -> invoice status 'partial' with correct outstanding.
  6. Exact settlement payment -> invoice status becomes 'paid'.
  7. Duplicate payment with same idempotency_key -> only one payment row.
  8. Forced mid-transaction failure during record_payment (monkeypatching
     the module's own _audit() call site, which runs inside the
     BEGIN IMMEDIATE .. COMMIT block right before conn.commit(), the same
     seam the production code already wraps in try/except+rollback) ->
     asserts no partial/corrupt state: invoice stays unpaid/unchanged and
     no orphaned payment row is left behind. The existing test suite
     (clinic_payment_wave0_test.py) has no built-in fault-injection seam
     for this, so this test adds its own via pytest's stock `monkeypatch`
     fixture -- no production code is modified, only the imported function
     reference is swapped for the duration of one test.

Run (own subprocess, per repo convention -- see products/run_all_tests.py):
    python -m pytest products/clinic/tests/wave1c_financial_gate_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_clinic_wave1c_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_clinic_conn  # noqa: E402
import api.clinic_api as clinic_api  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin_client():
    email = f'g1c-{uuid.uuid4().hex[:10]}@test.local'
    password = 'Gate1cPW1'
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


def _make_invoice(client, unit_price=100.0, qty=1, extra=None):
    r = client.post('/api/sub/clinic/patients', json={'name': 'Wave1C Gate Patient'})
    pid = r.get_json()['data']['id']
    payload = {'patient_id': pid, 'items': [{'qty': qty, 'unit_price': unit_price}]}
    if extra:
        payload.update(extra)
    r = client.post('/api/sub/clinic/invoices', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


# ─── Case 1: server-computed invoice total is authoritative ──────────────────

def test_case1_server_computed_total_authoritative_client_total_ignored():
    """Client submits total=1 (and a bogus subtotal/tax) alongside a real
    qty=2 @ $100 line item; the server must compute total=200.00 from the
    line items, not trust the client-submitted total."""
    client, cid = _make_admin_client()
    inv = _make_invoice(client, unit_price=100.0, qty=2, extra={
        'total': 1, 'subtotal': 1, 'tax': 999, 'discount': 0,
    })
    assert inv['total'] == 200.0, "server must ignore client-submitted total=1"

    conn = get_clinic_conn()
    row = conn.execute("SELECT subtotal, discount, tax, total FROM clinic_invoices WHERE id=? AND company_id=?",
                        (inv['id'], cid)).fetchone()
    conn.close()
    assert row['subtotal'] == 200.0
    assert row['total'] == 200.0


# ─── Case 2: zero payment rejected ────────────────────────────────────────────

def test_case2_zero_amount_payment_rejected():
    client, cid = _make_admin_client()
    inv = _make_invoice(client, unit_price=100.0)
    r = client.post('/api/sub/clinic/payments', json={'invoice_id': inv['id'], 'amount': 0.00})
    assert r.status_code == 400, r.get_json()


# ─── Case 3: negative payment rejected ────────────────────────────────────────

def test_case3_negative_amount_payment_rejected():
    client, cid = _make_admin_client()
    inv = _make_invoice(client, unit_price=100.0)
    r = client.post('/api/sub/clinic/payments', json={'invoice_id': inv['id'], 'amount': -25.00})
    assert r.status_code == 400, r.get_json()


# ─── Case 4: overpayment beyond outstanding balance rejected ─────────────────

def test_case4_overpayment_beyond_balance_rejected():
    client, cid = _make_admin_client()
    inv = _make_invoice(client, unit_price=100.0)
    r1 = client.post('/api/sub/clinic/payments', json={'invoice_id': inv['id'], 'amount': 40.00})
    assert r1.status_code == 200, r1.get_json()
    r2 = client.post('/api/sub/clinic/payments', json={'invoice_id': inv['id'], 'amount': 70.00})  # only 60 left
    assert r2.status_code == 400, r2.get_json()
    assert 'outstanding' in r2.get_json()['message'].lower()

    conn = get_clinic_conn()
    total_paid = conn.execute(
        "SELECT COALESCE(SUM(amount_paid),0) FROM clinic_payments WHERE invoice_id=? AND company_id=?",
        (inv['id'], cid)
    ).fetchone()[0]
    conn.close()
    assert total_paid == 40.00, "rejected overpayment must not be recorded"


# ─── Case 5: partial payment -> invoice reflects partial/outstanding ─────────

def test_case5_partial_payment_marks_invoice_partial_with_correct_outstanding():
    client, cid = _make_admin_client()
    inv = _make_invoice(client, unit_price=100.0)
    r = client.post('/api/sub/clinic/payments', json={'invoice_id': inv['id'], 'amount': 35.00})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['invoice_status'] == 'partial'

    conn = get_clinic_conn()
    row = conn.execute("SELECT status, amount_paid, total FROM clinic_invoices WHERE id=? AND company_id=?",
                        (inv['id'], cid)).fetchone()
    conn.close()
    assert row['status'] == 'partial'
    assert row['amount_paid'] == 35.00
    assert round(row['total'] - row['amount_paid'], 2) == 65.00


# ─── Case 6: exact settlement -> invoice becomes fully paid ──────────────────

def test_case6_exact_settlement_marks_invoice_paid():
    client, cid = _make_admin_client()
    inv = _make_invoice(client, unit_price=100.0)
    client.post('/api/sub/clinic/payments', json={'invoice_id': inv['id'], 'amount': 40.00})
    r = client.post('/api/sub/clinic/payments', json={'invoice_id': inv['id'], 'amount': 60.00})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['invoice_status'] == 'paid'

    conn = get_clinic_conn()
    row = conn.execute("SELECT status, amount_paid FROM clinic_invoices WHERE id=? AND company_id=?",
                        (inv['id'], cid)).fetchone()
    conn.close()
    assert row['status'] == 'paid'
    assert row['amount_paid'] == 100.00


# ─── Case 7: duplicate idempotency key -> only one payment recorded ──────────

def test_case7_duplicate_idempotency_key_records_only_one_payment():
    client, cid = _make_admin_client()
    inv = _make_invoice(client, unit_price=100.0)
    key = str(uuid.uuid4())
    payload = {'invoice_id': inv['id'], 'amount': 40.00, 'idempotency_key': key}
    r1 = client.post('/api/sub/clinic/payments', json=payload)
    r2 = client.post('/api/sub/clinic/payments', json=payload)
    r3 = client.post('/api/sub/clinic/payments', json=payload)
    assert r1.status_code == 200 and r2.status_code == 200 and r3.status_code == 200
    id1, id2, id3 = (r.get_json()['data']['id'] for r in (r1, r2, r3))
    assert id1 == id2 == id3

    conn = get_clinic_conn()
    payment_count = conn.execute(
        "SELECT COUNT(*) FROM clinic_payments WHERE invoice_id=? AND company_id=? AND idempotency_key=?",
        (inv['id'], cid, key)
    ).fetchone()[0]
    total_paid = conn.execute(
        "SELECT COALESCE(SUM(amount_paid),0) FROM clinic_payments WHERE invoice_id=? AND company_id=?",
        (inv['id'], cid)
    ).fetchone()[0]
    conn.close()
    assert payment_count == 1, "triple-submit must not create duplicate payment rows"
    assert total_paid == 40.00, "not 80.00 or 120.00"


# ─── Case 8: forced mid-transaction failure leaves no partial/corrupt state ──

def test_case8_forced_failure_mid_transaction_leaves_no_partial_state():
    """Force record_payment's own _audit() call (which happens inside its
    BEGIN IMMEDIATE block, after the payment INSERT and the invoice UPDATE,
    right before conn.commit()) to raise. Production code already wraps
    this whole block in try/except -> conn.rollback(); conn.close(); 500 --
    this test proves that seam actually holds: the payment INSERT and the
    invoice status UPDATE that happened earlier in the same uncommitted
    transaction must be rolled back together, not partially applied."""
    client, cid = _make_admin_client()
    inv = _make_invoice(client, unit_price=100.0)

    def _boom(conn, action, entity, entity_id, details=''):
        if action == 'PaymentRecorded':
            raise RuntimeError('wave1c injected failure: simulated mid-transaction crash')
        # any other _audit call (e.g. from invoice creation) behaves normally
        try:
            conn.execute(
                'INSERT INTO clinic_audit_log (company_id,user_id,action,entity,entity_id,details) VALUES (?,?,?,?,?,?)',
                (cid, 'system', action, entity, entity_id, details)
            )
        except Exception:
            pass

    original_audit = clinic_api._audit
    clinic_api._audit = _boom
    try:
        r = client.post('/api/sub/clinic/payments', json={'invoice_id': inv['id'], 'amount': 100.00})
    finally:
        clinic_api._audit = original_audit

    assert r.status_code == 500, r.get_json()

    conn = get_clinic_conn()
    row = conn.execute("SELECT status, amount_paid FROM clinic_invoices WHERE id=? AND company_id=?",
                        (inv['id'], cid)).fetchone()
    orphaned_payments = conn.execute(
        "SELECT COUNT(*) FROM clinic_payments WHERE invoice_id=? AND company_id=?",
        (inv['id'], cid)
    ).fetchone()[0]
    conn.close()

    assert row['status'] == 'unpaid', "invoice must not be marked paid/partial by a rolled-back transaction"
    assert row['amount_paid'] in (0, 0.0, None), "amount_paid must not reflect the rolled-back payment"
    assert orphaned_payments == 0, "no orphaned clinic_payments row from the failed transaction"

    # And the invoice must still be payable normally afterwards -- the
    # forced failure must not have wedged the row (e.g. left it locked or
    # left a phantom already_paid balance behind).
    r2 = client.post('/api/sub/clinic/payments', json={'invoice_id': inv['id'], 'amount': 100.00})
    assert r2.status_code == 200, r2.get_json()
    assert r2.get_json()['data']['invoice_status'] == 'paid'
