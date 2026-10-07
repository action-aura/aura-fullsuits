"""Aura Retail -- REHEARSAL-1007 B6: a customer's "total spent" is the same on every till.

THE DEFECT, measured in the two-till rehearsal: the Customers screen showed 10.440 on one
computer and 1.450 on the other for the SAME customer. `customers.total_spent` was a
running total bumped only by sales and returns rung on THIS computer; a sale rung on the
other computer reached this one as a synced `sales` row but never touched the column.

THE RULE: the column is a CACHE of what the shop's own rows say -- the sum of the
customer's sales, less the refund value of the returns made against them (the very
definition the old running total used: create_sale added `sales.total`, create_return
subtracted the refund it also stores as `returns.refund_amount`), never below zero.
Refreshed after every local sale/return and after every synced sale/return is applied, so
every computer converges on the same figure for the same rows -- exactly the rule
`loyalty.refresh_points` already applies to `loyalty_points`: it REPLACES the cache, never
bumps `row_version`, queues no customer event (the figure is derived from rows that sync on
their own, so shipping it as a customer edit would be a second, conflicting source).

B6b (schema v49) -- HISTORY FROM A PREVIOUS SYSTEM. B6 first handled an imported total with
"a customer with NO sale row keeps whatever the column holds", which only lasted until the
customer's first sale here, when the derived figure replaced it. The history now has its own
column, `customers.total_spent_baseline` (written by the importer: the figure the person typed
IS the history before this system), and the cache is

    total_spent = total_spent_baseline + MAX(0, sales - refunds)

The MAX(0, ...) wraps ONLY the derived part, on purpose: a refund larger than what this system
sold (the sale being in the old system) floors that part at zero and can never eat the
imported history. With no baseline (the default 0) this is exactly the B6 figure, and a
customer with no sale rows now shows their baseline -- no special case needed.

sync_service cannot import products code, so it carries a duplicate of the ONE statement
below (`REFRESH_TOTAL_SPENT_SQL`); retail_rehearsal_b6_total_spent_test pins the two equal.

NO IMPORT OF api/ OR general_ledger: nothing here posts or queues anything.
"""
from __future__ import annotations

#: What THIS system's rows explain for one customer: sales less the completed-return refunds,
#: floored at zero. Parameters, in order: (cid, customer) for the sales sum, (cid, customer)
#: for the returns sum. The v49 migration reuses it to split an existing total into
#: "explained here" and "history".
DERIVED_SPEND_SQL = (
    "MAX(0, "
    "COALESCE((SELECT SUM(total) FROM sales WHERE company_id=? AND customer_id=?), 0) "
    "- COALESCE((SELECT SUM(r.refund_amount) FROM returns r JOIN sales s ON s.id=r.sale_id "
    "WHERE s.company_id=? AND s.customer_id=? AND COALESCE(r.status,'completed')='completed'), 0))"
)

#: Parameters, in order: (cid, customer) for the sales sum, (cid, customer) for the returns
#: sum, (cid, customer) for the target row.
REFRESH_TOTAL_SPENT_SQL = (
    "UPDATE customers SET total_spent = COALESCE(total_spent_baseline, 0) + " + DERIVED_SPEND_SQL
    + " WHERE company_id=? AND id=?"
)


def refresh_total_spent(conn, company_id, customer_id):
    """Recompute `customers.total_spent` for one customer: its baseline (history from a
    previous system) plus what its sales and returns here explain. No-op for a falsy
    `customer_id` (a walk-in)."""
    if not customer_id:
        return
    conn.execute(REFRESH_TOTAL_SPENT_SQL,
                 (company_id, customer_id, company_id, customer_id,
                  company_id, customer_id))
