"""
Aura Retail -- GET /api/devices/me names the device's branch (FINAL polish F6,
crew D).

The app header chip reads `branch_name` / `device_name` off /api/devices/me.
The route is identity's (commercial_runtime/identity/device_routes.py) and
identity cannot see retail.db, so retail REGISTERS a resolver at import
(retail_api._device_branch_name_for_header). The unit contract of the hook is
in commercial_runtime/identity/tests/test_device_routes.py; this file proves
the REAL wiring in the REAL retail app:

  * the registration actually happened (an unregistered hook reads null and
    every unit test above would still pass -- so this is the test that counts);
  * the name is company-scoped (another company's lower-id branch must not
    leak into this company's header);
  * a pinned device reports the PINNED branch, an unpinned one the company's
    first active branch, and a pin that does not resolve falls back rather
    than blanking;
  * reading the header never self-heals/creates a branch as a side effect.

Self-contained bootstrap, ONE pytest process per file.

Run:
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest products/retail/tests/retail_device_me_branch_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_dev_me_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from api import retail_api  # noqa: E402
from commercial_runtime.identity import device_context, device_routes, user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402

API = '/api/sub/retail'


@pytest.fixture(autouse=True)
def _fresh_device_cache():
    device_context._cached_device = None
    yield
    device_context._cached_device = None


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin(company_id):
    email = f"devme-{uuid.uuid4().hex[:10]}@test.local"
    password = "DevMePW1"
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), 'admin', "active"),
    )
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    user_accounts.seed_capabilities_for_user(conn, user_id, 'admin')
    conn.commit()
    conn.close()
    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client


def _add_branch(client, name):
    r = client.post(f'{API}/branches', json={'name': name})
    assert r.status_code == 200, r.get_json()
    bid = r.get_json()['data']['id']
    # REHEARSAL-1007 B8: the route now also creates a standard "Main Branch" first on a
    # company with no branch; these tests pin /device/me's branch NAME resolution against
    # the branches they create, so the empty placeholder is dropped here (the placeholder
    # itself is owned by retail_rehearsal_b8_b9_branch_and_float_test.py).
    from database.schema import get_retail_conn
    conn = get_retail_conn()
    conn.execute("DELETE FROM branches WHERE name='Main Branch' AND id<? AND company_id="
                 "(SELECT company_id FROM branches WHERE id=?)", (bid, bid))
    conn.commit(); conn.close()
    return bid


def _branch_uid(branch_id, company_id):
    conn = retail_api.get_retail_conn()
    try:
        return conn.execute("SELECT uid FROM branches WHERE id=? AND company_id=?",
                            (branch_id, company_id)).fetchone()['uid']
    finally:
        conn.close()


def _branch_count():
    conn = retail_api.get_retail_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM branches").fetchone()[0]
    finally:
        conn.close()


# ONE device, ONE company: identity refuses (409 DEVICE_COMPANY_MISMATCH, Phase 0
# decision 0-a) to re-bind a local device to a second company, so every /me
# call below is made as MY_COMPANY. Other companies exist only as data (their
# branches) that must never leak into MY_COMPANY's header.
MY_COMPANY = str(uuid.uuid4())
_MINE = {}


def _mine():
    if 'client' not in _MINE:
        _MINE['client'] = _make_admin(MY_COMPANY)
    return _MINE['client']


@pytest.fixture(autouse=True)
def _my_company_starts_with_no_branches():
    conn = retail_api.get_retail_conn()
    conn.execute("DELETE FROM branches WHERE company_id=?", (MY_COMPANY,))
    conn.commit()
    conn.close()
    yield


def _me(client):
    r = client.get('/api/devices/me')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['device']


def test_retail_registered_its_resolver_with_identity():
    # The wiring itself. Every unit test in identity passes with NO resolver
    # registered (branch_name null), so only this can catch the registration
    # line being lost.
    assert device_routes._branch_name_resolver is retail_api._device_branch_name_for_header


def test_unpinned_device_reports_this_companys_first_active_branch_not_another_companys(monkeypatch):
    monkeypatch.setattr(retail_api, '_onboarding_get_device_branch_uid', lambda: None)
    other = _make_admin(str(uuid.uuid4()))
    _add_branch(other, 'Other Company Branch')  # created first => lower id than ours, on purpose
    mine = _mine()
    _add_branch(mine, 'Our Downtown')

    assert _me(mine)['branch_name'] == 'Our Downtown'


def test_pinned_device_reports_the_pinned_branch(monkeypatch):
    c = _mine()
    _add_branch(c, 'First Branch')
    second_id = _add_branch(c, 'Second Branch')
    uid = _branch_uid(second_id, MY_COMPANY)
    monkeypatch.setattr(retail_api, '_onboarding_get_device_branch_uid', lambda: uid)

    assert _me(c)['branch_name'] == 'Second Branch'


def test_pin_that_does_not_resolve_falls_back_to_default_rather_than_blank(monkeypatch):
    c = _mine()
    _add_branch(c, 'Only Branch')
    monkeypatch.setattr(retail_api, '_onboarding_get_device_branch_uid', lambda: 'uid-not-on-this-device')

    assert _me(c)['branch_name'] == 'Only Branch'


def test_a_pin_pointing_at_another_companys_branch_does_not_leak_its_name(monkeypatch):
    other_company = str(uuid.uuid4())
    other = _make_admin(other_company)
    foreign_id = _add_branch(other, 'Foreign Secret Branch')
    foreign_uid = _branch_uid(foreign_id, other_company)
    c = _mine()
    _add_branch(c, 'Mine')
    monkeypatch.setattr(retail_api, '_onboarding_get_device_branch_uid', lambda: foreign_uid)

    assert _me(c)['branch_name'] == 'Mine'


def test_company_with_no_branch_reads_null_and_the_header_read_creates_nothing(monkeypatch):
    monkeypatch.setattr(retail_api, '_onboarding_get_device_branch_uid', lambda: None)
    c = _mine()
    before = _branch_count()

    device = _me(c)

    assert device['branch_name'] is None
    assert _branch_count() == before, "GET /me must not self-heal a branch (that is _default_branch's write)"
