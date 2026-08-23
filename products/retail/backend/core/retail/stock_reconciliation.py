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

SCHEMA v15 (launch-readiness Phase 3, docs/launch-readiness/
phase3-ledger-truth.md) turned the contract above from a docstring into
something the database itself enforces. The migration seeds one
`opening_count` movement carrying the RESIDUAL -- `quantity_on_hand` minus
whatever the ledger already accounts for at that key -- for every key the
ledger cannot yet explain, and then REFUSES to advance `PRAGMA user_version`
if any repairable row still disagrees. So on a v15 install, `compute_drift`
returning rows means a writer is broken, not that the ledger never knew.

THAT DISTINCTION IS THE WHOLE BASIS ON WHICH THIS MODULE IS ALLOWED TO LOWER
A STOCK FIGURE, and it is why `repair_drift` carries two refusals that read
like paranoia and are not:

  * `SKIP_LEDGER_NOT_ESTABLISHED` -- before v15 has passed, a cache that
    claims MORE than the ledger is the ordinary state of every legacy shop
    (the stock predates the movement table), not evidence of a bug. Trusting
    the ledger there answers "your stock is what you have sold since we
    started recording", which is a negative number.
  * `SKIP_NO_LEDGER_HISTORY` -- "the ledger says nothing" is not "the ledger
    says zero".

Both exist because the measured failure was not hypothetical: a shop holding
[112, 57, 7] was refused by the gate, followed the refusal's own recovery
advice, and came out holding [-8, -3, -1] with a clean audit trail saying
"repaired 3, skipped 0".

Known pre-existing drift source, FIXED AT ITS SOURCE rather than
special-cased here: `database/schema.py::_seed_retail` used to insert demo
`inventory_balances` rows with no matching opening movement and then post
`sale_out` movements against them, so a demo-seeded database reported drift
for every seeded product and could not have passed v15's gate. It now writes
the opening declaration those sales were made from -- the same residual v15
infers -- which changes no balance and leaves this module's report honest.
Note the shape of that fix: the inconsistency was removed from the data that
was actually wrong, never suppressed in the report -- suppressing it here
would be exactly the kind of silent papering-over this module exists to end.
"""
from __future__ import annotations

import json

# Movement quantities are REAL. Two paths that should agree can still differ
# by an IEEE-754 ulp or two after enough float addition (a balance is
# accumulated by repeated `quantity_on_hand + ?` UPDATEs, the ledger by one
# SUM), and reporting 1e-13 as "drift" would bury the real rows. Anything at
# or below a thousandth of a unit is treated as agreement -- far below the
# smallest quantity any real product is sold in, and far above float noise.
DEFAULT_TOLERANCE = 0.0005

#: The schema version at which `inventory_movements` became able to reproduce
#: `inventory_balances` -- see database/schema.py's RETAIL_SCHEMA_VERSION v15
#: comment and `_migrate_seed_opening_counts_and_gate_drift`.
#:
#: Pinned as a literal rather than imported from `database.schema`, and NOT
#: the same thing as RETAIL_SCHEMA_VERSION. It names the version AT WHICH the
#: ledger became authoritative, which is a fact about history and never moves
#: again; RETAIL_SCHEMA_VERSION is today's number and will. Importing schema
#: here would also close an import cycle -- schema.py imports `compute_drift`
#: from this module.
LEDGER_ESTABLISHED_SCHEMA_VERSION = 15

#: Why a drifted row was left alone. All four are refusals, not failures, and
#: all four are shared vocabulary rather than private to one function:
#: `compute_drift` classifies with the first two, `repair_drift` skips with
#: all four, and v15's gate excludes exactly the rows the first two describe.
#: One name per reason, in one place, is what stops the report and the repair
#: quietly disagreeing about why a shop's stock was left alone.
SKIP_NO_BRANCH = 'no_branch'          # NULL-branch movements: no balance row can hold them
SKIP_NO_PRODUCT = 'no_product'        # the product row is gone: no movement row can be written
SKIP_NO_LEDGER_HISTORY = 'no_ledger_history'   # the ledger has never heard of this key
SKIP_LEDGER_NOT_ESTABLISHED = 'ledger_not_established'  # pre-v15: the ledger is not yet truth


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
#
# `product_exists` is a bare EXISTS on products(id) with NO company filter,
# deliberately different from the `p` join beside it. The `p` join is for
# NAMING the row and is company-scoped like every other read in this
# codebase; this probe answers a different question -- "can a movement row
# for this key be written at all?" -- and that is decided by
# `inventory_movements`' FOREIGN KEY to products(id), which knows nothing
# about companies. A balance whose product row is GONE can never be
# explained by any ledger write, and conflating "cannot be named" with
# "cannot be reconciled" would have put the two on the wrong side of the
# gate. See `repairable` below.
_DRIFT_SQL = """
SELECT k.product_id                        AS product_id,
       k.branch_id                         AS branch_id,
       p.name                              AS product_name,
       p.sku                               AS sku,
       br.name                             AS branch_name,
       COALESCE(b.quantity_on_hand, 0)     AS stored_balance,
       COALESCE(m.ledger_total, 0)         AS ledger_balance,
       EXISTS (SELECT 1 FROM products px
                WHERE px.id = k.product_id) AS product_exists
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
                            import signature, and also the ordinary shape of
                            every pre-v15 shop), negative means less
        repairable       -- False when NO write to this database could ever
                            reconcile the row, for either of the two
                            STRUCTURAL reasons below; see blocked_reason
        blocked_reason   -- None when repairable, otherwise SKIP_NO_BRANCH
                            or SKIP_NO_PRODUCT

    TWO STRUCTURAL IMPOSSIBILITIES, NOT ONE. `repairable` began life as
    `branch_id is not None` and that was half the predicate:

      * SKIP_NO_BRANCH -- a legacy NULL-branch movement.
        `inventory_balances.branch_id` is NOT NULL, so no balance row could
        ever hold it.
      * SKIP_NO_PRODUCT -- a balance whose product row is gone.
        `inventory_movements.product_id` has a FOREIGN KEY to products(id)
        and `schema._conn()` turns enforcement ON, so no movement row can
        ever be written for it either. v15's seeder skips it for exactly
        that reason -- and while this predicate still called it repairable,
        the gate refused the install, `repair_drift` refused the same row
        for SKIP_NO_LEDGER_HISTORY, and the install had NO SUPPORTED EXIT:
        measured over three launch/repair/launch cycles, `user_version`
        stayed 14, 14, 14. That is the v13 duplicate-uid wedge reached from
        a third direction. A row nothing can fix must not be allowed to
        block a migration; it belongs in a queue (`orphan_balances`), the
        same answer NULL-branch rows already got.

    Both classes are still REPORTED -- this function is the report, and
    hiding a disagreement because the repair cannot act on it is how a
    screen ends up quieter than the database.
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
        if row['branch_id'] is None:
            blocked = SKIP_NO_BRANCH
        elif not row['product_exists']:
            blocked = SKIP_NO_PRODUCT
        else:
            blocked = None
        drifted.append({
            'product_id': row['product_id'],
            'product_name': row['product_name'],
            'sku': row['sku'],
            'branch_id': row['branch_id'],
            'branch_name': row['branch_name'],
            'stored_balance': stored,
            'ledger_balance': ledger,
            'drift': drift,
            'repairable': blocked is None,
            'blocked_reason': blocked,
        })
    return drifted


