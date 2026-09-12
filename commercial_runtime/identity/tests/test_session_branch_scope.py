"""mt_auth.session_branch_scope() -- launch-readiness account-hierarchy
design §3.3/§4.2 D6, the scope read helper.

Standalone, in the same spirit as test_mt_require_subsystem_fail_closed.py
in this package: a tiny one-route Flask app that calls the function under
test directly and reports what it returned/raised, so a result can only
have come from `session_branch_scope()` itself. Drives the REAL registry
schema through `registry_db.init_registry_db()`.

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/identity/tests/test_session_branch_scope.py -v
"""
import sqlite3
import uuid

import pytest
from flask import Flask, jsonify

from commercial_runtime.identity import mt_auth, registry_db
from commercial_runtime.identity.mt_auth import BranchScopeLookupError, session_branch_scope


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(path))
    monkeypatch.setattr(registry_db, "_db_dir", str(tmp_path))
    registry_db.init_registry_db()
    # session_branch_scope resolves its own connection through
    # mt_auth._get_registry_conn(), which reads the module-level
    # mt_auth.REGISTRY_DB global at call time -- same pattern as
    # test_mt_require_subsystem_fail_closed.py's identical fixture.
    monkeypatch.setattr(mt_auth, "REGISTRY_DB", str(path))
    return path


@pytest.fixture
def app(db_path):
    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"
    flask_app.testing = True

    @flask_app.route('/dummy/scope')
    def dummy_scope():
        # A DELIBERATELY UN-DECORATED route (no mt_login_required) -- this
        # file is not testing a route guard, it is testing what the bare
        # function returns/raises for whatever the session happens to hold.
        try:
            scope = session_branch_scope()
            return jsonify({'ok': True, 'scope': scope})
        except BranchScopeLookupError as exc:
            return jsonify({'ok': False, 'error': str(exc)}), 403

    return flask_app


def _insert_user(db_path, user_id, company_id, role='cashier', branch_scope_uid=None):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, branch_scope_uid) "
        "VALUES (?,?,?,?,?,?, 'active', ?)",
        (user_id, company_id, f'EMP-{uuid.uuid4().hex[:6]}', f'{user_id}@test.local', 'x', role, branch_scope_uid),
    )
    conn.commit()
    conn.close()


def _login(client, user_id, company_id, role='cashier', demo=False):
    with client.session_transaction() as s:
        s['mt_user_id'] = user_id
        s['company_id'] = company_id
        s['mt_role'] = role
        if demo:
            s['is_demo_mode'] = True


# ═════════════════════════════════════════════════════════════════════════════
# (6) An admin is NEVER scoped -- structurally, unconditionally, ignoring
#     whatever (if anything) is stored on the admin's own row.
# ═════════════════════════════════════════════════════════════════════════════

def test_an_admin_resolves_to_none_regardless_of_the_stored_row(app, db_path):
    admin_id, company_id = str(uuid.uuid4()), str(uuid.uuid4())
    # Hand-set a scope directly on the admin's own row -- unreachable through
    # D5 (which refuses this), but session_branch_scope() must still ignore
    # it: the owner is never scoped BY THE SESSION ROLE, not merely by D5's
    # refusal to write one.
    _insert_user(db_path, admin_id, company_id, role='admin', branch_scope_uid='some-branch-uid')
    client = app.test_client()
    _login(client, admin_id, company_id, role='admin')

    body = client.get('/dummy/scope').get_json()
    assert body == {'ok': True, 'scope': None}


def test_a_demo_session_resolves_to_none_like_admin(app, db_path):
    cashier_id, company_id = str(uuid.uuid4()), str(uuid.uuid4())
    _insert_user(db_path, cashier_id, company_id, role='cashier', branch_scope_uid='branch-x')
    client = app.test_client()
    _login(client, cashier_id, company_id, role='cashier', demo=True)

    body = client.get('/dummy/scope').get_json()
    assert body == {'ok': True, 'scope': None}


# ═════════════════════════════════════════════════════════════════════════════
# (3) NULL scope = every branch -- the unscoped default every existing
#     non-admin account carries today.
# ═════════════════════════════════════════════════════════════════════════════

def test_a_non_admin_with_no_stored_scope_resolves_to_none(app, db_path):
    cashier_id, company_id = str(uuid.uuid4()), str(uuid.uuid4())
    _insert_user(db_path, cashier_id, company_id, role='cashier', branch_scope_uid=None)
    client = app.test_client()
    _login(client, cashier_id, company_id, role='cashier')

    body = client.get('/dummy/scope').get_json()
    assert body == {'ok': True, 'scope': None}


