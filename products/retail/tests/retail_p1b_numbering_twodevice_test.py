"""Phase 1b fix round -- readable numbers across TWO REAL computers.

Why this file exists. An adversarial verifier ran two real device processes
(separate retail.db each, joined by the repo's own FileRelay) and broke the
first design: the switch was a synced shop-wide flag and the first shift
auto-booked a till number, so two computers that had not heard from each
other yet each picked "till 1", printed the same numbers, and one side's
books were quarantined on sync -- silently.

The orchestrator's decision: readable numbers NEVER switch on by themselves.
Turning them on is an explicit choice made on ONE computer, with a till
number the owner types. This file pins that from the outside, over the real
`/numbering` route and real sync, on two real processes:

  T1  two brand-new tills open their first shifts before they have ever heard
      from each other: NEITHER auto-books, both keep the legacy SALE- numbers,
      and no numbering row exists on either.
  T2  an existing shop that never opted in, then a fresh device joins: still
      nothing turns on, on either side.
  T3  the main computer opts in: the switch is device-local -- the other computer
      never receives it and keeps its legacy numbers.
  T4  the same till number cannot be given to two computers: the main computer
      asking for a number that is taken is refused (409 TILL_NUMBER_TAKEN), a
      different number works, and the two computers then print different number
      ranges with nothing quarantined.
  T5  SCHEMA v42 REWROTE THIS ONE ON PURPOSE. The old T5 pinned the offline
      window ("two computers that have not synced both choose till 1; the second
      one to sync is quarantined") -- a window the registry now closes: only the
      MAIN computer hands out till numbers, so an offline second computer is
      refused (409 TILL_NEEDS_MAIN_COMPUTER) and nothing is written or parked.
      WHAT THE OLD T5 CAN NO LONGER CATCH: nothing reachable through a route
      creates two till-1s any more, so it could not notice the quarantine
      behaviour of a doc_series collision changing. That behaviour is still
      reachable one way -- two computers that BOTH believe they are main -- and
      T5b pins it as its own case.
  T5b the double-main residual (a takeover while two computers could not see each
      other): both give till 1 away, and when the two meet exactly one allocation
      survives, the same one on both machines; the other is superseded, never
      deleted.

  T6  the kill switch: with `READABLE_NUMBERS_AVAILABLE` off, the very same opt-in
      is refused (409 READABLE_NEEDS_DEVICE_REGISTRY) on both computers and
      nothing is written. (Until v42 this was the SHIPPED state; now the flag
      defaults on, and T6 flips it OFF inside each process, which is what an
      operator's kill switch does.)
  T7  the kill switch, the already-ON computer: a computer that had readable
      numbers when the switch went off: its next shift creates no books, its sale
      keeps the long default number, GET says it was switched off, and the readable
      books it made are retired and audited.

The harness is `_accept_harness` + `FileRelay`, the same one the acceptance
pass uses; the additions in `_accept_device.py` are `numbering_get` /
`numbering_set` (route drivers), `set_readable_available` (flips the flag inside
one device process) and `raw_exec` (plants a state no route can create now).
T1/T2 run on the plain two-device fixture; T3-T7 need a device REGISTRY (who is
the main computer?) so they run on `_v42_shop.Shop`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
from _accept_harness import run_accept_device  # noqa: E402
from _v42_shop import Shop  # noqa: E402

PY = sys.executable
TA = 'p1b-till-a-0000-0000-000000000001'
TB = 'p1b-till-b-0000-0000-000000000002'


def dev(d, relay, acts):
    return run_accept_device(PY, d, relay, acts, timeout=240)


def login(term, who):
    return [{"op": "set_terminal_id", "terminal_id": term},
            {"op": "login", "email": who["email"], "password": who["password"]}]


def sel(d, relay, sql, params=None):
    [r] = dev(d, relay, [{"op": "raw_select", "sql": sql, "params": params or []}])
    return r["rows"]


def join_second_device(b, relay):
    [boot] = dev(b, relay, [{"op": "bootstrap"}])
    [_, _, prod] = dev(b, relay, [{"op": "set_terminal_id", "terminal_id": TB},
                                  {"op": "login", "email": boot["email"], "password": boot["password"]},
                                  {"op": "create_product", "name": "B widget", "initial_stock": 50}])
    [br] = dev(b, relay, [{"op": "list_branches", "company_id": boot["company_id"]}])
    return boot, prod["product_id"], br["branches"][0]["id"]


def sale_number(result):
    return result["body"]["data"]["sale_number"]


def book_codes(d, relay):
    return sorted(r["code"] for r in sel(d, relay, "SELECT code FROM doc_series"))


def till_rows(d, relay):
    return sel(d, relay, "SELECT skey FROM retail_settings WHERE skey LIKE 'numbering_%'")


@pytest.fixture()
def two(tmp_path):
    a, b, relay = tmp_path / "a", tmp_path / "b", tmp_path / "relay.db"
    [shop] = dev(a, relay, [{"op": "create_shop", "price": 5.0, "initial_stock": 100}])
    boot, bpid, bbid = join_second_device(b, relay)
    return {"a": a, "b": b, "relay": relay, "shop": shop, "boot": boot, "bpid": bpid, "bbid": bbid}


def open_and_sell_a(t):
    r = dev(t["a"], t["relay"], login(TA, t["shop"]) + [
        {"op": "cash_session_open", "branch_id": t["shop"]["branch_id"]},
        {"op": "sell", "product_id": t["shop"]["product_id"], "quantity": 1}])
    return r[2], r[-1]


def open_and_sell_b(t):
    r = dev(t["b"], t["relay"], login(TB, t["boot"]) + [
        {"op": "cash_session_open", "branch_id": t["bbid"]},
        {"op": "sell", "product_id": t["bpid"], "quantity": 1}])
    return r[2], r[-1]


def sync_both(t):
    dev(t["b"], t["relay"], [{"op": "push"}])
    dev(t["a"], t["relay"], [{"op": "push"}])
    dev(t["a"], t["relay"], login(TA, t["shop"]) + [{"op": "pull"}])
    dev(t["b"], t["relay"], login(TB, t["boot"]) + [{"op": "pull"}])


def test_t1_two_new_tills_open_first_shifts_and_neither_auto_books(two):
    """The verifier's P1 scenario. Before the fix both tills auto-booked
    "till 1" at the first shift. Now the first shift must create NO books."""
    shift_a, sale_a = open_and_sell_a(two)
    shift_b, sale_b = open_and_sell_b(two)
    assert shift_a["status_code"] == 200 and shift_b["status_code"] == 200
    na, nb = sale_number(sale_a), sale_number(sale_b)
    assert na.startswith("SALE-") and nb.startswith("SALE-"), (na, nb)
    assert book_codes(two["a"], two["relay"]) == [], "a first shift auto-booked readable books on A"
    assert book_codes(two["b"], two["relay"]) == [], "a first shift auto-booked readable books on B"
    assert till_rows(two["a"], two["relay"]) == [] and till_rows(two["b"], two["relay"]) == []
    sync_both(two)
    for d in ("a", "b"):
        assert sel(two[d], two["relay"], "SELECT 1 FROM sync_apply_quarantine") == []
        assert book_codes(two[d], two["relay"]) == []
        assert len(sel(two[d], two["relay"], "SELECT 1 FROM sales")) == 2, \
            "a sale from the other till never reached this computer"


def test_t2_an_existing_shop_that_never_opted_in_stays_on_the_old_numbers(tmp_path):
    """The verifier's P2. A fresh device joining used to flip an existing
    shop's numbers. Nothing on either side may turn on now."""
    a, b, relay = tmp_path / "a", tmp_path / "b", tmp_path / "relay.db"
    [shop] = dev(a, relay, [{"op": "create_shop", "price": 5.0, "initial_stock": 100}])
    r0 = dev(a, relay, login(TA, shop) + [{"op": "sell", "product_id": shop["product_id"], "quantity": 1}])
    assert sale_number(r0[-1]).startswith("SALE-")
    dev(a, relay, [{"op": "push"}])
    boot, bpid, bbid = join_second_device(b, relay)
    rb = dev(b, relay, login(TB, boot) + [{"op": "cash_session_open", "branch_id": bbid}])
    assert rb[2]["status_code"] == 200
    dev(b, relay, [{"op": "push"}])
    dev(a, relay, login(TA, shop) + [{"op": "pull"}])
    ra = dev(a, relay, login(TA, shop) + [
        {"op": "cash_session_open", "branch_id": shop["branch_id"]},
        {"op": "sell", "product_id": shop["product_id"], "quantity": 1}])
    assert ra[2]["status_code"] == 200
    assert sale_number(ra[-1]).startswith("SALE-"), f"an existing shop that never opted in now prints {sale_number(ra[-1])}"
    assert book_codes(a, relay) == [] and book_codes(b, relay) == []
    assert till_rows(a, relay) == [] and till_rows(b, relay) == []


