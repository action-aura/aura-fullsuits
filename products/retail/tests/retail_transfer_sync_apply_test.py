"""W3b (schema v47) on the sync APPLY side: stock transfers shared between computers.

Before W3b a transfer's stock movements and their 1250/5910 journals synced, but
the `stock_transfers` header and its lines never did -- so a transfer sent from
computer A could not be received on computer B. This file pins what the
RECEIVING side must do with the four new events:

  stock_transfer       create  {id, source_branch_uid, destination_branch_uid, created_by, created_at, item_count}
  stock_transfer       update  {id, status, sent_at, received_at, cancelled_at, recalled_at}
  stock_transfer_item  create  {id, transfer_id, product_id, quantity_sent}
  stock_transfer_item  update  {id, unit_cost, quantity_received}

  * create the header under the RECEIVER's company and branches, once;
  * park -- never raise, never drop -- a line/update whose parent has not arrived;
  * merge the state FORWARD ONLY by RANK of the (status, recalled_at) pair
    (pending < cancelled-before-send < in_transit < recalled < received), with
    timestamps set-once (earliest wins), and record -- ONCE per transfer -- the
    two clashes a person must look at;
  * merge a line's unit_cost / quantity_received set-once, flagging a differing
    second value;
  * NEVER move stock or post books: stock moves only through inventory_movement
    events, books only through gl_journal events.

Same fixture technique as retail_po_sync_apply_test.py: a REAL `init_retail()`
database, `SyncService._apply_event` driven directly. One file per process.

Run:  <venv python> -m pytest -q -p no:cacheprovider -p no:warnings products/retail/tests/retail_transfer_sync_apply_test.py
"""
from __future__ import annotations

import itertools
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

for _k in ("AURA_SYNC_RELAY_URL", "AURA_OWNER_LICENSING_URL"):
    os.environ.pop(_k, None)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RETAIL_BACKEND_DIR = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_RETAIL_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import database.schema as schema  # noqa: E402
import api.retail_api as _retail_api_module  # noqa: E402
from api.retail_api import _ensure_credit_schema  # noqa: E402
from commercial_runtime.identity import registry_db  # noqa: E402
from commercial_runtime.sync import sync_service as ss  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402
from core.retail import stock_transfers as st_core  # noqa: E402

CID = "company-transfer-apply-test"
TID = str(uuid.uuid4())
ITEM1 = str(uuid.uuid4())
ITEM2 = str(uuid.uuid4())
PRODUCT1 = str(uuid.uuid4())
PRODUCT2 = str(uuid.uuid4())
SRC_UID = str(uuid.uuid4())
DST_UID = str(uuid.uuid4())
T1 = "2026-10-07 09:00:00"
T2 = "2026-10-07 10:00:00"
T3 = "2026-10-07 11:00:00"


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
            c.execute("INSERT INTO products (id, company_id, sku, name) VALUES (?,?,?,?)",
                      (PRODUCT1, CID, "SKU-1", "Widget"))
            c.execute("INSERT INTO products (id, company_id, sku, name) VALUES (?,?,?,?)",
                      (PRODUCT2, CID, "SKU-2", "Gadget"))
            # Two branches whose uids the events name: the apply must resolve
            # them to THIS computer's own integer ids.
            c.execute("INSERT INTO branches (company_id, name, uid) VALUES (?,?,?)",
                      (CID, "Source", SRC_UID))
            c.execute("INSERT INTO branches (company_id, name, uid) VALUES (?,?,?)",
                      (CID, "Dest", DST_UID))
            c.commit()
        finally:
            c.close()
    finally:
        _retail_api_module._CREDIT_SCHEMA_READY = previous
    return _get


def _svc(get_conn):
    return SyncService(client_factory=lambda: None, get_conn=get_conn,
                       local_company_id_provider=lambda: CID)


def _apply(get_conn, ev, sink=None):
    conn = get_conn()
    try:
        ok = _svc(get_conn)._apply_event(conn, ev, local_company_id=CID, conflict_sink=sink)
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


