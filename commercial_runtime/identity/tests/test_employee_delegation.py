"""Wave D -- delegation: a branch manager (role='manager' AND holding
`retail.employees` AND a non-NULL `branch_scope_uid`) may create and manage
CASHIERS AT THEIR OWN BRANCH ONLY.

docs/launch-readiness/account-hierarchy-design.md §2.4 (the allocator fix
and BEGIN IMMEDIATE), §3.4 (the matrix), §4.1 (guards G1-G8), §4.2
(enforcement points D1-D4, D9), §9 (the Clinic proof), §12 (risks) -- this
file pins the identity-layer surface those sections describe:
`onboarding_routes.py::create_employee` (D1), `::update_status` (D2),
`::update_pin` (D3), `::get_employees` (D4), and the `_delegated_employee_
id_fragment()` allocator (D0).

Same standalone bootstrap as test_employee_admin_routes.py in this package
(`registry_db.DB_PATH` redirected at a temp file, the real v7 schema built
through `init_registry_db()`) -- see that file's own docstring for why:
this runs against the ACTUAL registry, not a hand-rolled shape.

Run:
    pytest commercial_runtime/identity/tests/test_employee_delegation.py -v
"""
import re
import sqlite3
import uuid
from unittest import mock

import pytest
from flask import Flask

from commercial_runtime.identity import mt_auth, onboarding_routes, registry_db, user_accounts
from commercial_runtime.identity.onboarding_routes import onboarding_bp


# ── Bootstrap (identical to test_employee_admin_routes.py) ─────────────────

@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(path))
    monkeypatch.setattr(registry_db, "_db_dir", str(tmp_path))
    registry_db.init_registry_db()
    return path


@pytest.fixture
def app(db_path, tmp_path, monkeypatch):
    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(onboarding_routes, "get_conn", _get_conn)
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path))
    monkeypatch.setattr(mt_auth, "REGISTRY_DB", str(db_path))

    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"
    flask_app.testing = True
    flask_app.register_blueprint(onboarding_bp)
    return flask_app


@pytest.fixture
def admin(app):
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner@test.local', 'password': 'OwnerPW11',
    })
    assert r.status_code == 200, r.get_json()
    return client


def _make_employee(admin, role=None):
    email = f'emp-{uuid.uuid4().hex[:8]}@test.local'
    body = {'email': email}
    if role:
        body['role'] = role
    r = admin.post('/api/admin/employees', json=body)
    assert r.status_code == 200, r.get_json()
    employees = admin.get('/api/admin/employees').get_json()['employees']
    row = next(e for e in employees if e['email'] == email)
    return row['id'], email


def _user(db_path, user_id):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    finally:
        conn.close()


def _user_by_email(db_path, email):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    finally:
        conn.close()


def _caps(db_path, user_id):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return {r['subsystem']: r['access_level'] for r in conn.execute(
            "SELECT subsystem, access_level FROM user_permissions WHERE user_id=?", (user_id,))}
    finally:
        conn.close()


def _login_as(app, db_path, user_id):
    """Build a session for `user_id` the way a real login would leave it,
    without needing a password round trip -- mirrors test_employee_admin_
    routes.py's own `_login_as` (and, further back, test_device_routes.py's
    `_login`)."""
    row = _user(db_path, user_id)
    client = app.test_client()
    with client.session_transaction() as s:
        s['mt_user_id'] = user_id
        s['company_id'] = row['company_id']
        s['mt_role'] = row['role']
        s['email'] = row['email']
        s['mt_session_version'] = row['session_version']
    return client


def _make_branch_manager(app, admin, db_path, *, scope='branch-uid-1', grant=True):
    """A manager, scoped to `scope`, with `retail.employees` granted
    (unless `grant=False`, for the default-off/redundant-pair tests) --
    through the REAL admin routes (D5's scope setter, `update_perms`), then
    logged in. Returns (manager_id, logged-in client)."""
    manager_id, _email = _make_employee(admin, 'manager')
    r = admin.put(f'/api/admin/employees/{manager_id}/branch-scope',
                  json={'branch_scope_uid': scope})
    assert r.status_code == 200, r.get_json()
    if grant:
        r = admin.post(f'/api/admin/employees/{manager_id}/permissions',
                       json={'subsystem': user_accounts.CAP_EMPLOYEES, 'access_level': 'full'})
        assert r.status_code == 200, r.get_json()
    return manager_id, _login_as(app, db_path, manager_id)


