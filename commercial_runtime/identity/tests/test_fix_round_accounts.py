"""Fix round (2026-09-29 security review), ACCOUNT side: cash-pair guard on
receipt, boot sweep, sync-apply plans, name text rule, branch-manager scope,
tenant scope of the preset route.

Same fixtures as test_role_presets.py (the REAL v9 registry from
`init_registry_db()`, the REAL create-admin route). Each test below is written
so it FAILS when the fix it guards is removed; the mutation runs are in the
crew hand-back.

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/identity/tests/test_fix_round_accounts.py -v
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
from commercial_runtime.sync.sync_service import REGISTRY_SYNC_ENTITY_TYPES, SyncService

CLOSE = ua.CAP_CASH_CLOSE
APPROVE = ua.CAP_CASH_APPROVE


# ── fixtures (same shape as test_role_presets.py) ────────────────────────────

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


@pytest.fixture
def conn(db_path):
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    yield c
    c.close()


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
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def _caps(db_path, user_id):
    return {r['subsystem']: r['access_level'] for r in _rows(
        db_path, "SELECT subsystem, access_level FROM user_permissions WHERE user_id=?", (user_id,))}


def _uid(db_path, user_id):
    return _rows(db_path, "SELECT uid FROM users WHERE id=?", (user_id,))[0]['uid']


def _company(db_path):
    return _rows(db_path, "SELECT company_id FROM users WHERE email='owner@test.local'")[0]['company_id']


def _force_grant(db_path, user_id, code, level='full'):
    """Write a permission row straight to the table, the way a receiving
    device's apply (or a pre-guard build, or a hand edit) can: no route, so no
    AUDIT-032 check on the way in."""
    c = sqlite3.connect(str(db_path))
    c.execute("DELETE FROM user_permissions WHERE user_id=? AND subsystem=?", (user_id, code))
    c.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
              (str(uuid.uuid4()), user_id, code, level))
    c.commit()
    c.close()


def _breached_manager(admin, db_path):
    """A manager who (by direct write) holds BOTH cash.close and cash.approve."""
    uid = _make(admin, role='manager')
    _force_grant(db_path, uid, CLOSE)
    _force_grant(db_path, uid, APPROVE)
    return uid


def _audit(db_path, action):
    return _rows(db_path, "SELECT * FROM audit_logs WHERE action=? ORDER BY rowid", (action,))


def _outbox_count(db_path):
    return _rows(db_path, "SELECT COUNT(*) AS n FROM sync_outbox")[0]['n']


# ── H3: AUDIT-032 on the RECEIVING side ──────────────────────────────────────

def test_h3_a_non_admin_holding_both_cash_codes_loses_approve_on_receipt(admin, db_path, conn):
    uid = _breached_manager(admin, db_path)
    company = _company(db_path)
    outbox_before = _outbox_count(db_path)

    assert ua.enforce_cash_pair_after_apply(conn, company, _uid(db_path, uid)) is True
    conn.commit()

    caps = _caps(db_path, uid)
    assert caps[APPROVE] == 'none'                 # the half that can hide a shortfall goes
    assert caps[CLOSE] == 'full'                   # the other half is kept
    rows = _audit(db_path, 'CASH_PAIR_REVOKED_ON_RECEIPT')
    assert len(rows) == 1
    body = json.loads(rows[0]['new_value_json'])
    assert body['severity'] == 'WARNING' and body['source'] == 'receipt'
    assert body['revoked'] == APPROVE and body['kept'] == CLOSE
    # Deliberately silent on the wire: every device repairs by the same rule.
    assert _outbox_count(db_path) == outbox_before


def test_h3_allow_half_owner_single_code_and_unknown_user_are_left_alone(admin, db_path, conn):
    company = _company(db_path)
    owner_row = _rows(db_path, "SELECT id, uid FROM users WHERE email='owner@test.local'")[0]
    _force_grant(db_path, owner_row['id'], CLOSE)
    _force_grant(db_path, owner_row['id'], APPROVE)
    only_close = _make(admin, role='manager')
    _force_grant(db_path, only_close, CLOSE)
    _force_grant(db_path, only_close, APPROVE, 'none')

    assert ua.enforce_cash_pair_after_apply(conn, company, owner_row['uid']) is False   # admin exempt
    assert ua.enforce_cash_pair_after_apply(conn, company, _uid(db_path, only_close)) is False
    assert ua.enforce_cash_pair_after_apply(conn, company, 'no-such-uid') is False
    assert ua.enforce_cash_pair_after_apply(conn, company, '') is False
    assert ua.enforce_cash_pair_after_apply(conn, company, None) is False
    conn.commit()

    assert _caps(db_path, owner_row['id'])[APPROVE] == 'full'
    assert _caps(db_path, only_close)[CLOSE] == 'full'
    assert _audit(db_path, 'CASH_PAIR_REVOKED_ON_RECEIPT') == []


@pytest.mark.parametrize('bad_uid', ['no-such-uid', '', None])
def test_h3_an_unknown_or_empty_uid_never_reaches_a_legacy_row_without_a_uid(
        admin, db_path, conn, bad_uid):
    """Mutation-found gap (#7): the previous test only had accounts WITH a uid,
    so a lookup widened to `uid=? OR uid IS NULL` (an unknown/empty uid then
    matching every legacy row) still passed. Here a breached account has NO
    uid; a receipt naming a uid that is not local, or none at all, must not
    touch it -- that would revoke a stranger's authority on someone else's
    event. Only the boot sweep (by local id) may reach a uid-less row."""
    uid = _breached_manager(admin, db_path)
    c = sqlite3.connect(str(db_path))
    c.execute("UPDATE users SET uid=NULL WHERE id=?", (uid,))
    c.commit()
    c.close()

    assert ua.enforce_cash_pair_after_apply(conn, _company(db_path), bad_uid) is False
    conn.commit()

    assert _caps(db_path, uid)[APPROVE] == 'full'
    assert _audit(db_path, 'CASH_PAIR_REVOKED_ON_RECEIPT') == []


def test_d13_a_receipt_for_another_companys_uid_changes_nothing_and_is_scoped(admin, db_path, conn):
    """Mutant: the company_id filter dropped from the uid lookup, or from the
    UPDATE in _revoke_cash_approve_if_paired. uids are unique per install today,
    but the rule runs on RECEIVED events and must never revoke a user of
    another company whatever uid the event names. ALLOW half: the same uid
    under the OWNING company is repaired."""
    uid = _breached_manager(admin, db_path)
    user_uid = _uid(db_path, uid)
    company = _company(db_path)
    outbox_before = _outbox_count(db_path)
    assert ua.enforce_cash_pair_after_apply(conn, 'OTHER-CO', user_uid) is False
    conn.commit()
    assert _caps(db_path, uid)[APPROVE] == 'full'
    assert _audit(db_path, 'CASH_PAIR_REVOKED_ON_RECEIPT') == []
    assert ua.enforce_cash_pair_after_apply(conn, company, user_uid) is True
    conn.commit()
    assert _caps(db_path, uid)[APPROVE] == 'none'
    assert _outbox_count(db_path) == outbox_before


def test_d13_the_write_itself_is_scoped_to_the_company_even_when_the_lookup_is_bypassed(
        admin, db_path, conn):
    """Mutation-found gap: the test above only reaches the UPDATE through the
    company-scoped uid lookup, so removing the company filter from the UPDATE
    alone stayed green (the lookup already refused). The UPDATE is the second
    lock, and the boot sweep reaches `_revoke_cash_approve_if_paired` by LOCAL
    user id, with no uid lookup in front of it. Call it directly with a user
    who belongs to the real company but a company_id that is not theirs: the
    permission row must not move. ALLOW half: the true company narrows it."""
    uid = _breached_manager(admin, db_path)
    user_uid = _uid(db_path, uid)
    company = _company(db_path)
    role = _rows(db_path, "SELECT role FROM users WHERE id=?", (uid,))[0]['role']

    ua._revoke_cash_approve_if_paired(conn, 'OTHER-CO', uid, role, user_uid, 'test')
    conn.commit()
    assert _caps(db_path, uid)[APPROVE] == 'full', (
        'the cash.approve grant of a user in company A was narrowed by a call scoped to company B')

    ua._revoke_cash_approve_if_paired(conn, company, uid, role, user_uid, 'test')
    conn.commit()
    assert _caps(db_path, uid)[APPROVE] == 'none'


def test_h3_running_the_rule_twice_is_a_no_op_the_second_time(admin, db_path, conn):
    uid = _breached_manager(admin, db_path)
    company, user_uid = _company(db_path), _uid(db_path, uid)
    assert ua.enforce_cash_pair_after_apply(conn, company, user_uid) is True
    assert ua.enforce_cash_pair_after_apply(conn, company, user_uid) is False
    conn.commit()
    assert len(_audit(db_path, 'CASH_PAIR_REVOKED_ON_RECEIPT')) == 1


# ── Item 15: the boot sweep ──────────────────────────────────────────────────

def test_item15_boot_sweep_fixes_every_existing_breach_and_names_the_source(admin, db_path, conn):
    first = _breached_manager(admin, db_path)
    second = _breached_manager(admin, db_path)
    clean = _make(admin, role='manager')
    company = _company(db_path)

    fixed = ua.enforce_cash_pair_all(conn, company)
    conn.commit()

    assert sorted(fixed) == sorted([_uid(db_path, first), _uid(db_path, second)])
    assert _caps(db_path, first)[APPROVE] == 'none' and _caps(db_path, second)[APPROVE] == 'none'
    assert _caps(db_path, first)[CLOSE] == 'full'
    sources = {json.loads(r['new_value_json'])['source'] for r in _audit(db_path, 'CASH_PAIR_REVOKED_ON_RECEIPT')}
    assert sources == {'boot_sweep'}
    # allow half: the clean manager and the owner are untouched
    assert _caps(db_path, clean).get(APPROVE, 'none') == 'none'
    assert _rows(db_path, "SELECT COUNT(*) AS n FROM audit_logs WHERE action='CASH_PAIR_REVOKED_ON_RECEIPT'")[0]['n'] == 2
    # a second boot finds nothing
    assert ua.enforce_cash_pair_all(conn, company) == []


def test_item15_boot_sweep_reaches_an_account_with_no_uid(admin, db_path, conn):
    """A legacy row without a `uid` must still be swept (returned by local id)."""
    uid = _breached_manager(admin, db_path)
    c = sqlite3.connect(str(db_path))
    c.execute("UPDATE users SET uid=NULL WHERE id=?", (uid,))
    c.commit()
    c.close()
    fixed = ua.enforce_cash_pair_all(conn, _company(db_path))
    conn.commit()
    assert fixed == [uid]
    assert _caps(db_path, uid)[APPROVE] == 'none'


def test_item15_boot_sweep_never_reaches_into_another_company(admin, db_path, conn):
    uid = _breached_manager(admin, db_path)
    fixed = ua.enforce_cash_pair_all(conn, 'some-other-company')
    conn.commit()
    assert fixed == []
    assert _caps(db_path, uid)[APPROVE] == 'full'


# ── update_perms route ───────────────────────────────────────────────────────

def test_route_granting_the_second_half_of_the_pair_is_still_refused(admin, db_path):
    uid = _make(admin, role='manager')
    _force_grant(db_path, uid, CLOSE)
    _force_grant(db_path, uid, APPROVE, 'none')
    r = admin.post(f'/api/admin/employees/{uid}/permissions',
                   json={'subsystem': APPROVE, 'access_level': 'full'})
    assert r.status_code == 400
    assert r.get_json()['error'] == ua.CASH_SELF_APPROVAL_MESSAGE
    assert _caps(db_path, uid)[APPROVE] == 'none'


def test_m7_granting_a_capability_queues_its_user_permission_update_event(admin, db_path):
    """M7. The receiving devices only learn of a grant through the outbox, so
    the route must queue a `user_permission` event carrying the NEW level."""
    uid = _make(admin, role='manager')
    # A manager starts with cash.close; approve may only be given if close is
    # off (AUDIT-032), so set that up first.
    _force_grant(db_path, uid, CLOSE, 'none')
    _force_grant(db_path, uid, APPROVE, 'none')
    marker = _rows(db_path, "SELECT COALESCE(MAX(rowid),0) AS m FROM sync_outbox")[0]['m']

    r = admin.post(f'/api/admin/employees/{uid}/permissions',
                   json={'subsystem': APPROVE, 'access_level': 'full'})
    assert r.status_code == 200, r.get_json()

    events = _rows(db_path, "SELECT entity_type, event_type, payload FROM sync_outbox "
                            "WHERE rowid>? ORDER BY rowid", (marker,))
    perm = [e for e in events if e['entity_type'] == 'user_permission']
    assert [e['event_type'] for e in perm] == ['delete', 'update']
    last = json.loads(perm[-1]['payload'])
    assert last['subsystem'] == APPROVE and last['access_level'] == 'full'
    assert last['user_uid'] == _uid(db_path, uid)
    assert any(e['entity_type'] == 'user' and e['event_type'] == 'update' for e in events)


def test_m7_a_revoke_queues_too_and_a_failed_request_queues_nothing(admin, db_path):
    uid = _make(admin, role='manager')
    marker = _rows(db_path, "SELECT COALESCE(MAX(rowid),0) AS m FROM sync_outbox")[0]['m']
    bad = admin.post(f'/api/admin/employees/{uid}/permissions',
                     json={'subsystem': 'not.a.code', 'access_level': 'full'})
    assert bad.status_code == 400
    assert _rows(db_path, "SELECT COUNT(*) AS n FROM sync_outbox WHERE rowid>?", (marker,))[0]['n'] == 0
    ok = admin.post(f'/api/admin/employees/{uid}/permissions',
                    json={'subsystem': CLOSE, 'access_level': 'none'})
    assert ok.status_code == 200
    n = _rows(db_path, "SELECT COUNT(*) AS n FROM sync_outbox WHERE rowid>? AND entity_type='user_permission'",
              (marker,))[0]['n']
    assert n == 2


def test_h3_update_perms_takes_the_write_lock_before_it_reads(admin, db_path, monkeypatch):
    """H3, lock half. While ANOTHER connection holds the write lock, the route
    must wait (and here, time out) BEFORE it reads the person's grants; a route
    that read first would judge a stale set. Weak by construction (one process
    cannot really race two writers), so this pins only the ORDER: the route
    cannot get past its first statement while the lock is held elsewhere."""
    uid = _make(admin, role='manager')
    _force_grant(db_path, uid, CLOSE)

    def short_conn():
        c = sqlite3.connect(str(db_path), timeout=0.2)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(onboarding_routes, "get_conn", short_conn)
    holder = sqlite3.connect(str(db_path), timeout=5)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            admin.post(f'/api/admin/employees/{uid}/permissions',
                       json={'subsystem': APPROVE, 'access_level': 'full'})
    finally:
        holder.rollback()
        holder.close()
    # ...and once the lock is free the same request goes through the check.
    r = admin.post(f'/api/admin/employees/{uid}/permissions',
                   json={'subsystem': APPROVE, 'access_level': 'full'})
    assert r.status_code == 400


# ── L1: old sender demotes an Accountant ─────────────────────────────────────

def test_l1_old_sender_demoting_an_accountant_to_manager_resets_books():
    plan = ua.user_sync_apply_plan({'role': 'manager'}, 'manager', local_role_preset='accountant')
    assert plan['reset_books'] is True
    assert plan['books_plan'] == {c: 'none' for c in ua.BOOKS_CAPABILITY_CODES}


@pytest.mark.parametrize("local_preset", [None, '', 'manager'])
def test_l1_allow_half_a_plain_manager_is_not_reset(local_preset):
    plan = ua.user_sync_apply_plan({'role': 'manager'}, 'manager', local_role_preset=local_preset)
    assert plan['reset_books'] is False


def test_l1_allow_half_a_new_sender_with_the_key_present_never_resets_here():
    plan = ua.user_sync_apply_plan({'role': 'manager', 'role_preset': 'accountant'}, 'manager',
                                   local_role_preset='accountant')
    assert plan['reset_books'] is False and plan['has_role_preset'] is True


def test_l1_omitting_the_new_argument_keeps_the_old_behaviour():
    assert ua.user_sync_apply_plan({'role': 'manager'}, 'manager')['reset_books'] is False
    assert ua.user_sync_apply_plan({'role': 'cashier'}, 'manager')['reset_books'] is True


def test_l1_a_branch_manager_label_on_a_manager_also_resets():
    plan = ua.user_sync_apply_plan({'role': 'manager'}, 'manager', local_role_preset='branch_manager')
    assert plan['reset_books'] is True


# ── L3: the text rule ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("Sam‮", "Sam"),             # U+202E right-to-left override
    ("Sam\x00", "Sam"),               # NUL
    ("S​am", "Sam"),             # U+200B zero-width space
    ("⁧Sam⁩", "Sam"),       # U+2067 / U+2069 isolates
    ("‮" * 60 + "ok", "ok"),    # length is judged AFTER stripping
])
def test_l3_invisible_controls_are_stripped_and_length_is_judged_after(raw, expected):
    assert ua.normalize_display_name(raw) == expected


@pytest.mark.parametrize("raw", ["two\nlines", "cr\rhere", "a b", "a b", "tab\x0bvt"])
def test_l3_a_line_break_is_refused(raw):
    with pytest.raises(ValueError, match="Name must be a single line."):
        ua.normalize_display_name(raw)


def test_l3_allow_half_arabic_joiners_and_arabic_names_survive():
    joined = "می‌خواهم"                 # contains U+200C
    assert "‌" in ua.normalize_display_name(joined)
    assert "‍" in ua.normalize_display_name("a‍b")    # U+200D
    arabic = "محمد أحمد"
    assert ua.normalize_display_name(arabic) == arabic


@pytest.mark.parametrize("raw", [
    "Ahmad\u200f (أحمد)",     # U+200F RLM between Latin and Arabic
    "رقم\u061c 5",           # U+061C ALM before a digit
    "\u200fكاشير 1",              # leading RLM
    "Sam\u200e (سام)",       # U+200E LRM
])
def test_d12_directional_marks_survive_in_mixed_arabic_latin_names(raw):
    """Mutant: RLM/LRM/ALM dropped from _KEPT_FORMAT_CHARS. These are the marks
    a bilingual name NEEDS to display in order; stripping them (as the first L3
    pass did) scrambled 'Ahmad (أحمد)' style names."""
    assert ua.normalize_display_name(raw) == raw


@pytest.mark.parametrize("raw, expected", [
    ("Sam\u202e", "Sam"),                  # U+202E override
    ("\u202aSam\u202c", "Sam"),            # U+202A / U+202C embedding
    ("\u2067Sam\u2069", "Sam"),            # isolates
    ("S\u200bam", "Sam"),                  # U+200B zero-width space
    ("Sam\u200f\u202e", "Sam\u200f"),           # a kept RLM does not shelter an override
])
def test_d12_overrides_embeddings_isolates_are_still_stripped(raw, expected):
    """The ALLOW-half's partner: widening the kept set to the three marks must
    not have widened it to the rest of Cc/Cf."""
    assert ua.normalize_display_name(raw) == expected


def test_l3_only_invisible_characters_means_no_name():
    assert ua.normalize_display_name("​‮") is None


def test_l3_sanitize_synced_display_name():
    f = ua.sanitize_synced_user_fields
    assert f({'display_name': 'Sam‮'})['display_name'] == 'Sam'
    assert f({'display_name': 'line1\nline2'})['display_name'] == 'line1 line2'
    assert f({'display_name': 'x' * 100})['display_name'] == 'x' * ua.DISPLAY_NAME_MAX
    assert f({'display_name': '​'})['display_name'] is None
    assert f({'display_name': 12345})['display_name'] is None
    assert 'display_name' not in f({'role': 'cashier'})               # absent stays absent


def test_l3_sanitize_synced_role_preset():
    f = ua.sanitize_synced_user_fields
    assert f({'role_preset': 'accountant'})['role_preset'] == 'accountant'
    assert 'role_preset' not in f({'role_preset': 'from-a-newer-build'})
    assert 'role_preset' not in f({'role_preset': 7})
    assert 'role_preset' not in f({'role_preset': ['accountant']})
    assert f({'role_preset': None}) == {'role_preset': None}          # explicit clear survives
    out = f({'role': 'manager', 'pin_hash': 'h', 'role_preset': 'zzz'})
    assert out == {'role': 'manager', 'pin_hash': 'h'}                # other keys untouched


def test_l3_sanitize_returns_a_copy_and_tolerates_junk():
    src = {'display_name': 'a\nb'}
    ua.sanitize_synced_user_fields(src)
    assert src == {'display_name': 'a\nb'}
    assert ua.sanitize_synced_user_fields(None) == {}


def test_l3_routes_refuse_a_newline_name(admin, db_path):
    uid = _make(admin, role='cashier')
    r = admin.put(f'/api/admin/employees/{uid}/name', json={'name': 'a\nb'})
    assert r.status_code == 400 and r.get_json()['error'] == 'Name must be a single line.'
    r2, _ = _create(admin, role='cashier', name='a\nb')
    assert r2.status_code == 400 and r2.get_json()['error'] == 'Name must be a single line.'


# ── L5 ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("body", [
    {'permissions': ['retail.sales']},
    {'permissions': 'retail.sales'},
    {'permissions': 7},
])
def test_l5_create_with_a_non_object_permissions_is_400_not_a_crash(admin, body):
    r, _ = _create(admin, role='cashier', **body)
    assert r.status_code == 400
    assert r.get_json()['error'] == 'permissions must be an object.'


@pytest.mark.parametrize("preset", [["accountant"], {"a": 1}, 5])
def test_l5_create_with_a_non_string_preset_is_400_not_a_crash(admin, preset):
    r, _ = _create(admin, preset=preset)
    assert r.status_code == 400


def test_l5_a_body_that_is_not_an_object_is_400(admin):
    r = admin.post('/api/admin/employees', json=['not', 'an', 'object'])
    assert r.status_code == 400
    assert r.get_json()['error'] == 'Request body must be an object.'


def test_l5_rename_writes_an_audit_row(admin, db_path):
    uid = _make(admin, role='cashier')
    assert _audit(db_path, 'UPDATE_NAME') == []
    r = admin.put(f'/api/admin/employees/{uid}/name', json={'name': 'Layla'})
    assert r.status_code == 200
    rows = _audit(db_path, 'UPDATE_NAME')
    assert len(rows) == 1
    assert rows[0]['entity_id'] == uid
    assert json.loads(rows[0]['new_value_json']) == {'display_name': 'Layla'}


def test_l5_a_refused_rename_leaves_no_audit_row(admin, db_path):
    uid = _make(admin, role='cashier')
    admin.put(f'/api/admin/employees/{uid}/name', json={'name': 'x' * 61})
    assert _audit(db_path, 'UPDATE_NAME') == []


# ── Item 16: a branch manager needs a branch ─────────────────────────────────

def test_item16_preset_branch_manager_without_a_scope_is_refused_and_writes_nothing(admin, db_path):
    uid = _make(admin, role='cashier')
    before = _caps(db_path, uid)
    marker = _rows(db_path, "SELECT COALESCE(MAX(rowid),0) AS m FROM sync_outbox")[0]['m']
    r = admin.put(f'/api/admin/employees/{uid}/preset', json={'preset': 'branch_manager'})
    assert r.status_code == 400
    assert r.get_json()['code'] == 'BRANCH_SCOPE_REQUIRED'
    assert r.get_json()['error'] == onboarding_routes.BRANCH_SCOPE_REQUIRED_MESSAGE
    assert _caps(db_path, uid) == before
    assert _rows(db_path, "SELECT role, role_preset FROM users WHERE id=?", (uid,))[0]['role'] == 'cashier'
    assert _rows(db_path, "SELECT COUNT(*) AS n FROM sync_outbox WHERE rowid>?", (marker,))[0]['n'] == 0


def test_item16_preset_branch_manager_with_a_scope_succeeds(admin, db_path):
    uid = _make(admin, role='cashier')
    c = sqlite3.connect(str(db_path))
    c.execute("UPDATE users SET branch_scope_uid=? WHERE id=?", ('branch-uid-1', uid))
    c.commit()
    c.close()
    r = admin.put(f'/api/admin/employees/{uid}/preset', json={'preset': 'branch_manager'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['role_preset'] == 'branch_manager'
    assert ua.CAP_EMPLOYEES in {k for k, v in _caps(db_path, uid).items() if v == 'full'}


def test_item16_a_blank_scope_does_not_count(admin, db_path):
    uid = _make(admin, role='cashier')
    c = sqlite3.connect(str(db_path))
    c.execute("UPDATE users SET branch_scope_uid='   ' WHERE id=?", (uid,))
    c.commit()
    c.close()
    r = admin.put(f'/api/admin/employees/{uid}/preset', json={'preset': 'branch_manager'})
    assert r.status_code == 400 and r.get_json()['code'] == 'BRANCH_SCOPE_REQUIRED'


def test_item16_other_presets_do_not_need_a_scope(admin, db_path):
    uid = _make(admin, role='cashier')
    for preset in ('accountant', 'manager', 'cashier'):
        r = admin.put(f'/api/admin/employees/{uid}/preset', json={'preset': preset})
        assert r.status_code == 200, (preset, r.get_json())


def test_item16_create_branch_manager_without_a_scope_is_refused(admin, db_path):
    r, email = _create(admin, preset='branch_manager')
    assert r.status_code == 400 and r.get_json()['code'] == 'BRANCH_SCOPE_REQUIRED'
    assert _rows(db_path, "SELECT COUNT(*) AS n FROM users WHERE email=?", (email,))[0]['n'] == 0


def test_item16_create_branch_manager_with_a_scope_stores_it(admin, db_path):
    r, email = _create(admin, preset='branch_manager', branch_scope_uid='branch-uid-9')
    assert r.status_code == 200, r.get_json()
    row = _rows(db_path, "SELECT role, role_preset, branch_scope_uid FROM users WHERE email=?", (email,))[0]
    assert (row['role'], row['role_preset'], row['branch_scope_uid']) == ('manager', 'branch_manager', 'branch-uid-9')


def test_item16_create_other_presets_ignore_a_missing_scope(admin, db_path):
    r, _ = _create(admin, preset='accountant')
    assert r.status_code == 200, r.get_json()


def test_scope_route_refuses_to_clear_a_standing_branch_managers_scope(admin, db_path):
    """Mutant: the BRANCH_SCOPE_REQUIRED check in update_branch_scope removed.
    Clearing a branch manager's scope silently widens them to EVERY branch --
    the exact thing create and the preset route refuse. Every clearing shape
    (missing key, null, '', blanks) is refused and the stored scope survives;
    ALLOW halves: moving the scope to another branch works, and clearing the
    scope of a NON-branch-manager still works."""
    r, email = _create(admin, preset='branch_manager', branch_scope_uid='branch-uid-1')
    assert r.status_code == 200, r.get_json()
    uid = _id_for(admin, email)
    marker = _rows(db_path, "SELECT COALESCE(MAX(rowid),0) AS m FROM sync_outbox")[0]['m']
    for body in ({}, {'branch_scope_uid': None}, {'branch_scope_uid': ''}, {'branch_scope_uid': '   '}):
        r = admin.put(f'/api/admin/employees/{uid}/branch-scope', json=body)
        assert r.status_code == 400, (body, r.get_json())
        assert r.get_json()['code'] == 'BRANCH_SCOPE_REQUIRED'
        assert r.get_json()['error'] == onboarding_routes.BRANCH_SCOPE_REQUIRED_MESSAGE
    assert _rows(db_path, "SELECT branch_scope_uid FROM users WHERE id=?", (uid,))[0]['branch_scope_uid'] == 'branch-uid-1'
    assert _rows(db_path, "SELECT COUNT(*) AS n FROM sync_outbox WHERE rowid>?", (marker,))[0]['n'] == 0
    # allow half 1: re-pointing the manager at another branch is fine.
    r = admin.put(f'/api/admin/employees/{uid}/branch-scope', json={'branch_scope_uid': 'branch-uid-2'})
    assert r.status_code == 200, r.get_json()
    assert _rows(db_path, "SELECT branch_scope_uid FROM users WHERE id=?", (uid,))[0]['branch_scope_uid'] == 'branch-uid-2'
    # allow half 2: a plain manager with a scope can still have it cleared.
    plain = _make(admin, role='manager')
    admin.put(f'/api/admin/employees/{plain}/branch-scope', json={'branch_scope_uid': 'branch-uid-3'})
    r = admin.put(f'/api/admin/employees/{plain}/branch-scope', json={'branch_scope_uid': None})
    assert r.status_code == 200, r.get_json()
    assert _rows(db_path, "SELECT branch_scope_uid FROM users WHERE id=?", (plain,))[0]['branch_scope_uid'] is None


# ── M15: the preset route is tenant-scoped ───────────────────────────────────

def test_m15_preset_for_another_companys_user_is_not_found_and_changes_nothing(admin, db_path):
    foreign = str(uuid.uuid4())
    c = sqlite3.connect(str(db_path))
    c.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "uid, row_version, updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (foreign, 'another-company', 'EMP-9999', 'foreign@else.local', 'PENDING', 'cashier',
         'pending_setup', str(uuid.uuid4()), 1, ua.now_utc_iso()))
    c.commit()
    c.close()
    marker = _rows(db_path, "SELECT COALESCE(MAX(rowid),0) AS m FROM sync_outbox")[0]['m']

    r = admin.put(f'/api/admin/employees/{foreign}/preset', json={'preset': 'accountant'})

    assert r.status_code in (403, 404)
    row = _rows(db_path, "SELECT role, role_preset FROM users WHERE id=?", (foreign,))[0]
    assert row['role'] == 'cashier' and row['role_preset'] is None
    assert _caps(db_path, foreign) == {}
    assert _rows(db_path, "SELECT COUNT(*) AS n FROM sync_outbox WHERE rowid>?", (marker,))[0]['n'] == 0


def test_m15_permissions_for_another_companys_user_is_not_found_and_changes_nothing(admin, db_path):
    foreign = str(uuid.uuid4())
    c = sqlite3.connect(str(db_path))
    c.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "uid, row_version, updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (foreign, 'another-company', 'EMP-9998', 'foreign2@else.local', 'PENDING', 'cashier',
         'pending_setup', str(uuid.uuid4()), 1, ua.now_utc_iso()))
    c.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
              (str(uuid.uuid4()), foreign, CLOSE, 'full'))
    c.commit()
    c.close()

    r = admin.post(f'/api/admin/employees/{foreign}/permissions',
                   json={'subsystem': CLOSE, 'access_level': 'none'})

    assert r.status_code in (403, 404)
    assert _caps(db_path, foreign) == {CLOSE: 'full'}


def test_m15_same_company_user_still_works(admin, db_path):
    uid = _make(admin, role='cashier')
    assert admin.put(f'/api/admin/employees/{uid}/preset', json={'preset': 'accountant'}).status_code == 200


# ── Item 14: a role change keeps a preset that has the same base role ────────

def test_item14_role_change_to_the_presets_own_base_role_keeps_the_label(admin, db_path):
    uid = _make(admin, role='cashier')
    assert admin.put(f'/api/admin/employees/{uid}/preset', json={'preset': 'accountant'}).status_code == 200
    r = admin.put(f'/api/admin/employees/{uid}/role', json={'role': 'manager'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['role_preset'] == 'accountant'
    assert _rows(db_path, "SELECT role_preset FROM users WHERE id=?", (uid,))[0]['role_preset'] == 'accountant'
    assert APPROVE in {k for k, v in _caps(db_path, uid).items() if v == 'full'}   # rows kept too


def test_item14_allow_half_a_real_role_change_replaces_the_label(admin, db_path):
    uid = _make(admin, role='cashier')
    assert admin.put(f'/api/admin/employees/{uid}/preset', json={'preset': 'accountant'}).status_code == 200
    r = admin.put(f'/api/admin/employees/{uid}/role', json={'role': 'cashier'})
    assert r.status_code == 200
    assert r.get_json()['role_preset'] == 'cashier'
    assert not set(ua.BOOKS_CAPABILITY_CODES) & {k for k, v in _caps(db_path, uid).items() if v == 'full'}


# ── AUDIT-032 across two computers, through the REAL push/pull path ──────────

CO = "shop-co-1"


def _factory(p):
    def g():
        c = sqlite3.connect(str(p), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c
    return g


def _build(tmp_path, name):
    d = tmp_path / name
    mp = pytest.MonkeyPatch()
    mp.setattr(registry_db, "_db_dir", str(d))
    mp.setattr(registry_db, "DB_PATH", str(d / "registry.db"))
    registry_db.init_registry_db()
    mp.undo()
    return str(d / "registry.db"), _factory(d / "registry.db")


class _Pipe:
    def __init__(self, out_list, in_list):
        self.out, self.inn = out_list, in_list

    def push(self, chunk):
        self.out.extend(chunk)

    def pull(self, since):
        return {"events": self.inn[since:], "cursor": len(self.inn)}


def _held(get_conn, email):
    c = get_conn()
    try:
        uid = c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()[0]
        return {code: ua.user_has_capability(c, uid, code) for code in (CLOSE, APPROVE)}, uid
    finally:
        c.close()


def _flask(monkeypatch, db_path, get_conn, tmp_path, name):
    monkeypatch.setattr(onboarding_routes, "get_conn", get_conn)
    monkeypatch.setattr(mt_auth, "REGISTRY_DB", db_path)
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path / name))
    a = Flask(name)
    a.secret_key = "s"
    a.testing = True
    a.register_blueprint(onboarding_bp)
    return a


def _two_computers_cross_grants(tmp_path, monkeypatch):
    """The verifier's AUDIT-032 probe, ported. Each computer's update_perms
    check passes on its own copy (A grants approve, B grants close); the grants
    cross by the real sync path. Returns what each computer holds afterwards."""
    monkeypatch.setattr(flask_guard.LicenseStateRepository, "__init__", lambda self, db_path: None)
    monkeypatch.setattr(flask_guard.LicenseStateRepository, "load", lambda self: LicenseStateRecord(
        licensing_schema_version=1, product_code="AURA_TEST", platform="WINDOWS", current_state="ACTIVE_ONLINE"))
    pa, ga = _build(tmp_path, "A")
    pb, gb = _build(tmp_path, "B")
    ab, ba = [], []
    # Fix round 2 (D13): enforce_cash_pair_after_apply is now scoped to the
    # company it is handed, so the engine must be handed the company the users
    # REALLY belong to. This fixture used to feed a made-up CO ("shop-co-1")
    # while create-admin minted its own company id; the old unscoped lookup
    # never noticed. `real_co` is filled in once create-admin has run and is
    # what the engine's provider reports from then on -- what a real till does.
    real_co = {'v': CO}
    sa = SyncService(client_factory=lambda: _Pipe(ab, ba), get_conn=ga,
                     local_company_id_provider=lambda: real_co['v'],
                     handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES)
    sb = SyncService(client_factory=lambda: _Pipe(ba, ab), get_conn=gb,
                     local_company_id_provider=lambda: real_co['v'],
                     handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES)

    ca = _flask(monkeypatch, pa, ga, tmp_path, "A").test_client()
    assert ca.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner@shop.co', 'password': 'OwnerPW11'}).status_code == 200
    _c = ga()
    real_co['v'] = _c.execute("SELECT company_id FROM users WHERE email='owner@shop.co'").fetchone()[0]
    _c.close()
    assert ca.post('/api/admin/employees', json={'email': 'x@shop.co', 'role': 'manager'}).status_code == 200
    _, xa = _held(ga, 'x@shop.co')
    assert ca.post(f'/api/admin/employees/{xa}/permissions',
                   json={'subsystem': CLOSE, 'access_level': 'none'}).status_code == 200
    sa.push_once()
    sb.pull_once()
    assert _held(gb, 'x@shop.co')[0] == {CLOSE: False, APPROVE: False}

    assert ca.post(f'/api/admin/employees/{xa}/permissions',
                   json={'subsystem': APPROVE, 'access_level': 'full'}).status_code == 200

    cb = _flask(monkeypatch, pb, gb, tmp_path, "B").test_client()
    c = gb()
    owner_b = c.execute("SELECT id FROM users WHERE email='owner@shop.co'").fetchone()[0]
    c.close()
    _, xb = _held(gb, 'x@shop.co')
    with cb.session_transaction() as s:
        s.update({'mt_user_id': owner_b, 'mt_role': 'admin', 'company_id': real_co['v'],
                  'mt_session_version': 999, 'email': 'owner@shop.co'})
    assert cb.post(f'/api/admin/employees/{xb}/permissions',
                   json={'subsystem': CLOSE, 'access_level': 'full'}).status_code == 200

    sa.push_once()
    sb.push_once()
    sb.pull_once()
    sa.pull_once()

    return [_held(get_conn, 'x@shop.co')[0] for get_conn in (ga, gb)]


def test_h3_two_computers_each_pass_their_own_check_but_the_merge_cannot_breach(tmp_path, monkeypatch):
    """Before the fix both ended holding BOTH codes; now the receiving side
    drops approve, on both."""
    for held in _two_computers_cross_grants(tmp_path, monkeypatch):
        assert not (held[CLOSE] and held[APPROVE]), held        # never both, on either computer


def test_h3_the_two_computer_scenario_really_breaches_when_the_receiving_guard_is_off(
        tmp_path, monkeypatch):
    """Proves the scenario above is not vacuous (ENGINEERING 1: a pass must be
    shown to depend on the thing it guards). sync_service's receiving-side call
    is replaced by a no-op -- the pre-fix behaviour -- and the SAME crossing
    then leaves at least one computer holding cash.close AND cash.approve. If
    this stops breaching, the test above has lost its teeth (the fixture no
    longer manufactures the race), not gained safety."""
    from commercial_runtime.sync import sync_service
    monkeypatch.setattr(sync_service, "enforce_cash_pair_after_apply",
                        lambda *a, **k: False)
    outcomes = _two_computers_cross_grants(tmp_path, monkeypatch)
    assert any(h[CLOSE] and h[APPROVE] for h in outcomes), outcomes