def _branch_id(get_conn, uid):
    return _rows(get_conn, "SELECT id FROM branches WHERE uid=?", (uid,))[0]["id"]


# ── event builders (the exact D3 key sets) ───────────────────────────────────

def _create(tid=TID, **over):
    p = {"id": tid, "source_branch_uid": SRC_UID, "destination_branch_uid": DST_UID,
         "created_by": "alice", "created_at": T1, "item_count": 2,
         # A hostile/buggy sender's tenant key must never be trusted.
         "company_id": "the-senders-company"}
    p.update(over)
    return {"entity_type": "stock_transfer", "event_type": "create", "entity_id": tid, "payload": p}


def _update(tid=TID, status="in_transit", sent_at=None, received_at=None,
            cancelled_at=None, recalled_at=None):
    return {"entity_type": "stock_transfer", "event_type": "update", "entity_id": tid,
            "payload": {"id": tid, "status": status, "sent_at": sent_at,
                        "received_at": received_at, "cancelled_at": cancelled_at,
                        "recalled_at": recalled_at}}


def _item(item_id=ITEM1, tid=TID, product_id=PRODUCT1, qty=5.0):
    return {"entity_type": "stock_transfer_item", "event_type": "create", "entity_id": item_id,
            "payload": {"id": item_id, "transfer_id": tid, "product_id": product_id,
                        "quantity_sent": qty}}


def _item_update(item_id=ITEM1, unit_cost=None, quantity_received=None):
    return {"entity_type": "stock_transfer_item", "event_type": "update", "entity_id": item_id,
            "payload": {"id": item_id, "unit_cost": unit_cost,
                        "quantity_received": quantity_received}}


# The five merge states, as the (status, stamps) a header update carries.
STATES = {
    "pending": dict(status="pending"),
    "cancelled_pre": dict(status="cancelled", cancelled_at=T1),
    "in_transit": dict(status="in_transit", sent_at=T1),
    "recalled": dict(status="cancelled", sent_at=T1, cancelled_at=T2, recalled_at=T2),
    "received": dict(status="received", sent_at=T1, received_at=T2),
}
RANK = {"pending": 0, "cancelled_pre": 1, "in_transit": 2, "recalled": 3, "received": 4}


def _set_local_state(get_conn, state, tid=TID):
    s = dict(STATES[state])
    _exec(get_conn,
          "UPDATE stock_transfers SET status=?, sent_at=?, received_at=?, cancelled_at=?, "
          "recalled_at=? WHERE id=?",
          (s["status"], s.get("sent_at"), s.get("received_at"), s.get("cancelled_at"),
           s.get("recalled_at"), tid))


def _header(get_conn, tid=TID):
    r = _rows(get_conn, "SELECT * FROM stock_transfers WHERE id=?", (tid,))
    return r[0] if r else None


def _conflicts(get_conn, tid=TID):
    return sorted(json.loads(r["incoming_payload"])["reason"] for r in _rows(
        get_conn, "SELECT incoming_payload FROM sync_conflicts WHERE entity_type='stock_transfer' "
                  "AND entity_id=?", (tid,)))


def _parked(get_conn):
    return {(r["entity_type"], r["event_type"], r["reason"]) for r in _rows(
        get_conn, "SELECT entity_type, event_type, reason FROM sync_apply_quarantine")}


# ── header create ────────────────────────────────────────────────────────────

def test_create_lands_under_the_receivers_company_and_resolved_branches(get_conn):
    assert _apply(get_conn, _create()) is True
    row = _header(get_conn)
    assert row is not None
    assert row["company_id"] == CID, "tenant key must be the receiver's, never the payload's"
    assert row["source_branch_id"] == _branch_id(get_conn, SRC_UID)
    assert row["destination_branch_id"] == _branch_id(get_conn, DST_UID)
    assert row["status"] == "pending"
    assert row["created_by"] == "alice" and row["created_at"] == T1
    assert row["item_count"] == 2
    assert row["sync_backfill_pending"] == 0, "a receiver must never re-queue what it pulled"


