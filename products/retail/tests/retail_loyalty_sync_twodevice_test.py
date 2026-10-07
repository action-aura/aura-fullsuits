"""Aura Retail -- W2a: loyalty points are ONE balance across TWO computers -- through
the REAL routes, the REAL FileRelay and a REAL second OS process (harness shared with
retail_transfer_sync_twodevice_test.py / retail_po_receipts_twodevice_test.py).

THE CLAIM. Before W2a (schema v48) a `loyalty_ledger` row never left the computer that
wrote it: points earned on A did not exist on B, a return on B of A's sale found no
earn/redeem rows (no claw-back, no give-back, the full cash refunded), and each
computer's own 'opening' row for one customer would double the balance the moment rows
travelled. These five tests pin the cure at the only level that can see it -- two
independent databases:

  1. points earned on A are redeemable on B; both hold the same rows (same uids, the
     same sale uid on each row) and the same balance;
  2. a return on B of A's sale (which spent points) claws back / gives back on BOTH,
     refunds the same cash a single computer would, and the books agree;
  3. a return on B is REFUSED (409 LOYALTY_NOT_ARRIVED, nothing written) while the
     sale's points have not arrived, and works once they have;
  4. offline double-spend: both computers spend the same points -> the same negative
     balance on both, and a plain-sentence flag on both (prevention is out of scope);
  5. both computers' pre-v48 'opening' rows for one customer (50 vs 30) collapse to ONE
     row of 30 on both, flagged on both.

Hand-computed figures (point value 1.000 per point; earn = int(total / 10)):
  Big  = 100.000 (earns 10)     Half = 50.000 each     Cheap = 9.000 (earns int(0.9) = 0)

Run:
    pytest products/retail/tests/retail_loyalty_sync_twodevice_test.py -v
"""
from __future__ import annotations

import sqlite3
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from retail_gl_two_device_sync_test import _device_retail_db, _write_device  # noqa: E402
from retail_po_sync_twodevice_test import Shop, _tb  # noqa: E402
from _stock_sync_harness import run_device  # noqa: E402

PY = sys.executable


# ── harness helpers ─────────────────────────────────────────────────────────

def _provision(tmp_path):
    """A: company, one branch, three products, one customer; point value 1 on BOTH
    computers (a setting does not travel). B: bootstrap, then pulls everything."""
    s = Shop(tmp_path)
    [s.boot['a']] = run_device(PY, s.app['a'], s.relay, [{"op": "bootstrap"}])
    [s.boot['b']] = run_device(PY, s.app['b'], s.relay, [{"op": "bootstrap"}])
    br, big, half, cheap, cust = s.run(
        'a',
        {"op": "create_branch", "name": "Main", "address": "1 Rd", "phone": "555-0100"},
        {"op": "create_product", "name": "Loy Big", "price": 100.0, "initial_stock": 100},
        {"op": "create_product", "name": "Loy Half", "price": 50.0, "initial_stock": 100},
        {"op": "create_product", "name": "Loy Cheap", "price": 9.0, "initial_stock": 100},
        {"op": "http", "method": "POST", "path": "/customers", "json": {"name": "Loyal Lena"}},
    )
    for r in (br, big, half, cheap, cust):
        assert r["status_code"] == 200, r
    s.big, s.half, s.cheap = big["product_id"], half["product_id"], cheap["product_id"]
    s.cust = cust["body"]["data"]["id"]
    s.sync('a')
    s.pull('b')
    for who in ('a', 'b'):
        r = s.http(who, "POST", "/settings/credit", {"loyalty_point_value": 1})
        assert r["status_code"] == 200, (who, r)
        assert len(s.sql(who, "SELECT 1 FROM customers WHERE id=?", (s.cust,))) == 1, who
        assert len(s.sql(who, "SELECT 1 FROM branches")) == 1, who
    return s


def _sale(s, who, pid, qty, redeem=0):
    body = {"items": [{"product_id": pid, "quantity": qty}], "payment_method": "cash",
            "customer_id": s.cust, "idempotency_key": str(uuid.uuid4())}
    if redeem:
        body["points_redeemed"] = redeem
    r = s.http(who, "POST", "/sales", body)
    assert r["status_code"] == 200, r
    return r["body"]["data"]["id"]


def _sale_uid(s, who, sale_id):
    return s.sql(who, "SELECT uid FROM sales WHERE id=?", (sale_id,))[0]["uid"]


def _local_sale_id(s, who, uid):
    rows = s.sql(who, "SELECT id FROM sales WHERE uid=?", (uid,))
    assert len(rows) == 1, (who, uid, rows)
    return rows[0]["id"]


def _return(s, who, sale_id, pid, qty):
    return s.http(who, "POST", "/returns", {"sale_id": sale_id, "items": [{"product_id": pid, "quantity": qty}]})


def _balance_route(s, who):
    r = s.http(who, "GET", f"/customers/{s.cust}/loyalty")
    assert r["status_code"] == 200, r
    return r["body"]["data"]["balance"]


