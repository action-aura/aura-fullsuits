"""Registry v9 -- a device announces itself, and peers apply what they hear.

Two halves:
  SEND    announce_device_fields / device_context.announce_local_device
  RECEIVE device_sync_apply_plan (pure) / apply_device_event

WHY THE BOOT ANNOUNCE MATTERS. An older build (registry < 9) plain-skips
`device` events and, the registry stream being unstamped, cannot park them. The
ONLY thing that heals such a peer is a later announcement after it upgrades. So
"boot always announces, even when nothing changed" is a guard, and it is
mutation-proved (force ignored -> RED).

Run (one file per process):
    python -m pytest commercial_runtime/identity/tests/test_device_announce.py -q
"""
import json
import sqlite3

import pytest

from commercial_runtime.identity import device_context, device_registry
from commercial_runtime.identity.device_registry import (
    ANNOUNCE_HEARTBEAT_SECONDS,
    announce_device_fields,
    apply_admin_assignment,
    apply_device_event,
    apply_identity_device_schema,
    device_sync_apply_plan,
    device_sync_payload,
    admin_device_sync_payload,
    get_device,
    registry_rollout_blockers,
    upsert_local_device,
)
from commercial_runtime.identity.device_sync_schema import apply_device_sync_schema

CO = "company-1"
T0 = "2026-09-01T10:00:00+00:00"


def _later(seconds):
    from datetime import datetime, timedelta
    return (datetime.fromisoformat(T0) + timedelta(seconds=seconds)).isoformat()


@pytest.fixture(autouse=True)
def _reset_cache():
    device_context._cached_device = None
    yield
    device_context._cached_device = None


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path))
    c = sqlite3.connect(str(tmp_path / "registry.db"))
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE sync_outbox (id TEXT PRIMARY KEY, entity_type TEXT, entity_id TEXT,"
        " event_type TEXT, payload TEXT, created_at TEXT)"
    )
    apply_identity_device_schema(c)
    apply_device_sync_schema(c)
    c.commit()
    yield c
    c.close()


def _events(conn):
    return [(r["event_type"], r["entity_id"], json.loads(r["payload"])) for r in
            conn.execute("SELECT event_type, entity_id, payload FROM sync_outbox ORDER BY rowid")]


# ── SEND: announce_device_fields ─────────────────────────────────────────────

def test_first_announce_emits_create_with_the_build_facts(conn):
    upsert_local_device(conn, "d1", CO)
    row, announced = announce_device_fields(
        conn, "d1", device_label="Till", platform="WINDOWS", app_version="1.2.3",
        retail_schema_version=42, registry_schema_version=9, now=T0)
    assert announced is True
    assert (row["registry_schema_version"], row["retail_schema_version"], row["app_version"]) == (9, 42, "1.2.3")
    assert row["updated_at_utc"] == T0
    evs = _events(conn)
    assert [e[0] for e in evs] == ["create"] and evs[0][1] == "d1"
    assert evs[0][2]["registry_schema_version"] == 9


def test_unchanged_repeat_within_the_heartbeat_announces_nothing(conn):
    upsert_local_device(conn, "d1", CO)
    kw = dict(device_label="Till", platform="WINDOWS", app_version="1", retail_schema_version=42,
              registry_schema_version=9)
    announce_device_fields(conn, "d1", now=T0, **kw)
    version = get_device(conn, "d1")["row_version"]
    row, announced = announce_device_fields(conn, "d1", now=_later(60), **kw)
    assert announced is False
    assert row["row_version"] == version
    assert len(_events(conn)) == 1


def test_a_changed_field_announces_an_update(conn):
    upsert_local_device(conn, "d1", CO)
    announce_device_fields(conn, "d1", app_version="1", now=T0)
    row, announced = announce_device_fields(conn, "d1", app_version="2", now=_later(60))
    assert announced is True and row["app_version"] == "2"
    assert [e[0] for e in _events(conn)] == ["create", "update"]


