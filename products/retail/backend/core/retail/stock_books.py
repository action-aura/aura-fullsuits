"""Aura Retail -- STOCK-BOOKS: the read-side helpers that tie stock to the books.

The posting helpers live in `general_ledger.py` (they write journals); this
module holds the figures they and the Inventory tie need that are NOT journal
writes: what a unit costs right now, which stock movements the books have not
seen, and the pieces of the "does Inventory (1200) agree with the stock on the
shelves" comparison. `general_ledger.inventory_tie` stitches them together.

NO IMPORT OF `general_ledger` AT MODULE LEVEL: `general_ledger` imports this
module lazily inside `inventory_tie`, and this module never needs the ledger
itself -- it reads `gl_journals` by plain SQL -- so the two cannot form an
import cycle.

THE WORD "UNBOOKED". A stock movement is BOOKED when a journal exists that
names it. For the entries this wave posts (opening stock, manual stock in/out)
that is a journal whose `source_uid` is the movement's own `uid`. For a
purchase receipt the movement carries the PO number as its `reference`, and the
PO's order/receipt journals carry the same number. A movement with no such
journal is stock the books have never heard of -- stock entered before this
wave, or entered at no known cost (a journal at a zero amount is deliberately
never posted, exactly like `post_cogs`). The Inventory screen must say "Stock
not opened yet" for that, never "differs": a ledger that is missing history is
not a ledger that is wrong.
"""
from __future__ import annotations

from decimal import Decimal

from commercial_runtime.currency import currency_quantum, quantize_money

#: movement types whose books entry is keyed on the MOVEMENT's own uid
#: (`opening_entry` for stock entered at create/import, `stock_adjustment` for
#: a manual in/out or a counted exception).
_MOVEMENT_KEYED_TYPES = ('opening_stock', 'stock_in', 'stock_out')
_MOVEMENT_KEYED_JOURNALS = ('opening_entry', 'stock_adjustment')
#: journals a `purchase_in` movement may be booked by, matched on the PO
#: number (`inventory_movements.reference` == `gl_journals.reference`).
_PURCHASE_JOURNALS = ('purchase_order', 'po_receipt')


def effective_unit_cost(conn, cid, product_id, branch_id, currency=None):
    """What one unit of `product_id` at `branch_id` costs RIGHT NOW, as a
    Decimal -- the identical three-level chain `metrics.inventory_value` and
    `create_sale`'s cost snapshot read: the branch's own `inventory_balances.
    avg_cost`, else the company-wide `products.avg_cost`, else `products.
    cost_price`; 0 when nothing is known (an unknown cost is 0, never a guess
    -- every poster skips a zero amount).

    NOT quantized: a cost like 0.396 or 1.0005 is a real figure and the
    quantum applies to the LINE AMOUNT (qty x cost), not to the unit price,
    exactly as `sale_items` snapshots do. `currency` is accepted so callers
    pass one consistent signature; it does not change the figure."""
    row = conn.execute(
        "SELECT p.avg_cost AS p_avg, p.cost_price AS p_cost, "
        "       (SELECT b.avg_cost FROM inventory_balances b "
        "         WHERE b.company_id=? AND b.product_id=? AND b.branch_id=?) AS b_avg "
        "FROM products p WHERE p.id=? AND p.company_id=?",
        (cid, product_id, branch_id, product_id, cid)
    ).fetchone()
    if not row:
        return Decimal('0')
    for value in (row['b_avg'], row['p_avg'], row['p_cost']):
        if value:
            return Decimal(str(value))
    return Decimal('0')


def _has_column(conn, table, column):
    return any(row[1] == column for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall())


