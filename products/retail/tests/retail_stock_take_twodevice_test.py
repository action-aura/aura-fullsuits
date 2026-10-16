"""Aura Retail -- STOCK-TAKE across TWO computers, through the REAL routes, the REAL FileRelay
and a REAL second OS process (harness shared with retail_po_sync_twodevice_test.py).

THE CLAIM. A count started and counted on A reaches B (header and lines), and when BOTH
computers post it before they have heard from each other, the stock moves ONCE on each, the
books agree, and the header ends 'posted' on both.

  1. A starts a count and counts 6 of a product holding 10 (variance -4, 2.000 each); after a sync B
     holds the same open count and the same line.
  2. A and B each POST it offline; after a full exchange: on-hand 6 on BOTH (not 2), exactly one
     count movement and one count journal on both, identical trial balances, 5910 = 8.000.
  3. B counts a second product on the shared open count while A counts the same product too: one
     line on both after sync (the line's uid is derived from count + product), newest count wins.

MUTATION (quoted in the commit): random movement uid instead of the derived one -> test 2 goes RED
(stock lands at 2, two journals).
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


def test_2_both_computers_post_the_same_count_offline_and_the_stock_moves_once(tmp_path):
    s = _provision(tmp_path)
    uid = _start_and_count_on_a(s)
    s.pull('b')
    ra = s.http('a', 'POST', f'/stock-counts/{uid}/post')
    rb = s.http('b', 'POST', f'/stock-counts/{uid}/post')
    assert ra["status_code"] == 200 and rb["status_code"] == 200, (ra, rb)
    assert _on_hand(s, 'a', s.p1) == 6 and _on_hand(s, 'b', s.p1) == 6      # each moved ONCE locally
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


def test_3_the_same_product_counted_on_both_computers_is_one_line_newest_wins(tmp_path):
    s = _provision(tmp_path)
    uid = _start_and_count_on_a(s)
    s.pull('b')
    r = s.http('b', 'POST', f'/stock-counts/{uid}/lines', {"product_id": s.p1, "quantity": 7, "mode": "set"})
    assert r["status_code"] == 200, r
    r = s.http('b', 'POST', f'/stock-counts/{uid}/lines', {"product_id": s.p2, "quantity": 9, "mode": "set"})
    assert r["status_code"] == 200, r
    s.settle()
    for who in ('a', 'b'):
        lines = s.sql(who, "SELECT product_id, counted_qty FROM stock_count_lines WHERE count_uid=? "
                           "ORDER BY product_id", (uid,))
        assert sorted((ln["product_id"], ln["counted_qty"]) for ln in lines) == sorted(
            [(s.p1, 7.0), (s.p2, 9.0)]), (who, lines)
