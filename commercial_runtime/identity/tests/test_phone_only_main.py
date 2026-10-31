"""Phone-only shops (owner decision 2026-10-31): an ANDROID phone may be the
main computer ONLY while the shop has no Windows computer registered.

WHY THIS FILE EXISTS. The old rule was "only Windows may be main". The new
customer flow lets a shop run on two phones; so the role step must be able to
make a phone the boss device -- but the moment a PC is registered the role
belongs on the PC. The danger is not the allow-half (obvious) but the
deny-half silently disappearing: every test here pairs "phone allowed with no
PC" with "phone refused with a PC", through the registry helpers AND the
sync-apply and flag-recompute paths. A phone that was legitimately main must
also STAY main (on every device) when a PC joins later.

Run: python -m pytest commercial_runtime/identity/tests/test_phone_only_main.py -q
"""
import sqlite3

import pytest

from commercial_runtime.identity import device_registry
from commercial_runtime.identity.device_registry import (
    apply_admin_assignment,
    apply_device_event,
    apply_identity_device_schema,
    claim_admin_device_with_assignment,
    get_assignment,
    get_device,
    recompute_admin_flag,
    transfer_admin_device_with_assignment,
    upsert_local_device,
    windows_device_registered,
)
from commercial_runtime.identity.device_sync_schema import apply_device_sync_schema

CO = "company-1"
LONG_AGO = "2026-08-01T00:00:00+00:00"
ASSIGNED = "2026-10-01T00:00:00+00:00"
LATER = "2026-10-20T00:00:00+00:00"


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "registry.db"))
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE users (id TEXT PRIMARY KEY, uid TEXT, status TEXT NOT NULL DEFAULT 'active',"
              " session_version INTEGER NOT NULL DEFAULT 1)")
    c.execute("CREATE TABLE sync_outbox (id TEXT PRIMARY KEY, entity_type TEXT, entity_id TEXT,"
              " event_type TEXT, payload TEXT, created_at TEXT)")
    apply_identity_device_schema(c)
    apply_device_sync_schema(c)
    c.commit()
    yield c
    c.close()


def _seen(conn, device_id, when):
    conn.execute("UPDATE devices SET first_seen_at=? WHERE id=?", (when, device_id))
    conn.commit()


def _flagged(conn):
    return sorted(r[0] for r in conn.execute(
        "SELECT id FROM devices WHERE company_id=? AND is_admin_device=1", (CO,)).fetchall())


# -- the question itself ------------------------------------------------------

def test_windows_registered_is_false_for_a_phone_only_shop_and_true_with_a_pc(conn):
    upsert_local_device(conn, "phone-a", CO, platform="ANDROID")
    upsert_local_device(conn, "phone-b", CO, platform="ANDROID")
    assert windows_device_registered(conn, CO) is False
    upsert_local_device(conn, "pc", CO, platform="WINDOWS")
    assert windows_device_registered(conn, CO) is True


def test_a_revoked_or_foreign_windows_computer_does_not_count(conn):
    upsert_local_device(conn, "old-pc", CO, platform="WINDOWS")
    upsert_local_device(conn, "other-shop-pc", "other-co", platform="WINDOWS")
    conn.execute("UPDATE devices SET status='revoked' WHERE id='old-pc'")
    conn.commit()
    assert windows_device_registered(conn, CO) is False


def test_a_device_that_never_announced_its_platform_counts_as_windows(conn):
    upsert_local_device(conn, "legacy-pc", CO)            # platform NULL
    assert windows_device_registered(conn, CO) is True


# -- claim / transfer (the user-facing paths) ---------------------------------

def test_a_phone_can_claim_main_in_a_phone_only_shop(conn):
    upsert_local_device(conn, "phone-a", CO, platform="ANDROID")
    claimed = claim_admin_device_with_assignment(conn, CO, "phone-a", by_user_uid="u1")
    assert claimed["is_admin_device"] in (1, True)
    assert get_assignment(conn, CO)["device_id"] == "phone-a"
    assert _flagged(conn) == ["phone-a"]


