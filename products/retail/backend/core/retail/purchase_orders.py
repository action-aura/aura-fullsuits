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
    W4a-2: RETIRED from the apply path (state is derived now, see below); the
    constant stays only for the v45 compat path and its test.

W4a-2 (schema v46) adds the RECEIPT layer. AP was booked when an order was
TYPED, so supplier balances claimed debt for goods that never came, and an
order could only be received whole. A receipt (header + lines) is now an
immutable synced fact; `received_qty`, `status` and `received_at` on the order
are CACHES recomputed from receipts + the set-once `cancelled_at`/`closed_at`
by `recompute_po_receive_state` -- the ONE function every writer and every
sync apply calls, so every computer derives the same state from the same rows.

NO IMPORT OF api/: retail_api.py imports this module, so importing back would
be a cycle (same reason po_split.py duplicates its rounding helper).
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal

from commercial_runtime.currency import currency_quantum, quantize_money

#: forward-only merge order for `purchase_orders.status`. Higher rank wins.
PO_STATUS_RANK = {'pending': 0, 'cancelled': 1, 'received': 2}

#: NEVER CHANGE. Two computers must derive the SAME movement uid from the same
#: (order, line); a different namespace on one of them would make each mint its
#: own uid, the unique index would not collide, and the stock would be added
#: twice -- silently, because nothing errors.
_PO_RECEIPT_NAMESPACE = uuid.UUID('a674e3be-c149-497a-b0a7-f235e22c5ae0')

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


