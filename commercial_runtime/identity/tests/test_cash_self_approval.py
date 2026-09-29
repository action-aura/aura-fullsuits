"""AUDIT-032: the same non-owner person may not hold BOTH `retail.cash.close`
(close the drawer) and `retail.cash.approve` (approve the variance). The owner
is exempt. Every route that can hand out a capability is covered, and every
test has an ALLOW half next to its DENY half -- a refusal that refuses
everything would pass a deny-only suite (see ENGINEERING.md, "prove both
directions").

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/identity/tests/test_cash_self_approval.py -v
"""
import sqlite3
import uuid

import pytest
from flask import Flask

from commercial_runtime.identity import mt_auth, onboarding_routes, registry_db
from commercial_runtime.identity import user_accounts as ua
from commercial_runtime.identity.onboarding_routes import onboarding_bp
from commercial_runtime.licensing_contracts import flask_guard
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRecord

CLOSE = 'retail.cash.close'
APPROVE = 'retail.cash.approve'


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
    monkeypatch.setattr(flask_guard.LicenseStateRepository, "__init__", lambda self, db_path: None)
    monkeypatch.setattr(
        flask_guard.LicenseStateRepository, "load",
        lambda self: LicenseStateRecord(
            licensing_schema_version=1, product_code="AURA_TEST", platform="WINDOWS",
            current_state="ACTIVE_ONLINE"))
    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"
    flask_app.testing = True
    flask_app.register_blueprint(onboarding_bp)
    return flask_app


@pytest.fixture
def admin(app):
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner@test.local', 'password': 'OwnerPW11'})
    assert r.status_code == 200, r.get_json()
    return client


def _rows(db_path, sql, args=()):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _create(admin, **body):
    body.setdefault('email', f'emp-{uuid.uuid4().hex[:8]}@test.local')
    return admin.post('/api/admin/employees', json=body), body['email']


def _make(admin, **body):
    r, email = _create(admin, **body)
    assert r.status_code == 200, r.get_json()
    return next(e['id'] for e in admin.get('/api/admin/employees').get_json()['employees']
                if e['email'] == email)


def _caps(db_path, uid):
    return {r['subsystem']: r['access_level'] for r in _rows(
        db_path, "SELECT subsystem, access_level FROM user_permissions WHERE user_id=?", (uid,))}


def _grant(admin, uid, code, level='full'):
    return admin.post(f'/api/admin/employees/{uid}/permissions',
                      json={'subsystem': code, 'access_level': level})


def _direct_set(db_path, uid, code, level):
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM user_permissions WHERE user_id=? AND subsystem=?", (uid, code))
    conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
                 (str(uuid.uuid4()), uid, code, level))
    conn.commit()
    conn.close()


# ── the pure rule ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role,granted,expected", [
    ('admin',    {CLOSE, APPROVE}, False),      # the owner is exempt
    ('manager',  {CLOSE, APPROVE}, True),
    ('cashier',  {CLOSE, APPROVE}, True),
    ('employee', {CLOSE, APPROVE}, True),       # legacy alias of cashier
    ('manager',  {CLOSE}, False),
    ('manager',  {APPROVE}, False),
    ('manager',  set(), False),
    ('manager',  {APPROVE, 'retail.books.view', 'retail.sell'}, False),
])
def test_conflict_table(role, granted, expected):
    assert ua.cash_self_approval_conflict(role, granted) is expected


def test_the_message_is_the_fixed_sentence():
    assert ua.CASH_SELF_APPROVAL_MESSAGE == (
        'Approving cash variances and closing the drawer cannot be given to the same person.')


# ── create ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ['manager', 'cashier'])
def test_create_refuses_both_codes_and_writes_nothing(admin, db_path, role):
    n = _rows(db_path, "SELECT COUNT(*) FROM users")[0][0]
    perms = {APPROVE: 'full', CLOSE: 'full'}
    r, _ = _create(admin, role=role, permissions=perms)
    assert r.status_code == 400
    assert r.get_json()['error'] == ua.CASH_SELF_APPROVAL_MESSAGE
    assert _rows(db_path, "SELECT COUNT(*) FROM users")[0][0] == n


def test_create_cashier_plus_approve_is_refused_because_cashier_holds_close(admin, db_path):
    r, _ = _create(admin, role='cashier', permissions={APPROVE: 'full'})
    assert r.status_code == 400
    assert r.get_json()['error'] == ua.CASH_SELF_APPROVAL_MESSAGE


def test_create_allow_half(admin, db_path):
    # manager + a books code: nothing to do with cash -> allowed.
    uid = _make(admin, role='manager', permissions={'retail.books.view': 'full'})
    assert _caps(db_path, uid)['retail.books.view'] == 'full'
    # approve WITHOUT close is legal: that is exactly the accountant preset.
    uid2 = _make(admin, preset='accountant')
    assert (_caps(db_path, uid2)[APPROVE], _caps(db_path, uid2)[CLOSE]) == ('full', 'none')
    # (A plain manager already holds cash.close, so manager + approve is the
    # DENY case above -- test_create_refuses_both_codes_and_writes_nothing.)
    # A plain cashier: close only.
    uid3 = _make(admin, role='cashier')
    assert _caps(db_path, uid3)[CLOSE] == 'full'
    assert _caps(db_path, uid3)[APPROVE] == 'none'


# ── update_perms ─────────────────────────────────────────────────────────────

def test_granting_approve_to_someone_who_can_close_is_refused_and_changes_nothing(admin, db_path):
    uid = _make(admin, role='manager')
    assert _caps(db_path, uid)[CLOSE] == 'full'                  # the manager default holds close
    before = _caps(db_path, uid)
    r = _grant(admin, uid, APPROVE)
    assert r.status_code == 400
    assert r.get_json()['error'] == ua.CASH_SELF_APPROVAL_MESSAGE
    assert _caps(db_path, uid) == before


