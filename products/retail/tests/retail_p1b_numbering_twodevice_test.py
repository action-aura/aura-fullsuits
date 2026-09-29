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
  T3  one computer opts in: the switch is device-local -- the other computer
      never receives it and keeps its legacy numbers.
  T4  the same till number, chosen on the second computer AFTER it has heard
      of the first one's books, is refused (409 TILL_NUMBER_TAKEN); a different
      number works, and the two computers then print different number ranges
      with nothing quarantined.
  T5  the OFFLINE window that no code can close: two computers that have not
      synced both choose till 1. Nothing collides on the shop's books -- the
      second one to sync is quarantined, exactly as before. This test PINS
      that this still happens (and that it stays invisible to the owner) so a
      future change that hides or "fixes" it must update this test on purpose.
      The lasting fix is the device registry (Track S).

  T6  ROUND 3: in the shipped build readable numbers are UNAVAILABLE until the
      device registry syncs (Track S), so the very same opt-in is refused
      (409 READABLE_NEEDS_DEVICE_REGISTRY) on both computers and nothing is
      written. T3-T5 above can only be reached by flipping
      `retail_api.READABLE_NUMBERS_AVAILABLE` inside each device process (the
      `readable_available` fixture); they are kept as a PIN of the underlying
      window Track S has to close, and CANNOT be reached through the route as
      shipped.
  T7  ROUND 3: a computer an earlier build left switched ON: its next shift
      creates no books, its sale keeps the long default number, GET says it was
      switched off, and the readable books it made are retired and audited.

The harness is `_accept_harness` + `FileRelay`, the same one the acceptance
pass uses; the additions in `_accept_device.py` are `numbering_get` /
`numbering_set` (route drivers), `set_readable_available` (flips the flag inside
one device process) and `raw_exec` (plants a state no route can create now).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
from _accept_harness import run_accept_device  # noqa: E402

PY = sys.executable
TA = 'p1b-till-a-0000-0000-000000000001'
TB = 'p1b-till-b-0000-0000-000000000002'


# Round 3: the shipped build refuses to switch readable numbers on (see the
# module docstring). The tests below that pin the UNDERLYING two-computer window
# for Track S flip retail_api.READABLE_NUMBERS_AVAILABLE inside every device
# process they start (a flag lives only as long as its process), via the
# `readable_available` fixture. The tests WITHOUT that fixture run against the
# shipped build unchanged.
_READABLE = {"on": False}


@pytest.fixture()
def readable_available():
    _READABLE["on"] = True
    yield
    _READABLE["on"] = False


def dev(d, relay, acts):
    if _READABLE["on"]:
        flip = {"op": "set_readable_available", "value": True}
        return run_accept_device(PY, d, relay, [flip] + list(acts), timeout=240)[1:]
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


def test_t3_the_switch_lives_on_one_computer_only(two, readable_available):
    """Owner opts in on A with till 1. B must never receive that switch."""
    [_, _, on] = dev(two["a"], two["relay"], login(TA, two["shop"]) + [
        {"op": "numbering_set", "branch_code": "AAA", "readable_enabled": True, "till_number": 1}])
    assert on["status_code"] == 200, on
    assert on["body"]["data"]["readable_enabled"] is True and on["body"]["data"]["till_number"] == 1
    shift_a, sale_a = open_and_sell_a(two)
    assert shift_a["status_code"] == 200
    assert sale_number(sale_a).startswith("AAA1"), sale_number(sale_a)
    assert book_codes(two["a"], two["relay"]), "opting in and opening a shift created no books"
    shift_b, sale_b = open_and_sell_b(two)
    assert sale_number(sale_b).startswith("SALE-"), "B numbered readably without ever being switched on"
    sync_both(two)
    assert till_rows(two["b"], two["relay"]) == [], "A's device-local switch travelled to B"
    [_, view_b] = dev(two["b"], two["relay"], [{"op": "set_terminal_id", "terminal_id": TB},
                                               {"op": "login", "email": two["boot"]["email"], "password": two["boot"]["password"]}]
                      + [{"op": "numbering_get"}])[-2:]
    assert view_b["body"]["data"]["readable_enabled"] is False
    assert view_b["body"]["data"]["till_number"] is None
    sb2 = dev(two["b"], two["relay"], login(TB, two["boot"]) + [
        {"op": "sell", "product_id": two["bpid"], "quantity": 1}])
    assert sale_number(sb2[-1]).startswith("SALE-"), "B started numbering readably after hearing about A's books"


