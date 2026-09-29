"""
Aura Retail -- Retail UX P1b item 9 (backend half): GET /api/sub/retail/setup-checklist.

The owner's first-run list. Every tick is DERIVED from real state on every
call, so this file proves each of the nine derivations in BOTH directions
(done -> true, not done -> false) plus the third answer, `null` = "this server
cannot tell" (a store could not be read). It also proves:

  * the route is owner-only: a manager (even holding retail.employees=full) and
    a cashier get 403 OWNER_ONLY, anonymous gets 401;
  * company scoping: another shop's products / cashiers / name / books never
    tick this shop's list;
  * shape: exactly the nine ids, in the documented order, `done` is only ever
    true/false/null, `detail` is a string, `Cache-Control: no-store`;
  * it is a PURE READ: a GET writes no setting, no book, no sync event;
  * the two steps that must NOT exist (printer_tested, whatsapp_desktop) do not.

Run (ONE file per process):
    .venv/Scripts/python.exe -m pytest products/retail/tests/retail_p1b_backend_setup_checklist_test.py -q -p no:cacheprovider
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_p1b_checklist_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_BACKUP_DIR", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True
app.config["PROPAGATE_EXCEPTIONS"] = False

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.identity import device_context, device_registry, user_accounts  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.einvoicing import settings as einv_settings  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from api import retail_api  # noqa: E402

URL = '/api/sub/retail/setup-checklist'
ORDER = ['licence', 'main_computer', 'shop_name_logo', 'default_gst', 'products',
         'cashier_invited', 'readable_numbers', 'first_backup', 'jofotara']


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    device_context._cached_device = None
    # Every test starts with its OWN empty, real backup folder.
    backups = tmp_path / 'backups'
    monkeypatch.setenv('AURA_BACKUP_DIR', str(backups))
    yield
    device_context._cached_device = None


def _make_shop():
    email = f"p1bchk-{uuid.uuid4().hex[:10]}@test.local"
    password = "P1bChecklistPW1"
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, "EMP-0001", email, hash_password(password), "admin", "active"))
    conn.commit()
    conn.close()
    rconn = get_retail_conn()
    try:
        rconn.execute("INSERT INTO branches (company_id,name,uid) VALUES (?,?,?)",
                      (company_id, 'Main', str(uuid.uuid4())))
        rconn.commit()
    finally:
        rconn.close()
    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    client.get('/api/sub/retail/settings/tax')  # materialise the lazy tables
    return client, company_id


def _make_staff(company_id, role, capabilities=None, status='active'):
    email = f"p1bchk-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "P1bChecklistPW1"
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    try:
        conn.execute(
            "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
            "VALUES (?,?,?,?,?,?,?,0)",
            (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), role, status))
        conn.execute(
            "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), user_id, "retail", "full"))
        user_accounts.seed_capabilities_for_user(conn, user_id, role)
        for code, level in (capabilities or {}).items():
            conn.execute("UPDATE user_permissions SET access_level=? WHERE user_id=? AND subsystem=?",
                         (level, user_id, code))
        conn.commit()
    finally:
        conn.close()
    client = app.test_client()
    if status == 'active':
        r = client.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.get_json()
    return client


def _steps(client):
    r = client.get(URL)
    assert r.status_code == 200, r.get_json()
    return {s['id']: s for s in r.get_json()['data']['steps']}


def _put_setting(cid, skey, svalue):
    conn = get_retail_conn()
    try:
        conn.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?) "
                     "ON CONFLICT(company_id,skey) DO UPDATE SET svalue=excluded.svalue",
                     (cid, skey, svalue))
        conn.commit()
    finally:
        conn.close()


def _add_product(client):
    r = client.post('/api/sub/retail/products', json={
        'name': 'Checklist Widget', 'sku': f'CHK-{uuid.uuid4().hex[:8]}',
        'cost_price': 1.0, 'sell_price': 2.0, 'initial_stock': 1})
    assert r.status_code == 200, r.get_json()


def _add_book(cid, status='active'):
    conn = get_retail_conn()
    try:
        conn.execute("INSERT INTO doc_series (id,company_id,doc_type,code,label,status,row_version) "
                     "VALUES (?,?,'sale',?,?,?,1)",
                     (str(uuid.uuid4()), cid, 'K' + uuid.uuid4().hex[:6].upper(), 'k', status))
        conn.commit()
    finally:
        conn.close()


def _set_einv(cid, **kv):
    conn = get_retail_conn()
    try:
        for k, v in kv.items():
            einv_settings.set_setting(conn, cid, k, v)
        conn.commit()
    finally:
        conn.close()


# ── access ───────────────────────────────────────────────────────────────

def test_owner_gets_nine_steps_in_order_with_honest_types_and_no_store():
    c, cid = _make_shop()
    r = c.get(URL)
    assert r.status_code == 200
    assert r.headers.get('Cache-Control') == 'no-store'
    body = r.get_json()
    assert body['status'] == 'success'
    steps = body['data']['steps']
    assert [s['id'] for s in steps] == ORDER
    for s in steps:
        assert s['done'] in (True, False, None), s
        assert isinstance(s['detail'], str) and s['detail'], s
    ids = {s['id'] for s in steps}
    assert 'printer_tested' not in ids and 'whatsapp_desktop' not in ids


def test_manager_and_cashier_are_refused_even_holding_the_capability():
    c, cid = _make_shop()
    manager = _make_staff(cid, 'manager', {'retail.employees': 'full'})
    cashier = _make_staff(cid, 'cashier')
    for who in (manager, cashier):
        r = who.get(URL)
        assert r.status_code == 403 and r.get_json().get('code') == 'OWNER_ONLY', r.get_json()
    assert c.get(URL).status_code == 200      # the allow half


def test_anonymous_is_refused():
    assert app.test_client().get(URL).status_code in (401, 403)


def test_the_list_is_a_pure_read_it_writes_nothing():
    c, cid = _make_shop()

    def snapshot():
        conn = get_retail_conn()
        try:
            return (conn.execute("SELECT COUNT(*) FROM retail_settings WHERE company_id=?", (cid,)).fetchone()[0],
                    conn.execute("SELECT COUNT(*) FROM doc_series WHERE company_id=?", (cid,)).fetchone()[0],
                    conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0],
                    conn.execute("SELECT COUNT(*) FROM audit_log WHERE company_id=?", (cid,)).fetchone()[0])
        finally:
            conn.close()
    before = snapshot()
    c.get(URL)
    c.get(URL)
    assert snapshot() == before


# ── licence ──────────────────────────────────────────────────────────────

def test_licence_true_when_active_false_when_expired_null_when_unreadable(monkeypatch):
    c, cid = _make_shop()
    s = _steps(c)['licence']
    assert s['done'] is True, s
    from commercial_runtime.licensing_contracts import status_presenter
    real = status_presenter.present_status
    monkeypatch.setattr(status_presenter, 'present_status', lambda rec: {'current_state': 'EXPIRED'})
    s = _steps(c)['licence']
    assert s['done'] is False and s['detail'] == 'EXPIRED', s

    def boom(rec):
        raise RuntimeError('licence store unreadable')
    monkeypatch.setattr(status_presenter, 'present_status', boom)
    assert _steps(c)['licence']['done'] is None
    monkeypatch.setattr(status_presenter, 'present_status', real)
    assert _steps(c)['licence']['done'] is True


# ── main computer ────────────────────────────────────────────────────────

def test_main_computer_true_only_when_this_device_is_the_admin_device(monkeypatch):
    c, cid = _make_shop()
    conn = registry_conn()
    try:
        conn.execute("DELETE FROM devices WHERE id=?", (device_context.local_device_uuid(),))
        conn.commit()
    finally:
        conn.close()
    device_context._cached_device = None
    assert _steps(c)['main_computer']['done'] is False
    conn = registry_conn()
    try:
        did = device_context.local_device_uuid()
        device_registry.upsert_local_device(conn, did, cid)
        device_registry.set_admin_device(conn, cid, did)
    finally:
        conn.close()
    device_context._cached_device = None
    assert _steps(c)['main_computer']['done'] is True
    # a DIFFERENT company's admin device is not this shop's main computer
    other, other_cid = _make_shop()
    assert _steps(other)['main_computer']['done'] is False

    def boom(*a, **k):
        raise RuntimeError('registry unreadable')
    monkeypatch.setattr(device_context, 'local_device_is_admin', boom)
    assert _steps(c)['main_computer']['done'] is None


# ── shop name + logo, default tax ────────────────────────────────────────

def test_shop_name_and_logo_need_both_saved():
    c, cid = _make_shop()
    s = _steps(c)['shop_name_logo']
    assert s['done'] is False and 'shop name' in s['detail'] and 'logo' in s['detail']
    _put_setting(cid, 'branding_business_name', 'Blue Door')
    s = _steps(c)['shop_name_logo']
    assert s['done'] is False and s['detail'] == 'Missing: logo.'
    _put_setting(cid, retail_api._BRANDING_LOGO_KEY, 'data:image/png;base64,AAAA')
    assert _steps(c)['shop_name_logo']['done'] is True
    _put_setting(cid, 'branding_business_name', '   ')   # blank name is not a name
    s = _steps(c)['shop_name_logo']
    assert s['done'] is False and s['detail'] == 'Missing: shop name.'


def test_logo_alone_does_not_tick_it():
    c, cid = _make_shop()
    _put_setting(cid, retail_api._BRANDING_LOGO_KEY, 'data:image/png;base64,AAAA')
    s = _steps(c)['shop_name_logo']
    assert s['done'] is False and s['detail'] == 'Missing: shop name.'


def test_default_gst_follows_the_saved_rate():
    c, cid = _make_shop()
    assert _steps(c)['default_gst']['done'] is False
    _put_setting(cid, 'default_tax_rate', '16')
    assert _steps(c)['default_gst']['done'] is True
    _put_setting(cid, 'default_tax_rate', '')
    assert _steps(c)['default_gst']['done'] is False


def test_another_shops_name_logo_and_rate_never_tick_this_shop():
    a, cid_a = _make_shop()
    b, cid_b = _make_shop()
    _put_setting(cid_b, 'branding_business_name', 'Other Shop')
    _put_setting(cid_b, retail_api._BRANDING_LOGO_KEY, 'data:image/png;base64,AAAA')
    _put_setting(cid_b, 'default_tax_rate', '16')
    sa = _steps(a)
    assert sa['shop_name_logo']['done'] is False and sa['default_gst']['done'] is False
    sb = _steps(b)
    assert sb['shop_name_logo']['done'] is True and sb['default_gst']['done'] is True


# ── products ─────────────────────────────────────────────────────────────

def test_products_counts_only_this_shops_products():
    a, cid_a = _make_shop()
    b, cid_b = _make_shop()
    assert _steps(a)['products']['done'] is False
    _add_product(b)
    assert _steps(a)['products']['done'] is False, "another shop's product must not tick this list"
    _add_product(a)
    s = _steps(a)['products']
    assert s['done'] is True and s['detail'] == '1 products.'


# ── cashier invited ──────────────────────────────────────────────────────

def test_cashier_step_counts_an_active_non_owner_of_this_shop_only():
    a, cid_a = _make_shop()
    b, cid_b = _make_shop()
    assert _steps(a)['cashier_invited']['done'] is False
    _make_staff(cid_b, 'cashier')
    assert _steps(a)['cashier_invited']['done'] is False, "another shop's cashier must not tick this list"
    _make_staff(cid_a, 'cashier', status='disabled')
    assert _steps(a)['cashier_invited']['done'] is False, 'a disabled account is not an invited cashier'
    _make_staff(cid_a, 'manager')
    assert _steps(a)['cashier_invited']['done'] is True


def test_cashier_step_is_null_when_the_staff_list_cannot_be_read(monkeypatch):
    c, cid = _make_shop()
    _make_staff(cid, 'cashier')

    def boom():
        raise RuntimeError('registry unreadable')
    monkeypatch.setattr(retail_api, '_registry_conn', boom)
    assert _steps(c)['cashier_invited']['done'] is None


# ── readable numbers ─────────────────────────────────────────────────────

def test_readable_numbers_on_for_a_new_shop_off_when_switched_off_and_on_with_a_live_book():
    c, cid = _make_shop()
    assert _steps(c)['readable_numbers']['done'] is True         # new shop -> will be on
    assert c.post('/api/sub/retail/numbering', json={'readable_enabled': False}).status_code == 200
    assert _steps(c)['readable_numbers']['done'] is False
    _add_book(cid, status='retired')
    assert _steps(c)['readable_numbers']['done'] is False, 'a retired book is not "in use"'
    _add_book(cid, status='active')
    s = _steps(c)['readable_numbers']
    assert s['done'] is True and s['detail'] == 'A numbering book is in use.'


def test_readable_numbers_is_off_for_a_running_shop_and_ignores_another_shops_books():
    a, cid_a = _make_shop()
    b, cid_b = _make_shop()
    assert a.post('/api/sub/retail/numbering', json={'readable_enabled': False}).status_code == 200
    _add_book(cid_b)
    assert _steps(a)['readable_numbers']['done'] is False


# ── first backup ─────────────────────────────────────────────────────────

def test_first_backup_needs_a_real_backup_file(tmp_path):
    c, cid = _make_shop()
    backups = Path(os.environ['AURA_BACKUP_DIR'])
    assert not backups.exists()
    assert _steps(c)['first_backup']['done'] is False           # folder missing
    backups.mkdir(parents=True)
    assert _steps(c)['first_backup']['done'] is False           # empty folder
    (backups / 'notes.txt').write_text('x')
    (backups / 'aura-retail-backup-2026.txt').write_text('x')
    (backups / 'other-backup-2026.zip').write_text('x')
    assert _steps(c)['first_backup']['done'] is False           # wrong names
    (backups / 'aura-retail-backup-20260929-101500.zip').write_bytes(b'PK')
    assert _steps(c)['first_backup']['done'] is True
    (backups / 'aura-retail-backup-20260929-101500.zip').unlink()
    assert _steps(c)['first_backup']['done'] is False           # a tick that was true can go false


def test_first_backup_is_null_when_the_folder_cannot_be_read(monkeypatch):
    c, cid = _make_shop()
    Path(os.environ['AURA_BACKUP_DIR']).mkdir(parents=True)
    real_os = retail_api.os

    class _Shim:
        def __getattr__(self, name):
            return getattr(real_os, name)

        def listdir(self, *a, **k):
            raise PermissionError('denied')
    monkeypatch.setattr(retail_api, 'os', _Shim())
    assert _steps(c)['first_backup']['done'] is None


# ── JoFotara ─────────────────────────────────────────────────────────────

def test_jofotara_needs_the_jordan_regime_a_provider_and_a_tin():
    c, cid = _make_shop()
    s = _steps(c)['jofotara']
    assert s['done'] is False and 'not switched on' in s['detail']
    _set_einv(cid, tax_regime='jordan')
    s = _steps(c)['jofotara']
    assert s['done'] is False and 'not finished' in s['detail']
    _set_einv(cid, provider='live', seller_tin='')
    assert _steps(c)['jofotara']['done'] is False               # provider but no TIN
    _set_einv(cid, seller_tin='123456789')
    assert _steps(c)['jofotara']['done'] is True
    _set_einv(cid, provider='unconfigured')
    assert _steps(c)['jofotara']['done'] is False               # TIN but no provider
    _set_einv(cid, provider='live', tax_regime='none')
    assert _steps(c)['jofotara']['done'] is False               # finished but regime off


def test_another_shops_einvoicing_never_ticks_this_shop():
    a, cid_a = _make_shop()
    b, cid_b = _make_shop()
    _set_einv(cid_b, tax_regime='jordan', provider='live', seller_tin='123456789')
    assert _steps(b)['jofotara']['done'] is True
    assert _steps(a)['jofotara']['done'] is False


def test_jofotara_is_null_when_the_settings_cannot_be_read(monkeypatch):
    c, cid = _make_shop()

    def boom(*a, **k):
        raise RuntimeError('settings unreadable')
    monkeypatch.setattr(einv_settings, 'get_setting', boom)
    assert _steps(c)['jofotara']['done'] is None
