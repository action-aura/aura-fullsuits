"""P2S crew Y, deliverable 1 -- the PARKED-CHILDREN RULE, apply side.

A sale (or return) that is PARKED on this device -- number clash, missing
parent, anything -- must not leave its stock movement or its books journal
applied without it. Before this rule the parent parked while its children
landed: stock moved and revenue posted for a sale this device does not hold.

What is pinned here, one test each, both directions (deny AND allow):

  movement, new payload (`source_type`/`source_uid`):
    parked source                 -> parked   `missing_parent:sale`
    ABSENT source                 -> parked   (emit order puts the sale first)
    present source                -> applied  (allow half)
  movement, legacy payload (no source fields; `reference` = document number):
    parked sale with same (number, terminal) -> parked
    parked sale, SAME number, OTHER terminal -> applied (winner's movement)
    no parked sale at all                    -> applied
  gl_journal:
    source PARKED                 -> parked
    source ABSENT                 -> APPLIED, source_id NULL (v41 behaviour;
                                     the backfill ships journals for sales
                                     this device never held)
    source present                -> applied
  replay: when the parent finally applies, the retry loop applies the
    children, oldest first, and `compute_drift` is 0.

Same fixture technique as `retail_gl_journal_apply_test.py` (a REAL
`init_retail()` database, `SyncService._apply_event` driven directly).
Run:  pytest commercial_runtime/sync/tests/test_parked_children_apply.py -q
"""
from __future__ import annotations

import json
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
from core.retail.general_ledger import ensure_default_chart  # noqa: E402
from core.retail.stock_reconciliation import compute_drift  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402

CID = "company-parked-children"
TERMINAL_A = "terminal-A"
TERMINAL_B = "terminal-B"


@pytest.fixture
def install(tmp_path):
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

    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    previous = _retail_api_module._CREDIT_SCHEMA_READY
    _retail_api_module._CREDIT_SCHEMA_READY = False
    try:
        c = _get_conn()
        try:
            _ensure_credit_schema(c)
            c.commit()
        finally:
            c.close()
    finally:
        _retail_api_module._CREDIT_SCHEMA_READY = previous
    return _get_conn


def _svc(get_conn):
    return SyncService(client_factory=lambda: None, get_conn=get_conn,
                       local_company_id_provider=lambda: CID)


def _seed_product(conn) -> str:
    conn.execute("INSERT INTO branches (company_id,name,address,phone,uid) VALUES (?,?,?,?,?)",
                 (CID, "Main", "", "", str(uuid.uuid4())))
    pid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO products (id, company_id, sku, barcode, name, cost_price, sell_price, tax_rate, "
        "unit, reorder_level, status) VALUES (?, ?, ?, '', 'Widget', 5, 10, 0, 'pcs', 5, 'active')",
        (pid, CID, f"PC-{pid[:8]}"))
    conn.commit()
    return pid


def _sale_ev(number, terminal=TERMINAL_A, uid=None):
    uid = uid or str(uuid.uuid4())
    return {
        "entity_type": "sale", "event_type": "create", "entity_id": uid,
        "payload": {
            "uid": uid, "sale_number": number, "branch_uid": None, "customer_id": None,
            "cashier": "POS", "subtotal": 10, "discount_amount": 0, "tax_amount": 0, "total": 10,
            "amount_paid": 10, "change_amount": 0, "points_redeemed_amount": 0,
            "payment_method": "cash", "status": "completed", "notes": "",
            "created_at": "2026-09-24 09:00:00", "due_date": None, "actor_user_uid": None,
            "terminal_id": terminal, "created_at_utc": None,
        },
    }


def _park_sale(conn, sale_ev, reason="doc_number_clash"):
    """Hand-written parked sale -- the same shape `_quarantine_apply_event`
    writes (entity_id = wire uid, payload = the event payload)."""
    p = sale_ev["payload"]
    conn.execute(
        "INSERT INTO sync_apply_quarantine (entity_id, entity_type, event_type, payload, reason, detail) "
        "VALUES (?,?,?,?,?,?)",
        (p["uid"], "sale", "create", json.dumps(p), reason, "test"))
    conn.commit()


def _movement_ev(pid, qty=-3, movement_type="sale_out", reference=None, terminal=TERMINAL_A,
                 source_type=None, source_uid=None):
    uid = str(uuid.uuid4())
    payload = {
        "uid": uid, "product_id": pid, "branch_uid": None, "movement_type": movement_type,
        "quantity": qty, "unit_cost": 0, "reference": reference, "notes": None,
        "created_by": "System", "actor_user_uid": None, "terminal_id": terminal,
        "created_at_utc": None,
    }
    if source_type is not None:
        payload["source_type"] = source_type
        payload["source_uid"] = source_uid
    return {"entity_type": "inventory_movement", "event_type": "create", "entity_id": uid,
            "payload": payload}


