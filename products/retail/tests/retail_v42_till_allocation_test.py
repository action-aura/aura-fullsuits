"""Schema v42 -- till numbers are handed out by ONE computer, the main computer.

Real computers, real HTTP routes, one shared retail relay (see `_v42_shop.py`). No
mocks of the thing under test: every assertion reads the answer a real second
process gave, or the rows a real process left behind.

WHAT EACH TEST IS THE ONLY GUARD FOR (and what breaks it -- see the mutation list
at the bottom of this docstring):

  X5   a second computer cannot mint a till number, the main computer hands out
       unique ones.                                  <- allocate_tills main check
  offline B   a computer that does not know who the main computer is is refused
       with 409 TILL_NEEDS_MAIN_COMPUTER and leaves NOTHING behind: no allocation,
       no book, no setting, no queued sync event.    <- every main check
  hand-made books   POST /doc-series and .../allocator-claim were the last two
       ways two computers could pick the same code offline; they are main-only
       now.                                          <- create_doc_series/claim
  no double booking   the same till number cannot go to two computers, and one
       computer cannot hold two numbers.            <- till_number_problem + the
                                                        idx_till_alloc_number index
  double-main   two computers that BOTH believed they were main (a residual the
       registry cannot rule out) converge on ONE winner, the same one on both
       machines.                                     <- sync_service tie-break
  X3   a reinstalled computer (its counter row gone) sells again with 200 and a
       number nobody used.                           <- _adopt_reinstalled_counter
  Fact 2   the main computer's own sale, return and purchase all get a number,
       and the first till of a new shop prints `<CODE>1-000001`.
  the check is READ   the main-only decision is taken from the registry on every
       call. It is proven in BOTH directions: flip the registry flag and the two
       computers swap answers. A hard-wired "always deny" or "always allow" fails
       one half.

WHY `set_terminal_id` IS NOT USED: since v42 a till number belongs to a computer's
REGISTRY device id and the route refuses a terminal id that is not an active
registry device (DEVICE_IDENTITY_CLASH). `announce` returns that id.

Mutations run against a mirror of the tree (so a red run cannot break the other
crews' suites); the exact lines and results are in the M2 hand-back.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from _v42_shop import Shop  # noqa: E402

NEEDS_MAIN = "TILL_NEEDS_MAIN_COMPUTER"

# Everything a refused call must leave exactly as it found it.
_GUARDED_TABLES = ("till_allocations", "doc_series", "doc_series_counter",
                   "retail_settings", "sync_outbox")


def _snapshot(shop: Shop, which: str) -> dict:
    actions = [{"op": "raw_select", "sql": f"SELECT * FROM {t} ORDER BY 1", "params": []}
               for t in _GUARDED_TABLES]
    return {t: r["rows"] for t, r in zip(_GUARDED_TABLES, shop.run(which, actions))}


def _branch_id(shop: Shop, which: str) -> int:
    return shop.sel(which, "SELECT id FROM branches ORDER BY id LIMIT 1")[0]["id"]


def _branch_uid(shop: Shop, which: str) -> str:
    return shop.sel(which, "SELECT uid FROM branches ORDER BY id LIMIT 1")[0]["uid"]


def _allocs(shop: Shop, which: str) -> list:
    return shop.sel(which, "SELECT id, terminal_uid, till_number, status, admin_epoch "
                           "FROM till_allocations ORDER BY id")


def _code(res: dict):
    return (res["body"] or {}).get("code")


def _open_and_sell(shop: Shop, which: str, n: int = 1) -> list:
    """Open a shift on `which` and ring `n` sales. Returns the sale numbers."""
    pid = shop.shop["product_id"]
    out = shop.login(which, [
        {"op": "cash_session_open", "branch_id": _branch_id(shop, which)},
        *[{"op": "sell", "product_id": pid, "quantity": 1} for _ in range(n)]])
    assert out[0]["status_code"] == 200, out[0]
    sales = out[1:]
    for s in sales:
        assert s["status_code"] == 200, s
    return [s["body"]["data"]["sale_number"] for s in sales]


# --------------------------------------------------------------------- X5
def test_x5_only_the_main_computer_hands_out_till_numbers_and_they_are_unique(tmp_path):
    shop = Shop(tmp_path).build()
    # One shop, one branch: the harness founds each computer's default branch with
    # its own random uid; a real install converges them through the licence rebind.
    shop.patch_branch_uid("b", _branch_uid(shop, "a"))
    a, b = shop.ids["a"], shop.ids["b"]

    # B is ONLINE (it has pulled the registry and knows who the main computer is)
    # and still may not give itself a number.
    [self_alloc] = shop.login("b", [{"op": "allocate_till", "terminal_uid": b}])
    assert self_alloc["status_code"] == 409 and _code(self_alloc) == NEEDS_MAIN, self_alloc
    assert _allocs(shop, "b") == []

    # The main computer gives B a number, then itself one. Two different numbers.
    [give_b] = shop.login("a", [{"op": "allocate_till", "terminal_uid": b}])
    [give_a] = shop.login("a", [{"op": "allocate_till", "terminal_uid": a}])
    assert give_b["status_code"] == 200 and give_a["status_code"] == 200, (give_b, give_a)
    rows = {r["terminal_uid"]: r["till_number"] for r in _allocs(shop, "a")}
    assert rows == {b: 1, a: 2}, rows

    # The allocation reaches B through sync, and each computer then prints its own
    # number -- the numbers cannot collide because the prefixes differ.
    shop.sync_all()
    assert {r["terminal_uid"]: r["till_number"] for r in _allocs(shop, "b")} == rows
    [sale_b] = _open_and_sell(shop, "b")
    [sale_a] = _open_and_sell(shop, "a")
    assert sale_b == "MAI1-000001", sale_b
    assert sale_a == "MAI2-000001", sale_a
    assert sale_a != sale_b


# --------------------------------------------------------- offline B, item 2
def test_offline_computer_is_refused_and_leaves_nothing_written_or_parked(tmp_path):
    shop = Shop(tmp_path).build(offline=("b",))
    b = shop.ids["b"]
    shop.login("b", [])                       # settle whatever a login itself writes
    before = _snapshot(shop, "b")

    refused = shop.login("b", [
        {"op": "allocate_till", "terminal_uid": b},
        {"op": "allocate_till", "all": True},
        {"op": "release_till", "terminal_uid": b},
        {"op": "numbering_set", "readable_enabled": True, "till_number": 1},
        {"op": "numbering_set", "branch_code": "ZZZ"},
        {"op": "create_doc_series", "doc_type": "sale", "code": "ZZ"},
    ])
    for r in refused:
        assert r["status_code"] == 409 and _code(r) == NEEDS_MAIN, r
        assert "main computer" in r["body"]["message"]

    after = _snapshot(shop, "b")
    assert after == before, {t: (before[t], after[t]) for t in _GUARDED_TABLES if before[t] != after[t]}
    # ... and in particular no sync event of the kinds a successful call queues.
    assert [r for r in after["sync_outbox"]
            if r["entity_type"] in ("till_allocation", "doc_series", "setting")
            and r not in before["sync_outbox"]] == []


# ------------------------------------------------------- hand-made books, item 3
def test_hand_made_books_are_main_only_including_two_offline_computers_with_one_code(tmp_path):
    shop = Shop(tmp_path, computers=("a", "b", "c")).build(offline=("b", "c"))
    before = {w: _snapshot(shop, w) for w in ("b", "c")}

    # Two computers that cannot see each other or the main computer both try the
    # SAME code -- the exact collision this rule exists to close. Neither wins.
    for w in ("b", "c"):
        [r] = shop.login(w, [{"op": "create_doc_series", "doc_type": "sale", "code": "ZZ"}])
        assert r["status_code"] == 409 and _code(r) == NEEDS_MAIN, (w, r)
    for w in ("b", "c"):
        assert _snapshot(shop, w) == before[w]

    # The main computer can make the book (the allow half: a blanket refusal
    # would also pass everything above) ...
    [made] = shop.login("a", [{"op": "create_doc_series", "doc_type": "sale", "code": "ZZ"}])
    assert made["status_code"] == 200, made
    sid = made["series_id"]
    # ... and only once: the code is taken.
    [again] = shop.login("a", [{"op": "create_doc_series", "doc_type": "sale", "code": "ZZ"}])
    assert again["status_code"] != 200, again

    # Claiming a book for a computer is main-only too.
    shop.sync("a", "b")
    [claim_b] = shop.login("b", [{"op": "claim_doc_series", "series_id": sid}])
    assert claim_b["status_code"] == 409 and _code(claim_b) == NEEDS_MAIN, claim_b
    assert shop.sel("b", "SELECT allocator_terminal_uid FROM doc_series WHERE id=?", [sid]) \
        in ([], [{"allocator_terminal_uid": None}]), "B must not have become the book's allocator"
    [claim_a] = shop.login("a", [{"op": "claim_doc_series", "series_id": sid}])
    assert claim_a["status_code"] == 200, claim_a
    assert shop.sel("a", "SELECT allocator_terminal_uid AS t FROM doc_series WHERE id=?", [sid]) \
        == [{"t": shop.ids["a"]}]


# ----------------------------------------------------- the check is READ per call
def test_the_main_only_decision_is_read_from_the_registry_in_both_directions(tmp_path):
    """Swap who the registry says is main and the two computers swap answers.

    This is the 'count the check' proof from outside the process: an outcome-only
    test (a 409) cannot tell "the guard consulted the registry" from "something
    always says no". Here the SAME call from the SAME computer is refused, then
    allowed, purely because the registry flag moved -- so the answer is derived
    from `local_device_is_admin`, on every call, and is not cached or hard-wired.
    """
    shop = Shop(tmp_path).build()
    shop.patch_branch_uid("b", _branch_uid(shop, "a"))
    a, b = shop.ids["a"], shop.ids["b"]
    [r1] = shop.login("b", [{"op": "allocate_till", "terminal_uid": b}])
    assert r1["status_code"] == 409 and _code(r1) == NEEDS_MAIN

    # The registry allows ONE admin per shop, so B's own copy has to demote A
    # before it promotes B; A's copy just demotes A.
    shop.run("b", [{"op": "reg_exec", "sql": "UPDATE devices SET is_admin_device=0 WHERE id=?",
                    "params": [a]},
                   {"op": "reg_exec", "sql": "UPDATE devices SET is_admin_device=1 WHERE id=?",
                    "params": [b]}])
    shop.run("a", [{"op": "reg_exec", "sql": "UPDATE devices SET is_admin_device=0 WHERE id=?",
                    "params": [a]}])
    [r2] = shop.login("b", [{"op": "allocate_till", "terminal_uid": b}])
    assert r2["status_code"] == 200, r2                      # now main: allowed
    [r3] = shop.login("a", [{"op": "allocate_till", "terminal_uid": a}])
    assert r3["status_code"] == 409 and _code(r3) == NEEDS_MAIN, r3   # no longer main: refused


# ------------------------------------------------ no double booking of a number
def test_a_till_number_cannot_go_to_two_computers_and_one_computer_holds_one_number(tmp_path):
    """The index `idx_till_alloc_number` and the in-transaction check are two
    layers of one defence: either alone still refuses (the other backstops it),
    so the mutation that proves this test removes BOTH."""
    shop = Shop(tmp_path).build()
    shop.patch_branch_uid("b", _branch_uid(shop, "a"))
    a, b = shop.ids["a"], shop.ids["b"]
    [first] = shop.login("a", [{"op": "allocate_till", "terminal_uid": b, "till_number": 1}])
    assert first["status_code"] == 200, first

    [clash] = shop.login("a", [{"op": "allocate_till", "terminal_uid": a, "till_number": 1}])
    assert clash["status_code"] == 409 and _code(clash) == "TILL_NUMBER_TAKEN", clash
    [twice] = shop.login("a", [{"op": "allocate_till", "terminal_uid": b, "till_number": 2}])
    assert twice["status_code"] == 409 and _code(twice) == "TILL_ALREADY_ALLOCATED", twice
    # Idempotent: asking again for what it already holds writes nothing new.
    [same] = shop.login("a", [{"op": "allocate_till", "terminal_uid": b, "till_number": 1}])
    assert same["status_code"] == 200, same

    rows = _allocs(shop, "a")
    assert len(rows) == 1 and rows[0]["terminal_uid"] == b and rows[0]["till_number"] == 1, rows
    # The free numbers are still free: the next computer gets the lowest one.
    [next_one] = shop.login("a", [{"op": "allocate_till", "terminal_uid": a}])
    assert next_one["status_code"] == 200
    assert sorted(r["till_number"] for r in _allocs(shop, "a")) == [1, 2]


# ---------------------------------------------------------------- double main
def test_two_computers_that_both_believed_they_were_main_converge_on_one_winner(tmp_path):
    """The residual the registry cannot rule out: A and B each hold the main role
    (say a takeover happened while they could not see each other) and each gives
    till 1 away. When the two allocations meet, the SAME one must win on BOTH
    machines -- highest main-computer epoch first -- and the loser is marked
    'superseded' rather than deleted, so the history stays."""
    shop = Shop(tmp_path).build()
    shop.patch_branch_uid("b", _branch_uid(shop, "a"))
    a, b = shop.ids["a"], shop.ids["b"]
    # B becomes a second main computer with a NEWER epoch. The registries are not
    # synced again afterwards, so A never learns of it.
    shop.run("b", [
        {"op": "reg_exec", "sql": "UPDATE devices SET is_admin_device=0 WHERE id=?", "params": [a]},
        {"op": "reg_exec", "sql": "UPDATE devices SET is_admin_device=1 WHERE id=?", "params": [b]},
        {"op": "reg_exec", "sql": "UPDATE admin_device_assignment SET epoch=epoch+1", "params": []}])

    [from_a] = shop.login("a", [{"op": "allocate_till", "terminal_uid": b, "till_number": 1}])
    [from_b] = shop.login("b", [{"op": "allocate_till", "terminal_uid": a, "till_number": 1}])
    assert from_a["status_code"] == 200 and from_b["status_code"] == 200, (from_a, from_b)
    id_a_gave = from_a["body"]["data"]["allocations"][0]["id"]      # till 1 -> B, older epoch
    id_b_gave = from_b["body"]["data"]["allocations"][0]["id"]      # till 1 -> A, newer epoch
    epochs = {r["id"]: r["admin_epoch"] for r in _allocs(shop, "a") + _allocs(shop, "b")}
    assert epochs[id_b_gave] > epochs[id_a_gave], epochs            # the setup did what it claims

    shop.sync_all()
    on_a, on_b = _allocs(shop, "a"), _allocs(shop, "b")
    assert on_a == on_b, (on_a, on_b)                               # same rows, same verdicts
    active = [r for r in on_a if r["status"] == "active"]
    assert [(r["id"], r["terminal_uid"]) for r in active] == [(id_b_gave, a)], on_a
    loser = [r for r in on_a if r["id"] == id_a_gave]
    assert loser and loser[0]["status"] == "superseded", on_a

    shop.sync_all()                                                 # a second round changes nothing
    assert _allocs(shop, "a") == on_a and _allocs(shop, "b") == on_b


# ----------------------------------------------------------------------- X3
def test_x3_a_reinstalled_computer_sells_again_with_an_unused_number(tmp_path):
    """A reinstall loses the local counter row but the sales it printed are still
    known (they come back with the first pull). The next sale must be 200 and
    must not repeat a number: before v42 it restarted at 000001 and died on the
    unique index with a 500."""
    shop = Shop(tmp_path, computers=("a",)).build()
    [alloc] = shop.login("a", [{"op": "allocate_till", "terminal_uid": shop.ids["a"]}])
    assert alloc["status_code"] == 200
    used = _open_and_sell(shop, "a", 2)
    assert used == ["MAI1-000001", "MAI1-000002"], used

    shop.run("a", [{"op": "raw_exec", "sql": "DELETE FROM doc_series_counter", "params": []}])
    assert shop.sel("a", "SELECT COUNT(*) AS n FROM doc_series_counter") == [{"n": 0}]

    [_, sale] = shop.login("a", [{"op": "pull"},
                                 {"op": "sell", "product_id": shop.shop["product_id"], "quantity": 1}])
    assert sale["status_code"] == 200, sale
    number = sale["body"]["data"]["sale_number"]
    assert number not in used, number
    assert number == "MAI1-000003", number
    dupes = shop.sel("a", "SELECT sale_number, COUNT(*) AS n FROM sales GROUP BY sale_number HAVING n > 1")
    assert dupes == [], dupes


# ------------------------------------------------------------------- Fact 2
def test_fact2_the_main_computers_sale_return_and_purchase_all_get_a_number(tmp_path):
    """The main computer, once it gave itself a till, numbers every document kind
    -- and the first till of a brand-new shop prints `<CODE>1-000001`, the shape
    the owner reads on the receipt."""
    shop = Shop(tmp_path, computers=("a",)).build()
    sh = shop.shop
    [alloc] = shop.login("a", [{"op": "allocate_till", "terminal_uid": shop.ids["a"]}])
    assert alloc["status_code"] == 200, alloc
    assert alloc["body"]["data"]["branch_code"] == "MAI"
    assert [(r["till_number"]) for r in _allocs(shop, "a")] == [1]

    out = shop.login("a", [
        {"op": "cash_session_open", "branch_id": _branch_id(shop, "a")},
        {"op": "sell", "product_id": sh["product_id"], "quantity": 2},
        {"op": "create_supplier", "name": "Fact2 Supplies"}])
    sale, supplier = out[1], out[2]
    assert sale["status_code"] == 200, sale
    assert supplier["status_code"] == 200, supplier
    assert sale["body"]["data"]["sale_number"] == "MAI1-000001"

    out = shop.login("a", [
        {"op": "create_return", "sale_id": sale["sale_id"],
         "items": [{"product_id": sh["product_id"], "quantity": 1}]},
        {"op": "create_po", "supplier_id": supplier["supplier_id"],
         "items": [{"product_id": sh["product_id"], "quantity": 5, "unit_cost": 2.0}]}])
    ret, po = out
    assert ret["status_code"] == 200, ret
    assert po["status_code"] == 200, po
    assert ret["body"]["data"]["return_number"] == "MAI1-R-000001"
    assert po["body"]["data"]["po_number"] == "MAI1-P-000001"
