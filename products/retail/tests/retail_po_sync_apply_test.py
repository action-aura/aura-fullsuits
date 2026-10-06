"""W4a (schema v45) on the sync APPLY side: purchase orders shared between
computers.

Before W4a a PO lived only on the computer that typed it: `purchase_orders.id`
is a per-computer integer and nothing was queued, so a sibling computer could
not see, receive or cancel it, and a sibling's instalment arrived carrying
`related_id=<the writer's integer>` -- which on the receiver named an UNRELATED
local PO. This file pins what the receiving side must do with the new
`purchase_order` / `purchase_order_item` entities and the new `po_uid` /
`po_link` payment wire shapes:

  * create a PO under the RECEIVER's company and branch (never the sender's),
    once (a replay is the same fact),
  * park -- never raise, never drop -- anything whose parent has not arrived,
  * merge the header status FORWARD ONLY (pending < cancelled < received): a
    delayed old event must never un-receive an order, and received-vs-cancelled
    is recorded as a conflict with "received" winning,
  * resolve a payment's PO by `po_uid`, never by the writer's integer, and give
    a pre-v45 payment (no `po_uid`) NO PO rather than a wrong one,
  * recompute `amount_paid` / `payment_status` from the synced payments every
    time a payment or a PO lands (the figure is derived, never shipped).

Same fixture technique as test_party_balance_adjustment_apply.py: a REAL
`init_retail()` database, `SyncService._apply_event` driven directly. One file
per process (the retail modules keep module-level state).

Run:  <venv python> -m pytest -q -p no:cacheprovider products/retail/tests/retail_po_sync_apply_test.py
"""
from __future__ import annotations

import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RETAIL_BACKEND_DIR = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_RETAIL_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import database.schema as schema  # noqa: E402
import api.retail_api as _retail_api_module  # noqa: E402
from api.retail_api import _ensure_credit_schema  # noqa: E402
from commercial_runtime.identity import registry_db  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402

CID = "company-po-apply-test"
SUPPLIER = str(uuid.uuid4())
PRODUCT = str(uuid.uuid4())
PO_UID = str(uuid.uuid4())
ITEM_UID = str(uuid.uuid4())


