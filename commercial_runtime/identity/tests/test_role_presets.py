"""Role presets, person names and the books defaults (Registry v9, Track S).

Same fixture shape as test_employee_admin_routes.py: the REAL v9 registry built
by `init_registry_db()` in tmp_path, the REAL create-admin route for the owner,
so the columns and seeds are what production writes.

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/identity/tests/test_role_presets.py -v
"""
import json
import sqlite3
import uuid

import pytest
from flask import Flask

from commercial_runtime.identity import mt_auth, onboarding_routes, registry_db
from commercial_runtime.identity import user_accounts as ua
from commercial_runtime.identity.onboarding_routes import onboarding_bp
from commercial_runtime.licensing_contracts import flask_guard
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRecord


# ── fixtures ─────────────────────────────────────────────────────────────────

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


def _create(admin, **body):
    body.setdefault('email', f'emp-{uuid.uuid4().hex[:8]}@test.local')
    return admin.post('/api/admin/employees', json=body), body['email']


def _id_for(admin, email):
    rows = admin.get('/api/admin/employees').get_json()['employees']
    return next(e['id'] for e in rows if e['email'] == email)


def _make(admin, **body):
    r, email = _create(admin, **body)
    assert r.status_code == 200, r.get_json()
    return _id_for(admin, email)


def _rows(db_path, sql, args=()):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _caps(db_path, user_id):
    return {r['subsystem']: r['access_level'] for r in _rows(
        db_path, "SELECT subsystem, access_level FROM user_permissions WHERE user_id=?", (user_id,))}


def _held(db_path, user_id):
    return {c for c, lvl in _caps(db_path, user_id).items()
            if c in ua.CAPABILITY_CODES and lvl == 'full'}


# ── the preset table itself ──────────────────────────────────────────────────

def test_the_accountant_preset_is_exactly_the_four_named_codes():
    assert ua.ROLE_PRESETS[ua.PRESET_ACCOUNTANT] == {
        'retail.books.view', 'retail.books.record', 'retail.reports', 'retail.cash.approve'}


def test_only_the_accountant_and_the_owner_hold_any_books_code():
    """Books access is opt-in. Mutation (ROLE_MANAGER without the books
    subtraction) makes the manager assertion go RED."""
    books = set(ua.BOOKS_CAPABILITY_CODES)
    assert len(books) == 4
    assert not books & ua.ROLE_CAPABILITIES[ua.ROLE_MANAGER]
    assert not books & ua.ROLE_CAPABILITIES[ua.ROLE_CASHIER]
    assert not books & ua.ROLE_PRESETS[ua.PRESET_MANAGER]
    assert not books & ua.ROLE_PRESETS[ua.PRESET_BRANCH_MANAGER]
    assert books <= ua.ROLE_CAPABILITIES[ua.ROLE_ADMIN]


def test_branch_manager_is_manager_plus_employees_only():
    assert ua.ROLE_PRESETS[ua.PRESET_BRANCH_MANAGER] == (
        ua.ROLE_PRESETS[ua.PRESET_MANAGER] | {ua.CAP_EMPLOYEES})


@pytest.mark.parametrize("preset", sorted(ua.ROLE_PRESETS))
def test_no_shipped_preset_breaches_the_cash_rule(preset):
    assert not ua.cash_self_approval_conflict(ua.PRESET_BASE_ROLE[preset], ua.ROLE_PRESETS[preset])


@pytest.mark.parametrize("preset", sorted(ua.ROLE_PRESETS))
def test_every_preset_has_a_base_role_and_only_known_codes(preset):
    assert ua.PRESET_BASE_ROLE[preset] in (ua.ROLE_MANAGER, ua.ROLE_CASHIER)
    assert ua.ROLE_PRESETS[preset] <= set(ua.CAPABILITY_CODES)


# ── display name rule ────────────────────────────────────────────────────────

def test_display_name_is_nfc_trimmed_and_blank_means_none():
    decomposed = "José"          # e + combining acute
    assert ua.normalize_display_name("  " + decomposed + " ") == "José"
    assert ua.normalize_display_name("   ") is None
    assert ua.normalize_display_name(None) is None


