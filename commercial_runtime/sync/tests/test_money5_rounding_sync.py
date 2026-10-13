"""MONEY-5 verifier M2: the cash-rounding figure crosses devices.

A sale rounded on one till carries `rounding_amount` in its sync event. If the receiving till drops
it, that till's party balance shows a phantom debt of the rounded fils on every named customer (the
sale reads `total - amount_paid + rounding_amount`). Nothing pinned that before: binding None at the
peer's sale INSERT (sync_service.py, the `p.get("rounding_amount")` bind) left the sync suite and the
rounding suite green.

  * v55 peer      : applies the sale and stores the SAME rounding_amount (the allow half).
  * v54-shaped peer: parks the v55 sale as `schema_version_too_new` -- nothing written, nothing
                     corrupted -- and applies it, rounding intact, on the pull after it upgrades.
  * an old sender with no key stores NULL (never 0): "no rounding recorded".
  * the return carries its own `rounding_amount` the same way (verifier H1 fix).

The identical-GL-journals half is the real two-process test
products/retail/tests/retail_money5_rounding_twodevice_test.py.

MUTATION (performed): bind None for the sale's rounding_amount at the peer INSERT
-> test_a_v55_peer_stores_the_same_rounding_amount goes RED.

Run (one file per process):
    pytest commercial_runtime/sync/tests/test_money5_rounding_sync.py -v
"""
import uuid

from commercial_runtime.sync.sync_service import SyncService
from commercial_runtime.sync.tests.test_sync_service import (  # noqa: F401
    _quarantine, _return_event, _returns, _sale_event, _sales, db_path, get_conn,
)


def _apply_at(get_conn, events, schema_version, cursor=1):
    conn = get_conn()
    service = SyncService(client_factory=lambda: None, get_conn=get_conn,
                          local_company_id_provider=lambda: "money-co",
                          local_schema_version_provider=lambda: schema_version)
    try:
        service.apply_pull_result(conn, {"events": events, "cursor": cursor})
        conn.commit()
    finally:
        conn.close()


def _rounded_sale(uid=None, sender_version=55, **kw):
    ev = _sale_event(uid=uid or str(uuid.uuid4()), total=3.457, subtotal=3.457, amount_paid=3.45,
                     rounding_amount=-0.007, **kw)
    ev["payload"]["_sender_schema_version"] = sender_version
    return ev


def test_a_v55_peer_stores_the_same_rounding_amount(get_conn):
    ev = _rounded_sale()
    _apply_at(get_conn, [ev], schema_version=55)
    [row] = _sales(get_conn)
    assert row["rounding_amount"] == -0.007 and row["amount_paid"] == 3.45 and row["total"] == 3.457
    assert _quarantine(get_conn) == []


def test_a_positive_rounding_round_trips_too(get_conn):
    ev = _sale_event(total=3.457, subtotal=3.457, amount_paid=3.5, rounding_amount=0.043)
    ev["payload"]["_sender_schema_version"] = 55
    _apply_at(get_conn, [ev], schema_version=55)
    assert _sales(get_conn)[0]["rounding_amount"] == 0.043


def test_a_v54_peer_parks_the_v55_sale_instead_of_corrupting_it_then_applies_after_upgrade(get_conn):
    ev = _rounded_sale()
    _apply_at(get_conn, [ev], schema_version=54)
    assert _sales(get_conn) == [], "a v54 peer applied a v55 sale it cannot represent"
    [q] = _quarantine(get_conn)
    assert q["reason"] == "schema_version_too_new" and q["entity_type"] == "sale"
    # upgraded: the very next pull tick retries the parked event, rounding intact
    _apply_at(get_conn, [], schema_version=55, cursor=2)
    [row] = _sales(get_conn)
    assert row["rounding_amount"] == -0.007 and row["amount_paid"] == 3.45
    assert _quarantine(get_conn) == []


def test_a_sale_from_an_old_sender_without_the_key_stores_null_not_zero(get_conn):
    ev = _sale_event(total=3.457, amount_paid=3.457)
    ev["payload"]["_sender_schema_version"] = 55
    _apply_at(get_conn, [ev], schema_version=55)
    assert _sales(get_conn)[0]["rounding_amount"] is None


def test_redelivering_the_rounded_sale_changes_nothing(get_conn):
    ev = _rounded_sale()
    for n in range(3):
        _apply_at(get_conn, [ev], schema_version=55, cursor=n + 1)
    [row] = _sales(get_conn)
    assert row["rounding_amount"] == -0.007


def test_a_return_carries_its_own_rounding_amount_to_the_peer(get_conn):
    """The running-total return rounding reads earlier returns' rounding to recover their exact cash
    value, so a peer that later returns the same sale needs it. NULL stays NULL (never 0)."""
    sale = _rounded_sale()
    ret = _return_event(sale["payload"]["uid"], refund_amount=3.457, tender_refund_amount=3.45,
                        rounding_amount=-0.007)
    ret["payload"]["_sender_schema_version"] = 55
    old = _return_event(sale["payload"]["uid"], refund_amount=1.0, return_number="RET-2")
    old["payload"]["_sender_schema_version"] = 55
    _apply_at(get_conn, [sale, ret, old], schema_version=55)
    got = {r["return_number"]: r["rounding_amount"] for r in _returns(get_conn)}
    assert got == {"RET-1": -0.007, "RET-2": None}, got