EMP_ID_ADMIN_RE = re.compile(r'^EMP-\d{4}$')
EMP_ID_DELEGATED_RE = re.compile(r'^EMP-[0-9a-f]{4}-\d{4}$')


# ═════════════════════════════════════════════════════════════════════════
# D0 -- the allocator
# ═════════════════════════════════════════════════════════════════════════

def test_admin_path_mints_the_byte_identical_admin_id_format(admin, db_path):
    """§9 point 3: the admin arm's id format is UNTOUCHED. No device
    fragment, ever -- Clinic and every existing Retail install keep
    minting `EMP-NNNN`."""
    user_id, _ = _make_employee(admin)
    emp_id = _user(db_path, user_id)['employee_id']
    assert EMP_ID_ADMIN_RE.match(emp_id), emp_id
    assert '-' not in emp_id[4:], f"the admin arm's id carries an unexpected extra segment: {emp_id}"


def test_delegated_create_mints_the_dev4_discriminated_id_format(app, admin, db_path):
    manager_id, manager = _make_branch_manager(app, admin, db_path)
    r = manager.post('/api/admin/employees', json={'email': 'cashier-fmt@test.local', 'role': 'cashier'})
    assert r.status_code == 200, r.get_json()
    emp_id = _user_by_email(db_path, 'cashier-fmt@test.local')['employee_id']
    assert EMP_ID_DELEGATED_RE.match(emp_id), emp_id


def test_two_devices_at_the_same_local_count_mint_different_delegated_ids(app, admin, db_path):
    """Design §2.2's silent-fork failure, closed: two DEVICES with the SAME
    local `COUNT(*)` (because neither has yet seen the other's create) must
    mint DIFFERENT employee_ids -- the whole point of D0. Simulated by
    deleting device A's row after it creates (reproducing the local count
    a genuinely offline second device would independently compute) and
    swapping the patched device uuid before device B creates."""
    manager_id, manager = _make_branch_manager(app, admin, db_path)

    with mock.patch.object(onboarding_routes, 'peek_local_device_uuid', return_value='aaaa1111'):
        r1 = manager.post('/api/admin/employees', json={'email': 'deva@test.local', 'role': 'cashier'})
    assert r1.status_code == 200, r1.get_json()
    id_a = _user_by_email(db_path, 'deva@test.local')['employee_id']

    # Reproduce the SAME local COUNT a second, still-offline device would
    # independently see -- delete device A's row (no FK from user_
    # permissions to users, so this is a safe raw delete in a test).
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM users WHERE email=?", ('deva@test.local',))
    conn.commit()
    conn.close()

    with mock.patch.object(onboarding_routes, 'peek_local_device_uuid', return_value='bbbb2222'):
        r2 = manager.post('/api/admin/employees', json={'email': 'devb@test.local', 'role': 'cashier'})
    assert r2.status_code == 200, r2.get_json()
    id_b = _user_by_email(db_path, 'devb@test.local')['employee_id']

    assert id_a != id_b, (id_a, id_b)
    frag_a, count_a = id_a.split('-')[1], id_a.split('-')[2]
    frag_b, count_b = id_b.split('-')[1], id_b.split('-')[2]
    assert frag_a != frag_b, "the device fragment did not actually vary between the two devices"
    assert count_a == count_b, (
        "the two devices did not actually share the same local count -- this test proves "
        "nothing about the collision D0 exists to close"
    )


