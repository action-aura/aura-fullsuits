"""Track S + registry v9 -- MIXED BUILDS across two REAL PROCESSES.

A shop does not upgrade all its computers on the same day. For weeks one till
runs the new build (registry v9: display names, role presets, four "books"
permissions, a synced list of computers) while another still runs the last one
(registry v8). Whatever one sends, the other must neither choke on nor undo.

The "old" device here is not a mock. It is a real checkout of the last commit
before Track S (`_accept_harness.OLD_BUILD_COMMIT`, retail v41 / registry v8)
run in its own process with its own database, and every test first asks the
device which code it really imported (`code_identity`) -- a mixed-build test
that quietly ran the new code on both sides would pass and prove nothing.

WHAT THIS FILE PROVES (section 5.5 of the Track S contract):

  1. NEW -> OLD: a v9 user with a display name, a preset and the books grants
     reaches an old build with ZERO quarantine rows, and the old build's own
     sign-in lists only the eight codes it knows.
  2. OLD -> NEW: an old build changing a PIN sends a user event with no
     `display_name`/`role_preset` keys at all. The new build must read that as
     "not said" and KEEP the local values, not as "blank them".
  3. OLD -> NEW: an old build's role change reseeds only eight codes and knows
     nothing of the books grants. The new build must not let the person keep
     books access under a role they no longer hold (fail closed), and must not
     announce that reset back to the shop (no emit).
  4. Device announcements reach an old build and are skipped without a quarantine
     row or an error; after that computer upgrades, the real boot announcement
     makes it known to the shop again.

Run (one file per process):
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \
        products/retail/tests/retail_s_mixed_build_twodevice_test.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from _accept_harness import export_old_build, run_accept_device  # noqa: E402

PY = sys.executable
LICENCE = "LIC-MIX-TEST-1"
BOOKS_CODES = ("retail.books.view", "retail.books.record", "retail.books.approve", "retail.books.close")
OLD_EIGHT = (
    "retail.cash.approve", "retail.cash.close", "retail.discount", "retail.employees",
    "retail.refund", "retail.reports", "retail.sell", "retail.stock.adjust",
)


@pytest.fixture(scope="module")
def old_tree() -> Path:
    return export_old_build()


class Shop:
    """Two devices of ONE shop: `new` (this checkout) and `old` (last build),
    sharing a registry relay. Each call opens a fresh process on the device's
    persistent data directory, so state carries over but login cookies do not
    (every call that needs a session starts with a `login`)."""

    def __init__(self, tmp_path: Path, old_tree: Path):
        self.new_dir = tmp_path / "new"
        self.old_dir = tmp_path / "old"
        self.relay = tmp_path / "relay.db"
        self.reg_relay = tmp_path / "registry_relay.db"
        self.old_tree = old_tree
        self.company_id = None
        self.admin_email = None
        self.admin_password = None

    def run(self, which: str, actions: list, *, upgraded: bool = False) -> list:
        opts = {"registry_relay": str(self.reg_relay), "arm_provider": True}
        if which == "old" and not upgraded:
            opts["code_root"] = str(self.old_tree)
        return run_accept_device(
            PY, self.new_dir if which == "new" else self.old_dir, self.relay,
            actions, timeout=180, options=opts,
        )

    def login_admin(self, which: str, actions: list, **kw) -> list:
        """Runs `actions` after signing the owner in; returns the results of
        `actions` only."""
        out = self.run(which, [{"op": "login", "email": self.admin_email,
                                "password": self.admin_password}, *actions], **kw)
        return out[1:]


@pytest.fixture
def shop(tmp_path, old_tree) -> Shop:
    s = Shop(tmp_path, old_tree)
    ident_new, created, _lic = s.run("new", [
        {"op": "code_identity"},
        {"op": "create_shop"},
        {"op": "activate_shared_licence", "license_public_id": LICENCE},
    ])
    s.company_id, s.admin_email, s.admin_password = (
        created["company_id"], created["email"], created["password"])

    # The owner's row on the new device was written straight to the database (no
    # route creates the FIRST owner), so it never entered the outbox. Copy it to
    # the old device the same way, column for column over whatever columns the
    # OLD schema has, so both devices know the same owner in the same company.
    [admin_rows] = s.run("new", [{"op": "reg_select", "sql": "SELECT * FROM users WHERE email=?",
                                  "params": [s.admin_email]}])
    admin = admin_rows["rows"][0]
    ident_old, old_cols = s.run("old", [
        {"op": "code_identity"},
        {"op": "reg_select", "sql": "SELECT name FROM pragma_table_info('users')"},
    ])
    cols = [c["name"] for c in old_cols["rows"] if c["name"] in admin]
    s.run("old", [
        {"op": "reg_exec",
         "sql": f"INSERT INTO users ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
         "params": [admin[c] for c in cols]},
        {"op": "activate_shared_licence", "license_public_id": LICENCE},
    ])

    # Guard the fixture itself: the old device must be running OLD code.
    assert ident_new["registry_schema_version"] >= 9, ident_new
    assert "device" in ident_new["registry_entity_types"], ident_new
    assert ident_old["registry_schema_version"] == 8, (
        f"the 'old' device is not running the old build: {ident_old}")
    assert "device" not in ident_old["registry_entity_types"], ident_old
    assert Path(ident_old["sync_service_file"]).is_relative_to(s.old_tree), ident_old
    assert not Path(ident_new["sync_service_file"]).is_relative_to(s.old_tree), ident_new
    return s


def _invite_accountant(s: Shop, email: str = "sara@mix.test", name: str = "Sara Accountant") -> dict:
    """Owner on the NEW build invites an accountant, then both devices converge.
    Returns the employee's registry row as the new device holds it."""
    [_created, emitted, _push] = s.login_admin("new", [
        {"op": "http", "method": "POST", "path": "/api/admin/employees",
         "json": {"email": email, "name": name, "preset": "accountant"}},
        {"op": "reg_outbox"},
        {"op": "reg_push"},
    ])
    assert _created["status_code"] == 200, _created
    # Non-vacuity: the v9 build really did put the new keys and the books grants
    # on the wire. If it stopped doing so the tests below would be comparing an
    # old-shaped event with an old-shaped event.
    user_events = [r for r in emitted["rows"] if r["entity_type"] == "user"]
    assert user_events and user_events[-1]["payload"].get("display_name") == name, user_events
    assert user_events[-1]["payload"].get("role_preset") == "accountant", user_events
    perm_codes = {r["payload"].get("subsystem") for r in emitted["rows"]
                  if r["entity_type"] == "user_permission"}
    assert set(BOOKS_CODES) <= perm_codes, perm_codes
    [res] = s.run("new", [{"op": "reg_select", "sql": "SELECT * FROM users WHERE email=?",
                           "params": [email]}])
    [row] = res["rows"]
    return row


