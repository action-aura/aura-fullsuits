"""Aura Retail -- W4a: a purchase order typed on one computer is the SAME order on
its sibling -- through the REAL routes, the REAL FileRelay and a REAL second OS
process (harness: retail_gl_two_device_sync_test.py / retail_stock_books_twodevice_test.py).

THE CLAIM. Before W4a a PO lived only on the computer that typed it:
`purchase_orders.id` is a per-computer integer and nothing was queued, so a
sibling could not see, receive or cancel it; a sibling's instalment carried
`related_id=<the writer's integer>` and landed on an UNRELATED local PO;
`amount_paid` was a stored increment a void never corrected; and two computers
receiving one order each added the stock. These seven tests pin the cure at the
only level that can see it -- two independent databases exchanging real events:

  1. the order (header + items) arrives with the same uid / number / totals;
  2. instalments taken on two computers SUM (and a void REDUCES) the same way on both;
  3. receiving on the sibling moves stock and books once, everywhere;
  4. BOTH computers receiving offline still move the stock once (not twice);
  5. a cancel on the sibling reverses the order journal exactly once everywhere;
  6. an instalment never lands on an unrelated local PO that shares the integer id;
  7. a legacy PO (typed before W4a, never queued) reaches the sibling after the boot backfill.

Every expected figure below is hand-computed: one product ordered 10 @ 10.000
= 100.000, no tax. The order journal books Dr 1260 (goods ordered, not received)
100 / Cr AP 100; the receipt moves 100 from 1260 to 1200 (Inventory).

Run:
    pytest products/retail/tests/retail_po_sync_twodevice_test.py -v
"""
from __future__ import annotations

import json
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
    _read_device, _trial_balance, _write_device,
)

PY = sys.executable
QTY = 10
UNIT_COST = 10.0
PO_TOTAL = 100.0


def _acct(tb, code):
    return [a for a in tb['accounts'] if a['code'] == code][0]['balance']


# ── harness helpers ─────────────────────────────────────────────────────────

class Shop:
    """Two provisioned computers sharing one relay: A (originator) and B."""

    def __init__(self, tmp_path):
        self.relay = tmp_path / 'relay.db'
        self.app = {'a': tmp_path / 'device_a', 'b': tmp_path / 'device_b'}
        self.boot = {}
        self.pid = None

    def login(self, who):
        return {"op": "login", "email": self.boot[who]["email"], "password": self.boot[who]["password"]}

    def run(self, who, *actions, login=True):
        acts = ([self.login(who)] if login else []) + list(actions)
        out = run_device(PY, self.app[who], self.relay, acts)
        return out[1:] if login else out

    def http(self, who, method, path, body=None):
        [r] = self.run(who, {"op": "http", "method": method, "path": path, "json": body})
        return r

    def push(self, who):
        run_device(PY, self.app[who], self.relay, [{"op": "push"}])

    def pull(self, who):
        run_device(PY, self.app[who], self.relay, [{"op": "pull"}])

    def sync(self, *rounds):
        """Each round is a device name: push then pull-everything-for-the-other.
        `sync('a', 'b')` = A pushes, B pulls twice; then B pushes, A pulls twice."""
        for src in rounds:
            dst = 'b' if src == 'a' else 'a'
            self.push(src)
            run_device(PY, self.app[dst], self.relay, [{"op": "pull"}, {"op": "pull"}])

    def sql(self, who, query, params=()):
        return _read_device(self.app[who], query, params)

    def settle(self):
        """Full both-ways exchange, twice, so parked events replay."""
        self.sync('a', 'b', 'a', 'b')


def _provision(tmp_path):
    """A: bootstrap company, an operator branch, product (no stock), a supplier.
    B: bootstrap + pulls the catalogue. Bootstrap shops (not create_shop) so the
    operator-created branch is the one both computers file stock under."""
    s = Shop(tmp_path)
    [s.boot['a']] = run_device(PY, s.app['a'], s.relay, [{"op": "bootstrap"}])
    [s.boot['b']] = run_device(PY, s.app['b'], s.relay, [{"op": "bootstrap"}])
    branch, product, sup = s.run(
        'a',
        {"op": "create_branch", "name": "Warehouse", "address": "1 Rd", "phone": "555-0100"},
        {"op": "create_product", "name": "PO Sync Widget", "price": 20.0, "initial_stock": 0},
        {"op": "http", "method": "POST", "path": "/suppliers", "json": {"name": "W4a Sup"}},
    )
    assert branch["status_code"] == 200 and product["status_code"] == 200 and sup["status_code"] == 200, (
        branch, product, sup)
    s.pid = product["product_id"]
    s.sync('a')
    return s


