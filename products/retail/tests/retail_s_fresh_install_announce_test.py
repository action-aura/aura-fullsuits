"""Aura Retail -- a FRESH install announces itself the moment it has a company,
not at the next restart.

THE DEFECT (found by crew U, driving the real Team screen on a fresh install):
the first session after signup reported `role_editing.allowed == false` and
listed THIS computer as a blocker, until the backend was restarted. Two causes
stacked:

  1. `app.py::_announce_device('boot')` runs at process start and does nothing
     when no company exists yet -- which is exactly the state of a fresh
     install at boot. Signup creates the company a moment LATER.
  2. The only other announcer is the registry SyncService heartbeat, and a
     till that has not paired with a relay has no SyncService.

So the device row was created by the first login with NULL platform, NULL app
version and NULL registry_schema_version, and `registry_rollout_blockers`
treats a NULL registry version as "old build" -> role editing refused.

THE FIX: the moment a company comes into being on this install, announce.
  - signup: `onboarding_routes.create_admin` calls
    `device_context.notify_company_established(...)` after its final commit;
    `app.py` registers `_announce_device('changed')` as that hook.
  - activation first (a joining device: the licence seeds `company_settings`
    with no admin yet): `app.py::_on_licence_activated` announces after the
    rebind, before sync starts, so the event rides the first push.

Bootstrap mirrors retail_activation_order_test.py: the REAL app, the REAL
/api/onboarding/create-admin route over HTTP, the REAL activation hook.

MUTATION PROOFS: see the report that introduces this file -- removing the
`notify_company_established` call in create_admin turns
`test_signup_announces_this_computer_without_a_restart` RED; removing the
`_announce_device('changed')` call in `_on_licence_activated` turns
`test_activation_first_announces_this_computer_before_any_admin_exists` RED;
each leaves the other green (they guard different seams).

Run (ONE FILE PER PYTEST PROCESS):
    pytest products/retail/tests/retail_s_fresh_install_announce_test.py -v
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_fresh_announce_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_SYNC_RELAY_URL", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import device_context, device_registry  # noqa: E402
from commercial_runtime.identity.registry_db import (  # noqa: E402
    REGISTRY_SCHEMA_VERSION,
    get_conn as registry_conn,
)

REGISTRY_DB = DATA / 'database' / 'registry.db'
LICENSING_DB = DATA / 'database' / 'subsystems' / 'licensing.db'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _reset_databases():
    for db_path in (REGISTRY_DB, DATA / 'database' / 'subsystems' / 'retail.db', LICENSING_DB):
        for suffix in ('', '-wal', '-shm'):
            f = Path(str(db_path) + suffix)
            if f.exists():
                f.unlink()
    config_path = DATA / 'config.json'
    if config_path.exists():
        config_path.unlink()
    _app_module._split_state_refusal['reason'] = None
    device_context.invalidate_cache()
    _app_module.init_app()


def _write_licence_state(license_public_id):
    os.makedirs(LICENSING_DB.parent, exist_ok=True)
    conn = sqlite3.connect(str(LICENSING_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS licensing_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            licensing_schema_version INTEGER NOT NULL,
            product_code TEXT NOT NULL,
            platform TEXT NOT NULL,
            current_state TEXT NOT NULL,
            owner_installation_id TEXT,
            assertion_envelope_json TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    payload = {
        'assertion_id': str(uuid.uuid4()),
        'product_code': 'AURA_RETAIL',
        'license_public_id': license_public_id,
        'installation_public_id': str(uuid.uuid4()),
    }
    conn.execute('DELETE FROM licensing_state')
    conn.execute(
        'INSERT INTO licensing_state (id, licensing_schema_version, product_code, platform, '
        'current_state, owner_installation_id, assertion_envelope_json, updated_at) '
        'VALUES (1,1,?,?,?,?,?,?)',
        ('AURA_RETAIL', 'WINDOWS', 'ACTIVE_ONLINE', payload['installation_public_id'],
         json.dumps({'payload': payload}), '2026-08-24T00:00:00'),
    )
    conn.commit()
    conn.close()


def _device_rows(company_id):
    conn = registry_conn()
    try:
        return device_registry.list_devices(conn, company_id)
    finally:
        conn.close()


# -- 1. signup ---------------------------------------------------------------

def test_signup_announces_this_computer_without_a_restart():
    """Fresh install, signup, then the very next GET /api/devices -- no restart,
    no heartbeat, no sync service. THIS computer must already carry its
    platform, its build and its registry version, and role editing must be
    allowed. Before the fix: platform/app_version None, role_editing.allowed
    False with this computer listed as the blocker."""
    _reset_databases()
    assert _app_module._sync_service is None, (
        'fixture bug: a SyncService is running, so its heartbeat could announce and hide '
        'the very gap this test exists for')

    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'fresh-owner@test.local',
        'password': 'FreshInstallPW1', 'company_name': 'Fresh Install Co',
    })
    assert r.status_code == 200, r.get_json()

    body = client.get('/api/devices').get_json()
    assert body['success'] is True, body
    mine = [d for d in body['devices'] if d['is_this_device']]
    assert len(mine) == 1, f'expected exactly this computer in the list, got {body["devices"]!r}'
    me = mine[0]
    assert me['platform'] == device_context.detect_platform(), me
    assert me['app_version'] == _app_module.APP_VERSION, me
    assert me['registry_schema_version'] == REGISTRY_SCHEMA_VERSION, me
    assert body['role_editing']['allowed'] is True, body['role_editing']
    assert body['role_editing']['blocking_devices'] == []


# -- 2. activation before any admin (a joining computer) ---------------------

def test_activation_first_announces_this_computer_before_any_admin_exists():
    """The other way a company comes into being: a joining computer activates
    with the shop's key BEFORE anyone signs up here. Activation seeds
    `company_settings` with the licence id, so a company now exists -- and no
    admin does, so nothing else will announce (no signup, no session, no sync
    service). The activation hook must."""
    _reset_databases()
    owner_company_id = f'owner-issued-{uuid.uuid4()}'
    assert _device_rows(owner_company_id) == [], 'fixture bug: a device row exists before activation'

    _write_licence_state(owner_company_id)
    _app_module._on_licence_activated()

    rows = _device_rows(owner_company_id)
    assert len(rows) == 1, f'activation did not announce this computer: {rows!r}'
    me = rows[0]
    assert me['platform'] == device_context.detect_platform(), me
    assert me['app_version'] == _app_module.APP_VERSION, me
    assert me['registry_schema_version'] == REGISTRY_SCHEMA_VERSION, me


# -- 3. the hook must never break signup -------------------------------------

def test_a_failing_announce_hook_never_fails_signup():
    """`create_admin` has already committed the account and opened the session
    by the time the hook runs, so an announce failure must not turn a
    successful signup into a 500."""
    _reset_databases()

    def boom(*a, **k):
        raise RuntimeError('announce exploded')

    previous = device_context.set_company_established_hook(boom)
    try:
        client = app.test_client()
        r = client.post('/api/onboarding/create-admin', json={
            'name': 'Owner', 'email': 'hook-boom@test.local',
            'password': 'FreshInstallPW1', 'company_name': 'Boom Co',
        })
        assert r.status_code == 200, r.get_json()
    finally:
        device_context.set_company_established_hook(previous)
