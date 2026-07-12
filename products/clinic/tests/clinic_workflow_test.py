"""
Aura Clinic -- core workflow regression suite (Phase 3).

Patients, patient profile, appointments, visits, prescriptions, doctors, lab
expenses, billing/payments, notes, monetary precision, database integrity.

Run:
    pytest products/clinic/tests/clinic_workflow_test.py -v
"""
import json
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

DATA = Path(tempfile.mkdtemp(prefix="aura_clinic_workflow_"))
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
    email = f'wf-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'WorkflowPW1'
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


# ═════════════════════════════════════════════════════════════════════════════
# Patients
# ═════════════════════════════════════════════════════════════════════════════

def test_patient_creation_and_code_format():
    c, cid = _make_admin_client()
    r = c.post('/api/sub/clinic/patients', json={'name': 'Jane Doe', 'phone': '555-0100'})
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['id'] is not None
    assert data['patient_code'].startswith('P-')


def test_patient_search_by_name_and_phone():
    c, cid = _make_admin_client()
    c.post('/api/sub/clinic/patients', json={'name': 'Zaid Uniquename', 'phone': '555-9999'})
    r = c.get('/api/sub/clinic/patients?q=Uniquename')
    names = [p['name'] for p in r.get_json()['data']]
    assert 'Zaid Uniquename' in names
    r2 = c.get('/api/sub/clinic/patients?q=555-9999')
    assert any(p['phone'] == '555-9999' for p in r2.get_json()['data'])


def test_patient_edit():
    c, cid = _make_admin_client()
    r = c.post('/api/sub/clinic/patients', json={'name': 'Edit Target'})
    pid = r.get_json()['data']['id']
    r2 = c.patch(f'/api/sub/clinic/patients/{pid}', json={'phone': '555-1234'})
    assert r2.status_code == 200
    detail = c.get(f'/api/sub/clinic/patients/{pid}').get_json()['data']['patient']
    assert detail['phone'] == '555-1234'


def test_patient_edit_rejects_empty_field_set():
    c, cid = _make_admin_client()
    r = c.post('/api/sub/clinic/patients', json={'name': 'No Fields'})
    pid = r.get_json()['data']['id']
    r2 = c.patch(f'/api/sub/clinic/patients/{pid}', json={'not_a_real_field': 'x'})
    assert r2.status_code == 400


def test_patient_soft_delete_archives_not_removes():
    c, cid = _make_admin_client()
    r = c.post('/api/sub/clinic/patients', json={'name': 'Archive Me'})
    pid = r.get_json()['data']['id']
    r2 = c.delete(f'/api/sub/clinic/patients/{pid}')
    assert r2.status_code == 200
    listed = c.get('/api/sub/clinic/patients').get_json()['data']
    assert not any(p['id'] == pid for p in listed)  # excluded by default (archived)
    listed_with_archived = c.get('/api/sub/clinic/patients?include_archived=1').get_json()['data']
    assert any(p['id'] == pid and p['status'] == 'archived' for p in listed_with_archived)


def test_patient_hard_delete_admin_only_and_blocked_by_billing_history():
    c, cid = _make_admin_client()
    r = c.post('/api/sub/clinic/patients', json={'name': 'Has Invoice'})
    pid = r.get_json()['data']['id']
    c.post('/api/sub/clinic/invoices', json={'patient_id': pid, 'items': [{'qty': 1, 'unit_price': 50}]})
    r2 = c.delete(f'/api/sub/clinic/patients/{pid}?hard=1')
    assert r2.status_code == 409, r2.get_json()


def test_patient_creation_missing_body_does_not_crash():
    """Phase 3 fix regression: an omitted `name` (NOT NULL in schema) used
    to reach the DB as an unhandled IntegrityError, which ALSO leaked the
    connection (see clinic_api.py's create_patient docstring comment) and
    locked the database for the next request. Now returns a clean 400."""
    c, cid = _make_admin_client()
    r = c.post('/api/sub/clinic/patients', json={})
    assert r.status_code == 400
    assert 'detail' not in r.get_json()


