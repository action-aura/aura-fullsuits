"""STOCK-TAKE (schema v57) on the sync APPLY side, driven directly through `_apply_event` on a REAL
`init_retail()` database (same technique as retail_transfer_sync_apply_test.py).

  * a count whose branch has not arrived is PARKED (`missing_parent:branch`), never filed under this
    computer's default branch; once the branch exists the replay lands it on the RIGHT one;
  * two OPEN counts of one branch converge on every computer, whichever arrived first: the smaller
    uid wins, the other is 'merged' with `merged_into`, its contributions move under the winner, and
    a LATE line addressed to the loser is redirected to the winner;
  * contributions of different computers are separate rows (they add); one computer's newer value
    replaces only its own; `system_qty_at_count` is set-once; replays change nothing;
  * a posted/cancelled count is immutable: a late line for an existing contribution changes nothing;
  * applying counts NEVER moves stock or posts books.

Run:  <venv python> -m pytest -q -p no:cacheprovider -p no:warnings products/retail/tests/retail_stock_count_sync_apply_test.py
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

CID = "company-stock-count-apply-test"
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



LOW, HIGH = "00000000-0000-4000-8000-000000000001", "ffffffff-0000-4000-8000-000000000002"
BR = str(uuid.uuid4())


def _count(uid, branch_uid=None):
    return {"entity_type": "stock_count", "event_type": "create", "entity_id": uid,
            "payload": {"uid": uid, "branch_uid": branch_uid or SRC_UID, "note": None,
                        "started_by": "u", "started_at": T1}}


def _count_update(uid, status):
    return {"entity_type": "stock_count", "event_type": "update", "entity_id": uid,
            "payload": {"uid": uid, "status": status, "posted_at": T3 if status == "posted" else None,
                        "posted_by": None, "cancelled_at": None, "cancelled_by": None}}


def _line(count_uid, device, qty, at, product=PRODUCT1, system=12.0):
    luid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{count_uid}:{product}:{device}"))
    return {"entity_type": "stock_count_line", "event_type": "create", "entity_id": luid,
            "payload": {"uid": luid, "count_uid": count_uid, "product_id": product, "device_id": device,
                        "counted_qty": qty, "system_qty_at_count": system, "counted_at": at, "counted_by": "u"}}


def _heads(get_conn):
    return {r["uid"]: r for r in _rows(get_conn, "SELECT uid, status, merged_into, branch_id FROM stock_counts")}


def _parts(get_conn, count_uid=None):
    sql = "SELECT count_uid, device_id, counted_qty, system_qty_at_count, variance FROM stock_count_lines"
    rows = _rows(get_conn, sql + " ORDER BY device_id")
    return [r for r in rows if count_uid is None or r["count_uid"] == count_uid]


def _parked(get_conn):
    return {(r["entity_type"], r["event_type"], r["reason"]) for r in _rows(
        get_conn, "SELECT entity_type, event_type, reason FROM sync_apply_quarantine")}


def test_a_count_for_an_unknown_branch_is_parked_not_filed_under_the_default_branch(get_conn):
    _apply(get_conn, _count(LOW, branch_uid=BR))
    assert LOW not in _heads(get_conn), "must not fall back to this computer's default branch"
    assert ("stock_count", "create", "missing_parent:branch") in _parked(get_conn)
    _exec(get_conn, "INSERT INTO branches (company_id, name, uid) VALUES (?,?,?)", (CID, "Late", BR))
    _retry(get_conn)
    assert _heads(get_conn)[LOW]["branch_id"] == _branch_id(get_conn, BR)


@pytest.mark.parametrize("order", [(LOW, HIGH), (HIGH, LOW)])
def test_two_open_counts_of_one_branch_converge_whichever_arrives_first(get_conn, order):
    moves = lambda: _rows(get_conn, "SELECT COUNT(*) AS n FROM inventory_movements")[0]["n"]
    before = moves()
    for uid in order:
        assert _apply(get_conn, _count(uid)) is True
        _apply(get_conn, _line(uid, "devA" if uid == LOW else "devB", 5.0, T1 if uid == LOW else T2))
    h = _heads(get_conn)
    assert h[LOW]["status"] == "open" and h[HIGH]["status"] == "merged" and h[HIGH]["merged_into"] == LOW
    assert [(p["count_uid"], p["device_id"], p["counted_qty"]) for p in _parts(get_conn)] == [
        (LOW, "devA", 5.0), (LOW, "devB", 5.0)]
    # a LATE line addressed to the loser lands on the winner
    _apply(get_conn, _line(HIGH, "devC", 2.0, T3, product=PRODUCT2))
    assert [(p["count_uid"], p["device_id"]) for p in _parts(get_conn) if p["counted_qty"] == 2.0] == [(LOW, "devC")]
    assert moves() == before, "no stock moved"


def test_a_computers_newer_value_replaces_only_its_own_and_replays_change_nothing(get_conn):
    _apply(get_conn, _count(LOW))
    _apply(get_conn, _line(LOW, "devA", 5.0, T1))
    _apply(get_conn, _line(LOW, "devB", 5.0, T1))
    newer = _line(LOW, "devA", 8.0, T2, system=99.0)                 # system figure must stay set-once
    newer["event_type"] = "update"
    _apply(get_conn, newer)
    before = _parts(get_conn)
    _apply(get_conn, newer)
    _apply(get_conn, _line(LOW, "devA", 1.0, T1))                    # an OLDER replay loses
    assert _parts(get_conn) == before
    a, b = before
    assert (a["counted_qty"], a["system_qty_at_count"], a["variance"]) == (8.0, 12.0, -4.0)
    assert (b["counted_qty"], b["system_qty_at_count"]) == (5.0, 12.0)


def test_a_posted_count_is_immutable_and_a_late_line_still_completes_the_record(get_conn):
    _apply(get_conn, _count(LOW))
    _apply(get_conn, _line(LOW, "devA", 5.0, T1))
    _apply(get_conn, _count_update(LOW, "posted"))
    assert _heads(get_conn)[LOW]["status"] == "posted"
    _apply(get_conn, _count_update(LOW, "cancelled"))                  # lower rank: ignored
    assert _heads(get_conn)[LOW]["status"] == "posted"
    late = _line(LOW, "devA", 9.0, T3)
    late["event_type"] = "update"
    _apply(get_conn, late)                                             # existing contribution: frozen
    _apply(get_conn, _line(LOW, "devB", 3.0, T3))                      # missing one: recorded
    assert [(p["device_id"], p["counted_qty"]) for p in _parts(get_conn)] == [("devA", 5.0), ("devB", 3.0)]