def _local_id(s: Shop, which: str, email: str, **kw) -> str:
    """A person's `users.id` on ONE device. The wire identity is `uid`; each
    device keeps its own local `id`, so a route on the old build must be
    addressed with the OLD device's id, not the id the new device holds."""
    [res] = s.run(which, [{"op": "reg_select", "sql": "SELECT id FROM users WHERE email=?",
                           "params": [email]}], **kw)
    [row] = res["rows"]
    return row["id"]


def _perm_levels(s: Shop, which: str, user_id: str, **kw) -> dict:
    [res] = s.run(which, [{"op": "reg_select",
                           "sql": "SELECT subsystem, access_level FROM user_permissions WHERE user_id=?",
                           "params": [user_id]}], **kw)
    return {r["subsystem"]: r["access_level"] for r in res["rows"]}


def _give_login(s: Shop, which: str, employee_email: str, **kw) -> None:
    """The employee's real password is set through a mailed link that never
    happens in a test, so give the row on THIS device the owner's password hash
    and mark it active. Local fixture change only -- never emitted."""
    [res] = s.login_admin(which, [{"op": "reg_select", "sql": "SELECT password_hash FROM users WHERE email=?",
                                   "params": [s.admin_email]}], **kw)
    s.run(which, [{"op": "reg_exec",
                   "sql": "UPDATE users SET password_hash=?, status='active', require_password_change=0 "
                          "WHERE email=?",
                   "params": [res["rows"][0]["password_hash"], employee_email]}], **kw)