def _journal_ev(source_type, source_uid, reference="S-1"):
    uid = str(uuid.uuid4())
    return {
        "entity_type": "gl_journal", "event_type": "create", "entity_id": uid,
        "payload": {
            "uid": uid, "business_date": "2026-09-24", "reference": reference, "memo": None,
            "source_type": source_type, "source_uid": source_uid, "branch_uid": None,
            "currency": "JOD", "reversed_journal_uid": None, "posted_by": "System",
            "posted_at": "2026-09-24 10:11:12",
            "lines": [
                {"account_code": "1000", "debit": 10.0, "credit": 0.0, "memo": None},
                {"account_code": "4000", "debit": 0.0, "credit": 10.0, "memo": None},
            ],
            "accounts": [],
        },
    }


def _apply(svc, conn, ev):
    result = svc._apply_event(conn, ev, local_company_id=CID)
    conn.commit()
    return result


def _movement_count(conn, uid):
    return conn.execute("SELECT COUNT(*) n FROM inventory_movements WHERE uid=?", (uid,)).fetchone()["n"]


def _journal_row(conn, uid):
    return conn.execute("SELECT * FROM gl_journals WHERE uid=?", (uid,)).fetchone()


def _quarantine_reason(conn, entity_id):
    r = conn.execute("SELECT reason FROM sync_apply_quarantine WHERE entity_id=?", (entity_id,)).fetchone()
    return r["reason"] if r else None


# ---------------------------------------------------------------- movements, new payloads

def test_new_payload_movement_of_a_parked_sale_is_parked_too(install):
    conn = install()
    try:
        pid = _seed_product(conn)
        svc = _svc(install)
        sale = _sale_ev("S-CLASH")
        _park_sale(conn, sale)
        mv = _movement_ev(pid, reference="S-CLASH", source_type="sale", source_uid=sale["payload"]["uid"])
        assert _apply(svc, conn, mv) is False
        assert _movement_count(conn, mv["entity_id"]) == 0
        assert _quarantine_reason(conn, mv["entity_id"]) == "missing_parent:sale"
        bal = conn.execute("SELECT COUNT(*) n FROM inventory_balances WHERE product_id=?", (pid,)).fetchone()["n"]
        assert bal == 0, "a parked sale's movement still touched the stock balance"
    finally:
        conn.close()


def test_new_payload_movement_of_an_absent_sale_is_parked(install):
    conn = install()
    try:
        pid = _seed_product(conn)
        svc = _svc(install)
        mv = _movement_ev(pid, reference="S-NEVER", source_type="sale", source_uid=str(uuid.uuid4()))
        assert _apply(svc, conn, mv) is False
        assert _movement_count(conn, mv["entity_id"]) == 0
        assert _quarantine_reason(conn, mv["entity_id"]) == "missing_parent:sale"
    finally:
        conn.close()


def test_new_payload_movement_of_a_present_sale_applies(install):
    """Allow half: the rule must not park the ordinary case."""
    conn = install()
    try:
        pid = _seed_product(conn)
        svc = _svc(install)
        sale = _sale_ev("S-OK")
        assert _apply(svc, conn, sale)
        mv = _movement_ev(pid, qty=-3, reference="S-OK", source_type="sale", source_uid=sale["payload"]["uid"])
        assert _apply(svc, conn, mv) is True
        assert _movement_count(conn, mv["entity_id"]) == 1
        assert conn.execute("SELECT quantity_on_hand q FROM inventory_balances WHERE product_id=?",
                            (pid,)).fetchone()["q"] == -3
    finally:
        conn.close()


def test_new_payload_return_movement_of_a_parked_return_is_parked(install):
    conn = install()
    try:
        pid = _seed_product(conn)
        svc = _svc(install)
        ret_uid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO sync_apply_quarantine (entity_id, entity_type, event_type, payload, reason) "
            "VALUES (?,?,?,?,?)",
            (ret_uid, "return", "create", json.dumps({"uid": ret_uid, "return_number": "R-1",
                                                       "terminal_id": TERMINAL_A}), "x"))
        conn.commit()
        mv = _movement_ev(pid, qty=2, movement_type="return_in", reference="R-1",
                          source_type="return", source_uid=ret_uid)
        assert _apply(svc, conn, mv) is False
        assert _quarantine_reason(conn, mv["entity_id"]) == "missing_parent:return"
    finally:
        conn.close()


# ---------------------------------------------------------------- movements, legacy payloads

def test_legacy_movement_matching_a_parked_sale_number_and_terminal_is_parked(install):
    """Rollout window: an OLD sender's sale_out has no `source_uid`; only
    `reference` (= sale_number) + `terminal_id` tie it to the parked sale."""
    conn = install()
    try:
        pid = _seed_product(conn)
        svc = _svc(install)
        _park_sale(conn, _sale_ev("S-LEGACY", TERMINAL_A))
        mv = _movement_ev(pid, reference="S-LEGACY", terminal=TERMINAL_A)
        assert _apply(svc, conn, mv) is False
        assert _movement_count(conn, mv["entity_id"]) == 0
        assert _quarantine_reason(conn, mv["entity_id"]) == "missing_parent:sale"
    finally:
        conn.close()


