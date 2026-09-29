"""2026-09-29 verification round, crew R -- the DEVICE / main-computer half.

Every test here was written against a defect the adversarial review proved
(proofs P1-P6b, findings H1, H2, M1, M2, L2 and review items 12, 13, 17, 18),
and each was RED before its fix and is GREEN after. The mutation proofs (which
source line each test is tied to) are recorded in the hand-back, not here.

WHY THE SHAPE. Most of these guard a deny-half AND an allow-half: refusing a
forged epoch is worthless if it also refuses an honest hand-over, so each
refusal sits beside the neighbouring input that must still work
(ENGINEERING.md 1, "prove both directions").

NOT COVERED, ON PURPOSE: sender authentication (finding C1). Nothing here
proves a peer is who it says; these tests prove only that a peer's payload
cannot do more damage than the receiver's own rules allow.

Run (one file per process):
    python -m pytest commercial_runtime/identity/tests/test_fix_round_devices.py -q
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

from commercial_runtime.identity import (
    company_rebind,
    device_context,
    device_registry,
    device_routes,
    device_takeover,
    mt_auth,
)
from commercial_runtime.identity.device_registry import (
    admin_assignment_wins,
    announce_admin_assignment,
    apply_admin_assignment,
    apply_device_event,
    apply_identity_device_schema,
    claim_admin_device_with_assignment,
    get_assignment,
    get_device,
    is_joined_device,
    recompute_admin_flag,
    reinstate_device_synced,
    revoke_device_synced,
    transfer_admin_device_with_assignment,
    upsert_local_device,
)
from commercial_runtime.identity.device_routes import device_bp
from commercial_runtime.identity.device_sync_schema import apply_device_sync_schema

CO = "company-1"
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
GOOD_AT = "2026-09-01T00:00:00+00:00"


def _iso(dt):
    return dt.isoformat()


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
    conn.execute(
        "CREATE TABLE audit_logs (id TEXT PRIMARY KEY, company_id TEXT, user_id TEXT, action TEXT,"
        " entity_type TEXT, entity_id TEXT, new_value_json TEXT)"
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
    return [(r[0], json.loads(r[1])) for r in conn.execute(
        "SELECT event_type, payload FROM sync_outbox WHERE entity_type=? ORDER BY rowid",
        (entity_type,)).fetchall()]


def _shop(conn):
    """main + till2 (Windows) and phone (Android), all this shop's. Main is
    claimed by the owner, so the assignment is epoch 1 and a real row."""
    for did, platform in (("main", "WINDOWS"), ("till2", "WINDOWS"), ("phone", "ANDROID")):
        upsert_local_device(conn, did, CO, platform=platform, owner_installation_id="inst-" + did)
    claim_admin_device_with_assignment(conn, CO, "main", by_user_uid="owner-uid")
    conn.commit()


def _clear_outbox(conn):
    conn.execute("DELETE FROM sync_outbox")
    conn.commit()


def _flagged(conn):
    return sorted(r[0] for r in conn.execute(
        "SELECT id FROM devices WHERE company_id=? AND is_admin_device=1", (CO,)).fetchall())


def _a(device_id, epoch, at=GOOD_AT, **extra):
    return {"device_id": device_id, "epoch": epoch, "assigned_at_utc": at, **extra}


# ── H1: what an assignment payload may say ───────────────────────────────────

@pytest.mark.parametrize("epoch", [2 ** 63 - 1, 10 ** 20, 2 ** 31 + 1, 0, -1, True, "5", 2.0, None])
def test_h1_an_epoch_outside_1_to_2_31_or_not_an_int_is_refused(conn, epoch):
    """P1/P1b. A forged epoch of 2**63-1 froze every later hand-over (the next
    epoch overflowed), and a bool/str/float slipped through int(). Nothing may
    be stored by a refused payload."""
    with pytest.raises(ValueError):
        apply_admin_assignment(conn, CO, _a("till2", epoch), now=NOW)
    assert get_assignment(conn, CO) is None


def test_h1_the_epoch_ceiling_itself_and_a_plain_first_epoch_are_accepted(conn):
    """ALLOW half of the bound: 2**31 is the last legal value, 1 the first."""
    assert apply_admin_assignment(conn, CO, _a("till2", 1), now=NOW) is True
    assert apply_admin_assignment(conn, CO, _a("till2", 2 ** 31, "2026-09-02T00:00:00+00:00"), now=NOW) is True
    assert get_assignment(conn, CO)["epoch"] == 2 ** 31


def test_h1_a_refused_forgery_leaves_the_next_honest_handover_working(conn):
    """The damage the forged epoch did was the AFTER: with it stored, the
    legitimate transfer could not compute epoch+1. Prove the honest chain
    survives the refused forgery, then a takeover too."""
    _shop(conn)
    with pytest.raises(ValueError):
        apply_admin_assignment(conn, CO, _a("phone", 2 ** 63 - 1), now=NOW)
    moved = transfer_admin_device_with_assignment(conn, CO, "till2", by_user_uid="owner-uid")
    assert moved["id"] == "till2"
    assert get_assignment(conn, CO)["epoch"] == 2
    assert _flagged(conn) == ["till2"]


@pytest.mark.parametrize("bad_at", [
    "x", "", "0001-01-01T00:00:00+00:00", "2023-12-31T23:59:59+00:00",
    "2026-09-29T12:11:00+00:00",   # 11 minutes ahead of NOW
])
def test_h1_assigned_at_must_parse_be_recent_and_not_in_the_future(conn, bad_at):
    """P3 + the far-future variant: a timestamp of year 1 wins every epoch tie
    forever and a far-future one freezes the takeover clock. Bounded to
    [2024-01-01, now + 10 minutes]."""
    with pytest.raises(ValueError):
        apply_admin_assignment(conn, CO, _a("till2", 1, bad_at), now=NOW)
    assert get_assignment(conn, CO) is None


def test_h1_assigned_at_inside_the_window_is_accepted(conn):
    assert apply_admin_assignment(conn, CO, _a("till2", 1, "2026-09-29T12:05:00+00:00"), now=NOW) is True


def test_h1_a_forged_year_one_epoch_one_cannot_win_the_tie(conn):
    """P3. Same epoch, earlier stamp used to win. The floor makes the forged
    stamp unusable; the legitimate holder stays."""
    upsert_local_device(conn, "till2", CO, platform="WINDOWS")
    apply_admin_assignment(conn, CO, _a("main", 1, "2026-09-01T00:00:00+00:00"), now=NOW)
    with pytest.raises(ValueError):
        apply_admin_assignment(conn, CO, _a("till2", 1, "0001-01-01T00:00:00+00:00"), now=NOW)
    assert get_assignment(conn, CO)["device_id"] == "main"


@pytest.mark.parametrize("field,value", [
    ("device_id", ""), ("device_id", 7), ("device_id", "x" * 129),
    ("assigned_by_user_uid", "x" * 129), ("assigned_by_device_id", 5),
])
def test_h1_malformed_identity_fields_are_refused(conn, field, value):
    payload = _a("till2", 1)
    payload[field] = value
    with pytest.raises(ValueError):
        apply_admin_assignment(conn, CO, payload, now=NOW)
    assert get_assignment(conn, CO) is None


def test_h1_the_named_computer_must_be_eligible_when_we_know_it(conn):
    """A phone (not Windows), a revoked computer and another shop's computer
    are refused as main; a computer we have never heard of is STORED (its own
    `device` event may simply not have arrived yet -- ordering is not
    guaranteed). The check ran: each refusal carries the one reason code."""
    upsert_local_device(conn, "phone", CO, platform="ANDROID")
    upsert_local_device(conn, "till2", CO, platform="WINDOWS")
    upsert_local_device(conn, "foreign", "other-shop", platform="WINDOWS")
    revoke_device_synced(conn, CO, "till2")
    for name in ("phone", "till2", "foreign"):
        with pytest.raises(ValueError, match=device_registry.REASON_ADMIN_TARGET_INELIGIBLE):
            apply_admin_assignment(conn, CO, _a(name, 1), now=NOW)
        assert get_assignment(conn, CO) is None, name
    # ALLOW half: unknown device stored, and a live Windows one stored.
    assert apply_admin_assignment(conn, CO, _a("not-heard-of-yet", 1), now=NOW) is True
    upsert_local_device(conn, "main", CO, platform="WINDOWS")
    assert apply_admin_assignment(conn, CO, _a("main", 2, "2026-09-02T00:00:00+00:00"), now=NOW) is True
    assert get_assignment(conn, CO)["device_id"] == "main"


def test_item_17_equal_instants_spelled_differently_fall_to_the_device_id(conn):
    """The tie-break compared assigned_at as TEXT: '...:14+00:00' sorts before
    '...:14.000000+00:00' although they are the same instant, so the spelling
    decided who was main. As parsed instants they tie and the smaller device id
    wins -- on every device, whatever spelling its own peer used."""
    short = _a("b", 3, "2026-09-01T00:00:14+00:00")
    long_ = _a("a", 3, "2026-09-01T00:00:14.000000+00:00")
    assert admin_assignment_wins(long_, short) is False   # local 'a' keeps it
    assert admin_assignment_wins(short, long_) is True    # local 'b' yields to 'a'
    # A genuinely earlier instant still wins regardless of spelling.
    earlier = _a("z", 3, "2026-09-01T00:00:13+00:00")
    assert admin_assignment_wins(long_, earlier) is True


# ── items 1 / ghost main: the claim door stays shut ──────────────────────────

def test_a_claim_is_refused_while_an_assignment_names_a_computer_we_never_saw(conn):
    """The takeover treats a ghost main as silent-since-assigned (so a shop can
    recover after 7 days). That must NOT open the CLAIM door: a claim is for a
    shop with no main at all."""
    upsert_local_device(conn, "me", CO, platform="WINDOWS")
    apply_admin_assignment(conn, CO, _a("ghost", 2), now=NOW)
    assert claim_admin_device_with_assignment(conn, CO, "me") is None
    assert get_assignment(conn, CO)["device_id"] == "ghost"


# ── item 18: the helpers enforce eligibility themselves ──────────────────────

def test_item_18_transfer_refuses_revoked_and_non_windows_targets_directly(conn):
    """The route had the checks; the helper (which sync-adjacent callers use)
    did not. Each refusal must leave no assignment change and no event."""
    _shop(conn)
    revoke_device_synced(conn, CO, "till2")
    _clear_outbox(conn)
    for target in ("till2", "phone"):
        with pytest.raises(ValueError, match=device_registry.REASON_ADMIN_TARGET_INELIGIBLE):
            transfer_admin_device_with_assignment(conn, CO, target, by_user_uid="owner-uid")
    assert get_assignment(conn, CO)["device_id"] == "main"
    assert get_assignment(conn, CO)["epoch"] == 1
    assert _events(conn, "admin_device") == []


def test_item_18_claim_refuses_a_non_windows_device_directly(conn):
    upsert_local_device(conn, "phone", CO, platform="ANDROID")
    with pytest.raises(ValueError, match=device_registry.REASON_ADMIN_TARGET_INELIGIBLE):
        claim_admin_device_with_assignment(conn, CO, "phone")
    assert get_assignment(conn, CO) is None
    assert _events(conn, "admin_device") == []


# ── H2: what a `device` event may do ─────────────────────────────────────────

def _dev_event(device_id, **kw):
    base = {"device_id": device_id, "device_label": "Till", "platform": "WINDOWS",
            "status": "active", "row_version": 2}
    base.update(kw)
    return base


def test_h2a_a_revoke_of_the_main_computer_is_refused_on_receipt(conn):
    """P4. A forged event revoking the main used to strip its flag and leave the
    shop with no main. The local revoke route already refused this; the apply
    path did not."""
    _shop(conn)
    with pytest.raises(ValueError, match=device_registry.REASON_CANNOT_REVOKE_MAIN):
        apply_device_event(conn, CO, _dev_event("main", status="revoked", row_version=99), now=NOW)
    assert get_device(conn, "main")["status"] == "active"
    assert _flagged(conn) == ["main"]
    # ALLOW half: an ordinary computer can be revoked by the same kind of event.
    apply_device_event(conn, CO, _dev_event("till2", status="revoked", row_version=5), now=NOW)
    assert get_device(conn, "till2")["status"] == "revoked"


def test_h2b_timestamps_from_a_peer_are_clamped_to_now_plus_ten_minutes(conn):
    """P5. last_seen_at in year 9999 blocked a takeover for 8000 years."""
    _shop(conn)
    # Real clock: _shop stamps local rows with it, and the receiver keeps the
    # later of local/incoming, so a fixed past NOW would compare against a
    # local stamp from the future of that NOW.
    now = datetime.now(timezone.utc)
    apply_device_event(conn, CO, _dev_event(
        "till2", row_version=5, last_seen_at="9999-01-01T00:00:00+00:00",
        updated_at_utc="9999-01-01T00:00:00+00:00"), now=now)
    row = get_device(conn, "till2")
    ceiling = now + timedelta(seconds=device_registry.FUTURE_SKEW_SECONDS)
    for key in ("last_seen_at", "updated_at_utc"):
        assert device_registry._parse_utc(row[key]) <= ceiling, key


def test_h2c_a_peer_cannot_set_a_hostile_label_or_platform(conn):
    """P6. Bidi override characters are stripped, a 5000-character name and a
    platform that is not a token are refused whole (not truncated into
    something that looks intended)."""
    _shop(conn)
    apply_device_event(conn, CO, _dev_event("till2", device_label="Front‮desk", row_version=3),
                       now=NOW)
    assert get_device(conn, "till2")["device_label"] == "Frontdesk"
    with pytest.raises(ValueError):
        apply_device_event(conn, CO, _dev_event("till2", device_label="A" * 5000, row_version=4), now=NOW)
    with pytest.raises(ValueError):
        apply_device_event(conn, CO, _dev_event("till2", platform="x y<script>", row_version=4), now=NOW)
    with pytest.raises(ValueError):
        apply_device_event(conn, CO, _dev_event("till2", row_version=True), now=NOW)
    assert get_device(conn, "till2")["row_version"] == 3


def test_h2d_revoked_stays_revoked_unless_the_known_main_reinstates(conn):
    """A peer's plain 'active' never resurrects a revoked computer, not even at
    a higher version; only an event naming the MAIN THIS RECEIVER KNOWS does.
    Three denials (plain, a wrong named device, no known main) and the one
    allowance."""
    _shop(conn)
    revoke_device_synced(conn, CO, "till2")
    v = get_device(conn, "till2")["row_version"]
    apply_device_event(conn, CO, _dev_event("till2", row_version=v + 50), now=NOW)
    assert get_device(conn, "till2")["status"] == "revoked"
    apply_device_event(conn, CO, _dev_event("till2", row_version=v + 51,
                                            reinstated_by_device_id="phone"), now=NOW)
    assert get_device(conn, "till2")["status"] == "revoked"
    # same version as the revoke, even from the real main: not strictly newer.
    apply_device_event(conn, CO, _dev_event("till2", row_version=v,
                                            reinstated_by_device_id="main"), now=NOW)
    assert get_device(conn, "till2")["status"] == "revoked"
    apply_device_event(conn, CO, _dev_event("till2", row_version=v + 52,
                                            reinstated_by_device_id="main"), now=NOW)
    assert get_device(conn, "till2")["status"] == "active"


def test_h2d_a_receiver_that_knows_no_main_never_honours_a_reinstatement(conn):
    upsert_local_device(conn, "till2", CO, platform="WINDOWS")
    revoke_device_synced(conn, CO, "till2")
    v = get_device(conn, "till2")["row_version"]
    apply_device_event(conn, CO, _dev_event("till2", row_version=v + 1,
                                            reinstated_by_device_id="main"), now=NOW)
    assert get_device(conn, "till2")["status"] == "revoked"


def test_h2d_reinstate_helper_is_main_only_and_revoked_only(conn):
    _shop(conn)
    revoke_device_synced(conn, CO, "till2")
    with pytest.raises(ValueError, match="only the main computer"):
        reinstate_device_synced(conn, CO, "till2", by_device_id="phone")
    with pytest.raises(ValueError, match="not removed"):
        reinstate_device_synced(conn, CO, "phone", by_device_id="main")
    _clear_outbox(conn)
    row = reinstate_device_synced(conn, CO, "till2", by_device_id="main")
    assert row["status"] == "active"
    (kind, payload), = _events(conn, "device")
    assert kind == "update" and payload["reinstated_by_device_id"] == "main"


def test_h2d_the_reinstate_event_a_peer_receives_actually_reinstates(conn, tmp_path):
    """End to end across two registries: the sender's event, applied by a
    receiver that knows the same main, flips the revoked row back."""
    _shop(conn)
    peer = _new_registry(tmp_path / "peer.db")
    try:
        for did in ("main", "till2"):
            upsert_local_device(peer, did, CO, platform="WINDOWS")
        apply_admin_assignment(peer, CO, _a("main", 1), now=NOW)
        revoke_device_synced(conn, CO, "till2")
        revoke_device_synced(peer, CO, "till2")
        _clear_outbox(conn)
        reinstate_device_synced(conn, CO, "till2", by_device_id="main")
        (_, payload), = _events(conn, "device")
        apply_device_event(peer, CO, payload, now=NOW + timedelta(days=400))
        assert get_device(peer, "till2")["status"] == "active"
    finally:
        peer.close()


def test_p6b_a_revoked_install_returning_under_a_fresh_id_is_stored_revoked(conn):
    """The revoked computer reappears as a NEW device id with the same install
    id and no fingerprint. Stored revoked; an unrelated new computer is stored
    active (the allow half)."""
    _shop(conn)
    revoke_device_synced(conn, CO, "till2")
    apply_device_event(conn, CO, _dev_event("till2-new", owner_installation_id="inst-till2",
                                            row_version=1), now=NOW)
    assert get_device(conn, "till2-new")["status"] == "revoked"
    apply_device_event(conn, CO, _dev_event("till3", owner_installation_id="inst-till3",
                                            row_version=1), now=NOW)
    assert get_device(conn, "till3")["status"] == "active"


# ── item 12: the main computer keeps saying which computer is main ───────────

def _backfilled_assignment(conn, device_id, at):
    """What the v9 backfill leaves behind: an epoch-1 row and NO event."""
    conn.execute("INSERT INTO admin_device_assignment (company_id, device_id, epoch, assigned_at_utc)"
                 " VALUES (?,?,1,?)", (CO, device_id, at))
    recompute_admin_flag(conn, CO)
    conn.commit()


def test_item_12_i_a_backfilled_main_puts_admin_device_on_the_wire_at_boot(conn):
    upsert_local_device(conn, "main", CO, platform="WINDOWS")
    upsert_local_device(conn, "till2", CO, platform="WINDOWS")
    _backfilled_assignment(conn, "main", GOOD_AT)
    assert _events(conn, "admin_device") == []            # the backfill emits nothing
    assert announce_admin_assignment(conn, CO, "main") is True
    (kind, payload), = _events(conn, "admin_device")
    assert kind == "update" and payload["device_id"] == "main" and payload["epoch"] == 1
    # Only the named computer speaks for the role.
    _clear_outbox(conn)
    assert announce_admin_assignment(conn, CO, "till2") is False
    assert _events(conn, "admin_device") == []


@pytest.fixture
def _local_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path))
    device_context._cached_device = None
    yield
    device_context._cached_device = None


def _announce(conn, reason):
    return device_context.announce_local_device(
        conn, CO, device_label="Main", platform="WINDOWS", app_version="1.0",
        retail_schema_version=42, registry_schema_version=9, reason=reason)


def test_item_12_ii_a_claim_a_v8_peer_skipped_is_resent_on_boot_and_on_a_late_heartbeat(
        conn, _local_identity):
    """A v8 peer ignores `admin_device` events entirely, so the original claim
    is lost to it for good unless it is re-sent. Boot always re-sends; the
    heartbeat re-sends once per period (not on every tick)."""
    me = device_context.local_device_uuid()
    upsert_local_device(conn, me, CO, platform="WINDOWS")
    claim_admin_device_with_assignment(conn, CO, me, by_user_uid="owner-uid")
    device_context._cached_device = None

    _clear_outbox(conn)
    out = _announce(conn, "boot")
    assert out["announced"] and out["admin_announced"]
    assert len(_events(conn, "admin_device")) == 1

    _clear_outbox(conn)
    quiet = _announce(conn, "heartbeat")                   # same period: nothing new
    assert quiet["announced"] is False and quiet["admin_announced"] is False
    assert _events(conn, "admin_device") == []

    conn.execute("UPDATE devices SET updated_at_utc=? WHERE id=?",
                 (_iso(datetime.now(timezone.utc) - timedelta(hours=7)), me))
    conn.commit()
    late = _announce(conn, "heartbeat")                    # period elapsed: re-sent
    assert late["announced"] and late["admin_announced"]
    assert len(_events(conn, "admin_device")) == 1


def test_item_12_iii_two_backfilled_double_mains_converge_after_one_exchange(conn, tmp_path):
    """P1a: two computers each backfilled themselves main at epoch 1 and never
    exchanged the rows. After each announces once and the other applies it,
    both sides name the SAME main and exactly one computer is flagged on each."""
    other = _new_registry(tmp_path / "other.db")
    try:
        for c in (conn, other):
            for did in ("aaa", "bbb"):
                upsert_local_device(c, did, CO, platform="WINDOWS")
        _backfilled_assignment(conn, "bbb", "2026-09-05T00:00:00+00:00")
        _backfilled_assignment(other, "aaa", "2026-09-06T00:00:00+00:00")
        assert announce_admin_assignment(conn, CO, "bbb")
        assert announce_admin_assignment(other, CO, "aaa")
        (_, from_conn), = _events(conn, "admin_device")
        (_, from_other), = _events(other, "admin_device")
        apply_admin_assignment(conn, CO, from_other, now=NOW)
        apply_admin_assignment(other, CO, from_conn, now=NOW)
        conn.commit(); other.commit()
        assert get_assignment(conn, CO)["device_id"] == get_assignment(other, CO)["device_id"] == "bbb"
        assert _flagged(conn) == ["bbb"]
        recompute_admin_flag(other, CO)
        assert sorted(r[0] for r in other.execute(
            "SELECT id FROM devices WHERE is_admin_device=1").fetchall()) == ["bbb"]
    finally:
        other.close()


# ── M1: a takeover is loud, and a wrong clock cannot fake silence ────────────

def test_m1_a_takeover_is_marked_on_the_wire_and_audited_by_the_receiver(conn, tmp_path):
    now = datetime.now(timezone.utc)
    long_ago = _iso(now - timedelta(days=90))
    upsert_local_device(conn, "boss", CO, platform="WINDOWS")
    upsert_local_device(conn, "me", CO, platform="WINDOWS")
    conn.execute("UPDATE devices SET last_seen_at=?, first_seen_at=?, updated_at_utc=? WHERE id='boss'",
                 (long_ago, long_ago, long_ago))
    _backfilled_assignment(conn, "boss", long_ago)
    device_takeover.take_over_main(conn, CO, "me", by_user_uid="owner-uid", now=now)
    assert get_assignment(conn, CO)["device_id"] == "me"
    (_, payload), = [e for e in _events(conn, "admin_device")]
    assert payload["takeover"] is True

    peer = _new_registry(tmp_path / "peer.db")
    try:
        for did in ("boss", "me"):
            upsert_local_device(peer, did, CO, platform="WINDOWS")
        _backfilled_assignment(peer, "boss", long_ago)
        assert apply_admin_assignment(peer, CO, payload, now=now) is True
        rows = peer.execute("SELECT entity_id, new_value_json FROM audit_logs "
                            "WHERE action='MAIN_TAKEN_OVER'").fetchall()
        assert len(rows) == 1 and rows[0][0] == "me"
        detail = json.loads(rows[0][1])
        assert detail["severity"] == "WARNING" and detail["old_device_id"] == "boss"
    finally:
        peer.close()


def test_m1_an_ordinary_handover_carries_no_takeover_mark_and_writes_no_warning(conn, tmp_path):
    _shop(conn)
    _clear_outbox(conn)
    transfer_admin_device_with_assignment(conn, CO, "till2", by_user_uid="owner-uid")
    (_, payload), = _events(conn, "admin_device")
    assert "takeover" not in payload
    peer = _new_registry(tmp_path / "peer.db")
    try:
        apply_admin_assignment(peer, CO, payload, now=datetime.now(timezone.utc))
        assert peer.execute("SELECT COUNT(*) FROM audit_logs WHERE action='MAIN_TAKEN_OVER'"
                            ).fetchone()[0] == 0
    finally:
        peer.close()


# ── M2: the takeover's own password back-off (unit level) ────────────────────

def test_m2_three_misses_lock_for_fifteen_minutes_then_double_per_user_and_device(conn):
    u, d = "user-1", "me"
    locks = [device_takeover.record_takeover_failure(conn, u, d, now=NOW) for _ in range(5)]
    assert locks == [0, 0, 900, 1800, 3600]
    locked, retry = device_takeover.takeover_lock_state(conn, u, d, now=NOW)
    assert locked and 3600 <= retry <= 3601
    # a lock is per user AND per computer.
    assert device_takeover.takeover_lock_state(conn, "user-2", d, now=NOW) == (False, 0)
    assert device_takeover.takeover_lock_state(conn, u, "other-pc", now=NOW) == (False, 0)
    # it expires, and a correct password wipes the slate.
    assert device_takeover.takeover_lock_state(conn, u, d, now=NOW + timedelta(seconds=3601)) == (False, 0)
    device_takeover.clear_takeover_failures(conn, u, d)
    assert device_takeover.record_takeover_failure(conn, u, d, now=NOW) == 0


def test_m2_the_doubling_stops_at_the_cap(conn):
    top = None
    for _ in range(device_takeover.TAKEOVER_FREE_ATTEMPTS + device_takeover.TAKEOVER_MAX_LOCK_DOUBLINGS + 5):
        top = device_takeover.record_takeover_failure(conn, "u", "d", now=NOW)
    assert top == device_takeover.TAKEOVER_BASE_LOCK_SECONDS * 2 ** device_takeover.TAKEOVER_MAX_LOCK_DOUBLINGS


def test_m2_password_matches_is_false_for_empty_wrong_type_and_unknown_user(conn):
    assert device_takeover.password_matches(conn, "user-1", "") is False
    assert device_takeover.password_matches(conn, "user-1", None) is False
    assert device_takeover.password_matches(conn, "", "pw") is False


# ── item 13: a joiner leaves a durable mark ──────────────────────────────────

def test_item_13_seeding_a_joining_device_writes_the_joined_marker():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE users (id TEXT, role TEXT)")
    c.execute("CREATE TABLE company_settings (id TEXT, company_id TEXT)")
    c.execute("CREATE TABLE audit_logs (id TEXT PRIMARY KEY, company_id TEXT, user_id TEXT, action TEXT,"
              " entity_type TEXT, entity_id TEXT)")
    assert company_rebind.seed_company_settings_for_joining_device(c, "lic-1") is True
    assert c.execute("SELECT COUNT(*) FROM audit_logs WHERE company_id='lic-1' "
                     "AND action='JOINED_DEVICE_SEEDED'").fetchone()[0] == 1
    # idempotent: the second call seeds nothing and writes no second marker.
    assert company_rebind.seed_company_settings_for_joining_device(c, "lic-1") is False
    assert c.execute("SELECT COUNT(*) FROM audit_logs WHERE action='JOINED_DEVICE_SEEDED'"
                     ).fetchone()[0] == 1


def test_item_13_an_ordinary_install_that_already_has_an_admin_is_not_marked_joined():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE users (id TEXT, role TEXT)")
    c.execute("INSERT INTO users VALUES ('a','admin')")
    c.execute("CREATE TABLE company_settings (id TEXT, company_id TEXT)")
    c.execute("CREATE TABLE audit_logs (id TEXT PRIMARY KEY, company_id TEXT, user_id TEXT, action TEXT,"
              " entity_type TEXT, entity_id TEXT)")
    assert company_rebind.seed_company_settings_for_joining_device(c, "lic-1") is False
    assert c.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 0


def test_item_13_is_joined_device_fails_closed_and_reads_both_marks(conn):
    """No mark either way -> joined (fail closed). Founding mark -> not joined.
    A JOINED mark beats a founding mark. An assignment naming this device
    overrides everything (the holder re-claiming is not a bootstrap)."""
    upsert_local_device(conn, "me", CO, platform="WINDOWS")
    assert is_joined_device(conn, CO, "me") is True
    conn.execute("INSERT INTO audit_logs (id, company_id, action) VALUES ('1', ?, 'ADMIN_CREATED')", (CO,))
    conn.commit()
    assert is_joined_device(conn, CO, "me") is False
    conn.execute("INSERT INTO audit_logs (id, company_id, action) VALUES ('2', ?, 'JOINED_DEVICE_SEEDED')", (CO,))
    conn.commit()
    assert is_joined_device(conn, CO, "me") is True
    apply_admin_assignment(conn, CO, _a("me", 1), now=NOW)
    assert is_joined_device(conn, CO, "me") is False


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
    if founded:
        conn.execute("INSERT INTO audit_logs (id, company_id, action) VALUES ('f', ?, 'ADMIN_CREATED')", (CO,))
        conn.commit()
    did = device_context.local_device_uuid()
    upsert_local_device(conn, did, CO, platform=platform)
    device_context._cached_device = None
    return did


def _make_this_main(client, conn):
    did = _this_device(conn)
    r = client.post("/api/devices/me/claim-admin")
    assert r.status_code == 200, r.get_json()
    return did


def test_m9_the_transfer_route_refuses_a_revoked_target_and_changes_nothing(client, conn):
    did = _make_this_main(client, conn)
    upsert_local_device(conn, "till2", CO, platform="WINDOWS")
    revoke_device_synced(conn, CO, "till2")
    _clear_outbox(conn)
    r = client.post("/api/devices/till2/admin")
    assert r.status_code == 409 and r.get_json()["code"] == "DEVICE_REVOKED"
    assert get_assignment(conn, CO)["device_id"] == did
    assert _events(conn, "admin_device") == []
    # ALLOW half: a live Windows computer receives the role.
    upsert_local_device(conn, "till3", CO, platform="WINDOWS")
    ok = client.post("/api/devices/till3/admin")
    assert ok.status_code == 200, ok.get_json()
    assert get_assignment(conn, CO)["device_id"] == "till3"


def test_l2_only_an_admin_session_sees_the_fingerprint_and_install_id(client, conn):
    upsert_local_device(conn, "till2", CO, platform="WINDOWS",
                        device_fingerprint="fp-secret", owner_installation_id="inst-secret")
    admin_view = client.get("/api/devices").get_json()["devices"]
    row = next(d for d in admin_view if d["id"] == "till2")
    assert row["device_fingerprint"] == "fp-secret" and row["owner_installation_id"] == "inst-secret"
    with client.session_transaction() as s:
        s["mt_role"] = "cashier"
    cashier_view = client.get("/api/devices").get_json()["devices"]
    row = next(d for d in cashier_view if d["id"] == "till2")
    for hidden in device_routes.NON_ADMIN_HIDDEN_DEVICE_FIELDS:
        assert hidden not in row
    assert "device_label" in row                                        # still a usable list
    with client.session_transaction() as s:
        s["mt_role"] = "admin"
        s["is_demo_mode"] = True
    demo_view = client.get("/api/devices").get_json()["devices"]
    assert "device_fingerprint" not in next(d for d in demo_view if d["id"] == "till2")


def test_h2d_reinstate_route_main_only_revoked_only_and_it_emits_the_marker(client, conn):
    did = _make_this_main(client, conn)
    upsert_local_device(conn, "till2", CO, platform="WINDOWS")
    revoke_device_synced(conn, CO, "till2")
    _clear_outbox(conn)
    r = client.post("/api/devices/till2/reinstate")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["device"]["status"] == "active"
    (_, payload), = _events(conn, "device")
    assert payload["reinstated_by_device_id"] == did
    # not revoked -> 409 with its own code, and no new event.
    _clear_outbox(conn)
    again = client.post("/api/devices/till2/reinstate")
    assert again.status_code == 409 and again.get_json()["code"] == "DEVICE_NOT_REVOKED"
    assert _events(conn, "device") == []
    assert client.post("/api/devices/nope/reinstate").status_code == 404


def test_h2d_reinstate_route_is_refused_from_a_computer_that_is_not_main(client, conn):
    _this_device(conn)
    upsert_local_device(conn, "boss", CO, platform="WINDOWS")
    apply_admin_assignment(conn, CO, _a("boss", 1), now=NOW)
    upsert_local_device(conn, "till2", CO, platform="WINDOWS")
    revoke_device_synced(conn, CO, "till2")
    conn.commit()
    r = client.post("/api/devices/till2/reinstate")
    assert r.status_code in (403, 409)
    assert get_device(conn, "till2")["status"] == "revoked"


def test_the_claim_route_names_the_holder_before_it_says_you_joined(client, conn):
    """Another computer already holds the role AND this one is (also) a
    joiner: the actionable answer is the holder's name, so
    ADMIN_DEVICE_ALREADY_CLAIMED must win over JOINED_DEVICE_CANNOT_CLAIM."""
    _this_device(conn)
    upsert_local_device(conn, "boss", CO, platform="WINDOWS")
    apply_admin_assignment(conn, CO, _a("boss", 1), now=NOW)
    conn.commit()
    r = client.post("/api/devices/me/claim-admin")
    assert r.status_code == 409
    assert r.get_json()["code"] == "ADMIN_DEVICE_ALREADY_CLAIMED"
    assert r.get_json()["admin_device"]["id"] == "boss"
    assert get_assignment(conn, CO)["device_id"] == "boss"


# ── Mutation-found gaps (crew R2, 2026-09-30) ────────────────────────────────
# Each test below was written because a mutant of the source it guards stayed
# GREEN under the suite above. The mutant is named in every docstring.

def test_h1_at_the_ceiling_the_next_handover_is_refused_not_written(conn):
    """Mutant: `_next_epoch`'s bound replaced by `if False`. The ceiling itself
    is a legal stored epoch (ALLOW half tested above), so the handover AFTER it
    must be refused with the 'exhausted' ValueError -- writing 2**31 + 1 would
    store a value no peer's apply accepts, forking the shop's main."""
    _shop(conn)
    assert apply_admin_assignment(conn, CO, _a("till2", 2 ** 31, "2026-09-02T00:00:00+00:00"), now=NOW) is True
    _clear_outbox(conn)
    with pytest.raises(ValueError, match="exhausted"):
        transfer_admin_device_with_assignment(conn, CO, "main", by_user_uid="owner-uid")
    assert get_assignment(conn, CO)["epoch"] == 2 ** 31
    assert get_assignment(conn, CO)["device_id"] == "till2"
    assert _events(conn, "admin_device") == []


@pytest.mark.parametrize("exc", [
    OverflowError("Python int too large to convert to SQLite INTEGER"),
    TypeError("bad bind"),
    sqlite3.InterfaceError("bad parameter"),
    sqlite3.DataError("bad data"),
])
def test_h1_nothing_but_valueerror_escapes_apply_admin_assignment(conn, monkeypatch, exc):
    """Mutant: the `except (OverflowError, TypeError, ...)` clause narrowed to
    `ZeroDivisionError`. The bounds checks make these unreachable by design, so
    the only honest way to hold the 'nothing escapes but ValueError' contract
    (the engine quarantines ValueError and treats anything else as a crash) is
    to force each one out of the write and watch the conversion."""
    def _boom(*a, **k):
        raise exc
    monkeypatch.setattr(device_registry, "_write_assignment", _boom)
    with pytest.raises(ValueError, match="malformed admin_device payload"):
        apply_admin_assignment(conn, CO, _a("till2", 1), now=NOW)


def test_h2_the_local_revoke_helper_refuses_the_main_by_flag_and_by_assignment(conn):
    """Mutant: `revoke_device_synced` no longer raises for the main computer.
    (test_h2a covers only the RECEIVING path, apply_device_event.) Both arms of
    the condition are exercised: the flag, and the assignment with the flag
    cleared."""
    _shop(conn)
    _clear_outbox(conn)
    with pytest.raises(ValueError, match="main computer cannot be revoked"):
        revoke_device_synced(conn, CO, "main")
    conn.execute("UPDATE devices SET is_admin_device=0 WHERE id='main'")
    conn.commit()
    with pytest.raises(ValueError, match="main computer cannot be revoked"):
        revoke_device_synced(conn, CO, "main")
    assert get_device(conn, "main")["status"] == "active"
    assert _events(conn, "device") == []
    # ALLOW half: an ordinary computer is revoked and announced.
    assert revoke_device_synced(conn, CO, "till2")["status"] == "revoked"
    assert len(_events(conn, "device")) == 1


@pytest.mark.parametrize("mark", [1, "yes", "false", "true", {"x": 1}, [1], 2])
def test_m1_only_a_real_json_true_counts_as_a_takeover_mark(conn, mark):
    """Mutant: `incoming.get('takeover') is True` loosened to `bool(...)`. The
    mark drives a WARNING audit row and the takeover bookkeeping; a peer that
    can trigger it with `1` or the string "false" can raise false alarms (or
    hide a real one among them). The allow half (a real `True`) is
    test_m1_a_takeover_is_marked_on_the_wire_and_audited_by_the_receiver."""
    upsert_local_device(conn, "boss", CO, platform="WINDOWS")
    upsert_local_device(conn, "me", CO, platform="WINDOWS")
    apply_admin_assignment(conn, CO, _a("boss", 1), now=NOW)
    assert apply_admin_assignment(conn, CO, _a("me", 2, takeover=mark), now=NOW) is True
    assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE action='MAIN_TAKEN_OVER'"
                        ).fetchone()[0] == 0


def test_item_13_a_registry_without_an_audit_table_reads_as_joined(conn):
    """Mutant: the no-audit-table branch of is_joined_device returns False
    (not joined). Fail-closed means 'cannot tell' == joined; the opposite would
    let a device whose audit table is missing claim the main role."""
    upsert_local_device(conn, "me", CO, platform="WINDOWS")
    conn.execute("DROP TABLE audit_logs")
    conn.commit()
    assert is_joined_device(conn, CO, "me") is True


def test_item_13_an_assignment_naming_another_computer_beats_a_founding_mark(conn):
    """Mutant: arm (a) of is_joined_device (an assignment naming ANOTHER device
    is certain evidence of joined) short-circuited to False, which let the
    ADMIN_CREATED founding mark below decide instead. A device can carry that
    mark (copied database, restored backup) and still not be the shop's main."""
    upsert_local_device(conn, "me", CO, platform="WINDOWS")
    conn.execute("INSERT INTO audit_logs (id, company_id, action) VALUES ('1', ?, 'ADMIN_CREATED')", (CO,))
    conn.commit()
    assert is_joined_device(conn, CO, "me") is False          # founding mark, no assignment
    apply_admin_assignment(conn, CO, _a("boss", 1), now=NOW)
    assert is_joined_device(conn, CO, "me") is True           # the shop already has a main, and it is not me
    assert is_joined_device(conn, CO, "boss") is False        # ...whereas the holder itself is not joined
