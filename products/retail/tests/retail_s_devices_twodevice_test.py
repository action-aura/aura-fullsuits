"""Track S + registry v9 -- THE COMPUTERS LIST across two REAL PROCESSES.

Two computers of one shop, both on the NEW build, each with its own data
directory and its own process per call, meeting only through a registry relay.
This is the "which computer is the main one, what is it called, who was
removed" part of the shop, and every rule here is one a single-process test
cannot see: the flag, the names and the removals only matter if they cross a
process boundary and land on the OTHER computer's database.

WHAT THIS FILE PROVES:

  1. After each announces itself and they exchange, both list the SAME two
     computers, exactly one of them is the main one, and `is_this_device` points
     at the right row on each side.
  2. Only the main computer can rename another: the name crosses to the other
     computer, and the other computer's attempt to rename the main one is
     refused (403 NOT_ADMIN_DEVICE) and changes nothing anywhere.
  3. Removing a computer crosses over; the main computer cannot remove itself
     (409 CANNOT_REVOKE_MAIN); a non-main computer cannot remove anyone; the
     removed one comes back only through reinstate, and reinstating a computer
     that is not removed is refused (409 DEVICE_NOT_REVOKED).
  4. Handing the main role over moves the flag on BOTH sides with exactly one
     main afterwards, the old main loses its powers, and a second claim is
     refused (409 ADMIN_DEVICE_ALREADY_CLAIMED).
  5. Take-over: refused while the main was heard from in the last 7 days; after
     8 days of silence it works, and when the old main next syncs it learns it
     is no longer main WITHOUT anyone touching it (higher epoch wins).
  6. Boot re-announce: a computer the other one forgot is learned again the
     next time that computer starts (the real boot seam, not a test call).

Run (one file per process):
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \
        products/retail/tests/retail_s_devices_twodevice_test.py -q
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from _accept_harness import run_accept_device  # noqa: E402

PY = sys.executable
LICENCE = "LIC-DEVICES-TEST-1"


class Pair:
    """Computer A and computer B of ONE shop, both on the code under test."""

    def __init__(self, tmp_path: Path):
        self.dirs = {"a": tmp_path / "a", "b": tmp_path / "b"}
        self.relay = tmp_path / "relay.db"
        self.reg_relay = tmp_path / "registry_relay.db"
        self.admin_email = None
        self.admin_password = None
        self.ids = {}

    def run(self, which: str, actions: list) -> list:
        return run_accept_device(
            PY, self.dirs[which], self.relay, actions, timeout=180,
            options={"registry_relay": str(self.reg_relay), "arm_provider": True},
        )

    def login(self, which: str, actions: list) -> list:
        """Signs the owner in on `which`, then runs `actions`; returns the results
        of `actions` only."""
        out = self.run(which, [{"op": "login", "email": self.admin_email,
                                "password": self.admin_password}, *actions])
        return out[1:]

    def sync(self, sender: str, receiver: str) -> None:
        self.run(sender, [{"op": "reg_push"}])
        self.run(receiver, [{"op": "reg_pull"}])

    def listing(self, which: str) -> list:
        [res] = self.login(which, [{"op": "devices_list"}])
        assert res["status_code"] == 200, res
        return res["body"]["devices"]

    def device_row(self, which: str, device_id: str) -> dict:
        [res] = self.run(which, [{"op": "reg_select", "sql": "SELECT * FROM devices WHERE id=?",
                                  "params": [device_id]}])
        return res["rows"][0] if res["rows"] else None

    def epoch(self, which: str) -> int:
        [res] = self.run(which, [{"op": "reg_select", "sql": "SELECT epoch FROM admin_device_assignment"}])
        return res["rows"][0]["epoch"]


def _mains(devices: list) -> set:
    return {d["id"] for d in devices if d["is_admin_device"]}


@pytest.fixture
def pair(tmp_path) -> Pair:
    p = Pair(tmp_path)
    ident_a, created, _lic = p.run("a", [
        {"op": "code_identity"},
        {"op": "create_shop"},
        {"op": "activate_shared_licence", "license_public_id": LICENCE},
    ])
    p.admin_email, p.admin_password = created["email"], created["password"]
    # The harness founds a shop by inserting the owner row directly, which skips
    # the ADMIN_CREATED audit row that the real founding route (`create_admin`)
    # writes -- and that row is the ONLY thing that tells `is_joined_device`
    # this computer founded the shop rather than joined it. Without it the
    # founder is (correctly, fail-closed) refused the main role. Plant the row a
    # real first run would have left, on A only: B is the joiner and must not
    # have it.
    p.run("a", [{"op": "reg_exec",
                 "sql": "INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, entity_id) "
                        "VALUES (?, ?, NULL, 'ADMIN_CREATED', 'user', ?)",
                 "params": ["founder-audit-row", created["company_id"], "founder"]}])
    assert ident_a["registry_schema_version"] >= 9, ident_a
    assert "device" in ident_a["registry_entity_types"], ident_a

    # No route creates the FIRST owner, so it never entered the outbox. Give B
    # the same owner row directly (same company, same password) -- the way a
    # second computer would receive it from the owner's account sync.
    [rows] = p.run("a", [{"op": "reg_select", "sql": "SELECT * FROM users WHERE email=?",
                          "params": [p.admin_email]}])
    admin = rows["rows"][0]
    ident_b, b_cols = p.run("b", [
        {"op": "code_identity"},
        {"op": "reg_select", "sql": "SELECT name FROM pragma_table_info('users')"},
    ])
    assert ident_b["registry_schema_version"] == ident_a["registry_schema_version"], (ident_a, ident_b)
    assert ident_a["suite_root"] == ident_b["suite_root"], (
        "the two computers must run the SAME code for this file to mean anything")
    cols = [c["name"] for c in b_cols["rows"] if c["name"] in admin]
    p.run("b", [
        {"op": "reg_exec",
         "sql": f"INSERT INTO users ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
         "params": [admin[c] for c in cols]},
        {"op": "activate_shared_licence", "license_public_id": LICENCE},
    ])

    [ann_a, claim, _push] = p.login("a", [
        {"op": "announce", "reason": "boot"},
        {"op": "claim_main"},
        {"op": "reg_push"},
    ])
    assert claim["status_code"] == 200, claim
    [ann_b, _push_b] = p.login("b", [{"op": "announce", "reason": "boot"}, {"op": "reg_push"}])
    p.ids = {"a": ann_a["device"]["id"], "b": ann_b["device"]["id"]}
    assert p.ids["a"] != p.ids["b"], "both computers came up with the same device id"
    p.run("b", [{"op": "reg_pull"}])
    p.run("a", [{"op": "reg_pull"}])
    return p


def test_both_computers_list_the_same_two_and_only_a_is_main(pair):
    a_id, b_id = pair.ids["a"], pair.ids["b"]
    on_a, on_b = pair.listing("a"), pair.listing("b")

    assert {d["id"] for d in on_a} == {a_id, b_id}, on_a
    assert {d["id"] for d in on_b} == {a_id, b_id}, on_b
    assert _mains(on_a) == {a_id}, on_a
    assert _mains(on_b) == {a_id}, (
        f"B did not learn who the main computer is: {[(d['id'], d['is_admin_device']) for d in on_b]}")
    assert {d["id"] for d in on_a if d["is_this_device"]} == {a_id}
    assert {d["id"] for d in on_b if d["is_this_device"]} == {b_id}


def test_only_the_main_computer_can_rename_and_the_name_crosses_over(pair):
    a_id, b_id = pair.ids["a"], pair.ids["b"]

    [put] = pair.login("a", [{"op": "device_label", "device_id": b_id, "label": "Back counter"}])
    assert put["status_code"] == 200, put
    pair.sync("a", "b")
    on_a_row, on_b_row = pair.device_row("a", b_id), pair.device_row("b", b_id)
    assert on_b_row["device_label"] == "Back counter", (
        f"the new name never reached the computer it names. A's copy of B: "
        f"version {on_a_row['row_version']} label {on_a_row['device_label']!r}; "
        f"B's own row: version {on_b_row['row_version']} label {on_b_row['device_label']!r}")

    # B is not the main computer: it may not rename A, and nothing may change.
    a_label_before = pair.device_row("a", a_id)["device_label"]
    [refused] = pair.login("b", [{"op": "device_label", "device_id": a_id, "label": "Hijacked"}])
    assert refused["status_code"] == 403, refused
    assert refused["body"]["code"] == "NOT_ADMIN_DEVICE", refused
    pair.sync("b", "a")
    assert pair.device_row("a", a_id)["device_label"] == a_label_before
    assert pair.device_row("b", a_id)["device_label"] == a_label_before


def test_removing_and_reinstating_a_computer_crosses_over_and_the_main_cannot_be_removed(pair):
    a_id, b_id = pair.ids["a"], pair.ids["b"]

    [b_cannot] = pair.login("b", [{"op": "device_revoke", "device_id": a_id}])
    assert b_cannot["status_code"] == 403 and b_cannot["body"]["code"] == "NOT_ADMIN_DEVICE", b_cannot
    [a_self] = pair.login("a", [{"op": "device_revoke", "device_id": a_id}])
    assert a_self["status_code"] == 409 and a_self["body"]["code"] == "CANNOT_REVOKE_MAIN", a_self
    assert pair.device_row("a", a_id)["status"] == "active"

    # Reinstating a computer that was never removed is refused.
    [not_removed] = pair.login("a", [{"op": "http", "method": "POST",
                                      "path": f"/api/devices/{b_id}/reinstate"}])
    assert not_removed["status_code"] == 409, not_removed
    assert not_removed["body"]["code"] == "DEVICE_NOT_REVOKED", not_removed

    [revoked] = pair.login("a", [{"op": "device_revoke", "device_id": b_id}])
    assert revoked["status_code"] == 200, revoked
    pair.sync("a", "b")
    assert pair.device_row("b", b_id)["status"] == "revoked", (
        "the removal never reached the computer that was removed")

    [back] = pair.login("a", [{"op": "http", "method": "POST",
                               "path": f"/api/devices/{b_id}/reinstate"}])
    assert back["status_code"] == 200, back
    pair.sync("a", "b")
    a_copy, b_own = pair.device_row("a", b_id), pair.device_row("b", b_id)
    assert b_own["status"] == "active", (
        f"reinstate did not reach the computer that had been removed. A's copy of B: "
        f"version {a_copy['row_version']} status {a_copy['status']!r}; "
        f"B's own row: version {b_own['row_version']} status {b_own['status']!r}")
    assert pair.device_row("a", b_id)["status"] == "active"


def test_handing_over_the_main_role_moves_the_flag_on_both_sides(pair):
    a_id, b_id = pair.ids["a"], pair.ids["b"]
    epoch_before = pair.epoch("a")

    # Only the main computer may hand the role over.
    [b_cannot] = pair.login("b", [{"op": "transfer_main", "device_id": b_id}])
    assert b_cannot["status_code"] == 403 and b_cannot["body"]["code"] == "NOT_ADMIN_DEVICE", b_cannot

    [moved] = pair.login("a", [{"op": "transfer_main", "device_id": b_id}])
    assert moved["status_code"] == 200, moved
    pair.sync("a", "b")

    on_a, on_b = pair.listing("a"), pair.listing("b")
    assert _mains(on_a) == {b_id}, on_a
    assert _mains(on_b) == {b_id}, on_b
    assert pair.epoch("a") == pair.epoch("b") > epoch_before

    # The old main lost its powers; the new one has them.
    [a_label] = pair.login("a", [{"op": "device_label", "device_id": b_id, "label": "Nope"}])
    assert a_label["status_code"] == 403 and a_label["body"]["code"] == "NOT_ADMIN_DEVICE", a_label
    [b_label] = pair.login("b", [{"op": "device_label", "device_id": a_id, "label": "Old counter"}])
    assert b_label["status_code"] == 200, b_label

    # And the role cannot simply be grabbed back while another computer holds it.
    [claim] = pair.login("a", [{"op": "claim_main"}])
    assert claim["status_code"] == 409, claim
    assert claim["body"]["code"] == "ADMIN_DEVICE_ALREADY_CLAIMED", claim


def test_take_over_needs_a_week_of_silence_and_the_old_main_steps_down_by_itself(pair):
    a_id, b_id = pair.ids["a"], pair.ids["b"]
    pw = pair.admin_password

    # Fresh: A was heard from just now. Wrong password and missing phrase fail first.
    [wrong] = pair.login("b", [{"op": "take_over", "password": "not-the-password"}])
    assert wrong["status_code"] == 403 and wrong["body"]["code"] == "TAKEOVER_PASSWORD_INCORRECT", wrong
    [no_phrase] = pair.login("b", [{"op": "take_over", "password": pw, "confirm": "yes"}])
    assert no_phrase["status_code"] == 400 and no_phrase["body"]["code"] == "TAKEOVER_CONFIRM_REQUIRED", no_phrase
    [recent] = pair.login("b", [{"op": "take_over", "password": pw}])
    assert recent["status_code"] == 409, recent
    assert recent["body"]["code"] == "TAKEOVER_MAIN_SEEN_RECENTLY", recent
    assert _mains(pair.listing("b")) == {a_id}, "a refused take-over still moved the main role"

    # A goes silent for 8 days, as B knows it: every sign of life B holds for A
    # (its own row AND the assignment that names it) is 8 days old.
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    pair.run("b", [
        {"op": "reg_exec",
         "sql": "UPDATE devices SET last_seen_at=?, updated_at_utc=?, first_seen_at=? WHERE id=?",
         "params": [old, old, old, a_id]},
        {"op": "reg_exec", "sql": "UPDATE admin_device_assignment SET assigned_at_utc=?", "params": [old]},
    ])
    epoch_before = pair.epoch("b")
    [took] = pair.login("b", [{"op": "take_over", "password": pw}])
    assert took["status_code"] == 200, took
    assert _mains(pair.listing("b")) == {b_id}
    assert pair.epoch("b") == epoch_before + 1

    # A comes back online and syncs: it loses by epoch and steps down alone.
    assert _mains(pair.listing("a")) == {a_id}, "fixture: A must still believe it is main"
    # S-AUTH (AUDIT-033): a computer judges a take-over against ITS OWN record of
    # having been heard from -- a main that has been syncing keeps refusing one, a
    # main that was away does not. "A goes silent for 8 days" is therefore also true
    # of A's own row: nothing of A's has been echoed back to A for 8 days.
    pair.run("a", [{"op": "reg_exec",
                    "sql": "UPDATE sync_signers SET last_signed_seen_at_utc=? WHERE device_id=?",
                    "params": [old, a_id]}])
    pair.sync("b", "a")
    on_a = pair.listing("a")
    assert _mains(on_a) == {b_id}, (
        f"the old main did not step down after the take-over reached it: "
        f"{[(d['id'], d['is_admin_device']) for d in on_a]}")


def test_a_computer_the_other_forgot_is_learned_again_at_the_next_real_boot(pair):
    a_id, b_id = pair.ids["a"], pair.ids["b"]

    # B loses its copy of A (a restored backup, a wiped registry -- any way a
    # peer can be missing from a list).
    pair.run("b", [{"op": "reg_exec", "sql": "DELETE FROM devices WHERE id=?", "params": [a_id]}])
    assert pair.device_row("b", a_id) is None, "fixture: B must have forgotten A"

    # A simply starts again. Nothing in this test announces for it: the process
    # start IS the boot seam.
    [outbox, _push] = pair.run("a", [{"op": "reg_outbox"}, {"op": "reg_push"}])
    boot_events = [r for r in outbox["rows"] if r["entity_type"] == "device"
                   and r["entity_id"] == a_id]
    assert len(boot_events) >= 2, (
        f"a restarted computer queued no fresh announcement of itself: {outbox['rows']}")

    pair.run("b", [{"op": "reg_pull"}])
    row = pair.device_row("b", a_id)
    assert row is not None, "B never learned about A again after A restarted"
    assert row["status"] == "active"


# AUDIT-033 / S-AUTH: authority events (users, devices, the main-computer
# assignment, till allocations, numbering books, release acks) are refused unless
# a member of the shop signed them. Every simulated computer here is a real
# process with its own app-data directory, so each one is given what a real
# activated till has: a device key, an Owner-signed lease and a trust store (see
# `_provision_signed_identity` in _accept_device.py). Signing and verification are
# the production code path reading that app data -- nothing is stubbed.
@pytest.fixture(autouse=True)
def _real_signing_identities(monkeypatch):
    monkeypatch.setenv("AURA_ACCEPT_SIGNED", "1")
    monkeypatch.setenv("AURA_ACCEPT_SIGNED_LICENCE", "LIC-DEVICES-TEST-1")