def _supplier_id(s, who):
    rows = s.sql(who, "SELECT id FROM suppliers WHERE name='W4a Sup'")
    assert len(rows) == 1, (who, rows)
    return rows[0]["id"]


def _create_po(s, who, down_payment=None):
    body = {"supplier_id": _supplier_id(s, who),
            "items": [{"product_id": s.pid, "quantity": QTY, "unit_cost": UNIT_COST}]}
    if down_payment is not None:
        body["amount_paid"] = down_payment
    r = s.http(who, "POST", "/purchase-orders", body)
    assert r["status_code"] == 200, r
    return r["body"]["data"]["id"]


def _pos(s, who):
    return s.sql(who, "SELECT * FROM purchase_orders ORDER BY id")


def _po_by_uid(s, who, uid):
    rows = s.sql(who, "SELECT * FROM purchase_orders WHERE uid=?", (uid,)) if uid else []
    assert len(rows) == 1, f"{who}: expected exactly one PO with uid={uid!r}, got {rows}"
    return rows[0]


def _uid_of_local_po(s, who, po_id):
    rows = s.sql(who, "SELECT * FROM purchase_orders WHERE id=?", (po_id,))
    uid = rows[0].get("uid")
    assert uid, f"PO {po_id} on {who} has no uid -- a PO with no cross-computer identity cannot sync: {rows}"
    return uid


def _stock(s, who):
    rows = s.sql(who, "SELECT COALESCE(SUM(quantity_on_hand),0) AS q FROM inventory_balances WHERE product_id=?",
                 (s.pid,))
    return rows[0]["q"]


def _journals(s, who, source_type):
    return s.sql(who, "SELECT * FROM gl_journals WHERE source_type=? AND reversed_journal_id IS NULL",
                 (source_type,))


def _order_reversals(s, who):
    rows = s.sql(who, "SELECT COUNT(*) AS n FROM gl_journals r JOIN gl_journals o ON r.reversed_journal_id=o.id "
                      "WHERE o.source_type='purchase_order'")
    return rows[0]["n"]


def _tb(s, who):
    return _trial_balance(s.app[who], s.relay, s.boot[who]["email"], s.boot[who]["password"])


# ── 1. the order itself ─────────────────────────────────────────────────────

def test_po_created_on_a_appears_on_b_with_items(tmp_path):
    """RED before W4a: `purchase_orders` has no uid and nothing is queued, so B never
    learns the order exists."""
    s = _provision(tmp_path)
    po_id = _create_po(s, 'a')
    uid = _uid_of_local_po(s, 'a', po_id)
    a_po = _po_by_uid(s, 'a', uid)
    a_items = s.sql('a', "SELECT * FROM purchase_order_items WHERE po_id=?", (po_id,))
    assert a_po["total"] == PO_TOTAL and len(a_items) == 1, (a_po, a_items)

    s.sync('a')

    b_po = _po_by_uid(s, 'b', uid)
    assert b_po["po_number"] == a_po["po_number"]
    assert b_po["supplier_id"] == _supplier_id(s, 'b')
    assert b_po["total"] == PO_TOTAL and b_po["status"] == "pending"
    b_items = s.sql('b', "SELECT * FROM purchase_order_items WHERE po_id=?", (b_po["id"],))
    assert len(b_items) == 1, b_items
    assert b_items[0]["uid"] == a_items[0]["uid"] and b_items[0]["uid"]
    assert (b_items[0]["product_id"], b_items[0]["quantity"], b_items[0]["unit_cost"]) == (s.pid, QTY, UNIT_COST)
    # Pulling again changes nothing: still one PO, one item.
    s.pull('b')
    assert len(_pos(s, 'b')) == 1
    assert s.sql('b', "SELECT COUNT(*) AS n FROM purchase_order_items")[0]["n"] == 1


# ── 2. instalments on two computers add up ──────────────────────────────────