# ═════════════════════════════════════════════════════════════════════════════
# Appointments
# ═════════════════════════════════════════════════════════════════════════════

def test_appointment_booking_and_double_booking_conflict():
    c, cid = _make_admin_client()
    p = c.post('/api/sub/clinic/patients', json={'name': 'Appt Patient A'}).get_json()['data']
    p2 = c.post('/api/sub/clinic/patients', json={'name': 'Appt Patient B'}).get_json()['data']
    d = c.post('/api/sub/clinic/doctors', json={'name': 'Dr. Booking Test'}).get_json()['data']

    r1 = c.post('/api/sub/clinic/appointments', json={
        'patient_id': p['id'], 'doctor_id': d['id'], 'appointment_dt': '2026-08-01 10:00', 'reason': 'Checkup',
    })
    assert r1.status_code == 200, r1.get_json()

    r2 = c.post('/api/sub/clinic/appointments', json={
        'patient_id': p2['id'], 'doctor_id': d['id'], 'appointment_dt': '2026-08-01 10:00', 'reason': 'Conflict',
    })
    assert r2.status_code == 409, 'same doctor, same time slot must be rejected'


def test_appointment_checkin_updates_status():
    c, cid = _make_admin_client()
    p = c.post('/api/sub/clinic/patients', json={'name': 'Checkin Patient'}).get_json()['data']
    a = c.post('/api/sub/clinic/appointments', json={
        'patient_id': p['id'], 'appointment_dt': '2026-08-02 09:00',
    }).get_json()['data']
    r = c.post(f'/api/sub/clinic/appointments/{a["id"]}/checkin')
    assert r.status_code == 200
    rows = c.get('/api/sub/clinic/appointments?date=2026-08-02').get_json()['data']
    assert any(x['id'] == a['id'] and x['status'] == 'waiting' for x in rows)


def test_appointment_search_across_dates():
    c, cid = _make_admin_client()
    p = c.post('/api/sub/clinic/patients', json={'name': 'Searchable Patient Xyzzy'}).get_json()['data']
    c.post('/api/sub/clinic/appointments', json={'patient_id': p['id'], 'appointment_dt': '2026-09-01 08:00'})
    r = c.get('/api/sub/clinic/appointments?q=Xyzzy')
    assert any('Xyzzy' in (row.get('patient_name') or '') for row in r.get_json()['data'])


def test_invalid_appointment_handling_missing_patient_id():
    """Phase 3 fix regression: the source had no upfront validation here --
    an omitted patient_id reached the DB's NOT NULL constraint as an
    unhandled IntegrityError, which also leaked the open connection (see
    clinic_api.py's create_appointment docstring comment). Now returns a
    clean 400."""
    c, cid = _make_admin_client()
    r = c.post('/api/sub/clinic/appointments', json={'appointment_dt': '2026-08-05 09:00'})
    assert r.status_code == 400
    conn = get_clinic_conn()
    orphans = conn.execute("SELECT COUNT(*) FROM clinic_appointments WHERE patient_id IS NULL").fetchone()[0]
    conn.close()
    assert orphans == 0


# ═════════════════════════════════════════════════════════════════════════════
# Visits
# ═════════════════════════════════════════════════════════════════════════════

def test_visit_creation_links_appointment_and_patient():
    c, cid = _make_admin_client()
    p = c.post('/api/sub/clinic/patients', json={'name': 'Visit Patient'}).get_json()['data']
    a = c.post('/api/sub/clinic/appointments', json={'patient_id': p['id'], 'appointment_dt': '2026-08-06 09:00'}).get_json()['data']
    v = c.post('/api/sub/clinic/visits', json={'patient_id': p['id'], 'appointment_id': a['id']})
    assert v.status_code == 200
    vid = v.get_json()['data']['id']
    detail = c.get(f'/api/sub/clinic/visits/{vid}').get_json()['data']['visit']
    assert detail['patient_id'] == p['id']
    assert detail['appointment_id'] == a['id']
    # Starting a visit for a booked appointment should progress that appointment.
    appts = c.get('/api/sub/clinic/appointments?date=2026-08-06').get_json()['data']
    assert any(x['id'] == a['id'] and x['status'] == 'in_progress' for x in appts)


