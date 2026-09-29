"""Shared fixture for the schema-v42 tests: ONE shop, several REAL computers.

Not a test file (leading underscore, same convention as `_accept_*.py`). It builds
what `retail_s_devices_twodevice_test.py` builds -- computer A founds the shop and
is the MAIN computer, computer B joins with the licence key -- and adds what the
till-allocation tests need on top: a retail-data relay shared by every computer so
allocations and sales cross over, a registry relay per group of computers that
share a registry, and the READABLE_NUMBERS_AVAILABLE flag flipped inside every
process (the flag lives only as long as its process, so it is prepended to every
call).

WHY TERMINAL IDS COME FROM `announce`, NOT `set_terminal_id`: since v42 a till
number is bound to the computer's registry device id, and the route refuses a
terminal id that is not an ACTIVE registry device (DEVICE_IDENTITY_CLASH). The
harness's `announce` returns that id, so every test below uses it. Faking one with
`set_terminal_id` is the old, pre-registry way and is exactly what v42 refuses.
"""
from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from _accept_harness import run_accept_device  # noqa: E402

PY = sys.executable
LICENCE = "LIC-V42-TEST-1"


class Shop:
    """Computers A and B (and optionally more) of ONE shop.

    `registries` maps a computer to the name of the registry relay it uses. By
    default everyone shares one ("main"); a test that needs two computers that
    each believe they are the main computer (the double-main residual) gives one
    of them its own.
    """

    def __init__(self, tmp_path: Path, computers=("a", "b"), registries=None,
                 licence: str = LICENCE):
        self.tmp = tmp_path
        self.licence = licence
        self.computers = tuple(computers)
        self.dirs = {c: tmp_path / c for c in self.computers}
        self.relay = tmp_path / "relay.db"
        regs = registries or {}
        self.reg_relays = {c: tmp_path / f"registry_{regs.get(c, 'main')}.db" for c in self.computers}
        self.email = self.password = None
        self.shop = None      # create_shop result on A
        self.ids = {}         # computer -> registry device id (== terminal uid)

    # -- plumbing ---------------------------------------------------------
    def run(self, which: str, actions: list, readable: bool = True) -> list:
        acts = list(actions)
        if readable:
            acts = [{"op": "set_readable_available", "value": True}] + acts
        out = run_accept_device(
            PY, self.dirs[which], self.relay, acts, timeout=240,
            options={"registry_relay": str(self.reg_relays[which]), "arm_provider": True})
        return out[1:] if readable else out

    def login(self, which: str, actions: list, readable: bool = True) -> list:
        out = self.run(which, [{"op": "login", "email": self.email, "password": self.password},
                               *actions], readable)
        return out[1:]

    def sel(self, which: str, sql: str, params=None) -> list:
        [r] = self.run(which, [{"op": "raw_select", "sql": sql, "params": params or []}])
        return r["rows"]

    def reg_sel(self, which: str, sql: str, params=None) -> list:
        [r] = self.run(which, [{"op": "reg_select", "sql": sql, "params": params or []}])
        return r["rows"]

    def sync(self, sender: str, receiver: str) -> None:
        """Retail data sender -> receiver (sales, allocations, aliases...)."""
        self.login(sender, [{"op": "push"}])
        self.login(receiver, [{"op": "pull"}])

    def sync_all(self) -> None:
        for c in self.computers:
            self.login(c, [{"op": "push"}])
        for c in self.computers:
            self.login(c, [{"op": "pull"}])

    def reg_sync(self, sender: str, receiver: str) -> None:
        self.run(sender, [{"op": "reg_push"}])
        self.run(receiver, [{"op": "reg_pull"}])

    # -- building the shop -----------------------------------------------
    def patch_branch_uid(self, which: str, uid: str) -> None:
        """Give `which` the shop's branch uid. The harness founds each computer's
        default branch with its own random uid (a real install converges them
        through the licence rebind, which `activate_shared_licence` deliberately
        does not do), so a test that needs two computers to mean the SAME branch
        says so here, in the open, instead of relying on a side effect."""
        self.run(which, [{"op": "raw_exec", "sql": "UPDATE branches SET uid=? WHERE id=1",
                          "params": [uid]}])

    def build(self, claim_main: bool = True, offline=()) -> "Shop":
        """`offline`: computers that never pull the registry after announcing, so
        they do not know who the main computer is -- a joiner whose first sync
        has not happened yet, or one that lost its network."""
        a = self.computers[0]
        _, created, _ = self.run(a, [
            {"op": "code_identity"},
            {"op": "create_shop", "price": 5.0, "initial_stock": 200},
            {"op": "activate_shared_licence", "license_public_id": self.licence}])
        self.shop = created
        self.email, self.password = created["email"], created["password"]
        # The harness founds the shop by inserting the owner directly, skipping the
        # ADMIN_CREATED audit row the real founding route writes; that row is the
        # only thing that lets `is_joined_device` tell "founder" from "joiner".
        self._plant_founder_row(a, "founder-audit-row")
        [owner_rows] = self.run(a, [{"op": "reg_select",
                                     "sql": "SELECT * FROM users WHERE email=?",
                                     "params": [self.email]}])
        owner = owner_rows["rows"][0]
        for c in self.computers[1:]:
            self._join(c, owner)
        # A announces, claims the main role, pushes; everyone else announces.
        ann = self.login(a, [{"op": "announce", "reason": "boot"}]
                         + ([{"op": "claim_main"}] if claim_main else [])
                         + [{"op": "reg_push"}, {"op": "push"}])
        self.ids[a] = ann[0]["device"]["id"]
        if claim_main:
            assert ann[1]["status_code"] == 200, ann[1]
        for c in self.computers[1:]:
            [ann_c, _, _] = self.login(c, [{"op": "announce", "reason": "boot"},
                                           {"op": "reg_push"}, {"op": "pull"}])
            self.ids[c] = ann_c["device"]["id"]
        assert len(set(self.ids.values())) == len(self.ids), self.ids
        for c in self.computers:
            if c not in offline:
                self.run(c, [{"op": "reg_pull"}])
        return self

    def _plant_founder_row(self, which: str, row_id: str) -> None:
        self.run(which, [{"op": "reg_exec",
                          "sql": "INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, entity_id) "
                                 "VALUES (?, ?, NULL, 'ADMIN_CREATED', 'user', ?)",
                          "params": [row_id, self.shop["company_id"], "founder"]}])

    def _join(self, which: str, owner: dict) -> None:
        [b_cols] = self.run(which, [{"op": "reg_select",
                                     "sql": "SELECT name FROM pragma_table_info('users')"}])
        cols = [c["name"] for c in b_cols["rows"] if c["name"] in owner]
        self.run(which, [
            {"op": "reg_exec",
             "sql": f"INSERT INTO users ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
             "params": [owner[c] for c in cols]},
            {"op": "activate_shared_licence", "license_public_id": self.licence}])
