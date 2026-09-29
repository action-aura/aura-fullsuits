"""Registry v9 -- the synced "which computer is main" fact (admin_device_assignment).

Covers the pure ordering rule, claim / transfer (epoch + event, one
transaction), the receive side (apply_admin_assignment), the joined-device
refusal (both arms, both directions), the Windows-only rule, and the
label / revoke routes.

WHY THESE ASSERTIONS. The dangerous failures here are silent: two computers
each believing they are main, or a peer that never hears about a hand-over.
So the tests count what was WRITTEN (assignment rows, outbox events, the
flag), not just the HTTP status: a 409 cannot tell "the joined check ran and
refused" from "something else refused" (ENGINEERING.md 1, shape 1), so every
refusal test also asserts the check ran, by reaching a case that ONLY the new
check can produce (JOINED_DEVICE_CANNOT_CLAIM / MAIN_MUST_BE_WINDOWS are codes
no other branch returns).

Run (one file per process):
    python -m pytest commercial_runtime/identity/tests/test_admin_device_assignment.py -q
"""
import json
import sqlite3

import pytest
from flask import Flask

from commercial_runtime.identity import device_context, device_registry, device_routes, mt_auth
from commercial_runtime.identity.device_registry import (
    admin_assignment_wins,
    apply_admin_assignment,
    apply_identity_device_schema,
    claim_admin_device_with_assignment,
    get_assignment,
    is_joined_device,
    revoke_device_synced,
    set_device_label_synced,
    transfer_admin_device_with_assignment,
    upsert_local_device,
)
from commercial_runtime.identity.device_routes import device_bp
from commercial_runtime.identity.device_sync_schema import apply_device_sync_schema

CO = "company-1"


def _new_registry(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, uid TEXT, status TEXT NOT NULL DEFAULT 'active',"
        " session_version INTEGER NOT NULL DEFAULT 1)"
    )
    conn.execute(
        "CREATE TABLE sync_outbox (id TEXT PRIMARY KEY, entity_type TEXT, entity_id TEXT,"
        " event_type TEXT, payload TEXT, created_at TEXT)"
    )
    apply_identity_device_schema(conn)
    apply_device_sync_schema(conn)
    conn.commit()
    return conn


@pytest.fixture
def conn(tmp_path):
    c = _new_registry(tmp_path / "registry.db")
    yield c
    c.close()


def _events(conn, entity_type):
    return [
        (r["event_type"], json.loads(r["payload"]))
        for r in conn.execute(
            "SELECT event_type, payload FROM sync_outbox WHERE entity_type=? ORDER BY rowid",
            (entity_type,),
        ).fetchall()
    ]


def _flagged(conn, company=CO):
    return [r["id"] for r in conn.execute(
        "SELECT id FROM devices WHERE company_id=? AND is_admin_device=1", (company,)).fetchall()]


# ── pure ordering ────────────────────────────────────────────────────────────

def _a(device, epoch, at):
    return {"device_id": device, "epoch": epoch, "assigned_at_utc": at}


def test_no_local_assignment_means_incoming_wins():
    assert admin_assignment_wins(None, _a("d1", 1, "2026-01-01T00:00:00+00:00")) is True


def test_higher_epoch_wins_even_when_it_is_later_and_has_a_bigger_id():
    local = _a("a-dev", 2, "2026-01-01T00:00:00+00:00")
    incoming = _a("z-dev", 3, "2026-09-01T00:00:00+00:00")
    assert admin_assignment_wins(local, incoming) is True
    # ...and the mirror image: a LOWER epoch never wins, whatever else it has.
    assert admin_assignment_wins(incoming, _a("a-dev", 2, "2020-01-01T00:00:00+00:00")) is False


def test_equal_epoch_earlier_time_wins():
    local = _a("d-b", 2, "2026-05-02T00:00:00+00:00")
    earlier = _a("d-z", 2, "2026-05-01T00:00:00+00:00")
    assert admin_assignment_wins(local, earlier) is True
    assert admin_assignment_wins(earlier, local) is False


def test_equal_epoch_and_time_smaller_device_id_wins():
    at = "2026-05-01T00:00:00+00:00"
    assert admin_assignment_wins(_a("d-b", 2, at), _a("d-a", 2, at)) is True
    assert admin_assignment_wins(_a("d-a", 2, at), _a("d-b", 2, at)) is False


