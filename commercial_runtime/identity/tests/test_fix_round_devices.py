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
    ASSIGNMENT_RECEIPT_FUTURE_SECONDS,
    DEVICE_STATUSES,
    MAX_ADMIN_EPOCH,
    MAX_ROW_VERSION_JUMP,
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

@pytest.mark.parametrize("epoch", [2 ** 63 - 1, 10 ** 20, 2 ** 31 + 1, 2 ** 31, 0, -1, True, "5", 2.0, None])
def test_h1_an_epoch_outside_1_to_2_31_minus_1_or_not_an_int_is_refused(conn, epoch):
    """P1/P1b + D1 (fix round 2). A forged epoch of 2**63-1 froze every later
    hand-over (the next epoch overflowed), and a bool/str/float slipped through
    int(). D1: the ceiling was INCLUSIVE at 2**31, so an epoch a peer stored
    could not be followed (the next hand-over is 2**31 + 1, which no peer
    accepts). Valid epochs are now 1 .. MAX_ADMIN_EPOCH = 2**31 - 1, and 2**31
    is refused. Nothing may be stored by a refused payload."""
    with pytest.raises(ValueError):
        apply_admin_assignment(conn, CO, _a("till2", epoch), now=NOW)
    assert get_assignment(conn, CO) is None


def test_h1_the_epoch_ceiling_itself_and_a_plain_first_epoch_are_accepted(conn):
    """ALLOW half of the bound: MAX_ADMIN_EPOCH (2**31 - 1) is the last legal
    value, 1 the first. (This used to say 2**31; that was the D1 defect.)"""
    assert MAX_ADMIN_EPOCH == 2 ** 31 - 1
    assert apply_admin_assignment(conn, CO, _a("till2", 1), now=NOW) is True
    assert apply_admin_assignment(conn, CO, _a("till2", MAX_ADMIN_EPOCH, "2026-09-02T00:00:00+00:00"), now=NOW) is True
    assert get_assignment(conn, CO)["epoch"] == MAX_ADMIN_EPOCH


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
    "2026-10-01T12:00:01+00:00",   # 48h + 1s ahead of NOW (D8: receipt tolerance is 48h)
    "2027-09-29T12:00:00+00:00",   # a year ahead
])
def test_h1_assigned_at_must_parse_be_recent_and_not_in_the_future(conn, bad_at):
    """P3 + the far-future variant: a timestamp of year 1 wins every epoch tie
    forever and a far-future one freezes the takeover clock. Bounded to
    [2024-01-01, now + 48 hours] (D8: was 10 minutes, which refused an honest
    computer whose clock ran a few hours fast and left the shop with no main)."""
    with pytest.raises(ValueError):
        apply_admin_assignment(conn, CO, _a("till2", 1, bad_at), now=NOW)
    assert get_assignment(conn, CO) is None


