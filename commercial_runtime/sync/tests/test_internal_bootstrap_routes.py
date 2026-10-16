"""SYNC-BOOT Phase 3, Android seam: the `/_internal/bootstrap/*` routes Kotlin uses to hand a
DECRYPTED copy of the shop to the embedded Python backend, and the `include_own` flag it reads
from `/_internal/cursor`. Driven through a real Flask test client, like test_internal_routes.py.

The product's stage function is a recording stub here (the real one is
tests of installer.stage in retail_bootstrap_producer_installer_test.py); what this file pins is
the HTTP contract Kotlin depends on: secret required, a refusal is a 409 with its reason code and
stages nothing, the one-shot flag is only reported when armed, and a blueprint built WITHOUT the
seam (the registry stream, every older caller) exposes none of it.
"""
import sqlite3
from types import SimpleNamespace

import pytest
from flask import Flask

from commercial_runtime.sync.bootstrap.errors import BootstrapError, COMPANY_MISMATCH
from commercial_runtime.sync.internal_routes import make_sync_internal_blueprint
from commercial_runtime.sync.sync_service import SyncService

SECRET = "s3cret"


class _Repo:
    def load(self):
        return SimpleNamespace(owner_installation_id="inst-1")


def _conn_factory(path):
    def get_conn():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c
    return get_conn


@pytest.fixture
def parts(tmp_path):
    db = str(tmp_path / "t.db")
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE sync_cursor (id INTEGER PRIMARY KEY CHECK (id=1), last_seq INTEGER NOT NULL DEFAULT 0);"
                    "INSERT INTO sync_cursor (id,last_seq) VALUES (1,0);"
                    "CREATE TABLE sync_outbox (id TEXT PRIMARY KEY, entity_type TEXT, entity_id TEXT, event_type TEXT, "
                    "payload TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
    c.commit()
    c.close()
    state = SimpleNamespace(staged=[], armed=True, done=0, refuse=False)

    def stage_file(path, relay_url):
        if state.refuse:
            raise BootstrapError(COMPANY_MISMATCH, "another shop")
        state.staged.append((path, relay_url))
        return {"as_of": 41}

    def spent():
        state.done += 1
        state.armed = False

    state.info = {"schema_version": 56, "can_stage": True, "block_reason": None, "pending_staged": False,
                  "install_failures": 0, "needs_support": False, "last_install_failure": None}
    seam = SimpleNamespace(info=lambda: state.info, stage_file=stage_file,
                           include_own_pending=lambda: state.armed, include_own_done=spent,
                           reapply_outbox=lambda svc, conn: None)
    get_conn = _conn_factory(db)
    svc = SyncService(None, get_conn, lambda: None)

    def client(with_seam=True):
        app = Flask(__name__)
        app.register_blueprint(make_sync_internal_blueprint(
            sync_service=svc, get_conn=get_conn, state_repository=_Repo(), shared_secret=SECRET,
            bootstrap=seam if with_seam else None))
        return app.test_client()
    return client, state


H = {"X-Aura-Internal-Secret": SECRET}


def test_every_bootstrap_route_needs_the_internal_secret(parts):
    client, _ = parts
    c = client()
    assert c.get("/api/sync/_internal/bootstrap/info").status_code == 403
    assert c.post("/api/sync/_internal/bootstrap/stage-file", json={"path": "/x"}).status_code == 403
    assert c.post("/api/sync/_internal/bootstrap/include-own-done").status_code == 403


def test_stage_hands_the_bytes_and_relay_to_the_product_and_reports_the_position(parts):
    client, state = parts
    r = client().post("/api/sync/_internal/bootstrap/stage-file",
                      json={"path": "/data/device/bootstrap_download/copy.zip", "relay_url": "https://owner.example"}, headers=H)
    assert r.status_code == 200 and r.get_json() == {"result": "SUCCESS", "as_of": 41}
    assert state.staged == [("/data/device/bootstrap_download/copy.zip", "https://owner.example")]


def test_a_refused_copy_is_a_409_with_its_reason_and_stages_nothing(parts):
    """Both directions of the same route: accepted above, refused here."""
    client, state = parts
    state.refuse = True
    r = client().post("/api/sync/_internal/bootstrap/stage-file", json={"path": "/p"}, headers=H)
    assert r.status_code == 409 and r.get_json()["reason_code"] == COMPANY_MISMATCH
    assert state.staged == []
    assert client().post("/api/sync/_internal/bootstrap/stage-file", json={}, headers=H).status_code == 400