def test_identical_assignment_does_not_win():
    x = _a("d-a", 2, "2026-05-01T00:00:00+00:00")
    assert admin_assignment_wins(dict(x), dict(x)) is False


def test_time_compare_is_by_instant_not_text():
    # +03:00 10:00 is 07:00Z, EARLIER than 08:00Z although "10" > "08" as text.
    local = _a("d-b", 2, "2026-05-01T08:00:00+00:00")
    incoming = _a("d-b2", 2, "2026-05-01T10:00:00+03:00")
    assert admin_assignment_wins(local, incoming) is True


# ── claim ────────────────────────────────────────────────────────────────────

def test_first_claim_writes_epoch_one_and_one_create_event(conn):
    upsert_local_device(conn, "d1", CO, platform="WINDOWS")
    row = claim_admin_device_with_assignment(conn, CO, "d1", by_user_uid="u-1")
    assert row["is_admin_device"] == 1
    a = get_assignment(conn, CO)
    assert (a["device_id"], a["epoch"], a["assigned_by_user_uid"]) == ("d1", 1, "u-1")
    evs = _events(conn, "admin_device")
    assert [e[0] for e in evs] == ["create"]
    assert evs[0][1]["device_id"] == "d1" and evs[0][1]["epoch"] == 1


def test_reclaim_by_the_holder_burns_no_epoch_and_emits_nothing(conn):
    upsert_local_device(conn, "d1", CO)
    claim_admin_device_with_assignment(conn, CO, "d1")
    claim_admin_device_with_assignment(conn, CO, "d1")
    assert get_assignment(conn, CO)["epoch"] == 1
    assert len(_events(conn, "admin_device")) == 1


def test_claim_refused_when_a_synced_assignment_names_another_device_that_has_not_arrived(conn):
    """The flag alone would miss this: the assignment arrived, the device row
    did not, so NOBODY is flagged locally. Without the assignment check a
    second main is born."""
    upsert_local_device(conn, "d1", CO)
    apply_admin_assignment(conn, CO, {"device_id": "ghost", "epoch": 4,
                                      "assigned_at_utc": "2026-01-01T00:00:00+00:00"})
    assert _flagged(conn) == []
    assert claim_admin_device_with_assignment(conn, CO, "d1") is None
    assert get_assignment(conn, CO)["device_id"] == "ghost"
    assert _flagged(conn) == []


def test_claim_of_an_inactive_device_raises_and_writes_nothing(conn):
    upsert_local_device(conn, "d1", CO)
    conn.execute("UPDATE devices SET status='revoked' WHERE id='d1'")
    conn.commit()
    with pytest.raises(ValueError):
        claim_admin_device_with_assignment(conn, CO, "d1")
    assert get_assignment(conn, CO) is None
    assert _events(conn, "admin_device") == []


# ── transfer ─────────────────────────────────────────────────────────────────

def test_transfer_moves_flag_bumps_epoch_and_emits_update(conn):
    upsert_local_device(conn, "d1", CO)
    upsert_local_device(conn, "d2", CO)
    claim_admin_device_with_assignment(conn, CO, "d1", by_user_uid="u-1")
    transfer_admin_device_with_assignment(conn, CO, "d2", by_user_uid="u-1", by_device_id="d1")
    assert _flagged(conn) == ["d2"]
    a = get_assignment(conn, CO)
    assert (a["device_id"], a["epoch"], a["assigned_by_device_id"]) == ("d2", 2, "d1")
    assert [e[0] for e in _events(conn, "admin_device")] == ["create", "update"]


def test_transfer_to_the_current_main_is_a_no_op(conn):
    upsert_local_device(conn, "d1", CO)
    claim_admin_device_with_assignment(conn, CO, "d1")
    transfer_admin_device_with_assignment(conn, CO, "d1")
    assert get_assignment(conn, CO)["epoch"] == 1
    assert len(_events(conn, "admin_device")) == 1


def test_transfer_to_another_companys_device_raises(conn):
    upsert_local_device(conn, "d1", CO)
    upsert_local_device(conn, "other", "company-2")
    claim_admin_device_with_assignment(conn, CO, "d1")
    with pytest.raises(ValueError):
        transfer_admin_device_with_assignment(conn, CO, "other")
    assert _flagged(conn) == ["d1"]
    assert get_assignment(conn, CO)["epoch"] == 1


