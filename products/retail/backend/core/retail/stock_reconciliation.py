"""Aura Retail -- inventory balance/ledger reconciliation (stock-accuracy sweep).

The thing that was actually missing.

`inventory_balances.quantity_on_hand` is a mutable STORED number. Five
independent code paths in api/retail_api.py and api/import_api.py write it
(sale, return, purchase-order receive, manual adjustment, bulk import), each
one is expected to also append the matching signed row to
`inventory_movements`, and until this module NOTHING anywhere ever checked
that the two still agreed. Every stock bug this codebase has ever had
therefore failed silently and permanently: the balance drifted, no screen
could tell you it had drifted, and no operation existed to put it back.

The contract this module makes explicit and enforceable:

    inventory_movements IS the truth. inventory_balances is a CACHE of
    SUM(inventory_movements.quantity) per (company_id, product_id, branch_id).

Every writer already stores movement quantities SIGNED -- create_sale writes
`-qty` for 'sale_out', create_return `+qty` for 'return_in',
receive_purchase_order `+qty` for 'purchase_in', adjust_stock the signed
adjustment itself, create_product/_handle_retail_products the opening
declaration -- so the truth is a plain SUM, with no per-movement-type sign
table to keep in step with the writers. That is deliberate: a sign table
would be a second place to get wrong.

Two design decisions worth stating, because both are easy to get backwards:

  * REPORT IS THE DEFAULT, REPAIR IS OPT-IN. Drift is evidence of a bug in
    one of the five writers. Silently self-healing it on read would destroy
    that evidence and hide the next regression, so `compute_drift` is
    read-only and the repair is a separate, admin-gated, explicitly
    confirmed operation (see retail_api.py's two routes).

  * REPAIR DOES NOT WRITE A CORRECTING MOVEMENT. It overwrites the cached
    balance with the ledger sum, full stop. Appending a correcting movement
    would move the very total being reconciled against and turn a
    bookkeeping repair into a fabricated stock event that valuation, COGS
    and reorder maths would then treat as real. The audit trail for a
    repair belongs in `audit_log` (the caller writes it), not in the goods
    ledger.

Known pre-existing drift source, deliberately NOT special-cased here:
`database/schema.py::_seed_retail` inserts demo `inventory_balances` rows
with no matching opening movement, so a demo-seeded database legitimately
reports drift for every seeded product. That is a true report about a real
inconsistency in seed data -- suppressing it here would be exactly the kind
of silent papering-over this module exists to end.
"""
from __future__ import annotations

# Movement quantities are REAL. Two paths that should agree can still differ
# by an IEEE-754 ulp or two after enough float addition (a balance is
# accumulated by repeated `quantity_on_hand + ?` UPDATEs, the ledger by one
# SUM), and reporting 1e-13 as "drift" would bury the real rows. Anything at
# or below a thousandth of a unit is treated as agreement -- far below the
# smallest quantity any real product is sold in, and far above float noise.
DEFAULT_TOLERANCE = 0.0005


# Full outer join, SQLite-style: SQLite has no FULL OUTER JOIN, and a key can
# legitimately exist on either side alone -- a balance row with no movements
# behind it (the import/seed bug class), or movements with no balance row at
# all (a balance row deleted, or never created). UNION over both key sets
# then LEFT JOINing each side is the standard rewrite and is what makes both
# of those cases visible instead of silently dropped.
#
# `branch_id IS k.branch_id` rather than `=`: inventory_movements.branch_id
# is nullable (inventory_balances.branch_id is NOT NULL), and `=` would make
# every legacy NULL-branch movement row vanish from its own report. SQLite's
# `IS` is null-safe equality between two arbitrary expressions.
_DRIFT_SQL = """
SELECT k.product_id                        AS product_id,
       k.branch_id                         AS branch_id,
       p.name                              AS product_name,
       p.sku                               AS sku,
       br.name                             AS branch_name,
       COALESCE(b.quantity_on_hand, 0)     AS stored_balance,
       COALESCE(m.ledger_total, 0)         AS ledger_balance
FROM (
    SELECT product_id, branch_id FROM inventory_balances  WHERE company_id=?
    UNION
    SELECT product_id, branch_id FROM inventory_movements WHERE company_id=?
) k
LEFT JOIN inventory_balances b
       ON b.company_id=? AND b.product_id=k.product_id AND b.branch_id IS k.branch_id
LEFT JOIN (
    SELECT product_id, branch_id, SUM(quantity) AS ledger_total
    FROM inventory_movements
    WHERE company_id=?
    GROUP BY product_id, branch_id
) m ON m.product_id=k.product_id AND m.branch_id IS k.branch_id
LEFT JOIN products p ON p.id=k.product_id AND p.company_id=?
LEFT JOIN branches br ON br.id=k.branch_id AND br.company_id=?
ORDER BY p.name, k.branch_id
"""


