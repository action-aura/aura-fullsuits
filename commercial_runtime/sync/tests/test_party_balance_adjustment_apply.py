"""Schema v44 on the sync APPLY side: `party_balance_adjustment` (plan job
f8252b48, T4).

A customer's / supplier's balance is DERIVED from synced records
(products/retail/backend/core/retail/party_balance.py); this one append-only
entity carries what those records cannot explain (a single-computer shop's
balance at upgrade). The receiving side must:

  * apply it EXACTLY ONCE (a replay, an echo of its own event, or the second
    computer's copy of the same fixed-uid carry is the same fact),
  * stamp the RECEIVER's company_id, never the sender's,
  * refuse a hostile or buggy payload by PARKING it (a NaN amount would poison
    every balance derived for that party on every computer),
  * ignore any non-create event (the type has none), and
  * on a v43 peer, be PARKED as `schema_version_too_new` -- never eaten -- and
    apply once that computer upgrades.

Same fixture technique as test_branch_alias_till_allocation_apply.py: a REAL
`init_retail()` database, `SyncService._apply_event` driven directly.

Run:  pytest commercial_runtime/sync/tests/test_party_balance_adjustment_apply.py -q
"""
from __future__ import annotations

import sqlite3
import sys
import uuid
from decimal import Decimal
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
from commercial_runtime.sync import sync_service as ss  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402
from core.retail import party_balance  # noqa: E402

CID = "company-pba-test"
CUST = str(uuid.uuid4())
UID = party_balance.carry_uid("customer", CUST)


@pytest.fixture
def install(tmp_path, monkeypatch):
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
            c.execute("INSERT INTO customers (id, company_id, name, credit_balance) VALUES (?,?,?,0)",
                      (CUST, CID, "Carried Customer"))
            c.commit()
        finally:
            c.close()
    finally:
        _retail_api_module._CREDIT_SCHEMA_READY = previous
    return _get_conn


def _svc(get_conn, **kw):
    return SyncService(client_factory=lambda: None, get_conn=get_conn,
                       local_company_id_provider=lambda: CID, **kw)


def _apply(get_conn, ev, svc=None):
    svc = svc or _svc(get_conn)
    conn = get_conn()
    try:
        ok = svc._apply_event(conn, ev, local_company_id=CID)
        conn.commit()
        return ok
    finally:
        conn.close()


def _rows(get_conn, sql, params=()):
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _ev(event_type="create", **over):
    p = {"uid": UID, "party_type": "customer", "party_id": CUST, "amount": 7.0,
         "reason": "upgrade_v44_carry", "created_at": "2026-10-05 10:00:00",
         "created_at_utc": "2026-10-05T10:00:00Z", "created_by": "system:v44",
         "company_id": "the-senders-company"}
    p.update(over)
    return {"entity_type": "party_balance_adjustment", "event_type": event_type,
            "entity_id": p.get("uid") if isinstance(p.get("uid"), str) else "x", "payload": p}


def _adjustments(get_conn):
    return _rows(get_conn, "SELECT * FROM party_balance_adjustments ORDER BY uid")


def _quarantined(get_conn):
    return _rows(get_conn, "SELECT reason FROM sync_apply_quarantine WHERE entity_type='party_balance_adjustment'")


def test_applies_once_under_the_receivers_company_and_is_not_echoed(install):
    outbox_before = _rows(install, "SELECT COUNT(*) AS n FROM sync_outbox")[0]["n"]
    assert _apply(install, _ev()) is True
    [row] = _adjustments(install)
    assert (row["uid"], row["party_id"], row["amount"], row["party_type"]) == (UID, CUST, 7.0, "customer")
    assert row["company_id"] == CID, "the receiver stamps ITS OWN company, never the payload's"
    assert _rows(install, "SELECT COUNT(*) AS n FROM sync_outbox")[0]["n"] == outbox_before, \
        "a receiver must not re-announce what it applied"
    # replay and a competing copy of the same fixed-uid carry: still one fact
    assert _apply(install, _ev()) is True
    assert _apply(install, _ev(amount=999.0)) is True
    [again] = _adjustments(install)
    assert again == row, "the first copy wins; a second computer's copy changes nothing"
    assert _quarantined(install) == []


def test_the_applied_fact_moves_the_derived_balance(install):
    conn = install()
    try:
        assert party_balance.customer_balance(conn, CID, CUST, "JOD") == Decimal("0.000")
    finally:
        conn.close()
    _apply(install, _ev(amount=7.0))
    conn = install()
    try:
        assert party_balance.customer_balance(conn, CID, CUST, "JOD") == Decimal("7.000")
    finally:
        conn.close()


@pytest.mark.parametrize("over, why", [
    ({"uid": "not-a-uuid"}, "uid not a uuid"),
    ({"uid": str(uuid.uuid4()).upper()}, "uid not canonical lowercase"),
    ({"party_type": "employee"}, "unknown party_type"),
    ({"party_id": ""}, "empty party_id"),
    ({"party_id": "x" * 65}, "party_id too long"),
    ({"amount": float("nan")}, "nan amount"),
    ({"amount": float("inf")}, "infinite amount"),
    ({"amount": True}, "bool amount"),
    ({"amount": "7.0"}, "string amount"),
    ({"amount": 1e13}, "absurd amount"),
])
def test_a_malformed_payload_is_parked_never_stored(install, over, why):
    assert _apply(install, _ev(**over)) is False, why
    assert _adjustments(install) == [], why
    assert [q["reason"] for q in _quarantined(install)] == ["malformed_party_balance_adjustment"], why


@pytest.mark.parametrize("event_type", ["update", "delete"])
def test_only_create_exists_for_this_type(install, event_type):
    assert _apply(install, _ev(event_type)) is True
    assert _adjustments(install) == []
    assert _quarantined(install) == []


V43_HANDLED = ss.RETAIL_SYNC_ENTITY_TYPES - {"party_balance_adjustment"}


def test_a_v43_receiver_parks_it_and_a_later_upgrade_applies_it(install):
    old = _svc(install, handled_entity_types=V43_HANDLED, local_schema_version_provider=lambda: 43)
    ev = _ev()
    ev["payload"]["_sender_schema_version"] = 44
    sink: list = []
    conn = install()
    try:
        ok = old._apply_event(conn, ev, local_company_id=CID, schema_skew_sink=sink)
        conn.commit()
    finally:
        conn.close()
    assert ok is False
    assert ("party_balance_adjustment", "schema_version_too_new") in sink
    assert [q["reason"] for q in _quarantined(install)] == ["schema_version_too_new"]
    assert _adjustments(install) == [], "a v43 computer must not drop or half-apply the fact"

    # The computer upgrades: same event, now recognised and the schema version caught up.
    new = _svc(install, handled_entity_types=ss.RETAIL_SYNC_ENTITY_TYPES, local_schema_version_provider=lambda: 44)
    conn = install()
    try:
        new._retry_quarantined_events(conn, CID)
        conn.commit()
    finally:
        conn.close()
    [row] = _adjustments(install)
    assert (row["uid"], row["amount"]) == (UID, 7.0)
    assert _quarantined(install) == [], "the parked copy is consumed once it applies"


def test_the_type_is_a_company_scoped_type():
    assert "party_balance_adjustment" in ss.RETAIL_SYNC_ENTITY_TYPES
    assert "party_balance_adjustment" not in ss._ENTITY_TYPES_WITHOUT_COMPANY_ID