def test_stale_heartbeat_announces_and_fresh_does_not(conn):
    upsert_local_device(conn, "d1", CO)
    announce_device_fields(conn, "d1", app_version="1", now=T0)
    _, fresh = announce_device_fields(conn, "d1", app_version="1",
                                      now=_later(ANNOUNCE_HEARTBEAT_SECONDS - 1))
    _, stale = announce_device_fields(conn, "d1", app_version="1",
                                      now=_later(ANNOUNCE_HEARTBEAT_SECONDS))
    assert (fresh, stale) == (False, True)


def test_force_announces_even_when_nothing_changed(conn):
    upsert_local_device(conn, "d1", CO)
    announce_device_fields(conn, "d1", app_version="1", now=T0)
    _, announced = announce_device_fields(conn, "d1", app_version="1", force=True, now=_later(5))
    assert announced is True
    assert [e[0] for e in _events(conn)] == ["create", "update"]


def test_none_fields_are_left_alone(conn):
    upsert_local_device(conn, "d1", CO, device_label="Keep", platform="WINDOWS")
    row, _ = announce_device_fields(conn, "d1", app_version="1", now=T0)
    assert (row["device_label"], row["platform"]) == ("Keep", "WINDOWS")


def test_announcing_an_unknown_device_raises(conn):
    with pytest.raises(ValueError):
        announce_device_fields(conn, "ghost", app_version="1")


# ── SEND: announce_local_device (device_context) ─────────────────────────────

def test_boot_reason_forces_and_heartbeat_reason_does_not(conn):
    device_context.resolve_local_device(conn, CO)
    first = device_context.announce_local_device(conn, CO, app_version="1", reason="boot")
    assert first["announced"] is True
    hb = device_context.announce_local_device(conn, CO, app_version="1", reason="heartbeat")
    assert hb["announced"] is False
    boot_again = device_context.announce_local_device(conn, CO, app_version="1", reason="boot")
    assert boot_again["announced"] is True, "boot must always say so -- it is what heals an old peer"


def test_announce_local_defaults_platform_and_registry_version(conn):
    out = device_context.announce_local_device(conn, CO, reason="boot")
    from commercial_runtime.identity.registry_db import REGISTRY_SCHEMA_VERSION
    assert out["device"]["registry_schema_version"] == REGISTRY_SCHEMA_VERSION == 9
    assert out["device"]["platform"] == device_context.detect_platform()


def test_announce_local_propagates_a_company_mismatch_and_writes_nothing(conn):
    device_context.resolve_local_device(conn, CO)
    device_context._cached_device = None
    before = conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]
    with pytest.raises(device_context.DeviceCompanyMismatchError):
        device_context.announce_local_device(conn, "some-other-company", reason="boot")
    assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == before
    # ...and the row was NOT re-homed to the other company.
    assert get_device(conn, device_context.local_device_uuid())["company_id"] == CO


