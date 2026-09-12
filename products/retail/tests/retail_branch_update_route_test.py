"""
Aura Retail -- PUT /api/sub/retail/branches/<id> (update_branch).

`/branches` had only GET (list_branches) and POST (create_branch): a
mistyped branch name lived forever, and -- since Wave B -- synced everywhere,
because `branch` is a synced entity type (sync_service.py). The sync side
already converges renames (it applies `branch` events of type create AND
update by upserting on `uid`); nothing on the till side could ever emit one.
This file proves the missing route: tenant-scoped, CAP_EMPLOYEES-gated (the
same gate `create_branch` already carries), and emitting the `update` sync
event with the same payload shape `create` does.

Scope is deliberately narrow, matching retail_branches_test.js's own scope
note on the frontend side: name/address/phone ONLY. Retiring a branch
(status) is NOT this route -- that has stock, device-pin and cash-drawer
consequences that are a separate design question.

Run:
    pytest products/retail/tests/retail_branch_update_route_test.py -v
"""
import json
import os
import shutil
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_branch_update_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'
OWNER_EMAIL = 'branch-update-owner@test.local'
OWNER_PASSWORD = 'BranchUpdatePW1'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _owner():
    """The one admin this install allows -- same shape as
    retail_employee_management_test.py's `_owner()`."""
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': OWNER_EMAIL, 'password': OWNER_PASSWORD,
    })
    if r.status_code == 409:
        r = client.post('/api/auth/login', json={'email': OWNER_EMAIL, 'password': OWNER_PASSWORD})
    assert r.status_code == 200, r.get_json()
    return client


def _new_employee(owner, role='cashier'):
    """Through the REAL creation route -- same shape as
    retail_employee_management_test.py's `_new_employee()`."""
    email = f'branch-emp-{uuid.uuid4().hex[:8]}@test.local'
    r = owner.post('/api/admin/employees', json={'email': email, 'role': role})
    assert r.status_code == 200, r.get_json()
    conn = registry_conn()
    try:
        user_id = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()['id']
    finally:
        conn.close()
    return user_id, email


def _activate(user_id, password):
    """Give an invited (`pending_setup`, password_hash='PENDING') account a
    real password so it can log in -- same shape as
    retail_employee_management_test.py's `_activate()`."""
    conn = registry_conn()
    conn.execute("UPDATE users SET password_hash=?, status='active' WHERE id=?",
                 (hash_password(password), user_id))
    conn.commit()
    conn.close()


def _branches(client):
    return client.get(f'{API}/branches').get_json()['data']


def _create_branch(owner, name=None, address='', phone=''):
    """Through the REAL creation route, then re-read via GET so the returned
    row carries whatever the server actually stored -- including `uid`,
    which the POST response itself does not echo back."""
    r = owner.post(f'{API}/branches', json={
        'name': name or f'Branch-{uuid.uuid4().hex[:8]}', 'address': address, 'phone': phone,
    })
    assert r.status_code == 200, r.get_json()
    bid = r.get_json()['data']['id']
    return next(b for b in _branches(owner) if b['id'] == bid)


def _outbox():
    """Every `sync_outbox` row queued so far, oldest first -- same shape as
    retail_settings_sync_emit_test.py's `_outbox()`."""
    conn = get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT entity_type, entity_id, event_type, payload FROM sync_outbox ORDER BY created_at, rowid"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _branch_outbox_rows():
    """Only the `branch` outbox rows, decoded -- what every sync-side
    assertion below reads. All tests share ONE owner/company (module-level
    OWNER_EMAIL, same convention as retail_settings_sync_emit_test.py), so
    this accumulates across test functions -- callers snapshot the length
    BEFORE their own action and assert on the newly appended slice, never on
    an absolute count."""
    rows = []
    for r in _outbox():
        if r['entity_type'] != 'branch':
            continue
        rows.append({'entity_id': r['entity_id'], 'event_type': r['event_type'],
                      'payload': json.loads(r['payload'])})
    return rows


# ═════════════════════════════════════════════════════════════════════════════
# Rename -- 200, and the change is actually visible on GET /branches
# ═════════════════════════════════════════════════════════════════════════════

def test_rename_updates_name_address_and_phone():
    owner = _owner()
    branch = _create_branch(owner, name='Downtown', address='1 Main St', phone='0700000000')

    r = owner.put(f'{API}/branches/{branch["id"]}', json={
        'name': 'Downtown Renamed', 'address': '2 Main St', 'phone': '0799999999',
    })
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['id'] == branch['id']

    updated = next(b for b in _branches(owner) if b['id'] == branch['id'])
    assert updated['name'] == 'Downtown Renamed'
    assert updated['address'] == '2 Main St'
    assert updated['phone'] == '0799999999'


# ═════════════════════════════════════════════════════════════════════════════
# Sync -- exactly ONE `branch` outbox row, event_type='update', uid matches
# ═════════════════════════════════════════════════════════════════════════════