NEEDS_MAIN = "TILL_NEEDS_MAIN_COMPUTER"
_FLAG_OFF = {"op": "set_readable_available", "value": False}
# What a refused call must leave exactly as it found it.
_GUARDED = ("till_allocations", "doc_series", "doc_series_counter", "retail_settings", "sync_outbox")


def _new_shop(tmp_path, offline=()):
    """Two REAL computers of one shop with a shared device registry: A is the
    main computer. `offline` computers never pull the registry, so they do not
    know who the main computer is. Both mean the SAME branch."""
    shop = Shop(tmp_path).build(offline=offline)
    shop.patch_branch_uid("b", _branch_uid(shop, "a"))
    return shop


def _branch_id(shop, which):
    return shop.sel(which, "SELECT id FROM branches ORDER BY id LIMIT 1")[0]["id"]


def _branch_uid(shop, which):
    return shop.sel(which, "SELECT uid FROM branches ORDER BY id LIMIT 1")[0]["uid"]


def _open_and_sell(shop, which):
    out = shop.login(which, [
        {"op": "cash_session_open", "branch_id": _branch_id(shop, which)},
        {"op": "sell", "product_id": shop.shop["product_id"], "quantity": 1}])
    assert out[0]["status_code"] == 200, out[0]
    assert out[1]["status_code"] == 200, out[1]
    return out[1]["body"]["data"]["sale_number"]