def test_delegated_allocator_falls_back_to_a_random_fragment_never_a_shared_constant(app, admin, db_path):
    """A device whose UUID was never generated (fresh install,
    pre-activation) falls back to a random 4-hex fragment -- never a
    shared constant, which would silently collide across every such
    install the exact way the bug D0 fixes does."""
    manager_id, manager = _make_branch_manager(app, admin, db_path)
    with mock.patch.object(onboarding_routes, 'peek_local_device_uuid', return_value=None):
        r1 = manager.post('/api/admin/employees', json={'email': 'nouuid-a@test.local', 'role': 'cashier'})
        r2 = manager.post('/api/admin/employees', json={'email': 'nouuid-b@test.local', 'role': 'cashier'})
    assert r1.status_code == 200 and r2.status_code == 200
    id_a = _user_by_email(db_path, 'nouuid-a@test.local')['employee_id']
    id_b = _user_by_email(db_path, 'nouuid-b@test.local')['employee_id']
    assert EMP_ID_DELEGATED_RE.match(id_a) and EMP_ID_DELEGATED_RE.match(id_b)
    assert id_a.split('-')[1] != id_b.split('-')[1], (
        "two fallback fragments were identical -- looks like a shared constant, not a random draw"
    )


def test_delegated_allocator_falls_back_when_the_device_file_is_corrupt(app, admin, db_path):
    """`peek_local_device_uuid()` raises `LocalDeviceStateCorruptError` for
    an unreadable-but-present device file (its own docstring) --
    `_delegated_employee_id_fragment()` must never let that exception
    escape and break account creation over a device-identity problem."""
    manager_id, manager = _make_branch_manager(app, admin, db_path)
    with mock.patch.object(onboarding_routes, 'peek_local_device_uuid', side_effect=RuntimeError('corrupt')):
        r = manager.post('/api/admin/employees', json={'email': 'corrupt-dev@test.local', 'role': 'cashier'})
    assert r.status_code == 200, r.get_json()
    emp_id = _user_by_email(db_path, 'corrupt-dev@test.local')['employee_id']
    assert EMP_ID_DELEGATED_RE.match(emp_id), emp_id


# ═════════════════════════════════════════════════════════════════════════
# D1 -- create_employee: the legitimate case (allow-half) + G1/G2/G3/G6
# ═════════════════════════════════════════════════════════════════════════

def test_a_branch_manager_successfully_creates_an_own_branch_cashier(app, admin, db_path):
    """THE allow-half M6 exists to catch: a gate that denies everything
    passes every escalation test in this file."""
    manager_id, manager = _make_branch_manager(app, admin, db_path, scope='branch-main')
    r = manager.post('/api/admin/employees', json={'email': 'legit-cashier@test.local', 'role': 'cashier'})
    assert r.status_code == 200, r.get_json()
    assert 'setup_link' in r.get_json()

    row = _user_by_email(db_path, 'legit-cashier@test.local')
    assert row is not None
    assert user_accounts.normalize_role(row['role']) == user_accounts.ROLE_CASHIER
    assert row['branch_scope_uid'] == 'branch-main', "G3: scope was not stamped from the creator's own row"


def test_delegated_created_cashier_holds_exactly_the_role_seeded_capabilities(app, admin, db_path):
    """G2's subset invariant, pinned against the LIVE constants (design
    §4.1 G2): a delegated creator can never confer a capability they do
    not hold. `ROLE_CAPABILITIES[cashier]` is a strict subset of the
    manager's own set (CAP_EMPLOYEES/CAP_CASH_APPROVE excluded from
    both), so this also proves no owner-only code ever leaks through."""
    manager_id, manager = _make_branch_manager(app, admin, db_path)
    r = manager.post('/api/admin/employees', json={'email': 'exact-caps@test.local', 'role': 'cashier'})
    assert r.status_code == 200, r.get_json()
    user_id = _user_by_email(db_path, 'exact-caps@test.local')['id']

    caps = _caps(db_path, user_id)
    granted = {code for code, level in caps.items() if level == user_accounts.ACCESS_FULL}
    expected = user_accounts.ROLE_CAPABILITIES[user_accounts.ROLE_CASHIER]
    assert granted == set(expected), (granted, expected)
    assert user_accounts.CAP_EMPLOYEES not in granted
    assert user_accounts.CAP_CASH_APPROVE not in granted


def test_delegated_create_accepts_the_legacy_employee_alias_as_cashier(app, admin, db_path):
    """G1 via `normalize_role`, not string equality: the legacy 'employee'
    alias must resolve to cashier and be ACCEPTED, not wrongly refused by
    a naive `role != 'cashier'` check."""
    manager_id, manager = _make_branch_manager(app, admin, db_path)
    r = manager.post('/api/admin/employees', json={'email': 'legacy-alias@test.local', 'role': 'employee'})
    assert r.status_code == 200, r.get_json()
    row = _user_by_email(db_path, 'legacy-alias@test.local')
    assert user_accounts.normalize_role(row['role']) == user_accounts.ROLE_CASHIER