def test_new_user_and_books_events_reach_an_old_build_clean_and_it_lists_only_eight_codes(shop):
    row = _invite_accountant(shop)

    [pull, quarantine, old_user] = shop.run("old", [
        {"op": "reg_pull"},
        {"op": "reg_quarantine_rows"},
        {"op": "reg_select", "sql": "SELECT * FROM users WHERE email=?", "params": ["sara@mix.test"]},
    ])
    assert quarantine["rows"] == [], (
        f"an old build quarantined events from a v9 build: {quarantine['rows']}")
    assert old_user["rows"], "the invited accountant never reached the old build"
    assert old_user["rows"][0]["role"] == "manager"
    assert not [w for w in pull["_log_warnings"] if "quarantin" in w.lower()], pull["_log_warnings"]

    # What the OLD build's own sign-in says the accountant may do: only codes it
    # has heard of. Reports and cash-approve are the accountant's two old-vocabulary
    # grants; the four books codes are simply not something a v8 session lists.
    _give_login(shop, "old", "sara@mix.test")
    [_login, session] = shop.run("old", [
        {"op": "login", "email": "sara@mix.test", "password": shop.admin_password},
        {"op": "http", "method": "GET", "path": "/api/auth/session"},
    ])
    caps = session["body"]["capabilities"]
    assert caps == sorted(["retail.cash.approve", "retail.reports"]), caps
    assert not set(caps) & set(BOOKS_CODES)

    # And the owner on the old build sees exactly the eight it knows -- the
    # count the new build's twelve must never be mistaken for.
    [admin_session] = shop.login_admin("old", [
        {"op": "http", "method": "GET", "path": "/api/auth/session"}])
    assert admin_session["body"]["capabilities"] == sorted(OLD_EIGHT)

    # Same shop, new build: twelve.
    [new_session] = shop.login_admin("new", [
        {"op": "http", "method": "GET", "path": "/api/auth/session"}])
    assert len(new_session["body"]["capabilities"]) == 12
    assert row["display_name"] == "Sara Accountant"


def test_an_old_build_pin_change_does_not_wipe_the_name_or_preset_on_the_new_build(shop):
    """CHANGED BY S-AUTH (AUDIT-033), deliberately. This used to prove that an OLD
    build's PIN change (a `user` event with no `display_name` / `role_preset` keys)
    arrives on the new build and KEEPS the local name and preset instead of blanking
    them. An old build cannot sign, and `user` is an authority event, so the new
    build now PARKS it as `unsigned_authority_event` (visible in sync review) and
    applies none of it -- the name and preset are untouched because nothing is.

    WHAT THIS CAN NO LONGER CATCH: the absent-key "keep my local value" rule on the
    real pull path. That rule is still pinned where it can still be reached:
    `user_sync_apply_plan` in commercial_runtime/identity/tests/
    test_mixed_build_old_receiver.py (OLD -> NEW) and test_registry_v9_apply.py,
    which feed it signed events."""
    row = _invite_accountant(shop)
    shop.run("old", [{"op": "reg_pull"}])
    old_id = _local_id(shop, "old", "sara@mix.test")
    [pin_before] = shop.run("new", [{"op": "reg_select", "sql": "SELECT pin_hash FROM users WHERE email=?",
                                     "params": ["sara@mix.test"]}])

    [put, old_outbox, _push] = shop.login_admin("old", [
        {"op": "http", "method": "PUT", "path": f"/api/admin/employees/{old_id}/pin",
         "json": {"pin": "4321"}},
        {"op": "reg_outbox"},
        {"op": "reg_push"},
    ])
    assert put["status_code"] == 200, put
    # Non-vacuity: the OLD build's user event really does not mention the two
    # new fields, and really carries no signature.
    pin_events = [r for r in old_outbox["rows"] if r["entity_type"] == "user"]
    assert pin_events, old_outbox
    assert "display_name" not in pin_events[-1]["payload"], pin_events[-1]["payload"]
    assert "role_preset" not in pin_events[-1]["payload"], pin_events[-1]["payload"]

    [_pull, after, quarantine] = shop.run("new", [
        {"op": "reg_pull"},
        {"op": "reg_select", "sql": "SELECT display_name, role_preset, pin_hash, role FROM users WHERE email=?",
         "params": ["sara@mix.test"]},
        {"op": "reg_quarantine_rows"},
    ])
    got = after["rows"][0]
    assert got["pin_hash"] == pin_before["rows"][0]["pin_hash"], "an UNSIGNED change from an old build was applied"
    assert got["display_name"] == "Sara Accountant", f"the name changed: {got}"
    assert got["role_preset"] == "accountant", f"the preset changed: {got}"
    parked = [(r["entity_type"], r["reason"]) for r in quarantine["rows"]]
    assert parked == [("user", "unsigned_authority_event")], parked