def test_create_replayed_is_a_noop(get_conn):
    _apply(get_conn, _create())
    _set_local_state(get_conn, "in_transit")
    _apply(get_conn, _create())
    assert len(_rows(get_conn, "SELECT id FROM stock_transfers")) == 1
    assert _header(get_conn)["status"] == "in_transit", "a replayed create must not rewind state"


def test_create_without_item_count_stores_null_not_zero(get_conn):
    """0 would read as 'a transfer with no lines'; NULL means 'the writer did not
    say' and leaves the receive guard with nothing to compare against."""
    ev = _create()
    del ev["payload"]["item_count"]
    assert _apply(get_conn, ev) is True
    assert _header(get_conn)["item_count"] is None


# ── lines: park / replay / fields ────────────────────────────────────────────

def test_line_before_header_parks_then_replays_when_the_header_arrives(get_conn):
    assert _apply(get_conn, _item()) is False
    assert ("stock_transfer_item", "create", "missing_parent:stock_transfer") in _parked(get_conn)
    assert _rows(get_conn, "SELECT id FROM stock_transfer_items") == []
    _apply(get_conn, _create())
    _retry(get_conn)
    rows = _rows(get_conn, "SELECT * FROM stock_transfer_items")
    assert [r["id"] for r in rows] == [ITEM1]
    assert rows[0]["transfer_id"] == TID and rows[0]["quantity_sent"] == 5.0
    assert ("stock_transfer_item", "create", "missing_parent:stock_transfer") not in _parked(get_conn)


def test_line_arrives_unreconciled_cost_and_quantity_are_null(get_conn):
    """unit_cost has a column DEFAULT 0, which would make a line that has not
    been sent look like a free-goods line. The receive guard
    (TRANSFER_SEND_NOT_ARRIVED) reads NULL as 'the send has not arrived', so
    the apply must insert NULL explicitly."""
    _apply(get_conn, _create())
    assert _apply(get_conn, _item()) is True
    row = _rows(get_conn, "SELECT * FROM stock_transfer_items WHERE id=?", (ITEM1,))[0]
    assert row["unit_cost"] is None
    assert row["quantity_received"] is None


def test_line_with_unknown_product_parks_instead_of_raising(get_conn):
    _apply(get_conn, _create())
    assert _apply(get_conn, _item(product_id=str(uuid.uuid4()))) is False
    assert ("stock_transfer_item", "create", "missing_parent:product") in _parked(get_conn)
    assert _rows(get_conn, "SELECT id FROM stock_transfer_items") == []


def test_line_create_replayed_is_a_noop_and_never_overwrites_set_once_fields(get_conn):
    _apply(get_conn, _create())
    _apply(get_conn, _item())
    _apply(get_conn, _item_update(unit_cost=3.5))
    _apply(get_conn, _item())
    row = _rows(get_conn, "SELECT * FROM stock_transfer_items")[0]
    assert len(_rows(get_conn, "SELECT id FROM stock_transfer_items")) == 1
    assert row["unit_cost"] == 3.5


def test_line_cannot_attach_to_another_companys_transfer(get_conn):
    _apply(get_conn, _create())
    _exec(get_conn, "UPDATE stock_transfers SET company_id='someone-else' WHERE id=?", (TID,))
    assert _apply(get_conn, _item()) is False
    assert _rows(get_conn, "SELECT id FROM stock_transfer_items") == []


# ── header update: park + rank merge ─────────────────────────────────────────

def test_update_before_create_parks_then_replays(get_conn):
    assert _apply(get_conn, _update(status="in_transit", sent_at=T1)) is False
    assert ("stock_transfer", "update", "missing_parent:stock_transfer") in _parked(get_conn)
    _apply(get_conn, _create())
    _retry(get_conn)
    row = _header(get_conn)
    assert row["status"] == "in_transit" and row["sent_at"] == T1


