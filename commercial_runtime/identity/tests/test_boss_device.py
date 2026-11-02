"""Boss device (owner 2026-11-02): the role chooser's lean, and "exactly one boss" on hand-over.

"By default the mobile version is the Admin (boss device). If he wants to change, he must have
options to change and choose which device is the Admin."

What is pinned here, and the way each guard was mutation-proved (see the commit message):

  1. role_suggestion(): a phone with no boss yet leans ADMIN, a Windows computer leans CASHIER, and
     ANY device whose registry already names ANOTHER device as the boss leans CASHIER and names it.
     The lean is read-only and never a permission: claim-admin / transfer keep their own guards.
  2. A hand-over leaves exactly one boss, in both directions the platform rule allows (phone -> PC
     when a PC has joined; phone <-> phone in a phone-only shop), and the direction the rule refuses
     (PC -> phone once a PC exists) also leaves exactly one boss -- the old one.
  3. A signed-in user who is not the company admin cannot take or hand over the boss role, and the
     refused attempt changes nothing.

The allow-half and the deny-half are both asserted for every guard.
"""
import sqlite3

import pytest
from flask import Flask

from commercial_runtime.identity import (
    device_context, device_registry, device_routes, mt_auth, onboarding_routes as orr,
)
from commercial_runtime.identity.device_registry import (
    apply_identity_device_schema, transfer_admin_device_with_assignment, upsert_local_device,
)
from commercial_runtime.identity.device_routes import device_bp
from commercial_runtime.identity.device_sync_schema import apply_device_sync_schema

CO = "company-1"


@pytest.fixture(autouse=True)
def _reset_process_cache():
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
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, uid TEXT, status TEXT NOT NULL DEFAULT 'active',"
                 " session_version INTEGER NOT NULL DEFAULT 1)")
    conn.execute("CREATE TABLE sync_outbox (id TEXT PRIMARY KEY, entity_type TEXT, entity_id TEXT,"
                 " event_type TEXT, payload TEXT, created_at TEXT)")
    apply_identity_device_schema(conn)
    apply_device_sync_schema(conn)
    # a founded shop (claim fails closed without a founding ADMIN_CREATED row)
    conn.execute("CREATE TABLE audit_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, company_id TEXT, action TEXT)")
    conn.execute("INSERT INTO audit_logs (company_id, action) VALUES (?, 'ADMIN_CREATED')", (CO,))
    conn.commit()
    conn.close()
    return path


def _connect(db_path):
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


@pytest.fixture
def conn(db_path):
    c = _connect(db_path)
    yield c
    c.close()


@pytest.fixture
def client(db_path, monkeypatch):
    monkeypatch.setattr(device_routes, "get_conn", lambda: _connect(db_path))
    monkeypatch.setattr(mt_auth, "REGISTRY_DB", str(db_path))
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.testing = True
    app.register_blueprint(device_bp)
    return app.test_client()


def _login(client, db_path, role="admin", user_id="u1"):
    c = _connect(db_path)
    c.execute("INSERT OR IGNORE INTO users (id, status) VALUES (?, 'active')", (user_id,))
    c.commit()
    c.close()
    with client.session_transaction() as s:
        s["mt_user_id"] = user_id
        s["company_id"] = CO
        s["mt_role"] = role
        s["mt_session_version"] = 1


def _bosses(conn):
    """Every device flagged boss for the shop -- the thing that must be exactly one."""
    return sorted(r[0] for r in conn.execute(
        "SELECT id FROM devices WHERE company_id=? AND is_admin_device=1", (CO,)).fetchall())


def _assigned(conn):
    row = device_registry.get_assignment(conn, CO)
    return row["device_id"] if row else None


# ── 1. the chooser's lean ───────────────────────────────────────────────────────

@pytest.fixture
def lean(conn, db_path, monkeypatch):
    """Drive onboarding_routes.role_suggestion() against this registry, as `me` on `platform`."""
    state = {"platform": "ANDROID", "me": "me-device"}
    monkeypatch.setattr(orr, "get_conn", lambda: _connect(db_path))
    monkeypatch.setattr(orr, "_read_config", lambda: {"company_id": CO})
    monkeypatch.setattr(orr, "peek_local_device_uuid", lambda: state["me"])
    monkeypatch.setattr(orr._device_context, "detect_platform", lambda: state["platform"])
    state["suggest"] = orr.role_suggestion
    return state