def test_legacy_movement_with_same_number_but_other_terminal_is_NOT_parked(install):
    """Two computers can hold the same number -- that clash is what parked
    the loser. The WINNER's movement (other terminal) must still apply."""
    conn = install()
    try:
        pid = _seed_product(conn)
        svc = _svc(install)
        _park_sale(conn, _sale_ev("S-LEGACY", TERMINAL_A))
        mv = _movement_ev(pid, reference="S-LEGACY", terminal=TERMINAL_B)
        assert _apply(svc, conn, mv) is True
        assert _movement_count(conn, mv["entity_id"]) == 1
    finally:
        conn.close()


def test_legacy_movement_with_no_parked_sale_applies(install):
    conn = install()
    try:
        pid = _seed_product(conn)
        svc = _svc(install)
        mv = _movement_ev(pid, reference="S-NOBODY-PARKED")
        assert _apply(svc, conn, mv) is True
        assert _movement_count(conn, mv["entity_id"]) == 1
    finally:
        conn.close()


# ---------------------------------------------------------------- journals

def test_journal_of_a_parked_sale_is_parked(install):
    conn = install()
    try:
        ensure_default_chart(conn, CID)
        conn.commit()
        svc = _svc(install)
        sale = _sale_ev("S-CLASH")
        _park_sale(conn, sale)
        j = _journal_ev("sale", sale["payload"]["uid"])
        assert _apply(svc, conn, j) is False
        assert _journal_row(conn, j["entity_id"]) is None
        assert _quarantine_reason(conn, j["entity_id"]) == "missing_parent:sale"
    finally:
        conn.close()


def test_journal_of_an_ABSENT_sale_still_applies_with_null_source(install):
    """Allow half, and the reason the rule is one-way for journals: the v41
    backfill ships journals for sales this device never held."""
    conn = install()
    try:
        ensure_default_chart(conn, CID)
        conn.commit()
        svc = _svc(install)
        j = _journal_ev("sale", str(uuid.uuid4()))
        assert _apply(svc, conn, j) is True
        row = _journal_row(conn, j["entity_id"])
        assert row is not None and row["source_id"] is None
    finally:
        conn.close()


def test_journal_of_a_present_sale_applies_and_links(install):
    conn = install()
    try:
        ensure_default_chart(conn, CID)
        conn.commit()
        svc = _svc(install)
        sale = _sale_ev("S-OK")
        assert _apply(svc, conn, sale)
        j = _journal_ev("sale", sale["payload"]["uid"])
        assert _apply(svc, conn, j) is True
        assert _journal_row(conn, j["entity_id"])["source_id"] is not None
    finally:
        conn.close()


# ---------------------------------------------------------------- replay

def test_replay_applies_children_after_the_parent_lands_and_drift_is_zero(install):
    """Parent + movement + journal all parked. Parent's blocker clears (its
    quarantine row is retried and applies); the ordinary retry loop then
    applies the children in `quarantined_at` order. Nothing doubled, drift 0."""
    conn = install()
    try:
        pid = _seed_product(conn)
        ensure_default_chart(conn, CID)
        conn.commit()
        svc = _svc(install)
        sale = _sale_ev("S-LATER")
        # Parent is parked FIRST (oldest), children second/third.
        _park_sale(conn, sale, reason="missing_parent:branch")
        mv = _movement_ev(pid, qty=-3, reference="S-LATER", source_type="sale",
                          source_uid=sale["payload"]["uid"])
        j = _journal_ev("sale", sale["payload"]["uid"], reference="S-LATER")
        assert _apply(svc, conn, mv) is False
        assert _apply(svc, conn, j) is False
        assert conn.execute("SELECT COUNT(*) n FROM sync_apply_quarantine").fetchone()["n"] == 3

        # One pass of the real retry loop. The parent (a valid sale payload)
        # applies, its row is deleted, then the children are looked at.
        svc._retry_quarantined_events(conn, CID)
        conn.commit()

        assert conn.execute("SELECT COUNT(*) n FROM sync_apply_quarantine").fetchone()["n"] == 0
        assert _movement_count(conn, mv["entity_id"]) == 1
        assert _journal_row(conn, j["entity_id"])["source_id"] is not None
        assert conn.execute("SELECT quantity_on_hand q FROM inventory_balances WHERE product_id=?",
                            (pid,)).fetchone()["q"] == -3

        # A second pass changes nothing (idempotent -- nothing doubled).
        svc._retry_quarantined_events(conn, CID)
        conn.commit()
        assert conn.execute("SELECT quantity_on_hand q FROM inventory_balances WHERE product_id=?",
                            (pid,)).fetchone()["q"] == -3
        assert compute_drift(conn, CID) == []
    finally:
        conn.close()