def test_info_reports_what_the_requester_must_know_before_it_downloads(parts):
    """schema, whether a copy can be staged NOW (an open drawer refuses one), whether one is already
    staged, and the install-failure count -- passed through whole, so a changed key shows up here."""
    client, state = parts
    assert client().get("/api/sync/_internal/bootstrap/info", headers=H).get_json() == state.info
    state.info = {**state.info, "can_stage": False, "block_reason": "OPEN_CASH_SESSION: cash_sessions: 1"}
    got = client().get("/api/sync/_internal/bootstrap/info", headers=H).get_json()
    assert got["can_stage"] is False and got["block_reason"].startswith("OPEN_CASH_SESSION")


def test_cursor_reports_include_own_only_while_armed_and_done_spends_it(parts):
    client, state = parts
    c = client()
    assert c.get("/api/sync/_internal/cursor", headers=H).get_json()["include_own"] is True
    assert c.post("/api/sync/_internal/bootstrap/include-own-done", headers=H).status_code == 200
    assert state.done == 1
    assert c.get("/api/sync/_internal/cursor", headers=H).get_json()["include_own"] is False


def test_without_the_seam_nothing_changes(parts):
    client, _ = parts
    c = client(with_seam=False)
    assert c.get("/api/sync/_internal/bootstrap/info", headers=H).status_code == 404
    assert "include_own" not in c.get("/api/sync/_internal/cursor", headers=H).get_json()


# ---------------------------------------------------------------- unpushed outbox after an install (plan 2.4)
def test_cursor_re_applies_the_carried_outbox_once_after_an_install(tmp_path):
    """After a bootstrap install the copy predates this phone's still-unpushed writes. The installer carried
    the outbox rows over; Android's Kotlin loop never calls pull_once, so the FIRST /_internal/cursor call
    re-applies them to the new database -- once. The rows stay in the outbox (they still have to be pushed).
    MUTATION: drop the `bootstrap.reapply_outbox(...)` call in the route -> the category never appears."""
    import json
    import uuid

    from commercial_runtime.sync.bootstrap.hooks import BootstrapHooks
    from commercial_runtime.sync.tests.test_internal_routes import _SCHEMA

    db = str(tmp_path / "t.db")
    c = sqlite3.connect(db)
    c.executescript(_SCHEMA)
    entity_id = str(uuid.uuid4())
    c.execute("INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) VALUES (?,?,?,?,?,?)",
              (str(uuid.uuid4()), "category", entity_id, "create",
               json.dumps({"id": entity_id, "company_id": 1, "name": "Carried", "description": ""}),
               "2026-10-16T00:00:00+00:00"))
    c.commit()
    c.close()
    get_conn = _conn_factory(db)
    svc = SyncService(None, get_conn, lambda: "co")
    pending = {"armed": True, "done": 0}

    def done():
        pending["armed"] = False
        pending["done"] += 1

    svc.set_bootstrap_hooks(BootstrapHooks(reapply_outbox_pending=lambda: pending["armed"], reapply_outbox_done=done))
    seam = SimpleNamespace(
        info=lambda: {}, stage_file=lambda p, r: {}, include_own_pending=lambda: False,
        include_own_done=lambda: None, reapply_outbox=lambda s, conn: s._reapply_own_outbox_once(conn))
    app = Flask(__name__)
    app.register_blueprint(make_sync_internal_blueprint(
        sync_service=svc, get_conn=get_conn, state_repository=_Repo(), shared_secret=SECRET, bootstrap=seam))
    client = app.test_client()

    def categories():
        k = get_conn()
        try:
            return [r["name"] for r in k.execute("SELECT name FROM categories").fetchall()]
        finally:
            k.close()

    assert categories() == []
    assert client.get("/api/sync/_internal/cursor", headers=H).status_code == 200
    assert categories() == ["Carried"] and pending["done"] == 1
    k = get_conn(); k.execute("DELETE FROM categories"); k.commit(); k.close()
    assert client.get("/api/sync/_internal/cursor", headers=H).status_code == 200
    assert categories() == [], "the re-apply must happen once, not on every tick"
    k = get_conn()
    try:
        assert k.execute("SELECT COUNT(*) AS n FROM sync_outbox").fetchone()["n"] == 1     # still to be pushed
    finally:
        k.close()
