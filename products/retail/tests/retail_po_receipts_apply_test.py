"""W4a-2 (schema v46) on the sync APPLY side: purchase-order RECEIPTS.

Before v46 a purchase order was received whole: one `purchase_order` header
`update` {status:'received'} flipped every line. v46 makes each delivery an
immutable synced FACT -- a `purchase_order_receipt` header plus one
`purchase_order_receipt_line` per line -- and everything else (the lines'
`received_qty`, the order's status, its `received_at`) is DERIVED from those
facts plus the set-once `cancelled_at` / `closed_at`. This file pins what a
RECEIVING computer must do with them:

  * park -- never raise, never drop -- a receipt whose order has not arrived
    and a line whose receipt or item has not arrived; replay once they do,
  * apply each exactly once (a replay, or a sibling that recorded the identical
    delivery under the same deterministic uid, is one fact),
  * stamp the RECEIVER's company, never move stock and never post books (the
    writer's movements and journals sync as their own entities),
  * re-derive the order's state after every receipt / line / header / payment
    apply, through the hook the retail API installs (SS cannot import products/),
  * record an over-receipt (two different offline deliveries exceeding the
    order) as a conflict ONCE, and still apply both (they are facts),
  * merge the header's `cancelled_at` / `closed_at` set-once (earliest wins) and
    still understand a v45 writer's {status} payload.

Same fixture technique as retail_po_sync_apply_test.py: a REAL `init_retail()`
database, `SyncService._apply_event` driven directly. One file per process.

Run:  <venv python> -m pytest -q -p no:cacheprovider products/retail/tests/retail_po_receipts_apply_test.py
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
from commercial_runtime.sync import sync_service as _ss  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402

CID = "company-po-receipt-test"
SUPPLIER = str(uuid.uuid4())
PRODUCT = str(uuid.uuid4())
PRODUCT_B = str(uuid.uuid4())
PO_UID = str(uuid.uuid4())
ITEM_A = str(uuid.uuid4())
ITEM_B = str(uuid.uuid4())


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
            _ensure_credit_schema(c)
            c.execute("INSERT INTO suppliers (id, company_id, name) VALUES (?,?,?)",
                      (SUPPLIER, CID, "Sup"))
            for pid, sku in ((PRODUCT, "SKU-A"), (PRODUCT_B, "SKU-B")):
                c.execute("INSERT INTO products (id, company_id, sku, name) VALUES (?,?,?,?)",
                          (pid, CID, sku, sku))
            c.commit()
        finally:
            c.close()
    finally:
        _retail_api_module._CREDIT_SCHEMA_READY = previous
    return _get


def _svc(get_conn, hook=None):
    svc = SyncService(client_factory=lambda: None, get_conn=get_conn,
                      local_company_id_provider=lambda: CID)
    if hook is not None:
        svc.set_po_state_refresh(hook)
    return svc


def _apply(get_conn, ev, hook=None, sink=None):
    conn = get_conn()
    try:
        ok = _svc(get_conn, hook)._apply_event(
            conn, ev, local_company_id=CID, conflict_sink=sink)
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
         "ap_basis": "receipt", "company_id": "the-senders-company"}
    p.update(over)
    return {"entity_type": "purchase_order", "event_type": "create",
            "entity_id": p["uid"], "payload": p}


def _item(uid, product, qty, po_uid=PO_UID):
    p = {"uid": uid, "po_uid": po_uid, "product_id": product, "quantity": qty,
         "unit_cost": 10.0, "total": qty * 10.0, "received_qty": 0}
    return {"entity_type": "purchase_order_item", "event_type": "create",
            "entity_id": uid, "payload": p}


def _receipt(uid=None, po_uid=PO_UID, value=60.0, line_count=1,
             received_at="2026-10-07 10:00:00", **over):
    uid = uid or str(uuid.uuid4())
    p = {"uid": uid, "po_uid": po_uid, "branch_uid": str(uuid.uuid4()),
         "received_at": received_at, "value": value, "line_count": line_count,
         "created_by": "x", "actor_user_uid": None, "terminal_id": "T1",
         "created_at_utc": "2026-10-07T10:00:00Z",
         "company_id": "the-senders-company"}
    p.update(over)
    return {"entity_type": "purchase_order_receipt", "event_type": "create",
            "entity_id": uid, "payload": p}


def _rline(receipt_uid, item_uid, qty, uid=None):
    uid = uid or str(uuid.uuid4())
    p = {"uid": uid, "receipt_uid": receipt_uid, "po_item_uid": item_uid,
         "quantity": qty, "movement_uid": str(uuid.uuid4())}
    return {"entity_type": "purchase_order_receipt_line", "event_type": "create",
            "entity_id": uid, "payload": p}


def _hdr(**fields):
    p = {"uid": PO_UID}
    p.update(fields)
    return {"entity_type": "purchase_order", "event_type": "update",
            "entity_id": PO_UID, "payload": p}


def _po_row(get_conn, uid=PO_UID):
    r = _rows(get_conn, "SELECT * FROM purchase_orders WHERE uid=?", (uid,))
    return r[0] if r else None


def _parked(get_conn):
    return sorted(r["reason"] for r in _rows(
        get_conn, "SELECT reason FROM sync_apply_quarantine"))


def _setup_order(get_conn, qty_a=10.0, **po_over):
    """An order with one line of `qty_a` (default 10 at cost 10 = total 100)."""
    assert _apply(get_conn, _po(**po_over)) is True
    assert _apply(get_conn, _item(ITEM_A, PRODUCT, qty_a)) is True


def _deliver(get_conn, qty, item=ITEM_A, value=None, **kw):
    r = _receipt(value=qty * 10.0 if value is None else value, **kw)
    assert _apply(get_conn, r) is True
    assert _apply(get_conn, _rline(r["payload"]["uid"], item, qty)) is True
    return r["payload"]["uid"]


# -- wire registration -------------------------------------------------------

def test_receipt_types_are_registered_and_line_has_no_company_column():
    assert "purchase_order_receipt" in _ss.RETAIL_SYNC_ENTITY_TYPES
    assert "purchase_order_receipt_line" in _ss.RETAIL_SYNC_ENTITY_TYPES
    # The line resolves its parent by uid and carries no company column, so the
    # eager company fetch must not be demanded for a batch of only lines.
    assert "purchase_order_receipt_line" in _ss._ENTITY_TYPES_WITHOUT_COMPANY_ID
    assert "purchase_order_receipt" not in _ss._ENTITY_TYPES_WITHOUT_COMPANY_ID


def test_legacy_namespace_literal_matches_the_core_module():
    """SS cannot import products/, so it carries the W4a namespace literal; if
    it drifted from core, a v45 'received' event would synthesize a legacy
    receipt under a uid no other computer derives."""
    from core.retail import purchase_orders as po_mod
    assert _ss._PO_RECEIPT_NAMESPACE == po_mod._PO_RECEIPT_NAMESPACE


# -- create: ap_basis and set-once stamps ------------------------------------

def test_create_stores_ap_basis_and_absent_means_order(get_conn):
    assert _apply(get_conn, _po()) is True
    assert _po_row(get_conn)["ap_basis"] == "receipt"
    other = str(uuid.uuid4())
    ev = _po(uid=other, po_number="PO-0002")
    del ev["payload"]["ap_basis"]          # a v45 writer's create
    assert _apply(get_conn, ev) is True
    assert _po_row(get_conn, other)["ap_basis"] == "order", \
        "a payload without ap_basis is a pre-v46 order: its history is never re-booked"


def test_create_stores_cancelled_and_closed_stamps_when_present(get_conn):
    ev = _po(cancelled_at="2026-10-06 11:00:00", closed_at="2026-10-06 12:00:00")
    assert _apply(get_conn, ev) is True
    row = _po_row(get_conn)
    assert (row["cancelled_at"], row["closed_at"]) == ("2026-10-06 11:00:00", "2026-10-06 12:00:00")


# -- receipt header ----------------------------------------------------------

def test_receipt_before_its_order_parks_then_replays(get_conn):
    r = _receipt()
    assert _apply(get_conn, r) is False
    assert _parked(get_conn) == ["missing_parent:purchase_order"]
    _apply(get_conn, _po())
    _retry(get_conn)
    assert _parked(get_conn) == []
    rows = _rows(get_conn, "SELECT * FROM purchase_order_receipts")
    assert len(rows) == 1 and rows[0]["uid"] == r["payload"]["uid"]


def test_receipt_lands_under_the_receivers_company_and_is_not_legacy(get_conn):
    _setup_order(get_conn)
    r = _receipt(value=60.0, line_count=1)
    assert _apply(get_conn, r) is True
    row = _rows(get_conn, "SELECT * FROM purchase_order_receipts")[0]
    assert row["company_id"] == CID, "tenant key must be the receiver's, never the payload's"
    assert row["po_id"] == _po_row(get_conn)["id"]
    assert row["value"] == 60.0 and row["line_count"] == 1
    assert row["legacy"] == 0 and row["received_at"] == "2026-10-07 10:00:00"
    assert row["branch_id"] is not None, "branch_uid falls back to this computer's own branch"


def test_receipt_for_another_companys_order_is_not_resolved(get_conn):
    """W4a verify F4 shape: the parent lookup is company-scoped."""
    _setup_order(get_conn)
    _exec(get_conn, "UPDATE purchase_orders SET company_id='someone-else'")
    assert _apply(get_conn, _receipt()) is False
    assert _parked(get_conn) == ["missing_parent:purchase_order"]
    assert _rows(get_conn, "SELECT id FROM purchase_order_receipts") == []


def test_identical_receipt_twice_is_one_row(get_conn):
    _setup_order(get_conn)
    r = _receipt()
    _apply(get_conn, r)
    _apply(get_conn, r)
    assert len(_rows(get_conn, "SELECT id FROM purchase_order_receipts")) == 1


# -- receipt line ------------------------------------------------------------

def test_line_before_its_receipt_parks_then_replays(get_conn):
    _setup_order(get_conn)
    r = _receipt()
    ln = _rline(r["payload"]["uid"], ITEM_A, 6.0)
    assert _apply(get_conn, ln) is False
    assert _parked(get_conn) == ["missing_parent:purchase_order_receipt"]
    _apply(get_conn, r)
    _retry(get_conn)
    assert _parked(get_conn) == []
    rows = _rows(get_conn, "SELECT * FROM purchase_order_receipt_lines")
    assert len(rows) == 1 and rows[0]["quantity"] == 6.0
    assert rows[0]["movement_uid"] == ln["payload"]["movement_uid"]


def test_line_with_unknown_item_parks(get_conn):
    _setup_order(get_conn)
    r = _receipt()
    _apply(get_conn, r)
    assert _apply(get_conn, _rline(r["payload"]["uid"], str(uuid.uuid4()), 1.0)) is False
    assert _parked(get_conn) == ["missing_parent:purchase_order_item"]


def test_line_replayed_is_one_row(get_conn):
    _setup_order(get_conn)
    r = _receipt()
    _apply(get_conn, r)
    ln = _rline(r["payload"]["uid"], ITEM_A, 6.0)
    _apply(get_conn, ln)
    _apply(get_conn, ln)
    assert len(_rows(get_conn, "SELECT id FROM purchase_order_receipt_lines")) == 1


def test_line_resolves_receipt_inside_the_receivers_company(get_conn):
    _setup_order(get_conn)
    r = _receipt()
    _apply(get_conn, r)
    _exec(get_conn, "UPDATE purchase_order_receipts SET company_id='someone-else'")
    assert _apply(get_conn, _rline(r["payload"]["uid"], ITEM_A, 6.0)) is False
    assert _parked(get_conn) == ["missing_parent:purchase_order_receipt"]


def test_receipt_apply_never_moves_stock_or_posts_books(get_conn):
    _setup_order(get_conn)
    q = ("SELECT (SELECT COUNT(*) FROM inventory_movements) AS m, "
         "(SELECT COUNT(*) FROM gl_journals) AS j")
    before = _rows(get_conn, q)[0]
    _deliver(get_conn, 6.0)
    assert _rows(get_conn, q)[0] == before, \
        "movements and journals sync as their own entities; applying here too would double-count"


# -- derived state (SS fallback when no hook is installed) -------------------

def test_state_derives_partial_then_received_from_receipts(get_conn):
    _setup_order(get_conn)
    assert _po_row(get_conn)["status"] == "pending"
    _deliver(get_conn, 6.0, received_at="2026-10-07 10:00:00")
    row = _po_row(get_conn)
    assert row["status"] == "partial" and row["received_at"] is None
    assert _rows(get_conn, "SELECT received_qty FROM purchase_order_items")[0]["received_qty"] == 6.0
    _deliver(get_conn, 4.0, received_at="2026-10-08 10:00:00")
    row = _po_row(get_conn)
    assert row["status"] == "received" and row["received_at"] == "2026-10-08 10:00:00"
    assert _rows(get_conn, "SELECT received_qty FROM purchase_order_items")[0]["received_qty"] == 10.0


def test_state_follows_the_set_once_stamps(get_conn):
    _setup_order(get_conn)
    _deliver(get_conn, 6.0)
    _apply(get_conn, _hdr(closed_at="2026-10-09 09:00:00", cancelled_at=None))
    assert _po_row(get_conn)["status"] == "closed"
    # No receipts + cancelled_at -> cancelled; closed_at alone with no receipts
    # cannot be produced by the routes and is treated as cancelled.
    other = str(uuid.uuid4())
    _apply(get_conn, _po(uid=other, po_number="PO-0002"))
    _apply(get_conn, {"entity_type": "purchase_order", "event_type": "update", "entity_id": other,
                      "payload": {"uid": other, "cancelled_at": "2026-10-09 09:00:00",
                                  "closed_at": None}})
    assert _po_row(get_conn, other)["status"] == "cancelled"


def test_fully_received_order_stays_received_even_if_also_closed(get_conn):
    _setup_order(get_conn)
    _deliver(get_conn, 10.0)
    _apply(get_conn, _hdr(closed_at="2026-10-09 09:00:00"))
    assert _po_row(get_conn)["status"] == "received"


# -- over-receipt ------------------------------------------------------------

def test_over_receipt_applies_both_and_records_one_conflict(get_conn):
    _setup_order(get_conn)
    sink = []
    # Two computers each recorded a DIFFERENT 6-unit delivery offline: 12 > 10.
    r1 = _receipt(value=60.0)
    r2 = _receipt(value=60.0, received_at="2026-10-07 11:00:00")
    for r in (r1, r2):
        _apply(get_conn, r, sink=sink)
        _apply(get_conn, _rline(r["payload"]["uid"], ITEM_A, 6.0), sink=sink)
    assert len(_rows(get_conn, "SELECT id FROM purchase_order_receipts")) == 2, \
        "both deliveries are facts and are kept"
    assert _po_row(get_conn)["status"] == "received"
    conflicts = _rows(get_conn, "SELECT * FROM sync_conflicts WHERE entity_type='purchase_order'")
    assert len(conflicts) == 1 and "po_over_received" in conflicts[0]["incoming_payload"]
    assert conflicts[0]["entity_id"] == PO_UID
    # A redelivery of either event must not add a second row.
    _apply(get_conn, _rline(r2["payload"]["uid"], ITEM_A, 6.0,
                            uid=_rows(get_conn, "SELECT uid FROM purchase_order_receipt_lines")[1]["uid"]),
           sink=sink)
    assert len(_rows(get_conn, "SELECT id FROM sync_conflicts WHERE entity_type='purchase_order'")) == 1


def test_exact_receipt_records_no_conflict(get_conn):
    _setup_order(get_conn)
    _deliver(get_conn, 6.0)
    _deliver(get_conn, 4.0)
    assert _rows(get_conn, "SELECT id FROM sync_conflicts") == []


# -- header update: set-once stamps and the v45 compat payload ---------------

def test_header_update_sets_stamps_once_earliest_wins(get_conn):
    _setup_order(get_conn)
    _apply(get_conn, _hdr(cancelled_at="2026-10-09 10:00:00", closed_at=None))
    _apply(get_conn, _hdr(cancelled_at="2026-10-09 12:00:00"))   # later: ignored
    assert _po_row(get_conn)["cancelled_at"] == "2026-10-09 10:00:00"
    _apply(get_conn, _hdr(cancelled_at="2026-10-09 08:00:00"))   # earlier: wins
    assert _po_row(get_conn)["cancelled_at"] == "2026-10-09 08:00:00"
    _apply(get_conn, _hdr(cancelled_at=None))                    # null never erases
    assert _po_row(get_conn)["cancelled_at"] == "2026-10-09 08:00:00"


def test_header_update_for_unknown_order_parks(get_conn):
    assert _apply(get_conn, _hdr(cancelled_at="x")) is False
    assert _parked(get_conn) == ["missing_parent:purchase_order"]


def test_v45_cancelled_status_payload_becomes_a_cancelled_at_stamp(get_conn):
    _setup_order(get_conn)
    _apply(get_conn, _hdr(status="cancelled", received_at=None))
    row = _po_row(get_conn)
    assert row["cancelled_at"] == "2026-10-06 09:00:00", \
        "falls back to the order's own created_at when the payload carries no time"
    assert row["status"] == "cancelled"
    other = str(uuid.uuid4())
    _apply(get_conn, _po(uid=other, po_number="PO-0002"))
    _apply(get_conn, {"entity_type": "purchase_order", "event_type": "update", "entity_id": other,
                      "payload": {"uid": other, "status": "cancelled",
                                  "received_at": "2026-10-06 15:00:00"}})
    assert _po_row(get_conn, other)["cancelled_at"] == "2026-10-06 15:00:00"


def test_v45_received_status_payload_synthesizes_the_legacy_receipt(get_conn):
    from core.retail import purchase_orders as po_mod
    _apply(get_conn, _po(ap_basis="order"))
    _apply(get_conn, _item(ITEM_A, PRODUCT, 4.0))
    _apply(get_conn, _item(ITEM_B, PRODUCT_B, 6.0))
    _apply(get_conn, _hdr(status="received", received_at="2026-10-06 12:00:00"))
    receipts = _rows(get_conn, "SELECT * FROM purchase_order_receipts")
    assert len(receipts) == 1
    leg = receipts[0]
    assert leg["uid"] == str(uuid.uuid5(po_mod._PO_RECEIPT_NAMESPACE, f"{PO_UID}:legacy-receipt"))
    assert leg["legacy"] == 1 and leg["value"] == 100.0
    assert leg["received_at"] == "2026-10-06 12:00:00"
    lines = {r["po_item_id"]: r for r in _rows(
        get_conn, "SELECT l.* FROM purchase_order_receipt_lines l")}
    assert len(lines) == 2
    for item_uid, qty in ((ITEM_A, 4.0), (ITEM_B, 6.0)):
        ln = [r for r in lines.values() if r["uid"] == str(uuid.uuid5(
            po_mod._PO_RECEIPT_NAMESPACE, f"{leg['uid']}:{item_uid}"))]
        assert len(ln) == 1 and ln[0]["quantity"] == qty
        assert ln[0]["movement_uid"] == po_mod.receipt_movement_uid(PO_UID, item_uid)
    assert _po_row(get_conn)["status"] == "received"
    # Idempotent: the legacy receipt is never duplicated.
    _apply(get_conn, _hdr(status="received", received_at="2026-10-06 12:00:00"))
    assert len(_rows(get_conn, "SELECT id FROM purchase_order_receipts")) == 1


def test_v45_received_status_is_ignored_when_real_receipts_exist(get_conn):
    _setup_order(get_conn)
    _deliver(get_conn, 6.0)
    _apply(get_conn, _hdr(status="received", received_at="2026-10-06 12:00:00"))
    assert len(_rows(get_conn, "SELECT id FROM purchase_order_receipts")) == 1
    assert _po_row(get_conn)["status"] == "partial", "facts win over a stale status claim"


def test_create_payload_status_received_synthesizes_the_legacy_receipt(get_conn):
    """W4a-2 fix (F2): the boot backfill announces a legacy RECEIVED order as a
    create carrying status='received' and ap_basis='order' (the key is always
    sent). The receiver must end with the order 'received' and received_qty
    filled -- items arrive AFTER the header, so the lines join the legacy
    receipt as they land."""
    from core.retail import purchase_orders as po_mod
    assert _apply(get_conn, _po(ap_basis="order", status="received",
                                received_at="2026-10-06 12:00:00")) is True
    assert _apply(get_conn, _item(ITEM_A, PRODUCT, 4.0)) is True
    assert _apply(get_conn, _item(ITEM_B, PRODUCT_B, 6.0)) is True
    receipts = _rows(get_conn, "SELECT * FROM purchase_order_receipts")
    assert len(receipts) == 1 and receipts[0]["legacy"] == 1
    assert receipts[0]["uid"] == str(uuid.uuid5(po_mod._PO_RECEIPT_NAMESPACE, f"{PO_UID}:legacy-receipt"))
    row = _po_row(get_conn)
    assert row["status"] == "received" and row["received_at"] == "2026-10-06 12:00:00"
    got = sorted(r["received_qty"] for r in _rows(
        get_conn, "SELECT received_qty FROM purchase_order_items"))
    assert got == [4.0, 6.0]
    # Replay of the create is idempotent: still one legacy receipt, two lines.
    assert _apply(get_conn, _po(ap_basis="order", status="received",
                                received_at="2026-10-06 12:00:00")) is True
    assert len(_rows(get_conn, "SELECT id FROM purchase_order_receipts")) == 1
    assert len(_rows(get_conn, "SELECT id FROM purchase_order_receipt_lines")) == 2


def test_create_payload_status_received_on_a_receipt_basis_order_synthesizes_nothing(get_conn):
    """A 'receipt'-basis order's deliveries arrive as real receipt events; a
    synthesized legacy receipt beside them would count the goods twice."""
    assert _apply(get_conn, _po(ap_basis="receipt", status="received")) is True
    assert _apply(get_conn, _item(ITEM_A, PRODUCT, 10.0)) is True
    assert _rows(get_conn, "SELECT id FROM purchase_order_receipts") == []
    assert _po_row(get_conn)["status"] == "pending"


def test_create_payload_status_cancelled_stamps_cancelled_at(get_conn):
    """W4a-2 fix (F2): a backfilled cancelled order arrives cancelled."""
    assert _apply(get_conn, _po(ap_basis="order", status="cancelled",
                                received_at="2026-10-06 15:00:00")) is True
    row = _po_row(get_conn)
    assert row["cancelled_at"] == "2026-10-06 15:00:00" and row["status"] == "cancelled"
    other = str(uuid.uuid4())
    assert _apply(get_conn, _po(uid=other, po_number="PO-0002", ap_basis="order",
                                status="cancelled")) is True
    assert _po_row(get_conn, other)["cancelled_at"] == "2026-10-06 09:00:00", \
        "no received_at -> falls back to the order's own created_at"


def test_a_line_arriving_after_the_legacy_receipt_joins_it(get_conn):
    """The old apply set received_qty=quantity for a late line under a received
    header; state is derived now, so the late line must get its legacy receipt
    line instead -- otherwise the order would read 'partial' forever."""
    _apply(get_conn, _po(ap_basis="order"))
    _apply(get_conn, _item(ITEM_A, PRODUCT, 4.0))
    _apply(get_conn, _hdr(status="received", received_at="2026-10-06 12:00:00"))
    _apply(get_conn, _item(ITEM_B, PRODUCT_B, 6.0))
    assert len(_rows(get_conn, "SELECT id FROM purchase_order_receipt_lines")) == 2
    assert _po_row(get_conn)["status"] == "received"


# -- the D13 hook ------------------------------------------------------------

def test_hook_is_called_with_company_and_local_po_id_after_each_relevant_apply(get_conn):
    calls = []

    def hook(conn, company_id, po_id):
        calls.append((company_id, po_id))

    _apply(get_conn, _po(), hook=hook)
    po_id = _po_row(get_conn)["id"]
    assert calls == [(CID, po_id)], "order create"
    _apply(get_conn, _item(ITEM_A, PRODUCT, 10.0), hook=hook)
    r = _receipt()
    n = len(calls)
    _apply(get_conn, r, hook=hook)
    assert calls[n:] == [(CID, po_id)], "receipt header"
    n = len(calls)
    _apply(get_conn, _rline(r["payload"]["uid"], ITEM_A, 6.0), hook=hook)
    assert calls[n:] == [(CID, po_id)], "receipt line"
    n = len(calls)
    _apply(get_conn, _hdr(closed_at="2026-10-09 09:00:00"), hook=hook)
    assert calls[n:] == [(CID, po_id)], "header update"
    n = len(calls)
    pay = {"entity_type": "payment", "event_type": "create", "entity_id": "p1", "payload": {
        "uid": str(uuid.uuid4()), "reference": "R", "party_type": "supplier", "party_id": SUPPLIER,
        "direction": "out", "amount": 5.0, "currency": "USD", "method": "cash",
        "related_type": "po", "related_id": 1, "po_uid": PO_UID, "notes": "", "status": "active",
        "created_by": "x", "device": "A", "created_at": "2026-10-06 10:00:00"}}
    _apply(get_conn, pay, hook=hook)
    assert calls[n:] == [(CID, po_id)], "po payment"


def test_hook_is_not_called_for_a_parked_event(get_conn):
    calls = []
    _apply(get_conn, _receipt(), hook=lambda c, cid, po: calls.append(po))
    assert calls == []


def test_installed_hook_replaces_the_built_in_derivation(get_conn):
    """With the hook installed the retail core owns derivation (one
    implementation, not two that drift): the built-in twin must stand down."""
    _setup_order(get_conn)
    r = _receipt()
    _apply(get_conn, r, hook=lambda conn, cid, po: None)
    _apply(get_conn, _rline(r["payload"]["uid"], ITEM_A, 6.0), hook=lambda conn, cid, po: None)
    assert _po_row(get_conn)["status"] == "pending", "a no-op hook leaves state untouched"


def test_real_core_derivation_agrees_with_the_built_in_twin(get_conn):
    """The twin is the fallback for an instance with no hook; the core function
    is what the shop actually runs. They must give the same state."""
    from core.retail import purchase_orders as po_mod
    _setup_order(get_conn)
    _deliver(get_conn, 6.0)
    twin = _po_row(get_conn)
    conn = get_conn()
    try:
        po_mod.recompute_po_receive_state(conn, CID, twin["id"])
        conn.commit()
    finally:
        conn.close()
    real = _po_row(get_conn)
    assert (real["status"], real["received_at"]) == (twin["status"], twin["received_at"])
    assert _rows(get_conn, "SELECT received_qty FROM purchase_order_items")[0]["received_qty"] == 6.0
