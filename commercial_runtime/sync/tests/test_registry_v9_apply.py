"""Track S / registry v9 -- apply side of the registry stream.

Covers, against a REAL registry.db built by `init_registry_db()` and driven
through `SyncService._apply_event` (same technique as
test_registry_user_sync_apply.py):

  * the two new `user` keys (`display_name`, `role_preset`): an ABSENT key
    keeps the local value, a PRESENT key (even None) overwrites;
  * the fail-closed books reset when an OLD sender changes a role;
  * the `device` entity (insert / clash quarantines / sticky revoke);
  * the `admin_device` entity (epoch order, assignment before its device row,
    malformed payload parked);
  * nothing the apply side does ever emits back onto the wire.

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/sync/tests/test_registry_v9_apply.py -v
"""
from __future__ import annotations

import uuid

import pytest

from commercial_runtime.identity import registry_db
from commercial_runtime.identity.device_registry import (
    device_has_open_clash,
    get_assignment,
    get_device,
)
from commercial_runtime.identity.user_accounts import BOOKS_CAPABILITY_CODES
from commercial_runtime.sync.sync_service import (
    REGISTRY_SYNC_ENTITY_TYPES,
    RETAIL_SYNC_ENTITY_TYPES,
    SyncService,
)

COMPANY = "company-x"
HOSTILE = "hostile-company"


@pytest.fixture
def registry_env(tmp_path, monkeypatch):
    db_dir = tmp_path / "database"
    monkeypatch.setattr(registry_db, "_db_dir", str(db_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(db_dir / "registry.db"))
    registry_db.init_registry_db()
    return registry_db.get_conn


def _service(get_conn):
    return SyncService(
        client_factory=lambda: None, get_conn=get_conn,
        local_company_id_provider=lambda: COMPANY,
        handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES,
    )


def _apply(get_conn, ev):
    conn = get_conn()
    try:
        result = _service(get_conn)._apply_event(conn, ev, local_company_id=COMPANY)
        conn.commit()
        return result
    finally:
        conn.close()


def _q(get_conn, sql, args=()):
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


# ── user: display_name / role_preset ────────────────────────────────────────

def _seed_user(get_conn, **over):
    row = {
        "id": str(uuid.uuid4()), "company_id": COMPANY, "employee_id": "E1",
        "email": "o@x.com", "password_hash": "H", "role": "cashier", "status": "active",
        "require_password_change": 1, "session_version": 1, "language": "en",
        "clinic_role": "", "failed_login_count": 0, "locked_until": None,
        "uid": str(uuid.uuid4()), "pin_hash": "P", "row_version": 1,
        "updated_at_utc": "2026-01-01T00:00:00+00:00",
        "display_name": "Sara Local", "role_preset": "accountant",
    }
    row.update(over)
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
            "require_password_change, session_version, language, clinic_role, failed_login_count, "
            "locked_until, uid, pin_hash, row_version, updated_at_utc, display_name, role_preset) "
            "VALUES (:id,:company_id,:employee_id,:email,:password_hash,:role,:status,"
            ":require_password_change,:session_version,:language,:clinic_role,:failed_login_count,"
            ":locked_until,:uid,:pin_hash,:row_version,:updated_at_utc,:display_name,:role_preset)",
            row,
        )
        conn.commit()
    finally:
        conn.close()
    return row["uid"], row["id"]