def test_a_phone_with_no_boss_yet_leans_to_boss(lean):
    s = lean["suggest"]()
    assert (s["default"], s["boss_exists"]) == ("ADMIN", False)


def test_a_windows_computer_with_no_boss_yet_leans_to_cashier(lean):
    lean["platform"] = "WINDOWS"
    s = lean["suggest"]()
    assert (s["default"], s["boss_exists"]) == ("CASHIER", False)


def test_a_second_device_leans_to_cashier_and_names_the_boss_whatever_its_platform(conn, lean):
    upsert_local_device(conn, "front-pc", CO, platform="WINDOWS", device_label="Front desk PC")
    conn.commit()
    device_registry.claim_admin_device_with_assignment(conn, CO, "front-pc")
    for platform in ("ANDROID", "WINDOWS"):
        lean["platform"] = platform
        s = lean["suggest"]()
        assert s["default"] == "CASHIER", platform
        assert s["boss_exists"] is True
        assert s["boss_device_name"] == "Front desk PC"
        assert s["boss_device_platform"] == "WINDOWS"


def test_an_unnamed_boss_is_reported_nameless_with_its_platform(conn, lean):
    upsert_local_device(conn, "boss-phone", CO, platform="ANDROID")
    conn.commit()
    device_registry.claim_admin_device_with_assignment(conn, CO, "boss-phone")
    s = lean["suggest"]()
    assert (s["boss_device_name"], s["boss_device_platform"]) == ("", "ANDROID")


def test_this_device_being_the_boss_is_not_a_boss_elsewhere(conn, lean):
    """Allow-half of the 'boss exists' check: the assignment naming THIS device must not push
    this device to cashier (a founder reloading the role step is still a boss candidate)."""
    upsert_local_device(conn, "me-device", CO, platform="ANDROID")
    conn.commit()
    device_registry.claim_admin_device_with_assignment(conn, CO, "me-device")
    s = lean["suggest"]()
    assert (s["default"], s["boss_exists"]) == ("ADMIN", False)


def test_a_removed_boss_no_longer_counts(conn, lean):
    upsert_local_device(conn, "dead-pc", CO, platform="WINDOWS")
    conn.commit()
    device_registry.claim_admin_device_with_assignment(conn, CO, "dead-pc")
    conn.execute("UPDATE devices SET status='revoked' WHERE id='dead-pc'")
    conn.commit()
    assert lean["suggest"]()["boss_exists"] is False


def test_the_suggestion_route_serves_it_and_writes_nothing(conn, lean, db_path):
    upsert_local_device(conn, "front-pc", CO, platform="WINDOWS")
    conn.commit()
    device_registry.claim_admin_device_with_assignment(conn, CO, "front-pc")
    before = [tuple(r) for r in conn.execute("SELECT * FROM admin_device_assignment")]
    app = Flask(__name__)
    app.register_blueprint(orr.onboarding_bp)
    body = app.test_client().get("/api/onboarding/role-suggestion").get_json()
    assert body["suggestion"]["default"] == "CASHIER" and body["suggestion"]["boss_exists"] is True
    assert [tuple(r) for r in conn.execute("SELECT * FROM admin_device_assignment")] == before


# ── 2. a hand-over leaves exactly one boss ───────────────────────────────────────

def test_phone_hands_the_boss_role_to_a_pc_that_joined_and_exactly_one_boss_remains(
        app_data, client, db_path, conn, monkeypatch):
    # direction 1: the PHONE is the boss (founded the shop), a PC joins, the phone hands over.
    monkeypatch.setattr(device_context, "detect_platform", lambda: "ANDROID")
    _login(client, db_path)
    assert client.post("/api/devices/me/claim-admin").status_code == 200
    phone = device_context.peek_local_device_uuid()
    upsert_local_device(conn, "the-pc", CO, platform="WINDOWS")
    conn.commit()
    assert _bosses(conn) == [phone] and _assigned(conn) == phone

    r = client.post("/api/devices/the-pc/admin")

    assert r.status_code == 200, r.get_json()
    assert _bosses(conn) == ["the-pc"], "exactly ONE boss, and it is the PC"
    assert _assigned(conn) == "the-pc"
    assert device_registry.get_assignment(conn, CO)["epoch"] == 2
    # the phone really gave it up: asking again from the phone is now refused
    again = client.post("/api/devices/the-pc/admin")
    assert again.status_code == 403 and again.get_json()["code"] == "NOT_ADMIN_DEVICE"