@pytest.mark.parametrize("local,incoming", list(itertools.permutations(STATES, 2)))
def test_rank_merge_is_forward_only_for_every_ordered_pair(get_conn, local, incoming):
    """The state after a merge is the higher-ranked of the two, whichever order
    they arrive in -- this is what makes two computers converge."""
    _apply(get_conn, _create())
    _set_local_state(get_conn, local)
    _apply(get_conn, _update(**STATES[incoming]))
    winner = local if RANK[local] > RANK[incoming] else incoming
    row = _header(get_conn)
    assert row["status"] == STATES[winner]["status"], (local, incoming)
    # recalled vs cancelled-before-send is told apart by recalled_at, so the
    # winner's stamp must be what the row ends up carrying.
    if winner == "recalled":
        assert row["recalled_at"], (local, incoming)
    if winner == "cancelled_pre":
        assert not row["recalled_at"], (local, incoming)


#: W3b verify F3: `transfer_sent_after_cancel` is recorded when a cancel-BEFORE-send
#: meets ANY state that proves the goods left (in transit, recalled, received), in
#: either arrival order. It used to cover only {cancelled_pre, in_transit}, so a
#: cancel that met a transfer already sent AND received elsewhere was recorded on
#: one computer and not the other. What the old pair list could no longer catch:
#: nothing -- it is a subset of the new one.
_SENT_AFTER_CANCEL_PAIRS = [
    (a, b) for a in ("cancelled_pre",) for b in ("in_transit", "recalled", "received")]
_CLASH_PAIRS = (
    [(a, b, "transfer_sent_after_cancel") for a, b in _SENT_AFTER_CANCEL_PAIRS]
    + [(b, a, "transfer_sent_after_cancel") for a, b in _SENT_AFTER_CANCEL_PAIRS]
    + [("received", "recalled", "transfer_received_and_recalled"),
       ("recalled", "received", "transfer_received_and_recalled")])


@pytest.mark.parametrize("local,incoming,reason", _CLASH_PAIRS)
def test_the_clashes_are_recorded_in_either_arrival_order(get_conn, local, incoming, reason):
    _apply(get_conn, _create())
    _set_local_state(get_conn, local)
    _apply(get_conn, _update(**STATES[incoming]))
    assert _conflicts(get_conn) == [reason]
    # The rank rule still decides the status: whatever outranks wins (send beats
    # cancel-before-send; received beats recalled -- stock was moved by BOTH).
    winner = local if RANK[local] > RANK[incoming] else incoming
    assert _header(get_conn)["status"] == STATES[winner]["status"]


@pytest.mark.parametrize("local,incoming", [
    p for p in itertools.permutations(STATES, 2)
    if (p[0], p[1]) not in {(a, b) for a, b, _r in _CLASH_PAIRS}])
def test_every_other_pair_records_no_conflict(get_conn, local, incoming):
    _apply(get_conn, _create())
    _set_local_state(get_conn, local)
    _apply(get_conn, _update(**STATES[incoming]))
    assert _conflicts(get_conn) == [], (local, incoming)


def test_a_conflict_is_recorded_once_even_when_the_event_is_redelivered(get_conn):
    _apply(get_conn, _create())
    _set_local_state(get_conn, "received")
    for _ in range(3):
        _apply(get_conn, _update(**STATES["recalled"]))
    assert _conflicts(get_conn) == ["transfer_received_and_recalled"]


def test_redelivery_of_a_lower_state_is_silent(get_conn):
    """The ordinary case: 'in_transit' arrives again after 'received'. A lower
    rank is simply ignored -- no conflict, no rewind."""
    _apply(get_conn, _create())
    _apply(get_conn, _update(**STATES["received"]))
    _apply(get_conn, _update(**STATES["in_transit"]))
    assert _header(get_conn)["status"] == "received"
    assert _conflicts(get_conn) == []


def test_timestamps_are_set_once_earliest_wins_and_null_never_erases(get_conn):
    _apply(get_conn, _create())
    _apply(get_conn, _update(status="in_transit", sent_at=T2))
    _apply(get_conn, _update(status="in_transit", sent_at=T1))   # earlier wins
    assert _header(get_conn)["sent_at"] == T1
    _apply(get_conn, _update(status="in_transit", sent_at=T3))   # later never replaces
    _apply(get_conn, _update(status="in_transit", sent_at=None))  # null never erases
    assert _header(get_conn)["sent_at"] == T1