def _currency_of(conn, cid):
    """The shop's base currency (None when unreadable -> 2dp quantum)."""
    try:
        row = conn.execute(
            "SELECT svalue FROM retail_settings WHERE company_id=? AND skey='base_currency'",
            (cid,)).fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def unbooked_movement_count(conn, cid):
    """How many stock movements of this company the books have never seen
    (see the module docstring's "UNBOOKED"). Two groups:

      * `opening_stock` / `stock_in` / `stock_out` with no `opening_entry` /
        `stock_adjustment` journal whose `source_uid` is the movement's uid
        (a movement with no uid at all can never be named, so it counts);
      * `purchase_in` whose `reference` matches no `purchase_order` /
        `po_receipt` journal of the company.
    """
    moved = conn.execute(
        "SELECT COUNT(*) FROM inventory_movements m "
        "WHERE m.company_id=? AND m.movement_type IN (%s) "
        "AND NOT EXISTS (SELECT 1 FROM gl_journals j "
        "                 WHERE j.company_id=m.company_id AND j.source_uid=m.uid "
        "                   AND j.source_type IN (%s))" % (
            ','.join('?' * len(_MOVEMENT_KEYED_TYPES)),
            ','.join('?' * len(_MOVEMENT_KEYED_JOURNALS))),
        (cid, *_MOVEMENT_KEYED_TYPES, *_MOVEMENT_KEYED_JOURNALS)
    ).fetchone()[0]
    # STOCK-BOOKS verify F6: a receipt is matched to its journal by the LOCAL
    # purchase order id, not by the po_number text alone. Two computers can
    # both mint "PO-0001" (a configured document series has no per-computer
    # fragment), and the other computer's SYNCED journal carries that same
    # text with source_id NULL -- text matching let it "book" this computer's
    # own unbooked receipt. So: when a PO with that number exists HERE, only a
    # journal whose source_id is that PO's local id counts; only when no such
    # PO exists here (the receipt itself came from a sibling) is the text
    # match against the synced journal the only possible link.
    #
    # W4a: purchase orders now sync, so a PO with that number usually DOES exist
    # on every computer, and a SYNCED order/receipt journal (source_id NULL)
    # can no longer be linked by integer id. When the PO carries
    # `order_journal_uid`, that uid is the cross-computer link: the order
    # journal IS that uid, and the receipt journal's `source_uid` is that uid.
    # The F6 intent is kept -- the link is still through THE PO, never through
    # the number text alone, so two different "PO-0001"s cannot book each other.
    # The legacy source_id link stays for orders with no uid link (written
    # before v45, or posted at zero). A database without the column (hand-built
    # fixture) falls back to the pre-W4a SQL exactly.
    uid_link = (
        "OR (p.order_journal_uid IS NOT NULL "
        "    AND (j.uid=p.order_journal_uid OR j.source_uid=p.order_journal_uid)) "
        if _has_column(conn, 'purchase_orders', 'order_journal_uid') else "")
    # W4a-2 (schema v46): a receipt-basis order has NO order journal, so the
    # clauses above can never book its movements. A movement is booked when it
    # is the movement of a RECEIPT LINE (`purchase_order_receipt_lines.
    # movement_uid`, a pure function of receipt + line) whose receipt either has
    # its own po_receipt journal (`source_uid` = the receipt's uid) or is worth
    # nothing at all (a zero-cost receipt is never journalled -- unknown cost is
    # silence -- and must not read as "stock not opened yet"). Linked THROUGH
    # THE RECEIPT, never through the po_number text, for the same F6 reason as
    # above. Absent tables (a pre-v46 fixture) leave the SQL exactly as it was.
    receipt_link, receipt_params = "", ()
    if _has_column(conn, 'purchase_order_receipt_lines', 'movement_uid'):
        receipt_link = (
            "  OR EXISTS (SELECT 1 FROM purchase_order_receipt_lines rl "
            "              JOIN purchase_order_receipts r ON r.id = rl.receipt_id "
            "             WHERE rl.movement_uid = m.uid AND r.company_id = m.company_id "
            "               AND (r.value <= ? OR EXISTS (SELECT 1 FROM gl_journals j3 "
            "                     WHERE j3.company_id = r.company_id AND j3.source_type = 'po_receipt' "
            "                       AND j3.source_uid = r.uid))) ")
        receipt_params = (float(currency_quantum(_currency_of(conn, cid)) / 2),)
    # W4a-2 fix (F5): the per-ORDER links above (a journal whose source_id is the
    # order's id, or the order journal's uid) would book EVERY movement of an
    # order once ANY of its journals exists -- so a second receipt whose own
    # journal has not synced stayed hidden behind the first. A movement that is
    # the line of a REAL (non-legacy) receipt is therefore judged by the
    # receipt link alone. Legacy receipts (v46 migration / v45 compat) keep the
    # per-order link: their books are the single W4a/order journal, whose
    # source_uid is the order's, not a receipt's.
    own_receipt_guard = ""
    if _has_column(conn, 'purchase_order_receipt_lines', 'movement_uid'):
        own_receipt_guard = (
            "AND NOT EXISTS (SELECT 1 FROM purchase_order_receipt_lines rl0 "
            "                 JOIN purchase_order_receipts r0 ON r0.id = rl0.receipt_id "
            "                WHERE rl0.movement_uid = m.uid AND r0.company_id = m.company_id "
            "                  AND COALESCE(r0.legacy, 0) = 0) ")
    purchased = conn.execute(
        "SELECT COUNT(*) FROM inventory_movements m "
        "WHERE m.company_id=? AND m.movement_type='purchase_in' "
        "AND NOT ( "
        "  EXISTS (SELECT 1 FROM purchase_orders p JOIN gl_journals j "
        "            ON j.company_id=p.company_id AND j.source_type IN (%(js)s) "
        "           AND (j.source_id=p.id %(uid_link)s) "
        "           WHERE p.company_id=m.company_id AND p.po_number=m.reference "
        "           %(own_guard)s) "
        "  OR (NOT EXISTS (SELECT 1 FROM purchase_orders p2 "
        "                   WHERE p2.company_id=m.company_id AND p2.po_number=m.reference) "
        "      AND EXISTS (SELECT 1 FROM gl_journals j2 "
        "                   WHERE j2.company_id=m.company_id AND j2.reference=m.reference "
        "                     AND j2.source_type IN (%(js)s))) "
        "%(receipt_link)s"
        ")" % {'js': ','.join('?' * len(_PURCHASE_JOURNALS)), 'uid_link': uid_link,
               'receipt_link': receipt_link, 'own_guard': own_receipt_guard},
        (cid, *_PURCHASE_JOURNALS, *_PURCHASE_JOURNALS, *receipt_params)
    ).fetchone()[0]
    return int(moved) + int(purchased)


