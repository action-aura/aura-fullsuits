"""Aura Retail -- STOCK-BOOKS T8: the stock entries one computer posts show up,
once and identically, on its sibling -- through the REAL routes, the REAL
FileRelay and a REAL second OS process (harness: retail_gl_two_device_sync_test.py).

THE CLAIM. Computer A enters opening stock (10 @ 2.000), adjusts -3, and later
sends 4 to a second branch and receives 3. Every one of those journals is keyed
on something both computers can name (the movement's uid, the transfer's UUID),
so after one pull B holds EXACTLY A's books: Inventory 14, Opening Balance
Equity 20, Shrinkage 6 -- and B posted NOTHING of its own (a sync apply of a
stock movement must never post: if it did, B would count the same stock twice).
Re-pulling changes nothing.

Why a bootstrap shop and an operator branch: `create_shop` self-heals a default
branch that deliberately queues no branch sync event, so a peer could not place
the journals' branch. A `bootstrap` company has no branch of its own; its FIRST
branch is the operator-created one, which is where create_product files stock.

Run:
    pytest products/retail/tests/retail_stock_books_twodevice_test.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _stock_sync_harness import run_device  # noqa: E402
from retail_gl_two_device_sync_test import (  # noqa: E402
    _assert_ledgers_converge, _gl_census, _read_device, _trial_balance,
)

PY = sys.executable


def _acct(tb, code):
    return [a for a in tb['accounts'] if a['code'] == code][0]['balance']


def _provision_a(tmp_path, relay_db):
    """A: bootstrap company, an operator branch, a product 10 @ 2.000 filed
    there, then -3 adjusted. Returns everything the tests need."""
    app_a = tmp_path / 'device_a'
    [boot] = run_device(PY, app_a, relay_db, [{"op": "bootstrap"}])
    login = {"op": "login", "email": boot["email"], "password": boot["password"]}
    _, branch, product = run_device(PY, app_a, relay_db, [
        login,
        {"op": "create_branch", "name": "Warehouse", "address": "1 Rd", "phone": "555-0100"},
        {"op": "create_product", "name": "SB Sync Widget", "price": 4.0, "initial_stock": 10},
    ])
    assert branch["status_code"] == 200 and product["status_code"] == 200, (branch, product)
    pid = product["product_id"]
    _, adj = run_device(PY, app_a, relay_db, [
        login, {"op": "adjust_stock", "product_id": pid, "quantity": -3, "reason": "damaged"}])
    assert adj["status_code"] == 200, adj
    return app_a, boot, pid, branch


def _b_pulls(tmp_path, relay_db, times=1):
    app_b = tmp_path / 'device_b'
    [boot_b] = run_device(PY, app_b, relay_db, [{"op": "bootstrap"}])
    for _ in range(times):
        run_device(PY, app_b, relay_db, [{"op": "pull"}])
    return app_b, boot_b


def test_t8_opening_and_adjustment_journals_reach_the_sibling_exactly_once(tmp_path):
    relay_db = tmp_path / 'relay.db'
    app_a, boot_a, pid, _branch = _provision_a(tmp_path, relay_db)
    run_device(PY, app_a, relay_db, [{"op": "push"}])
    app_b, boot_b = _b_pulls(tmp_path, relay_db)

    tb_a = _trial_balance(app_a, relay_db, boot_a["email"], boot_a["password"])
    tb_b = _trial_balance(app_b, relay_db, boot_b["email"], boot_b["password"])
    # Hand-computed: opening 10 x 2.000 = 20 (Dr 1200 / Cr 3900); adjust -3 x 2.000 = 6
    # (Dr 5910 / Cr 1200) -> Inventory 14 = 7 units x 2.000.
    for tb in (tb_a, tb_b):
        assert _acct(tb, '1200') == 14.0
        assert _acct(tb, '3900') == 20.0
        assert _acct(tb, '5910') == 6.0

    census_a = _gl_census(app_a, relay_db, boot_a["company_id"])
    census_b = _gl_census(app_b, relay_db, boot_b["company_id"])
    # Same journals, same uids; B holds no journal A does not (it posted nothing).
    assert census_b['count'] == census_a['count'] == 2, (census_a, census_b)
    assert census_a['uid_hash'] == census_b['uid_hash']

    # Re-pulling (twice, as a second computer does every tick) changes nothing.
    run_device(PY, app_b, relay_db, [{"op": "pull"}, {"op": "pull"}])
    assert _gl_census(app_b, relay_db, boot_b["company_id"]) == census_b
    tb_b2 = _trial_balance(app_b, relay_db, boot_b["email"], boot_b["password"])
    assert {k: v for k, v in tb_b2.items() if k != 'subledger'} == \
           {k: v for k, v in tb_b.items() if k != 'subledger'}


def test_t8_transfer_journals_match_on_the_sibling_and_1250_clears_once(tmp_path):
    relay_db = tmp_path / 'relay.db'
    app_a, boot_a, pid, branch = _provision_a(tmp_path, relay_db)
    src = branch["branch_id"]
    login = {"op": "login", "email": boot_a["email"], "password": boot_a["password"]}
    _, dest_resp = run_device(PY, app_a, relay_db, [
        login, {"op": "create_branch", "name": "Storefront", "address": "2 Rd", "phone": "555-0101"}])
    dest = dest_resp["branch_id"]

    _, created = run_device(PY, app_a, relay_db, [
        login, {"op": "http", "method": "POST", "path": "/stock-transfers", "json": {
            "source_branch_id": src, "destination_branch_id": dest,
            "items": [{"product_id": pid, "quantity": 4}]}}])
    assert created["status_code"] == 200, created
    tid = created["body"]["data"]["id"]
    _, sent, got = run_device(PY, app_a, relay_db, [
        login, {"op": "http", "method": "POST", "path": f"/stock-transfers/{tid}/send"},
        {"op": "http", "path": f"/stock-transfers/{tid}"}])
    assert sent["status_code"] == 200, sent
    item_id = got["body"]["data"]["items"][0]["id"]
    _, recv = run_device(PY, app_a, relay_db, [
        login, {"op": "http", "method": "POST", "path": f"/stock-transfers/{tid}/receive",
                "json": {"items": [{"id": item_id, "quantity_received": 3}]}}])
    assert recv["status_code"] == 200 and recv["body"]["data"]["shortages"][0]["written_off_value"] == 2.0, recv
    run_device(PY, app_a, relay_db, [{"op": "push"}])

    app_b, boot_b = _b_pulls(tmp_path, relay_db, times=2)
    tb_a, tb_b, _pa, _pb = _assert_ledgers_converge(
        app_a, app_b, relay_db, boot_a["company_id"], boot_b["company_id"], '2020-01-01', '2099-12-31',
        boot_a["email"], boot_a["password"], boot_b["email"], boot_b["password"])
    # Hand-computed. Opening 20, adjust -6, send 4 (Dr 1250 8 / Cr 1200 8), receive 3
    # (Dr 1200 6, Dr 5910 2, Cr 1250 8): 1200 = 20-6-8+6 = 12; 1250 cleared; 5910 = 6+2 = 8.
    for tb in (tb_a, tb_b):
        assert _acct(tb, '1200') == 12.0
        assert _acct(tb, '1250') == 0.0
        assert _acct(tb, '5910') == 8.0
    # The 1250 lines exist exactly once on each computer (send + receive, not doubled).
    for app, cid in ((app_a, boot_a["company_id"]), (app_b, boot_b["company_id"])):
        rows = _read_device(app, "SELECT COUNT(*) AS n FROM gl_journals WHERE company_id=? "
                                 "AND source_type='stock_transfer'", (cid,))
        assert rows[0]["n"] == 2, rows


# ── STOCK-BOOKS verify F1: the sibling values the same stock the same way ───────────────────
# The verifier found a peer ended with a different average cost than the computer that
# made the movements: A (10 @ 2.000, adjust -3, receive PO 7 @ 4.000) averages 3.000, but the
# sync apply blended only purchase_in/transfer_in, so B never blended the opening stock and
# valued 14 units at 4.000 = 56 against books of 42. One shared rule now
# (commercial_runtime/stock_cost.py) governs both.

def _po_receipt_on_a(app_a, relay_db, boot_a, pid):
    login = {"op": "login", "email": boot_a["email"], "password": boot_a["password"]}
    _, sup = run_device(PY, app_a, relay_db, [
        login, {"op": "http", "method": "POST", "path": "/suppliers", "json": {"name": "F1 Sup"}}])
    sid = sup["body"]["data"]["id"]
    _, po = run_device(PY, app_a, relay_db, [
        login, {"op": "http", "method": "POST", "path": "/purchase-orders", "json": {
            "supplier_id": sid, "items": [{"product_id": pid, "quantity": 7, "unit_cost": 4.0}]}}])
    assert po["status_code"] == 200, po
    _, rcv = run_device(PY, app_a, relay_db, [
        login, {"op": "http", "method": "POST",
                "path": f"/purchase-orders/{po['body']['data']['id']}/receive"}])
    assert rcv["status_code"] == 200, rcv


def _assert_f1_converged(app_a, boot_a, app_b, boot_b, relay_db, pid):
    for app, boot in ((app_a, boot_a), (app_b, boot_b)):
        bal = _read_device(app, "SELECT quantity_on_hand, avg_cost FROM inventory_balances "
                                "WHERE product_id=?", (pid,))
        # Hand-computed: 10 @ 2.000, -3 -> 7 @ 2.000; +7 @ 4.000 -> 14 units, (14+28)/14 = 3.000.
        assert [(r["quantity_on_hand"], r["avg_cost"]) for r in bal] == [(14.0, 3.0)], (app, bal)
        inv = _trial_balance(app, relay_db, boot["email"], boot["password"])["inventory"]
        # Books: opening 20 - adjust 6 + receipt 28 = 42 = 14 x 3.000.
        assert inv["stock_value"] == 42.0 and inv["inventory_gl"] == 42.0, (app, inv)
        assert inv["status"] == "ties", (app, inv)


def test_f1_peer_average_matches_after_one_pull(tmp_path):
    """Out-of-order shape: B learns product, opening, adjustment and receipt in ONE pull."""
    relay_db = tmp_path / 'relay.db'
    app_a, boot_a, pid, _branch = _provision_a(tmp_path, relay_db)
    _po_receipt_on_a(app_a, relay_db, boot_a, pid)
    run_device(PY, app_a, relay_db, [{"op": "push"}])
    app_b, boot_b = _b_pulls(tmp_path, relay_db, times=1)
    _assert_f1_converged(app_a, boot_a, app_b, boot_b, relay_db, pid)


def test_f1_peer_average_matches_when_receipt_arrives_in_a_later_pull(tmp_path):
    """In-order shape: B holds the opening stock first, the receipt comes later."""
    relay_db = tmp_path / 'relay.db'
    app_a, boot_a, pid, _branch = _provision_a(tmp_path, relay_db)
    run_device(PY, app_a, relay_db, [{"op": "push"}])
    app_b, boot_b = _b_pulls(tmp_path, relay_db, times=2)
    _po_receipt_on_a(app_a, relay_db, boot_a, pid)
    run_device(PY, app_a, relay_db, [{"op": "push"}])
    run_device(PY, app_b, relay_db, [{"op": "pull"}, {"op": "pull"}])
    _assert_f1_converged(app_a, boot_a, app_b, boot_b, relay_db, pid)


# ── STOCK-BOOKS verify F4 x tombstone sync: deleting a product writes off ONCE ──────────────
# Deleting a product on A writes its remaining stock off (stock_out 'DELETE' + journal).
# The tombstone event that reaches B must NOT write off again -- B receives A's movement
# and A's journal by sync, so a second write-off there would count the same 7 units twice.

def test_deleting_a_product_writes_off_once_across_two_computers(tmp_path):
    relay_db = tmp_path / 'relay.db'
    app_a, boot_a, pid, _branch = _provision_a(tmp_path, relay_db)
    login = {"op": "login", "email": boot_a["email"], "password": boot_a["password"]}
    _, deleted = run_device(PY, app_a, relay_db, [
        login, {"op": "http", "method": "DELETE", "path": f"/products/{pid}"}])
    assert deleted["status_code"] == 200, deleted
    run_device(PY, app_a, relay_db, [{"op": "push"}])
    app_b, boot_b = _b_pulls(tmp_path, relay_db, times=2)

    # Hand-computed: opening 10 @ 2.000 = 20 (Dr 1200 / Cr 3900); adjust -3 = 6 to 5910;
    # delete writes off the remaining 7 x 2.000 = 14 (Dr 5910 / Cr 1200).
    # -> Inventory 0, Opening Balance Equity 20, Shrinkage 6 + 14 = 20.
    for app, boot in ((app_a, boot_a), (app_b, boot_b)):
        mv = _read_device(app, "SELECT movement_type, quantity FROM inventory_movements "
                               "WHERE product_id=? AND reference='DELETE'", (pid,))
        assert [(r["movement_type"], r["quantity"]) for r in mv] == [("stock_out", -7.0)], (app, mv)
        bal = _read_device(app, "SELECT quantity_on_hand FROM inventory_balances WHERE product_id=?", (pid,))
        assert [r["quantity_on_hand"] for r in bal] == [0.0], (app, bal)
        tb = _trial_balance(app, relay_db, boot["email"], boot["password"])
        assert _acct(tb, '1200') == 0.0
        assert _acct(tb, '3900') == 20.0
        assert _acct(tb, '5910') == 20.0
    census_a = _gl_census(app_a, relay_db, boot_a["company_id"])
    census_b = _gl_census(app_b, relay_db, boot_b["company_id"])
    assert census_b['count'] == census_a['count'] == 3, (census_a, census_b)
    assert census_a['uid_hash'] == census_b['uid_hash']
