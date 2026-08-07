"""Tests for internal_routes.py -- the Android-only local sync HTTP surface
(multi-device-sync-foundation, Task 9/wiring). Mirrors test_sync_service.py's
sqlite fixture (same categories/sync_outbox/sync_cursor schema), and drives
the blueprint through a real Flask test client rather than calling the route
functions directly, so the actual HTTP shape (status codes, JSON bodies,
header-based auth) is what's under test -- exactly what Kotlin's
SyncCoordinator depends on.
"""
import json
import sqlite3
import uuid

import pytest
from flask import Flask

from commercial_runtime.sync.internal_routes import make_sync_internal_blueprint
from commercial_runtime.sync.sync_service import SyncService

SECRET = "test-internal-secret"

_SCHEMA = """
CREATE TABLE categories (
    id TEXT PRIMARY KEY,
    company_id INTEGER DEFAULT 1,
    name TEXT NOT NULL,
    description TEXT
);
CREATE TABLE sync_outbox (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE sync_cursor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_seq INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO sync_cursor (id, last_seq) VALUES (1, 0);
"""


class FakeStateRepository:
    """Stand-in for LicenseStateRepository -- only .load().owner_installation_id
    is ever read by internal_routes.py."""

    def __init__(self, owner_installation_id=None):
        self._owner_installation_id = owner_installation_id

    def load(self):
        if self._owner_installation_id is None:
            return None

        class _Rec:
            pass

        rec = _Rec()
        rec.owner_installation_id = self._owner_installation_id
        return rec


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "retail.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def get_conn(db_path):
    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        return c
    return _get_conn


@pytest.fixture
def client(get_conn):
    app = Flask(__name__)
    sync_service = SyncService(None, get_conn)  # client_factory never used by these routes
    app.register_blueprint(make_sync_internal_blueprint(
        sync_service=sync_service,
        get_conn=get_conn,
        state_repository=FakeStateRepository(owner_installation_id="inst-123"),
        shared_secret=SECRET,
    ))
    app.testing = True
    return app.test_client()


def _auth_headers():
    return {"X-Aura-Internal-Secret": SECRET}


def _insert_outbox_row(get_conn, entity_id=None, event_type="create"):
    entity_id = entity_id or str(uuid.uuid4())
    payload = {"id": entity_id, "company_id": 1, "name": "Widgets", "description": ""}
    conn = get_conn()
    conn.execute(
        "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), "category", entity_id, event_type, json.dumps(payload), "2026-08-06T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return entity_id, payload


# ── auth ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,method", [
    ("/api/sync/_internal/outbox", "GET"),
    ("/api/sync/_internal/outbox/ack", "POST"),
    ("/api/sync/_internal/cursor", "GET"),
    ("/api/sync/_internal/pull-apply", "POST"),
])
def test_every_internal_route_rejects_missing_secret(client, path, method):
    resp = client.open(path, method=method, json={})
    assert resp.status_code == 403
    assert resp.get_json()["reason_code"] == "INVALID_REQUEST"


def test_wrong_secret_is_also_rejected(client):
    resp = client.get("/api/sync/_internal/cursor", headers={"X-Aura-Internal-Secret": "wrong"})
    assert resp.status_code == 403


# ── outbox / ack ─────────────────────────────────────────────────────────

def test_outbox_returns_installation_id_and_pending_rows(client, get_conn):
    _insert_outbox_row(get_conn, entity_id="cat-1", event_type="create")
    resp = client.get("/api/sync/_internal/outbox", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["installation_id"] == "inst-123"
    assert len(body["events"]) == 1
    assert body["events"][0]["entity_id"] == "cat-1"
    assert body["events"][0]["payload"]["name"] == "Widgets"


def test_outbox_ack_deletes_only_the_given_ids(client, get_conn):
    id_a, _ = _insert_outbox_row(get_conn, entity_id="cat-a")
    id_b, _ = _insert_outbox_row(get_conn, entity_id="cat-b")
    rows = client.get("/api/sync/_internal/outbox", headers=_auth_headers()).get_json()["events"]
    row_a_id = next(r["id"] for r in rows if r["entity_id"] == "cat-a")

    resp = client.post("/api/sync/_internal/outbox/ack", headers=_auth_headers(), json={"ids": [row_a_id]})
    assert resp.status_code == 200
    assert resp.get_json() == {"result": "SUCCESS"}

    remaining = client.get("/api/sync/_internal/outbox", headers=_auth_headers()).get_json()["events"]
    assert len(remaining) == 1
    assert remaining[0]["entity_id"] == "cat-b"


def test_outbox_ack_rejects_non_list_ids(client):
    resp = client.post("/api/sync/_internal/outbox/ack", headers=_auth_headers(), json={"ids": "not-a-list"})
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INVALID_REQUEST"


# ── cursor / pull-apply ──────────────────────────────────────────────────

def test_cursor_defaults_to_zero(client):
    resp = client.get("/api/sync/_internal/cursor", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.get_json() == {"installation_id": "inst-123", "since": 0}


def test_pull_apply_upserts_category_and_advances_cursor(client, get_conn):
    result = {
        "events": [{
            "entity_type": "category",
            "event_type": "create",
            "payload": {"id": "cat-9", "company_id": 1, "name": "Pulled", "description": "from owner"},
        }],
        "cursor": 7,
    }
    resp = client.post("/api/sync/_internal/pull-apply", headers=_auth_headers(), json=result)
    assert resp.status_code == 200
    assert resp.get_json() == {"result": "SUCCESS"}

    conn = get_conn()
    row = conn.execute("SELECT * FROM categories WHERE id='cat-9'").fetchone()
    cursor = conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()
    conn.close()
    assert row["name"] == "Pulled"
    assert cursor["last_seq"] == 7

    cursor_resp = client.get("/api/sync/_internal/cursor", headers=_auth_headers())
    assert cursor_resp.get_json()["since"] == 7


def test_pull_apply_rejects_body_without_cursor(client):
    resp = client.post("/api/sync/_internal/pull-apply", headers=_auth_headers(), json={"events": []})
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INVALID_REQUEST"


def test_pull_apply_failure_leaves_cursor_untouched(client, get_conn, monkeypatch):
    import commercial_runtime.sync.sync_service as sync_service_module

    def _boom(self, conn, ev):
        raise RuntimeError("malformed event")

    monkeypatch.setattr(sync_service_module.SyncService, "_apply_event", _boom)

    resp = client.post("/api/sync/_internal/pull-apply", headers=_auth_headers(), json={
        "events": [{"entity_type": "category", "event_type": "create", "payload": {}}],
        "cursor": 42,
    })
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "APPLY_FAILED"

    conn = get_conn()
    cursor = conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()
    conn.close()
    assert cursor["last_seq"] == 0