def compute_drift(conn, company_id, tolerance=DEFAULT_TOLERANCE):
    """Every (product, branch) whose cached balance disagrees with the ledger.

    Read-only: opens no transaction, writes nothing, and is safe to call on
    a live database while sales are being rung up. Returns a list of plain
    dicts (JSON-serialisable as-is, so the route can hand them straight to
    jsonify) sorted by product name, each with:

        product_id, product_name, sku, branch_id, branch_name,
        stored_balance   -- what inventory_balances currently claims
        ledger_balance   -- SUM(inventory_movements.quantity), the truth
        drift            -- stored - ledger; POSITIVE means the balance
                            claims MORE stock than the ledger can account
                            for (the double-received-PO / resurrected-by-
                            import signature), negative means less
        repairable       -- False only for legacy NULL-branch movement rows,
                            which have no balance row that could hold them
                            (inventory_balances.branch_id is NOT NULL)
    """
    rows = conn.execute(
        _DRIFT_SQL,
        (company_id, company_id, company_id, company_id, company_id, company_id),
    ).fetchall()

    drifted = []
    for row in rows:
        stored = float(row['stored_balance'] or 0)
        ledger = float(row['ledger_balance'] or 0)
        drift = stored - ledger
        if abs(drift) <= tolerance:
            continue
        drifted.append({
            'product_id': row['product_id'],
            'product_name': row['product_name'],
            'sku': row['sku'],
            'branch_id': row['branch_id'],
            'branch_name': row['branch_name'],
            'stored_balance': stored,
            'ledger_balance': ledger,
            'drift': drift,
            'repairable': row['branch_id'] is not None,
        })
    return drifted


def repair_drift(conn, company_id, tolerance=DEFAULT_TOLERANCE):
    """Overwrite every drifted cached balance with its ledger total.

    Writes only `inventory_balances`, and only the rows `compute_drift`
    already flagged. Never touches `inventory_movements` -- see this
    module's docstring for why a correcting movement would be the wrong
    repair.

    Deliberately does NOT manage its own transaction: the caller opens
    BEGIN IMMEDIATE around this call together with its own audit_log
    writes, so a partially-applied repair cannot exist, and no writer can
    slip a sale in between the recompute and the overwrite (which would
    write back a total that was already stale by the time it landed).

    Returns {'repaired': [...], 'skipped': [...]} using the same row dicts
    compute_drift returns, so the caller can audit and report exactly what
    moved.
    """
    drifted = compute_drift(conn, company_id, tolerance)
    repaired, skipped = [], []
    for row in drifted:
        if not row['repairable']:
            skipped.append(row)
            continue
        # INSERT OR IGNORE first: a key can come from the movements side
        # with no balance row at all, and that case is precisely one of the
        # drifts worth repairing.
        conn.execute(
            "INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) "
            "VALUES (?,?,?,0)",
            (company_id, row['product_id'], row['branch_id']),
        )
        conn.execute(
            "UPDATE inventory_balances SET quantity_on_hand=? "
            "WHERE company_id=? AND product_id=? AND branch_id=?",
            (row['ledger_balance'], company_id, row['product_id'], row['branch_id']),
        )
        repaired.append(row)
    return {'repaired': repaired, 'skipped': skipped}