def test_an_old_build_role_change_resets_the_books_grants_on_the_new_build_and_says_nothing(shop):
    """CHANGED BY S-AUTH (AUDIT-033), deliberately. This used to prove that an old
    build demoting an Accountant to cashier makes the new build take the books
    grants away (fail closed) without announcing the reset. An old build cannot
    sign, so the new build now PARKS the role change as `unsigned_authority_event`
    and applies none of it: the person keeps their role AND their grants until the
    old computer is upgraded and re-sends it signed.

    WHAT THIS CAN NO LONGER CATCH: the receiver-side books reset on the real pull
    path (`user_sync_apply_plan(...)['reset_books']`), which is still pinned
    against an old build's REAL payload in commercial_runtime/identity/tests/
    test_mixed_build_old_receiver.py. What it still pins: nothing is applied,
    nothing is emitted, and the refusal is visible rather than silent."""
    row = _invite_accountant(shop)
    shop.run("old", [{"op": "reg_pull"}])

    before = _perm_levels(shop, "new", row["id"])
    assert before.get("retail.books.view") == "full", before  # non-vacuity: books access existed

    old_id = _local_id(shop, "old", "sara@mix.test")
    [put, _push] = shop.login_admin("old", [
        {"op": "http", "method": "PUT", "path": f"/api/admin/employees/{old_id}/role",
         "json": {"role": "cashier"}},
        {"op": "reg_push"},
    ])
    assert put["status_code"] == 200, put

    [outbox_before] = shop.run("new", [{"op": "reg_outbox"}])
    [_pull, quarantine, after_user, outbox_after] = shop.run("new", [
        {"op": "reg_pull"},
        {"op": "reg_quarantine_rows"},
        {"op": "reg_select", "sql": "SELECT role, role_preset FROM users WHERE email=?",
         "params": ["sara@mix.test"]},
        {"op": "reg_outbox"},
    ])
    parked = {(r["entity_type"], r["reason"]) for r in quarantine["rows"]}
    assert ("user", "unsigned_authority_event") in parked, parked
    assert after_user["rows"][0]["role"] == "manager", f"an UNSIGNED role change was applied: {after_user}"
    assert after_user["rows"][0]["role_preset"] == "accountant", after_user
    assert _perm_levels(shop, "new", row["id"]) == before, "an unsigned change moved the books grants"

    def _person_events(rows):
        return [r for r in rows if r["entity_type"] in ("user", "user_permission")]
    assert len(_person_events(outbox_after["rows"])) == len(_person_events(outbox_before["rows"])), (
        "parking the old build's role change emitted new user/permission events on the receiver")