def test_g1_delegated_create_refuses_a_manager_role(app, admin, db_path):
    manager_id, manager = _make_branch_manager(app, admin, db_path)
    r = manager.post('/api/admin/employees', json={'email': 'wants-manager@test.local', 'role': 'manager'})
    assert r.status_code == 403, r.get_json()
    assert _user_by_email(db_path, 'wants-manager@test.local') is None, \
        "a refused create must create nothing"


def test_g2_delegated_create_refuses_a_permissions_dict_with_403(app, admin, db_path):
    """Design §0.2/§4.1 G2 -- the widest escalation path on the board if
    left open: refused LOUDLY (403), never silently dropped."""
    manager_id, manager = _make_branch_manager(app, admin, db_path)
    r = manager.post('/api/admin/employees', json={
        'email': 'wants-approve@test.local', 'role': 'cashier',
        'permissions': {user_accounts.CAP_CASH_APPROVE: 'full'},
    })
    assert r.status_code == 403, r.get_json()
    assert _user_by_email(db_path, 'wants-approve@test.local') is None, \
        "a refused create must create nothing -- the escalation attempt must not partially land"


def test_g2_delegated_create_refuses_a_recursive_employees_grant(app, admin, db_path):
    """The named worst case in design §0.2: a BM minting a cashier holding
    retail.employees itself -- a second account that can create accounts."""
    manager_id, manager = _make_branch_manager(app, admin, db_path)
    r = manager.post('/api/admin/employees', json={
        'email': 'wants-recursive@test.local', 'role': 'cashier',
        'permissions': {user_accounts.CAP_EMPLOYEES: 'full'},
    })
    assert r.status_code == 403, r.get_json()
    assert _user_by_email(db_path, 'wants-recursive@test.local') is None


def test_g3_delegated_create_ignores_a_request_supplied_scope(app, admin, db_path):
    """G3: even if a hand-made request tried to smuggle a scope field, this
    route's delegated arm has no such field to read in the first place --
    the stamped scope is always the creator's OWN row, read fresh."""
    manager_id, manager = _make_branch_manager(app, admin, db_path, scope='branch-real')
    r = manager.post('/api/admin/employees', json={
        'email': 'scope-smuggle@test.local', 'role': 'cashier',
        'branch_scope_uid': 'branch-someone-elses',
    })
    assert r.status_code == 200, r.get_json()
    row = _user_by_email(db_path, 'scope-smuggle@test.local')
    assert row['branch_scope_uid'] == 'branch-real'


def test_d1_gate_capability_check_actually_ran(app, admin, db_path):
    """ENGINEERING.md 'assert the check ran': a 403 alone cannot tell
    'the delegation guard refused' from 'something else refused'. A
    manager who IS role='manager' and IS scoped, but lacks retail.
    employees, guarantees Python's short-circuit `and` reaches
    `user_has_capability` -- so counting real calls to the exact function
    object the gate consults proves the gate's own check executed, not
    merely that a 403 came back from somewhere."""
    manager_id, manager = _make_branch_manager(app, admin, db_path, grant=False)

    calls = []
    original = user_accounts.user_has_capability

    def counting(conn, user_id, code):
        calls.append((user_id, code))
        return original(conn, user_id, code)

    with mock.patch.object(onboarding_routes._accounts, 'user_has_capability', side_effect=counting):
        r = manager.post('/api/admin/employees', json={'email': 'ungranted@test.local', 'role': 'cashier'})

    assert r.status_code == 403, r.get_json()
    assert calls, "the delegation gate's capability check never ran -- the 403 could have come from anywhere"
    assert calls[0] == (manager_id, user_accounts.CAP_EMPLOYEES)
    assert _user_by_email(db_path, 'ungranted@test.local') is None


# ═════════════════════════════════════════════════════════════════════════
# Default-off + the redundant role/capability pair (M4)
# ═════════════════════════════════════════════════════════════════════════