def test_granting_close_to_someone_who_can_approve_is_refused_too(admin, db_path):
    """The rule is symmetric: the route must check whichever code arrives
    SECOND. A check written only for the approve grant leaves this open."""
    uid = _make(admin, preset='accountant')          # holds approve, not close
    before = _caps(db_path, uid)
    assert (before[APPROVE], before[CLOSE]) == ('full', 'none')
    r = _grant(admin, uid, CLOSE)
    assert r.status_code == 400
    assert _caps(db_path, uid) == before


def test_after_revoking_the_first_the_second_is_allowed(admin, db_path):
    uid = _make(admin, role='manager')
    assert _grant(admin, uid, CLOSE).status_code == 200
    assert _grant(admin, uid, APPROVE).status_code == 400
    assert _grant(admin, uid, CLOSE, 'none').status_code == 200   # revoke
    assert _grant(admin, uid, APPROVE).status_code == 200         # now fine
    caps = _caps(db_path, uid)
    assert (caps[CLOSE], caps[APPROVE]) == ('none', 'full')


def test_a_revoke_of_an_already_breached_account_is_always_allowed(admin, db_path):
    """An account that breached the rule (built by a direct write, e.g. a row
    synced from an older build) must be REPAIRABLE. A check that also refused
    the revoke would trap the owner."""
    uid = _make(admin, role='manager')
    _direct_set(db_path, uid, CLOSE, 'full')
    _direct_set(db_path, uid, APPROVE, 'full')
    assert _grant(admin, uid, APPROVE, 'none').status_code == 200
    assert _caps(db_path, uid)[APPROVE] == 'none'


def test_non_cash_grants_are_unaffected(admin, db_path):
    uid = _make(admin, role='manager')
    assert _grant(admin, uid, CLOSE).status_code == 200
    assert _grant(admin, uid, 'retail.books.view').status_code == 200
    assert _grant(admin, uid, 'retail.sell', 'none').status_code == 200


# ── presets and the backstop ─────────────────────────────────────────────────

def test_accountant_preset_is_allowed_then_close_is_refused(admin, db_path):
    uid = _make(admin, role='cashier')
    assert admin.put(f'/api/admin/employees/{uid}/preset', json={'preset': 'accountant'}).status_code == 200
    assert _caps(db_path, uid)[APPROVE] == 'full'
    r = _grant(admin, uid, CLOSE)
    assert r.status_code == 400
    assert _caps(db_path, uid)[CLOSE] == 'none'


def test_role_change_to_cashier_is_allowed_only_because_approve_is_dropped(admin, db_path):
    """cashier + approve would breach; update_role resets to the role defaults,
    so an accountant moved to cashier ends with close and WITHOUT approve."""
    uid = _make(admin, role='cashier')
    admin.put(f'/api/admin/employees/{uid}/preset', json={'preset': 'accountant'})
    r = admin.put(f'/api/admin/employees/{uid}/role', json={'role': 'cashier'})
    assert r.status_code == 200
    caps = _caps(db_path, uid)
    assert (caps[CLOSE], caps[APPROVE]) == ('full', 'none')


def test_backstop_a_future_breaching_preset_is_refused_and_writes_nothing(admin, db_path, monkeypatch):
    uid = _make(admin, role='cashier')
    before_user = _rows(db_path, "SELECT role, role_preset, session_version, row_version FROM users WHERE id=?", (uid,))[0]
    before_caps = _caps(db_path, uid)
    outbox = _rows(db_path, "SELECT COUNT(*) FROM sync_outbox")[0][0]

    bad = dict(ua.ROLE_PRESETS)
    bad['accountant'] = frozenset(ua.ROLE_PRESETS['accountant'] | {CLOSE})
    monkeypatch.setattr(ua, 'ROLE_PRESETS', bad)

    r = admin.put(f'/api/admin/employees/{uid}/preset', json={'preset': 'accountant'})
    assert r.status_code == 400
    assert r.get_json()['error'] == ua.CASH_SELF_APPROVAL_MESSAGE
    assert tuple(_rows(db_path, "SELECT role, role_preset, session_version, row_version FROM users WHERE id=?", (uid,))[0]) \
        == tuple(before_user)
    assert _caps(db_path, uid) == before_caps
    assert _rows(db_path, "SELECT COUNT(*) FROM sync_outbox")[0][0] == outbox


def test_apply_preset_raises_the_typed_error_directly(db_path, monkeypatch):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cid = str(uuid.uuid4())
        conn.execute("INSERT INTO users (id, company_id, employee_id, email, password_hash, role, uid) "
                     "VALUES ('u1', ?, 'e1', 'x@y.z', 'h', 'cashier', ?)", (cid, str(uuid.uuid4())))
        bad = dict(ua.ROLE_PRESETS)
        bad['cashier'] = frozenset({CLOSE, APPROVE})
        monkeypatch.setattr(ua, 'ROLE_PRESETS', bad)
        with pytest.raises(ua.CashSelfApprovalError):
            ua.apply_preset(conn, 'u1', 'cashier')
    finally:
        conn.close()


def test_apply_preset_refuses_the_owner_with_the_typed_error(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("INSERT INTO users (id, company_id, employee_id, email, password_hash, role, uid) "
                     "VALUES ('own', 'c', 'e', 'o@y.z', 'h', 'admin', ?)", (str(uuid.uuid4()),))
        with pytest.raises(ua.PresetError):
            ua.apply_preset(conn, 'own', 'manager')
    finally:
        conn.close()