def test_visit_history_and_notes():
    c, cid = _make_admin_client()
    p = c.post('/api/sub/clinic/patients', json={'name': 'Notes Patient'}).get_json()['data']
    v = c.post('/api/sub/clinic/visits', json={'patient_id': p['id']}).get_json()['data']
    c.patch(f'/api/sub/clinic/visits/{v["id"]}', json={'diagnosis': 'Common cold'})
    c.post(f'/api/sub/clinic/visits/{v["id"]}/notes', json={'content': 'Rest and fluids advised.'})
    detail = c.get(f'/api/sub/clinic/visits/{v["id"]}').get_json()['data']
    assert detail['visit']['diagnosis'] == 'Common cold'
    assert any(n['content'] == 'Rest and fluids advised.' for n in detail['notes'])


def test_visit_completion_status_transition():
    c, cid = _make_admin_client()
    p = c.post('/api/sub/clinic/patients', json={'name': 'Completion Patient'}).get_json()['data']
    v = c.post('/api/sub/clinic/visits', json={'patient_id': p['id']}).get_json()['data']
    r = c.patch(f'/api/sub/clinic/visits/{v["id"]}', json={'status': 'completed'})
    assert r.status_code == 200
    detail = c.get(f'/api/sub/clinic/visits/{v["id"]}').get_json()['data']['visit']
    assert detail['status'] == 'completed'
    assert detail['completed_at'] is not None


# ═════════════════════════════════════════════════════════════════════════════
# Follow-ups
# ═════════════════════════════════════════════════════════════════════════════

def test_followup_sheet_append_and_list():
    c, cid = _make_admin_client()
    p = c.post('/api/sub/clinic/patients', json={'name': 'Followup Patient'}).get_json()['data']
    r = c.post(f'/api/sub/clinic/patients/{p["id"]}/followups', json={
        'weight': '70kg', 'blood_pressure': '120/80', 'progress': 'Improving',
    })
    assert r.status_code == 200
    rows = c.get(f'/api/sub/clinic/patients/{p["id"]}/followups').get_json()['data']
    assert any(f['progress'] == 'Improving' for f in rows)


# ═════════════════════════════════════════════════════════════════════════════
# Prescriptions -- also verifies the Phase 3 items_json fix
# ═════════════════════════════════════════════════════════════════════════════

def test_prescription_creation_stores_real_json():
    c, cid = _make_admin_client()
    p = c.post('/api/sub/clinic/patients', json={'name': 'Rx Patient'}).get_json()['data']
    items = [{'name': "Children's Tylenol", 'dose': '5ml', 'frequency': 'q6h'}]
    r = c.post('/api/sub/clinic/prescriptions', json={'patient_id': p['id'], 'items': items})
    assert r.status_code == 200, r.get_json()
    prx_id = r.get_json()['data']['id']

    conn = get_clinic_conn()
    row = conn.execute("SELECT items_json FROM clinic_prescriptions WHERE id=?", (prx_id,)).fetchone()
    conn.close()
    # Phase 3 fix regression: must be real, parseable JSON (not a Python
    # repr string) -- and must survive an apostrophe in the medication name,
    # which broke the source's str()-based storage + the frontend's
    # replace(/'/g,'"') workaround.
    parsed = json.loads(row['items_json'])
    assert parsed == items
    assert parsed[0]['name'] == "Children's Tylenol"


