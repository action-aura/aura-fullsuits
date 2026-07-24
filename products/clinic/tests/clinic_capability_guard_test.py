"""Aura Clinic -- Phase 7 Part T capability-guard verification.

Proves @require_license_capability actually blocks/allows the real routes in
clinic_api.py according to docs/licensing/phase7/
clinic-restriction-capability-matrix.md's documented decisions -- not just
the abstract behavior in commercial_runtime/licensing_contracts/
tests/test_flask_guard.py. Every other Clinic test file seeds an
ACTIVE_ONLINE license (see test_support.seed_active_license) specifically so
it never has to think about licensing; this file is the one place that
exercises RESTRICTED/SUSPENDED states against the real business routes.

Run:
    pytest products/clinic/tests/clinic_capability_guard_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_clinic_capability_guard_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository  # noqa: E402
from commercial_runtime.licensing_contracts.events import LicensingEventRecorder  # noqa: E402

# Start ACTIVE_ONLINE -- individual tests flip to RESTRICTED/SUSPENDED as needed.
seed_active_license(str(DATA), product_code="AURA_CLINIC", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _db_path():
    return DATA / "database" / "subsystems" / "licensing.db"


def _set_state(state: str):
    repo = LicenseStateRepository(_db_path())
    record = repo.load()
    record.current_state = state
    repo.save(record)


def _make_admin_client():
    email = f'cap-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'CapabilityPW1'
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


@pytest.fixture
def client_and_company():
    _set_state("ACTIVE_ONLINE")
    return _make_admin_client()


def _seed_patient(c):
    r = c.post('/api/sub/clinic/patients', json={'name': 'Restricted Test Patient', 'phone': '555-0199'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def test_restricted_blocks_new_patient_creation(client_and_company):
    c, _ = client_and_company
    _set_state("RESTRICTED")
    r = c.post('/api/sub/clinic/patients', json={'name': 'Should Be Blocked'})
    assert r.status_code == 403
    body = r.get_json()
    assert body["reason_code"] == "LICENSE_INACTIVE"


def test_restricted_allows_reading_patients(client_and_company):
    c, _ = client_and_company
    pid = _seed_patient(c)
    _set_state("RESTRICTED")
    r = c.get('/api/sub/clinic/patients')
    assert r.status_code == 200
    r2 = c.get(f'/api/sub/clinic/patients/{pid}')
    assert r2.status_code == 200


def test_restricted_blocks_new_appointment(client_and_company):
    c, _ = client_and_company
    pid = _seed_patient(c)
    _set_state("RESTRICTED")
    r = c.post('/api/sub/clinic/appointments', json={'patient_id': pid, 'appointment_dt': '2026-08-01 10:00'})
    assert r.status_code == 403


def test_restricted_allows_completing_an_open_visit(client_and_company):
    c, _ = client_and_company
    pid = _seed_patient(c)
    v = c.post('/api/sub/clinic/visits', json={'patient_id': pid})
    assert v.status_code == 200, v.get_json()
    vid = v.get_json()['data']['id']

    _set_state("RESTRICTED")
    r = c.patch(f'/api/sub/clinic/visits/{vid}', json={'status': 'completed', 'diagnosis': 'Test diagnosis'})
    assert r.status_code == 200, r.get_json()


def test_restricted_allows_prescription_for_in_progress_care(client_and_company):
    c, _ = client_and_company
    pid = _seed_patient(c)
    v = c.post('/api/sub/clinic/visits', json={'patient_id': pid})
    vid = v.get_json()['data']['id']

    _set_state("RESTRICTED")
    r = c.post('/api/sub/clinic/prescriptions', json={'patient_id': pid, 'visit_id': vid, 'items': []})
    assert r.status_code == 200, r.get_json()


def test_restricted_allows_appointment_checkin(client_and_company):
    c, _ = client_and_company
    pid = _seed_patient(c)
    ap = c.post('/api/sub/clinic/appointments', json={'patient_id': pid, 'appointment_dt': '2026-08-01 10:00'})
    aid = ap.get_json()['data']['id']

    _set_state("RESTRICTED")
    r = c.post(f'/api/sub/clinic/appointments/{aid}/checkin')
    assert r.status_code == 200


def test_restricted_blocks_appointment_reschedule(client_and_company):
    c, _ = client_and_company
    pid = _seed_patient(c)
    ap = c.post('/api/sub/clinic/appointments', json={'patient_id': pid, 'appointment_dt': '2026-08-01 10:00'})
    aid = ap.get_json()['data']['id']

    _set_state("RESTRICTED")
    r = c.patch(f'/api/sub/clinic/appointments/{aid}', json={'appointment_dt': '2026-09-01 10:00'})
    assert r.status_code == 403


def test_restricted_blocks_hard_delete_patient(client_and_company):
    c, _ = client_and_company
    pid = _seed_patient(c)
    _set_state("RESTRICTED")
    r = c.delete(f'/api/sub/clinic/patients/{pid}?hard=1')
    assert r.status_code == 403


def test_suspended_blocks_same_as_restricted(client_and_company):
    c, _ = client_and_company
    _set_state("SUSPENDED")
    r = c.post('/api/sub/clinic/patients', json={'name': 'Should Be Blocked'})
    assert r.status_code == 403


def test_capability_denials_are_recorded_as_events(client_and_company):
    c, _ = client_and_company
    _set_state("RESTRICTED")
    c.post('/api/sub/clinic/patients', json={'name': 'x'})
    events = LicensingEventRecorder(_db_path()).recent()
    denied = [e for e in events if e.event_type == "CAPABILITY_DENIED"]
    assert any(e.details.get("capability_code") == "clinic.patient.create" for e in denied)


def test_direct_route_call_with_no_auth_bypass_still_blocked(client_and_company):
    # Confirms the guard fires before the route's own business logic even
    # with a perfectly valid, authenticated session -- there is no separate
    # "UI hides the button" layer being relied on here (Part T).
    c, _ = client_and_company
    _set_state("EXPIRED")
    r = c.post('/api/sub/clinic/payments', json={'invoice_id': 1, 'amount_paid': 10})
    assert r.status_code == 403
    assert r.get_json()["reason_code"] == "LICENSE_INACTIVE"


def test_backup_restore_export_never_blocked_by_license_state(client_and_company):
    # The licensing routes themselves and backup/restore are deliberately
    # NOT capability-guarded at all (Part P/T) -- confirmed here against the
    # real backup blueprint, not just by absence of a decorator in the source.
    c, _ = client_and_company
    _set_state("REVOKED")
    r = c.get('/api/backup/list')
    assert r.status_code != 403
