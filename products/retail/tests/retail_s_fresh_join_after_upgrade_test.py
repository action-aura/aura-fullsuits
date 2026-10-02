"""AUDIT-033 B2b -- THE PROOF: a computer that joins AFTER the signed-authority upgrade
still gets the shop's whole history, across THREE real processes.

THE PROBLEM THIS CLOSES. Relays keep history. A shop that ran for months before the
upgrade has its owner, cashiers, permissions, main-computer assignment and till
numbers sitting on the relay UNSIGNED. A brand-new computer pulling from cursor 0
used to receive only that, and (rightly) parked every one of those events as
`unsigned_authority_event`: no users, no permissions, no main computer, no till
numbers -- nobody could sign in on it.

WHAT THIS FILE DOES, IN ORDER:
  1. UNSIGNED PAST (signing switched off, exactly the old build's behaviour): computer
     A founds the shop, becomes the main computer, invites a cashier (with the
     cashier preset's permissions), switches readable numbers on (a till number and
     numbering books) and pushes. The relay now holds raw, unsigned authority events.
     The test READS THE RELAY to prove that: it does not take the setup's word.
  2. UPGRADE: A gets a signing identity and ticks the REAL app seam
     (`app._announce_device`, via the harness `app_announce` op). The re-announce runs
     and A pushes.
  3. FRESH JOIN: computer C, which has never seen the shop and holds only its company
     id (what a real join gives it), pulls the registry and then the retail stream
     from cursor 0. It must end with the owner and cashier rows, the cashier's
     permissions, the main-computer assignment and the till allocation; the owner
     must be able to SIGN IN on it through the real login route; and its quarantine
     must hold no `unsigned_authority_event` rows.
  4. DENY HALF: a third licensed computer B -- not the main one -- signs a forged
     event that makes a new account an admin. C must park it, not apply it. This is
     what stops the fix from being "accept everything when in doubt".

NON-VACUITY. Step 3 is first run against a computer that pulls the history WITHOUT
the re-announce having happened (`test_without_the_reannounce_a_fresh_computer_has_nothing`):
it must come up empty and parked. If that ever stops being true the "fix" is proving
nothing.

Run (one file per process):
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \
        products/retail/tests/retail_s_fresh_join_after_upgrade_test.py -q
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from _v42_shop import Shop  # noqa: E402

LICENCE = "LIC-FRESHJOIN-TEST-1"
CASHIER_EMAIL = "cashier@freshjoin.test"
FORGED_EMAIL = "evil-admin@freshjoin.test"
UNSIGNED = "unsigned_authority_event"


def _relay_events(path: Path) -> list:
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute("SELECT seq, payload FROM relay_events ORDER BY seq").fetchall()
    finally:
        conn.close()
    return [{**json.loads(p), "seq": s} for s, p in rows]


def _is_signed(event: dict) -> bool:
    payload = event.get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    return bool(isinstance(payload, dict) and isinstance(payload.get("_auth"), dict)
                and payload["_auth"].get("sig"))


class Fixture:
    """Computers A (founder, main), B (a second licensed computer, only used to
    forge) and C (the fresh one). The Shop helper gives the plumbing: one retail
    relay, one registry relay, a process per call."""

    def __init__(self, tmp_path):
        self.shop = Shop(tmp_path, computers=("a", "b", "c"), licence=LICENCE)

    # -- the unsigned past ----------------------------------------------------
    def build_unsigned_history(self) -> None:
        s = self.shop
        _, created, _ = s.run("a", [
            {"op": "code_identity"},
            {"op": "create_shop", "price": 5.0, "initial_stock": 100},
            {"op": "activate_shared_licence", "license_public_id": LICENCE}])
        s.shop = created
        s.email, s.password = created["email"], created["password"]
        s._plant_founder_row("a", "founder-audit-row")
        self._give_the_owner_what_the_real_founding_route_gives_it(created["email"])
        ann = s.login("a", [{"op": "announce", "reason": "boot"}, {"op": "claim_main"}])
        assert ann[1]["status_code"] == 200, ann[1]
        s.ids["a"] = ann[0]["device"]["id"]
        [invite] = s.login("a", [{"op": "http", "method": "POST", "path": "/api/admin/employees",
                                  "json": {"email": CASHIER_EMAIL, "name": "Casey Cashier",
                                           "preset": "cashier"}}])
        assert invite["status_code"] == 200, invite
        # Readable numbers: a till number for A itself and the numbering books
        # (`doc_series`) -- the retail-stream authority events.
        [numbering, shift] = s.login("a", [
            {"op": "numbering_set", "branch_code": "FJA", "readable_enabled": True, "till_number": 1},
            {"op": "cash_session_open", "branch_id": created["branch_id"]}])
        assert numbering["status_code"] == 200, numbering
        assert shift["status_code"] == 200, shift
        s.login("a", [{"op": "reg_push"}, {"op": "push"}])

    def _give_the_owner_what_the_real_founding_route_gives_it(self, email: str) -> None:
        """The harness founds the shop by inserting the owner row directly, so the
        row has no `uid` and no queued sync event. The real founding route
        (`create_admin`) sets a uid and queues the owner's `user` event in the same
        transaction. Without a uid the owner can be neither re-announced nor
        addressed on the wire, so the fixture would be testing a state no real shop
        is in. Do what the real route does: a uid, and the (then unsigned) event."""
        s = self.shop
        cols = ("uid", "email", "employee_id", "role", "status", "require_password_change",
                "language", "password_hash", "pin_hash", "row_version", "updated_at_utc",
                "session_version", "branch_scope_uid", "display_name", "role_preset")
        s.run("a", [{"op": "reg_exec", "sql": "UPDATE users SET uid=? WHERE email=?",
                     "params": ["0a0e0b00-0000-4000-8000-000000000001", email]}])
        [res] = s.run("a", [{"op": "reg_select", "sql": f"SELECT {', '.join(cols)} FROM users WHERE email=?",
                             "params": [email]}])
        row = res["rows"][0]
        s.run("a", [{"op": "reg_exec",
                     "sql": "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) "
                            "VALUES ('owner-founding-1', 'user', ?, 'create', ?, '2026-01-01T00:00:00Z')",
                     "params": [row["uid"], json.dumps(row)]}])

    def plant_company_on(self, which: str) -> None:
        """What a real join leaves on a new computer before any sync: the shop's
        company id, the shop's licence -- and nothing else."""
        s = self.shop
        s.run(which, [
            {"op": "reg_exec",
             "sql": "INSERT INTO company_settings (id, company_id) VALUES (?, ?)",
             "params": ["cs-" + which, s.shop["company_id"]]},
            {"op": "activate_shared_licence", "license_public_id": LICENCE}])

    # -- the upgrade ------------------------------------------------------------
    def upgrade_a(self, monkeypatch) -> dict:
        monkeypatch.setenv("AURA_ACCEPT_SIGNED", "1")
        monkeypatch.setenv("AURA_ACCEPT_SIGNED_LICENCE", LICENCE)
        [tick, *_] = self.shop.run("a", [
            {"op": "app_announce", "reason": "heartbeat"},
            {"op": "reg_push"}, {"op": "push"}])
        return tick

    # -- what a computer holds ----------------------------------------------------
    def users(self, which):
        return {r["email"]: r for r in self.shop.reg_sel(which, "SELECT * FROM users")}

    def perms(self, which, email):
        return {r["subsystem"]: r["access_level"] for r in self.shop.reg_sel(
            which, "SELECT p.subsystem, p.access_level FROM user_permissions p "
                   "JOIN users u ON u.id=p.user_id WHERE u.email=?", [email])}

    def parked(self, which):
        [r] = self.shop.run(which, [{"op": "reg_quarantine_rows"}])
        [q] = self.shop.run(which, [{"op": "quarantine_rows"}])
        return r["rows"], q["rows"]

    def pull_both(self, which):
        self.shop.run(which, [{"op": "reg_pull"}])
        self.shop.run(which, [{"op": "pull"}])


