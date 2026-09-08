"""Aura Clinic -- JoFotara e-invoicing (docs/einvoicing/phase1/) regression
safety net.

Mirrors products/retail/tests/retail_einvoicing_regression_test.py -- see
that file's module docstring for the full rationale. Written BEFORE any
e-invoicing feature code exists (Step 3 of this wave's implementation plan).
Response key lists below come directly from the source (clinic_api.py's
create_invoice/get_invoice/record_payment literal jsonify(...) bodies -- all
three are fixed dict literals, not dynamically shaped), and table column
lists come directly from schema.py's CREATE TABLE + already-applied ALTER
statements.

Run:
    python -m pytest products/clinic/tests/clinic_einvoicing_regression_test.py -v
"""
import os
import shutil
import sys
import tempfile
import threading
import uuid
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_clinic_einvoice_regression_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_CLINIC", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from database.schema import get_clinic_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ─── Frozen literals -- see the retail mirror's docstring for the rule on
# editing these. ──────────────────────────────────────────────────────────

# AUDIT (2026-09-08): e-invoicing now defaults ON -- see the retail mirror's
# identical comment on SALE_RESPONSE_KEYS for the full rationale and the
# three consumers verified to tolerate the extra key:
#   1. Desktop (subsystem-clinic.js): _einvoiceInvoiceBlock fetches the
#      e-invoice state by invoice id (a separate GET), never by
#      destructuring the create-invoice response -- unaffected either way.
#   2. Android (android/aura-clinic/.../net/AuraApi.kt's createInvoice ->
#      CreatedResponse, Models.kt's CreatedRow, ApiClient.kt's Retrofit
#      GsonConverterFactory): same Gson behavior as the retail mirror --
#      unmapped JSON fields are silently dropped on parse, not an error.
#   3. commercial_runtime/sync/: replicates DB rows, never this HTTP
#      response body.
CREATE_INVOICE_RESPONSE_KEYS = ['einvoice', 'id', 'invoice_number', 'total']
GET_INVOICE_RESPONSE_KEYS = ['invoice', 'items', 'payments']
RECORD_PAYMENT_RESPONSE_KEYS = [
    'id', 'invoice_id', 'amount', 'total_paid', 'outstanding_balance',
    'invoice_status', 'idempotency_key',
]
CLINIC_INVOICES_TABLE_COLUMNS = [
    'id', 'company_id', 'invoice_number', 'patient_id', 'visit_id',
    'subtotal', 'tax', 'total', 'amount_paid', 'status', 'created_by',
    'created_at', 'discount', 'notes', 'prescription_id', 'accounting_synced',
]
CLINIC_INVOICE_ITEMS_TABLE_COLUMNS = [
    'id', 'invoice_id', 'service_id', 'description', 'qty', 'unit_price', 'line_total',
]
CLINIC_PATIENTS_TABLE_COLUMNS = [
    'id', 'company_id', 'patient_code', 'name', 'dob', 'gender', 'phone',
    'email', 'address', 'blood_type', 'emergency_contact', 'emergency_phone',
    'notes', 'status', 'created_by', 'updated_by', 'created_at', 'updated_at',
]
CLINIC_PAYMENTS_TABLE_COLUMNS = [
    'id', 'company_id', 'invoice_id', 'amount_paid', 'method', 'reference',
    'created_by', 'created_at', 'idempotency_key',
]
BASELINE_THREAD_NAMES = {'MainThread'}


def _make_admin_and_patient():
    email = f"einv-reg-{uuid.uuid4().hex[:10]}@test.local"
    password = "EinvRegPW1"
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()

    cconn = get_clinic_conn()
    cconn.execute(
        "INSERT INTO clinic_patients (company_id,patient_code,name) VALUES (?,?,?)",
        (company_id, f"PT-{uuid.uuid4().hex[:8]}", "Regression Patient"),
    )
    patient_id = cconn.execute(
        "SELECT id FROM clinic_patients WHERE company_id=? ORDER BY id DESC LIMIT 1", (company_id,)
    ).fetchone()[0]
    cconn.commit()
    cconn.close()

    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})
    return client, company_id, patient_id


