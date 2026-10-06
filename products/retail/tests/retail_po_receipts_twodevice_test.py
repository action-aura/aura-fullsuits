"""Aura Retail -- W4a-2: receipts and partial delivery across TWO computers,
through the REAL routes, the REAL FileRelay and a REAL second OS process
(harness shared with retail_po_sync_twodevice_test.py).

THE CLAIM. A receipt is an immutable synced fact; stock, the order's state, the
books (AP) and the supplier balance all DERIVE from receipts. These six tests
pin that at the only level that can see it -- two independent databases:

  1. 6 of 10 received on A, the remaining 4 on B -> both 'received', stock 10, AP = order total;
  2. the identical COMPLETING receive on both computers while apart collapses to ONE receipt;
  3. two different 6-unit deliveries offline both count (12), flagged po_over_received;
  4. partial on A, 'close short' on B -> closed everywhere, owed = the 6 received;
  5. a down payment before any receipt shows as an advance, then nets against the receipt;
  6. a retried partial request (same receipt_key) after sync records nothing new.

Hand-computed: one product ordered 10 @ 10.000 = 100.000, no tax, so 6 units
= 60.000, 4 units = 40.000. Receipt basis: no debt until goods arrive
(Dr 1200 Inventory / Cr 2000 AP per receipt).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from retail_po_sync_twodevice_test import (  # noqa: E402
    QTY, PO_TOTAL, _acct, _create_po, _journals, _po_by_uid, _provision, _stock, _tb,
    _uid_of_local_po,
)

K1 = "key-partial-aaaa1111"
K2 = "key-partial-bbbb2222"


def _setup(tmp_path, down_payment=None):
    s = _provision(tmp_path)
    po_a = _create_po(s, 'a', down_payment=down_payment)
    uid = _uid_of_local_po(s, 'a', po_a)
    s.sync('a')
    po_b = _po_by_uid(s, 'b', uid)["id"]
    return s, uid, {'a': po_a, 'b': po_b}


def _item_id(s, who, po_id):
    rows = s.sql(who, "SELECT id FROM purchase_order_items WHERE po_id=?", (po_id,))
    assert len(rows) == 1, rows
    return rows[0]["id"]


def _receive(s, who, po_id, qty=None, key=None):
    body = None
    if qty is not None:
        body = {"receipt_key": key, "lines": [{"item_id": _item_id(s, who, po_id), "quantity": qty}]}
    return s.http(who, "POST", f"/purchase-orders/{po_id}/receive", body)


def _supplier_balance(s, who):
    return s.sql(who, "SELECT credit_balance FROM suppliers WHERE name='W4a Sup'")[0]["credit_balance"]


def _receipts(s, who):
    return s.sql(who, "SELECT * FROM purchase_order_receipts ORDER BY id")


def _conflicts(s, who):
    return [c for c in s.sql(who, "SELECT * FROM sync_conflicts WHERE entity_type='purchase_order'")
            if "po_over_received" in c["incoming_payload"]]


def _assert_ap(s, who, expected):
    tb = _tb(s, who)
    assert _acct(tb, '2000') == expected, (who, _acct(tb, '2000'), expected)
    assert tb["subledger"]["ap_ties"] is True, (who, tb["subledger"])
    return tb


# ── 1. partial on A, the rest on B ──────────────────────────────────────────

def test_partial_on_a_then_rest_on_b(tmp_path):
    s, uid, po = _setup(tmp_path)
    r = _receive(s, 'a', po['a'], 6, K1)
    assert r["status_code"] == 200, r
    s.settle()
    for who in ('a', 'b'):
        assert _po_by_uid(s, who, uid)["status"] == "partial", who
        assert _stock(s, who) == 6, who
        assert _supplier_balance(s, who) == 60.0, who
        _assert_ap(s, who, 60.0)

    r = _receive(s, 'b', po['b'])          # empty body = the remaining 4
    assert r["status_code"] == 200, r
    s.settle()

    for who in ('a', 'b'):
        assert _po_by_uid(s, who, uid)["status"] == "received", who
        assert _stock(s, who) == QTY, (who, _stock(s, who))     # 6 + 4, not 20
        assert len(_receipts(s, who)) == 2, (who, _receipts(s, who))
        assert len(_journals(s, who, 'po_receipt')) == 2, who   # exactly one per receipt
        assert _supplier_balance(s, who) == PO_TOTAL, who
        tb = _assert_ap(s, who, PO_TOTAL)
        assert _acct(tb, '1200') == PO_TOTAL, who
        assert _conflicts(s, who) == [], who


# ── 2. identical completing receive offline collapses ───────────────────────

def test_identical_completing_receive_offline_collapses(tmp_path):
    s, uid, po = _setup(tmp_path)
    ra = _receive(s, 'a', po['a'])
    rb = _receive(s, 'b', po['b'])
    assert ra["status_code"] == 200 and rb["status_code"] == 200, (ra, rb)
    s.settle()
    for who in ('a', 'b'):
        assert _stock(s, who) == QTY, f"{who}: {_stock(s, who)} (double count would be {2 * QTY})"
        assert len(_receipts(s, who)) == 1, (who, _receipts(s, who))
        assert _po_by_uid(s, who, uid)["status"] == "received", who
        assert _conflicts(s, who) == [], who
        _assert_ap(s, who, PO_TOTAL)
    assert _receipts(s, 'a')[0]["uid"] == _receipts(s, 'b')[0]["uid"]


# ── 3. different partial deliveries offline both count ──────────────────────

def test_different_partial_deliveries_offline_both_count(tmp_path):
    s, uid, po = _setup(tmp_path)
    assert _receive(s, 'a', po['a'], 6, K1)["status_code"] == 200
    assert _receive(s, 'b', po['b'], 6, K2)["status_code"] == 200
    s.settle()
    for who in ('a', 'b'):
        assert len(_receipts(s, who)) == 2, (who, _receipts(s, who))
        assert _stock(s, who) == 12, (who, _stock(s, who))      # never silently lost
        assert s.sql(who, "SELECT received_qty FROM purchase_order_items")[0]["received_qty"] == 12, who
        assert _po_by_uid(s, who, uid)["status"] == "received", who
        assert len(_conflicts(s, who)) == 1, (who, _conflicts(s, who))


# ── 4. close short on B ─────────────────────────────────────────────────────

def test_close_short_on_b_reaches_a(tmp_path):
    s, uid, po = _setup(tmp_path)
    assert _receive(s, 'a', po['a'], 6, K1)["status_code"] == 200
    s.settle()
    r = s.http('b', "POST", f"/purchase-orders/{po['b']}/close")
    assert r["status_code"] == 200, r
    s.settle()
    for who in ('a', 'b'):
        assert _po_by_uid(s, who, uid)["status"] == "closed", who
        assert _supplier_balance(s, who) == 60.0, who            # the 6 received, nothing for the 4
        _assert_ap(s, who, 60.0)
        assert _stock(s, who) == 6, who
    r = _receive(s, 'a', po['a'])
    assert r["status_code"] == 409, r
    assert _stock(s, 'a') == 6


# ── 5. a down payment before any receipt ────────────────────────────────────

def test_down_payment_then_receipt_on_b(tmp_path):
    s, uid, po = _setup(tmp_path, down_payment=30.0)
    # Before delivery nothing is owed: the 30 is an advance, on both computers.
    for who in ('a', 'b'):
        assert _supplier_balance(s, who) == -30.0, who
        _assert_ap(s, who, -30.0)
    assert _receive(s, 'b', po['b'])["status_code"] == 200
    s.settle()
    for who in ('a', 'b'):
        assert _supplier_balance(s, who) == 70.0, who            # 100 - 30
        _assert_ap(s, who, 70.0)
        assert _po_by_uid(s, who, uid)["status"] == "received", who


# ── 6. a retried request after sync records nothing ─────────────────────────

def test_retry_with_same_receipt_key_after_sync_records_nothing(tmp_path):
    s, uid, po = _setup(tmp_path)
    assert _receive(s, 'a', po['a'], 6, K1)["status_code"] == 200
    s.settle()
    r = _receive(s, 'a', po['a'], 6, K1)
    assert r["status_code"] == 200 and r["body"].get("already_recorded") is True, r
    s.settle()
    for who in ('a', 'b'):
        assert _stock(s, who) == 6, (who, _stock(s, who))
        assert len(_receipts(s, who)) == 1, who
        assert len(_journals(s, who, 'po_receipt')) == 1, who
        _assert_ap(s, who, 60.0)
