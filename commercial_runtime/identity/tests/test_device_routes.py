"""Per-device login / "one Admin Device" -- tests for
commercial_runtime/identity/device_routes.py.

Drives the blueprint through a real Flask test client (same spirit as
commercial_runtime/sync/tests/test_internal_routes.py) rather than calling
the route functions directly, so the actual HTTP contract (status codes,
JSON envelope shape, session-based auth gating) is what's under test.

Standalone: builds its own tiny sqlite file with just the two columns
mt_login_required's session check needs from `users` (id, status) plus the
real devices/user_devices schema via
device_registry.apply_identity_device_schema() -- no dependency on real
product boot state or the full registry_db.py schema, matching
test_device_registry.py / test_device_context.py's own "standalone" spirit
in this same test package.

Both `device_routes.get_conn` and `mt_auth.REGISTRY_DB` are monkeypatched to
point at that same temp file -- device_routes.py resolves its own connection
via `registry_db.get_conn` (module-level import, bound once), and
mt_login_required's `_get_registry_conn()` reads the module-level
`mt_auth.REGISTRY_DB` global at call time (not a captured local), so
reassigning it via monkeypatch.setattr is sufficient without needing to
reload either module -- both `registry_db.DB_PATH` and `mt_auth.REGISTRY_DB`
are otherwise fixed at first import (see device_context.py's module
docstring for the general anti-pattern this avoids).

Run:
    pytest commercial_runtime/identity/tests/test_device_routes.py -v
"""
import sqlite3

import pytest
from flask import Flask

from commercial_runtime.identity import device_context, device_routes, mt_auth
from commercial_runtime.identity.device_registry import (
    apply_identity_device_schema,
    upsert_local_device,
)
from commercial_runtime.identity.device_routes import device_bp


@pytest.fixture(autouse=True)
def _reset_process_cache():
    """See test_device_context.py's identical fixture -- resolve_local_device()
    caches across calls in the same process; GET /api/devices/me goes
    through that same function, so this suite needs the same reset."""
    device_context._cached_device = None
    yield
    device_context._cached_device = None


@pytest.fixture
def app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path))
    return tmp_path


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "registry.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # Minimal stand-in for registry_db.py's real `users` table -- only the
    # column mt_login_required's session check actually reads.
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'active')")
    apply_identity_device_schema(conn)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def app(db_path, monkeypatch):
    def _get_conn():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    monkeypatch.setattr(device_routes, "get_conn", _get_conn)
    monkeypatch.setattr(mt_auth, "REGISTRY_DB", str(db_path))

    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"
    flask_app.testing = True
    flask_app.register_blueprint(device_bp)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _insert_user(db_path, user_id, status="active"):
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO users (id, status) VALUES (?, ?)", (user_id, status))
    conn.commit()
    conn.close()


def _login(client, user_id, company_id, role="admin"):
    with client.session_transaction() as s:
        s["mt_user_id"] = user_id
        s["company_id"] = company_id
        s["mt_role"] = role


# ── Auth gating ──────────────────────────────────────────────────────────

def test_list_devices_requires_auth(client):
    r = client.get("/api/devices")
    assert r.status_code == 401
    assert r.get_json()["code"] == 401


def test_my_device_requires_auth(client):
    r = client.get("/api/devices/me")
    assert r.status_code == 401
    assert r.get_json()["code"] == 401


# ── GET /api/devices ─────────────────────────────────────────────────────

def test_list_devices_happy_path_response_shape(client, db_path):
    _insert_user(db_path, "user-1")
    _login(client, "user-1", "company-1")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    upsert_local_device(conn, "dev-a", "company-1", device_label="Till 1", platform="WINDOWS")
    conn.close()

    r = client.get("/api/devices")

    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert isinstance(body["devices"], list)
    assert len(body["devices"]) == 1
    device = body["devices"][0]
    assert device["id"] == "dev-a"
    assert device["company_id"] == "company-1"
    assert device["device_label"] == "Till 1"
    assert device["is_admin_device"] is False
    assert isinstance(device["is_admin_device"], bool), "is_admin_device must be a real bool, not 0/1"


def test_list_devices_scoped_to_session_company_id(client, db_path):
    _insert_user(db_path, "user-1")
    _login(client, "user-1", "company-1")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    upsert_local_device(conn, "dev-a", "company-1")
    upsert_local_device(conn, "dev-b", "company-2")
    conn.close()

    r = client.get("/api/devices")

    body = r.get_json()
    assert [d["id"] for d in body["devices"]] == ["dev-a"]


# ── GET /api/devices/me ──────────────────────────────────────────────────

def test_my_device_happy_path_response_shape(app_data, client, db_path):
    _insert_user(db_path, "user-1")
    _login(client, "user-1", "company-1")

    r = client.get("/api/devices/me")

    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    device = body["device"]
    assert device["company_id"] == "company-1"
    assert "is_admin_device" in device, "is_admin_device must be explicitly present for the notification worker self-gate"
    assert isinstance(device["is_admin_device"], bool), "is_admin_device must be a real bool, not 0/1"
    assert device["is_admin_device"] is False, "a freshly resolved device is never the admin device by default"


def test_my_device_reflects_admin_flag_as_bool_true(app_data, client, db_path):
    _insert_user(db_path, "user-1")
    _login(client, "user-1", "company-1")

    # Resolve once to create the row via the real local-UUID path, then
    # promote it to admin directly (set_admin_device is out of scope for
    # this route file, so exercised here only as test setup).
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    from commercial_runtime.identity.device_context import local_device_uuid
    from commercial_runtime.identity.device_registry import set_admin_device
    device_id = local_device_uuid()
    upsert_local_device(conn, device_id, "company-1")
    set_admin_device(conn, "company-1", device_id)
    conn.close()
    device_context._cached_device = None

    r = client.get("/api/devices/me")

    device = r.get_json()["device"]
    assert device["is_admin_device"] is True
    assert isinstance(device["is_admin_device"], bool)