def test_default_off_a_plain_manager_with_no_grant_is_refused_on_all_four_routes(app, admin, db_path):
    """DEFAULT-OFF: an install with no manager holding retail.employees
    behaves exactly as before this wave, on every one of the four routes.
    A plain manager (no scope, no grant) is just a non-admin -- refused
    everywhere, the identical 'Admin only' 403 that predates this wave."""
    plain_manager_id, _ = _make_employee(admin, 'manager')
    plain_manager = _login_as(app, db_path, plain_manager_id)
    target_id, _ = _make_employee(admin, 'cashier')

    r = plain_manager.post('/api/admin/employees', json={'email': 'nope1@test.local', 'role': 'cashier'})
    assert r.status_code == 403 and r.get_json()['error'] == 'Admin only', r.get_json()

    r = plain_manager.put(f'/api/admin/employees/{target_id}/status', json={'status': 'disabled'})
    assert r.status_code == 403 and r.get_json()['error'] == 'Admin only', r.get_json()

    r = plain_manager.put(f'/api/admin/employees/{target_id}/pin', json={'pin': '1234'})
    assert r.status_code == 403 and r.get_json()['error'] == 'Admin only', r.get_json()

    r = plain_manager.get('/api/admin/employees')
    assert r.status_code == 403 and r.get_json()['error'] == 'Admin only', r.get_json()

    assert _user_by_email(db_path, 'nope1@test.local') is None
    assert _user(db_path, target_id)['status'] == 'pending_setup'


def test_default_off_a_scoped_manager_with_no_grant_is_refused_on_all_four_routes(app, admin, db_path):
    """The REALISTIC default-off install, not just the trivial one above:
    wave C2 already ships branch scoping on its own (a manager can be
    assigned to a branch for reporting purposes, D5, with no delegation
    implied). A manager who HAS a branch_scope_uid but has never been
    GRANTED retail.employees must behave exactly as before this wave on
    every one of the four routes -- this is the actual 'no manager holds
    retail.employees' contract the design's invisible-unless-opted-in
    promise makes, one property short of test_default_off_a_plain_
    manager_with_no_grant_is_refused_on_all_four_routes above (which never
    even sets a scope)."""
    manager_id, manager = _make_branch_manager(app, admin, db_path, scope='branch-default-off', grant=False)
    target_id, _ = _make_employee(admin, 'cashier')
    admin.put(f'/api/admin/employees/{target_id}/branch-scope', json={'branch_scope_uid': 'branch-default-off'})

    r = manager.post('/api/admin/employees', json={'email': 'nope2@test.local', 'role': 'cashier'})
    assert r.status_code == 403 and r.get_json()['error'] == 'Admin only', r.get_json()

    r = manager.put(f'/api/admin/employees/{target_id}/status', json={'status': 'disabled'})
    assert r.status_code == 403 and r.get_json()['error'] == 'Admin only', r.get_json()

    r = manager.put(f'/api/admin/employees/{target_id}/pin', json={'pin': '1234'})
    assert r.status_code == 403 and r.get_json()['error'] == 'Admin only', r.get_json()

    r = manager.get('/api/admin/employees')
    assert r.status_code == 403 and r.get_json()['error'] == 'Admin only', r.get_json()

    assert _user_by_email(db_path, 'nope2@test.local') is None
    assert _user(db_path, target_id)['status'] == 'pending_setup'


def test_m4_scoped_manager_with_grant_but_no_scope_is_refused(app, admin, db_path):
    """Half of the redundant pair (design §4.2 D1): a manager holding
    retail.employees but with NO branch_scope_uid is refused -- an
    unscoped grant is inert."""
    manager_id, _ = _make_employee(admin, 'manager')
    admin.post(f'/api/admin/employees/{manager_id}/permissions',
              json={'subsystem': user_accounts.CAP_EMPLOYEES, 'access_level': 'full'})
    manager = _login_as(app, db_path, manager_id)

    r = manager.post('/api/admin/employees', json={'email': 'unscoped-grant@test.local', 'role': 'cashier'})
    assert r.status_code == 403, r.get_json()
    assert _user_by_email(db_path, 'unscoped-grant@test.local') is None


