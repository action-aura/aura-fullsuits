"""
Aura Retail -- localization / bilingual parity suite (Phase 2B).

Validates the migrated static/locales/{en,ar}.json + the i18n.js loader
(extracted from Action Aura Enterprise's static/js/i18n.js, self-contained --
no dependency on any other subsystem's JS). These are server-served static
assets consumed by a browser at runtime; a real DOM/RTL rendering check needs
a browser automation tool (Playwright/Selenium) which is not part of this
Python/pytest stack -- these tests cover everything checkable without one:
file presence, JSON validity, key parity, translation completeness, correct
serving path, and static-source verification of the loader's documented
default/fallback/persistence/RTL behavior. See
docs/migration/retail-parity-matrix.md "localization" row for what remains
manual/deferred (visual RTL layout check in an actual browser).

Run:
    pytest products/retail/tests/retail_localization_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_i18n_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin_client():
    email = f"i18n-{uuid.uuid4().hex[:10]}@test.local"
    password = "I18nTestPW1"
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
    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})
    return client, user_id


# ═════════════════════════════════════════════════════════════════════════════
# File presence, no broken paths, no old-repo references
# ═════════════════════════════════════════════════════════════════════════════

def test_locale_files_exist_in_this_repo():
    assert (FRONTEND_DIR / 'locales' / 'en.json').exists()
    assert (FRONTEND_DIR / 'locales' / 'ar.json').exists()
    assert (FRONTEND_DIR / 'i18n.js').exists()


def test_no_reference_to_old_repository_path():
    """No copied localization asset should reference the source monorepo's
    absolute path or its multi-subsystem module names."""
    js_src = (FRONTEND_DIR / 'i18n.js').read_text(encoding='utf-8')
    assert 'AuraEnterprise' not in js_src
    assert 'C:\\' not in js_src and 'C:/' not in js_src


def test_locale_json_files_are_valid_json():
    en = json.loads((FRONTEND_DIR / 'locales' / 'en.json').read_text(encoding='utf-8'))
    ar = json.loads((FRONTEND_DIR / 'locales' / 'ar.json').read_text(encoding='utf-8'))
    assert isinstance(en, dict) and len(en) > 0
    assert isinstance(ar, dict) and len(ar) > 0


# ═════════════════════════════════════════════════════════════════════════════
# Key parity / translation completeness
# ═════════════════════════════════════════════════════════════════════════════

def test_english_and_arabic_have_identical_key_sets():
    en = json.loads((FRONTEND_DIR / 'locales' / 'en.json').read_text(encoding='utf-8'))
    ar = json.loads((FRONTEND_DIR / 'locales' / 'ar.json').read_text(encoding='utf-8'))
    assert set(en.keys()) == set(ar.keys()), \
        f"missing in ar: {set(en)-set(ar)} / extra in ar: {set(ar)-set(en)}"


def test_no_missing_arabic_translations():
    """Every English key must have a non-empty Arabic value -- a Retail page
    must never display an untranslated key name when Arabic is selected."""
    en = json.loads((FRONTEND_DIR / 'locales' / 'en.json').read_text(encoding='utf-8'))
    ar = json.loads((FRONTEND_DIR / 'locales' / 'ar.json').read_text(encoding='utf-8'))
    empty = [k for k in en if not (ar.get(k) or '').strip()]
    assert empty == [], f"keys with empty Arabic translation: {empty}"


def test_retail_specific_keys_are_genuinely_translated():
    """Confirms real translation (not just English copied into the Arabic
    file) for the Retail-specific strings."""
    en = json.loads((FRONTEND_DIR / 'locales' / 'en.json').read_text(encoding='utf-8'))
    ar = json.loads((FRONTEND_DIR / 'locales' / 'ar.json').read_text(encoding='utf-8'))
    for key in ('Retail & POS', 'Retail Overview', 'Dashboard', 'Import Data'):
        assert key in en, f"expected shell key missing from en.json: {key}"
        assert ar[key] != en[key], f"'{key}' is not translated (Arabic == English)"
        assert ar[key].strip() != ''


def test_no_untranslated_key_equals_value_pairs():
    """Zero-tolerance check across the whole file: no key/value pair in
    ar.json is byte-identical to en.json (which would render as an
    untranslated English string on an Arabic page)."""
    en = json.loads((FRONTEND_DIR / 'locales' / 'en.json').read_text(encoding='utf-8'))
    ar = json.loads((FRONTEND_DIR / 'locales' / 'ar.json').read_text(encoding='utf-8'))
    identical = [k for k in en if en[k] == ar.get(k)]
    assert identical == [], f"untranslated (identical en/ar) keys: {identical}"


# ═════════════════════════════════════════════════════════════════════════════
# Served correctly by the packaged app (Flask static route)
# ═════════════════════════════════════════════════════════════════════════════

def test_app_serves_locale_files_at_expected_static_path():
    with app.test_client() as c:
        r_en = c.get('/static/locales/en.json')
        r_ar = c.get('/static/locales/ar.json')
        assert r_en.status_code == 200
        assert r_ar.status_code == 200
        assert r_en.get_json()['Dashboard'] == 'Dashboard'
        assert r_ar.get_json()['Dashboard'] != 'Dashboard'


def test_app_serves_i18n_loader_script():
    with app.test_client() as c:
        r = c.get('/static/i18n.js')
        assert r.status_code == 200
        assert b'AuraI18n' in r.data


# ═════════════════════════════════════════════════════════════════════════════
# Loader behavior -- static-source verification (no browser/DOM available here)
# ═════════════════════════════════════════════════════════════════════════════

def test_loader_defaults_to_english():
    src = (FRONTEND_DIR / 'i18n.js').read_text(encoding='utf-8')
    assert re.search(r"current:\s*'en'", src), "default language must be English"


def test_loader_falls_back_to_english_for_missing_keys():
    src = (FRONTEND_DIR / 'i18n.js').read_text(encoding='utf-8')
    # t(text): "(text in d) ? d[text] : text" -- unknown key returns the original English text.
    assert 'text in d' in src and 'd[text] : text' in src


def test_loader_sets_rtl_direction_for_arabic():
    src = (FRONTEND_DIR / 'i18n.js').read_text(encoding='utf-8')
    assert "setAttribute('dir'" in src
    assert "isRTL() ? 'rtl' : 'ltr'" in src


def test_loader_persists_choice_to_localstorage():
    src = (FRONTEND_DIR / 'i18n.js').read_text(encoding='utf-8')
    assert "localStorage.setItem('aura_lang'" in src
    assert "localStorage.getItem('aura_lang')" in src


def test_loader_only_fetches_its_own_locale_files():
    """No broken/foreign paths: the loader must only ever fetch its own two
    locale files, nothing from another subsystem or the old repo layout."""
    src = (FRONTEND_DIR / 'i18n.js').read_text(encoding='utf-8')
    fetch_paths = re.findall(r"fetch\('([^']+)'\)", src)
    assert set(fetch_paths) == {'/static/locales/en.json', '/static/locales/ar.json'}


# ═════════════════════════════════════════════════════════════════════════════
# Persistence endpoint (server-side half of "language preference persists")
# ═════════════════════════════════════════════════════════════════════════════

def test_language_persistence_endpoint_not_404():
    client, _uid = _make_admin_client()
    r = client.post('/api/auth/language', json={'language': 'ar'})
    assert r.status_code != 404
    assert r.status_code == 200
    assert r.get_json()['success'] is True


def test_language_persistence_writes_to_database():
    client, uid = _make_admin_client()
    client.post('/api/auth/language', json={'language': 'ar'})
    conn = registry_conn()
    row = conn.execute("SELECT language FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    assert row['language'] == 'ar'


def test_language_persistence_rejects_unknown_language_safely():
    client, uid = _make_admin_client()
    r = client.post('/api/auth/language', json={'language': 'fr'})
    assert r.status_code == 200
    assert r.get_json()['language'] == 'en'  # invalid language falls back to English, not an error


def test_language_persistence_before_login_does_not_error():
    with app.test_client() as c:
        r = c.post('/api/auth/language', json={'language': 'ar'})
        assert r.status_code == 200
        assert r.get_json()['success'] is True  # pre-login: localStorage already holds it, server is a no-op success