@pytest.mark.parametrize("at", [
    "2026-09-29T12:05:00+00:00",   # 5 minutes ahead
    "2026-09-29T12:11:00+00:00",   # 11 minutes ahead: refused before D8
    "2026-09-30T12:00:00+00:00",   # one day ahead: an honest fast clock
    "2026-10-01T11:59:59+00:00",   # 1s inside the 48h edge
])
def test_h1_assigned_at_inside_the_window_is_accepted(conn, at):
    """D8. The ALLOW half of the future bound. We never CLAMP the stamp, only
    accept or refuse it. The takeover's own clock guard (FUTURE_SKEW_SECONDS,
    10 minutes) is deliberately unchanged: it guards the LOCAL clock, and this
    tolerance is only about a peer's stamp on receipt."""
    assert apply_admin_assignment(conn, CO, _a("till2", 1, at), now=NOW) is True


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
    something that looks intended).

    Item 12 (deliberate change): the label is an OWNER decision, so it rides
    `admin_row_version`; these events used to carry it under `row_version`,
    which now (correctly) leaves the label alone. The sanitiser is exercised
    exactly as before, through the counter that actually applies a label."""
    _shop(conn)
    apply_device_event(conn, CO, _dev_event("till2", device_label="Front‮desk",
                                            row_version=1, admin_row_version=3), now=NOW)
    assert get_device(conn, "till2")["device_label"] == "Frontdesk"
    with pytest.raises(ValueError):
        apply_device_event(conn, CO, _dev_event("till2", device_label="A" * 5000,
                                                row_version=1, admin_row_version=4), now=NOW)
    with pytest.raises(ValueError):
        apply_device_event(conn, CO, _dev_event("till2", platform="x y<script>", row_version=4), now=NOW)
    with pytest.raises(ValueError):
        apply_device_event(conn, CO, _dev_event("till2", row_version=True), now=NOW)
    with pytest.raises(ValueError):
        apply_device_event(conn, CO, _dev_event("till2", admin_row_version=True), now=NOW)
    assert get_device(conn, "till2")["admin_row_version"] == 3
    assert get_device(conn, "till2")["device_label"] == "Frontdesk"


def test_h2d_revoked_stays_revoked_unless_the_known_main_reinstates(conn):
    """A peer's plain 'active' never resurrects a revoked computer, not even at
    a higher version; only an event naming the MAIN THIS RECEIVER KNOWS does.
    Three denials (plain, a wrong named device, no known main) and the one
    allowance."""
    _shop(conn)
    revoke_device_synced(conn, CO, "till2")
    # Item 12: a revoke moves the OWNER counter, so that is the one to beat.
    v = get_device(conn, "till2")["admin_row_version"]
    apply_device_event(conn, CO, _dev_event("till2", admin_row_version=v + 50), now=NOW)
    assert get_device(conn, "till2")["status"] == "revoked"
    apply_device_event(conn, CO, _dev_event("till2", admin_row_version=v + 51,
                                            reinstated_by_device_id="phone"), now=NOW)
    assert get_device(conn, "till2")["status"] == "revoked"
    # same version as the revoke, even from the real main: not strictly newer.
    apply_device_event(conn, CO, _dev_event("till2", admin_row_version=v,
                                            reinstated_by_device_id="main"), now=NOW)
    assert get_device(conn, "till2")["status"] == "revoked"
    # a newer DEVICE counter alone never reinstates, whoever it names.
    apply_device_event(conn, CO, _dev_event("till2", row_version=v + 60, admin_row_version=v,
                                            reinstated_by_device_id="main"), now=NOW)
    assert get_device(conn, "till2")["status"] == "revoked"
    apply_device_event(conn, CO, _dev_event("till2", admin_row_version=v + 52,
                                            reinstated_by_device_id="main"), now=NOW)
    assert get_device(conn, "till2")["status"] == "active"


def test_h2d_a_receiver_that_knows_no_main_never_honours_a_reinstatement(conn):
    upsert_local_device(conn, "till2", CO, platform="WINDOWS")
    revoke_device_synced(conn, CO, "till2")
    v = get_device(conn, "till2")["admin_row_version"]
    apply_device_event(conn, CO, _dev_event("till2", admin_row_version=v + 1,
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
    The LATEST mark wins (D9; here the joined mark is inserted after the
    founding one, so it wins -- the reverse order is test_d9_*). An assignment naming this device
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


def test_l2_me_hides_the_fingerprint_and_install_id_from_non_admins(client, conn):
    """Mutant: the strip in my_device_route removed. /me used to hand the
    fingerprint and the Owner install id to ANY signed-in user (a cashier);
    the list route already hid them. The admin session must still see both
    (ALLOW half), a cashier and a demo-mode admin must not; the route still
    answers and still names the device (the fix must not empty the envelope)."""
    did = device_context.local_device_uuid()
    upsert_local_device(conn, did, CO, platform="WINDOWS",
                        device_fingerprint="fp-secret", owner_installation_id="inst-secret")
    device_context._cached_device = None
    admin = client.get("/api/devices/me").get_json()["device"]
    assert admin["device_fingerprint"] == "fp-secret"
    assert admin["owner_installation_id"] == "inst-secret"
    for role, demo in (("cashier", False), ("admin", True)):
        with client.session_transaction() as s:
            s["mt_role"] = role
            s["is_demo_mode"] = demo
        r = client.get("/api/devices/me")
        assert r.status_code == 200, r.get_json()
        seen = r.get_json()["device"]
        for hidden in device_routes.NON_ADMIN_HIDDEN_DEVICE_FIELDS:
            assert hidden not in seen, (role, demo, hidden)
        assert seen["id"] == did and "device_name" in seen and "branch_name" in seen


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
    must be refused with the 'exhausted' ValueError -- writing MAX_ADMIN_EPOCH + 1 would
    store a value no peer's apply accepts, forking the shop's main."""
    _shop(conn)
    assert apply_admin_assignment(conn, CO, _a("till2", MAX_ADMIN_EPOCH, "2026-09-02T00:00:00+00:00"), now=NOW) is True
    _clear_outbox(conn)
    with pytest.raises(ValueError, match="exhausted"):
        transfer_admin_device_with_assignment(conn, CO, "main", by_user_uid="owner-uid")
    assert get_assignment(conn, CO)["epoch"] == MAX_ADMIN_EPOCH
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