def _seed_books_rows(get_conn, user_id, level):
    conn = get_conn()
    try:
        for code in BOOKS_CAPABILITY_CODES:
            conn.execute(
                "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), user_id, code, level))
        conn.commit()
    finally:
        conn.close()


def _books(get_conn, user_id):
    rows = _q(get_conn, "SELECT subsystem, access_level FROM user_permissions WHERE user_id=?", (user_id,))
    return {r["subsystem"]: r["access_level"] for r in rows if r["subsystem"] in BOOKS_CAPABILITY_CODES}


def _user_ev(uid, **over):
    payload = {
        "uid": uid, "employee_id": "E1", "email": "o@x.com", "role": "cashier",
        "status": "active", "require_password_change": 0, "language": "en",
        "password_hash": "H2", "pin_hash": "P2", "row_version": 2,
        "updated_at_utc": "2026-02-01T00:00:00+00:00", "session_version": 2,
    }
    payload.update(over)
    return {"entity_type": "user", "entity_id": uid, "event_type": "update", "payload": payload}


def test_absent_v9_keys_keep_the_local_values(registry_env):
    """A v8 sender (a PIN change on an old build) writes neither key. The
    receiver keeps 'Sara Local' and 'accountant'. MUTATION-PROVEN: a bare
    `display_name=excluded.display_name` blanks the name (RED)."""
    uid, _ = _seed_user(registry_env)
    _apply(registry_env, _user_ev(uid))  # no display_name / role_preset keys
    row = _q(registry_env, "SELECT display_name, role_preset, row_version FROM users WHERE uid=?", (uid,))[0]
    assert row["row_version"] == 2, "the event did not apply at all"
    assert row["display_name"] == "Sara Local"
    assert row["role_preset"] == "accountant"


def test_absent_role_preset_key_is_kept_independently(registry_env):
    """The two flags are independent: display_name present, role_preset absent."""
    uid, _ = _seed_user(registry_env)
    _apply(registry_env, _user_ev(uid, display_name="Sara New"))
    row = _q(registry_env, "SELECT display_name, role_preset FROM users WHERE uid=?", (uid,))[0]
    assert row["display_name"] == "Sara New"
    assert row["role_preset"] == "accountant"


def test_present_v9_keys_overwrite_including_none(registry_env):
    """The ALLOW half: a present key is authoritative, and a present None
    clears (that is how a preset is removed). Without this a guard that
    always preserved would pass the absent-key tests."""
    uid, _ = _seed_user(registry_env)
    _apply(registry_env, _user_ev(uid, display_name="Renamed", role_preset=None))
    row = _q(registry_env, "SELECT display_name, role_preset FROM users WHERE uid=?", (uid,))[0]
    assert row["display_name"] == "Renamed"
    assert row["role_preset"] is None


def test_new_user_arrives_with_v9_keys(registry_env):
    uid = str(uuid.uuid4())
    _apply(registry_env, _user_ev(uid, display_name="Fresh", role_preset="accountant", role="manager"))
    row = _q(registry_env, "SELECT display_name, role_preset FROM users WHERE uid=?", (uid,))[0]
    assert (row["display_name"], row["role_preset"]) == ("Fresh", "accountant")


def test_old_sender_role_change_resets_books_rows_without_emitting(registry_env):
    """An OLD build changed accountant -> cashier: it re-seeded only the eight
    codes it knows. The receiver's four books rows must drop to the cashier
    defaults (all 'none'), and NOTHING may be queued for the wire."""
    uid, user_id = _seed_user(registry_env, role="manager")
    _seed_books_rows(registry_env, user_id, "full")
    _apply(registry_env, _user_ev(uid, role="cashier"))  # no role_preset key
    assert set(_books(registry_env, user_id).values()) == {"none"}
    assert len(_books(registry_env, user_id)) == len(BOOKS_CAPABILITY_CODES)
    assert _q(registry_env, "SELECT COUNT(*) AS n FROM sync_outbox")[0]["n"] == 0


def test_role_change_with_role_preset_key_does_not_reset(registry_env):
    """A new sender carries its own books events; the fail-closed reset is
    ONLY for a sender that could not."""
    uid, user_id = _seed_user(registry_env, role="manager")
    _seed_books_rows(registry_env, user_id, "full")
    _apply(registry_env, _user_ev(uid, role="cashier", role_preset="cashier"))
    assert set(_books(registry_env, user_id).values()) == {"full"}


def test_old_sender_without_role_change_does_not_reset(registry_env):
    """A plain manager (no preset) edited on an old build keeps its books rows.
    CHANGED 2026-09-29 (registry review L1): this test used to seed the
    default fixture person, an ACCOUNTANT preset on the manager role, and
    expect the rows to survive an old build's unrelated edit. That is now
    exactly the case identity resets (see the next test), so the seed is a
    manager with NO preset. What this can no longer catch on its own: the
    preset-holder case, which the next test pins."""
    uid, user_id = _seed_user(registry_env, role="manager", role_preset=None)
    _seed_books_rows(registry_env, user_id, "full")
    _apply(registry_env, _user_ev(uid, role="manager"))
    assert set(_books(registry_env, user_id).values()) == {"full"}


def test_old_sender_edit_of_a_preset_holder_resets_books_fail_closed(registry_env):
    """L1: an old build demoting an Accountant to Manager sends role
    'manager' -- the same string the Accountant already has as its base role
    -- so 'the role changed' is FALSE and the books rows used to survive: an
    ex-Accountant kept books access. Any old-sender event for a person with a
    non-default preset now resets the four rows. Cost, accepted: an old
    build's PIN change of an Accountant resets them too."""
    uid, user_id = _seed_user(registry_env, role="manager", role_preset="accountant")
    _seed_books_rows(registry_env, user_id, "full")
    _apply(registry_env, _user_ev(uid, role="manager"))  # no role_preset key
    assert set(_books(registry_env, user_id).values()) == {"none"}
    assert _q(registry_env, "SELECT COUNT(*) AS n FROM sync_outbox")[0]["n"] == 0


def test_stale_old_sender_role_change_resets_nothing(registry_env):
    """The reset is gated on the upsert having actually applied: a stale
    (lower row_version) event must not touch the books rows."""
    uid, user_id = _seed_user(registry_env, role="manager", row_version=5)
    _seed_books_rows(registry_env, user_id, "full")
    _apply(registry_env, _user_ev(uid, role="cashier", row_version=3))
    assert set(_books(registry_env, user_id).values()) == {"full"}


# ── device ──────────────────────────────────────────────────────────────────

def _device_ev(device_id, **over):
    payload = {
        "device_id": device_id, "device_label": "Till 1", "platform": "WINDOWS",
        "status": "active", "owner_installation_id": "inst-1",
        "device_fingerprint": "fp-" + device_id, "first_seen_at": "2026-01-01T00:00:00+00:00",
        "last_seen_at": "2026-01-02T00:00:00+00:00", "row_version": 1,
        "updated_at_utc": "2026-01-02T00:00:00+00:00", "app_version": "9.0",
        "retail_schema_version": 42, "registry_schema_version": 9,
    }
    payload.update(over)
    return {"entity_type": "device", "entity_id": device_id, "event_type": "update", "payload": payload}


def _device(get_conn, device_id):
    conn = get_conn()
    try:
        return get_device(conn, device_id)
    finally:
        conn.close()


def test_registry_stream_handles_devices_and_retail_stream_does_not():
    assert {"device", "admin_device"} <= REGISTRY_SYNC_ENTITY_TYPES
    assert not ({"device", "admin_device"} & RETAIL_SYNC_ENTITY_TYPES)


def test_device_insert_uses_receivers_company_and_never_the_payload_flag(registry_env):
    did = str(uuid.uuid4())
    ev = _device_ev(did, company_id=HOSTILE, is_admin_device=1)
    assert _apply(registry_env, ev) is not False
    row = _device(registry_env, did)
    assert row["company_id"] == COMPANY
    assert row["is_admin_device"] == 0
    assert row["device_label"] == "Till 1"


def test_device_identity_clash_is_parked(registry_env):
    did = str(uuid.uuid4())
    _apply(registry_env, _device_ev(did))
    result = _apply(registry_env, _device_ev(did, owner_installation_id="inst-OTHER", row_version=2))
    assert result is False
    rows = _q(registry_env, "SELECT reason, entity_id FROM sync_apply_quarantine WHERE entity_type='device'")
    assert [(r["reason"], r["entity_id"]) for r in rows] == [("device_identity_clash", did)]
    assert _device(registry_env, did)["row_version"] == 1, "the clashing event must not apply"


def test_duplicate_fingerprint_is_parked_and_names_the_local_device(registry_env):
    """The quarantine detail must carry the LOCAL device id, or
    `device_has_open_clash` cannot stop THAT device being handed the main role."""
    local_id, incoming_id = str(uuid.uuid4()), str(uuid.uuid4())
    _apply(registry_env, _device_ev(local_id, device_fingerprint="shared-fp"))
    result = _apply(registry_env, _device_ev(incoming_id, device_fingerprint="shared-fp"))
    assert result is False
    rows = _q(registry_env, "SELECT reason, detail FROM sync_apply_quarantine WHERE entity_type='device'")
    assert len(rows) == 1 and rows[0]["reason"] == "duplicate_device_fingerprint"
    assert local_id in rows[0]["detail"]
    assert _device(registry_env, incoming_id) is None
    conn = registry_env()
    try:
        assert device_has_open_clash(conn, local_id) is True
        assert device_has_open_clash(conn, str(uuid.uuid4())) is False
    finally:
        conn.close()


def test_revoked_is_sticky_against_a_same_version_active_announce(registry_env):
    did = str(uuid.uuid4())
    _apply(registry_env, _device_ev(did))
    _apply(registry_env, _device_ev(did, status="revoked", row_version=1))
    assert _device(registry_env, did)["status"] == "revoked"
    _apply(registry_env, _device_ev(did, status="active", row_version=1))
    assert _device(registry_env, did)["status"] == "revoked", "a peer resurrected a revoked device"


def test_malformed_device_payload_is_parked_not_raised(registry_env):
    ev = _device_ev(str(uuid.uuid4()))
    del ev["payload"]["device_id"]
    assert _apply(registry_env, ev) is False
    rows = _q(registry_env, "SELECT reason FROM sync_apply_quarantine WHERE entity_type='device'")
    assert [r["reason"] for r in rows] == ["malformed_device"]


# ── admin_device ────────────────────────────────────────────────────────────

def _admin_ev(device_id, epoch, at="2026-03-01T00:00:00+00:00", **over):
    payload = {"device_id": device_id, "epoch": epoch, "assigned_at_utc": at,
               "assigned_by_user_uid": None, "assigned_by_device_id": None}
    payload.update(over)
    return {"entity_type": "admin_device", "entity_id": str(uuid.uuid4()),
            "event_type": "update", "payload": payload}


def test_assignment_before_its_device_row_is_stored_then_flag_lands_with_the_device(registry_env):
    did = str(uuid.uuid4())
    _apply(registry_env, _admin_ev(did, 1))
    conn = registry_env()
    try:
        assert get_assignment(conn, COMPANY)["device_id"] == did
    finally:
        conn.close()
    assert _q(registry_env, "SELECT COUNT(*) AS n FROM devices WHERE is_admin_device=1")[0]["n"] == 0
    _apply(registry_env, _device_ev(did))
    assert _device(registry_env, did)["is_admin_device"] == 1


def test_higher_epoch_wins_and_a_lower_one_is_ignored(registry_env):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    _apply(registry_env, _device_ev(a))
    _apply(registry_env, _device_ev(b))
    _apply(registry_env, _admin_ev(a, 1))
    _apply(registry_env, _admin_ev(b, 2))
    assert (_device(registry_env, a)["is_admin_device"], _device(registry_env, b)["is_admin_device"]) == (0, 1)
    _apply(registry_env, _admin_ev(a, 1, at="2026-01-01T00:00:00+00:00"))  # stale replay
    assert (_device(registry_env, a)["is_admin_device"], _device(registry_env, b)["is_admin_device"]) == (0, 1)


def test_malformed_admin_device_is_parked(registry_env):
    ev = _admin_ev(str(uuid.uuid4()), 1)
    ev["payload"]["epoch"] = 0
    assert _apply(registry_env, ev) is False
    rows = _q(registry_env, "SELECT reason FROM sync_apply_quarantine WHERE entity_type='admin_device'")
    assert [r["reason"] for r in rows] == ["malformed_admin_device"]


def test_applying_registry_events_never_emits(registry_env):
    did = str(uuid.uuid4())
    _apply(registry_env, _device_ev(did))
    _apply(registry_env, _admin_ev(did, 1))
    assert _q(registry_env, "SELECT COUNT(*) AS n FROM sync_outbox")[0]["n"] == 0


# ── a poison payload parks ONE event; it never wedges the stream ────────────
# Track S security review, extra item 1. `apply_pull_result` has no per-event
# savepoint, so before `_apply_event_guarded` an exception from identity's
# helpers aborted the whole batch and the cursor never advanced: the same
# poison event was re-pulled forever and nothing behind it ever applied.

def _pull(get_conn, events, cursor=7):
    conn = get_conn()
    try:
        _service(get_conn).apply_pull_result(conn, {"events": events, "cursor": cursor})
        conn.commit()
    finally:
        conn.close()


def _cursor(get_conn):
    return _q(get_conn, "SELECT last_seq FROM sync_cursor WHERE id=1")[0]["last_seq"]


@pytest.mark.parametrize("epoch", [2 ** 63 - 1, 10 ** 20])
def test_hostile_admin_epoch_parks_one_row_and_the_stream_keeps_applying(registry_env, epoch):
    """2**63-1 fits SQLite's integer but is the max: the next epoch would
    overflow. 10**20 does not fit at all (OverflowError on bind). Either must
    become exactly ONE quarantine row, and a valid device event AFTER it in the
    same pull must still apply, with the cursor advancing past both."""
    bad_target, good = str(uuid.uuid4()), str(uuid.uuid4())
    events = [_admin_ev(bad_target, epoch), _device_ev(good)]
    _pull(registry_env, events, cursor=9)
    rows = _q(registry_env,
              "SELECT reason, entity_type FROM sync_apply_quarantine WHERE entity_type='admin_device'")
    assert len(rows) == 1 and rows[0]["reason"].startswith("malformed_admin_device")
    assert _device(registry_env, good) is not None, "the event behind the poison one never applied"
    assert _cursor(registry_env) == 9, "the cursor did not advance: the stream is wedged"


@pytest.mark.parametrize("shape", ["missing", "null", "abc"])
def test_an_admin_epoch_that_is_not_a_number_parks_one_row_and_the_stream_keeps_applying(registry_env, shape):
    """Re-verifier item: `int(p.get("epoch"))` raises TypeError for a missing or
    null epoch and ValueError for "abc". Each must become exactly ONE
    quarantine row (never an exception out of the batch), a valid device event
    behind it must still apply, and the cursor must advance past both."""
    bad_target, good = str(uuid.uuid4()), str(uuid.uuid4())
    bad = _admin_ev(bad_target, None if shape == "null" else "abc")
    if shape == "missing":
        del bad["payload"]["epoch"]
    _pull(registry_env, [bad, _device_ev(good)], cursor=11)
    rows = _q(registry_env,
              "SELECT reason FROM sync_apply_quarantine WHERE entity_type='admin_device'")
    assert [r["reason"] for r in rows] == ["malformed_admin_device"], rows
    conn = registry_env()
    try:
        assert get_assignment(conn, COMPANY) is None, "a non-numeric epoch was stored as an assignment"
    finally:
        conn.close()
    assert _device(registry_env, good) is not None, "the event behind the poison one never applied"
    assert _cursor(registry_env) == 11, "the cursor did not advance: the stream is wedged"


def test_epoch_ceiling_allows_the_top_real_value_and_refuses_one_past_it(registry_env):
    """Both halves of the epoch bound: 2**31-1 is still a legitimate
    assignment (allow-half), 2**31 is refused (deny-half)."""
    ok_dev, bad_dev = str(uuid.uuid4()), str(uuid.uuid4())
    _pull(registry_env, [_admin_ev(ok_dev, 2 ** 31 - 1)], cursor=1)
    conn = registry_env()
    try:
        assert get_assignment(conn, COMPANY)["device_id"] == ok_dev
    finally:
        conn.close()
    _pull(registry_env, [_admin_ev(bad_dev, 2 ** 31)], cursor=2)
    rows = _q(registry_env, "SELECT reason FROM sync_apply_quarantine WHERE entity_type='admin_device'")
    assert [r["reason"] for r in rows] == ["malformed_admin_device"]
    conn = registry_env()
    try:
        assert get_assignment(conn, COMPANY)["device_id"] == ok_dev
    finally:
        conn.close()


def test_poison_event_rolls_back_only_its_own_writes(registry_env, monkeypatch):
    """The savepoint undoes the poison event's PARTIAL writes and nothing else:
    an event applied EARLIER in the same pull survives. The helper is replaced
    by one that writes a `devices` row and THEN raises, which is exactly the
    half-done state a real mid-helper failure leaves behind."""
    from commercial_runtime.sync import sync_service as ss

    first, half = str(uuid.uuid4()), str(uuid.uuid4())
    real = ss.apply_device_event

    def write_then_blow_up(conn, company_id, payload):
        if payload.get("device_id") != half:
            return real(conn, company_id, payload)
        real(conn, company_id, payload)  # the row lands...
        raise TypeError("boom after a partial write")  # ...then the helper dies

    monkeypatch.setattr(ss, "apply_device_event", write_then_blow_up)
    _pull(registry_env, [_device_ev(first), _device_ev(half)], cursor=3)
    assert _device(registry_env, first) is not None, "an earlier, good event was undone"
    assert _device(registry_env, half) is None, "the poison event's partial write survived"
    rows = _q(registry_env, "SELECT reason FROM sync_apply_quarantine")
    assert [r["reason"] for r in rows] == ["malformed_device"]


def test_hostile_user_and_device_payloads_park_too(registry_env):
    """The user branch and the device branch get the same net: a row_version
    that cannot bind (10**20) is parked, and the good event that follows
    applies."""
    uid, good = str(uuid.uuid4()), str(uuid.uuid4())
    events = [
        _user_ev(uid, row_version=10 ** 20),
        _device_ev(str(uuid.uuid4()), row_version=10 ** 20),
        _device_ev(good),
    ]
    _pull(registry_env, events, cursor=4)
    reasons = sorted(r["reason"] for r in _q(registry_env, "SELECT reason FROM sync_apply_quarantine"))
    assert len(reasons) == 2, reasons
    assert all(r.startswith("malformed_") for r in reasons)
    assert _device(registry_env, good) is not None
    assert _cursor(registry_env) == 4


def test_a_parked_poison_event_is_retried_harmlessly_and_stays_one_row(registry_env):
    bad = _admin_ev(str(uuid.uuid4()), 10 ** 20)
    _pull(registry_env, [bad], cursor=1)
    _pull(registry_env, [], cursor=2)  # retry tick: re-fails, must not multiply or raise
    assert len(_q(registry_env, "SELECT 1 FROM sync_apply_quarantine")) == 1
    assert _cursor(registry_env) == 2


def test_the_user_branch_still_raises_loudly_on_an_unexpected_constraint(registry_env):
    """Deliberate wave-A behaviour the new net must NOT swallow: an
    IntegrityError that is neither email nor employee_id nor a missing
    required field is a real bug and stays loud, never quietly parked.

    F3a (vYM2): this test used to use a NOT NULL on `password_hash` as its
    "unexpected constraint". That case is now DELIBERATELY parked as
    `malformed_user` (a missing required field is a malformed payload, not a
    code bug, and raising stopped the whole ordered registry stream -- proved
    in test_money_sync_engine_fixes.py). The unexpected constraint is
    therefore produced by a trigger that raises a constraint error of a kind
    the handler does not know. WHAT THIS CAN NO LONGER CATCH: a NOT NULL
    failure staying loud -- that behaviour was reversed on purpose."""
    import sqlite3
    conn = registry_env()
    try:
        conn.execute(
            "CREATE TRIGGER f3a_unexpected_constraint BEFORE INSERT ON users "
            "WHEN NEW.language = 'zz' BEGIN "
            "SELECT RAISE(ABORT, 'CHECK constraint failed: f3a_unexpected'); END")
        conn.commit()
    finally:
        conn.close()
    uid = str(uuid.uuid4())
    with pytest.raises(sqlite3.IntegrityError):
        _pull(registry_env, [_user_ev(uid, language="zz")], cursor=5)
    assert _q(registry_env, "SELECT 1 FROM sync_apply_quarantine") == [], (
        "an unexpected constraint was quietly parked instead of staying loud")


# ── H3 / L3 of the 2026-09-29 security review: the receiving side ───────────

def _perm_ev(user_uid, subsystem, level="full"):
    return {"entity_type": "user_permission", "entity_id": f"{user_uid}:{subsystem}",
            "event_type": "update",
            "payload": {"user_uid": user_uid, "subsystem": subsystem, "access_level": level}}


def _set_perm(get_conn, user_id, subsystem, level):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id, subsystem) DO UPDATE SET access_level=excluded.access_level",
            (str(uuid.uuid4()), user_id, subsystem, level))
        conn.commit()
    finally:
        conn.close()