def _rows(s, who):
    """Every ledger row for the customer as (uid, type, delta, sale uid, return uid)."""
    rows = s.sql(who, "SELECT l.uid AS uid, l.entry_type AS t, l.points_delta AS d, s.uid AS su, r.uid AS ru "
                      "FROM loyalty_ledger l LEFT JOIN sales s ON s.id=l.sale_id "
                      "LEFT JOIN returns r ON r.id=l.return_id WHERE l.customer_id=? ORDER BY l.uid", (s.cust,))
    return [(r["uid"], r["t"], r["d"], r["su"], r["ru"]) for r in rows]


def _types(rows):
    return sorted((t, d) for _u, t, d, _su, _ru in rows)


def _cache(s, who):
    return s.sql(who, "SELECT loyalty_points AS p FROM customers WHERE id=?", (s.cust,))[0]["p"]


def _conflicts(s, who, reason):
    return [c for c in s.sql(who, "SELECT * FROM sync_conflicts WHERE entity_type='loyalty_entry'")
            if f'"reason": "{reason}"' in c["incoming_payload"]]


def _gl5000(s, who):
    return {a['code']: a['balance'] for a in _tb(s, who)['accounts']}.get('5000', 0.0)


def _assert_converged(s, balance, types):
    """Both computers hold the IDENTICAL ledger (uids and document links included),
    the balance route, the SUM and the cache all say `balance`, and the rows are
    exactly `types` (a sorted list of (entry_type, delta))."""
    rows = {who: _rows(s, who) for who in ('a', 'b')}
    assert rows['a'] == rows['b'], rows
    assert _types(rows['a']) == sorted(types), (_types(rows['a']), sorted(types))
    for who in ('a', 'b'):
        total = s.sql(who, "SELECT COALESCE(SUM(points_delta),0) AS t FROM loyalty_ledger WHERE customer_id=?",
                      (s.cust,))[0]["t"]
        assert (_balance_route(s, who), total, _cache(s, who)) == (balance, balance, balance), (
            who, _balance_route(s, who), total, _cache(s, who), balance)
    return rows['a']


# ── 1. earned on A, spent on B ──────────────────────────────────────────────

def test_points_earned_on_a_redeemable_on_b(tmp_path):
    s = _provision(tmp_path)
    sale_a = _sale(s, 'a', s.big, 1)                       # total 100 -> earns 10
    assert _balance_route(s, 'a') == 10
    s.settle()
    assert _balance_route(s, 'b') == 10                    # B knows A's points
    sale_b = _sale(s, 'b', s.cheap, 1, redeem=5)           # total 9, spends 5, earns 0
    assert s.sql('b', "SELECT points_redeemed_amount AS p FROM sales WHERE id=?", (sale_b,))[0]["p"] == 5
    s.settle()

    rows = _assert_converged(s, 5, [("earn", 10), ("redeem", -5)])
    uid_a, uid_b = _sale_uid(s, 'a', sale_a), _sale_uid(s, 'b', sale_b)
    by_type = {t: su for _u, t, _d, su, _ru in rows}
    # Each row points at the SAME sale on both computers (by uid, never by local id).
    assert by_type == {"earn": uid_a, "redeem": uid_b}, by_type
    for who in ('a', 'b'):
        assert _conflicts(s, who, "loyalty_overspent") == [], who
        assert _conflicts(s, who, "loyalty_opening_differs") == [], who
    # Books: only B's redemption (5) is an expense; identical on both computers.
    assert _gl5000(s, 'a') == _gl5000(s, 'b') == 5.0


# ── 2. return on B of A's sale that spent points ────────────────────────────

def test_return_on_b_of_a_sale_reverses_points_and_refunds_same_cash(tmp_path):
    s = _provision(tmp_path)
    _sale(s, 'a', s.big, 2)                                # total 200 -> earns 20
    sale1 = _sale(s, 'a', s.half, 2, redeem=10)            # total 100, spends 10, earns 10, cash 90
    uid1 = _sale_uid(s, 'a', sale1)
    assert _balance_route(s, 'a') == 20
    s.settle()

    rb = _return(s, 'b', _local_sale_id(s, 'b', uid1), s.half, 1)   # refund 50 = half the sale
    assert rb["status_code"] == 200, rb
    s.settle()

    # proportion 0.5: earn 10 -> claw back 5; redeemed 10 -> give back 5.
    rows = _assert_converged(s, 20, [("earn", 20), ("earn", 10), ("redeem", -10),
                                     ("adjust", -5), ("adjust", 5)])
    ret_uids = {ru for _u, t, _d, _su, ru in rows if t == "adjust"}
    assert len(ret_uids) == 1 and None not in ret_uids, rows    # both adjusts name B's one return
    # Cash: refund 50, of which 5 comes back as points -> 45 in cash, on both computers.
    for who in ('a', 'b'):
        ret = s.sql(who, "SELECT tender_refund_amount AS t, refund_amount AS r FROM returns")
        assert [(r["t"], r["r"]) for r in ret] == [(45.0, 50.0)], (who, ret)
    # Books: expense 10 on the sale, 5 credited back by the return -> 5, identical.
    assert _gl5000(s, 'a') == _gl5000(s, 'b') == 5.0
    for who in ('a', 'b'):
        assert _conflicts(s, who, "loyalty_overspent") == [], who

    # Control: the SAME flow on ONE computer refunds the same cash.
    sale2 = _sale(s, 'a', s.half, 2, redeem=10)
    rc = _return(s, 'a', sale2, s.half, 1)
    assert rc["status_code"] == 200, rc
    ctrl = s.sql('a', "SELECT tender_refund_amount AS t FROM returns WHERE sale_id=?", (sale2,))
    assert [c["t"] for c in ctrl] == [45.0], ctrl