def test_rename_queues_exactly_one_branch_update_event_with_the_row_uid():
    owner = _owner()
    branch = _create_branch(owner, name='Outbox Branch')
    assert branch['uid'], 'create_branch did not mint a uid -- fixture is broken, not the route under test'
    before = len(_branch_outbox_rows())

    r = owner.put(f'{API}/branches/{branch["id"]}', json={'name': 'Outbox Branch Renamed'})
    assert r.status_code == 200, r.get_json()

    new_rows = _branch_outbox_rows()[before:]
    assert len(new_rows) == 1, (
        f'expected exactly 1 new `branch` outbox row from one rename, got {len(new_rows)}: {new_rows}')
    row = new_rows[0]
    assert row['event_type'] == 'update', row
    assert row['entity_id'] == branch['uid'], (
        f"outbox entity_id {row['entity_id']!r} does not match the branch's own uid {branch['uid']!r} -- "
        f"sync_service.py's branch handler upserts on uid, so a mismatch here means the rename would never "
        f"converge on another device")
    assert row['payload']['uid'] == branch['uid']
    assert row['payload']['name'] == 'Outbox Branch Renamed'


# ═════════════════════════════════════════════════════════════════════════════
# Guards -- blank name (400) and unknown id (404)
# ═════════════════════════════════════════════════════════════════════════════

def test_blank_name_is_refused_with_400():
    owner = _owner()
    branch = _create_branch(owner, name='Has A Name')
    before = len(_branch_outbox_rows())

    r = owner.put(f'{API}/branches/{branch["id"]}', json={'name': '   '})
    assert r.status_code == 400, r.get_json()
    assert r.get_json()['message'] == 'Branch name required'

    # No sync event for a refused write, and the stored name is untouched.
    assert len(_branch_outbox_rows()) == before
    assert next(b for b in _branches(owner) if b['id'] == branch['id'])['name'] == 'Has A Name'


def test_unknown_branch_id_is_404():
    owner = _owner()
    r = owner.put(f'{API}/branches/999999', json={'name': 'Nope'})
    assert r.status_code == 404, r.get_json()
    assert r.get_json()['message'] == 'Branch not found'


# ═════════════════════════════════════════════════════════════════════════════
# Capability gate -- a cashier (no CAP_EMPLOYEES) is refused, same as
# create_branch's own @mt_require_capability(CAP_EMPLOYEES)
# ═════════════════════════════════════════════════════════════════════════════

def test_cashier_without_cap_employees_gets_403():
    owner = _owner()
    branch = _create_branch(owner, name='Cashier Guarded Branch')
    user_id, email = _new_employee(owner, role='cashier')
    _activate(user_id, 'BranchCashierPW1')

    cashier = app.test_client()
    assert cashier.post('/api/auth/login', json={
        'email': email, 'password': 'BranchCashierPW1',
    }).status_code == 200

    r = cashier.put(f'{API}/branches/{branch["id"]}', json={'name': 'Should Not Apply'})
    assert r.status_code == 403, r.get_json()

    # The refusal must be enforced server-side, not merely reported: the
    # stored name is unchanged.
    assert next(b for b in _branches(owner) if b['id'] == branch['id'])['name'] == 'Cashier Guarded Branch'


# ═════════════════════════════════════════════════════════════════════════════
# Legacy self-heal -- a pre-v13 row with NULL uid gets one minted on rename,
# the same way _default_branch self-heals a branch it invents
# ═════════════════════════════════════════════════════════════════════════════

def test_legacy_row_with_null_uid_self_heals_on_rename():
    owner = _owner()
    # A real branch supplies a real company_id for the direct INSERT below
    # without this test needing to know the registry schema for
    # users.company_id -- same trick retail_device_branch_pin_test.py uses.
    seed_branch = _create_branch(owner, name='Seed For Company Id')
    company_id = seed_branch['company_id']

    conn = get_retail_conn()
    try:
        conn.execute(
            "INSERT INTO branches (company_id, name, address, phone) VALUES (?, 'Legacy Branch', '', '')",
            (company_id,))
        conn.commit()
        legacy_row = conn.execute(
            "SELECT id, uid FROM branches WHERE company_id=? AND name='Legacy Branch'", (company_id,)
        ).fetchone()
        legacy_id = legacy_row['id']
        assert legacy_row['uid'] is None, 'fixture is broken: the legacy row already has a uid'
    finally:
        conn.close()

    before = len(_branch_outbox_rows())
    r = owner.put(f'{API}/branches/{legacy_id}', json={'name': 'Legacy Branch Renamed'})
    assert r.status_code == 200, r.get_json()

    conn = get_retail_conn()
    try:
        row = conn.execute("SELECT uid, name FROM branches WHERE id=?", (legacy_id,)).fetchone()
    finally:
        conn.close()
    assert row['uid'], 'the legacy NULL-uid branch was not self-healed on rename'
    assert row['name'] == 'Legacy Branch Renamed'

    new_rows = _branch_outbox_rows()[before:]
    assert len(new_rows) == 1, new_rows
    assert new_rows[0]['payload']['uid'] == row['uid'], (
        'the outbox event carries a different uid than the one actually written to the row -- '
        'a receiver applying this event would converge on the wrong identity')
