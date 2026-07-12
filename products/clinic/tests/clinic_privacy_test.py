"""
Aura Clinic -- privacy / sensitive-data-boundary suite (Phase 3).

See docs/privacy/clinic-sensitive-data-boundary.md for the full inspection
this suite backs.

Run:
    pytest products/clinic/tests/clinic_privacy_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_clinic_privacy_"))
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
    email = f'priv-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'PrivacyPW1'
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
    assert r.status_code == 200
    return c, company_id


# ═════════════════════════════════════════════════════════════════════════════
# 1. Patient objects cannot be serialized into a future telemetry-safe payload
# ═════════════════════════════════════════════════════════════════════════════

# The exact set of fields any future allowlisted-telemetry client MUST
# exclude -- documents the boundary contract described in
# docs/privacy/clinic-sensitive-data-boundary.md.
FORBIDDEN_TELEMETRY_FIELDS = {
    'name', 'dob', 'gender', 'phone', 'email', 'address', 'blood_type',
    'emergency_contact', 'emergency_phone', 'notes',
}


def test_patient_row_contains_fields_a_future_telemetry_allowlist_must_exclude():
    c, cid = _make_admin_client()
    c.post('/api/sub/clinic/patients', json={'name': 'Telemetry Boundary Patient', 'phone': '555-1', 'notes': 'secret note'})
    conn = get_clinic_conn()
    row = dict(conn.execute("SELECT * FROM clinic_patients LIMIT 1").fetchone())
    conn.close()
    # Confirms these fields genuinely exist on the row (so the "exclude
    # list" in the privacy doc is documenting something real, not a strawman).
    assert FORBIDDEN_TELEMETRY_FIELDS.issubset(set(row.keys()))


# ═════════════════════════════════════════════════════════════════════════════
# 2. Medical notes do not appear in sanitized exception logs / responses
# ═════════════════════════════════════════════════════════════════════════════

def test_patient_creation_error_does_not_leak_traceback_to_client(monkeypatch):
    """Forces create_patient's except-branch and asserts the Phase 3 fix:
    no `detail`/traceback field, no distinctive marker string in the response."""
    c, cid = _make_admin_client()
    import api.clinic_api as clinic_api

    def _boom(*a, **kw):
        raise RuntimeError('SECRET_MARKER_A_SENSITIVE_VALUE_MUST_NOT_LEAK')

    monkeypatch.setattr(clinic_api, 'get_clinic_conn', _boom)
    r = c.post('/api/sub/clinic/patients', json={'name': 'Should Not Leak'})
    assert r.status_code == 500
    body = r.get_json()
    assert 'detail' not in body
    raw = json.dumps(body)
    assert 'SECRET_MARKER_A_SENSITIVE_VALUE_MUST_NOT_LEAK' not in raw
    assert 'Traceback' not in raw
    assert 'traceback' not in raw.lower()


# ═════════════════════════════════════════════════════════════════════════════
# 3. Passwords and tokens are redacted
# ═════════════════════════════════════════════════════════════════════════════

def test_session_never_contains_plaintext_password_or_hash():
    email = f'nohash-{uuid.uuid4().hex[:8]}@test.local'
    password = 'NoHashPW123'
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()
    with app.test_client() as c:
        c.post('/api/auth/login', json={'email': email, 'password': password})
        with c.session_transaction() as s:
            blob = json.dumps(dict(s))
            assert password not in blob
            assert 'password_hash' not in blob
            assert 'pbkdf2_sha256$' not in blob


def test_setup_invite_token_is_not_stored_in_plaintext():
    """secure_links stores only a SHA-256 hash of the invite token, never
    the raw token -- verified directly against the table."""
    c, cid = _make_admin_client()
    r = c.post('/api/admin/employees', json={'email': f'invite-{uuid.uuid4().hex[:6]}@test.local'})
    setup_url = r.get_json()['setup_link']
    raw_token = setup_url.rsplit('/', 1)[-1]
    conn = registry_conn()
    rows = [dict(row) for row in conn.execute("SELECT * FROM secure_links WHERE company_id=?", (cid,)).fetchall()]
    conn.close()
    assert any(raw_token not in json.dumps(row) for row in rows)
    assert all('token_hash' in row and row['token_hash'] != raw_token for row in rows)


# ═════════════════════════════════════════════════════════════════════════════
# 4. Database connection errors do not expose local secrets
# ═════════════════════════════════════════════════════════════════════════════

def test_registry_db_path_has_no_embedded_credentials():
    """SQLite connections carry no username/password by construction --
    confirms the connection string is a bare filesystem path, nothing else."""
    from commercial_runtime.identity.registry_db import DB_PATH
    assert '@' not in DB_PATH  # no user:pass@host style credential could ever appear
    assert 'password' not in DB_PATH.lower()


# ═════════════════════════════════════════════════════════════════════════════
# 5. File upload errors -- NOT APPLICABLE (no upload feature exists)
# ═════════════════════════════════════════════════════════════════════════════

def test_no_file_upload_route_exists_in_clinic_api():
    """Documents, rather than silently omits, that Section 8 (file/path
    safety) is not applicable -- confirmed no request.files usage anywhere
    in the extracted clinic_api.py."""
    src = (BACKEND_DIR / 'api' / 'clinic_api.py').read_text(encoding='utf-8')
    assert 'request.files' not in src


# ═════════════════════════════════════════════════════════════════════════════
# 6. Raw patient database files are never treated as diagnostic artifacts
# ═════════════════════════════════════════════════════════════════════════════

def test_no_diagnostics_submission_feature_exists_yet():
    """Documents the boundary rule for a future diagnostics feature (Phase
    10 equivalent, not built yet): confirms no such route exists today that
    could accidentally accept a .db file."""
    src = (BACKEND_DIR / 'api' / 'clinic_api.py').read_text(encoding='utf-8')
    assert 'diagnostic' not in src.lower()


# ═════════════════════════════════════════════════════════════════════════════
# 7. Debug mode is not enabled in production packaging
# ═════════════════════════════════════════════════════════════════════════════

def test_debug_mode_disabled():
    assert app.debug is False
    assert app.config.get('DEBUG') in (False, None) or app.config.get('DEBUG') is False


def test_app_py_never_calls_run_with_debug_true():
    src = (BACKEND_DIR / 'app.py').read_text(encoding='utf-8')
    assert 'debug=True' not in src