# ── Second fix round (crew R2, 2026-09-30): D1-D13 + item 12 ────────────────
# Every test below was RED against commit 99b9aa95 (or was written first and
# watched fail) and is GREEN now. The mutant each one is tied to is named in
# the docstring; the mutation logs are in the hand-back.

# D3 ─ an ANDROID row never becomes main, whatever order the events arrive in.

def test_d3_an_assignment_that_arrived_before_an_android_row_never_flags_it(conn):
    """Mutant: the platform filter in recompute_admin_flag removed. The
    assignment is stored while the device is still unknown (ordering is not
    guaranteed); the row then lands as ANDROID. It used to be flagged main the
    moment it arrived. The assignment row stays -- it is the shop's recorded
    fact -- but the flag must not land on a phone."""
    assert apply_admin_assignment(conn, CO, _a("late-phone", 1), now=NOW) is True
    apply_device_event(conn, CO, _dev_event("late-phone", platform="ANDROID", row_version=1), now=NOW)
    assert get_device(conn, "late-phone")["platform"] == "ANDROID"
    assert _flagged(conn) == []
    assert get_assignment(conn, CO)["device_id"] == "late-phone"


def test_d3_a_windows_or_unknown_platform_device_is_still_flagged(conn):
    """ALLOW half of D3: the filter must not lock out the honest cases -- a
    WINDOWS row, and a row whose platform is not known yet (NULL)."""
    assert apply_admin_assignment(conn, CO, _a("late-win", 1), now=NOW) is True
    apply_device_event(conn, CO, _dev_event("late-win", platform="WINDOWS", row_version=1), now=NOW)
    assert _flagged(conn) == ["late-win"]
    upsert_local_device(conn, "no-platform", CO)                 # platform NULL
    assert apply_admin_assignment(conn, CO, _a("no-platform", 2, "2026-09-02T00:00:00+00:00"),
                                  now=NOW) is True
    assert get_device(conn, "no-platform")["platform"] is None
    assert _flagged(conn) == ["no-platform"]


# D4 ─ status is a whitelist, matched exactly.

@pytest.mark.parametrize("status", [
    "inactive", " revoked", "revoked\t", "disabled", "REVOKED\u200b", "Revoked", "ACTIVE", "", 0, True, ["revoked"],
])
def test_d4_a_status_that_is_not_exactly_active_or_revoked_is_refused(conn, status):
    """Mutant: the whitelist check in _validated_device_payload removed. The old
    guard only recognised the lower-cased word 'revoked', so every spelling
    above slipped past cannot-revoke-main and was STORED. Refused whole, and the
    row is untouched (no half-applied update)."""
    _shop(conn)
    before = dict(get_device(conn, "till2"))
    with pytest.raises(ValueError, match="malformed device payload: status"):
        apply_device_event(conn, CO, _dev_event("till2", status=status, row_version=5), now=NOW)
    assert dict(get_device(conn, "till2")) == before