def _sell(shop, which):
    [r] = shop.login(which, [{"op": "sell", "product_id": shop.shop["product_id"], "quantity": 1}])
    assert r["status_code"] == 200, r
    return r["body"]["data"]["sale_number"]


def _codes(shop, which):
    return sorted(r["code"] for r in shop.sel(which, "SELECT code FROM doc_series"))


def _quarantine(shop, which):
    return shop.sel(which, "SELECT entity_type, reason FROM sync_apply_quarantine")


def _settings_rows(shop, which):
    return shop.sel(which, "SELECT skey FROM retail_settings WHERE skey LIKE 'numbering_%'")


def _allocs(shop, which):
    return shop.sel(which, "SELECT id, terminal_uid, till_number, status, admin_epoch "
                           "FROM till_allocations ORDER BY id")


def _snapshot(shop, which):
    acts = [{"op": "raw_select", "sql": f"SELECT * FROM {t} ORDER BY 1", "params": []} for t in _GUARDED]
    return {t: r["rows"] for t, r in zip(_GUARDED, shop.run(which, acts))}


def _set_numbering(shop, which, **body):
    [r] = shop.login(which, [{"op": "numbering_set", **body}])
    return r


def _off(shop, which, acts):
    """Run `acts` on `which` with the kill switch OFF inside that process."""
    return shop.login(which, [_FLAG_OFF] + list(acts), readable=False)[1:]


_ON = {"branch_code": "AAA", "readable_enabled": True, "till_number": 1}


def test_t3_the_switch_lives_on_one_computer_only(tmp_path):
    """The main computer opts in with till 1. B must never receive that switch."""
    shop = _new_shop(tmp_path)
    on = _set_numbering(shop, "a", **_ON)
    assert on["status_code"] == 200, on
    assert on["body"]["data"]["readable_enabled"] is True and on["body"]["data"]["till_number"] == 1
    assert _open_and_sell(shop, "a").startswith("AAA1")
    assert _codes(shop, "a"), "opting in and opening a shift created no books"
    assert _open_and_sell(shop, "b").startswith("SALE-"), "B numbered readably without ever being switched on"
    shop.sync_all()
    assert _settings_rows(shop, "b") == [], "A's device-local switch travelled to B"
    [view_b] = shop.login("b", [{"op": "numbering_get"}])
    assert view_b["body"]["data"]["readable_enabled"] is False
    assert view_b["body"]["data"]["till_number"] is None
    assert _sell(shop, "b").startswith("SALE-"), "B started numbering readably after hearing about A's books"


