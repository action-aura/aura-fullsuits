"""A failed IDENTITY audit write must be DISCOVERABLE without becoming FATAL.

Seven `INSERT INTO audit_logs` statements in onboarding_routes.py were
wrapped in a bare `except Exception: pass` -- no log line, no counter, no
trace: PASSWORD_RESET, CREATE_EMPLOYEE, UPDATE_BRANCH_SCOPE, UPDATE_ROLE,
SET_PIN/CLEAR_PIN, UPDATE_CLINIC_ROLE and EMPLOYEE_SETUP_COMPLETE. Those are
the PRIVILEGE-CHANGE records, so a lost one is a lost answer to "who gave
this account that authority". The file was even internally inconsistent
about it: the UPDATE_STATUS and UPDATE_PERM inserts were never wrapped at
all.

Retail fixed exactly this shape at products/retail/backend/api/retail_api.py
(swallow kept, `log.exception` added), pinned in both directions by
products/retail/tests/retail_audit_write_failure_test.py. The identity
layer, where the privilege changes actually happen, never got it. This file
is that test for this layer.

It compounds with a second gap, which this file cannot close and does not
pretend to: registry `audit_logs` is read only by GET /api/admin/audit and
GET /api/admin/stats, and no shipped client calls either. So a lost row has
no reader to miss it, and the log line is currently the only place the loss
can surface at all.

BOTH DIRECTIONS ARE TESTED. A test that only proves "a failure gets logged"
is satisfied by an implementation that logs unconditionally, or by one that
raises -- so this file also proves the promotion still SUCCEEDS while the
audit table is gone (the swallow), and that an ordinary successful
promotion writes a real row and logs nothing (the allow-half).

Fixture shape copied from test_employee_admin_routes.py in this package --
see that file's docstring for why registry_db.DB_PATH, onboarding_routes.
get_conn, mt_auth.REGISTRY_DB and LicenseStateRepository all have to be
redirected together.

Run:
    pytest commercial_runtime/identity/tests/test_audit_write_failure.py -v
"""
import logging
import sqlite3
import uuid

import pytest
from flask import Flask

from commercial_runtime.identity import mt_auth, onboarding_routes, registry_db
from commercial_runtime.identity.onboarding_routes import onboarding_bp
from commercial_runtime.licensing_contracts import flask_guard
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRecord


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
            current_state="ACTIVE_ONLINE",
        ),
    )

    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"  # pragma: allowlist secret -- a Flask test secret_key, literally the string 'test-secret'
    flask_app.testing = True
    flask_app.register_blueprint(onboarding_bp)
    return flask_app


@pytest.fixture
def admin(app):
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner@test.local', 'password': 'OwnerPW11',  # pragma: allowlist secret -- throwaway fixture for an account this test creates
    })
    assert r.status_code == 200, r.get_json()
    return client


def _make_employee(admin, role='cashier'):
    email = f'emp-{uuid.uuid4().hex[:8]}@test.local'
    r = admin.post('/api/admin/employees', json={'email': email, 'role': role})
    assert r.status_code == 200, r.get_json()
    employees = admin.get('/api/admin/employees').get_json()['employees']
    return next(e for e in employees if e['email'] == email)['id'], email