def test_po_instalments_on_two_devices_sum_not_overwrite(tmp_path):
    """RED before W4a: the PO never reaches B, and `amount_paid` was a stored
    increment (30 on A, 20 on B, each blind to the other) a void never corrected."""
    s = _provision(tmp_path)
    po_a = _create_po(s, 'a')
    uid = _uid_of_local_po(s, 'a', po_a)
    s.sync('a')
    po_b = _po_by_uid(s, 'b', uid)["id"]

    r = s.http('a', "POST", f"/purchase-orders/{po_a}/pay", {"amount": 30.0})
    assert r["status_code"] == 200, r
    s.sync('a')
    r = s.http('b', "POST", f"/purchase-orders/{po_b}/pay", {"amount": 20.0})
    assert r["status_code"] == 200, r
    s.settle()

    # Hand-computed: 30 + 20 = 50 of 100 -> partial, on BOTH computers.
    for who in ('a', 'b'):
        po = _po_by_uid(s, who, uid)
        assert (po["amount_paid"], po["payment_status"]) == (50.0, "partial"), (who, po)

    # Void A's 30 from B (a payment B received by sync): 20 left on both.
    thirty = s.sql('b', "SELECT id FROM payments WHERE related_type='po' AND amount=30 AND status='active'")
    assert len(thirty) == 1, thirty
    r = s.http('b', "POST", f"/payments/{thirty[0]['id']}/void", {"reason": "entered twice"})
    assert r["status_code"] == 200, r
    s.settle()
    for who in ('a', 'b'):
        po = _po_by_uid(s, who, uid)
        assert (po["amount_paid"], po["payment_status"]) == (20.0, "partial"), (who, po)

    # The supplier balance agrees too: owed 100, paid 20 -> 80 on both.
    bal = {who: s.sql(who, "SELECT credit_balance FROM suppliers WHERE name='W4a Sup'")[0]["credit_balance"]
           for who in ('a', 'b')}
    assert bal['a'] == bal['b'] == 80.0, bal


# ── 3. receive on the sibling ───────────────────────────────────────────────

def test_po_received_on_b_moves_stock_and_books_once_on_both(tmp_path):
    """RED before W4a: B has no such order to receive."""
    s = _provision(tmp_path)
    po_a = _create_po(s, 'a')
    uid = _uid_of_local_po(s, 'a', po_a)
    s.sync('a')
    po_b = _po_by_uid(s, 'b', uid)["id"]

    r = s.http('b', "POST", f"/purchase-orders/{po_b}/receive")
    assert r["status_code"] == 200, r
    s.settle()

    for who in ('a', 'b'):
        assert _stock(s, who) == QTY, (who, _stock(s, who))
        assert _po_by_uid(s, who, uid)["status"] == "received", who
        tb = _tb(s, who)
        # Order: Dr 1260 100. Receipt: Dr 1200 100 / Cr 1260 100 -> 1260 back to 0, 1200 = 100.
        assert _acct(tb, '1260') == 0.0, (who, _acct(tb, '1260'))
        assert _acct(tb, '1200') == PO_TOTAL, (who, _acct(tb, '1200'))
        assert len(_journals(s, who, 'po_receipt')) == 1, (who, _journals(s, who, 'po_receipt'))


# ── 4. both computers receive while apart ───────────────────────────────────

def test_double_offline_receive_moves_stock_once(tmp_path):
    """RED before W4a: B cannot even see the order; the contract also needs the
    receipt's movement uid to be derived from the ORDER, so two computers receiving
    the same delivery agree on one movement instead of adding the stock twice."""
    s = _provision(tmp_path)
    po_a = _create_po(s, 'a')
    uid = _uid_of_local_po(s, 'a', po_a)
    s.sync('a')
    po_b = _po_by_uid(s, 'b', uid)["id"]

    # Neither has seen the other's receipt: no sync between these two calls.
    ra = s.http('a', "POST", f"/purchase-orders/{po_a}/receive")
    rb = s.http('b', "POST", f"/purchase-orders/{po_b}/receive")
    assert ra["status_code"] == 200 and rb["status_code"] == 200, (ra, rb)
    s.settle()

    uids = {}
    for who in ('a', 'b'):
        assert _stock(s, who) == QTY, f"{who}: stock {_stock(s, who)} (2x would be {2 * QTY})"
        mv = s.sql(who, "SELECT uid, quantity FROM inventory_movements WHERE product_id=? "
                        "AND movement_type='purchase_in'", (s.pid,))
        assert len(mv) == 1 and mv[0]["quantity"] == QTY, (who, mv)
        uids[who] = mv[0]["uid"]
        assert len(_journals(s, who, 'po_receipt')) == 1, (who, _journals(s, who, 'po_receipt'))
        assert _po_by_uid(s, who, uid)["status"] == "received", who
        assert _acct(_tb(s, who), '1200') == PO_TOTAL, who
    assert uids['a'] == uids['b'] and uids['a'], uids


# ── 5. cancel on the sibling ────────────────────────────────────────────────

def test_po_cancel_on_b_reaches_a(tmp_path):
    """RED before W4a: B has no such order to cancel."""
    s = _provision(tmp_path)
    po_a = _create_po(s, 'a')
    uid = _uid_of_local_po(s, 'a', po_a)
    s.sync('a')
    po_b = _po_by_uid(s, 'b', uid)["id"]

    r = s.http('b', "POST", f"/purchase-orders/{po_b}/cancel")
    assert r["status_code"] == 200, r
    s.settle()

    for who in ('a', 'b'):
        assert _po_by_uid(s, who, uid)["status"] == "cancelled", who
        assert _order_reversals(s, who) == 1, (who, _order_reversals(s, who))
        assert _acct(_tb(s, who), '1260') == 0.0, who


