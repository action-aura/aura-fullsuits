"""REHEARSAL-1007 B5: what the owner types at sign-up must belong to the SHOP and to the
ACCOUNT, not to one computer's config file.

TWO DEFECTS, both measured in the two-till rehearsal:
  1. The shop name typed at sign-up lived only in computer A's config.json. Computer B
     (joined with the licence key) answered `business_name_source: none` -- no name on
     its sidebar or receipts -- because nothing ever turned that file entry into the
     synced `branding_business_name` setting.
  2. The person's own name typed at sign-up went into config.json's admin_name and nowhere
     else; the account row (users.display_name) stayed empty, so the greeting said "Owner"
     and no other computer ever learned the name.

HOW "A SECOND INSTALL" IS MODELLED (A and B cannot be two live apps in one process --
AURA_APP_DATA resolves at import): A's real outbox event is captured, B's side is the same
database with the setting row removed and config.json rewritten the way a JOINED computer
has it (no admin_email), then the real SyncService.apply_pull_result applies the event and the
real branding route answers.

Run (one pytest process per file):
    pytest products/retail/tests/retail_rehearsal_b5_shop_identity_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_rehearsal_b5_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA), AURA_SITE_RELAY_ENABLED="0")
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
import api.retail_api as retail_api  # noqa: E402

API = '/api/sub/retail'
CONFIG = DATA / 'config.json'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _write_config(**kw):
    CONFIG.write_text(json.dumps(kw), encoding='utf-8')


@pytest.fixture
def client():
    company_id = str(uuid.uuid4())
    email = f'b5-{uuid.uuid4().hex[:8]}@test.local'
    password = 'B5PW12345678'  # pragma: allowlist secret
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'))
    conn.commit(); conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    c.test_company_id = company_id
    return c


def _branding_row(cid):
    conn = get_retail_conn()
    try:
        return conn.execute("SELECT svalue FROM retail_settings WHERE company_id=? "
                            "AND skey='branding_business_name'", (cid,)).fetchone()
    finally:
        conn.close()


def _name_events():
    conn = get_retail_conn()
    try:
        out = []
        for r in conn.execute("SELECT payload FROM sync_outbox WHERE entity_type='retail_setting' ORDER BY rowid"):
            p = json.loads(r['payload'])
            if p.get('skey') == 'branding_business_name':
                out.append(p)
        return out
    finally:
        conn.close()


def _cid_of(client):
    return client.test_company_id


def test_signup_shop_name_becomes_a_synced_setting_and_reaches_the_second_till(client):
    cid = _cid_of(client)
    _write_config(company_id='Sweets & Co', admin_email='owner@a.test')      # computer A ran sign-up
    got = client.get(f'{API}/settings/branding').get_json()['data']
    assert got['business_name'] == 'Sweets & Co'
    assert got['branding_business_name'] == 'Sweets & Co', 'the name must now be a stored setting'
    assert got['business_name_source'] == 'branding'
    events = _name_events()
    assert events and events[-1]['svalue'] == 'Sweets & Co', 'and it must be queued for sync'

    # Computer B: no admin_email in its config, never saw the row, pulls A's event.
    conn = get_retail_conn()
    conn.execute("DELETE FROM retail_settings WHERE company_id=? AND skey='branding_business_name'", (cid,))
    conn.commit(); conn.close()
    _write_config(company_id='some-licence-id')                                 # a JOINED computer
    conn = get_retail_conn()
    try:
        svc = SyncService(client_factory=lambda: None, get_conn=get_retail_conn,
                          local_company_id_provider=lambda: cid,
                          local_ensure_schema=retail_api._ensure_credit_schema)
        svc.apply_pull_result(conn, {"events": [{
            "entity_type": "retail_setting", "event_type": "update",
            "payload": events[-1], "seq": 1}], "cursor": 1})
        conn.commit()
    finally:
        conn.close()
    on_b = client.get(f'{API}/settings/branding').get_json()['data']
    assert on_b['business_name'] == 'Sweets & Co'
    assert on_b['business_name_source'] == 'branding', on_b


def test_a_joined_computer_never_seeds_a_name_of_its_own(client):
    cid = _cid_of(client)
    before = len(_name_events())
    _write_config(company_id='licence-or-whatever')                              # no admin_email
    got = client.get(f'{API}/settings/branding').get_json()['data']
    assert _branding_row(cid) is None
    assert got['business_name_source'] == 'signup'                              # read-time fallback only
    assert len(_name_events()) == before


def test_a_name_the_owner_cleared_on_purpose_is_not_reseeded(client):
    cid = _cid_of(client)
    _write_config(company_id='Sweets & Co', admin_email='owner@a.test')
    assert client.post(f'{API}/settings/branding', json={'branding_business_name': ''}).status_code == 200
    client.get(f'{API}/settings/branding')
    row = _branding_row(cid)
    assert row is not None and row['svalue'] == '', 'an existing (empty) row must never be overwritten'


def test_owner_display_name_typed_at_signup_persists_and_is_queued_for_sync():
    """create-admin on a registry with no admin must put `name` on the ACCOUNT."""
    conn = registry_conn()
    conn.execute("DELETE FROM users WHERE role='admin'")
    conn.commit(); conn.close()
    email = f'layla-{uuid.uuid4().hex[:6]}@test.local'
    password = 'LaylaPW123456'  # pragma: allowlist secret
    c = app.test_client()
    r = c.post('/api/onboarding/create-admin', json={
        'name': 'Layla Haddad', 'email': email, 'password': password, 'company_name': 'Layla Sweets',
        'country': 'JO', 'timezone': 'Asia/Amman', 'currency': 'JOD', 'language': 'en'})
    assert r.status_code in (200, 201), r.get_json()
    conn = registry_conn()
    try:
        row = conn.execute("SELECT display_name, uid FROM users WHERE email=?", (email,)).fetchone()
        assert row['display_name'] == 'Layla Haddad'
        ev = conn.execute("SELECT payload FROM sync_outbox WHERE entity_type='user' AND entity_id=? "
                          "AND event_type='create'", (row['uid'],)).fetchone()
        assert json.loads(ev['payload'])['display_name'] == 'Layla Haddad', \
            'a second computer must learn the name from the create event'
    finally:
        conn.close()
    me = c.get('/api/auth/session').get_json()
    assert me['user']['display_name'] == 'Layla Haddad', me


def test_signup_with_an_unusable_name_still_succeeds_and_leaves_display_name_empty():
    conn = registry_conn()
    conn.execute("DELETE FROM users WHERE role='admin'")
    conn.commit(); conn.close()
    email = f'long-{uuid.uuid4().hex[:6]}@test.local'
    c = app.test_client()
    r = c.post('/api/onboarding/create-admin', json={
        'name': 'x' * 200, 'email': email, 'password': 'LongNamePW123',  # pragma: allowlist secret
        'company_name': 'Shop', 'country': 'JO', 'timezone': 'Asia/Amman', 'currency': 'JOD'})
    assert r.status_code in (200, 201), r.get_json()
    conn = registry_conn()
    try:
        assert conn.execute("SELECT display_name FROM users WHERE email=?", (email,)).fetchone()['display_name'] is None
    finally:
        conn.close()