def test_d4_the_two_real_statuses_and_an_absent_status_are_accepted(conn):
    """ALLOW half of D4: the whitelist is exactly ('active', 'revoked') and an
    event that says nothing about status changes nothing about it."""
    assert DEVICE_STATUSES == ("active", "revoked")
    _shop(conn)
    apply_device_event(conn, CO, _dev_event("till2", status="revoked", row_version=3), now=NOW)
    assert get_device(conn, "till2")["status"] == "revoked"
    event = _dev_event("phone", row_version=3)
    del event["status"]
    apply_device_event(conn, CO, event, now=NOW)
    assert get_device(conn, "phone")["status"] == "active"
    apply_device_event(conn, CO, _dev_event("phone", status="active", row_version=4), now=NOW)
    assert get_device(conn, "phone")["status"] == "active"


def test_d4_no_received_event_moves_the_main_computer_off_active(conn):
    """Mutant: the main-status guard in apply_device_event removed. With the
    whitelist in place 'revoked' is the only way left to try, and the local
    assignment names the main. A refused attempt leaves the flag and the
    status alone; an 'active' event about the main is an ordinary update."""
    _shop(conn)
    for version in (2, 50):
        with pytest.raises(ValueError, match=device_registry.REASON_CANNOT_REVOKE_MAIN):
            apply_device_event(conn, CO, _dev_event("main", status="revoked", row_version=version,
                                                    admin_row_version=version), now=NOW)
    assert get_device(conn, "main")["status"] == "active"
    assert _flagged(conn) == ["main"]
    apply_device_event(conn, CO, _dev_event("main", status="active", row_version=3,
                                            app_version="9.9"), now=NOW)
    assert get_device(conn, "main")["app_version"] == "9.9"
    assert _flagged(conn) == ["main"]


# D5 ─ AUDIT-033: the reinstate event is forgeable; what is bounded today.

def test_d5_reinstate_never_moves_main(conn):
    """Mutant: a reinstate that also rewrote the assignment / the flag. D5 is a
    KNOWN residual (AUDIT-033, next wave): `reinstated_by_device_id` is a public
    string, so any peer that can put an event on the wire can un-revoke a
    device. No pseudo-authentication is invented here. What is bounded, and
    pinned by this test: the reinstate sets status 'active' and NOTHING ELSE. A
    reinstated device cannot become main through it, whoever forged it."""
    _shop(conn)
    revoke_device_synced(conn, CO, "till2")
    v = get_device(conn, "till2")["admin_row_version"]
    assignment_before = dict(get_assignment(conn, CO))
    apply_device_event(conn, CO, _dev_event("till2", admin_row_version=v + 1,
                                            reinstated_by_device_id="main"), now=NOW)
    assert get_device(conn, "till2")["status"] == "active"            # the door opened...
    assert dict(get_assignment(conn, CO)) == assignment_before        # ...and only that
    assert _flagged(conn) == ["main"]
    assert get_device(conn, "till2")["is_admin_device"] == 0


# D7 ─ a version may not leap, on either counter; the sender caps itself.

@pytest.mark.parametrize("counter", ["row_version", "admin_row_version"])
def test_d7_a_version_more_than_the_jump_cap_above_ours_is_refused(conn, counter):
    """Mutant: the D7 jump check removed (or its counter loop narrowed to
    row_version). A forged 2**31 version would pin the row so no honest edit
    could ever be newer. Each counter is compared with its OWN local value;
    exactly the cap above is still a legal (if large) edit."""
    _shop(conn)
    local = get_device(conn, "till2")[counter]
    ok = {"row_version": 1, "admin_row_version": 1}
    with pytest.raises(ValueError, match="jumps too far"):
        apply_device_event(conn, CO, _dev_event(
            "till2", **{**ok, counter: local + MAX_ROW_VERSION_JUMP + 1}), now=NOW)
    assert get_device(conn, "till2")[counter] == local
    apply_device_event(conn, CO, _dev_event(
        "till2", **{**ok, counter: local + MAX_ROW_VERSION_JUMP}), now=NOW)
    assert get_device(conn, "till2")[counter] == local + MAX_ROW_VERSION_JUMP