# ── 6. an instalment never lands on the wrong local PO ──────────────────────

def test_sibling_payment_never_lands_on_unrelated_local_po(tmp_path):
    """The defect that made this wave necessary: A's instalment carried
    `related_id=1` -- A's integer -- and B applied it to ITS OWN PO number 1.
    Both computers mint their first PO as id 1, so the collision is guaranteed."""
    s = _provision(tmp_path)
    own_b = _create_po(s, 'b')           # B's own order, local id 1
    po_a = _create_po(s, 'a')            # A's order, ALSO local id 1
    assert own_b == po_a == 1, (own_b, po_a)  # the trap this test exists for
    uid_a = _uid_of_local_po(s, 'a', po_a)
    uid_b = _uid_of_local_po(s, 'b', own_b)
    assert uid_a != uid_b

    r = s.http('a', "POST", f"/purchase-orders/{po_a}/pay", {"amount": 30.0})
    assert r["status_code"] == 200, r
    s.settle()

    a_on_b = _po_by_uid(s, 'b', uid_a)
    mine_on_b = _po_by_uid(s, 'b', uid_b)
    assert a_on_b["id"] != mine_on_b["id"], (a_on_b["id"], mine_on_b["id"])
    assert (a_on_b["amount_paid"], a_on_b["payment_status"]) == (30.0, "partial"), a_on_b
    # B's own order was never touched by A's money.
    assert (mine_on_b["amount_paid"], mine_on_b["payment_status"]) == (0.0, "unpaid"), mine_on_b
    pay = s.sql('b', "SELECT related_id FROM payments WHERE related_type='po' AND amount=30")
    assert [p["related_id"] for p in pay] == [a_on_b["id"]], pay


# ── 7. a legacy PO reaches the sibling through the boot backfill ────────────

def test_legacy_po_backfill_reaches_sibling(tmp_path):
    """A PO typed before W4a was never queued. Re-create that state faithfully: the
    PO + item events are removed from A's outbox, the payment's event loses its
    `po_uid` (a pre-W4a writer never sent one), and the row is marked
    `sync_backfill_pending=1` -- exactly what the v45 migration does to legacy rows.
    The NEXT launch of A is the boot that runs the backfill (see
    retail_gl_two_device_sync_test.py::test_pre_upgrade_journal_heals_after_reboot_backfill)."""
    s = _provision(tmp_path)
    po_a = _create_po(s, 'a')
    uid = _uid_of_local_po(s, 'a', po_a)
    r = s.http('a', "POST", f"/purchase-orders/{po_a}/pay", {"amount": 30.0})
    assert r["status_code"] == 200, r
    # Precondition: the new code DID queue them (otherwise deleting proves nothing).
    [n_po, n_item] = s.run('a', {"op": "outbox_count_by_type", "entity_type": "purchase_order"},
                           {"op": "outbox_count_by_type", "entity_type": "purchase_order_item"})
    assert n_po["count"] == 1 and n_item["count"] == 1, (n_po, n_item)

    s.run('a', {"op": "outbox_delete_entity_type", "entity_type": "purchase_order"},
          {"op": "outbox_delete_entity_type", "entity_type": "purchase_order_item"}, login=False)
    for row in s.sql('a', "SELECT id, payload FROM sync_outbox WHERE entity_type='payment'"):
        payload = json.loads(row["payload"])
        if payload.get("related_type") == "po":
            payload.pop("po_uid", None)
            _write_device(s.app['a'], "UPDATE sync_outbox SET payload=? WHERE id=?",
                          (json.dumps(payload), row["id"]))
    _write_device(s.app['a'], "UPDATE purchase_orders SET sync_backfill_pending=1 WHERE uid=?", (uid,))

    # The reboot: this launch's boot backfill queues the PO, items and the payment link;
    # the same invocation's push flushes them.
    s.push('a')
    assert _po_by_uid(s, 'a', uid)["sync_backfill_pending"] == 0
    s.pull('b')
    s.pull('b')

    b_po = _po_by_uid(s, 'b', uid)
    assert b_po["total"] == PO_TOTAL
    assert s.sql('b', "SELECT COUNT(*) AS n FROM purchase_order_items WHERE po_id=?", (b_po["id"],))[0]["n"] == 1
    pay = s.sql('b', "SELECT related_id FROM payments WHERE related_type='po' AND amount=30")
    assert [p["related_id"] for p in pay] == [b_po["id"]], (pay, b_po)
    assert (b_po["amount_paid"], b_po["payment_status"]) == (30.0, "partial"), b_po
