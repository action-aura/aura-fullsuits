"""
Aura Clinic -- localization / bilingual parity suite (Phase 3).

Clinic reuses the same shared 139-key static/locales/{en,ar}.json dictionary
Retail uses (duplicated into products/clinic/frontend/locales/, matching the
Phase 2 precedent for import-wizard.js -- see clinic-dependency-map.md).
Clinic's own UI (subsystem-clinic.js) has NO explicit t()/data-i18n calls at
all -- it relies entirely on i18n.js's automatic DOM-text-sweep translation,
confirmed by inspection in Phase 3 discovery.

Run:
    pytest products/clinic/tests/clinic_localization_test.py -v
"""
import json
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
FRONTEND_DIR = PRODUCT_DIR / 'frontend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_clinic_i18n_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_CLINIC", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin_client():
    email = f'i18n-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'I18nPW12345'
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
    return c, user_id


# ═════════════════════════════════════════════════════════════════════════════
# File presence / packaging
# ═════════════════════════════════════════════════════════════════════════════

def test_locale_files_exist_in_this_repo():
    assert (FRONTEND_DIR / 'locales' / 'en.json').exists()
    assert (FRONTEND_DIR / 'locales' / 'ar.json').exists()
    assert (FRONTEND_DIR / 'i18n.js').exists()


def test_no_reference_to_old_repository_path():
    js_src = (FRONTEND_DIR / 'i18n.js').read_text(encoding='utf-8')
    assert 'AuraEnterprise' not in js_src
    assert 'C:\\' not in js_src and 'C:/' not in js_src


def test_app_serves_locale_files_and_i18n_loader():
    with app.test_client() as c:
        assert c.get('/static/locales/en.json').status_code == 200
        assert c.get('/static/locales/ar.json').status_code == 200
        assert c.get('/static/i18n.js').status_code == 200
        assert c.get('/static/subsystem-clinic.js').status_code == 200


# ═════════════════════════════════════════════════════════════════════════════
# English startup / key coverage / reported counts
# ═════════════════════════════════════════════════════════════════════════════

def test_english_key_count_and_arabic_parity():
    en = json.loads((FRONTEND_DIR / 'locales' / 'en.json').read_text(encoding='utf-8'))
    ar = json.loads((FRONTEND_DIR / 'locales' / 'ar.json').read_text(encoding='utf-8'))
    # Reported counts (Phase 3 requirement: report EN count, AR count,
    # missing keys, untranslated identical pairs, intentional identical values).
    assert len(en) == 139
    assert len(ar) == 139
    assert set(en.keys()) == set(ar.keys()), "no missing keys either direction"
    identical = [k for k in en if en[k] == ar.get(k)]
    assert identical == [], f"untranslated (identical en/ar) keys: {identical}"


def test_core_clinic_relevant_keys_are_present_and_translated():
    """Confirms Clinic's most-visible strings ARE covered by the shared
    dictionary, even though Clinic has no dedicated locale file."""
    en = json.loads((FRONTEND_DIR / 'locales' / 'en.json').read_text(encoding='utf-8'))
    ar = json.loads((FRONTEND_DIR / 'locales' / 'ar.json').read_text(encoding='utf-8'))
    for key in ('Clinic Overview', 'Patients', 'Appointments', 'Doctors', 'Prescriptions', 'Billing'):
        assert key in en, f"expected clinic-relevant key missing: {key}"
        assert ar[key] != en[key]
        assert ar[key].strip() != ''


def test_known_missing_clinic_keys_are_explicitly_documented_not_silently_broken():
    """These clinic-relevant strings are NOT in the shared dictionary in
    source -- a real, pre-existing gap (see clinic-parity-matrix.md), not
    something this extraction introduced. This test pins the exact set so a
    future fix (adding real Arabic translations) has a clear regression
    marker to flip, and so nobody mistakes the gap for a raw-key-name bug
    (English fallback for an untranslated string is graceful, not a
    "clinic.visits.title"-style raw key leak)."""
    en = json.loads((FRONTEND_DIR / 'locales' / 'en.json').read_text(encoding='utf-8'))
    known_missing = {'Lab Expenses', 'Visits', 'Clinic'}
    for key in known_missing:
        assert key not in en, (
            f"'{key}' now exists in the dictionary -- update this test and "
            f"docs/migration/clinic-parity-matrix.md's localization row"
        )


# ═════════════════════════════════════════════════════════════════════════════
# Loader behavior -- static-source verification (no browser/DOM available)
# ═════════════════════════════════════════════════════════════════════════════

def test_loader_defaults_to_english():
    src = (FRONTEND_DIR / 'i18n.js').read_text(encoding='utf-8')
    assert re.search(r"current:\s*'en'", src)


def test_loader_falls_back_to_english_for_missing_keys():
    src = (FRONTEND_DIR / 'i18n.js').read_text(encoding='utf-8')
    assert 'text in d' in src and 'd[text] : text' in src


def test_loader_sets_rtl_direction_for_arabic():
    src = (FRONTEND_DIR / 'i18n.js').read_text(encoding='utf-8')
    assert "setAttribute('dir'" in src
    assert "isRTL() ? 'rtl' : 'ltr'" in src


def test_loader_persists_choice_to_localstorage():
    src = (FRONTEND_DIR / 'i18n.js').read_text(encoding='utf-8')
    assert "localStorage.setItem('aura_lang'" in src


def test_clinic_ui_has_no_explicit_i18n_calls_relies_on_dom_sweep():
    """Confirms the Phase 3 discovery finding: subsystem-clinic.js has zero
    AuraI18n/data-i18n references -- translation is fully automatic via the
    shared loader's DOM text-node sweep, not per-string opt-in."""
    src = (FRONTEND_DIR / 'subsystem-clinic.js').read_text(encoding='utf-8')
    assert 'AuraI18n' not in src
    assert 'data-i18n' not in src


# ═════════════════════════════════════════════════════════════════════════════
# Persistence endpoint (shared with Retail, exercised here for Clinic)
# ═════════════════════════════════════════════════════════════════════════════

def test_language_persistence_endpoint_works_for_clinic_user():
    c, uid = _make_admin_client()
    r = c.post('/api/auth/language', json={'language': 'ar'})
    assert r.status_code == 200
    assert r.get_json()['success'] is True
    conn = registry_conn()
    row = conn.execute("SELECT language FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    assert row['language'] == 'ar'


def test_session_endpoint_reports_persisted_language():
    c, uid = _make_admin_client()
    c.post('/api/auth/language', json={'language': 'ar'})
    r = c.get('/api/auth/session')
    assert r.get_json()['language'] == 'ar'
