"""Aura Retail -- W4a: purchase orders shared between computers.

WHY THIS MODULE EXISTS. A purchase order used to live only on the computer
that typed it: `purchase_orders.id` is a per-computer integer and nothing was
queued for sync. A second computer could not see, receive or cancel it; a
supplier instalment typed there carried `related_id=<the writer's integer>`
and landed on an UNRELATED local order; `amount_paid` was a stored increment
that a void never corrected; and two computers receiving one order would each
have added the stock. W4a gives an order a `uid`, syncs it, and moves every
figure that used to be a per-computer increment onto something derivable.

Three small rules live here because the API (api/retail_api.py), the sync
apply (commercial_runtime/sync/sync_service.py) and the ledger all need the
SAME answer and none of them may import another's file:

  * `recompute_po_paid` -- paid is DERIVED from the active supplier payments
    naming the order. Called after every write that can change the set
    (instalment, void, a synced payment arriving, a `po_link` re-pointing it).
  * `receipt_movement_uid` -- the receipt's stock movement uid is a pure
    function of (order uid, line uid). Two computers that each receive the
    order mint the SAME uid, so the second one's insert collides on the unique
    index and the stock is added once.
  * `PO_STATUS_RANK` -- the order's status only moves forward when merged
    (pending -> cancelled -> received); `received` beats `cancelled` because
    stock physically arrived and cannot be un-arrived by a click elsewhere.

NO IMPORT OF api/: retail_api.py imports this module, so importing back would
be a cycle (same reason po_split.py duplicates its rounding helper).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

#: forward-only merge order for `purchase_orders.status`. Higher rank wins.
PO_STATUS_RANK = {'pending': 0, 'cancelled': 1, 'received': 2}

#: NEVER CHANGE. Two computers must derive the SAME movement uid from the same
#: (order, line); a different namespace on one of them would make each mint its
#: own uid, the unique index would not collide, and the stock would be added
#: twice -- silently, because nothing errors.
_PO_RECEIPT_NAMESPACE = uuid.UUID('a674e3be-c149-497a-b0a7-f235e22c5ae0')

_MONEY_DIGITS = Decimal('0.001')


def receipt_movement_uid(po_uid: str, item_uid: str) -> str:
    """The uid of the stock movement that receiving line `item_uid` of order
    `po_uid` writes -- deterministic, so it is the same on every computer."""
    return str(uuid.uuid5(_PO_RECEIPT_NAMESPACE, f"{po_uid}:{item_uid}:receipt"))


def po_id_by_uid(conn, cid, uid):
    """This computer's integer id for the order with wire identity `uid`, or
    None when it has not arrived here (callers park the event, they never
    guess). Scoped to the company like every lookup in this codebase."""
    if not uid:
        return None
    row = conn.execute(
        "SELECT id FROM purchase_orders WHERE company_id=? AND uid=?", (cid, uid)
    ).fetchone()
    return None if row is None else row[0]


def _money(value) -> Decimal:
    """Round to 3 decimals (the finest currency in use is the 3-dp JOD) so a
    sum of floats like 0.1+0.1+0.1 is exactly 0.3 and never 0.30000000000000004."""
    return Decimal(str(value or 0)).quantize(_MONEY_DIGITS)


def recompute_po_paid(conn, cid, po_id, epsilon):
    """Derive and store (paid, payment_status) for order `po_id`.

    paid = SUM(amount) over the ACTIVE supplier payments that name this order
    (`related_type='po'`, `related_id=po_id`, company-scoped). "Active" is the
    SAME predicate core/retail/party_balance.py uses for active supplier
    payments -- `COALESCE(status,'active')='active'` -- so a voided payment
    leaves the order's paid figure exactly as it leaves the supplier's balance,
    and the two can never disagree about whether it counts.

    status: 'paid' when what remains is within `epsilon` (and the order has a
    real total), 'partial' when anything beyond `epsilon` is paid, else
    'unpaid'. Writes `amount_paid` / `payment_status` and returns
    (paid as float, status). The caller owns the transaction."""
    eps = Decimal(str(epsilon or 0))
    rows = conn.execute(
        "SELECT amount FROM payments WHERE company_id=? AND related_type='po' "
        "AND related_id=? AND COALESCE(status,'active')='active'",
        (cid, po_id)
    ).fetchall()
    paid = sum((_money(r[0]) for r in rows), Decimal('0'))
    head = conn.execute(
        "SELECT total FROM purchase_orders WHERE id=? AND company_id=?", (po_id, cid)
    ).fetchone()
    total = _money(head[0]) if head else Decimal('0')
    if total > eps and total - paid <= eps:
        status = 'paid'
    elif paid > eps:
        status = 'partial'
    else:
        status = 'unpaid'
    paid_f = float(paid)
    conn.execute(
        "UPDATE purchase_orders SET amount_paid=?, payment_status=? WHERE id=? AND company_id=?",
        (paid_f, status, po_id, cid))
    return paid_f, status