@pytest.fixture
def get_conn(tmp_path, monkeypatch):
    reg_dir = tmp_path / "registry_db"
    monkeypatch.setattr(registry_db, "_db_dir", str(reg_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(reg_dir / "registry.db"))
    registry_db.init_registry_db()
    root = tmp_path / "install"
    subsys = root / "subsystems"
    subsys.mkdir(parents=True, exist_ok=True)
    original = (schema.BASE_DIR, schema.SUBSYS_DIR)
    schema.BASE_DIR, schema.SUBSYS_DIR = str(root), str(subsys)
    try:
        schema.init_retail()
    finally:
        schema.BASE_DIR, schema.SUBSYS_DIR = original
    db_path = subsys / "retail.db"

    def _get():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    previous = _retail_api_module._CREDIT_SCHEMA_READY
    _retail_api_module._CREDIT_SCHEMA_READY = False
    try:
        c = _get()
        try:
            # The `payments` columns the apply branch INSERTs are created
            # lazily by retail_api, not by init_retail (see CLAUDE memory
            # "Lazy columns + migration order") -- ensure them like the
            # sibling apply tests do.
            _ensure_credit_schema(c)
            c.execute("INSERT INTO suppliers (id, company_id, name) VALUES (?,?,?)",
                      (SUPPLIER, CID, "Sup"))
            c.execute("INSERT INTO products (id, company_id, sku, name) VALUES (?,?,?,?)",
                      (PRODUCT, CID, "SKU-1", "Widget"))
            c.commit()
        finally:
            c.close()
    finally:
        _retail_api_module._CREDIT_SCHEMA_READY = previous
    return _get


def _svc(get_conn):
    return SyncService(client_factory=lambda: None, get_conn=get_conn,
                       local_company_id_provider=lambda: CID)


def _apply(get_conn, ev):
    conn = get_conn()
    try:
        ok = _svc(get_conn)._apply_event(conn, ev, local_company_id=CID)
        conn.commit()
        return ok
    finally:
        conn.close()


def _retry(get_conn):
    conn = get_conn()
    try:
        _svc(get_conn)._retry_quarantined_events(conn, local_company_id=CID)
        conn.commit()
    finally:
        conn.close()


def _rows(get_conn, sql, params=()):
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _exec(get_conn, sql, params=()):
    conn = get_conn()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _po(**over):
    p = {"uid": PO_UID, "po_number": "PO-0001", "supplier_id": SUPPLIER,
         "branch_uid": str(uuid.uuid4()), "status": "pending",
         "subtotal": 100.0, "tax_total": 0.0, "total": 100.0, "notes": "n",
         "ordered_at": "2026-10-06 09:00:00", "received_at": None,
         "created_at": "2026-10-06 09:00:00", "due_date": None,
         "order_journal_uid": None, "split_group_id": None, "split_index": None,
         "split_count": None, "routing_status": None, "routed_channel": None,
         "routed_at": None, "routed_to": None, "idempotency_key": None,
         # A hostile/buggy sender's tenant key must never be trusted.
         "company_id": "the-senders-company"}
    p.update(over)
    return {"entity_type": "purchase_order", "event_type": "create",
            "entity_id": p["uid"], "payload": p}


def _po_update(status, received_at=None, uid=PO_UID):
    return {"entity_type": "purchase_order", "event_type": "update", "entity_id": uid,
            "payload": {"uid": uid, "status": status, "received_at": received_at}}


def _item(uid=ITEM_UID, po_uid=PO_UID, product_id=None, qty=4.0):
    p = {"uid": uid, "po_uid": po_uid, "product_id": product_id or PRODUCT,
         "quantity": qty, "unit_cost": 25.0, "total": qty * 25.0, "received_qty": 0}
    return {"entity_type": "purchase_order_item", "event_type": "create",
            "entity_id": uid, "payload": p}


def _pay(uid=None, amount=30.0, po_uid=PO_UID, related_id=1, with_po_uid=True, **over):
    uid = uid or str(uuid.uuid4())
    p = {"uid": uid, "reference": "R-" + uid[:6], "party_type": "supplier",
         "party_id": SUPPLIER, "direction": "out", "amount": amount, "currency": "USD",
         "method": "cash", "related_type": "po", "related_id": related_id,
         "notes": "", "status": "active", "created_by": "x", "device": "A",
         "created_at": "2026-10-06 10:00:00"}
    if with_po_uid:
        p["po_uid"] = po_uid
    p.update(over)
    return {"entity_type": "payment", "event_type": "create", "entity_id": uid, "payload": p}


def _void(uid):
    return {"entity_type": "payment", "event_type": "update", "entity_id": uid,
            "payload": {"uid": uid, "status": "voided"}}


def _link(payment_uid, po_uid=PO_UID):
    return {"entity_type": "payment", "event_type": "po_link", "entity_id": payment_uid,
            "payload": {"uid": payment_uid, "po_uid": po_uid}}


def _po_row(get_conn, uid=PO_UID):
    r = _rows(get_conn, "SELECT * FROM purchase_orders WHERE uid=?", (uid,))
    return r[0] if r else None


def _parked(get_conn):
    return _rows(get_conn, "SELECT entity_type, event_type, reason FROM sync_apply_quarantine")


def _local_po(get_conn, number="LOCAL-1", total=500.0):
    """A PO this computer typed itself -- takes the first autoincrement id,
    the very integer a sibling's instalment will carry as `related_id`."""
    _exec(get_conn,
          "INSERT INTO purchase_orders (company_id, po_number, supplier_id, status, total, uid) "
          "VALUES (?,?,?,?,?,?)", (CID, number, SUPPLIER, "pending", total, str(uuid.uuid4())))
    return _rows(get_conn, "SELECT id, uid FROM purchase_orders WHERE po_number=?", (number,))[0]


# ── create ────────────────────────────────────────────────────────────────────

def test_create_lands_under_the_receivers_company_and_branch(get_conn):
    assert _apply(get_conn, _po()) is True
    row = _po_row(get_conn)
    assert row is not None
    assert row["company_id"] == CID, "tenant key must be the receiver's, never the payload's"
    assert row["po_number"] == "PO-0001" and row["supplier_id"] == SUPPLIER
    assert row["status"] == "pending" and row["total"] == 100.0
    # An unknown branch_uid falls back to THIS computer's own branch.
    assert row["branch_id"] is not None
    assert row["amount_paid"] == 0 and row["payment_status"] == "unpaid"
    assert row["sync_backfill_pending"] == 0, "a receiver must never re-queue what it pulled"


def test_f1_create_stores_the_writers_line_count(get_conn):
    """W4a verify F1: the receiver keeps `item_count` so receive can tell that a
    line is still parked. Absent (an older writer) is stored as NULL, not 0 --
    0 would read as "an order with no lines" and a fabricated count would
    either strand the order or hide a missing line."""
    assert _apply(get_conn, _po(item_count=3)) is True
    assert _po_row(get_conn)["item_count"] == 3
    other = str(uuid.uuid4())
    assert _apply(get_conn, _po(uid=other, po_number="PO-0002")) is True
    assert _po_row(get_conn, other)["item_count"] is None


def test_create_replayed_is_a_noop(get_conn):
    _apply(get_conn, _po())
    _apply(get_conn, _po())
    assert len(_rows(get_conn, "SELECT id FROM purchase_orders WHERE uid=?", (PO_UID,))) == 1


def test_create_with_unknown_supplier_parks_instead_of_raising(get_conn):
    ev = _po(supplier_id=str(uuid.uuid4()))
    assert _apply(get_conn, ev) is False
    assert _po_row(get_conn) is None
    assert ("purchase_order", "create", "missing_parent:supplier") in {
        (r["entity_type"], r["event_type"], r["reason"]) for r in _parked(get_conn)}


def test_duplicate_po_number_under_another_uid_parks(get_conn):
    _apply(get_conn, _po())
    other = _po(uid=str(uuid.uuid4()))  # same po_number, different order
    assert _apply(get_conn, other) is False
    assert [r["reason"] for r in _parked(get_conn)] == ["duplicate_document_number"]
    assert len(_rows(get_conn, "SELECT id FROM purchase_orders")) == 1


# ── items ─────────────────────────────────────────────────────────────────────

def test_item_before_parent_parks_then_replays_when_parent_arrives(get_conn):
    assert _apply(get_conn, _item()) is False
    assert [r["reason"] for r in _parked(get_conn)] == ["missing_parent:purchase_order"]
    _apply(get_conn, _po())
    _retry(get_conn)
    items = _rows(get_conn, "SELECT * FROM purchase_order_items WHERE uid=?", (ITEM_UID,))
    assert len(items) == 1 and items[0]["quantity"] == 4.0
    assert items[0]["po_id"] == _po_row(get_conn)["id"], "item must hang off the LOCAL po id"
    assert _parked(get_conn) == []


def test_item_with_unknown_product_parks(get_conn):
    _apply(get_conn, _po())
    assert _apply(get_conn, _item(product_id=str(uuid.uuid4()))) is False
    assert [r["reason"] for r in _parked(get_conn)] == ["missing_parent:product"]


def test_item_replayed_is_a_noop(get_conn):
    _apply(get_conn, _po())
    _apply(get_conn, _item())
    _apply(get_conn, _item())
    assert len(_rows(get_conn, "SELECT id FROM purchase_order_items")) == 1


# ── header status: forward-only merge ─────────────────────────────────────────

def test_update_received_applies_and_fills_received_qty(get_conn):
    _apply(get_conn, _po())
    _apply(get_conn, _item())
    assert _apply(get_conn, _po_update("received", "2026-10-06 12:00:00")) is True
    row = _po_row(get_conn)
    assert row["status"] == "received" and row["received_at"] == "2026-10-06 12:00:00"
    assert _rows(get_conn, "SELECT received_qty, quantity FROM purchase_order_items")[0] == {
        "received_qty": 4.0, "quantity": 4.0}


def test_update_never_moves_status_backwards(get_conn):
    """The guard that makes a delayed or replayed old event harmless: an order
    already received here must not be flipped back to pending."""
    _apply(get_conn, _po())
    _apply(get_conn, _po_update("received", "2026-10-06 12:00:00"))
    _apply(get_conn, _po_update("pending"))
    row = _po_row(get_conn)
    assert row["status"] == "received" and row["received_at"] == "2026-10-06 12:00:00"


def test_received_then_cancelled_keeps_received_and_records_a_conflict(get_conn):
    _apply(get_conn, _po())
    _apply(get_conn, _po_update("received", "2026-10-06 12:00:00"))
    _apply(get_conn, _po_update("cancelled"))
    assert _po_row(get_conn)["status"] == "received"
    conflicts = _rows(get_conn, "SELECT * FROM sync_conflicts WHERE entity_type='purchase_order'")
    assert len(conflicts) == 1 and "po_received_and_cancelled" in conflicts[0]["incoming_payload"]


def test_cancelled_then_received_ends_received_and_records_a_conflict(get_conn):
    _apply(get_conn, _po())
    _apply(get_conn, _po_update("cancelled"))
    _apply(get_conn, _po_update("received", "2026-10-06 12:00:00"))
    assert _po_row(get_conn)["status"] == "received"
    conflicts = _rows(get_conn, "SELECT * FROM sync_conflicts WHERE entity_type='purchase_order'")
    assert len(conflicts) == 1 and "po_received_and_cancelled" in conflicts[0]["incoming_payload"]


def test_pending_to_cancelled_applies_without_a_conflict(get_conn):
    _apply(get_conn, _po())
    _apply(get_conn, _po_update("cancelled"))
    assert _po_row(get_conn)["status"] == "cancelled"
    assert _rows(get_conn, "SELECT * FROM sync_conflicts") == []


def test_update_for_unknown_po_parks(get_conn):
    assert _apply(get_conn, _po_update("received", "x")) is False
    assert [r["reason"] for r in _parked(get_conn)] == ["missing_parent:purchase_order"]


def test_update_never_posts_stock_or_books(get_conn):
    """Stock and journals sync as their own entities; the header apply must not
    ALSO move them (that would double-count on the receiver)."""
    _apply(get_conn, _po())
    _apply(get_conn, _item())
    before = (_rows(get_conn, "SELECT COUNT(*) AS n FROM inventory_movements")[0]["n"],
              _rows(get_conn, "SELECT COUNT(*) AS n FROM gl_journals")[0]["n"])
    _apply(get_conn, _po_update("received", "2026-10-06 12:00:00"))
    after = (_rows(get_conn, "SELECT COUNT(*) AS n FROM inventory_movements")[0]["n"],
             _rows(get_conn, "SELECT COUNT(*) AS n FROM gl_journals")[0]["n"])
    assert before == after


# ── payments: resolved by po_uid, never by the writer's integer ───────────────

def test_payment_with_po_uid_resolves_to_the_right_local_po(get_conn):
    unrelated = _local_po(get_conn)            # takes local id 1 -- the writer's integer too
    _apply(get_conn, _po())                    # the sibling's PO gets a different local id
    mine = _po_row(get_conn)
    assert mine["id"] != unrelated["id"]
    p = _pay(amount=30.0, related_id=unrelated["id"])  # writer's integer collides with ours
    _apply(get_conn, p)
    pay = _rows(get_conn, "SELECT related_type, related_id FROM payments WHERE uid=?",
                (p["payload"]["uid"],))[0]
    assert pay == {"related_type": "po", "related_id": mine["id"]}
    after = _po_row(get_conn)
    assert (after["amount_paid"], after["payment_status"]) == (30.0, "partial")
    other = _rows(get_conn, "SELECT amount_paid, payment_status FROM purchase_orders WHERE id=?",
                  (unrelated["id"],))[0]
    assert (other["amount_paid"] or 0, other["payment_status"]) == (0, "unpaid"), \
        "an unrelated local PO must never be touched by a sibling's instalment"


def test_instalments_sum_and_full_payment_flips_to_paid(get_conn):
    _apply(get_conn, _po())
    _apply(get_conn, _pay(amount=30.0))
    _apply(get_conn, _pay(amount=70.0))
    row = _po_row(get_conn)
    assert (row["amount_paid"], row["payment_status"]) == (100.0, "paid")


def test_legacy_payment_without_po_uid_gets_no_po(get_conn):
    """A pre-v45 writer sends only its own integer. On this computer that
    integer names a DIFFERENT order, which is the bug W4a fixes -- the safe
    answer is 'no PO' (a later po_link can attach it), never the wrong PO."""
    unrelated = _local_po(get_conn)
    p = _pay(with_po_uid=False, related_id=unrelated["id"], amount=40.0)
    assert _apply(get_conn, p) is True
    pay = _rows(get_conn, "SELECT related_type, related_id FROM payments WHERE uid=?",
                (p["payload"]["uid"],))[0]
    assert pay["related_id"] is None
    other = _rows(get_conn, "SELECT amount_paid FROM purchase_orders WHERE id=?", (unrelated["id"],))[0]
    assert (other["amount_paid"] or 0) == 0


def test_payment_before_its_po_parks_then_applies_and_recomputes(get_conn):
    p = _pay(amount=30.0)
    assert _apply(get_conn, p) is False
    assert [r["reason"] for r in _parked(get_conn)] == ["missing_parent:purchase_order"]
    _apply(get_conn, _po())
    _retry(get_conn)
    assert _parked(get_conn) == []
    row = _po_row(get_conn)
    assert (row["amount_paid"], row["payment_status"]) == (30.0, "partial")


def test_void_reduces_amount_paid(get_conn):
    _apply(get_conn, _po())
    a, b = _pay(amount=30.0), _pay(amount=20.0)
    _apply(get_conn, a)
    _apply(get_conn, b)
    assert _po_row(get_conn)["amount_paid"] == 50.0
    _apply(get_conn, _void(a["payload"]["uid"]))
    row = _po_row(get_conn)
    assert (row["amount_paid"], row["payment_status"]) == (20.0, "partial")


def test_payment_for_another_company_po_id_is_not_counted(get_conn):
    """The recompute is company-scoped like every other derived figure."""
    _apply(get_conn, _po())
    _apply(get_conn, _pay(amount=30.0))
    _exec(get_conn, "UPDATE payments SET company_id='someone-else'")
    # Any later recompute trigger must now see zero active payments for CID.
    _apply(get_conn, _pay(amount=10.0))
    assert _po_row(get_conn)["amount_paid"] == 10.0


# ── po_link: attach a legacy payment ──────────────────────────────────────────

def test_po_link_attaches_a_legacy_payment_and_recomputes(get_conn):
    _apply(get_conn, _po())
    p = _pay(with_po_uid=False, amount=25.0, related_id=999)
    _apply(get_conn, p)
    assert _po_row(get_conn)["amount_paid"] == 0
    assert _apply(get_conn, _link(p["payload"]["uid"])) is True
    pay = _rows(get_conn, "SELECT related_type, related_id FROM payments WHERE uid=?",
                (p["payload"]["uid"],))[0]
    assert pay == {"related_type": "po", "related_id": _po_row(get_conn)["id"]}
    assert _po_row(get_conn)["amount_paid"] == 25.0
    _apply(get_conn, _link(p["payload"]["uid"]))  # idempotent
    assert _po_row(get_conn)["amount_paid"] == 25.0


def test_po_link_moving_a_payment_recomputes_both_orders(get_conn):
    first = _local_po(get_conn, total=100.0)
    _apply(get_conn, _po())
    p = _pay(with_po_uid=False, amount=25.0, related_id=first["id"])
    # Seed the payment as a pre-v45 sibling would have left it: pointing at
    # the LOCAL first order by the writer's integer (the wrong-order shape).
    _exec(get_conn, "INSERT INTO payments (company_id, reference, party_type, party_id, direction, "
                    "amount, currency, method, related_type, related_id, status, created_at, uid) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (CID, "R", "supplier", SUPPLIER, "out", 25.0, "USD", "cash", "po", first["id"],
           "active", "2026-10-06 10:00:00", p["payload"]["uid"]))
    _apply(get_conn, _link(p["payload"]["uid"]))
    now_first = _rows(get_conn, "SELECT amount_paid, payment_status FROM purchase_orders WHERE id=?",
                      (first["id"],))[0]
    assert (now_first["amount_paid"], now_first["payment_status"]) == (0, "unpaid"), \
        "the order the payment LEFT must be recomputed too"
    assert _po_row(get_conn)["amount_paid"] == 25.0