def test_d7_a_new_row_has_no_baseline_so_a_high_version_is_stored(conn):
    """Documents the stated residual: with no local row there is nothing to
    compare with, so the first event for an unknown device is not jump-capped
    (only the DEVICE_ROW_VERSION_MAX ceiling applies). Recorded, not fixed."""
    apply_device_event(conn, CO, _dev_event("brand-new", row_version=5000, admin_row_version=7000), now=NOW)
    assert get_device(conn, "brand-new")["row_version"] == 5000
    assert get_device(conn, "brand-new")["admin_row_version"] == 7000


@pytest.mark.parametrize("counter", ["row_version", "admin_row_version"])
def test_d7_a_new_row_above_the_ceiling_is_refused_and_the_ceiling_itself_is_not(conn, counter):
    """Mutant: the DEVICE_ROW_VERSION_MAX range check in the payload validator
    loosened (or removed). A brand-new row has no jump baseline (test above),
    so this ceiling is the ONLY thing between a forged 10**12 and a row no
    honest edit could ever be newer than. Refusal must store NOTHING (no
    half-made row); exactly the ceiling is legal, and so is the sender's own
    cap (test below) -- deny half and allow half, each counter."""
    top = device_registry.DEVICE_ROW_VERSION_MAX
    other = "admin_row_version" if counter == "row_version" else "row_version"
    with pytest.raises(ValueError, match="malformed device payload"):
        apply_device_event(conn, CO, _dev_event(
            "forged", **{other: 1, counter: top + 1}), now=NOW)
    assert get_device(conn, "forged") is None
    with pytest.raises(ValueError, match="malformed device payload"):
        apply_device_event(conn, CO, _dev_event(
            "zero", **{other: 1, counter: 0}), now=NOW)
    assert get_device(conn, "zero") is None
    apply_device_event(conn, CO, _dev_event(
        "at-ceiling", **{other: 1, counter: top}), now=NOW)
    assert get_device(conn, "at-ceiling")[counter] == top


def test_d7_the_sender_stops_at_the_ceiling_a_receiver_accepts(conn):
    """Mutant: the MIN(..., DEVICE_ROW_VERSION_MAX) cap removed from
    _bump_device. A row a forgery pushed to the ceiling would otherwise emit
    ceiling+1 on the next honest change, which every receiver refuses for good.
    Both counters. ALLOW half: below the ceiling a bump is still +1."""
    _shop(conn)
    top = device_registry.DEVICE_ROW_VERSION_MAX
    conn.execute("UPDATE devices SET row_version=?, admin_row_version=? WHERE id='till2'", (top, top))
    conn.commit()
    announce_row, _ = device_registry.announce_device_fields(conn, "till2", app_version="new-build")
    assert announce_row["row_version"] == top
    assert set_label(conn, "till2", "Renamed")["admin_row_version"] == top
    conn.execute("UPDATE devices SET row_version=5, admin_row_version=5 WHERE id='till2'")
    conn.commit()
    announce_row, _ = device_registry.announce_device_fields(conn, "till2", app_version="newer-build")
    assert announce_row["row_version"] == 6 and announce_row["admin_row_version"] == 5
    assert set_label(conn, "till2", "Renamed again")["admin_row_version"] == 6


def set_label(conn, device_id, label):
    return device_registry.set_device_label_synced(conn, CO, device_id, label)


# D9 ─ the LATEST of the two marks decides.