def _drop_audit_logs(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.execute('DROP TABLE audit_logs')
    conn.commit()
    conn.close()


def _audit_records(caplog):
    return [r for r in caplog.records if 'audit write FAILED' in r.getMessage()]


def _audit_rows(db_path, action):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM audit_logs WHERE action=?", (action,)).fetchall()
    finally:
        conn.close()


# ── The failure half ─────────────────────────────────────────────────────────

def test_a_promotion_still_succeeds_when_the_audit_table_is_gone(admin, db_path, caplog):
    """The swallow, kept deliberately: an owner must be able to promote a
    manager during a shift even if the audit table is full, locked or
    corrupt. Refusing the privilege change in order to record it is the
    wrong trade."""
    user_id, _ = _make_employee(admin)
    _drop_audit_logs(db_path)

    with caplog.at_level(logging.ERROR, logger=onboarding_routes.log.name):
        r = admin.put(f'/api/admin/employees/{user_id}/role', json={'role': 'manager'})

    assert r.status_code == 200, r.get_json()
    assert r.get_json()['role'] == 'manager'

    # And the promotion actually landed -- the swallow must not have taken
    # the surrounding transaction down with it.
    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()[0] == 'manager'
    finally:
        conn.close()


def test_the_lost_privilege_record_is_reported_at_error_with_a_traceback(admin, db_path, caplog):
    user_id, _ = _make_employee(admin)
    _drop_audit_logs(db_path)

    with caplog.at_level(logging.ERROR, logger=onboarding_routes.log.name):
        assert admin.put(f'/api/admin/employees/{user_id}/role',
                         json={'role': 'manager'}).status_code == 200

    records = _audit_records(caplog)
    assert records, (
        'a swallowed identity audit failure must leave a trace. This table '
        'has no reader in any shipped client, so the log line is the only '
        'place the loss can surface.')
    record = records[0]
    assert record.levelno >= logging.ERROR
    # log.exception, not log.warning: the traceback is what separates a
    # locked database from schema drift, and those need different answers.
    assert record.exc_info is not None
    message = record.getMessage()
    assert 'UPDATE_ROLE' in message, 'the log line must name the action that was lost'
    assert user_id in message, 'the log line must name the account it was about'


def test_the_new_value_payload_is_never_written_to_the_log(admin, db_path, caplog):
    """`new_value_json` carries emails and role names; the log has a wider
    audience and a longer life than the audit table it was bound for.
    CREATE_EMPLOYEE is the sharpest case -- its payload is the employee's
    email address."""
    _drop_audit_logs(db_path)
    email = f'emp-{uuid.uuid4().hex[:8]}@test.local'

    with caplog.at_level(logging.ERROR, logger=onboarding_routes.log.name):
        r = admin.post('/api/admin/employees', json={'email': email, 'role': 'cashier'})

    assert r.status_code == 200, r.get_json()
    records = _audit_records(caplog)
    assert records, 'CREATE_EMPLOYEE is one of the seven; its loss must be reported too'
    blob = '\n'.join(r_.getMessage() for r_ in caplog.records)
    assert email not in blob, (
        'the employee email must not reach the log -- action/entity_type/'
        'entity_id are enough to find the missing row')


@pytest.mark.parametrize('action,call', [
    ('SET_PIN', lambda c, uid: c.put(f'/api/admin/employees/{uid}/pin', json={'pin': '1234'})),
    ('UPDATE_BRANCH_SCOPE',
     lambda c, uid: c.put(f'/api/admin/employees/{uid}/branch-scope', json={'branch_scope_uid': None})),
    ('UPDATE_CLINIC_ROLE',
     lambda c, uid: c.put(f'/api/admin/employees/{uid}/clinic-role', json={'clinic_role': 'doctor'})),
])
def test_every_wrapped_privilege_route_reports_its_own_lost_row(admin, db_path, caplog, action, call):
    """One route going quiet is a bug; six others going quiet because only
    one was fixed is the same bug six more times. Parametrized over the
    remaining reachable handlers rather than trusting that fixing UPDATE_ROLE
    fixed its siblings."""
    user_id, _ = _make_employee(admin)
    _drop_audit_logs(db_path)

    with caplog.at_level(logging.ERROR, logger=onboarding_routes.log.name):
        r = call(admin, user_id)

    assert r.status_code == 200, r.get_json()
    messages = [rec.getMessage() for rec in _audit_records(caplog)]
    assert any(action in m for m in messages), (
        f'{action} lost its audit row silently; reported lines were {messages}')


# ── The success half: the one a failure-only test cannot see ─────────────────

def test_an_ordinary_promotion_writes_a_real_row_and_logs_nothing(admin, db_path, caplog):
    """Without this, an implementation that logs unconditionally -- or one
    that logs and then swallows a failure it never had -- passes every test
    above while telling the operator nothing useful. It is also the half
    that quietly destroys the audit trail if it breaks."""
    user_id, _ = _make_employee(admin)

    with caplog.at_level(logging.DEBUG, logger=onboarding_routes.log.name):
        assert admin.put(f'/api/admin/employees/{user_id}/role',
                         json={'role': 'manager'}).status_code == 200

    rows = _audit_rows(db_path, 'UPDATE_ROLE')
    assert len(rows) == 1, 'the ordinary path must still write the privilege record'
    assert rows[0]['entity_id'] == user_id
    assert '"to": "manager"' in rows[0]['new_value_json'], (
        'new_value_json belongs IN the audit table -- it is only the LOG that '
        'must not carry it')

    assert not _audit_records(caplog), 'a write that succeeded must stay silent'