def test_po_link_for_unknown_payment_or_po_parks(get_conn):
    _apply(get_conn, _po())
    assert _apply(get_conn, _link(str(uuid.uuid4()))) is False
    p = _pay(with_po_uid=False, amount=5.0, related_id=1)
    _apply(get_conn, p)
    assert _apply(get_conn, _link(p["payload"]["uid"], po_uid=str(uuid.uuid4()))) is False
    assert sorted(r["reason"] for r in _parked(get_conn)) == [
        "missing_parent:payment", "missing_parent:purchase_order"]


# ── parity with the writer-side module ────────────────────────────────────────

def test_receiver_recompute_matches_the_writer_side_module(get_conn):
    """commercial_runtime cannot import products/, so the receiver carries a
    twin of core/retail/purchase_orders.recompute_po_paid. If the two ever
    disagree, the computer that typed an order and the one that pulled it show
    different 'paid' for the same instalments -- this pins them together."""
    from core.retail import purchase_orders as po_mod
    from core.retail import party_balance
    assert dict(po_mod.PO_STATUS_RANK) == dict(__import__(
        "commercial_runtime.sync.sync_service", fromlist=["x"]).PO_STATUS_RANK)
    _apply(get_conn, _po())
    po_id = _po_row(get_conn)["id"]
    eps = party_balance.money_epsilon("USD")
    uids = []
    for amount in (0.0, 30.0, 70.0):
        if amount:
            ev = _pay(amount=amount)
            uids.append(ev["payload"]["uid"])
            _apply(get_conn, ev)
        ours = (_po_row(get_conn)["amount_paid"], _po_row(get_conn)["payment_status"])
        conn = get_conn()
        try:
            theirs = po_mod.recompute_po_paid(conn, CID, po_id, eps)
            conn.commit()
        finally:
            conn.close()
        assert ours == theirs
    _apply(get_conn, _void(uids[0]))
    ours = (_po_row(get_conn)["amount_paid"], _po_row(get_conn)["payment_status"])
    conn = get_conn()
    try:
        assert ours == po_mod.recompute_po_paid(conn, CID, po_id, eps)
    finally:
        conn.close()
    assert ours == (70.0, "partial")