def test_m4_scoped_manager_without_the_capability_grant_is_refused(app, admin, db_path):
    """The other half of the redundant pair: a manager with a
    branch_scope_uid but NO retail.employees row is refused -- scope
    alone confers nothing. This is what keeps a stray hand-granted scope
    on a manager inert (design §4.2 D1's own reasoning, restated for the
    scope side rather than the role side)."""
    manager_id, manager = _make_branch_manager(app, admin, db_path, grant=False)
    r = manager.post('/api/admin/employees', json={'email': 'scoped-no-grant@test.local', 'role': 'cashier'})
    assert r.status_code == 403, r.get_json()
    assert _user_by_email(db_path, 'scoped-no-grant@test.local') is None


def test_a_cashier_holding_a_stray_employees_grant_cannot_delegate(app, admin, db_path):
    """The role half of the redundant pair, from the opposite direction: a
    CASHIER somehow holding retail.employees (a stray hand grant, or a
    demotion that has not re-run the capability reset) must still be
    refused -- G1's role check keeps it inert."""
    cashier_id, _ = _make_employee(admin, 'cashier')
    admin.put(f'/api/admin/employees/{cashier_id}/branch-scope', json={'branch_scope_uid': 'branch-x'})
    admin.post(f'/api/admin/employees/{cashier_id}/permissions',
              json={'subsystem': user_accounts.CAP_EMPLOYEES, 'access_level': 'full'})
    cashier = _login_as(app, db_path, cashier_id)

    r = cashier.post('/api/admin/employees', json={'email': 'via-cashier@test.local', 'role': 'cashier'})
    assert r.status_code == 403, r.get_json()
    assert _user_by_email(db_path, 'via-cashier@test.local') is None


# ═════════════════════════════════════════════════════════════════════════
# D2 -- update_status: G4 target rule, both directions
# ═════════════════════════════════════════════════════════════════════════

def test_d2_delegated_disable_of_an_own_branch_cashier_succeeds(app, admin, db_path):
    manager_id, manager = _make_branch_manager(app, admin, db_path, scope='branch-d2')
    cashier_id, _ = _make_employee(admin, 'cashier')
    admin.put(f'/api/admin/employees/{cashier_id}/branch-scope', json={'branch_scope_uid': 'branch-d2'})

    r = manager.put(f'/api/admin/employees/{cashier_id}/status', json={'status': 'disabled'})
    assert r.status_code == 200, r.get_json()
    assert _user(db_path, cashier_id)['status'] == 'disabled'


def test_d2_delegated_enable_is_refused(app, admin, db_path):
    """Design §3.4: re-enable is owner-only, ASYMMETRICALLY to disable.
    The BM's own branch, the BM's own disable, still refused to reverse."""
    manager_id, manager = _make_branch_manager(app, admin, db_path, scope='branch-d2b')
    cashier_id, _ = _make_employee(admin, 'cashier')
    admin.put(f'/api/admin/employees/{cashier_id}/branch-scope', json={'branch_scope_uid': 'branch-d2b'})
    assert admin.put(f'/api/admin/employees/{cashier_id}/status', json={'status': 'disabled'}).status_code == 200

    r = manager.put(f'/api/admin/employees/{cashier_id}/status', json={'status': 'active'})
    assert r.status_code == 403, r.get_json()
    assert _user(db_path, cashier_id)['status'] == 'disabled', "a refused enable must change nothing"


def test_d2_admin_can_still_reenable_after_a_delegated_disable(app, admin, db_path):
    """The allow-half for the admin arm of the SAME asymmetry -- the owner
    is never blocked by D2's enable refusal."""
    manager_id, manager = _make_branch_manager(app, admin, db_path, scope='branch-d2c')
    cashier_id, _ = _make_employee(admin, 'cashier')
    admin.put(f'/api/admin/employees/{cashier_id}/branch-scope', json={'branch_scope_uid': 'branch-d2c'})
    assert manager.put(f'/api/admin/employees/{cashier_id}/status', json={'status': 'disabled'}).status_code == 200

    r = admin.put(f'/api/admin/employees/{cashier_id}/status', json={'status': 'active'})
    assert r.status_code == 200, r.get_json()
    assert _user(db_path, cashier_id)['status'] == 'active'