def test_t4_a_taken_till_number_is_refused_and_a_free_one_works(tmp_path):
    """A (main) takes till 1 and syncs. Giving B the same number is refused with
    TILL_NUMBER_TAKEN and NOTHING is written; till 2 works."""
    shop = _new_shop(tmp_path)
    b = shop.ids["b"]
    assert _set_numbering(shop, "a", **_ON)["status_code"] == 200
    _open_and_sell(shop, "a")
    shop.sync_all()
    before = _snapshot(shop, "a")
    [same] = shop.login("a", [{"op": "allocate_till", "terminal_uid": b, "till_number": 1}])
    assert same["status_code"] == 409, same
    assert same["body"].get("code") == "TILL_NUMBER_TAKEN", same["body"]
    assert _snapshot(shop, "a") == before, "a refused request still wrote something"
    [free] = shop.login("a", [{"op": "allocate_till", "terminal_uid": b, "till_number": 2}])
    assert free["status_code"] == 200, free
    shop.sync_all()
    assert _open_and_sell(shop, "b").startswith("AAA2"), "B did not print its own range"
    shop.sync_all()
    for w in ("a", "b"):
        assert _quarantine(shop, w) == [], f"{w}: books quarantined"
    assert _codes(shop, "a") == _codes(shop, "b")


def test_t5_an_offline_second_computer_is_refused_and_nothing_is_parked(tmp_path):
    """REWRITTEN ON PURPOSE for schema v42 (see the module docstring).

    B has never heard who the main computer is. It tries to give itself till 1:
    refused, and NOTHING is written -- no allocation, no book, no setting, no
    queued sync event. So when the two computers meet there is nothing to
    quarantine (the old T5 pinned exactly that quarantine)."""
    shop = _new_shop(tmp_path, offline=("b",))
    shop.login("b", [])                        # settle whatever a login itself writes
    before = _snapshot(shop, "b")
    r = _set_numbering(shop, "b", **_ON)
    assert r["status_code"] == 409, r
    assert r["body"]["code"] == NEEDS_MAIN, r["body"]
    assert "main computer" in r["body"]["message"]
    assert _snapshot(shop, "b") == before, "a refused request still wrote or parked something"
    # The main computer can still give itself till 1 (the allow half).
    assert _set_numbering(shop, "a", **_ON)["status_code"] == 200
    assert _open_and_sell(shop, "a").startswith("AAA1")
    assert _open_and_sell(shop, "b").startswith("SALE-")
    shop.sync_all()
    # S-AUTH (AUDIT-033): B has still never heard who the main computer is, so the
    # main's authority events (its book, its allocation) are PARKED on B -- not
    # applied, and not lost -- with the plain reason that B cannot yet tell the real
    # main from a forgery. WHAT THIS CAN NO LONGER CATCH: B applying the main's books
    # before it has heard who the main is.
    assert _quarantine(shop, "a") == [], "a: something was parked"
    parked_on_b = {r["reason"] for r in _quarantine(shop, "b")}
    assert parked_on_b <= {"main_unknown"}, f"b: {parked_on_b}"
    # Once B's registry stream delivers the assignment the same parked events apply
    # on the next pull, and nothing is left parked anywhere (the original claim).
    shop.run("b", [{"op": "reg_pull"}])
    shop.sync_all()
    for w in ("a", "b"):
        assert _quarantine(shop, w) == [], f"{w}: something was parked"
        rows = shop.sel(w, "SELECT code, COUNT(*) AS n FROM doc_series "
                           "GROUP BY doc_type, code HAVING n > 1")
        assert rows == [], f"{w}: duplicate book codes reached the shop's books"