def _level(get_conn, user_id, subsystem):
    rows = _q(get_conn, "SELECT access_level FROM user_permissions WHERE user_id=? AND subsystem=?",
              (user_id, subsystem))
    return rows[0]["access_level"] if rows else None


def _audit_actions(get_conn):
    return [r["action"] for r in _q(get_conn, "SELECT action FROM audit_logs")]


def test_synced_permission_cannot_give_a_cashier_the_cash_pair(registry_env):
    """Two events, cash.close then cash.approve, arrive on a till that never saw
    the conflicting request. The local routes refuse the pair; the apply must
    too: cash.approve ends at 'none', cash.close stays, one audit row, and
    nothing is queued for the wire. MUTATION-PROVEN: removing the
    `enforce_cash_pair_after_apply` call after the permission upsert leaves
    cash.approve at 'full' (RED)."""
    uid, user_id = _seed_user(registry_env, role="cashier", role_preset=None)
    _apply(registry_env, _perm_ev(uid, "retail.cash.close"))
    assert _level(registry_env, user_id, "retail.cash.close") == "full"
    _apply(registry_env, _perm_ev(uid, "retail.cash.approve"))
    assert _level(registry_env, user_id, "retail.cash.approve") == "none"
    assert _level(registry_env, user_id, "retail.cash.close") == "full"
    assert _audit_actions(registry_env) == ["CASH_PAIR_REVOKED_ON_RECEIPT"]
    assert _q(registry_env, "SELECT COUNT(*) AS n FROM sync_outbox")[0]["n"] == 0