def test_d9_the_latest_audit_row_decides_joined_or_founder(conn):
    """Mutant: the ORDER BY rowid DESC dropped / flipped to ASC. A device that
    joined (JOINED_DEVICE_SEEDED) and whose owner then chose 'Set up a new shop
    instead' (create-admin writes ADMIN_CREATED) is a FOUNDER. Ordering is by
    insertion (rowid), NOT by id or timestamp: the ids below are deliberately
    out of alphabetical order so an id sort would answer the opposite way."""
    upsert_local_device(conn, "me", CO, platform="WINDOWS")
    conn.execute("INSERT INTO audit_logs (id, company_id, action) VALUES ('z', ?, 'JOINED_DEVICE_SEEDED')", (CO,))
    conn.commit()
    assert is_joined_device(conn, CO, "me") is True                  # joined, nothing later
    conn.execute("INSERT INTO audit_logs (id, company_id, action) VALUES ('a', ?, 'ADMIN_CREATED')", (CO,))
    conn.commit()
    assert is_joined_device(conn, CO, "me") is False                 # then founded: a founder
    conn.execute("INSERT INTO audit_logs (id, company_id, action) VALUES ('m', ?, 'JOINED_DEVICE_SEEDED')", (CO,))
    conn.commit()
    assert is_joined_device(conn, CO, "me") is True                  # then re-joined: joined again


def test_d9_another_shops_audit_rows_do_not_decide(conn):
    """The lookup is company-scoped: a founding mark under another company_id
    must not turn a joined device into a founder."""
    upsert_local_device(conn, "me", CO, platform="WINDOWS")
    conn.execute("INSERT INTO audit_logs (id, company_id, action) VALUES ('1', 'other-shop', 'ADMIN_CREATED')")
    conn.commit()
    assert is_joined_device(conn, CO, "me") is True


# Item 12 ─ two counters: the owner's changes and the device's own facts.

def _pair_with_till2(tmp_path, label="Old"):
    """Two registries that both know till2 (Windows) under the same label."""
    owner = _new_registry(tmp_path / "owner.db")
    peer = _new_registry(tmp_path / "peer.db")
    for reg in (owner, peer):
        upsert_local_device(reg, "till2", CO, platform="WINDOWS", device_label=label,
                            owner_installation_id="inst-till2")
    return owner, peer


def test_item_12_an_owner_rename_survives_a_computer_that_restarts_more_often(tmp_path):
    """Mutant (a): announce bumps the SHARED counter (`counters=` back to
    row_version on a boot/heartbeat). The owner renames till2 once; till2's own
    computer restarts three times. Before this fix its row_version outran the
    rename and the rename was silently discarded everywhere. Now the restart
    moves no version, so the rename wins on the restarting computer AND the
    restarting computer's old label does not win on the owner's."""
    owner, peer = _pair_with_till2(tmp_path)
    try:
        set_label(owner, "till2", "Front desk")
        (_, from_owner), = _events(owner, "device")
        for _ in range(3):
            device_registry.announce_device_fields(peer, "till2", force=True,
                                                   device_label="Old")
        after_restarts = get_device(peer, "till2")
        assert after_restarts["row_version"] == 1 and after_restarts["admin_row_version"] == 1
        from_peer = _events(peer, "device")[-1][1]
        apply_device_event(peer, CO, from_owner, now=NOW)
        assert get_device(peer, "till2")["device_label"] == "Front desk"
        apply_device_event(owner, CO, from_peer, now=NOW)
        assert get_device(owner, "till2")["device_label"] == "Front desk"
    finally:
        owner.close()
        peer.close()


def test_item_12_an_owner_removal_survives_a_restarting_computer_too(tmp_path):
    """Same shape for status: the owner revokes till2 on the owner's registry;
    the revoked computer's own restarts (which still say 'active') cannot
    outvote it, and its event cannot un-revoke the row."""
    owner, peer = _pair_with_till2(tmp_path)
    try:
        upsert_local_device(owner, "main", CO, platform="WINDOWS")
        claim_admin_device_with_assignment(owner, CO, "main", by_user_uid="owner-uid")
        before = get_device(owner, "till2")
        revoke_device_synced(owner, CO, "till2")
        # Mutation-found gap: the revoke bumping the SHARED row_version instead of
        # the owner counter stayed green here (the sticky-revoked rule alone kept
        # the row revoked). The owner's decision belongs to the owner counter,
        # and the event must carry it, or a restarting computer can outrun it.
        after = get_device(owner, "till2")
        assert after["admin_row_version"] == before["admin_row_version"] + 1
        assert after["row_version"] == before["row_version"]
        revoke_payload = _events(owner, "device")[-1][1]
        assert revoke_payload["admin_row_version"] == before["admin_row_version"] + 1
        assert revoke_payload["row_version"] == before["row_version"]
        for _ in range(3):
            device_registry.announce_device_fields(peer, "till2", force=True)
        from_peer = _events(peer, "device")[-1][1]
        assert from_peer["status"] == "active"
        apply_device_event(owner, CO, from_peer, now=NOW)
        assert get_device(owner, "till2")["status"] == "revoked"
    finally:
        owner.close()
        peer.close()