def _create_invoice(client, patient_id, unit_price=100.0, qty=1, tax_rate=0.0):
    r = client.post('/api/sub/clinic/invoices', json={
        'patient_id': patient_id,
        'items': [{'description': 'Regression Service', 'qty': qty, 'unit_price': unit_price}],
        'tax_rate': tax_rate,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def test_create_invoice_response_keys_unchanged():
    client, cid, patient_id = _make_admin_and_patient()
    data = _create_invoice(client, patient_id)
    assert sorted(data.keys()) == sorted(CREATE_INVOICE_RESPONSE_KEYS)


def test_get_invoice_response_keys_unchanged():
    client, cid, patient_id = _make_admin_and_patient()
    inv = _create_invoice(client, patient_id)
    r = client.get(f"/api/sub/clinic/invoices/{inv['id']}")
    assert r.status_code == 200, r.get_json()
    assert sorted(r.get_json()['data'].keys()) == sorted(GET_INVOICE_RESPONSE_KEYS)


def test_record_payment_response_keys_unchanged():
    client, cid, patient_id = _make_admin_and_patient()
    inv = _create_invoice(client, patient_id, unit_price=100.0)
    r = client.post('/api/sub/clinic/payments', json={
        'invoice_id': inv['id'], 'amount': inv['total'], 'method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    assert sorted(r.get_json()['data'].keys()) == sorted(RECORD_PAYMENT_RESPONSE_KEYS)


def test_no_alter_ran_on_existing_tables():
    conn = get_clinic_conn()
    assert [c[1] for c in conn.execute("PRAGMA table_info(clinic_invoices)").fetchall()] == CLINIC_INVOICES_TABLE_COLUMNS
    assert [c[1] for c in conn.execute("PRAGMA table_info(clinic_invoice_items)").fetchall()] == CLINIC_INVOICE_ITEMS_TABLE_COLUMNS
    assert [c[1] for c in conn.execute("PRAGMA table_info(clinic_patients)").fetchall()] == CLINIC_PATIENTS_TABLE_COLUMNS
    assert [c[1] for c in conn.execute("PRAGMA table_info(clinic_payments)").fetchall()] == CLINIC_PAYMENTS_TABLE_COLUMNS
    conn.close()


def test_einvoice_outbox_gets_exactly_one_row_for_the_invoice_not_the_payment():
    """AUDIT (2026-09-08): renamed from
    test_einvoice_outbox_stays_empty_through_a_full_invoice_and_payment_cycle
    -- see the retail mirror's identical rewrite for the full rationale.
    E-invoicing defaults ON, so the invoice creation below DOES enqueue.
    What's still true: recording a payment never enqueues anything of its
    own -- only create_invoice calls enqueue_invoice (see
    core/clinic/einvoice_adapter.py; clinic_api.py's record_payment has no
    e-invoice/credit-note handling at all), so exactly ONE outbox row must
    exist after an invoice+payment cycle. Scoped to this test's own
    company_id for the same cross-test-leakage reason as the retail
    mirror -- see that file's comment."""
    client, cid, patient_id = _make_admin_and_patient()
    inv = _create_invoice(client, patient_id, unit_price=100.0)
    assert 'einvoice' in inv, "the invoice itself must have enqueued -- e-invoicing defaults ON"
    client.post('/api/sub/clinic/payments', json={
        'invoice_id': inv['id'], 'amount': inv['total'], 'method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    })
    conn = get_clinic_conn()
    rows = conn.execute(
        "SELECT source_type, source_id FROM einvoice_outbox WHERE company_id=?", (cid,)
    ).fetchall()
    conn.close()
    assert len(rows) == 1, "a payment must never enqueue its own e-invoice row (not implemented in Phase 1)"
    assert rows[0][0] == 'clinic_invoice' and rows[0][1] == inv['id'], "the one row must be the invoice itself, not the payment"


def test_no_worker_thread_starts_for_a_company_with_no_enqueued_rows_at_boot():
    """AUDIT (2026-09-08): renamed from
    test_no_background_thread_exists_when_feature_never_enabled -- see the
    retail mirror's identical rewrite for the full rationale. Short form:
    e-invoicing defaults ON, so "never enabled" described no fixture that
    exists here (the test above asserts `'einvoice' in inv`), and the old
    failure message therefore said something untrue about its own setup.

    What is pinned instead is the boot sweep, _resume_einvoicing_workers()
    at app.py:209. It runs once inside init_app() (import time, top of this
    file) over a database that was empty at that moment, so it starts zero
    threads; the invoice below then enqueues a real row and starts nothing,
    because enqueue_invoice writes the outbox and never touches the worker
    registry. Same process-global scope caveat as the retail mirror:
    threading.enumerate() cannot attribute a thread to `cid`."""
    client, cid, patient_id = _make_admin_and_patient()
    _create_invoice(client, patient_id)

    # Anti-vacuity, same as the retail mirror: "no worker started" proves
    # nothing unless the invoice above really did enqueue.
    conn = get_clinic_conn()
    queued = conn.execute(
        "SELECT COUNT(*) c FROM einvoice_outbox WHERE company_id=?", (cid,)
    ).fetchone()['c']
    conn.close()
    assert queued == 1, (
        f"fixture is not exercising the thing under test: {queued} outbox row(s) "
        "for this company."
    )

    names = {t.name for t in threading.enumerate()}
    assert names.issubset(BASELINE_THREAD_NAMES | {'MainThread'}), (
        "an e-invoicing worker thread is running in a process whose boot sweep "
        "(_resume_einvoicing_workers, app.py:209) found an empty outbox: "
        f"{names - BASELINE_THREAD_NAMES}. Either that gate regressed, or "
        "enqueueing now starts a worker -- if the latter is deliberate, rewrite "
        "this test to pin the new rule; do not widen BASELINE_THREAD_NAMES."
    )
