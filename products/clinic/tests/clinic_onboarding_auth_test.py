"""
Aura Clinic -- onboarding + authentication suite (Phase 3).

Covers Definition of Done items 3/4/5: clean onboarding works, no hardcoded
account or demo login exists, clean database initialization works.

Important design note: `create_admin` allows exactly ONE real admin per
installation's registry.db (`SELECT ... FROM users WHERE role='admin' LIMIT 1`
is NOT company-scoped -- this matches the standalone product model, one
install = one company/one onboarding). So only ONE test in this module
exercises the actual onboarding-wizard HTTP flow end-to-end
(`test_full_onboarding_flow_creates_first_admin_and_blocks_a_second`); every
other login/session/logout test creates its OWN additional user via direct
registry-DB insertion (the same pattern Retail's security tests use), since
that's how a real second employee account is created in this product too
(admin -> create-employee -> invite link -> employee/setup), not by
re-running the onboarding wizard.

Run:
    pytest products/clinic/tests/clinic_onboarding_auth_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_clinic_onboarding_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_CLINIC", platform="WINDOWS")
os.environ.pop("AURA_CLINIC_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_clinic_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_company_user(email, password, role='employee', status='active'):
    """Direct-insert helper for tests that need *a* working account without
    going through the once-only onboarding wizard (mirrors Retail's
    tests/retail_security_test.py::_make_company_user)."""
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, "EMP-0001", email, hash_password(password), role, status),
    )
    conn.commit()
    conn.close()
    return user_id, company_id


# ═════════════════════════════════════════════════════════════════════════════
# Clean database initialization (checked BEFORE any test creates a user)
# ═════════════════════════════════════════════════════════════════════════════

def test_clean_clinic_db_has_zero_patient_records():
    conn = get_clinic_conn()
    for table in ('clinic_patients', 'clinic_appointments', 'clinic_visits',
                  'clinic_prescriptions', 'clinic_invoices', 'clinic_payments'):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count == 0, f"{table} should be empty on a clean standalone install, found {count} rows"
    conn.close()


def test_clean_clinic_db_has_zero_doctors_before_onboarding():
    conn = get_clinic_conn()
    count = conn.execute("SELECT COUNT(*) FROM clinic_doctors").fetchone()[0]
    assert count == 0
    conn.close()


def test_clean_registry_db_has_zero_users():
    conn = registry_conn()
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    assert count == 0, "a fresh install must never ship with a seeded/demo account"
    conn.close()


def test_no_hardcoded_credentials_anywhere_in_extracted_backend():
    """Static guard: scan the extracted Clinic backend and shared identity
    code for the historical hardcoded-backdoor literal and any obvious
    hardcoded password/credential assignment."""
    backend_src = "\n".join(
        p.read_text(encoding='utf-8') for p in BACKEND_DIR.rglob('*.py')
    )
    shared_src = "\n".join(
        p.read_text(encoding='utf-8') for p in (SUITE_ROOT / 'commercial_runtime').rglob('*.py')
    )
    combined = backend_src + shared_src
    assert "baha.aura@admin" not in combined
    assert "bahaa123" not in combined
    assert "pass123" not in combined
    assert "aura-enterprise-secret-2025-xK9mP2vL" not in combined


def test_onboarding_status_needs_setup_when_no_admin():
    with app.test_client() as c:
        r = c.get('/api/onboarding/status')
        assert r.status_code == 200
        assert r.get_json()['needs_setup'] is True


# ═════════════════════════════════════════════════════════════════════════════
# The ONE real onboarding-wizard flow this installation ever runs
# ═════════════════════════════════════════════════════════════════════════════

_FIRST_ADMIN_EMAIL = None
_FIRST_ADMIN_PASSWORD = 'RealPassword1'


def test_full_onboarding_flow_creates_first_admin_and_blocks_a_second():
    global _FIRST_ADMIN_EMAIL
    email = f'first-admin-{uuid.uuid4().hex[:8]}@test.local'

    with app.test_client() as c:
        r = c.post('/api/onboarding/create-admin', json={})
        assert r.status_code == 400  # missing email/password

        r2 = c.post('/api/onboarding/create-admin', json={'email': 'short@test.local', 'password': '123'})
        assert r2.status_code == 400  # too short

        r3 = c.post('/api/onboarding/create-admin', json={
            'email': email, 'password': _FIRST_ADMIN_PASSWORD, 'company_name': 'Test Clinic',
        })
        assert r3.status_code == 200, r3.get_json()
        data = r3.get_json()
        assert data['success'] is True
        assert data['user']['role'] == 'admin'
        with c.session_transaction() as s:
            assert s.get('mt_user_id') is not None
            assert s.get('mt_role') == 'admin'

    _FIRST_ADMIN_EMAIL = email

    with app.test_client() as c2:
        assert c2.get('/api/onboarding/status').get_json()['needs_setup'] is False

    with app.test_client() as c3:
        r4 = c3.post('/api/onboarding/create-admin', json={
            'email': f'second-{uuid.uuid4().hex[:8]}@test.local', 'password': 'AnotherPW1',
        })
        assert r4.status_code == 409


def test_complete_onboarding_requires_admin_session():
    with app.test_client() as c:
        r = c.post('/api/onboarding/complete')
        assert r.status_code == 403


def test_login_with_the_onboarded_admin_account():
    assert _FIRST_ADMIN_EMAIL is not None, 'onboarding flow test must run first'
    with app.test_client() as c:
        r = c.post('/api/auth/login', json={'email': _FIRST_ADMIN_EMAIL, 'password': _FIRST_ADMIN_PASSWORD})
        assert r.status_code == 200
        assert r.get_json()['success'] is True


def test_session_endpoint_reflects_the_onboarded_admin():
    assert _FIRST_ADMIN_EMAIL is not None
    with app.test_client() as c:
        c.post('/api/auth/login', json={'email': _FIRST_ADMIN_EMAIL, 'password': _FIRST_ADMIN_PASSWORD})
        r = c.get('/api/auth/session')
        data = r.get_json()
        assert data['authenticated'] is True
        assert data['user']['email'] == _FIRST_ADMIN_EMAIL
        assert data['user']['role'] == 'admin'


# ═════════════════════════════════════════════════════════════════════════════
# Login / session / logout with directly-provisioned (non-onboarding) accounts
# ═════════════════════════════════════════════════════════════════════════════

def test_random_unknown_credential_rejected():
    with app.test_client() as c:
        r = c.post('/api/auth/login', json={'email': 'nobody@nowhere.test', 'password': 'whatever123'})
        assert r.status_code == 401


def test_no_auth_path_grants_privilege_without_stored_account():
    with app.test_client() as c:
        r = c.get('/api/sub/clinic/dashboard/stats')
        assert r.status_code == 401


def test_logout_invalidates_session():
    email = f'logout-{uuid.uuid4().hex[:8]}@test.local'
    _make_company_user(email, 'LogoutPW1', role='admin')
    with app.test_client() as c:
        c.post('/api/auth/login', json={'email': email, 'password': 'LogoutPW1'})
        c.post('/api/auth/logout')
        r = c.get('/api/sub/clinic/dashboard/stats')
        assert r.status_code == 401


def test_disabled_account_cannot_login():
    email = f'disabled-{uuid.uuid4().hex[:8]}@test.local'
    _make_company_user(email, 'DisabledPW1', role='admin', status='disabled')
    with app.test_client() as c:
        r = c.post('/api/auth/login', json={'email': email, 'password': 'DisabledPW1'})
        assert r.status_code == 403


def test_employee_setup_rejects_password_shorter_than_six_chars():
    """Regression: employee_setup() (the self-service invite-link password
    form) used to enforce no minimum password length at all, unlike
    create_admin() which requires 6+ characters -- hash_password() itself
    only rejects an EMPTY string, so a 1-character password sailed straight
    through and got hashed/stored. Exercises the real end-to-end flow
    (admin -> create-employee -> invite link -> employee/setup) rather than
    inserting a row directly, so it proves the HTTP-layer policy gate."""
    admin_email = f'setup-admin-{uuid.uuid4().hex[:8]}@test.local'
    _make_company_user(admin_email, 'SetupAdminPW1', role='admin')
    admin = app.test_client()
    r = admin.post('/api/auth/login', json={'email': admin_email, 'password': 'SetupAdminPW1'})
    assert r.status_code == 200, r.get_json()

    staff_email = f'weak-setup-{uuid.uuid4().hex[:8]}@test.local'
    r2 = admin.post('/api/admin/employees', json={'email': staff_email})
    assert r2.status_code == 200, r2.get_json()
    setup_url = r2.get_json()['setup_link']
    token = re.search(r'/#setup/([0-9a-f]+)', setup_url).group(1)

    setup_client = app.test_client()
    r3 = setup_client.post('/api/auth/employee/setup', json={'token': token, 'password': 'x'})
    assert r3.status_code == 400, r3.get_json()

    # The rejected request must not have logged the employee in with a
    # 1-character password.
    r4 = app.test_client().post('/api/auth/login', json={'email': staff_email, 'password': 'x'})
    assert r4.status_code in (401, 403)


def test_employee_setup_strips_whitespace_from_password_like_login_does():
    """Regression: employee_setup() used to hash the submitted password
    exactly as-is (no .strip()), while login() always strips the submitted
    password before verifying it. An invite-link password with incidental
    leading/trailing whitespace (common with clipboard-copied generated
    passwords or mobile-keyboard auto-space) would get baked verbatim into
    the stored hash, but every future login attempt strips first -- so the
    account could never authenticate again, even re-entering the exact
    original string. Exercises the real end-to-end flow (admin ->
    create-employee -> invite link -> employee/setup) so it proves the
    HTTP-layer strip behavior actually matches login()'s, not just that
    hash_password()/verify_password() agree in isolation."""
    admin_email = f'strip-admin-{uuid.uuid4().hex[:8]}@test.local'
    _make_company_user(admin_email, 'StripAdminPW1', role='admin')
    admin = app.test_client()
    r = admin.post('/api/auth/login', json={'email': admin_email, 'password': 'StripAdminPW1'})
    assert r.status_code == 200, r.get_json()

    staff_email = f'padded-setup-{uuid.uuid4().hex[:8]}@test.local'
    r2 = admin.post('/api/admin/employees', json={'email': staff_email})
    assert r2.status_code == 200, r2.get_json()
    setup_url = r2.get_json()['setup_link']
    token = re.search(r'/#setup/([0-9a-f]+)', setup_url).group(1)

    setup_client = app.test_client()
    r3 = setup_client.post('/api/auth/employee/setup',
                            json={'token': token, 'password': '  PaddedPW1  '})
    assert r3.status_code == 200, r3.get_json()

    # Logging in with the exact (whitespace-padded) string the employee
    # actually typed at setup time must succeed -- both endpoints must
    # strip identically, or this is a permanent lockout with no recovery.
    r4 = app.test_client().post('/api/auth/login',
                                 json={'email': staff_email, 'password': '  PaddedPW1  '})
    assert r4.status_code == 200, r4.get_json()

    # The trimmed value must also work (this direction already worked
    # before the fix, and must keep working).
    r5 = app.test_client().post('/api/auth/login',
                                 json={'email': staff_email, 'password': 'PaddedPW1'})
    assert r5.status_code == 200, r5.get_json()


def test_cookie_hardening_flags():
    assert app.config.get('SESSION_COOKIE_HTTPONLY') is True
    assert app.config.get('SESSION_COOKIE_SAMESITE') == 'Lax'


def test_session_has_bounded_lifetime():
    assert app.permanent_session_lifetime.total_seconds() > 0
    assert app.permanent_session_lifetime.total_seconds() <= 24 * 3600