# ── W4a verify F2: a po_link must never come back to the computer that recorded the payment ──

def test_f2_po_link_arriving_at_the_origin_is_ignored_and_recorded_once(get_conn):
    """This computer recorded the payment (it holds a LOCAL po_payment journal:
    source_id = a real integer). A po_link for it, from any other computer, is
    by construction wrong -- the origin knows which order it paid. Re-pointing
    it would move our money onto another order. Ignored, with one conflict
    row so a human can see it (not one per redelivery)."""
    mine = _local_po(get_conn, total=100.0)
    _apply(get_conn, _po())                       # the order the stray link names
    pay_uid = str(uuid.uuid4())
    _exec(get_conn, "INSERT INTO payments (company_id, reference, party_type, party_id, direction, "
                    "amount, currency, method, related_type, related_id, status, created_at, uid) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (CID, "R", "supplier", SUPPLIER, "out", 25.0, "USD", "cash", "po", mine["id"],
           "active", "2026-10-06 10:00:00", pay_uid))
    _exec(get_conn, "INSERT INTO gl_journals (company_id, business_date, source_type, source_id, "
                    "currency, uid, source_uid) VALUES (?,?,?,?,?,?,?)",
          (CID, "2026-10-06", "po_payment", mine["id"], "USD", str(uuid.uuid4()), pay_uid))

    assert _apply(get_conn, _link(pay_uid)) is True
    assert _apply(get_conn, _link(pay_uid)) is True        # redelivery
    row = _rows(get_conn, "SELECT related_type, related_id FROM payments WHERE uid=?", (pay_uid,))[0]
    assert row == {"related_type": "po", "related_id": mine["id"]}, \
        "the origin's own payment must stay on the order it paid"
    conflicts = _rows(get_conn, "SELECT * FROM sync_conflicts WHERE entity_type='payment'")
    assert len(conflicts) == 1 and "po_link_from_non_origin" in conflicts[0]["incoming_payload"]
    assert _po_row(get_conn)["amount_paid"] == 0, "the stray target order must not gain the money"


