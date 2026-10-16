"""Aura Retail -- STOCK-TAKE across TWO computers, through the REAL routes, the REAL FileRelay
and a REAL second OS process (harness shared with retail_po_sync_twodevice_test.py).

THE CLAIM. A count started and counted on A reaches B (header and lines), and when BOTH
computers post it before they have heard from each other, the stock moves ONCE on each, the
books agree, and the header ends 'posted' on both.

  1. A starts a count and counts 6 of a product holding 10 (variance -4, 2.000 each); after a sync B
     holds the same open count and the same line.
  2. only the MAIN computer posts: B's post is refused (and changes nothing); A's post moves the stock
     once on BOTH, one movement and one journal, identical trial balances, 5910 = 8.000.
  3. two computers start a count of one branch while offline and scan the same product: after sync
     ONE open count (smaller uid) on both, the other 'merged' into it, 5 + 5 = 10, one post.
  4. the same product counted on both computers = two contributions that ADD.

MUTATIONS (quoted in the commit): skip the convergence -> test 3 RED; drop the main check -> test 2
RED; (earlier) random movement uid -> the stock moves twice.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from retail_po_sync_twodevice_test import Shop, _tb  # noqa: E402
from _stock_sync_harness import run_device  # noqa: E402

PY = sys.executable
COST = 2.0


def _provision(tmp_path):
    s = Shop(tmp_path)
    [s.boot['a']] = run_device(PY, s.app['a'], s.relay, [{"op": "bootstrap"}])
    [s.boot['b']] = run_device(PY, s.app['b'], s.relay, [{"op": "bootstrap"}])
    br, p1, p2 = s.run(
        'a',
        {"op": "create_branch", "name": "Warehouse", "address": "1 Rd", "phone": "555-0100"},
        {"op": "create_product", "name": "Count Widget One", "price": 4.0, "initial_stock": 10},
        {"op": "create_product", "name": "Count Widget Two", "price": 4.0, "initial_stock": 10},
    )
    assert br["status_code"] == 200 and p1["status_code"] == 200 and p2["status_code"] == 200, (br, p1, p2)
    s.p1, s.p2 = p1["product_id"], p2["product_id"]
    # A is the shop's MAIN computer (only it may post a count); B is an ordinary second computer.
    [made] = s.run('a', {"op": "make_main"})
    assert made.get("ok") is True, made
    s.sync('a')
    s.pull('b')
    return s


def _wh(s, who):
    rows = s.sql(who, "SELECT id FROM branches WHERE name='Warehouse'")
    assert len(rows) == 1, (who, rows)
    return rows[0]["id"]


def _on_hand(s, who, pid):
    return s.sql(who, "SELECT COALESCE(SUM(quantity_on_hand),0) AS q FROM inventory_balances "
                      "WHERE product_id=? AND branch_id=?", (pid, _wh(s, who)))[0]["q"]


def _start_and_count_on_a(s):
    r = s.http('a', 'POST', '/stock-counts', {"branch_id": _wh(s, 'a')})
    assert r["status_code"] == 200, r
    uid = r["body"]["data"]["uid"]
    r = s.http('a', 'POST', f'/stock-counts/{uid}/lines', {"product_id": s.p1, "quantity": 6, "mode": "set"})
    assert r["status_code"] == 200, r
    s.sync('a')
    return uid


def _count_movements(s, who, uid):
    return s.sql(who, "SELECT quantity, uid FROM inventory_movements WHERE notes=?", (f"Stock count {uid[:8]}",))


def _count_journals(s, who, uid):
    movs = [m["uid"] for m in _count_movements(s, who, uid)]
    return [j for j in s.sql(who, "SELECT source_uid FROM gl_journals") if j["source_uid"] in movs]


def test_1_a_count_started_and_counted_on_a_is_the_same_open_count_on_b(tmp_path):
    s = _provision(tmp_path)
    uid = _start_and_count_on_a(s)
    s.pull('b')
    head = s.sql('b', "SELECT status, branch_id FROM stock_counts WHERE uid=?", (uid,))
    assert len(head) == 1 and head[0]["status"] == "open" and head[0]["branch_id"] == _wh(s, 'b'), head
    line = s.sql('b', "SELECT counted_qty, system_qty_at_count, variance FROM stock_count_lines "
                      "WHERE count_uid=? AND product_id=?", (uid, s.p1))
    assert line == [{"counted_qty": 6.0, "system_qty_at_count": 10.0, "variance": -4.0}], line
    # Counting applied no stock and no books on B.
    assert _on_hand(s, 'b', s.p1) == 10 and _count_movements(s, 'b', uid) == []


def test_2_only_the_main_computer_posts_and_the_stock_moves_once_on_both(tmp_path):
    s = _provision(tmp_path)
    uid = _start_and_count_on_a(s)
    s.pull('b')
    refused = s.http('b', 'POST', f'/stock-counts/{uid}/post')
    assert refused["status_code"] == 409 and refused["body"]["code"] == "STOCK_COUNT_NEEDS_MAIN", refused
    assert _on_hand(s, 'b', s.p1) == 10 and _count_movements(s, 'b', uid) == []   # a refusal changes nothing
    ra = s.http('a', 'POST', f'/stock-counts/{uid}/post')
    assert ra["status_code"] == 200, ra
    s.settle()
    for who in ('a', 'b'):
        assert _on_hand(s, who, s.p1) == 6, (who, _on_hand(s, who, s.p1))
        assert [m["quantity"] for m in _count_movements(s, who, uid)] == [-4.0], who
        assert len(_count_journals(s, who, uid)) == 1, (who, _count_journals(s, who, uid))
        assert s.sql(who, "SELECT status FROM stock_counts WHERE uid=?", (uid,))[0]["status"] == "posted"
    books = {who: {a['code']: a['balance'] for a in _tb(s, who)['accounts']} for who in ('a', 'b')}
    assert books['a'] == books['b']
    assert books['a']['5910'] == 8.0                                         # 4 x 2.000, once
    assert books['a']['1200'] == 32.0                                        # 2 x 10 x 2.000 - 8.000
    # B now shows the posted count at the cost the post booked at, from the synced movement.
    view = s.http('b', 'GET', f'/stock-counts/{uid}')
    assert view["body"]["data"]["totals"]["variance_value"] == -8.0


def test_3_rival_counts_started_offline_converge_and_scans_add_up(tmp_path):
    """The review's HIGH defect: A and B each start a count of the Warehouse while offline, both
    scan the same product (5 each), system says 12. Before the fix: two counts, two posts, the
    shelf corrected twice. Now: after sync both hold ONE open count (the smaller uid), the other is
    'merged' into it on BOTH computers, the quantity is 5 + 5 = 10, and one post moves 12 -> 10."""
    s = _provision(tmp_path)
    # bring the product to 12 on both computers first
    assert s.http('a', 'POST', f'/products/{s.p1}/stock-adjust',
                  {"quantity": 2, "reason": "added", "branch_id": _wh(s, 'a')})["status_code"] == 200
    s.settle()
    ua = s.http('a', 'POST', '/stock-counts', {"branch_id": _wh(s, 'a')})["body"]["data"]["uid"]
    ub = s.http('b', 'POST', '/stock-counts', {"branch_id": _wh(s, 'b')})["body"]["data"]["uid"]
    assert ua != ub
    for who, uid in (('a', ua), ('b', ub)):
        r = s.http(who, 'POST', f'/stock-counts/{uid}/lines', {"product_id": s.p1, "quantity": 5, "mode": "set"})
        assert r["status_code"] == 200, r
    s.settle()
    winner = min(ua, ub)
    loser = max(ua, ub)
    for who in ('a', 'b'):
        heads = {r["uid"]: r for r in s.sql(who, "SELECT uid, status, merged_into FROM stock_counts")}
        assert heads[winner]["status"] == "open", (who, heads)
        assert heads[loser]["status"] == "merged" and heads[loser]["merged_into"] == winner, (who, heads)
        parts = s.sql(who, "SELECT count_uid, counted_qty FROM stock_count_lines WHERE product_id=?", (s.p1,))
        assert sorted(p["counted_qty"] for p in parts) == [5.0, 5.0] and {p["count_uid"] for p in parts} == {winner}, (who, parts)
    # a merged count can no longer be POSTED, on either computer (a scan on it is followed to the winner:
    # proved in retail_stock_take_test.py test_7a)
    for who in ('a', 'b'):
        r = s.http(who, 'POST', f'/stock-counts/{loser}/post')
        assert r["status_code"] == 409 and r["body"]["code"] == "STOCK_COUNT_MERGED", (who, r)
    r = s.http('a', 'POST', f'/stock-counts/{winner}/post')
    assert r["status_code"] == 200, r
    s.settle()
    for who in ('a', 'b'):
        assert _on_hand(s, who, s.p1) == 10, (who, _on_hand(s, who, s.p1))          # 12 + (10 - 12), once
        assert len(_count_movements(s, who, winner)) == 1 and _count_movements(s, who, loser) == []


def test_4_the_same_product_counted_on_both_computers_is_two_contributions_that_add(tmp_path):
    s = _provision(tmp_path)
    uid = _start_and_count_on_a(s)                       # A: 6 of product one
    s.pull('b')
    r = s.http('b', 'POST', f'/stock-counts/{uid}/lines', {"product_id": s.p1, "quantity": 7, "mode": "set"})
    assert r["status_code"] == 200 and r["body"]["data"]["counted_qty"] == 13.0 and r["body"]["data"]["this_device_qty"] == 7.0, r
    r = s.http('b', 'POST', f'/stock-counts/{uid}/lines', {"product_id": s.p2, "quantity": 9, "mode": "set"})
    assert r["status_code"] == 200, r
    s.settle()
    for who in ('a', 'b'):
        lines = s.http(who, 'GET', f'/stock-counts/{uid}')["body"]["data"]["lines"]
        assert sorted((ln["product_id"], ln["counted_qty"]) for ln in lines) == sorted(
            [(s.p1, 13.0), (s.p2, 9.0)]), (who, lines)


def _books(s):
    books = {who: {a['code']: a['balance'] for a in _tb(s, who)['accounts']} for who in ('a', 'b')}
    assert books['a'] == books['b'], books
    return books['a']


def test_5_a_scan_that_reaches_the_main_computer_after_the_post_is_corrected_once(tmp_path):
    """HIGH (round 3): A (main) counts 6 of 10 and posts (-4). B scanned 2 more OFFLINE before it heard
    of the post. When B's scan arrives, A books +2 (total 8 vs 10 = -2, already posted -4): on-hand 8
    and 5910 = 4.000 on BOTH computers; syncing again changes nothing."""
    s = _provision(tmp_path)
    uid = _start_and_count_on_a(s)                       # A: 6 of product one, synced
    s.pull('b')
    r = s.http('b', 'POST', f'/stock-counts/{uid}/lines', {"product_id": s.p1, "quantity": 2})   # offline on B
    assert r["status_code"] == 200, r
    assert s.http('a', 'POST', f'/stock-counts/{uid}/post')["status_code"] == 200
    assert _on_hand(s, 'a', s.p1) == 6
    s.settle()
    for who in ('a', 'b'):
        assert _on_hand(s, who, s.p1) == 8, (who, _on_hand(s, who, s.p1))
    after_first = _books(s)
    assert after_first['5910'] == 4.0 and after_first['1200'] == 36.0       # (-4 then +2) x 2.000; 40 - 4
    late = s.sql('a', "SELECT quantity FROM inventory_movements WHERE notes LIKE ? ORDER BY quantity",
                 (f"Stock count {uid[:8]} (late)",))
    assert [m["quantity"] for m in late] == [2.0]
    s.settle()                                           # re-sync: nothing moves again
    assert [_on_hand(s, w, s.p1) for w in ('a', 'b')] == [8, 8] and _books(s) == after_first
    assert len(s.sql('a', "SELECT 1 FROM inventory_movements WHERE notes LIKE ?",
                     (f"Stock count {uid[:8]} (late)",))) == 1


def test_6_a_rival_count_started_before_the_post_is_merged_into_it_and_cannot_be_posted_again(tmp_path):
    """HIGH (round 3): B starts its own count of the Warehouse offline and scans 4; A (main) counts 6 and
    posts. When B's count reaches A it started BEFORE the post, so it is merged into the posted count
    (not left open): 6 + 4 = 10 against 10 = no net difference (the +4 late correction cancels the -4).
    A rival started AFTER the post is a legitimate new count and stays open."""
    s = _provision(tmp_path)
    ua = s.http('a', 'POST', '/stock-counts', {"branch_id": _wh(s, 'a')})["body"]["data"]["uid"]
    ub = s.http('b', 'POST', '/stock-counts', {"branch_id": _wh(s, 'b')})["body"]["data"]["uid"]
    assert s.http('a', 'POST', f'/stock-counts/{ua}/lines', {"product_id": s.p1, "quantity": 6, "mode": "set"})["status_code"] == 200
    assert s.http('b', 'POST', f'/stock-counts/{ub}/lines', {"product_id": s.p1, "quantity": 4, "mode": "set"})["status_code"] == 200
    assert s.http('a', 'POST', f'/stock-counts/{ua}/post')["status_code"] == 200       # A has not heard of B's count
    s.settle()
    for who in ('a', 'b'):
        heads = {r["uid"]: r for r in s.sql(who, "SELECT uid, status, merged_into FROM stock_counts")}
        assert heads[ua]["status"] == "posted" and heads[ub]["status"] == "merged" and heads[ub]["merged_into"] == ua, (who, heads)
        assert _on_hand(s, who, s.p1) == 10, (who, _on_hand(s, who, s.p1))
        r = s.http(who, 'POST', f'/stock-counts/{ub}/post')
        assert r["status_code"] == 409, (who, r)                                         # cannot be posted again
    _books(s)
    # ... and a count started AFTER the post (B had already heard of it) is a legitimate new one.
    new = s.http('b', 'POST', '/stock-counts', {"branch_id": _wh(s, 'b')})
    assert new["status_code"] == 200, new
    s.settle()
    for who in ('a', 'b'):
        status = s.sql(who, "SELECT status FROM stock_counts WHERE uid=?", (new["body"]["data"]["uid"],))
        assert status == [{"status": "open"}], (who, status)
