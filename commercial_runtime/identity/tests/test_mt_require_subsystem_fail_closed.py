"""mt_auth.mt_require_subsystem -- the last decorator that still fails OPEN.

`mt_login_required` and `mt_require_capability` both fail closed on a
registry read failure (see their own docstrings in mt_auth.py). This
decorator still had the exact `except Exception: pass` that was just closed
on `mt_login_required` -- "Fail-open only for a transient local-SQLite read
error" -- which makes the BLANKET subsystem gate weaker than the
FINE-GRAINED capability gate stacked on top of it on every mutating retail
route: a two-second `database is locked` used to serve the request here even
though `mt_require_capability` right next to it would have refused the very
same fault correctly.

Standalone, in the same spirit as test_device_routes.py in this package: a
tiny one-route Flask app decorated with nothing but `mt_require_subsystem`,
so a refusal can only have come from the decorator under test. Drives the
REAL registry schema through `registry_db.init_registry_db()`.

Run:
    pytest commercial_runtime/identity/tests/test_mt_require_subsystem_fail_closed.py -v
"""
import sqlite3
import uuid

import pytest
from flask import Flask, jsonify

from commercial_runtime.identity import mt_auth, registry_db
from commercial_runtime.identity.mt_auth import mt_require_subsystem


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(path))
    monkeypatch.setattr(registry_db, "_db_dir", str(tmp_path))
    registry_db.init_registry_db()
    # mt_require_subsystem resolves its own connection through
    # mt_auth._get_registry_conn(), which reads the module-level
    # mt_auth.REGISTRY_DB global at call time -- reassigning it here is
    # sufficient without reloading either module (see test_device_routes.py's
    # own docstring for the general pattern this mirrors).
    monkeypatch.setattr(mt_auth, "REGISTRY_DB", str(path))
    return path


@pytest.fixture
def app(db_path):
    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"
    flask_app.testing = True

    @flask_app.route('/dummy/retail-only')
    @mt_require_subsystem('retail')
    def dummy_retail_only():
        return jsonify({'ok': True})

    return flask_app


def _insert_user(db_path, user_id, company_id, role='cashier', access_level='full'):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status) "
        "VALUES (?,?,?,?,?,?, 'active')",
        (user_id, company_id, f'EMP-{uuid.uuid4().hex[:6]}', f'{user_id}@test.local', 'x', role),
    )
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, 'retail', access_level),
    )
    conn.commit()
    conn.close()


def _login(client, user_id, company_id, role='cashier'):
    with client.session_transaction() as s:
        s['mt_user_id'] = user_id
        s['company_id'] = company_id
        s['mt_role'] = role


class _ExplodingRegistry:
    """Stands in for `mt_auth._get_registry_conn` and raises the single most
    likely real fault instead of returning a connection. Counts its calls so
    a 403 here can be proven to have actually come from the `except` branch
    rather than from an ordinary denial that happens to look the same."""

    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise sqlite3.OperationalError('database is locked')


def test_precondition_a_healthy_granted_session_passes(app, db_path):
    user_id, company_id = str(uuid.uuid4()), str(uuid.uuid4())
    _insert_user(db_path, user_id, company_id, access_level='full')
    client = app.test_client()
    _login(client, user_id, company_id)

    assert client.get('/dummy/retail-only').status_code == 200


def test_a_registry_read_failure_fails_closed(app, db_path, monkeypatch):
    user_id, company_id = str(uuid.uuid4()), str(uuid.uuid4())
    _insert_user(db_path, user_id, company_id, access_level='full')
    client = app.test_client()
    _login(client, user_id, company_id)
    assert client.get('/dummy/retail-only').status_code == 200, "precondition: healthy session works"

    boom = _ExplodingRegistry()
    monkeypatch.setattr(mt_auth, '_get_registry_conn', boom)

    r = client.get('/dummy/retail-only')

    assert boom.calls >= 1, "the decorator's DB read never ran -- this test would assert nothing"
    assert r.status_code == 403, "a DB error must refuse the request, never wave it through"


