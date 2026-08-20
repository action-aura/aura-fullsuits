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
    # columns mt_login_required's session check actually reads. That is two
    # columns, not one, since the decorator started enforcing session_version
    # (registry v3 / multi-device Phase 1): the real table has always had the
    # column with DEFAULT 1, so mirroring it here keeps this fixture a
    # faithful stand-in rather than a smaller world where the check cannot
    # fire. `_session()` below stamps the matching mt_session_version, so a
    # session built by `_login` below is current, exactly like a real one.
    conn.execute(
        "CREATE TABLE users ("
        "  id TEXT PRIMARY KEY,"
        "  status TEXT NOT NULL DEFAULT 'active',"
        "  session_version INTEGER NOT NULL DEFAULT 1"
        ")"
    )
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


def _login(client, user_id, company_id, role="admin", session_version=1):
    """Mirrors the fields `mt_auth.create_session` sets that these routes and
    their decorator actually read. `mt_session_version` is stamped because a
    real login stamps it -- a fake session that omitted it would be leaning on
    the decorator's missing-value fallback instead of reproducing what the
    product does."""
    with client.session_transaction() as s:
        s["mt_user_id"] = user_id
        s["company_id"] = company_id
        s["mt_role"] = role
        s["mt_session_version"] = session_version


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
    # 2026-08-20: this is the FIRST device ever resolved for company-1 and
    # it is NOT admin. The 2026-08-17 revision of this test asserted the
    # opposite ("the first device a company ever resolves becomes its admin
    # device"), which is the behaviour that made resolve_local_device() -- a
    # read path that retail_api.py's audit-log authorization check calls --
    # silently grant admin to whichever device asked first. Resolving is a
    # check-in; claiming is a decision (see the claim tests below).
    assert device["is_admin_device"] is False, "merely resolving a device must never make it admin"
    # ...but the fresh-install state is a dead end unless the UI is told a
    # claim is available, which is what this field exists for.
    assert body["can_claim_admin"] is True


def test_my_device_second_device_for_same_company_is_not_admin(app_data, client, db_path):
    """The auto-promotion above must not just default every device to
    admin -- once a company already has one, a second device resolving
    for the first time stays a plain (non-admin) device."""
    _insert_user(db_path, "user-1")
    _login(client, "user-1", "company-1")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    upsert_local_device(conn, "already-admin-device", "company-1")
    from commercial_runtime.identity.device_registry import set_admin_device
    set_admin_device(conn, "company-1", "already-admin-device")
    conn.close()

    r = client.get("/api/devices/me")

    assert r.status_code == 200
    device = r.get_json()["device"]
    assert device["id"] != "already-admin-device"
    assert device["is_admin_device"] is False


def test_my_device_second_device_cannot_claim_so_can_claim_admin_is_false(app_data, client, db_path):
    """`can_claim_admin` must track the same rule the claim route enforces,
    not just "am I admin" -- otherwise the UI offers a button that always
    409s."""
    _insert_user(db_path, "user-1")
    _login(client, "user-1", "company-1")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    upsert_local_device(conn, "already-admin-device", "company-1")
    from commercial_runtime.identity.device_registry import set_admin_device
    set_admin_device(conn, "company-1", "already-admin-device")
    conn.close()

    body = client.get("/api/devices/me").get_json()
    assert body["device"]["is_admin_device"] is False
    assert body["can_claim_admin"] is False


def test_my_device_non_admin_role_cannot_claim(app_data, client, db_path):
    _insert_user(db_path, "user-1")
    _login(client, "user-1", "company-1", role="cashier")

    body = client.get("/api/devices/me").get_json()
    assert body["device"]["is_admin_device"] is False
    assert body["can_claim_admin"] is False, "only a company admin decides the admin device"


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


# ── POST /api/devices/me/claim-admin ─────────────────────────────────────
# The setter that makes `is_admin_device` a flag something can actually
# hold. Before 2026-08-20 the only writer was an auto-promotion inside
# resolve_local_device() -- i.e. inside the read path retail_api.py's
# audit-log authorization check calls -- so the check granted the privilege
# it was checking for. These tests pin the replacement: the flag moves only
# on a deliberate, authenticated, admin-role POST.

def test_claim_admin_requires_auth(client):
    r = client.post("/api/devices/me/claim-admin")
    assert r.status_code == 401


def test_claim_admin_requires_admin_role(app_data, client, db_path):
    _insert_user(db_path, "user-1")
    _login(client, "user-1", "company-1", role="cashier")

    r = client.post("/api/devices/me/claim-admin")

    assert r.status_code == 403
    assert r.get_json()["code"] == "ADMIN_ROLE_REQUIRED"
    conn = sqlite3.connect(str(db_path))
    assert conn.execute("SELECT COUNT(*) FROM devices WHERE is_admin_device=1").fetchone()[0] == 0
    conn.close()


def test_claim_admin_refuses_a_demo_session(app_data, client, db_path):
    """mt_login_required waves demo sessions through with no user at all --
    that must not be able to write a permanent privilege into registry.db
    that outlives the demo."""
    with client.session_transaction() as s:
        s["is_demo_mode"] = True
        s["company_id"] = "company-1"
        s["mt_role"] = "admin"

    r = client.post("/api/devices/me/claim-admin")

    assert r.status_code == 403
    assert r.get_json()["code"] == "ADMIN_ROLE_REQUIRED"