def test_device_announcements_are_skipped_by_an_old_build_and_learned_again_after_it_upgrades(shop):
    # NEW announces itself and pushes.
    [announce, outbox, _push] = shop.login_admin("new", [
        {"op": "announce", "reason": "boot"},
        {"op": "reg_outbox"},
        {"op": "reg_push"},
    ])
    new_device_id = announce["device"]["id"]
    assert [r for r in outbox["rows"] if r["entity_type"] == "device"], (
        "the new build queued no device event -- nothing to skip")

    # OLD pulls: no crash, nothing quarantined, no complaint.
    [pull, quarantine] = shop.run("old", [{"op": "reg_pull"}, {"op": "reg_quarantine_rows"}])
    assert quarantine["rows"] == [], quarantine["rows"]
    assert not [w for w in pull["_log_warnings"] if "error" in w.lower() or "quarantin" in w.lower()], (
        pull["_log_warnings"])

    # UPGRADE the old computer: same data directory, new code. The process that
    # starts here runs the REAL boot seam (app.py `_announce_boot_device`) --
    # nothing in this test calls announce for it.
    [ident, up_outbox, up_devices] = shop.run("old", [
        {"op": "code_identity"},
        {"op": "reg_outbox"},
        {"op": "reg_select", "sql": "SELECT id, registry_schema_version FROM devices"},
    ], upgraded=True)
    assert ident["registry_schema_version"] >= 9
    assert not Path(ident["sync_service_file"]).is_relative_to(shop.old_tree)
    assert [r for r in up_outbox["rows"] if r["entity_type"] == "device"], (
        "an upgraded computer did not announce itself when it booted")
    upgraded_device_ids = {d["id"] for d in up_devices["rows"]}
    assert upgraded_device_ids, "an upgraded computer has no device row of its own after booting"

    # The NEW device boots again (its heartbeat/boot announcement), the upgraded
    # one pushes its own, and each learns the other.
    shop.login_admin("new", [{"op": "announce", "reason": "boot"}, {"op": "reg_push"}])
    shop.run("old", [{"op": "reg_push"}], upgraded=True)
    [_pull, listing] = shop.login_admin("old", [
        {"op": "reg_pull"},
        {"op": "devices_list"},
    ], upgraded=True)
    known = {d["id"] for d in listing["body"]["devices"]}
    assert new_device_id in known, (
        f"the upgraded computer never learned the other computer exists: {known}")
    [_pull_new, listing_new] = shop.login_admin("new", [{"op": "reg_pull"}, {"op": "devices_list"}])
    known_new = {d["id"] for d in listing_new["body"]["devices"]}
    assert upgraded_device_ids <= known_new, (
        f"the new computer never learned the upgraded one: {known_new} vs {upgraded_device_ids}")


# --------------------------------------------------------------------------
# RETAIL stream: a v41 till meets v42 events (plan section 5.5, last clause).
# --------------------------------------------------------------------------

class RetailPair:
    """A new (v42) till and an old (v41) till, each with its own shop, meeting
    on one retail relay -- the way the parked-children test wires two
    computers. The old till is armed with the same schema-skew guard the real
    app arms (`arm_provider`): an unarmed old till would apply a v42 sale it
    cannot understand and this test would prove nothing."""

    def __init__(self, tmp_path: Path, old_tree: Path):
        self.new_dir = tmp_path / "rnew"
        self.old_dir = tmp_path / "rold"
        self.relay = tmp_path / "retail_relay.db"
        self.reg_relay = tmp_path / "retail_registry_relay.db"
        self.old_tree = old_tree

    def run(self, which: str, actions: list, *, upgraded: bool = False) -> list:
        opts = {"registry_relay": str(self.reg_relay), "arm_provider": True}
        if which == "old" and not upgraded:
            opts["code_root"] = str(self.old_tree)
        return run_accept_device(
            PY, self.new_dir if which == "new" else self.old_dir, self.relay,
            actions, timeout=240, options=opts,
        )


def _count(res: dict) -> int:
    return res["rows"][0]["n"]