# ── receive side ─────────────────────────────────────────────────────────────

def test_apply_higher_epoch_moves_the_flag_when_the_device_is_known(conn):
    upsert_local_device(conn, "d1", CO)
    upsert_local_device(conn, "d2", CO)
    claim_admin_device_with_assignment(conn, CO, "d1")
    assert apply_admin_assignment(conn, CO, {"device_id": "d2", "epoch": 2,
                                             "assigned_at_utc": "2026-09-01T00:00:00+00:00"}) is True
    conn.commit()
    assert _flagged(conn) == ["d2"]


def test_apply_lower_epoch_changes_nothing(conn):
    upsert_local_device(conn, "d1", CO)
    upsert_local_device(conn, "d2", CO)
    claim_admin_device_with_assignment(conn, CO, "d1")
    transfer_admin_device_with_assignment(conn, CO, "d2")   # epoch 2
    assert apply_admin_assignment(conn, CO, {"device_id": "d1", "epoch": 1,
                                             "assigned_at_utc": "2025-01-01T00:00:00+00:00"}) is False
    # 2026-09-29: was "2020-01-01". apply now refuses a pre-2024 timestamp as
    # implausible (test_fix_round_devices.py pins that refusal), so this old
    # fixture date would never reach the epoch comparison it exists to test.
    # The moved date changes nothing about WHAT is asserted (a lower epoch
    # loses whatever its timestamp says); it just can no longer double as a
    # check that a pre-2024 stamp is tolerated -- deliberately, that is now
    # the opposite rule.
    assert _flagged(conn) == ["d2"] and get_assignment(conn, CO)["epoch"] == 2


def test_apply_never_emits(conn):
    """The receive side must not echo: re-emitting what arrived would ping-pong
    between peers forever."""
    upsert_local_device(conn, "d2", CO)
    apply_admin_assignment(conn, CO, {"device_id": "d2", "epoch": 2,
                                      "assigned_at_utc": "2026-09-01T00:00:00+00:00"})
    assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 0


@pytest.mark.parametrize("bad", [
    {}, {"device_id": "d", "epoch": 0, "assigned_at_utc": "x"},
    {"device_id": "", "epoch": 1, "assigned_at_utc": "x"},
    {"device_id": "d", "epoch": "nope", "assigned_at_utc": "x"},
    {"device_id": "d", "epoch": 1, "assigned_at_utc": ""},
])
def test_apply_malformed_payload_raises_and_stores_nothing(conn, bad):
    with pytest.raises(ValueError):
        apply_admin_assignment(conn, CO, bad)
    assert get_assignment(conn, CO) is None


def test_recompute_does_not_flag_a_revoked_assigned_device(conn):
    # 2026-09-29 (item 1): apply now REFUSES an assignment naming an already
    # revoked device (admin_target_ineligible), so the old way of building this
    # state -- revoke first, then apply -- is no longer reachable through apply.
    # The recompute rule still matters for a device revoked AFTER the fact
    # (a pre-fix store, or a row edited locally), so build that state instead.
    upsert_local_device(conn, "d1", CO)
    apply_admin_assignment(conn, CO, {"device_id": "d1", "epoch": 1,
                                      "assigned_at_utc": "2026-09-01T00:00:00+00:00"})
    assert _flagged(conn) == ["d1"]
    conn.execute("UPDATE devices SET status='revoked' WHERE id='d1'")
    conn.commit()
    device_registry.recompute_admin_flag(conn, CO)
    assert _flagged(conn) == []


def test_apply_refuses_an_assignment_naming_a_revoked_device(conn):
    upsert_local_device(conn, "d1", CO)
    conn.execute("UPDATE devices SET status='revoked' WHERE id='d1'")
    conn.commit()
    with pytest.raises(ValueError, match="admin_target_ineligible"):
        apply_admin_assignment(conn, CO, {"device_id": "d1", "epoch": 1,
                                          "assigned_at_utc": "2026-09-01T00:00:00+00:00"})
    assert get_assignment(conn, CO) is None


# ── label / revoke helpers ───────────────────────────────────────────────────

def test_label_bumps_version_and_emits_but_does_not_touch_last_seen(conn):
    upsert_local_device(conn, "d1", CO, device_label="old")
    before = device_registry.get_device(conn, "d1")
    row = set_device_label_synced(conn, CO, "d1", "Front till")
    assert row["device_label"] == "Front till"
    assert row["row_version"] == before["row_version"] + 1
    assert row["last_seen_at"] == before["last_seen_at"]
    assert [e[0] for e in _events(conn, "device")] == ["update"]


