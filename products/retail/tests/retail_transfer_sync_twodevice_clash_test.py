"""Aura Retail -- W3b: the CLASH scenarios for a stock transfer across TWO computers -- through the REAL routes, the REAL FileRelay and a REAL second OS
process (harness shared with retail_po_sync_twodevice_test.py).

NOTE: split out of retail_transfer_sync_twodevice_test.py because the runner
(products/run_all_tests.py) kills a file after PER_FILE_TIMEOUT_SECONDS = 1200; the
straight-line scenarios (tests 1-5) stay in that file, which also documents the shared
fixture: one product, 20 units opened at cost 2.000 in branch X "Warehouse", a transfer
of 10 to Y "Storefront".

  6. A recalls while B receives (offline) -> 'received' on both, flagged, computers AGREE;
  7. A cancels while B sends (offline) -> 'in_transit' on both, flagged;
  F1-F3. the verify-round clashes (double send at different costs, overlapping two-line
  receipt, cancel vs send+receive).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from retail_po_sync_twodevice_test import Shop, _tb  # noqa: E402
from _stock_sync_harness import run_device  # noqa: E402

PY = sys.executable
OPEN_QTY = 20
SEND_QTY = 10
COST = 2.0


# ── harness helpers ─────────────────────────────────────────────────────────

def _provision(tmp_path, initial_stock=OPEN_QTY):
    """A: company, branches X (Warehouse, holds the stock) and Y (Storefront), a product.
    B: bootstrap, then pulls everything (branches and the product's movements)."""
    s = Shop(tmp_path)
    [s.boot['a']] = run_device(PY, s.app['a'], s.relay, [{"op": "bootstrap"}])
    [s.boot['b']] = run_device(PY, s.app['b'], s.relay, [{"op": "bootstrap"}])
    x, product, y = s.run(
        'a',
        {"op": "create_branch", "name": "Warehouse", "address": "1 Rd", "phone": "555-0100"},
        {"op": "create_product", "name": "Transfer Sync Widget", "price": 4.0,
         "initial_stock": initial_stock},
        {"op": "create_branch", "name": "Storefront", "address": "2 Rd", "phone": "555-0101"},
    )
    assert x["status_code"] == 200 and product["status_code"] == 200 and y["status_code"] == 200, (x, product, y)
    s.pid = product["product_id"]
    s.sync('a')
    s.pull('b')
    for who in ('a', 'b'):
        assert _stock(s, who) == {"Warehouse": float(initial_stock)}, (who, _stock(s, who))
        assert len(s.sql(who, "SELECT 1 FROM branches")) == 2, who
    return s


def _branch_id(s, who, name):
    rows = s.sql(who, "SELECT id FROM branches WHERE name=?", (name,))
    assert len(rows) == 1, (who, name, rows)
    return rows[0]["id"]


def _stock(s, who):
    """{branch name: quantity_on_hand} for the product (zero rows included)."""
    rows = s.sql(who, "SELECT b.name AS n, ib.quantity_on_hand AS q FROM inventory_balances ib "
                      "JOIN branches b ON b.id=ib.branch_id WHERE ib.product_id=?", (s.pid,))
    return {r["n"]: r["q"] for r in rows}


def _create(s, who, qty=SEND_QTY):
    r = s.http(who, "POST", "/stock-transfers", {
        "source_branch_id": _branch_id(s, who, "Warehouse"),
        "destination_branch_id": _branch_id(s, who, "Storefront"),
        "items": [{"product_id": s.pid, "quantity": qty}]})
    assert r["status_code"] == 200, r
    return r["body"]["data"]["id"]


def _item_id(s, who, tid):
    rows = s.sql(who, "SELECT id FROM stock_transfer_items WHERE transfer_id=?", (tid,))
    assert len(rows) == 1, (who, rows)
    return rows[0]["id"]


def _send(s, who, tid):
    r = s.http(who, "POST", f"/stock-transfers/{tid}/send")
    assert r["status_code"] == 200, r
    return r


def _receive(s, who, tid, qty):
    return s.http(who, "POST", f"/stock-transfers/{tid}/receive",
                  {"items": [{"id": _item_id(s, who, tid), "quantity_received": qty}]})


def _status(s, who, tid):
    rows = s.sql(who, "SELECT status FROM stock_transfers WHERE id=?", (tid,))
    assert len(rows) == 1, (who, tid, rows)
    return rows[0]["status"]


def _conflicts(s, who, reason):
    return [c for c in s.sql(who, "SELECT * FROM sync_conflicts WHERE entity_type='stock_transfer'")
            if f'"reason": "{reason}"' in c["incoming_payload"]]


def _ins(s, who, tid):
    """The transfer_in movements this transfer wrote, as (branch name, quantity)."""
    rows = s.sql(who, "SELECT b.name AS n, m.quantity AS q FROM inventory_movements m "
                      "JOIN branches b ON b.id=m.branch_id "
                      "WHERE m.movement_type='transfer_in' AND m.reference=? ORDER BY m.uid",
                 (f"XFER-{tid}",))
    return sorted((r["n"], r["q"]) for r in rows)


def _books(s):
    """Both computers' trial balances, asserted IDENTICAL (every account, and the
    set of journal uids); returns the {code: balance} map."""
    maps = {}
    for who in ('a', 'b'):
        tb = _tb(s, who)
        maps[who] = {a['code']: a['balance'] for a in tb['accounts']}
    assert maps['a'] == maps['b'], maps
    uids = {who: sorted(r["uid"] for r in s.sql(who, "SELECT uid FROM gl_journals")) for who in ('a', 'b')}
    assert uids['a'] == uids['b'], uids
    return maps['a']


def _acct0(m, code):
    return m.get(code, 0.0)


def _assert_both(s, tid, status, stock, ins=None):
    for who in ('a', 'b'):
        assert _status(s, who, tid) == status, (who, _status(s, who, tid))
        assert _stock(s, who) == stock, (who, _stock(s, who), stock)
        if ins is not None:
            assert _ins(s, who, tid) == ins, (who, _ins(s, who, tid), ins)


# ── 6. recall on A vs receive on B ──────────────────────────────────────────

def test_recall_on_a_vs_receive_on_b_flagged_and_computers_agree(tmp_path):
    s = _provision(tmp_path)
    tid = _create(s, 'a')
    _send(s, 'a', tid)
    s.settle()
    assert s.http('a', "POST", f"/stock-transfers/{tid}/recall")["status_code"] == 200
    assert _receive(s, 'b', tid, SEND_QTY)["status_code"] == 200
    # A's recall reaches B FIRST. B already holds the higher state, and must never
    # show a downgrade even for a moment: the later redelivery of B's own receive
    # would otherwise hide a merge that lets an older event overwrite a newer one.
    s.sync('a')
    assert _status(s, 'b', tid) == "received"
    s.settle()

    for who in ('a', 'b'):
        assert _status(s, who, tid) == "received", who          # received outranks recalled
        assert len(_conflicts(s, who, "transfer_received_and_recalled")) == 1, who
    # Both movements happened (recall: X +10 back; receive: Y +10): the stock is in
    # BOTH places, and the two computers must say the same thing about it.
    sa, sb = _stock(s, 'a'), _stock(s, 'b')
    assert sa == sb == {"Warehouse": 20.0, "Storefront": 10.0}, (sa, sb)
    assert _ins(s, 'a', tid) == _ins(s, 'b', tid) == [("Storefront", 10.0), ("Warehouse", 10.0)]
    m = _books(s)
    # Recall reverses the send (1250 -20); the receive clears 1250 again (-20): -20.
    assert m['1250'] == -20.0 and m['1200'] == 60.0, m


# ── 7. cancel on A vs send on B ─────────────────────────────────────────────

def test_cancel_on_a_vs_send_on_b_ends_in_transit_flagged(tmp_path):
    s = _provision(tmp_path)
    tid = _create(s, 'a')
    s.settle()
    assert s.http('a', "POST", f"/stock-transfers/{tid}/cancel")["status_code"] == 200
    _send(s, 'b', tid)
    # A's cancel reaches B first; B has sent (higher rank) and must stay in transit
    # at every moment, not only after the redeliveries of the full settle.
    s.sync('a')
    assert _status(s, 'b', tid) == "in_transit"
    s.settle()

    for who in ('a', 'b'):
        assert _status(s, who, tid) == "in_transit", who        # the goods left: send wins
        assert len(_conflicts(s, who, "transfer_sent_after_cancel")) == 1, who
    assert _stock(s, 'a') == _stock(s, 'b') == {"Warehouse": 10.0}, (_stock(s, 'a'), _stock(s, 'b'))
    m = _books(s)
    assert m['1250'] == 20.0 and m['1200'] == 20.0, m


# ── W3b verify round 2: the PRINCIPLE for every clash ───────────────────────
# After any clash BOTH computers end with identical stock, books and line facts, and
# a recorded conflict. "Consistent but needs a person" is acceptable; "different on
# each computer" is not. These three came from the adversarial verify of c2308cee.

def _lines(s, who, tid):
    return [(r["quantity_sent"], r["quantity_received"], r["unit_cost"]) for r in s.sql(
        who, "SELECT quantity_sent, quantity_received, unit_cost FROM stock_transfer_items "
             "WHERE transfer_id=? ORDER BY id", (tid,))]


def test_double_send_at_different_costs_converges_and_transit_clears(tmp_path):
    """F1. A raises its own average cost offline (a purchase receipt of 10 @ 8.000 not yet
    synced) and sends; B sends the same transfer at 2.000. Before the fix the send
    journal uid was the plain transfer id: each computer kept only ITS figure, the
    books differed on every computer for ever, and after B received, 1250 was 0 on B and
    still 12 on A. Now the line cost converges to the SMALLER (2.000), both journals reach
    both computers, the dearer one is reversed (the same reversal uid on both), and after
    the receive 1250 clears to 0 on BOTH with identical books."""
    s = _provision(tmp_path)
    tid = _create(s, 'a')
    s.settle()
    sup = s.http('a', "POST", "/suppliers", {"name": "F1 Sup"})
    po = s.http('a', "POST", "/purchase-orders", {
        "supplier_id": sup["body"]["data"]["id"],
        "items": [{"product_id": s.pid, "quantity": 10, "unit_cost": 8.0}]})
    assert s.http('a', "POST", f"/purchase-orders/{po['body']['data']['id']}/receive")["status_code"] == 200
    _send(s, 'a', tid)
    _send(s, 'b', tid)                       # no sync in between: the two sends clash
    s.settle()

    assert _lines(s, 'a', tid) == _lines(s, 'b', tid) == [(10.0, None, COST)], (
        _lines(s, 'a', tid), _lines(s, 'b', tid))          # the smaller cost, on BOTH
    for who in ('a', 'b'):
        assert len(_conflicts(s, who, "transfer_sent_twice")) == 1, who
        assert len(s.sql(who, "SELECT 1 FROM gl_journals WHERE source_type='reversal'")) == 1, (
            who, 'the dearer send is reversed exactly once -- the same reversal on both')
    m = _books(s)                            # identical accounts AND identical journal uids
    assert m['1250'] == 20.0, m              # the send value ONCE, at the lower cost

    r = _receive(s, 'b', tid, SEND_QTY)
    assert r["status_code"] == 200, r
    s.settle()
    m = _books(s)
    assert _acct0(m, '1250') == 0.0, m       # cleared on BOTH (it stayed 12 on A before)
    _assert_both(s, tid, "received", {"Warehouse": 20.0 - SEND_QTY + 10.0, "Storefront": 10.0})
    assert _lines(s, 'a', tid) == _lines(s, 'b', tid)


def test_two_line_receipt_where_one_line_matches_keeps_the_inventory_tie(tmp_path):
    """F2. Two lines (3 and 2). A receives (3, 2); B receives (3, 1) -- offline. Line 1's
    stock movement collapses (same quantity), and now so does its journal: before the
    fix each receipt was ONE journal, both posted, and line 1's value was booked twice so
    1200 sat above the stock value. Line 2 differs, so both count (flagged). Both
    computers end identical, with 1200 equal to the value on the shelves."""
    s = _provision(tmp_path)
    r = s.http('a', "POST", "/stock-transfers", {
        "source_branch_id": _branch_id(s, 'a', "Warehouse"),
        "destination_branch_id": _branch_id(s, 'a', "Storefront"),
        "items": [{"product_id": s.pid, "quantity": 3}, {"product_id": s.pid, "quantity": 2}]})
    assert r["status_code"] == 200, r
    tid = r["body"]["data"]["id"]
    _send(s, 'a', tid)
    s.settle()

    def receipt(who, qtys):
        ids = [x["id"] for x in s.sql(who, "SELECT id FROM stock_transfer_items WHERE transfer_id=? "
                                           "ORDER BY id", (tid,))]
        # lines are ordered by id; give the 3-line the 3 whichever id sorts first
        sent = {x["id"]: x["quantity_sent"] for x in s.sql(
            who, "SELECT id, quantity_sent FROM stock_transfer_items WHERE transfer_id=?", (tid,))}
        by_sent = sorted(ids, key=lambda i: -sent[i])      # the 3-line first, then the 2-line
        return s.http(who, "POST", f"/stock-transfers/{tid}/receive",
                      {"items": [{"id": i, "quantity_received": q} for i, q in zip(by_sent, qtys)]})
    assert receipt('a', [3, 2])["status_code"] == 200
    assert receipt('b', [3, 1])["status_code"] == 200
    s.settle()

    assert _lines(s, 'a', tid) == _lines(s, 'b', tid), (_lines(s, 'a', tid), _lines(s, 'b', tid))
    for who in ('a', 'b'):
        assert len(_conflicts(s, who, "transfer_received_twice")) == 1, who
    m = _books(s)
    for who in ('a', 'b'):
        tie = _tb(s, who)['inventory']
        assert tie['status'] in ('ties', 'rounding'), (who, tie)
        assert abs(tie['difference']) < 0.01, (who, tie)
    assert _stock(s, 'a') == _stock(s, 'b')


def test_cancel_on_a_vs_send_and_receive_on_b_is_flagged_on_both(tmp_path):
    """F3. A cancels a pending transfer; B sends AND receives it, offline. The goods
    left, so 'received' wins -- and the cancel-versus-send clash is recorded on BOTH
    computers (it was recorded on only one: the rule matched {cancelled, in transit}
    and no other pair)."""
    s = _provision(tmp_path)
    tid = _create(s, 'a')
    s.settle()
    assert s.http('a', "POST", f"/stock-transfers/{tid}/cancel")["status_code"] == 200
    _send(s, 'b', tid)
    assert _receive(s, 'b', tid, SEND_QTY)["status_code"] == 200
    s.settle()

    for who in ('a', 'b'):
        assert _status(s, who, tid) == "received", who
        assert len(_conflicts(s, who, "transfer_sent_after_cancel")) == 1, (
            who, _conflicts(s, who, "transfer_sent_after_cancel"))
    m = _books(s)
    assert _acct0(m, '1250') == 0.0, m
    assert _stock(s, 'a') == _stock(s, 'b') == {"Warehouse": 10.0, "Storefront": 10.0}