def test_a_v41_till_parks_every_v42_event_and_replays_them_all_once_it_upgrades(tmp_path, old_tree):
    pair = RetailPair(tmp_path, old_tree)
    [ident_old, boot_old] = pair.run("old", [{"op": "code_identity"}, {"op": "bootstrap"}])
    [ident_new, shop] = pair.run("new", [{"op": "code_identity"},
                                         {"op": "create_shop", "price": 7.0, "initial_stock": 20}])
    # Guard the fixture: the old till must really be the v41 build.
    assert ident_new["retail_schema_version"] >= 42, ident_new
    assert ident_old["retail_schema_version"] == 41, (
        f"the 'old' till is not running the v41 build: {ident_old}")
    assert Path(ident_old["sync_service_file"]).is_relative_to(old_tree), ident_old

    # NEW till rings a sale and pushes everything it knows (product, stock,
    # sale, items, payment, movement, journal), all stamped as v42.
    sold = pair.run("new", [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "cash_session_open", "branch_id": shop["branch_id"]},
        {"op": "sell", "product_id": shop["product_id"], "quantity": 2},
        {"op": "push"},
    ])
    assert sold[2]["status_code"] == 200, sold[2]
    sale_uid = sold[2]["body"]["data"].get("uid")

    # OLD (v41) till pulls: it must PARK, not apply, not crash.
    count_sales = {"op": "raw_select", "sql": "SELECT COUNT(*) AS n FROM sales", "params": []}
    count_moves = {"op": "raw_select", "sql": "SELECT COUNT(*) AS n FROM inventory_movements", "params": []}
    [_pull, parked, sales_before, moves_before] = pair.run("old", [
        {"op": "pull"}, {"op": "quarantine_rows"}, count_sales, count_moves])
    reasons = {r["reason"] for r in parked["rows"]}
    kinds = {r["entity_type"] for r in parked["rows"]}
    assert parked["rows"], "the v41 till parked nothing -- it either applied v42 events or never saw them"
    assert reasons == {"schema_version_too_new"}, (
        f"a v41 till must park v42 events for exactly one reason: {reasons}")
    assert {"sale", "sale_item", "payment"} <= kinds, kinds
    assert _count(sales_before) == 0, "a v41 till applied a v42 sale it cannot understand"
    assert _count(moves_before) == 0, "a v41 till applied a v42 stock movement"

    # A second pull changes nothing: parking is stable on replay.
    [_pull2, parked2] = pair.run("old", [{"op": "pull"}, {"op": "quarantine_rows"}])
    assert len(parked2["rows"]) == len(parked["rows"]), (len(parked2["rows"]), len(parked["rows"]))

    # UPGRADE the old till: same data directory, current code. The next pull
    # replays what was parked, in full and without a quarantine row left over.
    [ident_up, _p1, _p2, left, sales_after, stock, drift] = pair.run("old", [
        {"op": "code_identity"}, {"op": "pull"}, {"op": "pull"}, {"op": "quarantine_rows"}, count_sales,
        {"op": "raw_select", "sql": "SELECT COALESCE(SUM(quantity_on_hand),0) AS n FROM inventory_balances", "params": []},
        {"op": "compute_drift", "company_id": boot_old["company_id"]},
    ], upgraded=True)
    assert ident_up["retail_schema_version"] >= 42, ident_up
    assert left["rows"] == [], f"the upgraded till still has parked events: {left['rows']}"
    assert _count(sales_after) == 1, "the parked sale never applied after the upgrade"
    assert _count(stock) == 18, f"20 opening minus the 2 units sold should be 18, got {_count(stock)}"
    assert drift["drift"] == [], drift
    [uid_row] = pair.run("old", [{"op": "raw_select", "sql": "SELECT uid FROM sales", "params": []}], upgraded=True)
    if sale_uid:
        assert uid_row["rows"][0]["uid"] == sale_uid


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
    monkeypatch.setenv("AURA_ACCEPT_SIGNED_LICENCE", "LIC-MIX-TEST-1")
