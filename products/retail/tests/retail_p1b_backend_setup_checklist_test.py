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
import contextlib
import os
import shutil
import sys
import tempfile
import uuid
import zipfile
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


TILL_A = 'aaaaaaaa-1111-4111-8111-00000000000a'
TILL_B = 'bbbbbbbb-2222-4222-8222-00000000000b'


@contextlib.contextmanager
def at_terminal(terminal_id):
    """Pin which till this process is for the duration of a call (the seam
    retail_doc_series_test.py and the numbering tests use)."""
    previous = retail_api.local_terminal_id
    retail_api.local_terminal_id = lambda: terminal_id
    try:
        yield
    finally:
        retail_api.local_terminal_id = previous


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


def _make_staff(company_id, role, capabilities=None, status='active', pin=False):
    email = f"p1bchk-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "P1bChecklistPW1"
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    try:
        conn.execute(
            "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change, pin_hash) "
            "VALUES (?,?,?,?,?,?,?,0,?)",
            (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), role, status,
             hash_password('4821') if pin else None))
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
        # F12: this is a READ route, so the refusal must not say "change".
        assert r.get_json().get('message') == 'Only the shop owner can open this.', r.get_json()
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

def test_cashier_step_counts_only_an_active_cashier_with_a_pin_of_this_shop():
    """Every 'no' below is one wrong ingredient of an otherwise-valid cashier:
    another shop, disabled, no PIN, or the wrong role. Only the last, fully
    valid account may tick the step (the allow half)."""
    a, cid_a = _make_shop()
    b, cid_b = _make_shop()
    assert _steps(a)['cashier_invited']['done'] is False
    _make_staff(cid_b, 'cashier', pin=True)
    assert _steps(a)['cashier_invited']['done'] is False, "another shop's cashier must not tick this list"
    _make_staff(cid_a, 'cashier', status='disabled', pin=True)
    assert _steps(a)['cashier_invited']['done'] is False, 'a disabled account is not an invited cashier'
    _make_staff(cid_a, 'cashier', pin=False)
    assert _steps(a)['cashier_invited']['done'] is False, 'a cashier with no PIN cannot use the till yet'
    _make_staff(cid_a, 'manager', pin=True)
    assert _steps(a)['cashier_invited']['done'] is False, 'a manager is not the person who sells'
    _make_staff(cid_a, 'cashier', pin=True)
    s = _steps(a)['cashier_invited']
    assert s['done'] is True and s['detail'] == 'A cashier with a PIN is ready.', s
    assert _steps(b)['cashier_invited']['done'] is True         # b has its own valid cashier


def test_cashier_step_is_null_when_the_staff_list_cannot_be_read(monkeypatch):
    c, cid = _make_shop()
    _make_staff(cid, 'cashier')

    def boom():
        raise RuntimeError('registry unreadable')
    monkeypatch.setattr(retail_api, '_registry_conn', boom)
    assert _steps(c)['cashier_invited']['done'] is None


# ── readable numbers ─────────────────────────────────────────────────────

_CODE_COUNTER = [0]