@pytest.fixture
def fx(tmp_path, monkeypatch):
    monkeypatch.delenv("AURA_ACCEPT_SIGNED", raising=False)
    monkeypatch.setenv("AURA_ACCEPT_SIGNED_LICENCE", LICENCE)
    f = Fixture(tmp_path)
    f.build_unsigned_history()
    return f


def test_the_setup_really_left_unsigned_history_on_the_relay(fx):
    """Guard the fixture: if the history were signed already, every assertion below
    would pass for the wrong reason."""
    reg = _relay_events(fx.shop.reg_relays["a"])
    retail = _relay_events(fx.shop.relay)
    authority = [e for e in reg if e["entity_type"] in ("user", "user_permission", "admin_device", "device")]
    authority += [e for e in retail if e["entity_type"] in ("till_allocation", "doc_series")]
    kinds = {e["entity_type"] for e in authority}
    assert {"user", "user_permission", "admin_device", "till_allocation"} <= kinds, kinds
    assert authority and not any(_is_signed(e) for e in authority), \
        "the 'old' history is signed -- the scenario is not the one this file claims"


def test_without_the_reannounce_a_fresh_computer_has_nothing(fx, monkeypatch):
    """NON-VACUITY: the bug itself. C is upgraded and signs, but A never re-announced,
    so everything C pulls is unsigned history and is parked."""
    monkeypatch.setenv("AURA_ACCEPT_SIGNED", "1")
    fx.plant_company_on("c")
    fx.pull_both("c")
    assert fx.users("c") == {}, "unsigned history was applied on the fresh computer"
    reg_q, retail_q = fx.parked("c")
    assert UNSIGNED in {r["reason"] for r in reg_q}, reg_q
    assert fx.shop.sel("c", "SELECT 1 FROM till_allocations") == []