def stock_value(conn, cid, currency):
    """Stock on hand at current cost, as a Decimal -- `metrics.inventory_value`
    (the figure the dashboard already shows), so the tie compares the books to
    the number the shop sees, not to a second formula."""
    from core.retail import metrics
    return Decimal(str(metrics.inventory_value(conn, cid, currency=currency)))


def on_hand_unit_count(conn, cid):
    """Sum of ABSOLUTE on-hand quantities over the same rows `stock_value`
    values (active, non-deleted products) -- the budget for moving-average
    rounding: each unit's cost can be off by at most half a quantum."""
    row = conn.execute(
        "SELECT COALESCE(SUM(ABS(b.quantity_on_hand)), 0) "
        "FROM inventory_balances b JOIN products p ON b.product_id=p.id "
        "WHERE b.company_id=? AND p.status='active' AND p.deleted_at_utc IS NULL",
        (cid,)
    ).fetchone()
    return Decimal(str(row[0] or 0))


def in_transit_stock_value(conn, cid, currency):
    """Value of stock sent but not yet received, on THIS computer: sum over
    in-transit transfers of quantize(qty_sent x send-time unit_cost) per line
    -- the same per-line rounding `post_transfer_send` books."""
    rows = conn.execute(
        "SELECT i.quantity_sent, COALESCE(i.unit_cost, 0) "
        "FROM stock_transfer_items i JOIN stock_transfers t ON t.id=i.transfer_id "
        "WHERE t.company_id=? AND t.status='in_transit'",
        (cid,)
    ).fetchall()
    total = Decimal('0')
    for qty, cost in rows:
        total += quantize_money(Decimal(str(qty or 0)) * Decimal(str(cost or 0)), currency)
    return total


def rounding_allowance(conn, cid, currency):
    """Half a quantum per unit on hand -- the most the moving average's
    rounding can legitimately separate the books from the shelf value."""
    return Decimal('0.5') * currency_quantum(currency) * on_hand_unit_count(conn, cid)
