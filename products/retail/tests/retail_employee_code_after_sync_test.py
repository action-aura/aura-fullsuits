"""
Aura Retail -- inviting staff must still work on a device whose staff list
arrived by sync.

`POST /api/admin/employees` minted the next code as `EMP-{COUNT(users)+1}`.
That assumes every code in the table was minted HERE, contiguously. On a
multi-device shop it is false the moment one row arrives from another
device: the Mi Note 10, freshly joined on 2026-09-06 with five synced
accounts (ADMIN-0001, ADMIN-0002, EMP-0002, EMP-0003, EMP-0006), counted
five rows, minted EMP-0006, and the route died with
`sqlite3.IntegrityError: UNIQUE constraint failed: users.company_id,
users.employee_id` -- a 500 from the Employees screen. The demo laptop held
the same five rows and would have failed the same way on its next invite.

The allocator now takes the highest existing numeric suffix plus one, which
is byte-for-byte the old answer on every single-device install (contiguous
local codes -> max == count) and only differs where count+1 was already a
collision. Concurrent minting on two offline devices is still possible; the
sync apply re-numbers on arrival (`SyncService._resolve_employee_code`),
which is the other half of the same rule.

Run:
    pytest products/retail/tests/retail_employee_code_after_sync_test.py -v
"""
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_emp_code_sync_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402

OWNER_EMAIL = 'emp-code-sync-owner@test.local'
OWNER_PASSWORD = 'OwnerCodeSyncPW1'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _owner():
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': OWNER_EMAIL, 'password': OWNER_PASSWORD,
    })
    if r.status_code == 409:
        r = client.post('/api/auth/login', json={'email': OWNER_EMAIL, 'password': OWNER_PASSWORD})
    assert r.status_code == 200, r.get_json()
    return client


def _company_id():
    conn = registry_conn()
    try:
        return conn.execute("SELECT company_id FROM users WHERE email=?", (OWNER_EMAIL,)).fetchone()[0]
    finally:
        conn.close()


def _arrive_by_sync(company_id, employee_id, email):
    """A row exactly as the registry sync writes it: its own uid, a hash
    (the other device set the password), no local allocation involved."""
    conn = registry_conn()
    try:
        conn.execute(
            "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
            "require_password_change, uid, row_version, updated_at_utc) "
            "VALUES (?, ?, ?, ?, ?, 'cashier', 'active', 0, ?, 1, ?)",
            (str(uuid.uuid4()), company_id, employee_id, email, 'pbkdf2_sha256$x', str(uuid.uuid4()),
             user_accounts.now_utc_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _codes(company_id):
    conn = registry_conn()
    try:
        return sorted(r[0] for r in conn.execute(
            "SELECT employee_id FROM users WHERE company_id=?", (company_id,)).fetchall())
    finally:
        conn.close()


def test_invite_after_synced_rows_does_not_collide():
    owner = _owner()
    company_id = _company_id()
    # Two rows from another device with gaps in the numbering, exactly the
    # shape measured on the phone: COUNT is 3 here (owner + these two), so
    # the old allocator would mint EMP-0004 -- fine -- but bump the synced
    # code to the very value count+1 produces and it dies.
    _arrive_by_sync(company_id, 'EMP-0002', 'synced-two@test.local')
    _arrive_by_sync(company_id, 'EMP-0004', 'synced-four@test.local')
    assert _codes(company_id) == ['ADMIN-0001', 'EMP-0002', 'EMP-0004']

    r = owner.post('/api/admin/employees', json={'email': 'invited-after-sync@test.local', 'role': 'cashier'})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert 'EMP-0005' in _codes(company_id), _codes(company_id)

    # And the one after it keeps climbing, never re-minting a synced code.
    r = owner.post('/api/admin/employees', json={'email': 'invited-after-sync-2@test.local', 'role': 'cashier'})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert 'EMP-0006' in _codes(company_id), _codes(company_id)


def test_allocator_is_max_of_count_and_highest_code_plus_one():
    """The rule is `max(COUNT(users), highest EMP suffix) + 1`. On a fresh
    single-device install the highest suffix never exceeds the count (the
    owner is ADMIN-0001 and every EMP code was minted here as count+1), so
    the answer is byte-for-byte the old one -- the first invite is still
    EMP-0002. It only differs where a synced code already sits at count+1."""
    owner = _owner()
    company_id = _company_id()
    before = _codes(company_id)
    highest = max(int(c.rsplit('-', 1)[-1]) for c in before if c.startswith('EMP-'))
    expected = f'EMP-{max(len(before), highest) + 1:04d}'
    r = owner.post('/api/admin/employees', json={'email': 'plain-invite@test.local', 'role': 'cashier'})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert expected in _codes(company_id), (expected, _codes(company_id))


def test_fresh_install_first_invite_is_still_emp_0002():
    """Pins the "unchanged on a single device" half on the real first-run
    shape: exactly one owner row -> the first invite is EMP-0002, as every
    Clinic and Retail install has always minted it."""
    conn = registry_conn()
    try:
        conn.execute("DELETE FROM users WHERE email != ?", (OWNER_EMAIL,))
        conn.commit()
    finally:
        conn.close()
    owner = _owner()
    company_id = _company_id()
    assert _codes(company_id) == ['ADMIN-0001']
    r = owner.post('/api/admin/employees', json={'email': 'first-ever-invite@test.local', 'role': 'cashier'})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert _codes(company_id) == ['ADMIN-0001', 'EMP-0002'], _codes(company_id)