def test_unknown_status_is_ignored_not_raised(get_conn):
    """A garbage event must not wedge the batch or out-rank real state."""
    _apply(get_conn, _create())
    assert _apply(get_conn, _update(status="teleported")) is True
    assert _header(get_conn)["status"] == "pending"


# ── lines: unit_cost / quantity_received set-once + the doubles ──────────────

def test_item_update_fills_cost_and_quantity(get_conn):
    _apply(get_conn, _create())
    _apply(get_conn, _item())
    _apply(get_conn, _item_update(unit_cost=2.25))
    _apply(get_conn, _item_update(quantity_received=4.0))
    row = _rows(get_conn, "SELECT * FROM stock_transfer_items")[0]
    assert row["unit_cost"] == 2.25 and row["quantity_received"] == 4.0
    assert _conflicts(get_conn) == []


def test_item_update_before_the_line_parks_then_replays(get_conn):
    _apply(get_conn, _create())
    assert _apply(get_conn, _item_update(unit_cost=2.25)) is False
    assert ("stock_transfer_item", "update", "missing_parent:stock_transfer_item") in _parked(get_conn)
    _apply(get_conn, _item())
    _retry(get_conn)
    assert _rows(get_conn, "SELECT unit_cost FROM stock_transfer_items")[0]["unit_cost"] == 2.25


def test_same_value_twice_is_not_a_conflict_but_a_different_one_is(get_conn):
    _apply(get_conn, _create())
    _apply(get_conn, _item())
    _apply(get_conn, _item_update(unit_cost=2.25, quantity_received=4.0))
    _apply(get_conn, _item_update(unit_cost=2.25, quantity_received=4.0))
    _apply(get_conn, _item_update(unit_cost=2.250, quantity_received=4))   # same numbers
    assert _conflicts(get_conn) == []
    _apply(get_conn, _item_update(unit_cost=9.0))
    _apply(get_conn, _item_update(quantity_received=3.0))
    _apply(get_conn, _item_update(unit_cost=9.0, quantity_received=3.0))   # redelivered
    assert _conflicts(get_conn) == ["transfer_received_twice", "transfer_sent_twice"]
    row = _rows(get_conn, "SELECT * FROM stock_transfer_items")[0]
    # W3b verify F1a UPDATE: the SMALLER value wins (was: the first value stays).
    # Cost 9.0 arrived second and is larger, so 2.25 stays; the quantity 3.0 is
    # smaller than the 4.0 held, so it REPLACES it. What this can no longer
    # catch: "the first value always stays" -- order independence is the point
    # and the next test pins it.
    assert row["unit_cost"] == 2.25 and row["quantity_received"] == 3.0


@pytest.mark.parametrize("first,second", [(2.25, 9.0), (9.0, 2.25)])
def test_two_computers_converge_on_the_smaller_cost_whatever_the_arrival_order(get_conn, first, second):
    """W3b verify F1a: computer A applies (its own 9.0, then B's 2.25) and computer
    B applies (its own 2.25, then A's 9.0). Both must end on 2.25 -- the old
    'first value kept' left each computer holding its own figure for ever.
    The same for the received quantity. Mutation: restore 'keep the local value'
    and the (9.0, 2.25) order stays at 9.0."""
    _apply(get_conn, _create())
    _apply(get_conn, _item())
    _apply(get_conn, _item_update(unit_cost=first, quantity_received=first))
    _apply(get_conn, _item_update(unit_cost=second, quantity_received=second))
    row = _rows(get_conn, "SELECT unit_cost, quantity_received FROM stock_transfer_items")[0]
    assert row["unit_cost"] == 2.25 and row["quantity_received"] == 2.25
    assert _conflicts(get_conn) == ["transfer_received_twice", "transfer_sent_twice"]


def test_the_smaller_tie_break_is_pinned_equal_to_the_retail_core():
    """sync_service cannot import products code, so it carries a twin of
    `core.retail.stock_transfers.smaller`; they must agree on every pair."""
    from core.retail import stock_transfers as st
    from commercial_runtime.sync.sync_service import SyncService
    for a, b in [(5, 2), (2, 5), (None, 3), (3, None), (3, 3), ('4', 5.0), (0, 5), (None, None), (0.1, 0.10)]:
        assert SyncService._transfer_smaller(a, b) == st.smaller(a, b), (a, b)


