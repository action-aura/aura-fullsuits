"""Aura Retail -- W2a: loyalty points shared between computers (schema v48).

WHY THIS MODULE EXISTS (W2a). `loyalty_ledger` rows never left the computer
that wrote them: points earned on A did not exist on B, a return on B of A's
sale found no earn/redeem rows (no claw-back, no give-back, the wrong cash
refund), `sale_id`/`return_id` were local integers, and each computer's v27
'opening' row for the same customer would have DOUBLED the balance the moment
rows started syncing.

The small rules below live here because the API (api/retail_api.py), the
importer (api/import_api.py) and the sync apply (commercial_runtime/sync/
sync_service.py) all need the SAME answer. sync_service cannot import products
code, so it carries a duplicate of the one SQL statement in `refresh_points`
and a test pins the two equal (the W4a / W3b pattern).

  * `opening_uid` -- a pure function of the customer, so two computers' own
    'opening' rows for one customer collide on the unique `uid` index.
  * `refresh_points` -- `customers.loyalty_points` is a CACHE of
    SUM(points_delta); this is the ONE rule that refreshes it. It never bumps
    `row_version` and queues nothing: the cache is derived, not an edit.
  * `balance` -- the SUM itself.
  * `smaller` -- the deterministic tie-break when two computers wrote a
    different opening value for one customer (the smaller value wins on every
    computer; the less generous reading is the safe one for the shop).

NO IMPORT OF api/ OR general_ledger: nothing here posts or queues anything.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

#: NEVER CHANGE. Two computers must derive the SAME opening uid for a customer;
#: a different namespace on one of them would make each mint its own uid, the
#: unique index would not collide, and the opening balance would be counted
#: twice -- silently, because nothing errors.
_OPENING_NAMESPACE = uuid.UUID('b3a7d1c4-5e92-4f08-9a61-0c2d8e4f7b13')


def opening_uid(customer_id: str) -> str:
    """The uid of customer `customer_id`'s 'opening' ledger row -- the same on
    every computer."""
    return str(uuid.uuid5(_OPENING_NAMESPACE, f"{customer_id}:opening"))


#: W2a verify F1 -- NEVER CHANGE. The namespace of an IMPORT's 'adjust' row uid.
#: Two computers that import the same file on the same shop day must mint the
#: SAME uid for the same customer, so the second copy collides on the unique
#: uid index and collapses into one row instead of crediting the difference
#: twice. A different literal on one computer would silently mint different
#: uids and the over-credit would come back. sync_service carries a twin; a
#: test pins the two equal.
_IMPORT_NAMESPACE = uuid.UUID('4d9e2f7a-81c3-4b56-9e0d-6a3f1c8b5e72')


def canonical_points(value) -> str:
    """A points figure as one canonical decimal string ('150', never '150.0'
    or '1.5E+2'), so the same target spelled two ways derives the same uid."""
    text = format(Decimal(str(value)).normalize(), 'f')
    return '0' if text in ('-0', '') else text


def import_uid(customer_id: str, target, business_date: str) -> str:
    """The uid of the 'adjust' row an import writes to bring `customer_id` to
    `target` points on shop day `business_date` -- the same on every computer
    that imports the same figure the same day. An 'opening' import row keeps
    `opening_uid` instead."""
    return str(uuid.uuid5(
        _IMPORT_NAMESPACE, f"{customer_id}:import:{canonical_points(target)}:{business_date}"))


def balance(conn, company_id, customer_id):
    """A customer's points: the sum of every ledger row, scoped to the company."""
    row = conn.execute(
        "SELECT COALESCE(SUM(points_delta), 0) FROM loyalty_ledger "
        "WHERE company_id=? AND customer_id=?", (company_id, customer_id)).fetchone()
    return row[0]


def refresh_points(conn, company_id, customer_id):
    """Set `customers.loyalty_points` to SUM(points_delta) and return it.

    REPLACES the cache (never adds to it), so applying the same ledger rows
    twice cannot double it. Deliberately does not touch `row_version` or
    `updated_at`, and queues no customer event: the figure is derived from
    rows that sync on their own. sync_service duplicates this ONE statement
    (it cannot import this module); retail_loyalty_sync_apply_test pins the
    twin equal."""
    conn.execute(
        "UPDATE customers SET loyalty_points = COALESCE((SELECT SUM(points_delta) FROM loyalty_ledger "
        "WHERE company_id=? AND customer_id=?), 0) WHERE company_id=? AND id=?",
        (company_id, customer_id, company_id, customer_id))
    return balance(conn, company_id, customer_id)


def smaller(a, b):
    """The deterministic tie-break: the smaller value wins, either way round."""
    return a if a <= b else b