def test_t5b_two_main_computers_end_with_one_till_1_and_the_clash_parked_for_review(tmp_path):
    """The residual the registry cannot rule out: after a takeover that one
    computer never heard of, BOTH hold the main role and both give till 1 away.
    They cannot know. When the two allocations meet exactly one survives -- the
    same one on both machines (newer main epoch wins) -- and the other is marked
    'superseded', not deleted, so the history stays. Pinned so it can never get
    quieter or louder by accident. The numbers the loser already printed are NOT
    rescued: they land in the review queue of both machines."""
    shop = _new_shop(tmp_path)
    a, b = shop.ids["a"], shop.ids["b"]
    # B becomes a second main computer with a NEWER epoch; registries are not
    # synced again, so A never learns of it. (One admin per shop in a registry, so
    # B's copy demotes A before it promotes B.)
    shop.run("b", [
        {"op": "reg_exec", "sql": "UPDATE devices SET is_admin_device=0 WHERE id=?", "params": [a]},
        {"op": "reg_exec", "sql": "UPDATE devices SET is_admin_device=1 WHERE id=?", "params": [b]},
        {"op": "reg_exec", "sql": "UPDATE admin_device_assignment SET epoch=epoch+1", "params": []}])
    for w in ("a", "b"):
        assert _set_numbering(shop, w, **_ON)["status_code"] == 200, w   # nothing can refuse either
    won_a = shop.sel("a", "SELECT id FROM till_allocations WHERE terminal_uid=?", [a])[0]["id"]
    won_b = shop.sel("b", "SELECT id FROM till_allocations WHERE terminal_uid=?", [b])[0]["id"]
    epochs = {r["id"]: r["admin_epoch"] for r in _allocs(shop, "a") + _allocs(shop, "b")}
    assert epochs[won_b] > epochs[won_a], epochs            # the setup did what it claims
    _open_and_sell(shop, "a")
    _open_and_sell(shop, "b")
    shop.sync_all()
    # CHANGED ON PURPOSE (1e7194cb, fix crew F2 / money verifier M2): the engine no
    # longer takes a till number from a main computer it has never heard of, so on
    # A (registry still at epoch 1) B's newer-epoch allocation is PARKED as
    # allocation_epoch_unknown until A's registry catches up. This test can no
    # longer catch a double-main clash converging WITHOUT the registry catching up
    # -- which is now refused by design (it is exactly how a forged allocation got
    # in). A then learns of B's role, as a registry pull would deliver it, and the
    # retry loop replays the parked allocation.
    parked_on_a = shop.sel("a", "SELECT entity_id, reason FROM sync_apply_quarantine "
                                "WHERE entity_type='till_allocation'")
    assert [(r["entity_id"], r["reason"]) for r in parked_on_a] == \
        [(won_b, "allocation_epoch_unknown")], parked_on_a
    shop.run("a", [
        {"op": "reg_exec", "sql": "UPDATE devices SET is_admin_device=0 WHERE id=?", "params": [a]},
        {"op": "reg_exec", "sql": "UPDATE devices SET is_admin_device=1 WHERE id=?", "params": [b]},
        {"op": "reg_exec", "sql": "UPDATE admin_device_assignment SET epoch=epoch+1, device_id=?",
         "params": [b]}])
    shop.sync_all()
    on_a, on_b = _allocs(shop, "a"), _allocs(shop, "b")
    assert on_a == on_b, (on_a, on_b)                       # same rows, same verdicts
    active = [r for r in on_a if r["status"] == "active" and r["till_number"] == 1]
    assert [r["id"] for r in active] == [won_b], on_a
    lost = [r for r in on_a if r["id"] == won_a]
    assert lost and lost[0]["status"] == "superseded", on_a
    shop.sync_all()
    assert _allocs(shop, "a") == on_a and _allocs(shop, "b") == on_b   # a second round changes nothing
    # What the loser's ALREADY-PRINTED numbers do to the books. Observed on a real
    # run (2026-09-30) and pinned so it can never get quieter or louder by accident:
    # each computer keeps the one sale it rang (both are AAA1-000001 -- that
    # collision is the residual itself, the registry cannot rule it out), and the
    # OTHER computer's copy of that sale, its books and everything hanging off the
    # sale are parked for a person to review, on BOTH machines, the same way. Nothing
    # is merged silently and nothing is thrown away.
    sales_a = shop.sel("a", "SELECT sale_number FROM sales ORDER BY id")
    sales_b = shop.sel("b", "SELECT sale_number FROM sales ORDER BY id")
    assert sales_a == [{"sale_number": "AAA1-000001"}], sales_a
    assert sales_b == [{"sale_number": "AAA1-000001"}], sales_b
    q_a, q_b = _quarantine(shop, "a"), _quarantine(shop, "b")
    assert q_a == q_b, (q_a, q_b)                            # symmetric: same verdict on both
    reasons = {(r["entity_type"], r["reason"]) for r in q_a}
    assert ("sale", "duplicate_document_number") in reasons, q_a      # the collision is SEEN
    assert ("doc_series", "duplicate_series_code") in reasons, q_a    # ...and so is the clash of books
    assert ("sale_item", "missing_parent:sale") in reasons, q_a       # the sale's children wait with it
    assert ("payment", "missing_parent:sale") in reasons, q_a
    # Stock and books wait with the sale too (crew Y, 52aa477c): a parked sale must
    # not have already taken stock or posted a journal on this machine.
    assert ("inventory_movement", "missing_parent:sale") in reasons, q_a
    assert ("gl_journal", "missing_parent:sale") in reasons, q_a