def test_zero_received_is_a_real_value_not_unset(get_conn):
    """quantity_received 0 means 'arrived with nothing'; a later 5 differs from
    it and must be flagged, not silently adopted."""
    _apply(get_conn, _create())
    _apply(get_conn, _item())
    _apply(get_conn, _item_update(quantity_received=0))
    _apply(get_conn, _item_update(quantity_received=5))
    assert _rows(get_conn, "SELECT quantity_received FROM stock_transfer_items")[0][
        "quantity_received"] == 0
    assert _conflicts(get_conn) == ["transfer_received_twice"]


def test_a_local_default_zero_cost_on_an_unsent_line_does_not_block_the_senders_cost(get_conn):
    """The creating computer's own lines hold unit_cost 0 (the column default)
    until IT sends. If a sibling sends first, the sibling's cost must land on
    the creator -- 0 there is 'not sent yet', not 'free goods' -- or the creator
    would receive the transfer at cost 0.

    W3b fix (F4): this is only true of a transfer that existed at the v47
    upgrade (`pre_v47`), whose pending lines were created with the DEFAULT 0.
    A v47 transfer's lines are created NULL, so the same 0 there is real (see
    the two tests below). What this can no longer catch: a v47 transfer whose
    lines somehow hold a placeholder 0 -- by construction none can."""
    _apply(get_conn, _create(pre_v47=1))
    _apply(get_conn, _item())
    _exec(get_conn, "UPDATE stock_transfer_items SET unit_cost=0 WHERE id=?", (ITEM1,))
    _apply(get_conn, _update(**STATES["in_transit"]))
    _apply(get_conn, _item_update(unit_cost=7.0))
    assert _rows(get_conn, "SELECT unit_cost FROM stock_transfer_items")[0]["unit_cost"] == 7.0
    assert _conflicts(get_conn) == []


def test_create_stores_pre_v47_and_absent_means_zero(get_conn):
    """F3: the flag travels in the create payload; a sender that predates the
    key is treated as a v47 transfer (the strict side: the books guard applies)."""
    _apply(get_conn, _create(pre_v47=1))
    _apply(get_conn, _create(tid=str(uuid.uuid4())))
    flags = sorted(r["pre_v47"] for r in _rows(get_conn, "SELECT pre_v47 FROM stock_transfers"))
    assert flags == [0, 1]


def test_a_genuine_zero_cost_line_survives_the_header_moving_to_in_transit(get_conn):
    """F4: the sender's item update (a REAL cost of 0) reached this computer
    BEFORE the header moved to in_transit (a parked header replayed late). The
    0->NULL placeholder reset used to wipe it, and since set-once item updates
    are never resent the transfer stuck on TRANSFER_SEND_NOT_ARRIVED forever."""
    _apply(get_conn, _create())
    _apply(get_conn, _item())
    _apply(get_conn, _item_update(unit_cost=0))
    assert _rows(get_conn, "SELECT unit_cost FROM stock_transfer_items")[0]["unit_cost"] == 0
    _apply(get_conn, _update(**STATES["in_transit"]))
    assert _rows(get_conn, "SELECT unit_cost FROM stock_transfer_items")[0]["unit_cost"] == 0, (
        "a real zero cost must not be re-NULLed into 'the send has not arrived'")


def test_a_genuine_zero_cost_on_a_v47_transfer_is_not_overwritten_by_a_different_cost(get_conn):
    """F4 allow/deny pair: on a v47 transfer a stored 0 is REAL, so a second,
    different cost is a double send (flagged, first value kept) rather than
    'a placeholder to replace'."""
    _apply(get_conn, _create())
    _apply(get_conn, _item())
    _apply(get_conn, _item_update(unit_cost=0))
    _apply(get_conn, _item_update(unit_cost=7.0))
    assert _rows(get_conn, "SELECT unit_cost FROM stock_transfer_items")[0]["unit_cost"] == 0
    assert _conflicts(get_conn) == ["transfer_sent_twice"]