# ── 3. return refused until the sale's points have arrived ──────────────────

def _restore(s, who, rows):
    conn = sqlite3.connect(str(_device_retail_db(s.app[who])), timeout=30)
    try:
        for r in rows:
            cols = list(r)
            conn.execute(f"INSERT INTO sync_outbox ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                         [r[c] for c in cols])
        conn.commit()
    finally:
        conn.close()


def test_return_on_b_refused_until_the_sales_points_arrived(tmp_path):
    s = _provision(tmp_path)
    sale = _sale(s, 'a', s.big, 1)                         # earns 10
    uid = _sale_uid(s, 'a', sale)
    held = s.sql('a', "SELECT * FROM sync_outbox WHERE entity_type='loyalty_entry'")
    assert len(held) == 1, held                            # precondition: the earn was queued
    s.run('a', {"op": "outbox_delete_entity_type", "entity_type": "loyalty_entry"}, login=False)
    s.sync('a')                                            # the sale arrives; its points do not
    sale_on_b = _local_sale_id(s, 'b', uid)
    assert s.sql('b', "SELECT COUNT(*) AS n FROM loyalty_ledger")[0]["n"] == 0

    r = _return(s, 'b', sale_on_b, s.big, 1)
    assert r["status_code"] == 409 and r["body"]["code"] == "LOYALTY_NOT_ARRIVED", r
    # Nothing was written by the refusal.
    assert s.sql('b', "SELECT COUNT(*) AS n FROM returns")[0]["n"] == 0
    assert s.sql('b', "SELECT COUNT(*) AS n FROM loyalty_ledger")[0]["n"] == 0

    _restore(s, 'a', held)                                 # the points arrive
    s.settle()
    r = _return(s, 'b', sale_on_b, s.big, 1)
    assert r["status_code"] == 200, r
    s.settle()
    _assert_converged(s, 0, [("earn", 10), ("adjust", -10)])   # the full claw-back, on both


# ── 4. offline double-spend ─────────────────────────────────────────────────

def test_offline_double_spend_is_negative_and_flagged_on_both(tmp_path):
    s = _provision(tmp_path)
    _sale(s, 'a', s.big, 1)                                # earns 10
    s.settle()
    _sale(s, 'a', s.cheap, 1, redeem=9)                    # each computer spends 9 of the 10, offline
    _sale(s, 'b', s.cheap, 1, redeem=9)
    assert _balance_route(s, 'a') == 1 and _balance_route(s, 'b') == 1
    s.settle()

    _assert_converged(s, -8, [("earn", 10), ("redeem", -9), ("redeem", -9)])
    for who in ('a', 'b'):
        assert len(_conflicts(s, who, "loyalty_overspent")) == 1, (who, _conflicts(s, who, "loyalty_overspent"))
    # Both redemptions really happened: 9 + 9 on the books.
    assert _gl5000(s, 'a') == _gl5000(s, 'b') == 18.0


# ── 5. both computers' pre-v48 opening rows collapse to the smaller ─────────

def _legacy_opening(s, who, points):
    """A pre-v48 'opening' row exactly as the v48 migration leaves one: a random uid
    and `sync_backfill_pending=1`. The NEXT launch's boot backfill (real code) re-keys
    it to the deterministic opening uid and queues it."""
    cid = s.sql(who, "SELECT company_id FROM customers WHERE id=?", (s.cust,))[0]["company_id"]
    _write_device(s.app[who],
                  "INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type,created_by,"
                  "sync_backfill_pending) VALUES (?,?,?,?,'opening','legacy',1)",
                  (str(uuid.uuid4()), cid, s.cust, points))


def test_both_computers_openings_collapse_to_the_smaller(tmp_path):
    s = _provision(tmp_path)
    _legacy_opening(s, 'a', 50)
    _legacy_opening(s, 'b', 30)
    s.settle()                                             # each push is a boot: backfill, then travel

    rows = _assert_converged(s, 30, [("opening", 30)])
    assert len(rows) == 1
    for who in ('a', 'b'):
        assert len(_conflicts(s, who, "loyalty_opening_differs")) == 1, (who, _conflicts(s, who, "loyalty_opening_differs"))