def test_synced_cash_pair_is_left_alone_for_the_owner_and_for_a_single_grant(registry_env):
    """The ALLOW half: the admin holds every code by definition, and a person
    with cash.approve alone (no cash.close) is a legitimate manager. A guard
    that always stripped approve would pass the test above and break both."""
    admin_uid, admin_id = _seed_user(registry_env, role="admin", role_preset=None,
                                     employee_id="A1", email="a@x.com")
    _apply(registry_env, _perm_ev(admin_uid, "retail.cash.close"))
    _apply(registry_env, _perm_ev(admin_uid, "retail.cash.approve"))
    assert _level(registry_env, admin_id, "retail.cash.approve") == "full"

    mgr_uid, mgr_id = _seed_user(registry_env, role="manager", role_preset=None,
                                 employee_id="M1", email="m@x.com")
    _apply(registry_env, _perm_ev(mgr_uid, "retail.cash.approve"))
    assert _level(registry_env, mgr_id, "retail.cash.approve") == "full"
    assert _audit_actions(registry_env) == []


def test_a_synced_user_event_also_enforces_the_cash_pair(registry_env):
    """The pair can also arrive through the user row (the permission rows were
    already here, the role that makes them illegal is what arrives)."""
    uid, user_id = _seed_user(registry_env, role="cashier", role_preset=None)
    _set_perm(registry_env, user_id, "retail.cash.close", "full")
    _set_perm(registry_env, user_id, "retail.cash.approve", "full")
    _apply(registry_env, _user_ev(uid, role="cashier", role_preset=None))
    assert _level(registry_env, user_id, "retail.cash.approve") == "none"