def test_prescription_history_list():
    c, cid = _make_admin_client()
    p = c.post('/api/sub/clinic/patients', json={'name': 'Rx History Patient'}).get_json()['data']
    c.post('/api/sub/clinic/prescriptions', json={'patient_id': p['id'], 'items': [{'name': 'Amoxicillin'}]})
    rows = c.get(f'/api/sub/clinic/prescriptions?patient_id={p["id"]}').get_json()['data']
    assert len(rows) == 1


# ═════════════════════════════════════════════════════════════════════════════
# Doctors
# ═════════════════════════════════════════════════════════════════════════════

def test_doctor_creation_requires_name():
    c, cid = _make_admin_client()
    r = c.post('/api/sub/clinic/doctors', json={})
    assert r.status_code == 400


def test_doctor_update_and_specialty():
    c, cid = _make_admin_client()
    d = c.post('/api/sub/clinic/doctors', json={'name': 'Dr. Update', 'specialty': 'Cardiology'}).get_json()['data']
    r = c.patch(f'/api/sub/clinic/doctors/{d["id"]}', json={'specialty': 'Neurology'})
    assert r.status_code == 200
    rows = c.get('/api/sub/clinic/doctors').get_json()['data']
    assert any(x['id'] == d['id'] and x['specialty'] == 'Neurology' for x in rows)


# ═════════════════════════════════════════════════════════════════════════════
# Lab expenses
# ═════════════════════════════════════════════════════════════════════════════

def test_lab_expense_entry_and_precision():
    c, cid = _make_admin_client()
    p = c.post('/api/sub/clinic/patients', json={'name': 'Lab Patient'}).get_json()['data']
    r = c.post('/api/sub/clinic/lab-expenses', json={
        'patient_id': p['id'], 'lab_name': 'CityLab', 'test_name': 'CBC', 'amount': '45.999',
    })
    assert r.status_code == 200
    le_id = r.get_json()['data']['id']
    conn = get_clinic_conn()
    row = conn.execute("SELECT amount FROM clinic_lab_expenses WHERE id=?", (le_id,)).fetchone()
    conn.close()
    assert row['amount'] == pytest.approx(45.999, abs=0.001)


def test_lab_expense_deletion():
    c, cid = _make_admin_client()
    r = c.post('/api/sub/clinic/lab-expenses', json={'lab_name': 'X', 'test_name': 'Y', 'amount': 10})
    le_id = r.get_json()['data']['id']
    r2 = c.delete(f'/api/sub/clinic/lab-expenses/{le_id}')
    assert r2.status_code == 200
    rows = c.get('/api/sub/clinic/lab-expenses').get_json()['data']
    assert not any(x['id'] == le_id for x in rows)


def test_lab_expense_accounting_sync_failure_does_not_block_the_clinic_record():
    """Regression for the intentionally-unresolved Accounting cross-import
    (see clinic-dependency-map.md) -- the lab expense must still be recorded
    even though the accounting sync always ImportErrors in this standalone
    build."""
    c, cid = _make_admin_client()
    r = c.post('/api/sub/clinic/lab-expenses', json={'lab_name': 'CityLab', 'test_name': 'X-Ray', 'amount': 200})
    assert r.status_code == 200, r.get_json()


# ═════════════════════════════════════════════════════════════════════════════
# Billing / payments -- financial precision
# ═════════════════════════════════════════════════════════════════════════════

def test_invoice_creation_totals_and_discount_before_tax():
    c, cid = _make_admin_client()
    p = c.post('/api/sub/clinic/patients', json={'name': 'Billing Patient'}).get_json()['data']
    r = c.post('/api/sub/clinic/invoices', json={
        'patient_id': p['id'],
        'items': [{'qty': 2, 'unit_price': 50}, {'qty': 1, 'unit_price': 25}],
        'discount': 10, 'tax_rate': 0.15,
    })
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    # subtotal = 125, discount = 10 -> taxable = 115, tax = 17.25, total = 132.25
    assert data['total'] == pytest.approx(132.25, abs=0.01)