def test_display_name_limit_is_sixty_after_normalisation_both_sides():
    assert ua.normalize_display_name("a" * 60) == "a" * 60
    with pytest.raises(ValueError, match="60 characters or fewer"):
        ua.normalize_display_name("a" * 61)
    # 60 visible characters that are 61 code points before NFC: judged by what
    # a reader sees, so it is ACCEPTED.
    assert len(ua.normalize_display_name("é" * 60)) == 60


# ── apply_preset through the real route ──────────────────────────────────────

def test_applying_the_accountant_preset_moves_everything_together(admin, db_path):
    uid = _make(admin, role='cashier')
    before = _rows(db_path, "SELECT session_version, row_version FROM users WHERE id=?", (uid,))[0]
    # A legacy coarse-gate row that mt_require_subsystem('retail') reads.
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT OR REPLACE INTO user_permissions (id, user_id, subsystem, access_level) "
                 "VALUES (?, ?, 'retail', 'full')", (str(uuid.uuid4()), uid))
    conn.commit()
    conn.close()
    outbox_before = _rows(db_path, "SELECT COUNT(*) AS n FROM sync_outbox")[0]['n']

    r = admin.put(f'/api/admin/employees/{uid}/preset', json={'preset': 'accountant'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {'success': True, 'role': 'manager', 'role_preset': 'accountant'}

    user = _rows(db_path, "SELECT role, role_preset, session_version, row_version FROM users WHERE id=?", (uid,))[0]
    assert (user['role'], user['role_preset']) == ('manager', 'accountant')
    assert user['session_version'] == before['session_version'] + 1      # revocation channel
    assert user['row_version'] == before['row_version'] + 1
    assert _held(db_path, uid) == set(ua.ROLE_PRESETS['accountant'])
    # All twelve rows exist (explicit 'none', never a missing row) ...
    assert set(c for c in _caps(db_path, uid) if c in ua.CAPABILITY_CODES) == set(ua.CAPABILITY_CODES)
    # ... and the legacy row was NOT swept away by the bounded reset.
    assert _caps(db_path, uid)['retail'] == 'full'

    # Sync: the user event comes FIRST, then a delete per code, then the reseed.
    # (sync_outbox.id is a random uuid, so insertion order is the rowid.)
    events = _rows(db_path, "SELECT entity_type, event_type FROM sync_outbox ORDER BY rowid")[outbox_before:]
    assert events[0]['entity_type'] == 'user' and events[0]['event_type'] == 'update'
    perm = [e['event_type'] for e in events[1:] if e['entity_type'] == 'user_permission']
    assert perm.count('delete') == 12
    assert perm.count('create') == 12


def test_a_role_change_to_the_same_base_role_keeps_the_preset_and_its_books(admin, db_path):
    """REWRITTEN ON PURPOSE 2026-09-29 (verifier item 14). The old test
    (`..._replaces_the_previous_preset_label`) pinned that PUT /role manager on
    an Accountant relabelled them plain 'manager' and took their books away.
    Accountant IS a manager (PRESET_BASE_ROLE), so a "change" to manager is not
    a demotion -- silently stripping the twelve rows was the defect, and the
    old assertion is exactly what enforced it. What the old test can no longer
    catch: a role change that lands on the SAME base role resetting the
    preset. What replaces it: that path must keep the preset and every row,
    and a change to a DIFFERENT base role must still drop the books."""
    uid = _make(admin, role='cashier')
    assert admin.put(f'/api/admin/employees/{uid}/preset', json={'preset': 'accountant'}).status_code == 200
    held_before = _held(db_path, uid)
    r = admin.put(f'/api/admin/employees/{uid}/role', json={'role': 'manager'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['role_preset'] == 'accountant'
    assert _rows(db_path, "SELECT role_preset FROM users WHERE id=?", (uid,))[0][0] == 'accountant'
    assert _held(db_path, uid) == held_before == set(ua.ROLE_PRESETS['accountant'])
    assert set(c for c in _caps(db_path, uid) if c in ua.CAPABILITY_CODES) == set(ua.CAPABILITY_CODES)


def test_a_role_change_to_another_base_role_replaces_the_preset_label(admin, db_path):
    uid = _make(admin, role='cashier')
    assert admin.put(f'/api/admin/employees/{uid}/preset', json={'preset': 'accountant'}).status_code == 200
    assert admin.put(f'/api/admin/employees/{uid}/role', json={'role': 'cashier'}).status_code == 200
    assert _rows(db_path, "SELECT role_preset FROM users WHERE id=?", (uid,))[0][0] == 'cashier'
    # Deny-half: the ex-accountant does not keep the books.
    assert not _held(db_path, uid) & set(ua.BOOKS_CAPABILITY_CODES)


def test_preset_refusals_and_what_they_leave_behind(admin, db_path, app):
    uid = _make(admin, role='cashier')
    snap = _caps(db_path, uid)
    r = admin.put(f'/api/admin/employees/{uid}/preset', json={'preset': 'wizard'})
    assert (r.status_code, r.get_json()['error']) == (400, 'Unknown preset.')
    assert admin.put(f'/api/admin/employees/{uuid.uuid4()}/preset', json={'preset': 'manager'}).status_code == 404
    assert _caps(db_path, uid) == snap
    # The owner cannot be given a preset.
    owner_id = _rows(db_path, "SELECT id FROM users WHERE role='admin'")[0][0]
    r = admin.put(f'/api/admin/employees/{owner_id}/preset', json={'preset': 'cashier'})
    assert r.status_code == 409
    assert _rows(db_path, "SELECT role FROM users WHERE id=?", (owner_id,))[0][0] == 'admin'


def test_a_non_admin_cannot_apply_a_preset(app, admin, db_path):
    uid = _make(admin, role='manager')
    other = app.test_client()
    with other.session_transaction() as s:
        s.update(mt_role='manager', company_id='x', mt_user_id=uid)
    r = other.put(f'/api/admin/employees/{uid}/preset', json={'preset': 'accountant'})
    assert r.status_code in (401, 403)
    assert _rows(db_path, "SELECT role_preset FROM users WHERE id=?", (uid,))[0][0] != 'accountant'


# ── preset_matches (the "Custom" badge) ──────────────────────────────────────

def test_preset_matches_true_then_false_after_one_switch_is_tuned(admin, db_path):
    uid = _make(admin, role='cashier')
    admin.put(f'/api/admin/employees/{uid}/preset', json={'preset': 'accountant'})
    conn = sqlite3.connect(str(db_path))
    try:
        assert ua.preset_matches(conn, uid) is True
        assert admin.post(f'/api/admin/employees/{uid}/permissions',
                          json={'subsystem': 'retail.books.record', 'access_level': 'none'}).status_code == 200
        assert ua.preset_matches(conn, uid) is False
        # No stored preset -> nothing to contradict.
        conn.execute("UPDATE users SET role_preset=NULL WHERE id=?", (uid,))
        assert ua.preset_matches(conn, uid) is True
        # A name from a newer build: we do not claim a match.
        conn.execute("UPDATE users SET role_preset='auditor' WHERE id=?", (uid,))
        assert ua.preset_matches(conn, uid) is False
    finally:
        conn.close()


# ── create with name / preset ────────────────────────────────────────────────

def test_create_with_preset_and_name_stores_both(admin, db_path):
    uid = _make(admin, preset='accountant', name='  Layla  ')
    row = _rows(db_path, "SELECT role, role_preset, display_name FROM users WHERE id=?", (uid,))[0]
    assert (row['role'], row['role_preset'], row['display_name']) == ('manager', 'accountant', 'Layla')
    assert _held(db_path, uid) == set(ua.ROLE_PRESETS['accountant'])


def test_create_refusals(admin, db_path):
    r, _ = _create(admin, preset='wizard')
    assert (r.status_code, r.get_json()['error']) == (400, 'Unknown preset.')
    r, _ = _create(admin, preset='manager', permissions={'retail.sell': 'full'})
    assert (r.status_code, r.get_json()['error']) == (400, 'Choose a preset or set permissions, not both.')
    r, _ = _create(admin, preset='accountant', role='cashier')
    assert (r.status_code, r.get_json()['error']) == (400, 'Role does not match the chosen preset.')
    r, _ = _create(admin, role='cashier', name='n' * 61)
    assert (r.status_code, r.get_json()['error']) == (400, 'Name must be 60 characters or fewer.')
    # None of the refusals left an account behind.
    assert _rows(db_path, "SELECT COUNT(*) FROM users")[0][0] == 1


def test_create_without_name_or_preset_is_the_old_behaviour(admin, db_path):
    uid = _make(admin, role='cashier')
    row = _rows(db_path, "SELECT display_name, role_preset FROM users WHERE id=?", (uid,))[0]
    assert (row['display_name'], row['role_preset']) == (None, None)


# ── rename ───────────────────────────────────────────────────────────────────

def test_rename_sets_clears_and_never_logs_the_person_out(admin, db_path):
    uid = _make(admin, role='cashier')
    before = _rows(db_path, "SELECT session_version, row_version FROM users WHERE id=?", (uid,))[0]
    n0 = _rows(db_path, "SELECT COUNT(*) FROM sync_outbox")[0][0]
    r = admin.put(f'/api/admin/employees/{uid}/name', json={'name': 'Omar'})
    assert r.status_code == 200 and r.get_json()['display_name'] == 'Omar'
    after = _rows(db_path, "SELECT display_name, session_version, row_version FROM users WHERE id=?", (uid,))[0]
    assert after['display_name'] == 'Omar'
    assert after['session_version'] == before['session_version']      # rename is not an access change
    assert after['row_version'] == before['row_version'] + 1
    assert _rows(db_path, "SELECT COUNT(*) FROM sync_outbox")[0][0] == n0 + 1
    # Blank clears; too long is refused and changes nothing.
    assert admin.put(f'/api/admin/employees/{uid}/name', json={'name': '  '}).get_json()['display_name'] is None
    assert admin.put(f'/api/admin/employees/{uid}/name', json={'name': 'Omar'}).status_code == 200
    r = admin.put(f'/api/admin/employees/{uid}/name', json={'name': 'x' * 61})
    assert (r.status_code, r.get_json()['error']) == (400, 'Name must be 60 characters or fewer.')
    assert _rows(db_path, "SELECT display_name FROM users WHERE id=?", (uid,))[0][0] == 'Omar'
    assert admin.put(f'/api/admin/employees/{uuid.uuid4()}/name', json={'name': 'A'}).status_code == 404


# ── the pure user-sync rules Y consumes ──────────────────────────────────────

def test_user_sync_absent_key_means_keep_and_present_none_means_clear():
    plan = ua.user_sync_apply_plan({'role': 'manager'}, 'manager')
    assert (plan['has_display_name'], plan['has_role_preset']) == (False, False)
    plan = ua.user_sync_apply_plan({'role': 'manager', 'display_name': None, 'role_preset': None}, 'manager')
    assert (plan['has_display_name'], plan['has_role_preset']) == (True, True)
    assert plan['reset_books'] is False


def test_user_sync_old_sender_role_change_resets_books_fail_closed():
    plan = ua.user_sync_apply_plan({'role': 'cashier'}, 'manager')       # v8 sender, role changed
    assert plan['reset_books'] is True
    assert plan['books_plan'] == {c: 'none' for c in ua.BOOKS_CAPABILITY_CODES}


def test_user_sync_old_sender_no_role_change_or_new_user_does_not_reset():
    assert ua.user_sync_apply_plan({'role': 'manager'}, 'manager')['reset_books'] is False
    assert ua.user_sync_apply_plan({'role': 'manager'}, None)['reset_books'] is False
    # A NEW sender (role_preset key present) carries the books rows itself.
    assert ua.user_sync_apply_plan({'role': 'cashier', 'role_preset': 'cashier'}, 'manager')['reset_books'] is False


def test_books_reset_plan_admin_full_everyone_else_none():
    assert set(ua.books_codes_reset_plan('admin').values()) == {'full'}
    assert set(ua.books_codes_reset_plan('manager').values()) == {'none'}
    assert set(ua.books_codes_reset_plan('employee').values()) == {'none'}   # legacy alias = cashier