def _unique_code():
    """Letters-only branch code, unique in this process (idx_doc_series_code
    is global across shops)."""
    n = _CODE_COUNTER[0]
    _CODE_COUNTER[0] += 1
    return 'Z' + chr(65 + (n // 26) % 26) + chr(65 + n % 26)


def _numbering(client, terminal, body):
    with at_terminal(terminal):
        return client.post('/api/sub/retail/numbering', json=body)


def _steps_at(client, terminal):
    with at_terminal(terminal):
        return _steps(client)


def test_readable_numbers_is_off_until_the_owner_switches_this_computer_on():
    """Nothing turns readable numbers on for the owner (orchestrator decision):
    a brand-new shop reads OFF, and a shop that is asked for a till number and
    given none stays OFF."""
    c, cid = _make_shop()
    s = _steps_at(c, TILL_A)['readable_numbers']
    assert s['done'] is False and 'Off on this computer' in s['detail'], s
    r = _numbering(c, TILL_A, {'readable_enabled': True, 'branch_code': _unique_code()})
    assert r.status_code == 400                                  # no till number chosen
    assert _steps_at(c, TILL_A)['readable_numbers']['done'] is False


def test_readable_numbers_on_for_this_computer_before_and_after_the_books_exist():
    c, cid = _make_shop()
    r = _numbering(c, TILL_A, {'readable_enabled': True, 'till_number': 3, 'branch_code': _unique_code()})
    assert r.status_code == 200, r.get_json()
    s = _steps_at(c, TILL_A)['readable_numbers']
    assert s['done'] is True, s
    assert s['detail'].startswith('On for this computer (till 3).') and 'next shift' in s['detail'], s
    with at_terminal(TILL_A):
        assert c.post('/api/sub/retail/cash-sessions/open', json={'opening_float': 0}).status_code == 200
    s = _steps_at(c, TILL_A)['readable_numbers']
    assert s['done'] is True and s['detail'] == 'On for this computer (till 3).', s
    # switching off is a tick that goes back to false
    assert _numbering(c, TILL_A, {'readable_enabled': False}).status_code == 200
    s = _steps_at(c, TILL_A)['readable_numbers']
    assert s['done'] is False and 'Off on this computer' in s['detail'], s


def test_readable_numbers_tick_is_per_computer_and_ignores_another_shops_books():
    a, cid_a = _make_shop()
    b, cid_b = _make_shop()
    assert _numbering(b, TILL_B, {'readable_enabled': True, 'till_number': 4,
                                  'branch_code': _unique_code()}).status_code == 200
    _add_book(cid_b)
    assert _steps_at(b, TILL_B)['readable_numbers']['done'] is True
    assert _steps_at(a, TILL_A)['readable_numbers']['done'] is False, \
        "another shop's switch and books must not tick this list"
    # the same shop, a DIFFERENT computer: not switched on there
    assert _steps_at(b, TILL_A)['readable_numbers']['done'] is False


# ── first backup ─────────────────────────────────────────────────────────

BACKUP_NAME = 'aura-retail-backup-20260929-101500.zip'


def _write_zip(path, names):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for n in names:
            zf.writestr(n, 'x' * 200)


REAL_MEMBERS = ('manifest.json', 'registry.db', 'retail.db')


def test_first_backup_needs_a_readable_zip_with_the_expected_files(tmp_path):
    c, cid = _make_shop()
    backups = Path(os.environ['AURA_BACKUP_DIR'])
    assert not backups.exists()
    assert _steps(c)['first_backup']['done'] is False           # folder missing
    backups.mkdir(parents=True)
    assert _steps(c)['first_backup']['done'] is False           # empty folder
    (backups / 'notes.txt').write_text('x')
    (backups / 'aura-retail-backup-2026.txt').write_text('x')
    _write_zip(backups / 'other-backup-2026.zip', REAL_MEMBERS)
    assert _steps(c)['first_backup']['done'] is False           # wrong names, even with real contents
    target = backups / BACKUP_NAME
    target.write_bytes(b'PK')                                    # the old, too-easy pass condition
    s = _steps(c)['first_backup']
    assert s['done'] is False and 'cannot be opened' in s['detail'], s
    target.write_bytes(b'')
    assert _steps(c)['first_backup']['done'] is False           # empty file
    _write_zip(target, ('manifest.json', 'registry.db'))
    assert _steps(c)['first_backup']['done'] is False           # a zip missing retail.db
    _write_zip(target, REAL_MEMBERS)
    s = _steps(c)['first_backup']
    assert s['done'] is True and s['detail'] == 'A backup exists and can be opened.', s
    target.unlink()
    assert _steps(c)['first_backup']['done'] is False           # a tick that was true can go false


def test_first_backup_a_real_backup_made_by_the_product_counts(tmp_path):
    """The fake zips above prove the rules; this proves the rules agree with
    what the product's own backup service really writes (file name AND the
    member names), so the step can actually turn green for a real owner."""
    from commercial_runtime.backup import service as backup_service
    c, cid = _make_shop()
    backups = Path(os.environ['AURA_BACKUP_DIR'])
    assert _steps(c)['first_backup']['done'] is False
    made = backup_service.create_backup('retail', _app_module._EINVOICING_APP_DATA_DIR, str(backups), 'test')
    assert Path(made['path']).parent == backups
    s = _steps(c)['first_backup']
    assert s['done'] is True and s['detail'] == 'A backup exists and can be opened.', s


def test_first_backup_cut_short_mid_copy_does_not_count(tmp_path):
    c, cid = _make_shop()
    backups = Path(os.environ['AURA_BACKUP_DIR'])
    backups.mkdir(parents=True)
    target = backups / BACKUP_NAME
    _write_zip(target, REAL_MEMBERS)
    whole = target.read_bytes()
    target.write_bytes(whole[:len(whole) // 2])                  # half-copied: no central directory
    s = _steps(c)['first_backup']
    assert s['done'] is False and 'cannot be opened' in s['detail'], s
    target.write_bytes(whole)
    assert _steps(c)['first_backup']['done'] is True
    # a bad file does not hide a good one next to it
    (backups / 'aura-retail-backup-20260101-000000.zip').write_bytes(b'PK')
    assert _steps(c)['first_backup']['done'] is True


def test_first_backup_with_a_damaged_member_does_not_count(tmp_path):
    c, cid = _make_shop()
    backups = Path(os.environ['AURA_BACKUP_DIR'])
    backups.mkdir(parents=True)
    target = backups / BACKUP_NAME
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_STORED) as zf:
        for n in REAL_MEMBERS:
            zf.writestr(n, 'x' * 200)
    assert _steps(c)['first_backup']['done'] is True
    raw = bytearray(target.read_bytes())
    at = raw.index(b'x' * 50)
    raw[at + 10] = ord('y')                                      # data no longer matches its CRC
    target.write_bytes(bytes(raw))
    s = _steps(c)['first_backup']
    assert s['done'] is False, s


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

def _wire_provider(monkeypatch, provider):
    """Put a REAL `/api/einvoicing/status` view -- built by the product's own
    blueprint factory around `provider` -- where the checklist looks for it.
    The view is a closure over the wired provider, so this is the only honest
    way to vary what the shop's install is really connected to (no invented
    provider names: the values below are the shipped providers' own)."""
    from flask import Flask
    from commercial_runtime.einvoicing.routes import make_einvoicing_blueprint
    scratch = Flask('scratch-einvoicing')
    scratch.register_blueprint(make_einvoicing_blueprint(
        product_code='AURA_RETAIL', platform=_app_module.LICENSING_PLATFORM,
        app_data_dir=_app_module._EINVOICING_APP_DATA_DIR,
        conn_factory=get_retail_conn, provider=provider))
    monkeypatch.setitem(app.view_functions, 'einvoicing_api._status',
                        scratch.view_functions['einvoicing_api._status'])


def _status_view_says(client):
    """What the real status endpoint itself reports for this shop -- the
    checklist must agree with it, not with a second copy of its rules."""
    d = client.get('/api/einvoicing/status').get_json()['data']
    return d


@pytest.fixture
def _killswitch_clean(monkeypatch):
    from commercial_runtime.einvoicing import killswitch
    app_dir = _app_module._EINVOICING_APP_DATA_DIR
    monkeypatch.delenv('AURA_EINVOICING_DISABLED', raising=False)
    killswitch.clear_disabled(app_dir)
    yield killswitch
    killswitch.clear_disabled(app_dir)


def _live_provider():
    from commercial_runtime.einvoicing.providers.direct_istd import DirectISTDProvider
    return DirectISTDProvider(base_url='https://jofotara.invalid', credentials=None)


def test_jofotara_default_build_is_never_done_even_when_switched_on(_killswitch_clean):
    """The shipped build wires UnconfiguredProvider. Whatever the shop toggles,
    nothing reaches the tax authority, so the step must not tick."""
    c, cid = _make_shop()
    s = _steps(c)['jofotara']
    assert s['done'] is False and s['detail'] == 'Jordan e-invoicing is not switched on.', s
    _set_einv(cid, tax_regime='jordan', enabled='1')
    real = _status_view_says(c)
    assert real['enabled'] is True and real['provider_configured'] is False   # the endpoint itself agrees
    s = _steps(c)['jofotara']
    assert s['done'] is False and s['detail'] == 'Switched on, but the connection is not finished.', s


def test_jofotara_done_only_when_the_status_endpoint_is_enabled_and_the_wired_provider_is_real(
        monkeypatch, _killswitch_clean):
    from commercial_runtime.einvoicing.providers.mock import MockProvider
    from commercial_runtime.einvoicing.providers.unconfigured import UnconfiguredProvider
    c, cid = _make_shop()
    # not switched on -> not done, whatever is wired
    _wire_provider(monkeypatch, _live_provider())
    s = _steps(c)['jofotara']
    assert s['done'] is False and 'not switched on' in s['detail'], s
    # switched on + a provider that can reach the authority -> done (the allow half)
    _set_einv(cid, tax_regime='jordan', enabled='1', provider='direct_istd')
    s = _steps(c)['jofotara']
    assert s['done'] is True and s['detail'] == 'Connected.', s
    real = _status_view_says(c)
    assert real['enabled'] is True and real['provider_configured'] is True
    # the wired provider is unconfigured -> not done, even though the shop chose a real one
    _wire_provider(monkeypatch, UnconfiguredProvider())
    s = _steps(c)['jofotara']
    assert s['done'] is False and 'not finished' in s['detail'], s
    # the shop's chosen provider is 'unconfigured' while a real one is wired -> not done
    _wire_provider(monkeypatch, _live_provider())
    _set_einv(cid, provider='unconfigured')
    s = _steps(c)['jofotara']
    assert s['done'] is False and 'not finished' in s['detail'], s
    # regime back to none -> off, whatever else is right
    _set_einv(cid, provider='direct_istd', tax_regime='none')
    s = _steps(c)['jofotara']
    assert s['done'] is False and 'not switched on' in s['detail'], s
    # the mock provider reports itself configured, so it mirrors the endpoint: done
    _wire_provider(monkeypatch, MockProvider())
    _set_einv(cid, tax_regime='jordan', provider='mock')
    assert _steps(c)['jofotara']['done'] is True


def test_jofotara_a_wired_provider_that_says_it_is_not_configured_is_not_done(monkeypatch, _killswitch_clean):
    """`provider_configured` is its own guard, separate from the provider NAME.
    Only UnconfiguredProvider reports is_configured=False today, and it is also
    named 'unconfigured', so the name check alone would hide a broken
    `provider_configured` check. A real provider (DirectISTDProvider) whose own
    flag says 'not configured' -- the base-class contract for exactly this --
    is the case only that check can catch."""
    from commercial_runtime.einvoicing.providers.direct_istd import DirectISTDProvider

    class _HalfWired(DirectISTDProvider):
        is_configured = False

    c, cid = _make_shop()
    _wire_provider(monkeypatch, _HalfWired(base_url='https://jofotara.invalid', credentials=None))
    _set_einv(cid, tax_regime='jordan', enabled='1', provider='direct_istd')
    real = _status_view_says(c)
    assert real['enabled'] is True and real['provider'] == 'direct_istd'
    s = _steps(c)['jofotara']
    assert s['done'] is False and 'not finished' in s['detail'], s


def test_jofotara_paused_by_killswitch_or_env_is_not_done_and_says_paused(monkeypatch, _killswitch_clean):
    c, cid = _make_shop()
    _wire_provider(monkeypatch, _live_provider())
    _set_einv(cid, tax_regime='jordan', enabled='1', provider='direct_istd')
    assert _steps(c)['jofotara']['done'] is True
    _killswitch_clean.set_disabled(_app_module._EINVOICING_APP_DATA_DIR, reason='test pause')
    s = _steps(c)['jofotara']
    assert s['done'] is False and s['detail'] == 'E-invoicing is paused on this computer.', s
    assert _status_view_says(c)['enabled'] is False              # the endpoint agrees it is off
    _killswitch_clean.clear_disabled(_app_module._EINVOICING_APP_DATA_DIR)
    assert _steps(c)['jofotara']['done'] is True
    monkeypatch.setenv('AURA_EINVOICING_DISABLED', '1')
    s = _steps(c)['jofotara']
    assert s['done'] is False and s['detail'] == 'E-invoicing is paused on this computer.', s
    monkeypatch.delenv('AURA_EINVOICING_DISABLED')
    assert _steps(c)['jofotara']['done'] is True


def test_another_shops_einvoicing_never_ticks_this_shop(monkeypatch, _killswitch_clean):
    a, cid_a = _make_shop()
    b, cid_b = _make_shop()
    _wire_provider(monkeypatch, _live_provider())
    _set_einv(cid_b, tax_regime='jordan', enabled='1', provider='direct_istd')
    assert _steps(b)['jofotara']['done'] is True
    assert _steps(a)['jofotara']['done'] is False


def test_jofotara_is_null_when_the_status_cannot_be_read(monkeypatch, _killswitch_clean):
    c, cid = _make_shop()

    def boom(*a, **k):
        raise RuntimeError('settings unreadable')
    monkeypatch.setattr(einv_settings, 'get_setting', boom)
    assert _steps(c)['jofotara']['done'] is None


def test_jofotara_is_null_in_a_build_without_the_einvoicing_routes(monkeypatch, _killswitch_clean):
    c, cid = _make_shop()
    fresh = dict(app.view_functions)
    fresh.pop('einvoicing_api._status')
    monkeypatch.setattr(app, 'view_functions', fresh)
    s = _steps(c)['jofotara']
    assert s['done'] is None and 'not part of this build' in s['detail'], s


def test_jofotara_is_null_when_the_status_view_answers_with_an_error_tuple(monkeypatch, _killswitch_clean):
    c, cid = _make_shop()
    monkeypatch.setitem(app.view_functions, 'einvoicing_api._status',
                        lambda: ({'status': 'error'}, 401))
    assert _steps(c)['jofotara']['done'] is None