def test_item_12_a_boot_or_heartbeat_that_changes_nothing_moves_no_version(conn):
    """Mutant: an unchanged force announce bumps row_version. It must still
    queue ONE event (a peer that lost the row re-learns it; an old peer
    updates) and move last_seen_at, but no counter."""
    _shop(conn)
    before = get_device(conn, "till2")
    _clear_outbox(conn)
    row, announced = device_registry.announce_device_fields(
        conn, "till2", force=True, now="2026-09-29T13:00:00+00:00")
    assert announced is True
    assert row["row_version"] == before["row_version"]
    assert row["admin_row_version"] == before["admin_row_version"]
    assert row["last_seen_at"] == "2026-09-29T13:00:00+00:00"
    (_, payload), = _events(conn, "device")
    assert payload["row_version"] == before["row_version"]
    assert payload["admin_row_version"] == before["admin_row_version"]
    # a stale heartbeat (no force) behaves the same way.
    _clear_outbox(conn)
    row, announced = device_registry.announce_device_fields(
        conn, "till2", now="2026-09-30T13:00:00+00:00")
    assert announced is True and row["row_version"] == before["row_version"]


def test_item_12_a_change_to_the_devices_own_facts_moves_only_row_version(conn):
    """ALLOW half: a real change still versions the self group, and only it."""
    _shop(conn)
    before = get_device(conn, "till2")
    row, announced = device_registry.announce_device_fields(conn, "till2", app_version="2.0")
    assert announced is True
    assert row["row_version"] == before["row_version"] + 1
    assert row["admin_row_version"] == before["admin_row_version"]


def test_item_12_an_announce_only_fills_a_blank_label_and_that_moves_the_owner_counter(conn):
    """Mutant: the announce puts device_label back into the diff. A non-blank
    label the owner set is never overwritten by a restart; a BLANK one is
    filled (that fill is an owner-group change, so admin_row_version moves and
    row_version does not)."""
    upsert_local_device(conn, "till2", CO, platform="WINDOWS")
    before = get_device(conn, "till2")
    row, _ = device_registry.announce_device_fields(conn, "till2", device_label="Till 2")
    assert row["device_label"] == "Till 2"
    assert row["admin_row_version"] == before["admin_row_version"] + 1
    assert row["row_version"] == before["row_version"]
    row, _ = device_registry.announce_device_fields(conn, "till2", device_label="Something else", force=True)
    assert row["device_label"] == "Till 2"
    assert row["admin_row_version"] == before["admin_row_version"] + 1


def test_item_12_a_self_announce_with_a_newer_row_version_leaves_label_and_status_alone(conn):
    """Mutant (b): the label (or status) applied under row_version. An event
    whose row_version is newer but whose admin_row_version is not carries the
    device's own facts only -- its label and status are NOT authoritative."""
    _shop(conn)
    set_label(conn, "till2", "Front desk")
    admin_v = get_device(conn, "till2")["admin_row_version"]
    apply_device_event(conn, CO, _dev_event("till2", device_label="Hijacked", row_version=9,
                                            admin_row_version=admin_v, app_version="7.7"), now=NOW)
    row = get_device(conn, "till2")
    assert row["device_label"] == "Front desk" and row["status"] == "active"
    assert row["row_version"] == 9 and row["app_version"] == "7.7"        # the self group DID apply
    assert row["admin_row_version"] == admin_v
    # An absent admin_row_version reads as 1: it can never beat the owner's.
    event = _dev_event("till2", device_label="Hijacked", row_version=10)
    apply_device_event(conn, CO, event, now=NOW)
    assert get_device(conn, "till2")["device_label"] == "Front desk"
    # ALLOW half: a strictly newer admin_row_version DOES apply the label.
    apply_device_event(conn, CO, _dev_event("till2", device_label="Renamed by owner",
                                            row_version=1, admin_row_version=admin_v + 1), now=NOW)
    assert get_device(conn, "till2")["device_label"] == "Renamed by owner"
    assert get_device(conn, "till2")["row_version"] == 10                 # self group untouched