@pytest.mark.parametrize("env,expected", [
    ({"ANDROID_ROOT": "/system"}, "ANDROID"),
    ({"ANDROID_DATA": "/data"}, "ANDROID"),
])
def test_detect_platform_android_from_env(monkeypatch, env, expected):
    monkeypatch.delenv("ANDROID_ROOT", raising=False)
    monkeypatch.delenv("ANDROID_DATA", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert device_context.detect_platform() == expected


# ── payload builders ─────────────────────────────────────────────────────────

def test_device_payload_never_carries_the_admin_flag_or_company(conn):
    upsert_local_device(conn, "d1", CO)
    conn.execute("UPDATE devices SET is_admin_device=1 WHERE id='d1'")
    p = device_sync_payload(get_device(conn, "d1"))
    assert "is_admin_device" not in p and "company_id" not in p
    assert p["device_id"] == "d1" and p["status"] == "active"


def test_admin_payload_has_no_company_id():
    p = admin_device_sync_payload({"company_id": CO, "device_id": "d", "epoch": 3,
                                   "assigned_at_utc": T0})
    assert "company_id" not in p and p["epoch"] == 3


# ── RECEIVE: the pure plan ───────────────────────────────────────────────────

def _inc(**kw):
    base = {"device_id": "d1", "device_label": "L", "platform": "WINDOWS", "status": "active",
            "owner_installation_id": "inst-A", "device_fingerprint": None, "row_version": 2,
            "updated_at_utc": T0, "last_seen_at": T0, "first_seen_at": T0}
    base.update(kw)
    return base


def _loc(**kw):
    base = {"id": "d1", "owner_installation_id": "inst-A", "device_fingerprint": None,
            "row_version": 1, "status": "active", "last_seen_at": T0, "device_label": "old"}
    base.update(kw)
    return base


def test_plan_insert_when_unknown_and_never_admin():
    plan = device_sync_apply_plan(None, None, _inc(is_admin_device=1))
    assert plan["action"] == "insert"
    assert "is_admin_device" not in plan["set"]


def test_plan_update_only_when_incoming_version_is_greater():
    assert device_sync_apply_plan(_loc(row_version=1), None, _inc(row_version=2))["action"] == "update"
    assert device_sync_apply_plan(_loc(row_version=2), None, _inc(row_version=2))["action"] == "skip"
    assert device_sync_apply_plan(_loc(row_version=3), None, _inc(row_version=2))["action"] == "skip"


def test_plan_quarantines_an_identity_clash_but_not_an_unknown_installation():
    clash = device_sync_apply_plan(_loc(owner_installation_id="inst-A"), None,
                                   _inc(owner_installation_id="inst-B"))
    assert (clash["action"], clash["reason"]) == ("quarantine", "device_identity_clash")
    # allow-half: an unknown installation (either side) is not evidence.
    assert device_sync_apply_plan(_loc(owner_installation_id=None), None,
                                  _inc(owner_installation_id="inst-B"))["action"] == "update"
    assert device_sync_apply_plan(_loc(owner_installation_id="inst-A"), None,
                                  _inc(owner_installation_id=None))["action"] == "update"


def test_plan_quarantines_a_duplicate_fingerprint_held_by_another_id():
    plan = device_sync_apply_plan(None, {"id": "other"}, _inc(device_fingerprint="fp"))
    assert (plan["action"], plan["reason"]) == ("quarantine", "duplicate_device_fingerprint")
    # allow-half: the same id holding that fingerprint is not a duplicate.
    ok = device_sync_apply_plan(_loc(), {"id": "d1"}, _inc(device_fingerprint="fp"))
    assert ok["action"] == "update"


def test_revoked_is_sticky_both_ways():
    # An incoming revoke applies even at an EQUAL row_version...
    rv = device_sync_apply_plan(_loc(row_version=2), None, _inc(row_version=2, status="revoked"))
    assert rv["action"] == "update" and rv["set"]["status"] == "revoked"
    # ...and a newer 'active' announcement never resurrects a locally revoked device.
    up = device_sync_apply_plan(_loc(row_version=1, status="revoked"), None,
                                _inc(row_version=9, status="active"))
    assert up["action"] == "update" and up["set"]["status"] == "revoked"


def test_plan_update_never_blanks_identity_and_takes_the_later_last_seen():
    plan = device_sync_apply_plan(
        _loc(owner_installation_id="inst-A", device_fingerprint="fp", last_seen_at=_later(500)), None,
        _inc(owner_installation_id=None, device_fingerprint=None, last_seen_at=_later(10)))
    assert plan["set"]["owner_installation_id"] == "inst-A"
    assert plan["set"]["device_fingerprint"] == "fp"
    assert plan["set"]["last_seen_at"] == _later(500)


def test_plan_touch_when_only_last_seen_moves():
    plan = device_sync_apply_plan(_loc(row_version=2, last_seen_at=T0), None,
                                  _inc(row_version=2, last_seen_at=_later(90)))
    assert plan["action"] == "touch" and plan["set"] == {"last_seen_at": _later(90)}


# ── RECEIVE: apply_device_event against a real table ─────────────────────────

def test_apply_inserts_under_the_receivers_company_without_the_admin_flag(conn):
    plan = apply_device_event(conn, CO, _inc(device_id="peer", is_admin_device=1, company_id="evil"))
    conn.commit()
    row = get_device(conn, "peer")
    assert plan["action"] == "insert"
    assert row["company_id"] == CO and row["is_admin_device"] == 0


def test_apply_of_a_new_peer_never_disturbs_the_computer_that_holds_the_flag(conn):
    """The test above cannot see the insert's own flag value: the recompute
    that follows the insert clears it whatever it was. With a real main already
    in place the difference is visible -- a peer inserted WITH the flag would
    collide with the main on the one-main-per-company unique index (or steal
    the flag). The peer arrives with no flag and the main keeps its own."""
    upsert_local_device(conn, "boss", CO, platform="WINDOWS")
    apply_admin_assignment(conn, CO, {"device_id": "boss", "epoch": 1, "assigned_at_utc": T0})
    apply_device_event(conn, CO, _inc(device_id="boss", row_version=1))
    conn.commit()
    assert get_device(conn, "boss")["is_admin_device"] == 1
    apply_device_event(conn, CO, _inc(device_id="peer", is_admin_device=1))
    conn.commit()
    assert get_device(conn, "peer")["is_admin_device"] == 0
    assert get_device(conn, "boss")["is_admin_device"] == 1


def test_apply_emits_nothing(conn):
    apply_device_event(conn, CO, _inc(device_id="peer"))
    assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 0


def test_apply_arrival_of_the_assigned_device_sets_the_flag(conn):
    """The assignment landed first (device row not yet here); when the device
    event arrives the derived flag must appear."""
    apply_admin_assignment(conn, CO, {"device_id": "peer", "epoch": 1, "assigned_at_utc": T0})
    apply_device_event(conn, CO, _inc(device_id="peer"))
    assert get_device(conn, "peer")["is_admin_device"] == 1


def test_apply_revoke_of_the_assigned_device_clears_the_flag(conn):
    apply_admin_assignment(conn, CO, {"device_id": "peer", "epoch": 1, "assigned_at_utc": T0})
    apply_device_event(conn, CO, _inc(device_id="peer", row_version=1))
    assert get_device(conn, "peer")["is_admin_device"] == 1
    apply_device_event(conn, CO, _inc(device_id="peer", row_version=2, status="revoked"))
    assert get_device(conn, "peer")["is_admin_device"] == 0


def test_apply_row_under_another_company_is_quarantined_and_untouched(conn):
    upsert_local_device(conn, "peer", "company-2", device_label="theirs")
    plan = apply_device_event(conn, CO, _inc(device_id="peer", device_label="mine", row_version=9))
    assert (plan["action"], plan["reason"]) == ("quarantine", "device_identity_clash")
    row = get_device(conn, "peer")
    assert (row["company_id"], row["device_label"]) == ("company-2", "theirs")


def test_apply_malformed_raises(conn):
    with pytest.raises(ValueError):
        apply_device_event(conn, CO, {"device_label": "no id"})


def test_apply_duplicate_fingerprint_is_quarantined_not_an_integrity_error(conn):
    upsert_local_device(conn, "first", CO, device_fingerprint="fp-1")
    plan = apply_device_event(conn, CO, _inc(device_id="second", device_fingerprint="fp-1"))
    assert plan["action"] == "quarantine" and plan["reason"] == "duplicate_device_fingerprint"
    assert get_device(conn, "second") is None


# ── rollout blockers ─────────────────────────────────────────────────────────

def test_rollout_blockers_lists_null_and_old_active_devices_only(conn):
    upsert_local_device(conn, "never", CO)
    upsert_local_device(conn, "old", CO)
    upsert_local_device(conn, "new", CO)
    upsert_local_device(conn, "gone", CO)
    upsert_local_device(conn, "elsewhere", "company-2")
    conn.execute("UPDATE devices SET registry_schema_version=8 WHERE id='old'")
    conn.execute("UPDATE devices SET registry_schema_version=9 WHERE id='new'")
    conn.execute("UPDATE devices SET status='revoked' WHERE id='gone'")
    conn.commit()
    assert {b["id"] for b in registry_rollout_blockers(conn, CO)} == {"never", "old"}


def test_rollout_blockers_is_empty_on_a_pre_v9_devices_table(tmp_path):
    c = sqlite3.connect(str(tmp_path / "pre.db"))
    c.row_factory = sqlite3.Row
    apply_identity_device_schema(c)         # no v9 columns
    upsert_local_device(c, "d", CO)
    assert registry_rollout_blockers(c, CO) == []
    c.close()