def test_a_non_admin_with_a_stored_scope_resolves_to_it(app, db_path):
    manager_id, company_id = str(uuid.uuid4()), str(uuid.uuid4())
    _insert_user(db_path, manager_id, company_id, role='manager', branch_scope_uid='branch-uid-123')
    client = app.test_client()
    _login(client, manager_id, company_id, role='manager')

    body = client.get('/dummy/scope').get_json()
    assert body == {'ok': True, 'scope': 'branch-uid-123'}


# ═════════════════════════════════════════════════════════════════════════════
# (10) FAIL CLOSED, and PROVEN by counting the read, not merely by the
#      response shape -- a raised BranchScopeLookupError, never a silent
#      None (which would be the single MOST PERMISSIVE answer this function
#      could give).
# ═════════════════════════════════════════════════════════════════════════════

class _ExplodingRegistry:
    """Stands in for `mt_auth._get_registry_conn` and raises instead of
    returning a connection. Counts its own calls so a refusal can be proven
    to have actually come from the exception branch."""

    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise sqlite3.OperationalError('database is locked')


def test_a_registry_read_failure_raises_rather_than_returning_none(app, db_path, monkeypatch):
    cashier_id, company_id = str(uuid.uuid4()), str(uuid.uuid4())
    _insert_user(db_path, cashier_id, company_id, role='cashier', branch_scope_uid='branch-uid-9')
    client = app.test_client()
    _login(client, cashier_id, company_id, role='cashier')
    assert client.get('/dummy/scope').get_json()['scope'] == 'branch-uid-9', \
        'precondition: a healthy session resolves its real scope'

    boom = _ExplodingRegistry()
    monkeypatch.setattr(mt_auth, '_get_registry_conn', boom)

    resp = client.get('/dummy/scope')
    body = resp.get_json()

    assert boom.calls >= 1, "the helper's DB read never ran -- this test would assert nothing"
    assert resp.status_code == 403
    assert body['ok'] is False
    assert 'scope' not in body, "a lookup failure must never carry a scope value, not even None"


def test_admin_never_touches_the_registry_at_all(app, db_path, monkeypatch):
    """The admin short-circuit must return BEFORE any registry read -- an
    admin's scope answer must not depend on the registry being reachable."""
    admin_id, company_id = str(uuid.uuid4()), str(uuid.uuid4())
    _insert_user(db_path, admin_id, company_id, role='admin')
    client = app.test_client()
    _login(client, admin_id, company_id, role='admin')

    boom = _ExplodingRegistry()
    monkeypatch.setattr(mt_auth, '_get_registry_conn', boom)

    body = client.get('/dummy/scope').get_json()
    assert body == {'ok': True, 'scope': None}
    assert boom.calls == 0, "the admin bypass must never reach the registry read"


def test_a_missing_user_row_raises_rather_than_reading_as_unscoped(app, db_path):
    """A session naming a user id the registry no longer has (deleted, or a
    stale/forged cookie) must refuse rather than silently answer 'every
    branch' -- the single most permissive value this function can return."""
    ghost_id, company_id = str(uuid.uuid4()), str(uuid.uuid4())
    client = app.test_client()
    _login(client, ghost_id, company_id, role='cashier')

    resp = client.get('/dummy/scope')
    assert resp.status_code == 403
    assert resp.get_json()['ok'] is False

# ═════════════════════════════════════════════════════════════════════════════
# (12) NO IDENTIFIED CALLER AT ALL -- must raise, never read as "every branch".
#
#      The third door into the same failure as the two cases above, and the one
#      that was open: mutating this branch from `raise BranchScopeLookupError`
#      to `return None` left every other test in this file AND every test in
#      products/retail/tests/retail_branch_scope_enforcement_test.py green.
#      Eighteen passing tests, and the most permissive value this function can
#      return was reachable without one of them objecting.
#
#      Reachable only ahead of `mt_login_required` -- i.e. a caller-ordering
#      bug, not a registry fault. That is precisely why it must fail loudly: a
#      route wired in the wrong decorator order would otherwise silently grant
#      every branch to a caller the product never identified.
# ═════════════════════════════════════════════════════════════════════════════

def test_a_session_with_no_user_id_raises_rather_than_reading_as_unscoped(app, db_path):
    """A session carrying no `mt_user_id` has not identified anybody. Answering
    `None` would hand it 'every branch' -- the widest possible answer -- on the
    strength of no identity whatsoever."""
    client = app.test_client()
    with client.session_transaction() as s:
        s['company_id'] = str(uuid.uuid4())
        s['mt_role'] = 'cashier'
        # deliberately NO mt_user_id

    resp = client.get('/dummy/scope')
    assert resp.status_code == 403, (
        'a session with no mt_user_id must be refused, not resolved to '
        '"every branch". Got %s: %s' % (resp.status_code, resp.get_json())
    )
    assert resp.get_json()['ok'] is False