def _numbering(t, who, term, acts):
    return dev(t[who], t["relay"], login(term, t["shop" if who == "a" else "boot"]) + acts)[-len(acts):]


def test_t4_a_known_till_number_is_refused_and_a_free_one_works(two, readable_available):
    """A takes till 1 and syncs. B has heard of A's books, so B choosing till 1
    is refused with TILL_NUMBER_TAKEN and NOTHING is written; till 2 works."""
    [on] = _numbering(two, "a", TA, [{"op": "numbering_set", "branch_code": "AAA", "readable_enabled": True, "till_number": 1}])
    assert on["status_code"] == 200, on
    open_and_sell_a(two)
    sync_both(two)
    [same] = _numbering(two, "b", TB, [{"op": "numbering_set", "branch_code": "AAA", "readable_enabled": True, "till_number": 1}])
    assert same["status_code"] == 409, same
    assert same["body"].get("code") == "TILL_NUMBER_TAKEN", same["body"]
    assert till_rows(two["b"], two["relay"]) == [], "a refused request still wrote the switch"
    [free] = _numbering(two, "b", TB, [{"op": "numbering_set", "branch_code": "AAA", "readable_enabled": True, "till_number": 2}])
    assert free["status_code"] == 200, free
    shift_b, sale_b = open_and_sell_b(two)
    assert shift_b["status_code"] == 200 and sale_number(sale_b).startswith("AAA2"), sale_number(sale_b)
    sync_both(two)
    for d in ("a", "b"):
        assert sel(two[d], two["relay"], "SELECT 1 FROM sync_apply_quarantine") == [], f"{d}: books quarantined"
    assert book_codes(two["a"], two["relay"]) == book_codes(two["b"], two["relay"])


def test_t5_the_offline_window_still_ends_in_quarantine_and_the_owner_is_not_told(two, readable_available):
    """Both computers choose till 1 while offline from each other. The tool
    cannot know. Pinned so it can never get quieter or louder by accident."""
    for who, term, acts in (("a", TA, [{"op": "numbering_set", "branch_code": "AAA", "readable_enabled": True, "till_number": 1}]),
                            ("b", TB, [{"op": "numbering_set", "branch_code": "AAA", "readable_enabled": True, "till_number": 1}])):
        [r] = _numbering(two, who, term, acts)
        assert r["status_code"] == 200, r   # B has not heard of A's books: nothing can refuse it
    open_and_sell_a(two)
    open_and_sell_b(two)
    sync_both(two)
    qa = sel(two["a"], two["relay"], "SELECT entity_type, reason FROM sync_apply_quarantine")
    qb = sel(two["b"], two["relay"], "SELECT entity_type, reason FROM sync_apply_quarantine")
    quarantined = [r for r in qa + qb if r["entity_type"] == "doc_series"]
    assert quarantined, ("two computers with the same till number did not quarantine a doc_series row -- "
                         f"the window closed or moved; update this pin on purpose: A={qa} B={qb}")
    for d in ("a", "b"):
        rows = sel(two[d], two["relay"], "SELECT code, COUNT(*) AS n FROM doc_series GROUP BY doc_type, code HAVING n > 1")
        assert rows == [], f"{d}: duplicate book codes reached the shop's books"


