"""
Aura Clinic -- RBAC enforcement suite (Phase 3).

Verifies the REAL binary access model documented in
docs/security/clinic-rbac-matrix.md: admin (global bypass), doctor
(session['clinic_role']=='doctor', 5 gated routes), secretary (everyone
else -- front-desk actions ungated beyond subsystem access).

Run:
    pytest products/clinic/tests/clinic_rbac_test.py -v
"""
import os
import re
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

DATA = Path(tempfile.mkdtemp(prefix="aura_clinic_rbac_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_CLINIC_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin_client():
    """Direct-insert an admin for a NEW company, then log in. `create_admin`
    (the onboarding wizard) allows only one admin per installation ever, so
    RBAC tests -- which need many independent companies -- provision admins
    the same way a second real admin never gets created in this product:
    directly in the registry, matching Retail's test pattern."""
    email = f'admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'AdminPW123'
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


def _make_staff_client(admin_client, clinic_role):
    """Create a staff account with the given clinic_role via the admin's
    create-employee + invite-link flow (the real, non-backdoor account
    creation path), then log them in on a fresh client."""
    email = f'{clinic_role or "staff"}-{uuid.uuid4().hex[:8]}@test.local'
    # mt_require_subsystem('clinic') denies any non-admin with no
    # user_permissions row for 'clinic' (or access_level=='none') -- grant
    # front-desk/clinical access explicitly, same as a real admin would when
    # inviting clinic staff.
    r = admin_client.post('/api/admin/employees', json={
        'email': email, 'clinic_role': clinic_role, 'permissions': {'clinic': 'full'},
    })
    assert r.status_code == 200, r.get_json()
    setup_url = r.get_json()['setup_link']
    token = re.search(r'/#setup/([0-9a-f]+)', setup_url).group(1)

    setup_client = app.test_client()
    password = 'StaffPW123'
    r2 = setup_client.post('/api/auth/employee/setup', json={'token': token, 'password': password})
    assert r2.status_code == 200, r2.get_json()

    login_client = app.test_client()
    r3 = login_client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r3.status_code == 200, r3.get_json()
    return login_client


DOCTOR_ONLY_ROUTES = [
    ('PATCH', '/api/sub/clinic/visits/999999', {'status': 'completed'}),
    ('POST', '/api/sub/clinic/visits/999999/notes', {'content': 'x'}),
    ('POST', '/api/sub/clinic/patients/999999/followups', {'notes': 'x'}),
    ('DELETE', '/api/sub/clinic/followups/999999', None),
    ('POST', '/api/sub/clinic/prescriptions', {'patient_id': 999999, 'items': []}),
]


def _call(client, method, url, json_body):
    fn = getattr(client, method.lower())
    return fn(url, json=json_body) if json_body is not None else fn(url)


# ═════════════════════════════════════════════════════════════════════════════
# 1. Secretary cannot perform doctor-only medical actions
# ═════════════════════════════════════════════════════════════════════════════

def test_secretary_blocked_from_all_doctor_only_routes():
    admin, _cid = _make_admin_client()
    secretary = _make_staff_client(admin, 'secretary')
    for method, url, body in DOCTOR_ONLY_ROUTES:
        r = _call(secretary, method, url, body)
        assert r.status_code == 403, f'{method} {url} should be 403 for secretary, got {r.status_code}'


def test_blank_clinic_role_also_blocked_from_doctor_only_routes():
    """A staff account with no clinic_role at all (front-desk default) is
    treated the same as 'secretary' -- not implicitly trusted as a doctor."""
    admin, _cid = _make_admin_client()
    blank = _make_staff_client(admin, '')
    for method, url, body in DOCTOR_ONLY_ROUTES:
        r = _call(blank, method, url, body)
        assert r.status_code == 403


def test_secretary_can_perform_front_desk_actions():
    admin, _cid = _make_admin_client()
    secretary = _make_staff_client(admin, 'secretary')
    r = secretary.post('/api/sub/clinic/patients', json={'name': 'Front Desk Created Patient'})
    assert r.status_code == 200, r.get_json()
    r2 = secretary.get('/api/sub/clinic/patients')
    assert r2.status_code == 200
    r3 = secretary.post('/api/sub/clinic/doctors', json={'name': 'Dr. Test'})
    assert r3.status_code == 200


# ═════════════════════════════════════════════════════════════════════════════
# 2. Doctor cannot access admin-only user management
# ═════════════════════════════════════════════════════════════════════════════

def test_doctor_cannot_access_admin_endpoints():
    admin, _cid = _make_admin_client()
    doctor = _make_staff_client(admin, 'doctor')
    assert doctor.get('/api/admin/employees').status_code == 403
    assert doctor.post('/api/admin/employees', json={'email': 'x@x.com'}).status_code == 403
    assert doctor.get('/api/admin/audit').status_code == 403
    assert doctor.get('/api/admin/stats').status_code == 403


def test_doctor_can_perform_doctor_only_actions():
    admin, cid = _make_admin_client()
    doctor = _make_staff_client(admin, 'doctor')
    pr = doctor.post('/api/sub/clinic/patients', json={'name': 'Doctor Test Patient'})
    pid = pr.get_json()['data']['id']
    vr = doctor.post('/api/sub/clinic/visits', json={'patient_id': pid})
    vid = vr.get_json()['data']['id']
    r = doctor.patch(f'/api/sub/clinic/visits/{vid}', json={'diagnosis': 'Test dx'})
    assert r.status_code == 200
    rx = doctor.post('/api/sub/clinic/prescriptions', json={'patient_id': pid, 'items': [{'name': 'Amoxicillin', 'dose': '500mg'}]})
    assert rx.status_code == 200, rx.get_json()


# ═════════════════════════════════════════════════════════════════════════════
# 3. Unauthorized direct API calls are rejected (no session at all)
# ═════════════════════════════════════════════════════════════════════════════

def test_unauthenticated_direct_calls_rejected_for_every_area():
    with app.test_client() as c:
        for method, url, body in DOCTOR_ONLY_ROUTES:
            r = _call(c, method, url, body)
            assert r.status_code == 401
        assert c.get('/api/sub/clinic/patients').status_code == 401
        assert c.get('/api/sub/clinic/appointments').status_code == 401
        assert c.get('/api/admin/employees').status_code == 403 or c.get('/api/admin/employees').status_code == 401


# ═════════════════════════════════════════════════════════════════════════════
# 4. A logged-out user cannot access protected Clinic endpoints
# ═════════════════════════════════════════════════════════════════════════════

def test_logout_then_access_is_rejected():
    admin, _cid = _make_admin_client()
    secretary = _make_staff_client(admin, 'secretary')
    assert secretary.get('/api/sub/clinic/patients').status_code == 200
    secretary.post('/api/auth/logout')
    r = secretary.get('/api/sub/clinic/patients')
    assert r.status_code == 401


# ═════════════════════════════════════════════════════════════════════════════
# 5. Role changes take effect
# ═════════════════════════════════════════════════════════════════════════════

def test_promoting_secretary_to_doctor_unlocks_doctor_routes_on_relogin():
    admin, cid = _make_admin_client()
    email = f'promote-{uuid.uuid4().hex[:8]}@test.local'
    r = admin.post('/api/admin/employees', json={
        'email': email, 'clinic_role': 'secretary', 'permissions': {'clinic': 'full'},
    })
    setup_url = r.get_json()['setup_link']
    token = re.search(r'/#setup/([0-9a-f]+)', setup_url).group(1)
    app.test_client().post('/api/auth/employee/setup', json={'token': token, 'password': 'PromotePW1'})

    user_id = registry_conn().execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()[0]
    r2 = admin.put(f'/api/admin/employees/{user_id}/clinic-role', json={'clinic_role': 'doctor'})
    assert r2.status_code == 200

    # Session state is only re-read at login, so the effect is verified on a
    # fresh login (matches the real client behavior: the browser re-logs-in
    # or the session naturally refreshes clinic_role from the DB at login).
    login_client = app.test_client()
    login_client.post('/api/auth/login', json={'email': email, 'password': 'PromotePW1'})
    pr = login_client.post('/api/sub/clinic/patients', json={'name': 'Promoted Doctor Patient'})
    pid = pr.get_json()['data']['id']
    r3 = login_client.post('/api/sub/clinic/prescriptions', json={'patient_id': pid, 'items': []})
    assert r3.status_code == 200


def test_clinic_role_change_rejects_invalid_value():
    admin, _cid = _make_admin_client()
    secretary = _make_staff_client(admin, 'secretary')
    user_id = registry_conn().execute(
        "SELECT id FROM users WHERE company_id=? AND role='employee'", (_cid,)
    ).fetchone()[0]
    r = admin.put(f'/api/admin/employees/{user_id}/clinic-role', json={'clinic_role': 'wizard'})
    assert r.status_code == 400


# ═════════════════════════════════════════════════════════════════════════════
# 6. Disabled users cannot continue using stale authorization
# ═════════════════════════════════════════════════════════════════════════════

def test_disabled_user_rejected_on_next_request():
    admin, cid = _make_admin_client()
    secretary = _make_staff_client(admin, 'secretary')
    assert secretary.get('/api/sub/clinic/patients').status_code == 200

    user_id = registry_conn().execute(
        "SELECT id FROM users WHERE company_id=? AND role='employee'", (cid,)
    ).fetchone()[0]
    r = admin.put(f'/api/admin/employees/{user_id}/status', json={'status': 'disabled'})
    assert r.status_code == 200

    r2 = secretary.get('/api/sub/clinic/patients')
    assert r2.status_code == 401, 'a disabled account must be rejected on its next request, not just at next login'


# ═════════════════════════════════════════════════════════════════════════════
# 7. One Clinic installation cannot reference another installation's context
# ═════════════════════════════════════════════════════════════════════════════

def test_cross_company_patient_isolation():
    admin_a, cid_a = _make_admin_client()
    admin_b, cid_b = _make_admin_client()
    admin_a.post('/api/sub/clinic/patients', json={'name': 'Company A Patient'})
    admin_b.post('/api/sub/clinic/patients', json={'name': 'Company B Patient'})

    names_a = [p['name'] for p in admin_a.get('/api/sub/clinic/patients').get_json()['data']]
    names_b = [p['name'] for p in admin_b.get('/api/sub/clinic/patients').get_json()['data']]
    assert 'Company A Patient' in names_a
    assert 'Company B Patient' not in names_a
    assert 'Company B Patient' in names_b
    assert 'Company A Patient' not in names_b


def test_demo_wipe_scoped_to_own_company_only():
    """Phase 3 hardening regression: confirms the fixed demo-wipe no longer
    deletes other tenants' data (see clinic-source-inventory.md defect #5)."""
    os.environ['AURA_CLINIC_DEMO_MODE'] = '1'
    try:
        admin_a, cid_a = _make_admin_client()
        admin_b, cid_b = _make_admin_client()
        admin_a.post('/api/sub/clinic/patients', json={'name': 'A Wipe Target'})
        admin_b.post('/api/sub/clinic/patients', json={'name': 'B Should Survive'})

        r = admin_a.delete('/api/sub/clinic/demo-wipe', json={'confirm': f'WIPE-{cid_a}'})
        assert r.status_code == 200, r.get_json()

        a_count = admin_a.get('/api/sub/clinic/patients').get_json()['data']
        b_count = admin_b.get('/api/sub/clinic/patients').get_json()['data']
        assert a_count == []
        assert any(p['name'] == 'B Should Survive' for p in b_count)
    finally:
        os.environ.pop('AURA_CLINIC_DEMO_MODE', None)


def test_demo_wipe_unavailable_when_demo_mode_off():
    os.environ.pop('AURA_CLINIC_DEMO_MODE', None)
    admin, cid = _make_admin_client()
    r = admin.delete('/api/sub/clinic/demo-wipe', json={'confirm': f'WIPE-{cid}'})
    assert r.status_code == 404


def test_demo_wipe_denied_to_non_admin_even_in_demo_mode():
    os.environ['AURA_CLINIC_DEMO_MODE'] = '1'
    try:
        admin, cid = _make_admin_client()
        secretary = _make_staff_client(admin, 'secretary')
        r = secretary.delete('/api/sub/clinic/demo-wipe', json={'confirm': f'WIPE-{cid}'})
        assert r.status_code == 403
    finally:
        os.environ.pop('AURA_CLINIC_DEMO_MODE', None)


def test_demo_wipe_requires_confirmation_token():
    os.environ['AURA_CLINIC_DEMO_MODE'] = '1'
    try:
        admin, cid = _make_admin_client()
        r = admin.delete('/api/sub/clinic/demo-wipe', json={})
        assert r.status_code == 400
    finally:
        os.environ.pop('AURA_CLINIC_DEMO_MODE', None)