def test_fresh_install_admin_claims_this_device_and_it_becomes_admin(app_data, client, db_path):
    """The fresh-install path end to end: no `devices` row at all -> not
    admin -> claim -> admin, entirely through real HTTP."""
    _insert_user(db_path, "user-1")
    _login(client, "user-1", "company-1")

    before = client.get("/api/devices/me").get_json()
    assert before["device"]["is_admin_device"] is False
    assert before["can_claim_admin"] is True

    r = client.post("/api/devices/me/claim-admin")

    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["success"] is True
    assert body["device"]["is_admin_device"] is True
    assert isinstance(body["device"]["is_admin_device"], bool)

    # And it must still read back as admin on the NEXT request -- the
    # process-level cache in device_context holds the pre-claim row, so a
    # missing invalidate_cache() would report the claim as a no-op here.
    after = client.get("/api/devices/me").get_json()
    assert after["device"]["is_admin_device"] is True
    assert after["can_claim_admin"] is False


def test_claiming_twice_from_the_same_device_is_idempotent(app_data, client, db_path):
    _insert_user(db_path, "user-1")
    _login(client, "user-1", "company-1")

    first = client.post("/api/devices/me/claim-admin")
    second = client.post("/api/devices/me/claim-admin")

    assert first.status_code == 200
    assert second.status_code == 200, second.get_json()
    assert second.get_json()["device"]["is_admin_device"] is True
    conn = sqlite3.connect(str(db_path))
    assert conn.execute(
        "SELECT COUNT(*) FROM devices WHERE company_id='company-1' AND is_admin_device=1"
    ).fetchone()[0] == 1
    conn.close()


def test_claim_admin_409s_when_another_device_already_holds_it(app_data, client, db_path):
    _insert_user(db_path, "user-1")
    _login(client, "user-1", "company-1")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    upsert_local_device(conn, "the-front-till", "company-1", device_label="Front Till")
    from commercial_runtime.identity.device_registry import set_admin_device
    set_admin_device(conn, "company-1", "the-front-till")
    conn.close()

    r = client.post("/api/devices/me/claim-admin")

    assert r.status_code == 409
    body = r.get_json()
    assert body["code"] == "ADMIN_DEVICE_ALREADY_CLAIMED"
    # Naming the holder is what makes the refusal actionable.
    assert body["admin_device"]["id"] == "the-front-till"
    assert body["admin_device"]["device_label"] == "Front Till"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    holders = conn.execute(
        "SELECT id FROM devices WHERE company_id='company-1' AND is_admin_device=1"
    ).fetchall()
    conn.close()
    assert [h["id"] for h in holders] == ["the-front-till"], "a refused claim must not move the flag"


def test_claim_admin_refuses_a_revoked_local_device(app_data, client, db_path):
    _insert_user(db_path, "user-1")
    _login(client, "user-1", "company-1")

    from commercial_runtime.identity.device_context import local_device_uuid
    from commercial_runtime.identity.device_registry import revoke_device
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    device_id = local_device_uuid()
    upsert_local_device(conn, device_id, "company-1")
    revoke_device(conn, device_id)
    conn.close()
    device_context._cached_device = None

    r = client.post("/api/devices/me/claim-admin")

    assert r.status_code == 409
    assert r.get_json()["code"] == "DEVICE_NOT_ELIGIBLE"


# ── POST /api/devices/<device_id>/admin (transfer) ───────────────────────

def test_transfer_admin_requires_the_request_to_come_from_the_current_admin_device(app_data, client, db_path):
    """The whole security value of the transfer route: a company admin on a
    NON-admin device cannot take the flag. Without this, the claim route's
    409 above would be decorative -- anyone refused there could just call
    this instead."""
    _insert_user(db_path, "user-1")
    _login(client, "user-1", "company-1")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    upsert_local_device(conn, "the-front-till", "company-1")
    upsert_local_device(conn, "the-back-office", "company-1")
    from commercial_runtime.identity.device_registry import set_admin_device
    set_admin_device(conn, "company-1", "the-front-till")
    conn.close()

    r = client.post("/api/devices/the-back-office/admin")

    assert r.status_code == 403
    assert r.get_json()["code"] == "NOT_ADMIN_DEVICE"


def test_transfer_admin_moves_the_flag_when_asked_from_the_admin_device(app_data, client, db_path):
    _insert_user(db_path, "user-1")
    _login(client, "user-1", "company-1")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    upsert_local_device(conn, "the-new-till", "company-1")
    conn.close()

    assert client.post("/api/devices/me/claim-admin").status_code == 200

    r = client.post("/api/devices/the-new-till/admin")

    assert r.status_code == 200, r.get_json()
    assert r.get_json()["device"]["id"] == "the-new-till"
    assert r.get_json()["device"]["is_admin_device"] is True
    # This device gave the role away, so it is no longer admin -- and the
    # cached row must reflect that too.
    assert client.get("/api/devices/me").get_json()["device"]["is_admin_device"] is False


def test_transfer_admin_404s_for_another_companys_device(app_data, client, db_path):
    _insert_user(db_path, "user-1")
    _login(client, "user-1", "company-1")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    upsert_local_device(conn, "someone-elses-till", "company-2")
    conn.close()

    assert client.post("/api/devices/me/claim-admin").status_code == 200

    r = client.post("/api/devices/someone-elses-till/admin")

    assert r.status_code == 404
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT is_admin_device FROM devices WHERE id='someone-elses-till'").fetchone()
    conn.close()
    assert row["is_admin_device"] == 0, "a cross-tenant transfer must not touch the other company's row"