def test_a_phone_cannot_claim_main_when_the_shop_has_a_windows_computer(conn):
    upsert_local_device(conn, "pc", CO, platform="WINDOWS")
    upsert_local_device(conn, "phone-a", CO, platform="ANDROID")
    with pytest.raises(ValueError, match=device_registry.REASON_ADMIN_TARGET_INELIGIBLE):
        claim_admin_device_with_assignment(conn, CO, "phone-a")
    assert get_assignment(conn, CO) is None and _flagged(conn) == []


def test_main_can_move_from_one_phone_to_another_in_a_phone_only_shop(conn):
    upsert_local_device(conn, "phone-a", CO, platform="ANDROID")
    upsert_local_device(conn, "phone-b", CO, platform="ANDROID")
    claim_admin_device_with_assignment(conn, CO, "phone-a")
    transfer_admin_device_with_assignment(conn, CO, "phone-b")
    assert _flagged(conn) == ["phone-b"] and get_assignment(conn, CO)["epoch"] == 2


def test_main_cannot_move_to_a_phone_once_a_pc_has_joined(conn):
    upsert_local_device(conn, "pc", CO, platform="WINDOWS")
    upsert_local_device(conn, "phone-b", CO, platform="ANDROID")
    claim_admin_device_with_assignment(conn, CO, "pc")
    with pytest.raises(ValueError, match=device_registry.REASON_ADMIN_TARGET_INELIGIBLE):
        transfer_admin_device_with_assignment(conn, CO, "phone-b")
    assert _flagged(conn) == ["pc"]


# -- sync apply + recompute ---------------------------------------------------

def test_apply_accepts_a_phone_named_main_when_no_pc_was_registered_then(conn):
    upsert_local_device(conn, "phone-a", CO, platform="ANDROID")
    assert apply_admin_assignment(
        conn, CO, {"device_id": "phone-a", "epoch": 1, "assigned_at_utc": ASSIGNED}) is True
    assert _flagged(conn) == ["phone-a"]


def test_apply_refuses_a_phone_named_main_when_a_pc_was_already_registered(conn):
    upsert_local_device(conn, "pc", CO, platform="WINDOWS")
    _seen(conn, "pc", LONG_AGO)
    upsert_local_device(conn, "phone-a", CO, platform="ANDROID")
    with pytest.raises(ValueError, match=device_registry.REASON_ADMIN_TARGET_INELIGIBLE):
        apply_admin_assignment(conn, CO, {"device_id": "phone-a", "epoch": 1, "assigned_at_utc": ASSIGNED})
    assert get_assignment(conn, CO) is None


def test_a_phone_that_was_main_stays_main_when_a_pc_joins_later(conn):
    """THE STAYS-MAIN HALF. Without the 'as of the assignment' cutoff, adding
    the shop's first PC would clear the phone's flag and leave NO main at all
    (and, on the PC, let it claim a second one)."""
    upsert_local_device(conn, "phone-a", CO, platform="ANDROID")
    apply_admin_assignment(conn, CO, {"device_id": "phone-a", "epoch": 1, "assigned_at_utc": ASSIGNED})
    upsert_local_device(conn, "pc", CO, platform="WINDOWS")
    _seen(conn, "pc", LATER)
    recompute_admin_flag(conn, CO)
    conn.commit()
    assert _flagged(conn) == ["phone-a"]
    # ...and the PC can then be handed the role the normal way.
    transfer_admin_device_with_assignment(conn, CO, "pc")
    assert _flagged(conn) == ["pc"]


def test_an_assignment_that_beat_the_phone_row_flags_it_only_in_a_phone_only_shop(conn):
    assert apply_admin_assignment(
        conn, CO, {"device_id": "late-phone", "epoch": 1, "assigned_at_utc": ASSIGNED}) is True
    apply_device_event(conn, CO, {"device_id": "late-phone", "device_label": "Phone",
                                  "platform": "ANDROID", "status": "active", "row_version": 1})
    assert get_device(conn, "late-phone")["platform"] == "ANDROID"
    assert _flagged(conn) == ["late-phone"]