def unassigned_movements(conn, company_id):
    """Legacy movements that belong to no branch -- the "needs assignment"
    queue schema v15 leaves behind rather than guessing.

    `inventory_balances.branch_id` is NOT NULL, so a movement whose
    `branch_id` is NULL has no balance row that could ever hold it: it can
    never be reconciled, and v15's gate deliberately excludes it instead of
    wedging the install out of every future migration. v15 resolves these
    automatically wherever the company has exactly ONE branch (one place
    they can belong is not a guess); what reaches this queue is what was
    genuinely ambiguous, and an owner has to say where it happened.

    Computed live, never stored. A count written down at migration time is
    wrong the moment the owner assigns the first row, and a queue that
    disagrees with the thing it is a queue OF is worse than no queue.

    Read-only, same as `compute_drift`: it opens no transaction and writes
    nothing.
    """
    rows = conn.execute(
        "SELECT m.id            AS movement_id, "
        "       m.product_id    AS product_id, "
        "       p.name          AS product_name, "
        "       p.sku           AS sku, "
        "       m.movement_type AS movement_type, "
        "       m.quantity      AS quantity, "
        "       m.reference     AS reference, "
        "       m.created_at    AS created_at "
        "FROM inventory_movements m "
        "LEFT JOIN products p ON p.id=m.product_id AND p.company_id=? "
        "WHERE m.company_id=? AND m.branch_id IS NULL "
        "ORDER BY p.name, m.id",
        (company_id, company_id),
    ).fetchall()
    return [{
        'movement_id': row['movement_id'],
        'product_id': row['product_id'],
        'product_name': row['product_name'],
        'sku': row['sku'],
        'movement_type': row['movement_type'],
        'quantity': float(row['quantity'] or 0),
        'reference': row['reference'],
        'created_at': row['created_at'],
    } for row in rows]