def test_registry_apply_calls_the_cash_pair_check_once_per_applied_user_and_permission_event(
        registry_env, monkeypatch):
    """Counts CALLS, not outcomes: a 'none' at the end cannot tell 'the check ran
    and revoked' from 'nothing granted it in the first place'."""
    from commercial_runtime.sync import sync_service
    calls = []
    real = sync_service.enforce_cash_pair_after_apply

    def counting(conn, company_id, user_uid):
        calls.append((company_id, user_uid))
        return real(conn, company_id, user_uid)

    monkeypatch.setattr(sync_service, "enforce_cash_pair_after_apply", counting)
    uid, _ = _seed_user(registry_env, role="cashier", role_preset=None)
    _apply(registry_env, _user_ev(uid))
    _apply(registry_env, _perm_ev(uid, "retail.sell"))
    _apply(registry_env, _device_ev(str(uuid.uuid4())))  # not a user event
    assert calls == [(COMPANY, uid), (COMPANY, uid)], calls


def test_hostile_display_name_and_unknown_preset_are_cleaned_on_receipt(registry_env, monkeypatch):
    """L3: a peer cannot store a bidi override or a line break in a name, and an
    unknown preset (junk or a newer build's) is treated as ABSENT, so the local
    value survives. MUTATION-PROVEN: removing the `sanitize_synced_user_fields`
    call stores the raw name (RED)."""
    uid, _ = _seed_user(registry_env, role="manager")
    _apply(registry_env, _user_ev(uid, role="manager", display_name="Sara‮\nAli",
                                  role_preset="superuser"))
    row = _q(registry_env, "SELECT display_name, role_preset FROM users WHERE uid=?", (uid,))[0]
    assert row["display_name"] == "Sara Ali", repr(row["display_name"])
    assert row["role_preset"] == "accountant", "an unknown preset overwrote the local one"