def test_d2_g4_refuses_a_cross_branch_cashier(app, admin, db_path):
    manager_id, manager = _make_branch_manager(app, admin, db_path, scope='branch-mine')
    other_cashier_id, _ = _make_employee(admin, 'cashier')
    admin.put(f'/api/admin/employees/{other_cashier_id}/branch-scope', json={'branch_scope_uid': 'branch-theirs'})

    r = manager.put(f'/api/admin/employees/{other_cashier_id}/status', json={'status': 'disabled'})
    assert r.status_code == 403, r.get_json()
    assert _user(db_path, other_cashier_id)['status'] == 'pending_setup'


def test_d2_g4_refuses_a_null_scope_head_office_cashier(app, admin, db_path):
    """NULL matches nothing -- a head-office (unscoped) cashier is outside
    every branch manager's reach by construction."""
    manager_id, manager = _make_branch_manager(app, admin, db_path, scope='branch-mine2')
    head_office_cashier_id, _ = _make_employee(admin, 'cashier')  # unscoped, the default

    r = manager.put(f'/api/admin/employees/{head_office_cashier_id}/status', json={'status': 'disabled'})
    assert r.status_code == 403, r.get_json()


def test_d2_g4_refuses_a_same_branch_manager_target(app, admin, db_path):
    """Own-branch CASHIERS only -- a fellow manager (even at the same
    branch) is not a valid target."""
    manager_id, manager = _make_branch_manager(app, admin, db_path, scope='branch-mine3')
    peer_manager_id, _ = _make_employee(admin, 'manager')
    admin.put(f'/api/admin/employees/{peer_manager_id}/branch-scope', json={'branch_scope_uid': 'branch-mine3'})

    r = manager.put(f'/api/admin/employees/{peer_manager_id}/status', json={'status': 'disabled'})
    assert r.status_code == 403, r.get_json()


def test_d2_g4_refuses_self_targeting(app, admin, db_path):
    manager_id, manager = _make_branch_manager(app, admin, db_path, scope='branch-self')
    r = manager.put(f'/api/admin/employees/{manager_id}/status', json={'status': 'disabled'})
    assert r.status_code == 403, r.get_json()
    assert _user(db_path, manager_id)['status'] != 'disabled'


def test_d2_delegated_status_change_404s_before_privilege_is_known(app, admin, db_path):
    """Ordering proof: the branch-manager check runs before the target
    row is looked up, so a non-privileged caller cannot use 404-vs-403 to
    learn whether an id exists."""
    plain_manager_id, _ = _make_employee(admin, 'manager')
    plain_manager = _login_as(app, db_path, plain_manager_id)
    ghost = str(uuid.uuid4())
    r = plain_manager.put(f'/api/admin/employees/{ghost}/status', json={'status': 'disabled'})
    assert r.status_code == 403, r.get_json()


# ═════════════════════════════════════════════════════════════════════════
# D3 -- update_pin: same non-admin gate as D2, both directions (set/clear)
# ═════════════════════════════════════════════════════════════════════════

def test_d3_delegated_set_pin_for_an_own_branch_cashier_succeeds(app, admin, db_path):
    manager_id, manager = _make_branch_manager(app, admin, db_path, scope='branch-d3')
    cashier_id, _ = _make_employee(admin, 'cashier')
    admin.put(f'/api/admin/employees/{cashier_id}/branch-scope', json={'branch_scope_uid': 'branch-d3'})

    r = manager.put(f'/api/admin/employees/{cashier_id}/pin', json={'pin': '4321'})
    assert r.status_code == 200, r.get_json()
    assert _user(db_path, cashier_id)['pin_hash']


def test_d3_delegated_clear_pin_for_an_own_branch_cashier_succeeds(app, admin, db_path):
    manager_id, manager = _make_branch_manager(app, admin, db_path, scope='branch-d3b')
    cashier_id, _ = _make_employee(admin, 'cashier')
    admin.put(f'/api/admin/employees/{cashier_id}/branch-scope', json={'branch_scope_uid': 'branch-d3b'})
    admin.put(f'/api/admin/employees/{cashier_id}/pin', json={'pin': '1111'})

    r = manager.delete(f'/api/admin/employees/{cashier_id}/pin')
    assert r.status_code == 200, r.get_json()
    assert _user(db_path, cashier_id)['pin_hash'] is None


