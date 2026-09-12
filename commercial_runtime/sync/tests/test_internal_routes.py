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
-- launch-readiness Phase 6 stage 6a-i (2026-08-26 follow-up): row_version/
-- updated_at_utc added -- _apply_event's category branch now writes both
-- columns (carrying the sender's row_version through, so two devices'
-- counters converge instead of silently diverging), and this hand-built
-- minimal fixture predates that.
CREATE TABLE categories (
    id TEXT PRIMARY KEY,
    company_id INTEGER DEFAULT 1,
    name TEXT NOT NULL,
    description TEXT,
    row_version INTEGER NOT NULL DEFAULT 1,
    updated_at_utc TEXT,
    -- launch-readiness Phase 6 stage 6b-ii: the real schema has carried this
    -- since v13; the category apply branch now reads and writes it (a
    -- tombstone, and an import resurrection clearing one), so a fixture
    -- without it fails on the column, not on anything this file tests.
    -- Column added to match the real schema; no assertion changed.
    deleted_at_utc TEXT
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
-- Phase 5 (money-moving sync): apply_pull_result() now unconditionally
-- checks this table (SyncService._has_quarantined_events) on every pull, so
-- it must exist even for a route test that never touches a money entity --
-- see products/retail/backend/database/schema.py's own CREATE TABLE for the
-- full reasoning.
CREATE TABLE sync_apply_quarantine (
    entity_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    reason TEXT NOT NULL,
    detail TEXT,
    quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entity_id, event_type)
);
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
    # client_factory never used by these routes. local_company_id_provider
    # is this (fake) receiving device's own company_id -- see
    # test_pull_apply_stamps_the_receiving_devices_own_company_id below for
    # why this must never come from a pulled event's own payload.
    sync_service = SyncService(None, get_conn, lambda: "receiving-company")
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


def test_pull_apply_stamps_the_receiving_devices_own_company_id_not_the_payloads(client, get_conn):
    """Same cross-device company_id bug covered in test_sync_service.py,
    proven through the real route Android's Kotlin SyncCoordinator actually
    calls: a pulled event's payload carries device A's own company_id
    ("company-A"); this fixture's SyncService is wired with
    local_company_id_provider returning "receiving-company" (this device's
    own id) -- the applied row must be stamped "receiving-company", never
    "company-A"."""
    result = {
        "events": [{
            "entity_type": "category", "event_type": "create",
            "payload": {"id": "cat-cross-device", "company_id": "company-A", "name": "From A", "description": ""},
        }],
        "cursor": 1,
    }
    resp = client.post("/api/sync/_internal/pull-apply", headers=_auth_headers(), json=result)
    assert resp.status_code == 200

    conn = get_conn()
    row = conn.execute("SELECT * FROM categories WHERE id='cat-cross-device'").fetchone()
    conn.close()
    assert row["company_id"] == "receiving-company"


def test_pull_apply_rejects_body_without_cursor(client):
    resp = client.post("/api/sync/_internal/pull-apply", headers=_auth_headers(), json={"events": []})
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INVALID_REQUEST"


def test_pull_apply_failure_leaves_cursor_untouched(client, get_conn, monkeypatch):
    import commercial_runtime.sync.sync_service as sync_service_module

    def _boom(self, conn, ev, local_company_id=None):
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


# ── blueprint_name / url_prefix parameterization (sync backend follow-up,
# Android registry-stream wiring) ───────────────────────────────────────────
#
# `make_sync_internal_blueprint` gained these two keyword-only parameters so
# products/retail/backend/app.py's ANDROID branch can register a SECOND
# internal blueprint (over registry.db, REGISTRY_SYNC_ENTITY_TYPES) alongside
# the original retail-stream one this whole file already exercises via the
# `client` fixture above -- see commercial_runtime/sync/tests/
# test_android_registry_stream.py for the full app.py wiring-level proof.
# The two tests below are the narrower, direct proof of the parameterization
# mechanism itself: that a caller-supplied name/prefix is actually honored,
# and that two independently-parameterized blueprints can coexist on one
# Flask app (the whole reason this parameter exists at all -- Flask refuses
# a duplicate blueprint name or a duplicate URL prefix outright).

def test_custom_blueprint_name_and_prefix_are_honored():
    """Catches: `blueprint_name`/`url_prefix` accepted but silently ignored
    (e.g. a leftover hardcoded `Blueprint("sync_internal", ...)` in the
    function body) -- would pass every other test in this file (all of which
    use the defaults) while still breaking Android's second registration."""
    sync_service = SyncService(None, lambda: None, lambda: "irrelevant")
    bp = make_sync_internal_blueprint(
        sync_service=sync_service,
        get_conn=lambda: None,
        state_repository=FakeStateRepository(),
        shared_secret=SECRET,
        blueprint_name="registry_sync_internal",
        url_prefix="/api/registry-sync",
    )
    assert bp.name == "registry_sync_internal"
    assert bp.url_prefix == "/api/registry-sync"


def test_two_independently_parameterized_blueprints_coexist_on_one_flask_app(get_conn, tmp_path):
    """The actual reason blueprint_name/url_prefix exist: Android registers
    TWO of these blueprints (one per SyncService/database) on the SAME Flask
    app. Registering the default-named one twice, or two blueprints sharing
    a URL prefix, raises at `register_blueprint()` time -- so this test
    would fail LOUDLY (an exception during app construction, not a quiet
    wrong answer) if the parameterization did not actually prevent the
    collision it exists to prevent. Both instances point at the SAME sqlite
    file here (irrelevant to what this test proves -- it proves ROUTING
    coexistence, not database isolation, which test_android_registry_stream.
    py's own tests cover against real retail.db/registry.db)."""
    second_db_path = tmp_path / "second.db"
    second_conn = sqlite3.connect(str(second_db_path))
    second_conn.executescript(_SCHEMA)
    second_conn.commit()
    second_conn.close()

    def _second_get_conn():
        c = sqlite3.connect(str(second_db_path), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    app = Flask(__name__)
    app.register_blueprint(make_sync_internal_blueprint(
        sync_service=SyncService(None, get_conn, lambda: "company-a"),
        get_conn=get_conn,
        state_repository=FakeStateRepository(owner_installation_id="inst-first"),
        shared_secret=SECRET,
    ))
    app.register_blueprint(make_sync_internal_blueprint(
        sync_service=SyncService(None, _second_get_conn, lambda: "company-b"),
        get_conn=_second_get_conn,
        state_repository=FakeStateRepository(owner_installation_id="inst-second"),
        shared_secret=SECRET,
        blueprint_name="registry_sync_internal",
        url_prefix="/api/registry-sync",
    ))
    app.testing = True
    two_bp_client = app.test_client()

    first = two_bp_client.get("/api/sync/_internal/cursor", headers=_auth_headers())
    second = two_bp_client.get("/api/registry-sync/_internal/cursor", headers=_auth_headers())
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.get_json()["installation_id"] == "inst-first"
    assert second.get_json()["installation_id"] == "inst-second"

    # Still secret-guarded independently on both prefixes.
    assert two_bp_client.get("/api/sync/_internal/cursor").status_code == 403
    assert two_bp_client.get("/api/registry-sync/_internal/cursor").status_code == 403