def test_item_12_an_insert_carries_both_counters(conn):
    """A peer that lost the row gets both versions, not a reset to 1."""
    apply_device_event(conn, CO, _dev_event("fresh", row_version=4, admin_row_version=6), now=NOW)
    row = get_device(conn, "fresh")
    assert (row["row_version"], row["admin_row_version"]) == (4, 6)
    apply_device_event(conn, CO, _dev_event("fresh-2", row_version=3), now=NOW)   # absent reads as 1
    assert get_device(conn, "fresh-2")["admin_row_version"] == 1


def test_item_12_last_seen_is_merged_to_the_later_value_never_replaced(conn):
    """Mutant: `last_seen = incoming.get('last_seen_at')` instead of MAX. A peer
    event that is accepted on its counters but carries an OLDER last_seen_at
    (a relayed, delayed announce) must not drag this computer's 'last seen'
    backwards -- that timestamp is what the takeover freshness rule reads.
    Both accepted branches are covered (device facts newer, owner decision newer)
    plus the pure 'touch' branch; and the ALLOW half: a later incoming value wins."""
    _shop(conn)
    late, early = "2026-09-29T11:00:00+00:00", "2026-09-29T09:00:00+00:00"
    conn.execute("UPDATE devices SET last_seen_at=? WHERE id='till2'", (late,))
    conn.commit()
    # accepted on row_version (device facts), older last_seen
    apply_device_event(conn, CO, _dev_event("till2", row_version=5, last_seen_at=early), now=NOW)
    row = get_device(conn, "till2")
    assert row["row_version"] == 5
    assert row["last_seen_at"] == late, row["last_seen_at"]
    # accepted on admin_row_version (owner decision), older last_seen
    apply_device_event(conn, CO, _dev_event("till2", row_version=1, admin_row_version=4,
                                            device_label="Renamed", last_seen_at=early), now=NOW)
    row = get_device(conn, "till2")
    assert row["device_label"] == "Renamed" and row["admin_row_version"] == 4
    assert row["last_seen_at"] == late, row["last_seen_at"]
    # not newer on either counter, older last_seen: nothing moves
    apply_device_event(conn, CO, _dev_event("till2", row_version=1, last_seen_at=early), now=NOW)
    assert get_device(conn, "till2")["last_seen_at"] == late
    # ALLOW half: a LATER last_seen_at wins, on the touch path and on the update path.
    later = "2026-09-29T11:30:00+00:00"
    apply_device_event(conn, CO, _dev_event("till2", row_version=1, last_seen_at=later), now=NOW)
    assert get_device(conn, "till2")["last_seen_at"] == later
    latest = "2026-09-29T11:45:00+00:00"
    apply_device_event(conn, CO, _dev_event("till2", row_version=9, last_seen_at=latest), now=NOW)
    assert get_device(conn, "till2")["last_seen_at"] == latest


def test_item_12_the_wire_payload_carries_admin_row_version(conn):
    """device_sync_payload puts the owner counter on the wire; without it a
    receiver could never tell an owner change from a device announce."""
    _shop(conn)
    set_label(conn, "till2", "Front desk")
    payload = device_registry.device_sync_payload(get_device(conn, "till2"))
    assert payload["admin_row_version"] == get_device(conn, "till2")["admin_row_version"] >= 2
    assert payload["row_version"] == get_device(conn, "till2")["row_version"]