def test_a_fresh_computer_joining_after_the_upgrade_gets_the_whole_shop(fx, monkeypatch):
    # -- the upgrade ---------------------------------------------------------
    tick = fx.upgrade_a(monkeypatch)
    marker = tick["marker"]
    assert marker and marker["self_done"] is True and marker["main_done"], marker
    signed = [e for e in _relay_events(fx.shop.reg_relays["a"]) if _is_signed(e)]
    assert {"user", "user_permission", "admin_device"} <= {e["entity_type"] for e in signed}, \
        "the upgrade queued no signed re-announce of the shop's authority state"
    assert {"till_allocation"} <= {e["entity_type"] for e in _relay_events(fx.shop.relay) if _is_signed(e)}

    # -- the fresh computer joins --------------------------------------------
    fx.plant_company_on("c")
    assert fx.users("c") == {}
    fx.pull_both("c")

    users = fx.users("c")
    owner_email = fx.shop.email
    assert owner_email in users and CASHIER_EMAIL in users, sorted(users)
    assert users[owner_email]["role"] == "admin"
    assert users[CASHIER_EMAIL]["role"] == "cashier"
    # The cashier's permissions arrived, and they are the SAME ones A holds.
    on_a, on_c = fx.perms("a", CASHIER_EMAIL), fx.perms("c", CASHIER_EMAIL)
    assert on_a and on_c == on_a, (on_a, on_c)
    # The main computer is known.
    [assign] = fx.shop.reg_sel("c", "SELECT device_id FROM admin_device_assignment")
    assert assign["device_id"] == fx.shop.ids["a"]
    # The till allocation applied on the retail side.
    allocs = fx.shop.sel("c", "SELECT terminal_uid, till_number, status FROM till_allocations")
    assert allocs and allocs[0]["till_number"] == 1 and allocs[0]["status"] == "active", allocs
    # The numbering books arrived too.
    assert fx.shop.sel("c", "SELECT 1 FROM doc_series"), "the numbering books never reached C"

    # -- nothing the shop needs is still parked as unsigned ---------------------
    reg_q, retail_q = fx.parked("c")
    leftovers = [r for r in (reg_q + retail_q) if r["reason"] == UNSIGNED]
    assert leftovers == [], f"unsigned history is still parked on the fresh computer: {leftovers}"

    # -- and the owner can really sign in there ---------------------------------
    [login] = fx.shop.run("c", [{"op": "http", "method": "POST", "path": "/api/auth/login",
                                 "json": {"email": owner_email, "password": fx.shop.password}}])
    assert login["status_code"] == 200, login


def test_a_forged_admin_event_from_a_non_main_computer_stays_parked_on_the_fresh_computer(fx, monkeypatch):
    fx.upgrade_a(monkeypatch)
    fx.plant_company_on("c")
    fx.pull_both("c")
    before = fx.users("c")
    assert fx.shop.email in before            # the legitimate history applied (allow half)

    # B is a real, licensed computer of the shop (it signs for real) that is NOT the
    # main one. It queues a raw `user` event minting a second admin and pushes it.
    fx.plant_company_on("b")
    forged = {"uid": "forged-0000-0000-0000-000000000001", "email": FORGED_EMAIL, "employee_id": "EMP-666",
              "role": "admin", "status": "active", "require_password_change": 0, "language": "en",
              "password_hash": "x", "pin_hash": None, "row_version": 9, "updated_at_utc": "2026-10-02T00:00:00Z",
              "session_version": 1, "branch_scope_uid": None, "display_name": "Evil", "role_preset": None}
    fx.shop.run("b", [
        {"op": "reg_exec",
         "sql": "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) "
                "VALUES ('forge-1', 'user', ?, 'create', ?, '2026-10-02T00:00:00Z')",
         "params": [forged["uid"], json.dumps(forged)]},
        {"op": "reg_push"}])
    pushed = [e for e in _relay_events(fx.shop.reg_relays["b"])
              if e["entity_type"] == "user" and e["entity_id"] == forged["uid"]]
    assert pushed and _is_signed(pushed[-1]), "B's forged event did not go out signed -- not the attack under test"

    fx.shop.run("c", [{"op": "reg_pull"}])
    after = fx.users("c")
    assert FORGED_EMAIL not in after, "a non-main computer minted an admin on the fresh computer"
    assert sorted(after) == sorted(before), (sorted(before), sorted(after))
    assert [u for u, r in after.items() if r["role"] == "admin"] == [fx.shop.email]
    reg_q, _ = fx.parked("c")
    reasons = {r["reason"] for r in reg_q if r["entity_id"] == forged["uid"]}
    assert reasons and not (reasons & {UNSIGNED}), f"forged event was not parked for a forgery reason: {reg_q}"