# ── W4a verify F3: a redelivered losing event is ONE conflict, not one per delivery ──

def test_f3_redelivered_losing_cancel_records_one_conflict(get_conn):
    _apply(get_conn, _po())
    _apply(get_conn, _po_update("received", "2026-10-06 12:00:00"))
    _apply(get_conn, _po_update("cancelled"))
    _apply(get_conn, _po_update("cancelled"))      # the same event, delivered again
    conflicts = _rows(get_conn, "SELECT * FROM sync_conflicts WHERE entity_type='purchase_order'")
    assert len(conflicts) == 1 and "po_received_and_cancelled" in conflicts[0]["incoming_payload"]
    assert _po_row(get_conn)["status"] == "received"


# ── W4a verify F4: parents are looked up inside the receiver's company ──

def test_f4_children_never_attach_to_another_companys_order(get_conn):
    foreign_uid = str(uuid.uuid4())
    _exec(get_conn, "INSERT INTO purchase_orders (company_id, po_number, supplier_id, status, total, uid) "
                    "VALUES (?,?,?,?,?,?)", ("someone-else", "THEIRS-1", SUPPLIER, "pending", 50.0, foreign_uid))
    assert _apply(get_conn, _item(po_uid=foreign_uid)) is False
    assert _apply(get_conn, _pay(po_uid=foreign_uid)) is False
    p = _pay(with_po_uid=False, related_id=1)
    _apply(get_conn, p)
    assert _apply(get_conn, _link(p["payload"]["uid"], po_uid=foreign_uid)) is False
    assert _rows(get_conn, "SELECT id FROM purchase_order_items") == []
    assert [r["reason"] for r in _parked(get_conn)] == ["missing_parent:purchase_order"] * 3
    theirs = _rows(get_conn, "SELECT amount_paid FROM purchase_orders WHERE uid=?", (foreign_uid,))[0]
    assert (theirs["amount_paid"] or 0) == 0


