"""AUDIT-033 / S-AUTH fix round -- tests that need helpers or shapes the fix added.

The verifier's own attack tests (RED at fafaf7cc) are in
test_sync_authority_attacks.py; this file pins the REST of the fix in both
directions (deny and allow), with real signatures against a real registry.db:

  * finding #2  every admin row is owner-protected (permissions too)
  * device      a device cannot lift its own admin_row_version past the main's
  * finding #3  the harmless owner path writes display_name/language only
  * finding #4  a main-signed owner account beats a squat, a replay does not
  * #6/#7/#8    the quarantine shapes (keyed, and the old-shape fallback),
                evidence stickiness, supersede only by the main / the author

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/sync/tests/test_sync_authority_fixround.py -q
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from commercial_runtime.sync import authority
from commercial_runtime.sync.tests._signing import install_main
from commercial_runtime.sync.tests.test_sync_authority_rules import (
    CASHIER, COMPANY, OWNER, Shop, _device_ev, _perm)


@pytest.fixture
def shop(tmp_path, monkeypatch):
    return Shop(tmp_path, monkeypatch)


def _owner_account(uid="real-owner", **over):
    payload = {"uid": uid, "employee_id": "ADMIN-0007", "email": "real@x.com", "role": "admin",
               "status": "active", "require_password_change": 0, "language": "en",
               "password_hash": "REAL", "pin_hash": "R", "row_version": 1,
               "updated_at_utc": "2026-02-01T00:00:00+00:00", "session_version": 1}
    payload.update(over)
    return {"entity_type": "user", "entity_id": uid, "event_type": "update", "payload": payload}


def _user_ev(uid, **over):
    ev = _owner_account(uid, role="cashier", employee_id=f"EMP-{uid}", email=f"{uid}@x.com",
                        password_hash=f"H-{uid}")
    ev["payload"].update(over)
    return ev


def _main_rv(shop, uid):
    return shop.q("SELECT main_row_version FROM users WHERE uid=?", (uid,))[0]["main_row_version"]


# ═══ finding #2: every admin row is owner-protected ═══════════════════════════

def test_both_admin_rows_permissions_are_main_only(shop):
    """A device with TWO admin rows (its own first-run placeholder OWNER plus the
    shop owner's real account that the main introduced): a permission event for
    EITHER is the owner's permission and needs the main. At fafaf7cc only one row
    was protected (`LIMIT 1`)."""
    assert shop.apply(_owner_account("real-owner"), shop.M) is True
    for uid in (OWNER, "real-owner"):
        assert shop.apply(_perm(uid, "none"), shop.A) is False, uid
    assert shop.parked("user_permission") == ["owner_change_needs_main"] * 2
    assert shop.q("SELECT COUNT(*) AS n FROM user_permissions")[0]["n"] == 0
    # the allow half: the main may
    assert shop.apply(_perm("real-owner", "none"), shop.M) is True


def test_a_soft_deleted_owner_row_is_still_protected(shop):
    """No `deleted_at_utc` filter: a tombstoned owner is still the owner's row."""
    conn = shop.get_conn()
    conn.execute("UPDATE users SET deleted_at_utc='2026-01-02T00:00:00+00:00' WHERE uid=?", (OWNER,))
    conn.commit()
    conn.close()
    assert shop.apply(shop.user_event(OWNER, password_hash="ATTACKER-HASH"), shop.A) is False
    assert shop.parked("user") == ["owner_change_needs_main"]


# ═══ device: admin_row_version cannot be self-advanced ════════════════════════

def test_a_device_cannot_lift_its_own_admin_row_version(shop):
    """`admin_row_version` is the OWNER's version of a device's name/status. A
    device that sends itself with a higher one (existing row) is claiming an owner
    decision, and would freeze the main's later rename on every receiver."""
    assert shop.apply(_device_ev(shop.B.dev, row_version=3, admin_row_version=5), shop.B) is False
    assert shop.parked("device") == ["device_change_needs_main"]
    assert shop.q("SELECT admin_row_version FROM devices WHERE id=?", (shop.B.dev,))[0]["admin_row_version"] == 1


def test_a_device_new_to_this_receiver_cannot_arrive_with_a_huge_admin_row_version(shop):
    new = shop.world.new_device("dev-fresh")
    assert shop.apply(_device_ev(new.dev, row_version=1, admin_row_version=10 ** 9), new) is False
    assert shop.parked("device") == ["device_change_needs_main"]
    assert shop.q("SELECT id FROM devices WHERE id=?", (new.dev,)) == []


def test_a_device_announcing_itself_at_the_same_admin_version_is_fine(shop):
    """The allow half: a new app_version at the SAME admin version, and a first
    announcement at version 1."""
    assert shop.apply(_device_ev(shop.B.dev, row_version=3, admin_row_version=1, app_version="7.7.7"),
                      shop.B) is True
    assert shop.q("SELECT app_version FROM devices WHERE id=?", (shop.B.dev,))[0]["app_version"] == "7.7.7"
    new = shop.world.new_device("dev-fresh")
    assert shop.apply(_device_ev(new.dev, row_version=1, admin_row_version=1), new) is True


def test_the_main_may_still_bump_a_devices_admin_row_version(shop):
    assert shop.apply(_device_ev(shop.B.dev, row_version=3, admin_row_version=4, device_label="Renamed"),
                      shop.M) is True
    assert shop.q("SELECT admin_row_version FROM devices WHERE id=?", (shop.B.dev,))[0]["admin_row_version"] == 4


# ═══ finding #3: the harmless owner path ══════════════════════════════════════

def test_a_harmless_owner_edit_by_a_non_main_writes_only_name_and_language(shop):
    ev = shop.user_event(OWNER, display_name="Renamed", language="ar", row_version=77,
                         updated_at_utc="2030-01-01T00:00:00+00:00", employee_id="ADMIN-0042")
    assert shop.apply(ev, shop.A) is True
    row = shop.user(OWNER)
    assert (row["display_name"], row["language"]) == ("Renamed", "ar")
    assert (row["row_version"], row["employee_id"], row["updated_at_utc"]) == \
        (1, "ADMIN-0001", "2026-01-01T00:00:00+00:00")


def test_the_main_edits_the_owner_through_the_normal_path_and_moves_the_version(shop):
    assert shop.apply(shop.user_event(OWNER, display_name="By Main"), shop.M) is True
    row = shop.user(OWNER)
    assert (row["display_name"], row["row_version"]) == ("By Main", 2)


def test_a_non_mains_edit_of_an_ordinary_user_still_bumps_the_version(shop):
    """Ordinary users keep plain version semantics: only the OWNER row is special."""
    assert shop.apply(shop.user_event(CASHIER, display_name="Edited", row_version=5), shop.A) is True
    assert shop.user(CASHIER)["row_version"] == 5


def test_a_non_mains_harmless_owner_edit_does_not_block_the_mains_next_change(shop):
    """The reason for the design: after a burst of non-main owner edits the main's
    next change (its local+1) still lands, because non-mains never advance the version."""
    for rv in (2, 3, 4, 10 ** 6):
        assert shop.apply(shop.user_event(OWNER, display_name=f"x{rv}", row_version=rv), shop.A) is True
    assert shop.apply(shop.user_event(OWNER, password_hash="MAIN-NEW", row_version=2), shop.M) is True
    assert shop.user(OWNER)["password_hash"] == "MAIN-NEW"


# ═══ finding #4: a main-signed owner account beats a squat, a replay does not ══

def test_the_mains_next_owner_change_applies_after_it_overrode_a_squat(shop):
    squat = _owner_account("real-owner", role="cashier", employee_id="EMP-0777",
                           password_hash="ATTACKER-HASH", row_version=10 ** 9)
    shop.apply(squat, shop.A)
    assert shop.apply(_owner_account("real-owner", row_version=1), shop.M) is True
    row = shop.user("real-owner")
    assert (row["role"], row["password_hash"], row["row_version"]) == ("admin", "REAL", 1)
    assert _main_rv(shop, "real-owner") == 1
    assert shop.apply(_owner_account("real-owner", row_version=2, password_hash="REAL-2"), shop.M) is True
    assert shop.user("real-owner")["password_hash"] == "REAL-2"


def test_a_replayed_old_admin_event_cannot_re_promote_a_user_the_main_demoted(shop):
    base = _user_ev("promoted", row_version=1)
    shop.apply(base, shop.M)
    promote = shop.world.sign(_user_ev("promoted", role="admin", row_version=2), shop.M)
    assert shop.apply(promote) is True
    assert shop.user("promoted")["role"] == "admin" and _main_rv(shop, "promoted") == 2
    assert shop.apply(_user_ev("promoted", role="cashier", row_version=3), shop.M) is True
    assert _main_rv(shop, "promoted") == 3
    shop.apply(promote)                                           # the replay
    assert shop.user("promoted")["role"] == "cashier", "a replayed old event re-promoted a demoted user"


def test_a_member_edit_in_between_does_not_help_a_replay(shop):
    shop.apply(_user_ev("promoted", row_version=1), shop.M)
    promote = shop.world.sign(_user_ev("promoted", role="admin", row_version=2), shop.M)
    shop.apply(promote)
    shop.apply(_user_ev("promoted", role="cashier", row_version=3), shop.M)
    assert shop.apply(_user_ev("promoted", display_name="member edit", row_version=4), shop.A) is True
    shop.apply(promote)
    assert shop.user("promoted")["role"] == "cashier"


def test_a_main_event_for_an_ordinary_user_never_overrides_a_higher_member_version(shop):
    """Ordinary users converge by plain version. Only 'this event makes someone an
    owner' may override a member-written row."""
    shop.apply(_user_ev("ordinary", row_version=1), shop.M)
    shop.apply(_user_ev("ordinary", display_name="member wrote this", row_version=9), shop.A)
    shop.apply(_user_ev("ordinary", display_name="main wrote this", row_version=3), shop.M)
    row = shop.user("ordinary")
    assert (row["display_name"], row["row_version"]) == ("member wrote this", 9)


def test_a_member_cannot_override_with_an_admin_event_even_at_a_higher_version(shop):
    """The override is for MAIN-signed events only (the member's admin event is
    refused outright by the rule: second_admin_refused)."""
    shop.apply(_user_ev("ordinary", row_version=1), shop.M)
    assert shop.apply(_user_ev("ordinary", role="admin", row_version=50), shop.A) is False
    assert shop.user("ordinary")["role"] == "cashier"


def test_a_registry_without_the_main_row_version_column_still_applies_users(shop):
    """A fixture registry hand-built before the column existed: no override, no stamp, no crash."""
    conn = shop.get_conn()
    try:
        conn.execute("ALTER TABLE users DROP COLUMN main_row_version")
        conn.commit()
    except Exception:   # noqa: BLE001 -- an SQLite without DROP COLUMN: skip the premise
        pytest.skip("SQLite too old for DROP COLUMN")
    finally:
        conn.close()
    assert shop.apply(shop.user_event(CASHIER, display_name="fine", row_version=3), shop.M) is True
    assert shop.user(CASHIER)["display_name"] == "fine"


# ═══ #7/#8 the quarantine: keyed shape and the old-shape fallback ═════════════

def _make_old_shape(shop):
    conn = shop.get_conn()
    conn.execute("DROP TABLE sync_apply_quarantine")
    conn.execute("""CREATE TABLE sync_apply_quarantine (
        entity_id TEXT NOT NULL, entity_type TEXT NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL,
        reason TEXT NOT NULL, detail TEXT, quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (entity_id, event_type))""")
    conn.commit()
    conn.close()


def _drop_main(shop):
    conn = shop.get_conn()
    conn.execute("DELETE FROM admin_device_assignment")
    conn.commit()
    conn.close()


def test_the_registry_quarantine_is_keyed_by_signature(shop):
    forged = shop.world.sign(shop.user_event(OWNER, password_hash="ATTACKER-HASH"), shop.A)
    shop.apply(forged)
    rows = shop.q("SELECT auth_key FROM sync_apply_quarantine")
    assert len(rows) == 1 and rows[0]["auth_key"] == authority.quarantine_auth_key(forged["payload"])
    assert rows[0]["auth_key"].startswith("sig:") and len(rows[0]["auth_key"]) == 36
    shop.apply({"entity_type": "user", "entity_id": OWNER, "event_type": "update", "payload": {"uid": OWNER}})
    assert [r["auth_key"] for r in shop.q("SELECT auth_key FROM sync_apply_quarantine")] == [rows[0]["auth_key"]]


def test_an_unsigned_event_alone_is_parked_under_the_empty_key(shop):
    shop.apply({"entity_type": "user", "entity_id": OWNER, "event_type": "update",
                "payload": {"uid": OWNER, "role": "cashier"}})
    assert [r["auth_key"] for r in shop.q("SELECT auth_key FROM sync_apply_quarantine")] == [""]


def test_old_shape_an_unsigned_copy_never_replaces_a_signed_one(shop):
    _make_old_shape(shop)
    forged = shop.user_event(OWNER, password_hash="ATTACKER-HASH")
    shop.apply(forged, shop.A)
    shop.apply({"entity_type": "user", "entity_id": OWNER, "event_type": "update", "payload": {"uid": OWNER}})
    rows = shop.q("SELECT reason, payload FROM sync_apply_quarantine")
    assert [r["reason"] for r in rows] == ["owner_change_needs_main"]
    assert "ATTACKER-HASH" in rows[0]["payload"]


def test_old_shape_the_first_parked_signed_event_is_kept_against_a_second_signer(shop, caplog):
    """Residual R6, written down: the old key cannot hold two rows, so between two
    DIFFERENT signers the first parked event wins -- and says so in the log."""
    _make_old_shape(shop)
    shop.apply(shop.user_event(OWNER, password_hash="FIRST-SIGNER"), shop.A)
    with caplog.at_level(logging.WARNING, logger="commercial_runtime.sync.sync_service"):
        shop.apply(shop.user_event(OWNER, password_hash="SECOND-SIGNER", row_version=9), shop.B)
    rows = shop.q("SELECT payload FROM sync_apply_quarantine")
    assert len(rows) == 1 and "FIRST-SIGNER" in rows[0]["payload"]
    assert any("keeping the first parked signed" in r.getMessage() for r in caplog.records)


def test_old_shape_evidence_is_never_downgraded_to_a_supersedable_reason(shop):
    _make_old_shape(shop)
    shop.apply(shop.user_event(OWNER, password_hash="ATTACKER-HASH", row_version=9), shop.A)
    assert shop.parked("user") == ["owner_change_needs_main"]
    _drop_main(shop)
    shop.pull()
    assert shop.parked("user") == ["owner_change_needs_main"]


def test_a_retry_that_still_parks_keeps_one_row_per_signed_event(shop):
    """Idempotence: pulling again re-parks the SAME key, it does not grow the pile."""
    shop.apply(shop.user_event(OWNER, password_hash="ATTACKER-HASH", row_version=9), shop.A)
    for _ in range(3):
        shop.pull()
    assert shop.parked("user") == ["owner_change_needs_main"]


# ═══ supersede: only the main (or the entity's own author) clears parked copies ═

def test_only_the_main_clears_parked_unsigned_copies_of_a_user(shop):
    shop.apply({"entity_type": "user", "entity_id": CASHIER, "event_type": "update",
                "payload": shop.user_event(CASHIER, display_name="old unsigned")["payload"]})
    assert shop.parked("user") == ["unsigned_authority_event"]
    # `_supersede_parked_unsigned` runs on the PULL path (`_apply_event_guarded`), so go through it
    shop.pull([shop.world.sign(shop.user_event(CASHIER, display_name="member edit", row_version=3), shop.A)])
    assert shop.user(CASHIER)["display_name"] == "member edit"
    assert shop.parked("user") == ["unsigned_authority_event"], "an ordinary member cleared a parked row"
    shop.pull([shop.world.sign(shop.user_event(CASHIER, display_name="main edit", row_version=4), shop.M)])
    assert shop.user(CASHIER)["display_name"] == "main edit"
    assert shop.parked("user") == []


def test_a_device_clears_parked_unsigned_copies_of_itself(shop):
    unsigned = _device_ev(shop.B.dev, row_version=2)
    shop.apply(unsigned)
    assert shop.parked("device") == ["unsigned_authority_event"]
    shop.pull([shop.world.sign(_device_ev(shop.B.dev, row_version=3), shop.B)])
    assert shop.parked("device") == []


def test_the_signer_flags_never_come_from_the_wire(shop):
    """A payload cannot grant itself `_authority_*`: those are set by the gate only."""
    shop.apply(shop.user_event(OWNER, display_name="p"))                      # a parked unsigned row (rv 2)
    ev = shop.world.sign(shop.user_event(OWNER, display_name="x", row_version=3), shop.A)
    ev["_authority_signer_is_main"] = True            # an attempt to be treated as the main
    ev["_authority_signer_dev"] = shop.M.dev
    ev["_authority_harmless_owner_edit"] = False      # ... and to skip the restricted write
    ev["_authority_verified"] = True
    shop.pull([ev])                                   # a harmless owner edit; A is not the main
    assert shop.parked("user") == ["unsigned_authority_event"], "a wire flag cleared the parked row"
    assert shop.user(OWNER)["row_version"] == 1, "a wire flag turned the restricted write into a full one"