def orphan_balances(conn, company_id):
    """Balances whose product row is gone -- the second "needs a human" queue
    schema v15 leaves behind rather than wedging the install.

    `inventory_movements.product_id` has a FOREIGN KEY to products(id), so
    NO movement can be written for these keys: v15 cannot seed them an
    opening count, and `repair_drift` cannot rewrite them from a ledger that
    can never exist. Before this queue they were counted as repairable, the
    gate refused the install over them, and nothing an operator could run
    changed that -- three launch/repair/launch cycles left `user_version` at
    14 every time. An install must ALWAYS have a supported way forward, so
    they are excluded from the gate and surfaced here instead.

    THE SUPPORTED EXITS, both owner decisions rather than migration guesses:
    re-create the product under its original id (the stock comes back with
    its name and the balance becomes ordinary and reconcilable), or delete
    the stray balance row (the stock was already unsellable -- no screen can
    show a product that does not exist -- so this only stops it inflating
    valuation). Neither is something a migration may pick: one asserts a
    product exists, the other destroys a record of stock.

    No current code path produces such a row -- `delete_product` is a soft
    `status='inactive'` and `demo_wipe` clears balances before products --
    but FK enforcement was OFF everywhere before Wave 0/AUDIT-016, so an old
    database can carry one.

    Computed live, never stored, read-only: same contract as
    `unassigned_movements` and for the same reason.
    """
    rows = conn.execute(
        "SELECT b.product_id      AS product_id, "
        "       b.branch_id       AS branch_id, "
        "       br.name           AS branch_name, "
        "       b.quantity_on_hand AS quantity_on_hand "
        "FROM inventory_balances b "
        "LEFT JOIN branches br ON br.id=b.branch_id AND br.company_id=? "
        "WHERE b.company_id=? "
        "  AND NOT EXISTS (SELECT 1 FROM products p WHERE p.id=b.product_id) "
        "ORDER BY b.product_id, b.branch_id",
        (company_id, company_id),
    ).fetchall()
    return [{
        'product_id': row['product_id'],
        'branch_id': row['branch_id'],
        'branch_name': row['branch_name'],
        'quantity_on_hand': float(row['quantity_on_hand'] or 0),
    } for row in rows]


def _ledger_is_established(conn):
    """Has this database passed v15's gate?

    `PRAGMA user_version` is the only honest answer available. v15 is the
    step that seeds every unexplained key its opening count AND refuses to
    advance the marker until the ledger reproduces the cache, so the marker
    reaching 15 is precisely the statement "on this database,
    inventory_movements can account for inventory_balances". Below 15 it
    cannot, and no amount of looking at an individual row can tell the
    difference between a writer that dropped a movement and a shop whose
    stock simply predates the ledger.
    """
    return (conn.execute('PRAGMA user_version').fetchone()[0]
            >= LEDGER_ESTABLISHED_SCHEMA_VERSION)