# ── W4a verify F5: the two recomputes must agree in EVERY currency, at the boundary ──
#
# The test above runs the receiver in the shop's default currency (JOD, 3 dp)
# but hands the writer-side function USD's epsilon (0.005): they were never
# asked the same question, so it could not catch the two disagreeing about
# where "paid" begins. Here both run with the SAME currency, for a 3-dp, a
# 2-dp and a 0-dp currency, with the outstanding amount landing exactly on,
# just under and just over half a minor unit (the point the status flips).

#: (currency, total, paid, why). Half a minor unit is 0.0005 / 0.005 / 0.5.
_PARITY_CASES = [
    ("JOD", 100.0, 99.9995, "JOD: outstanding exactly half a fil"),
    ("JOD", 100.0, 99.9994, "JOD: outstanding just OVER half a fil"),
    ("JOD", 100.0, 99.9996, "JOD: outstanding just UNDER half a fil"),
    ("JOD", 100.0, 99.999, "JOD: one whole fil outstanding"),
    ("USD", 100.0, 99.995, "USD: outstanding exactly half a cent"),
    ("USD", 100.0, 99.994, "USD: just OVER half a cent"),
    ("USD", 100.0, 99.996, "USD: just UNDER half a cent"),
    ("USD", 100.0, 99.99, "USD: one whole cent outstanding"),
    ("JPY", 100.0, 99.5, "JPY (0 dp): outstanding exactly half a yen"),
    ("JPY", 100.0, 99.4, "JPY: just OVER half a yen"),
    ("JPY", 100.0, 99.6, "JPY: just UNDER half a yen"),
    ("JPY", 100.0, 99.0, "JPY: one whole yen outstanding"),
    ("USD", 0.0, 0.0, "USD: a zero-total order is never 'paid'"),
]


@pytest.mark.parametrize("currency,total,paid,why", _PARITY_CASES, ids=[c[3] for c in _PARITY_CASES])
def test_f5_receiver_and_writer_recompute_agree_in_every_currency(get_conn, currency, total, paid, why):
    from core.retail import purchase_orders as po_mod
    from core.retail import party_balance
    _exec(get_conn, "INSERT OR REPLACE INTO retail_settings (company_id, skey, svalue) VALUES (?,?,?)",
          (CID, "base_currency", currency))
    _apply(get_conn, _po(total=total, subtotal=total))
    po_id = _po_row(get_conn)["id"]
    if paid:
        _apply(get_conn, _pay(amount=paid, currency=currency))
    ours = (_po_row(get_conn)["amount_paid"], _po_row(get_conn)["payment_status"])
    conn = get_conn()
    try:
        theirs = po_mod.recompute_po_paid(conn, CID, po_id, party_balance.money_epsilon(currency))
        conn.commit()
    finally:
        conn.close()
    assert ours == theirs, (why, "receiver", ours, "writer", theirs)