def test_t6_the_shipped_build_refuses_to_switch_readable_numbers_on(two):
    """Round 3 decision 1, from the outside on two real computers: the opt-in
    that T3-T5 exercise (with the flag flipped) is REFUSED in the shipped build,
    with a plain sentence, and nothing is written on either computer -- so the
    two-computer window they pin cannot be entered at all."""
    for who, term in (("a", TA), ("b", TB)):
        [r] = _numbering(two, who, term, [
            {"op": "numbering_set", "branch_code": "AAA", "readable_enabled": True, "till_number": 1}])
        assert r["status_code"] == 409, r
        assert r["body"]["code"] == "READABLE_NEEDS_DEVICE_REGISTRY", r["body"]
        assert r["body"]["message"], r["body"]
        assert till_rows(two[who], two["relay"]) == [], "a refused request still wrote the switch"
    [view] = _numbering(two, "a", TA, [{"op": "numbering_get"}])
    d = view["body"]["data"]
    assert d["available"] is False and d["readable_enabled"] is False, d
    assert d["unavailable_reason"], d
    assert d["readable_switched_off_by_update"] is False, d
    # both first shifts and sales stay on the long default numbers, sync clean
    _, sale_a = open_and_sell_a(two)
    _, sale_b = open_and_sell_b(two)
    assert sale_number(sale_a).startswith("SALE-") and sale_number(sale_b).startswith("SALE-")
    sync_both(two)
    for d_ in ("a", "b"):
        assert sel(two[d_], two["relay"], "SELECT 1 FROM sync_apply_quarantine") == []
        assert book_codes(two[d_], two["relay"]) == []


def test_t7_a_computer_left_on_by_an_earlier_build_is_switched_off_and_its_books_retired(two):
    """Round 3 decision 1, the already-ON computer. Process 1 has the flag flipped
    on and opts in (the state an earlier build left behind), then a shift opens
    and the books exist. Process 2 is the shipped build."""
    _READABLE["on"] = True
    try:
        [on] = _numbering(two, "a", TA, [
            {"op": "numbering_set", "branch_code": "AAA", "readable_enabled": True, "till_number": 1}])
        assert on["status_code"] == 200, on
        [shift] = dev(two["a"], two["relay"], login(TA, two["shop"]) + [
            {"op": "cash_session_open", "branch_id": two["shop"]["branch_id"]}])[-1:]
        assert shift["status_code"] == 200, shift
        session_id = shift["session_id"]
    finally:
        _READABLE["on"] = False
    active = sel(two["a"], two["relay"], "SELECT code FROM doc_series WHERE status='active'")
    assert active, "setup failed: the earlier build's books were not created"
    [closed] = dev(two["a"], two["relay"], login(TA, two["shop"]) + [
        {"op": "cash_session_close", "session_id": session_id, "closing_float_counted": 100.0}])[-1:]
    assert closed["status_code"] == 200, closed
    # the shipped build reads the stored ON switch as off, plainly, before anything else
    [view] = _numbering(two, "a", TA, [{"op": "numbering_get"}])
    d = view["body"]["data"]
    assert d["available"] is False and d["readable_enabled"] is False, d
    assert d["readable_switched_off_by_update"] is True, d
    assert "multi-computer support" in d["unavailable_reason"], d
    # the NEXT shift creates nothing, retires the books, sells on the long number
    [shift2] = dev(two["a"], two["relay"], login(TA, two["shop"]) + [
        {"op": "cash_session_open", "branch_id": two["shop"]["branch_id"]}])[-1:]
    assert shift2["status_code"] == 200, shift2
    assert sel(two["a"], two["relay"], "SELECT code FROM doc_series WHERE status='active'") == [],         "the readable books are still active after the withdrawal"
    retired = sel(two["a"], two["relay"], "SELECT code FROM doc_series WHERE status='retired'")
    assert sorted(r["code"] for r in retired) == sorted(r["code"] for r in active)
    audited = sel(two["a"], two["relay"],
                  "SELECT COUNT(*) AS n FROM audit_log WHERE action='NUMBERING_READABLE_WITHDRAWN'")
    assert audited[0]["n"] == 1, audited
    sale = dev(two["a"], two["relay"], login(TA, two["shop"]) + [
        {"op": "sell", "product_id": two["shop"]["product_id"], "quantity": 1}])[-1]
    assert sale_number(sale).startswith("SALE-"), sale_number(sale)