def test_pc_cannot_hand_the_boss_role_back_to_a_phone_once_a_pc_exists_and_the_old_boss_stays(conn):
    # direction 2 (the rule's deny-half): PC -> phone is refused, nothing moves.
    upsert_local_device(conn, "the-pc", CO, platform="WINDOWS")
    upsert_local_device(conn, "the-phone", CO, platform="ANDROID")
    conn.commit()
    device_registry.claim_admin_device_with_assignment(conn, CO, "the-pc")
    with pytest.raises(ValueError):
        transfer_admin_device_with_assignment(conn, CO, "the-phone")
    conn.rollback()
    assert _bosses(conn) == ["the-pc"] and _assigned(conn) == "the-pc"
    assert device_registry.get_assignment(conn, CO)["epoch"] == 1


def test_in_a_phone_only_shop_the_role_moves_both_ways_with_exactly_one_boss_each_time(conn):
    upsert_local_device(conn, "phone-a", CO, platform="ANDROID")
    upsert_local_device(conn, "phone-b", CO, platform="ANDROID")
    conn.commit()
    device_registry.claim_admin_device_with_assignment(conn, CO, "phone-a")
    assert _bosses(conn) == ["phone-a"]
    transfer_admin_device_with_assignment(conn, CO, "phone-b")
    assert _bosses(conn) == ["phone-b"] and _assigned(conn) == "phone-b"
    transfer_admin_device_with_assignment(conn, CO, "phone-a")
    assert _bosses(conn) == ["phone-a"] and _assigned(conn) == "phone-a"
    assert device_registry.get_assignment(conn, CO)["epoch"] == 3


def test_a_second_claim_cannot_make_a_second_boss(app_data, client, db_path, conn, monkeypatch):
    """Two devices that both picked 'boss': the one whose claim lands second is told no and
    the shop still has exactly one."""
    monkeypatch.setattr(device_context, "detect_platform", lambda: "WINDOWS")
    upsert_local_device(conn, "other-pc", CO, platform="WINDOWS")
    conn.commit()
    device_registry.claim_admin_device_with_assignment(conn, CO, "other-pc")
    _login(client, db_path)

    r = client.post("/api/devices/me/claim-admin")

    assert r.status_code == 409 and r.get_json()["code"] == "ADMIN_DEVICE_ALREADY_CLAIMED"
    assert _bosses(conn) == ["other-pc"]


# ── 3. only the company admin can take or hand over the boss role ───────────────────

def test_a_cashier_cannot_hand_over_take_over_or_claim_and_nothing_moves(
        app_data, client, db_path, conn, monkeypatch):
    monkeypatch.setattr(device_context, "detect_platform", lambda: "WINDOWS")
    # an admin makes this device boss first, then a CASHIER sits at the same till
    _login(client, db_path, role="admin")
    assert client.post("/api/devices/me/claim-admin").status_code == 200
    me = device_context.peek_local_device_uuid()
    upsert_local_device(conn, "other-pc", CO, platform="WINDOWS")
    conn.commit()
    _login(client, db_path, role="cashier", user_id="u2")

    transfer = client.post("/api/devices/other-pc/admin")
    assert transfer.status_code == 403 and transfer.get_json()["code"] == "ADMIN_ROLE_REQUIRED"
    take = client.post("/api/devices/me/take-over", json={"password": "x", "confirm": "TAKE OVER"})
    assert take.status_code == 403 and take.get_json()["code"] == "ADMIN_ROLE_REQUIRED"
    assert client.post("/api/devices/me/claim-admin").status_code == 403
    assert _bosses(conn) == [me] and _assigned(conn) == me

    # allow-half: the same request from the company admin is accepted
    _login(client, db_path, role="admin")
    assert client.post("/api/devices/other-pc/admin").status_code == 200
    assert _bosses(conn) == ["other-pc"]