def repair_drift(conn, company_id, tolerance=DEFAULT_TOLERANCE, actor_user_id=None):
    """Overwrite every drifted cached balance with its ledger total, in one
    transaction of its own, with its own audit row.

    Writes only `inventory_balances`, and only the rows `compute_drift`
    already flagged. Never touches `inventory_movements` -- see this
    module's docstring for why a correcting movement would be the wrong
    repair.

    TAKES ITS OWN `BEGIN IMMEDIATE` (Phase 3). It previously took none and
    its docstring instructed the caller to supply one, together with the
    caller's own audit_log writes. Both halves of that contract mattered --
    without the write lock held across the recompute AND the overwrite, a
    sale committing between them is counted into the ledger total that was
    read and then erased by the balance that was written, so the repair
    creates fresh drift; and an audit-less repair is a stock rewrite nobody
    can trace. Nothing enforced either half, and an unenforced contract
    about writing to stock is a bad trade: the next caller (a sync batch, a
    maintenance script, an operator recovering a v15 gate failure from a
    Python prompt) has no route through the reviewed one.

    A caller that has ALREADY opened a transaction keeps it -- the lock is
    taken here only when there is none. Joining the caller's transaction
    preserves the identical guarantee (one atomic unit spanning recompute,
    overwrite and audit) where opening a second `BEGIN` would simply raise,
    which would have broken every existing caller to enforce a rule they
    were already following.

    FOUR KINDS OF ROW ARE REFUSED, and all four land in `skipped` carrying a
    `skip_reason`. Two are structural (nothing could ever fix them) and two
    are about whether the ledger has earned the right to overwrite a shelf:

      * `SKIP_NO_BRANCH` -- a NULL-branch movement. There is no balance row
        that could hold it (`inventory_balances.branch_id` is NOT NULL); see
        `unassigned_movements`.

      * `SKIP_NO_PRODUCT` -- the product row is gone, so no movement can be
        written for the key and no ledger total for it can ever exist; see
        `orphan_balances`.

      * `SKIP_LEDGER_NOT_ESTABLISHED` -- the database has not yet passed
        v15's gate AND this row's drift is POSITIVE, i.e. the repair would
        LOWER the balance. THIS IS THE ONE THAT WAS MISSING, and its absence
        was measured, not theorised: a shop holding [112, 57, 7] was refused
        by v15's gate, followed the refusal's own recovery advice, and came
        out holding [-8, -3, -1] -- because every key had sales and no
        opening row, so its "ledger total" was the sum of what it had SOLD.
        `SKIP_NO_LEDGER_HISTORY` did not catch it: those keys HAD history.
        Before v15 has passed, a cache claiming more than the ledger is the
        ordinary state of every legacy shop, not evidence of a bug, and the
        ledger total is a floor on nothing. Note the asymmetry, which is the
        point: NEGATIVE drift is still repaired at any version, because
        raising a balance to a total the ledger can prove arrived destroys
        no stock -- and that is what keeps the v15 gate's documented
        recovery working instead of merely safe.

      * `SKIP_NO_LEDGER_HISTORY` -- a balance whose (product, branch) has NO
        movement rows at all. "The ledger says nothing" is not "the ledger
        says zero", and the difference is a shop's entire opening stock.
        Kept as well as the guard above rather than folded into it: it holds
        on a POST-v15 database too, where a balance inserted with no
        movement behind it is still a key the ledger has never heard of.

    Returns {'repaired': [...], 'skipped': [...]} using the same row dicts
    compute_drift returns, so the caller can audit and report exactly what
    moved.
    """
    # `in_transaction` is False only in autocommit -- Python's sqlite3 opens
    # an implicit transaction on the caller's first DML, so this also covers
    # a caller that wrote something before calling in without an explicit
    # BEGIN: its work and this repair commit together, which is the outcome
    # it would have wanted.
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        # Read once, outside the loop: it is a property of the DATABASE, not
        # of a row, and re-reading it per row would invite the thought that
        # it could change mid-repair.
        established = _ledger_is_established(conn)
        drifted = compute_drift(conn, company_id, tolerance)
        repaired, skipped = [], []
        for row in drifted:
            if not row['repairable']:
                # `blocked_reason` comes straight from compute_drift so the
                # report and the repair can never disagree about WHY a row
                # was left alone -- a second classification here would be a
                # second thing to get out of step.
                skipped.append(dict(row, skip_reason=row['blocked_reason']))
                continue
            if row['drift'] > 0 and not established:
                skipped.append(dict(row, skip_reason=SKIP_LEDGER_NOT_ESTABLISHED))
                continue
            if not _has_ledger_history(conn, company_id, row['product_id'], row['branch_id']):
                skipped.append(dict(row, skip_reason=SKIP_NO_LEDGER_HISTORY))
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
        _write_repair_audit(conn, company_id, repaired, skipped, actor_user_id)
        if owns_transaction:
            conn.commit()
    except Exception:
        # Only ever roll back a transaction this function opened. Rolling
        # back the caller's would discard work this function never saw.
        if owns_transaction:
            conn.rollback()
        raise
    return {'repaired': repaired, 'skipped': skipped}


def _has_ledger_history(conn, company_id, product_id, branch_id):
    """Does this (product, branch) have ANY movement row at all?

    Deliberately not folded into `compute_drift`'s SQL as another column:
    `compute_drift` is the REPORT and must keep saying "these two numbers
    disagree" about every row, including the ones the repair will refuse.
    Hiding a disagreement from the report because the repair cannot act on
    it is how a screen ends up quieter than the database.
    """
    return conn.execute(
        "SELECT 1 FROM inventory_movements "
        "WHERE company_id=? AND product_id=? AND branch_id=? LIMIT 1",
        (company_id, product_id, branch_id),
    ).fetchone() is not None


def _write_repair_audit(conn, company_id, repaired, skipped, actor_user_id):
    """One summary row per repair call, written inside the repair's own
    transaction so a repair that lands without a trace is impossible.

    `user_id` is whatever the caller passed and NULL otherwise -- a repair
    run from a maintenance prompt was performed by nobody the database can
    name, and inventing an attribution for it would be worse than the honest
    blank. The route above this still writes its per-balance
    `STOCK_RECONCILED` rows: this is a different granularity (one row per
    call, with the refusals counted), under a different action, so neither
    trail hides the other.
    """
    if not (repaired or skipped):
        return
    conn.execute(
        "INSERT INTO audit_log (company_id,user_id,action,entity,entity_id,details) "
        "VALUES (?,?,?,?,?,?)",
        (company_id, actor_user_id, 'STOCK_RECONCILE_REPAIR', 'inventory_balances', None,
         json.dumps({
             'repaired_count': len(repaired),
             'skipped_count': len(skipped),
             'net_drift_repaired': round(sum(r['drift'] for r in repaired), 4),
             'skipped_reasons': sorted({r['skip_reason'] for r in skipped}),
         }, sort_keys=True)),
    )