def test_d3_g4_refuses_a_cross_branch_cashiers_pin(app, admin, db_path):
    manager_id, manager = _make_branch_manager(app, admin, db_path, scope='branch-d3c')
    other_cashier_id, _ = _make_employee(admin, 'cashier')
    admin.put(f'/api/admin/employees/{other_cashier_id}/branch-scope', json={'branch_scope_uid': 'branch-elsewhere'})

    r = manager.put(f'/api/admin/employees/{other_cashier_id}/pin', json={'pin': '9999'})
    assert r.status_code == 403, r.get_json()
    assert _user(db_path, other_cashier_id)['pin_hash'] is None


def test_d3_g4_refuses_self_targeting(app, admin, db_path):
    manager_id, manager = _make_branch_manager(app, admin, db_path, scope='branch-d3self')
    r = manager.put(f'/api/admin/employees/{manager_id}/pin', json={'pin': '5555'})
    assert r.status_code == 403, r.get_json()


# ═════════════════════════════════════════════════════════════════════════
# D4 -- get_employees: the roster filter
# ═════════════════════════════════════════════════════════════════════════

def test_d4_delegated_roster_shows_only_own_branch_non_admin_rows(app, admin, db_path):
    manager_id, manager = _make_branch_manager(app, admin, db_path, scope='branch-d4')
    own_cashier_id, _ = _make_employee(admin, 'cashier')
    admin.put(f'/api/admin/employees/{own_cashier_id}/branch-scope', json={'branch_scope_uid': 'branch-d4'})
    other_cashier_id, _ = _make_employee(admin, 'cashier')
    admin.put(f'/api/admin/employees/{other_cashier_id}/branch-scope', json={'branch_scope_uid': 'branch-other'})
    unscoped_cashier_id, _ = _make_employee(admin, 'cashier')  # head office

    body = manager.get('/api/admin/employees')
    assert body.status_code == 200, body.get_json()
    ids = {e['id'] for e in body.get_json()['employees']}

    assert own_cashier_id in ids
    assert manager_id in ids, "the BM's own row (own branch, own scope) should still be visible to them"
    assert other_cashier_id not in ids, "another branch's cashier leaked into the roster"
    assert unscoped_cashier_id not in ids, "a NULL-scope (head office) row leaked into the roster"
    owner_id = next(e['id'] for e in admin.get('/api/admin/employees').get_json()['employees']
                    if e['effective_role'] == 'admin')
    assert owner_id not in ids, "the owner's own row leaked into a branch manager's roster"


def test_d4_admin_roster_is_unaffected(app, admin, db_path):
    manager_id, manager = _make_branch_manager(app, admin, db_path, scope='branch-d4b')
    other_id, _ = _make_employee(admin, 'cashier')

    body = admin.get('/api/admin/employees').get_json()
    ids = {e['id'] for e in body['employees']}
    assert manager_id in ids and other_id in ids


# ═════════════════════════════════════════════════════════════════════════
# Fresh-authority read (G6) -- a revoked/descoped manager cannot keep
# minting on a session that has not yet re-read its own row
# ═════════════════════════════════════════════════════════════════════════

def test_g6_a_revoked_grant_is_read_fresh_even_when_session_version_is_untouched(app, admin, db_path):
    """G6: the gate reads role/scope/capability from registry.db INSIDE
    this request, never memoized. The real `update_perms` route ALSO bumps
    `session_version` (belt-and-braces, per design), which on its own
    would let `mt_login_required`'s staleness check explain a subsequent
    refusal -- so this mutates `user_permissions` directly, bypassing that
    bump entirely, to prove the refusal comes from the GATE re-reading the
    fact fresh, not from the session having gone stale."""
    manager_id, manager = _make_branch_manager(app, admin, db_path, scope='branch-g6')
    assert manager.post('/api/admin/employees',
                        json={'email': 'before-revoke@test.local', 'role': 'cashier'}).status_code == 200

    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM user_permissions WHERE user_id=? AND subsystem=?",
                 (manager_id, user_accounts.CAP_EMPLOYEES))
    conn.commit()
    conn.close()

    r = manager.post('/api/admin/employees', json={'email': 'after-revoke@test.local', 'role': 'cashier'})
    assert r.status_code == 403, r.get_json()
    assert _user_by_email(db_path, 'after-revoke@test.local') is None