def test_the_failure_response_is_indistinguishable_from_an_ordinary_denial(app, db_path, monkeypatch):
    """A caller must not be able to tell 'the registry is unhealthy' from
    'you were never granted this subsystem' -- otherwise the response itself
    becomes a health oracle, exactly the property `_unauthenticated_response`
    already protects for `mt_login_required`."""
    denied_id, denied_company = str(uuid.uuid4()), str(uuid.uuid4())
    _insert_user(db_path, denied_id, denied_company, access_level='none')
    denied_client = app.test_client()
    _login(denied_client, denied_id, denied_company)
    ordinary_denial = denied_client.get('/dummy/retail-only')
    assert ordinary_denial.status_code == 403

    broken_id, broken_company = str(uuid.uuid4()), str(uuid.uuid4())
    _insert_user(db_path, broken_id, broken_company, access_level='full')
    broken_client = app.test_client()
    _login(broken_client, broken_id, broken_company)
    monkeypatch.setattr(mt_auth, '_get_registry_conn', _ExplodingRegistry())
    broken_denial = broken_client.get('/dummy/retail-only')

    assert broken_denial.status_code == ordinary_denial.status_code
    assert broken_denial.get_json() == ordinary_denial.get_json()


def test_a_transient_failure_does_not_clear_the_session(app, db_path, monkeypatch):
    """Fail closed, not fail destructive -- clearing the session here would
    turn a couple of seconds of lock contention into every till in the shop
    getting logged out mid-sale, for no security benefit: this check re-runs
    on every request, so refusing THIS one is the whole requirement."""
    user_id, company_id = str(uuid.uuid4()), str(uuid.uuid4())
    _insert_user(db_path, user_id, company_id, access_level='full')
    client = app.test_client()
    _login(client, user_id, company_id)
    assert client.get('/dummy/retail-only').status_code == 200

    monkeypatch.setattr(mt_auth, '_get_registry_conn', _ExplodingRegistry())
    assert client.get('/dummy/retail-only').status_code == 403
    with client.session_transaction() as s:
        assert 'mt_user_id' in s, "a transient DB fault must not tear down the session"


def test_a_recovered_registry_serves_the_same_session_again(app, db_path, monkeypatch):
    user_id, company_id = str(uuid.uuid4()), str(uuid.uuid4())
    _insert_user(db_path, user_id, company_id, access_level='full')
    client = app.test_client()
    _login(client, user_id, company_id)
    assert client.get('/dummy/retail-only').status_code == 200

    # Restored via a second setattr (not monkeypatch.undo()): the `db_path`
    # fixture above shares this SAME monkeypatch instance to point
    # mt_auth.REGISTRY_DB at the temp db, so calling .undo() here would roll
    # that back too, not just this one attribute -- and the "recovery" this
    # test means to prove would actually be measuring a newly-broken DB path,
    # not a genuinely recovered one.
    original_get_registry_conn = mt_auth._get_registry_conn
    monkeypatch.setattr(mt_auth, '_get_registry_conn', _ExplodingRegistry())
    assert client.get('/dummy/retail-only').status_code == 403

    monkeypatch.setattr(mt_auth, '_get_registry_conn', original_get_registry_conn)  # the lock clears, as a transient lock does
    assert client.get('/dummy/retail-only').status_code == 200, \
        "a recovered database must serve the same session again, not demand a fresh login"


def test_an_admin_role_bypasses_the_permission_table_read(app, db_path, monkeypatch):
    """The admin bypass (`if role == 'admin': return f()`) runs AFTER
    `_is_module_enabled` (which makes its own, separately-guarded registry
    read and already tolerates failure by falling back to the local
    config.json license check) but BEFORE the `user_permissions` lookup this
    fix targets. So a broken registry still costs `_is_module_enabled` one
    call -- it is not the thing under test here -- but must never reach the
    permission-table SELECT this test guards: that would mean the fail-closed
    rewrite accidentally started gating a role that was always meant to skip
    the permission table entirely.
    """
    admin_id, company_id = str(uuid.uuid4()), str(uuid.uuid4())
    _insert_user(db_path, admin_id, company_id, role='admin', access_level='none')
    client = app.test_client()
    _login(client, admin_id, company_id, role='admin')

    boom = _ExplodingRegistry()
    monkeypatch.setattr(mt_auth, '_get_registry_conn', boom)

    assert client.get('/dummy/retail-only').status_code == 200
    assert boom.calls == 1, (
        "expected exactly one call, from _is_module_enabled's own tolerated "
        f"read -- {boom.calls} calls means the admin path reached (or skipped) "
        "a different number of registry reads than expected"
    )