def test_t6_the_kill_switch_refuses_the_opt_in_on_both_computers(tmp_path):
    """With READABLE_NUMBERS_AVAILABLE off (the operator's kill switch; until v42
    this was the SHIPPED state, now the flag defaults on) the opt-in is refused
    with a plain sentence and nothing is written on either computer."""
    shop = _new_shop(tmp_path)
    for w in ("a", "b"):
        before = _snapshot(shop, w)
        r = _off(shop, w, [{"op": "numbering_set", **_ON}])[-1]
        assert r["status_code"] == 409, r
        assert r["body"]["code"] == "READABLE_NEEDS_DEVICE_REGISTRY", r["body"]
        assert r["body"]["message"], r["body"]
        assert _snapshot(shop, w) == before, "a refused request still wrote the switch"
    [view] = _off(shop, "a", [{"op": "numbering_get"}])[-1:]
    d = view["body"]["data"]
    assert d["available"] is False and d["readable_enabled"] is False, d
    assert d["unavailable_reason"], d
    assert d["readable_switched_off_by_update"] is False, d
    # both first shifts and sales stay on the long default numbers, sync clean
    assert _off(shop, "a", [{"op": "cash_session_open", "branch_id": _branch_id(shop, "a")},
                            {"op": "sell", "product_id": shop.shop["product_id"], "quantity": 1}])[-1][
        "body"]["data"]["sale_number"].startswith("SALE-")
    assert _off(shop, "b", [{"op": "cash_session_open", "branch_id": _branch_id(shop, "b")},
                            {"op": "sell", "product_id": shop.shop["product_id"], "quantity": 1}])[-1][
        "body"]["data"]["sale_number"].startswith("SALE-")
    shop.sync_all()
    for w in ("a", "b"):
        assert _quarantine(shop, w) == []
        assert _codes(shop, w) == []


def test_t7_a_computer_that_had_readable_numbers_when_the_switch_went_off_is_retired(tmp_path):
    """The already-ON computer. The main computer opts in and opens a shift (the
    books exist); then the kill switch goes off in every later process."""
    shop = _new_shop(tmp_path)
    on = _set_numbering(shop, "a", **_ON)
    assert on["status_code"] == 200, on
    [shift] = shop.login("a", [{"op": "cash_session_open", "branch_id": _branch_id(shop, "a")}])
    assert shift["status_code"] == 200, shift
    session_id = shift["session_id"]
    active = shop.sel("a", "SELECT code FROM doc_series WHERE status='active'")
    assert active, "setup failed: the books were not created"
    [closed] = _off(shop, "a", [{"op": "cash_session_close", "session_id": session_id,
                                 "closing_float_counted": 100.0}])[-1:]
    assert closed["status_code"] == 200, closed
    # the switched-off build reads the stored ON switch as off, plainly, first
    [view] = _off(shop, "a", [{"op": "numbering_get"}])[-1:]
    d = view["body"]["data"]
    assert d["available"] is False and d["readable_enabled"] is False, d
    assert d["readable_switched_off_by_update"] is True, d
    # Reworded on purpose (v42): the old "until multi-computer support arrives" promise is
    # gone. Literal text, so the test still catches the wording drifting back.
    assert ("it has no till number from the main computer now"
            in d["unavailable_reason"]), d
    assert "multi-computer support" not in d["unavailable_reason"], d
    # the NEXT shift creates nothing, retires the books, sells on the long number
    [shift2] = _off(shop, "a", [{"op": "cash_session_open", "branch_id": _branch_id(shop, "a")}])[-1:]
    assert shift2["status_code"] == 200, shift2
    assert shop.sel("a", "SELECT code FROM doc_series WHERE status='active'") == [], \
        "the readable books are still active after the withdrawal"
    retired = shop.sel("a", "SELECT code FROM doc_series WHERE status='retired'")
    assert sorted(r["code"] for r in retired) == sorted(r["code"] for r in active)
    audited = shop.sel("a", "SELECT COUNT(*) AS n FROM audit_log WHERE action='NUMBERING_READABLE_WITHDRAWN'")
    assert audited[0]["n"] == 1, audited
    sale = _off(shop, "a", [{"op": "sell", "product_id": shop.shop["product_id"], "quantity": 1}])[-1]
    assert sale["body"]["data"]["sale_number"].startswith("SALE-"), sale


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
    monkeypatch.setenv("AURA_ACCEPT_SIGNED_LICENCE", "LIC-V42-TEST-1")