# ── never moves stock, never posts books ─────────────────────────────────────

def _counts(get_conn):
    return {t: _rows(get_conn, f"SELECT COUNT(*) AS n FROM {t}")[0]["n"] for t in
            ("inventory_movements", "inventory_balances", "gl_journals", "gl_journal_lines")}


def _balances(get_conn):
    return _rows(get_conn, "SELECT product_id, branch_id, quantity_on_hand FROM inventory_balances "
                           "ORDER BY id")


def test_applying_transfers_never_moves_stock_or_posts_books(get_conn):
    """D1: stock moves only through inventory_movement events, books only
    through gl_journal events. If applying a header/line moved stock itself, a
    transfer would count twice (once here, once when the movement event
    arrives)."""
    before = (_counts(get_conn), _balances(get_conn))
    for ev in (_create(), _item(), _item(ITEM2, product_id=PRODUCT2, qty=2.0),
               _update(**STATES["in_transit"]), _item_update(unit_cost=3.0),
               _update(**STATES["received"]), _item_update(quantity_received=5.0),
               _update(**STATES["recalled"])):
        _apply(get_conn, ev)
    assert (_counts(get_conn), _balances(get_conn)) == before


# ── wire plumbing ────────────────────────────────────────────────────────────

def test_the_new_types_are_handled_and_the_line_types_carry_no_company_id():
    assert {"stock_transfer", "stock_transfer_item"} <= ss.RETAIL_SYNC_ENTITY_TYPES
    assert "stock_transfer_item" in ss._ENTITY_TYPES_WITHOUT_COMPANY_ID
    assert "stock_transfer" not in ss._ENTITY_TYPES_WITHOUT_COMPANY_ID


def test_a_pull_batch_applies_header_and_lines_through_apply_pull_result(get_conn):
    """The full path, not just `_apply_event`: the types must be in the handled
    set (otherwise `_apply_event` skips them as 'not mine' and returns True,
    which is exactly how a missing registration looks green)."""
    conn = get_conn()
    try:
        _svc(get_conn).apply_pull_result(conn, {"cursor": 3, "events": [
            _create(), _item(), _update(**STATES["in_transit"])]})
        conn.commit()
    finally:
        conn.close()
    assert _header(get_conn)["status"] == "in_transit"
    assert [r["id"] for r in _rows(get_conn, "SELECT id FROM stock_transfer_items")] == [ITEM1]


def test_a_batch_of_only_line_events_still_resolves_the_receivers_company(get_conn):
    """A line carries no company_id, so a batch of nothing but lines does not
    trigger the eager company fetch -- the line branch must fetch it itself or
    it would look the parent up under company None and park forever."""
    _apply(get_conn, _create())
    conn = get_conn()
    try:
        _svc(get_conn).apply_pull_result(conn, {"cursor": 1, "events": [_item()]})
        conn.commit()
    finally:
        conn.close()
    assert [r["id"] for r in _rows(get_conn, "SELECT id FROM stock_transfer_items")] == [ITEM1]


# ── the duplicated rank rule must equal the core's ───────────────────────────

def test_sync_rank_is_pinned_equal_to_the_core_modules_rank():
    """commercial_runtime cannot import products/, so the rank rule is
    duplicated in sync_service. This pins the two equal over every state --
    drift would make two computers pick different winners."""
    for status, recalled in [("pending", None), ("cancelled", None), ("cancelled", T1),
                             ("in_transit", None), ("received", None), ("received", T1),
                             ("in_transit", T1)]:
        assert ss.transfer_state_rank(status, recalled) == st_core.transfer_rank(status, recalled), (
            status, recalled)
    assert ss.TRANSFER_CONFLICT_REASONS == {
        st_core.CONFLICT_SENT_AFTER_CANCEL, st_core.CONFLICT_RECEIVED_AND_RECALLED,
        st_core.CONFLICT_SENT_TWICE, st_core.CONFLICT_RECEIVED_TWICE}