def test_label_of_another_companys_device_is_none_and_silent(conn):
    upsert_local_device(conn, "other", "company-2", device_label="keep")
    assert set_device_label_synced(conn, CO, "other", "hijack") is None
    assert device_registry.get_device(conn, "other")["device_label"] == "keep"
    assert _events(conn, "device") == []


def test_revoke_marks_revoked_emits_once_and_is_idempotent(conn):
    upsert_local_device(conn, "d1", CO)
    upsert_local_device(conn, "d2", CO)
    claim_admin_device_with_assignment(conn, CO, "d1")
    row = revoke_device_synced(conn, CO, "d2")
    assert row["status"] == "revoked"
    again = revoke_device_synced(conn, CO, "d2")
    assert again["row_version"] == row["row_version"]
    assert len(_events(conn, "device")) == 1


def test_revoke_of_the_main_computer_is_refused_and_changes_nothing(conn):
    upsert_local_device(conn, "d1", CO)
    claim_admin_device_with_assignment(conn, CO, "d1")
    with pytest.raises(ValueError):
        revoke_device_synced(conn, CO, "d1")
    row = device_registry.get_device(conn, "d1")
    assert row["status"] == "active" and row["is_admin_device"] == 1


# ── is_joined_device: both arms, both directions ─────────────────────────────