def test_invoice_discount_cannot_exceed_subtotal():
    c, cid = _make_admin_client()
    p = c.post('/api/sub/clinic/patients', json={'name': 'Overdiscount Patient'}).get_json()['data']
    r = c.post('/api/sub/clinic/invoices', json={
        'patient_id': p['id'], 'items': [{'qty': 1, 'unit_price': 10}], 'discount': 999, 'tax_rate': 0,
    })
    data = r.get_json()['data']
    assert data['total'] == pytest.approx(0.0, abs=0.01)  # clamped, never negative


def test_payment_recording_and_invoice_status_transitions():
    c, cid = _make_admin_client()
    p = c.post('/api/sub/clinic/patients', json={'name': 'Payment Patient'}).get_json()['data']
    inv = c.post('/api/sub/clinic/invoices', json={
        'patient_id': p['id'], 'items': [{'qty': 1, 'unit_price': 100}], 'tax_rate': 0,
    }).get_json()['data']

    r1 = c.post('/api/sub/clinic/payments', json={'invoice_id': inv['id'], 'amount': 40, 'method': 'cash'})
    assert r1.get_json()['data']['invoice_status'] == 'partial'

    r2 = c.post('/api/sub/clinic/payments', json={'invoice_id': inv['id'], 'amount': 60, 'method': 'card'})
    assert r2.get_json()['data']['invoice_status'] == 'paid'

    detail = c.get(f'/api/sub/clinic/invoices/{inv["id"]}').get_json()['data']
    assert len(detail['payments']) == 2
    assert sum(pay['amount_paid'] for pay in detail['payments']) == pytest.approx(100.0, abs=0.01)


def test_invoice_not_found_returns_404_not_500():
    c, cid = _make_admin_client()
    r = c.get('/api/sub/clinic/invoices/999999999')
    assert r.status_code == 404


# ═════════════════════════════════════════════════════════════════════════════
# Dashboard
# ═════════════════════════════════════════════════════════════════════════════

def test_dashboard_stats_shape_and_counts():
    c, cid = _make_admin_client()
    c.post('/api/sub/clinic/patients', json={'name': 'Dashboard Patient'})
    r = c.get('/api/sub/clinic/dashboard/stats')
    assert r.status_code == 200
    data = r.get_json()['data']
    for key in ('today_appointments', 'waiting', 'active_visits', 'today_revenue', 'total_patients'):
        assert key in data
    assert data['total_patients'] >= 1


# ═════════════════════════════════════════════════════════════════════════════
# Database integrity
# ═════════════════════════════════════════════════════════════════════════════

def test_foreign_key_enforcement_on_clinic_connection():
    conn = get_clinic_conn()
    fk_status = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.close()
    assert fk_status == 1


def test_invoice_number_uniqueness_enforced():
    c, cid = _make_admin_client()
    p = c.post('/api/sub/clinic/patients', json={'name': 'Unique Invoice Patient'}).get_json()['data']
    conn = get_clinic_conn()
    # Directly attempt a duplicate invoice_number to prove the UNIQUE
    # constraint from the schema is actually in force (not just app-level
    # convention) -- the API itself avoids collisions via time+random.
    conn.execute(
        "INSERT INTO clinic_invoices (company_id,invoice_number,patient_id,subtotal,tax,total,status,created_by) "
        "VALUES (?,?,?,?,?,?,?,?)", (cid, 'DUPTEST-1', p['id'], 10, 0, 10, 'unpaid', 'test')
    )
    conn.commit()
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO clinic_invoices (company_id,invoice_number,patient_id,subtotal,tax,total,status,created_by) "
            "VALUES (?,?,?,?,?,?,?,?)", (cid, 'DUPTEST-1', p['id'], 20, 0, 20, 'unpaid', 'test')
        )
    conn.close()