def _digits_from_epsilon(epsilon) -> int:
    """How many minor-unit digits the currency has, read back from its epsilon.

    Every caller passes `_money_epsilon(currency)` / `party_balance.money_
    epsilon(currency)`, which is HALF a minor unit (0.0005 JOD, 0.005 USD, 0.5
    JPY), so doubling it gives the quantum. No usable epsilon (None/0/negative)
    answers 3, the finest currency in use, so a bad argument can never make a
    figure coarser than the money really is."""
    quantum = (Decimal(str(epsilon or 0)) * 2).normalize()
    if quantum <= 0:
        return 3
    return max(0, -quantum.as_tuple().exponent)


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
    (paid as float, status). The caller owns the transaction.

    W4a verify F5: ONE RULE with the receiver's twin (`SyncService.
    _recompute_po_paid`, which cannot import this module). Both sum in INTEGER
    MINOR UNITS of the shop's currency -- the same `CAST(ROUND(amount * factor)
    AS INTEGER)` SQL, factor = 10 ** digits -- and compare floats the same way.
    This used to round every amount to a fixed 3 dp, so on a 2-dp or 0-dp
    currency the computer that typed an order and the one that pulled it could
    disagree about 'paid' exactly at the half-unit boundary (measured: USD
    99.995 of 100 read 'paid' on both, but as 99.995 here and 100.0 there).
    Digits come from `epsilon`, which every caller derives from the currency."""
    factor = 10 ** _digits_from_epsilon(epsilon)
    eps = float(epsilon or 0)
    minor = conn.execute(
        "SELECT COALESCE(SUM(CAST(ROUND(amount * ?) AS INTEGER)), 0) FROM payments "
        "WHERE company_id=? AND related_type='po' AND related_id=? "
        "AND COALESCE(status,'active')='active'",
        (factor, cid, po_id)
    ).fetchone()[0]
    paid = float(Decimal(minor) / Decimal(factor))
    head = conn.execute(
        "SELECT total, status FROM purchase_orders WHERE id=? AND company_id=?", (po_id, cid)
    ).fetchone()
    total = float(head[0] or 0) if head else 0.0
    if head and head[1] == 'closed':
        # W4a-2 verify R4: a CLOSED-short order owes only for what arrived, so
        # "paid" means paid against the receipts' value, not the full total
        # (24 of 40 delivered and 24 paid read 'partial' for ever). 'cancelled'
        # is unchanged. Same rule in the sync twin (SyncService._recompute_po_paid).
        charge_minor = conn.execute(
            "SELECT COALESCE(SUM(CAST(ROUND(value * ?) AS INTEGER)), 0) FROM purchase_order_receipts "
            "WHERE company_id=? AND po_id=?", (factor, cid, po_id)).fetchone()[0]
        total = float(Decimal(charge_minor) / Decimal(factor))
    if total > eps and total - paid <= eps:
        status = 'paid'
    elif paid > eps:
        status = 'partial'
    else:
        status = 'unpaid'
    conn.execute(
        "UPDATE purchase_orders SET amount_paid=?, payment_status=? WHERE id=? AND company_id=?",
        (paid, status, po_id, cid))
    return paid, status


# -- W4a-2: receipts ---------------------------------------------------------------

def _qty_text(q) -> str:
    """A quantity as canonical text: 6, 6.0, '6.000' and Decimal('6.00') all
    read '6'. Two computers typing the same delivery must hash the same text."""
    return format(Decimal(str(q)).normalize(), 'f')


#: a client-supplied receipt key: 8..64 chars of [A-Za-z0-9-] (a uuid fits).
_RECEIPT_KEY_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def valid_receipt_key(key) -> bool:
    """True when `key` is an acceptable client receipt key (see `keyed_receipt_uid`)."""
    return isinstance(key, str) and bool(_RECEIPT_KEY_RE.fullmatch(key))


def completing_receipt_uid(po_uid: str, prior_receipt_uids) -> str:
    """The uid of the receipt that COMPLETES an order -- the one that takes every
    line's full remaining quantity.

    W4a-2 verify R1 (replaces the D4 rule, whose local receipt count `n` was
    wrong both ways: the same delivery typed on two computers that held a
    different number of receipts minted different uids and was counted twice
    with no over-receipt flag, and two DIFFERENT same-size deliveries at the
    same `n` minted the same uid so one was silently lost).

    The identity of "the rest" is the history it completes: the order's uid and
    the sorted uids of the receipts present locally. Two computers that saw the
    SAME history and both receive the rest mint the same uid and collapse (this
    keeps W4a's double-offline-receive guarantee: prior = empty for a whole
    order). Computers with DIFFERENT histories mint different uids, both facts
    count, and the over-receipt is flagged -- visible, never silently lost.
    NEVER derive this from a per-computer counter."""
    prior = ",".join(sorted(prior_receipt_uids))
    return str(uuid.uuid5(_PO_RECEIPT_NAMESPACE, f"{po_uid}:rest-after:{prior}"))


def keyed_receipt_uid(po_uid: str, receipt_key: str) -> str:
    """The uid of a PARTIAL receipt, from the key the client minted when it
    opened the receive dialog. A retry or a double submit carries the same key,
    so it mints the same uid and hits ON CONFLICT(uid) DO NOTHING -- recorded
    once. A request WITHOUT a key gets a random uuid4 from the route (no retry
    dedupe: with nothing to say "this is the same delivery" a deterministic uid
    would merge two genuinely different deliveries of the same size).

    Residual, by design: the same partial delivery typed on two computers while
    offline has two keys, counts twice, and -- if that exceeds the order -- is
    flagged `po_over_received`. It is never silently lost."""
    return str(uuid.uuid5(_PO_RECEIPT_NAMESPACE, f"{po_uid}:key:{receipt_key}"))


def receipt_line_uid(receipt_uid_: str, item_uid: str) -> str:
    return str(uuid.uuid5(_PO_RECEIPT_NAMESPACE, f"{receipt_uid_}:{item_uid}"))


def receipt_line_movement_uid(receipt_uid_: str, item_uid: str) -> str:
    """The stock movement a receipt line writes: a pure function of (receipt,
    line), so the W4a ON CONFLICT(uid) gate keeps stock from landing twice."""
    return str(uuid.uuid5(_PO_RECEIPT_NAMESPACE, f"{receipt_uid_}:{item_uid}:movement"))


def legacy_receipt_uid(po_uid: str) -> str:
    """The one receipt synthesised for an order received before v46 (D15).
    Never queued: every computer derives the same uid from the order's uid."""
    return str(uuid.uuid5(_PO_RECEIPT_NAMESPACE, f"{po_uid}:legacy-receipt"))


def _eps(currency) -> Decimal:
    return currency_quantum(currency) / 2


def received_by_item(conn, cid, po_id) -> dict:
    """{po_item_id: Decimal received} summed over this order's receipt lines."""
    out = {}
    for row in conn.execute(
            "SELECT l.po_item_id, l.quantity FROM purchase_order_receipt_lines l "
            "JOIN purchase_order_receipts r ON r.id = l.receipt_id "
            "WHERE r.company_id=? AND r.po_id=?", (cid, po_id)).fetchall():
        out[row[0]] = out.get(row[0], Decimal('0')) + Decimal(str(row[1] or 0))
    return out


def receipt_completes(conn, cid, po_id, lines) -> bool:
    """True when a delivery of `lines` = [(po_item_id, quantity)] takes every
    line of the order to (at least) its ordered quantity -- i.e. it is the
    receipt that COMPLETES the order. Decimal throughout (no float slack).
    An order with no lines is never completed by anything."""
    items = {row[0]: Decimal(str(row[1] or 0)) for row in conn.execute(
        "SELECT id, quantity FROM purchase_order_items WHERE po_id=?", (po_id,)).fetchall()}
    if not items:
        return False
    delivered = {}
    for item_id, qty in lines:
        delivered[item_id] = delivered.get(item_id, Decimal('0')) + Decimal(str(qty))
    already = received_by_item(conn, cid, po_id)
    return all(already.get(i, Decimal('0')) + delivered.get(i, Decimal('0')) >= ordered
               for i, ordered in items.items())


def receipt_value(conn, cid, po_id, lines, currency) -> Decimal:
    """What a receipt of `lines` = [(po_item_id, quantity)] is WORTH, computed
    ONCE by the writer and then stored and synced -- a peer never recomputes it
    (D5).

    value = quantize(SUM(qty x unit_cost) x factor), factor = total / subtotal
    when the order has a subtotal (so tax rides along proportionally), else 1.
    If this receipt COMPLETES every line (everything received, including this
    delivery, reaches the ordered quantity) the value is the REMAINDER instead:
    total - the value of the earlier receipts. Rounding each part on its own
    drifts (3 x 33.336 = 100.008 against a total of 100.000); the remainder rule
    makes a fully received order's receipts sum to its total exactly, which is
    what keeps the supplier's balance, the order's total and Accounts Payable
    from disagreeing by a few fils."""
    head = conn.execute(
        "SELECT total, subtotal FROM purchase_orders WHERE id=? AND company_id=?", (po_id, cid)).fetchone()
    if head is None:
        raise ValueError(f"purchase order {po_id} not found for company {cid}")
    total = Decimal(str(head[0] or 0))
    subtotal = Decimal(str(head[1] or 0))
    factor = (total / subtotal) if subtotal > _eps(currency) else Decimal('1')
    items = {row[0]: (Decimal(str(row[1] or 0)), Decimal(str(row[2] or 0))) for row in conn.execute(
        "SELECT id, quantity, unit_cost FROM purchase_order_items WHERE po_id=?", (po_id,)).fetchall()}
    raw = Decimal('0')
    this_delivery = {}
    for item_id, qty in lines:
        if item_id not in items:
            raise ValueError(f"line {item_id} does not belong to purchase order {po_id}")
        q = Decimal(str(qty))
        raw += q * items[item_id][1]
        this_delivery[item_id] = this_delivery.get(item_id, Decimal('0')) + q
    value = quantize_money(raw * factor, currency)
    if receipt_completes(conn, cid, po_id, lines):
        prior = sum((Decimal(str(r[0] or 0)) for r in conn.execute(
            "SELECT value FROM purchase_order_receipts WHERE company_id=? AND po_id=?",
            (cid, po_id)).fetchall()), Decimal('0'))
        value = max(Decimal('0'), quantize_money(total, currency) - prior)
    return value


def po_over_received(conn, cid, po_id) -> bool:
    """True when some line's receipts exceed its ordered quantity -- two
    different offline deliveries that together overshoot (D12). A fact to
    record as a conflict, never to refuse."""
    ordered = {row[0]: Decimal(str(row[1] or 0)) for row in conn.execute(
        "SELECT id, quantity FROM purchase_order_items WHERE po_id=?", (po_id,)).fetchall()}
    return any(got > ordered.get(i, Decimal('0')) for i, got in received_by_item(conn, cid, po_id).items())


def recompute_po_receive_state(conn, cid, po_id):
    """Derive and store (items.received_qty, status, received_at) for order
    `po_id` from its receipts and the set-once `cancelled_at` / `closed_at`.
    Returns the status, or None when the order is not this company's.

    D3: receipts exist -> 'received' when every line's received >= ordered; else
    'closed' when closed_at OR cancelled_at is set (an order cancelled after a
    partial delivery IS a close-short -- the stock is here); else 'partial'. No
    receipts -> 'cancelled' when cancelled_at (or closed_at, which the routes
    cannot produce without a receipt) is set, else 'pending'. `received_at` is
    the latest receipt's when 'received', else NULL. The caller owns the
    transaction."""
    head = conn.execute(
        "SELECT closed_at, cancelled_at FROM purchase_orders WHERE id=? AND company_id=?",
        (po_id, cid)).fetchone()
    if head is None:
        return None
    received = received_by_item(conn, cid, po_id)
    items = conn.execute("SELECT id, quantity FROM purchase_order_items WHERE po_id=?", (po_id,)).fetchall()
    for item in items:
        conn.execute("UPDATE purchase_order_items SET received_qty=? WHERE id=?",
                     (float(received.get(item[0], Decimal('0'))), item[0]))
    n_receipts, latest = conn.execute(
        "SELECT COUNT(*), MAX(received_at) FROM purchase_order_receipts WHERE company_id=? AND po_id=?",
        (cid, po_id)).fetchone()
    if n_receipts:
        # W4a-2 verify R2: an order with NO lines is never 'received' (the old
        # all([]) said it was) -- the sync twin always said otherwise.
        complete = bool(items) and all(
            received.get(i[0], Decimal('0')) >= Decimal(str(i[1] or 0)) for i in items)
        if complete:
            status = 'received'
        elif head[0] or head[1]:
            status = 'closed'
        else:
            status = 'partial'
    else:
        status = 'cancelled' if (head[0] or head[1]) else 'pending'
    conn.execute("UPDATE purchase_orders SET status=?, received_at=? WHERE id=? AND company_id=?",
                 (status, latest if status == 'received' else None, po_id, cid))
    return status


def po_supplier_charge_minor(conn, cid, po_id, epsilon) -> int:
    """What order `po_id` adds to its supplier's balance, in integer MINOR units
    (D10). The same rule `party_balance._supplier_minor` aggregates in SQL --
    kept as a per-order function so the statement and the aging report read ONE
    answer instead of re-deriving it.

    'receipt' basis: the sum of the receipt values, whatever the state.
    'order' basis (every order that existed at v46, NULL included): closed (or
    cancelled after a delivery) -> the receipt values; else cancelled -> 0; else
    the total (when above epsilon)."""
    factor = 10 ** _digits_from_epsilon(epsilon)
    head = conn.execute(
        "SELECT total, ap_basis, closed_at, cancelled_at, status FROM purchase_orders "
        "WHERE id=? AND company_id=?", (po_id, cid)).fetchone()
    if head is None:
        return 0
    n, minor = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(CAST(ROUND(value * ?) AS INTEGER)), 0) "
        "FROM purchase_order_receipts WHERE company_id=? AND po_id=?", (factor, cid, po_id)).fetchone()
    if head[1] == 'receipt':
        return int(minor)
    if head[2] or (head[3] and n):
        return int(minor)
    if head[3] or (head[4] or '') == 'cancelled':
        return 0
    total = float(head[0] or 0)
    if total <= float(epsilon or 0):
        return 0
    return int(conn.execute("SELECT CAST(ROUND(? * ?) AS INTEGER)", (total, factor)).fetchone()[0])