def _with_shop_tables(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS audit_logs (id INTEGER PRIMARY KEY, company_id TEXT, action TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS company_settings (company_id TEXT PRIMARY KEY, country TEXT)")
    conn.commit()


def test_joined_arm_a_assignment_names_another_device(conn):
    upsert_local_device(conn, "me", CO)
    apply_admin_assignment(conn, CO, {"device_id": "boss", "epoch": 1,
                                      "assigned_at_utc": "2026-01-01T00:00:00+00:00"})
    assert is_joined_device(conn, CO, "me") is True
    # allow-half: the SAME assignment does not make the named device "joined".
    assert is_joined_device(conn, CO, "boss") is False


def test_joined_arm_b_no_founding_audit_row_is_joined_whatever_the_settings_say(conn):
    # 2026-09-29 (item 13): arm (b) is now "no ADMIN_CREATED audit row", full
    # stop. The old test pinned "NULL country AND no audit row"; it can no
    # longer catch a regression to the country test, and that is on purpose --
    # the country is an operator-typed field, not evidence of anything.
    _with_shop_tables(conn)
    conn.execute("INSERT INTO company_settings VALUES (?, NULL)", (CO,))
    conn.commit()
    assert is_joined_device(conn, CO, "me") is True


def test_arm_b_allow_and_deny_directions_fail_closed(conn):
    _with_shop_tables(conn)
    # No settings row, no audit row -> JOINED (used to read "not joined": the
    # escape the verifier used to claim the main role on a joined device).
    assert is_joined_device(conn, CO, "me") is True
    # Country typed in, still no founding row -> STILL joined (verifier's P3).
    conn.execute("INSERT INTO company_settings VALUES (?, 'JO')", (CO,))
    conn.commit()
    assert is_joined_device(conn, CO, "me") is True
    # Founding audit row present -> not joined, whatever the country says.
    conn.execute("UPDATE company_settings SET country=NULL")
    conn.execute("INSERT INTO audit_logs (company_id, action) VALUES (?, 'ADMIN_CREATED')", (CO,))
    conn.commit()
    assert is_joined_device(conn, CO, "me") is False
    # The seeder's positive marker beats a founding row: this install was
    # seeded as a joiner, so it is joined.
    conn.execute("INSERT INTO audit_logs (company_id, action) VALUES (?, 'JOINED_DEVICE_SEEDED')", (CO,))
    conn.commit()
    assert is_joined_device(conn, CO, "me") is True


def test_missing_audit_table_reads_as_joined(conn):
    # Fail closed: no audit table means no proof of founding.
    assert is_joined_device(conn, CO, "me") is True


# ── routes ───────────────────────────────────────────────────────────────────

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
def rdb(tmp_path):
    return tmp_path / "registry.db"


@pytest.fixture
def client(rdb, app_data, monkeypatch, conn):
    def _get_conn():
        c = sqlite3.connect(str(rdb))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(device_routes, "get_conn", _get_conn)
    monkeypatch.setattr(mt_auth, "REGISTRY_DB", str(rdb))
    flask_app = Flask(__name__)
    flask_app.secret_key = "t"
    flask_app.testing = True
    flask_app.register_blueprint(device_bp)
    c = flask_app.test_client()
    conn.execute("INSERT INTO users (id, uid) VALUES ('user-1', 'uid-1')")
    conn.commit()
    with c.session_transaction() as s:
        s["mt_user_id"] = "user-1"
        s["company_id"] = CO
        s["mt_role"] = "admin"
        s["mt_session_version"] = 1
    return c


def _this_device(conn, platform="WINDOWS", founded=True):
    """`founded=True` (default) gives the shop a founding ADMIN_CREATED audit
    row: since item 13 the claim FAILS CLOSED, so a device with no such row is
    treated as a joiner and every claim-route test would be refused for the
    wrong reason. Pass founded=False to test the joiner arm."""
    if founded:
        _with_shop_tables(conn)
        conn.execute("INSERT INTO audit_logs (company_id, action) VALUES (?, 'ADMIN_CREATED')", (CO,))
        conn.commit()
    did = device_context.local_device_uuid()
    upsert_local_device(conn, did, CO, platform=platform)
    device_context._cached_device = None
    return did


def test_claim_route_refuses_a_joined_device_with_the_joined_code(client, conn):
    _this_device(conn)
    apply_admin_assignment(conn, CO, {"device_id": "boss", "epoch": 1,
                                      "assigned_at_utc": "2026-01-01T00:00:00+00:00"})
    conn.commit()
    r = client.post("/api/devices/me/claim-admin")
    assert r.status_code == 409
    assert r.get_json()["code"] == "JOINED_DEVICE_CANNOT_CLAIM"
    assert r.get_json()["error"] == device_routes.JOINED_DEVICE_MESSAGE
    assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 0


def test_claim_route_arm_b_joined_refusal_and_founder_allowed(client, conn):
    did = _this_device(conn, founded=False)
    _with_shop_tables(conn)
    conn.execute("INSERT INTO company_settings VALUES (?, NULL)", (CO,))
    conn.commit()
    refused = client.post("/api/devices/me/claim-admin")
    assert refused.get_json()["code"] == "JOINED_DEVICE_CANNOT_CLAIM"
    # Typing a country in (verifier P3) must not turn a joined device into a
    # claimant. The old test had no such step.
    conn.execute("UPDATE company_settings SET country='JO'")
    conn.commit()
    still = client.post("/api/devices/me/claim-admin")
    assert still.get_json()["code"] == "JOINED_DEVICE_CANNOT_CLAIM"
    assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 0
    # Same device, but the founding audit row exists -> the claim goes through.
    conn.execute("INSERT INTO audit_logs (company_id, action) VALUES (?, 'ADMIN_CREATED')", (CO,))
    conn.commit()
    ok = client.post("/api/devices/me/claim-admin")
    assert ok.status_code == 200, ok.get_json()
    assert get_assignment(conn, CO)["device_id"] == did


def test_claim_route_refuses_a_non_windows_device(client, conn):
    _this_device(conn, platform="ANDROID")
    r = client.post("/api/devices/me/claim-admin")
    assert r.status_code == 409
    assert r.get_json()["code"] == "MAIN_MUST_BE_WINDOWS"
    assert get_assignment(conn, CO) is None


def test_claim_route_allows_windows_and_unknown_platform(client, conn):
    did = _this_device(conn, platform=None)
    r = client.post("/api/devices/me/claim-admin")
    assert r.status_code == 200, r.get_json()
    assert get_assignment(conn, CO)["device_id"] == did


def test_transfer_route_refuses_an_android_target_and_a_clashing_target(client, conn):
    _this_device(conn)
    assert client.post("/api/devices/me/claim-admin").status_code == 200
    upsert_local_device(conn, "phone", CO, platform="ANDROID")
    upsert_local_device(conn, "twin", CO, platform="WINDOWS")
    r1 = client.post("/api/devices/phone/admin")
    assert r1.status_code == 409 and r1.get_json()["code"] == "MAIN_MUST_BE_WINDOWS"

    conn.execute(
        "CREATE TABLE sync_apply_quarantine (entity_id TEXT, entity_type TEXT, event_type TEXT,"
        " payload TEXT, reason TEXT, detail TEXT, quarantined_at TEXT, PRIMARY KEY (entity_id, event_type))")
    conn.execute("INSERT INTO sync_apply_quarantine VALUES ('twin','device','update','{}',"
                 "'device_identity_clash','','2026-01-01')")
    conn.commit()
    r2 = client.post("/api/devices/twin/admin")
    assert r2.status_code == 409 and r2.get_json()["code"] == "DEVICE_IDENTITY_CLASH"
    # Neither refusal moved the role.
    assert get_assignment(conn, CO)["epoch"] == 1


def test_transfer_route_writes_epoch_two_and_an_event(client, conn):
    _this_device(conn)
    client.post("/api/devices/me/claim-admin")
    upsert_local_device(conn, "second", CO, platform="WINDOWS")
    r = client.post("/api/devices/second/admin")
    assert r.status_code == 200, r.get_json()
    a = get_assignment(conn, CO)
    assert (a["device_id"], a["epoch"], a["assigned_by_user_uid"]) == ("second", 2, "uid-1")
    assert [e[0] for e in _events(conn, "admin_device")] == ["create", "update"]


def test_label_route_both_directions(client, conn):
    _this_device(conn)
    client.post("/api/devices/me/claim-admin")
    upsert_local_device(conn, "till2", CO)
    ok = client.post("/api/devices/till2/label", json={"label": "  Back till  "})
    assert ok.status_code == 200 and ok.get_json()["device"]["device_label"] == "Back till"
    empty = client.post("/api/devices/till2/label", json={"label": "   "})
    assert empty.status_code == 400 and empty.get_json()["code"] == "DEVICE_NAME_REQUIRED"
    edge = client.post("/api/devices/till2/label", json={"label": "x" * 60})
    assert edge.status_code == 200
    long_ = client.post("/api/devices/till2/label", json={"label": "x" * 61})
    assert long_.status_code == 400 and long_.get_json()["code"] == "DEVICE_NAME_TOO_LONG"
    assert client.post("/api/devices/nope/label", json={"label": "a"}).status_code == 404


def test_label_and_revoke_routes_refuse_a_non_main_computer(client, conn):
    _this_device(conn)                       # this device is NOT main
    upsert_local_device(conn, "boss", CO)
    conn.execute("UPDATE devices SET is_admin_device=1 WHERE id='boss'")
    conn.commit()
    upsert_local_device(conn, "till2", CO)
    for path, body in (("/api/devices/till2/label", {"label": "x"}), ("/api/devices/till2/revoke", None)):
        r = client.post(path, json=body)
        assert r.status_code == 403 and r.get_json()["code"] == "NOT_ADMIN_DEVICE", path
    assert device_registry.get_device(conn, "till2")["status"] == "active"


def test_revoke_route_and_cannot_revoke_main(client, conn):
    did = _this_device(conn)
    client.post("/api/devices/me/claim-admin")
    upsert_local_device(conn, "till2", CO)
    ok = client.post("/api/devices/till2/revoke")
    assert ok.status_code == 200 and ok.get_json()["device"]["status"] == "revoked"
    refused = client.post(f"/api/devices/{did}/revoke")
    assert refused.status_code == 409 and refused.get_json()["code"] == "CANNOT_REVOKE_MAIN"
    assert client.post("/api/devices/ghost/revoke").status_code == 404


def test_list_route_marks_this_device_and_reports_the_rollout_advisory(client, conn):
    did = _this_device(conn)
    upsert_local_device(conn, "old-till", CO)         # never announced -> blocks
    body = client.get("/api/devices").get_json()
    flags = {d["id"]: d["is_this_device"] for d in body["devices"]}
    assert flags == {did: True, "old-till": False}
    assert body["role_editing"]["allowed"] is False
    assert {b["id"] for b in body["role_editing"]["blocking_devices"]} == {did, "old-till"}
    # allow-half: once every ACTIVE device reports v9 the advisory clears; a
    # revoked laggard does not block.
    conn.execute("UPDATE devices SET registry_schema_version=9 WHERE id=?", (did,))
    conn.execute("UPDATE devices SET status='revoked' WHERE id='old-till'")
    conn.commit()
    body = client.get("/api/devices").get_json()
    assert body["role_editing"]["allowed"] is True and body["role_editing"]["blocking_devices"] == []
