"""
Aura Retail -- promotions resolver, wave 1 (ROADMAP.md's 2026-08-30 "retail
schema v23 CLAIMED for promotions, wave 1" entry; schema v23,
database/schema.py's `_migrate_add_promotions`).

Pure functions only -- no Flask import, no database WRITE, unit-testable on
their own with plain dict/list fixtures. `load_active_promotions` is the one
function that touches a connection, and it only ever SELECTs.

THE INTEGRATION CONTRACT, stated once here rather than scattered across
callers: a promotion never computes money. It resolves to an effective
`discount_pct`, which is then fed through the EXISTING
`core.retail.pricing.calculate_line` -- the only module in this backend
allowed to compute a persisted financial total (see that module's own
docstring). This module must never import `pricing` or do arithmetic on a
currency figure; it hands back a percentage and nothing else.

THE RULE MOST LIKELY TO BE GOT WRONG: best price wins, discounts do not
stack. `resolve_line_discount_pct` returns `max(promo_pct, manual_pct)`,
NEVER the sum -- summing is how a 60% promotion plus a 50% manual discount
becomes 110%, clamps to 100 downstream, and hands the item over for
nothing. See that function's own docstring and
products/retail/tests/retail_promotions_test.py's mutation proof M1.
"""
from __future__ import annotations


def load_active_promotions(conn, company_id, branch_id, now_local):
    """Every promotion currently live for `(company_id, branch_id)` at the
    instant `now_local` names.

    Loaded ONCE PER SALE by the caller (api/retail_api.py's `create_sale`),
    never once per line -- there will be tens of active rules in a real
    shop, not thousands, so re-querying per line would be N wasted round
    trips for zero benefit. The caller passes the SAME list into
    `resolve_line_discount_pct` for every line of the sale.

    A row qualifies when ALL of:
      * `company_id` matches (tenancy -- AUDIT-032C's supplier_id lesson:
        an unscoped read here would let one company's sale discover
        another company's promotion rows, which is a read, but the same
        class of leak this codebase has already shipped once on a write).
      * `status='active'` (a deactivated promotion, DELETE /promotions/<id>
        -- a status flip, not a row delete -- must never apply again).
      * `branch_id IS NULL` (every branch) OR `branch_id` equals the sale's
        own branch.
      * `now_local` falls inside `[starts_at, ends_at]`, treating a NULL on
        either side as unbounded on that side -- a promotion with no
        `starts_at` has always started, one with no `ends_at` never ends.

    `now_local` is the caller's OWN local wall-clock string (the SAME value
    `create_sale` already computes for the sale's own `created_at`,
    `'%Y-%m-%d %H:%M:%S'`), reused rather than re-read here -- this module
    never calls `datetime.now()` itself. A till is explicitly built to keep
    selling while offline (docs/launch-readiness/phase7-offline-ux.md), so
    the device's own clock is the only clock available at the moment a
    promotion's live-ness actually needs deciding; there is no
    authoritative remote clock to consult instead. `starts_at`/`ends_at`
    are stored as TEXT in that SAME sortable `'%Y-%m-%d %H:%M:%S'` shape
    (validated at write time by create_promotion/update_promotion), so a
    plain lexicographic `<=`/`>=` comparison in SQL is correct without ever
    parsing either side into a `datetime`.

    Returns a list of plain dicts (not `sqlite3.Row` -- converted here so
    `resolve_line_discount_pct` and every test fixture can hand this
    function a plain list of dicts with no database involved at all),
    each carrying `id, name, discount_pct, product_id, category_id`.
    """
    rows = conn.execute(
        "SELECT id, name, discount_pct, product_id, category_id, branch_id, "
        "starts_at, ends_at FROM promotions "
        "WHERE company_id=? AND status='active' "
        "AND (branch_id IS NULL OR branch_id=?) "
        "AND (starts_at IS NULL OR starts_at<=?) "
        "AND (ends_at IS NULL OR ends_at>=?)",
        (company_id, branch_id, now_local, now_local),
    ).fetchall()
    return [dict(row) for row in rows]


def resolve_line_discount_pct(promotions, product_id, category_id, manual_pct):
    """Resolve ONE sale line's effective discount percentage against the
    promotions already loaded for this sale (`load_active_promotions`,
    above) and the MANUAL per-line discount the cashier typed.

    Returns `(effective_pct, applied_promotion_or_None)`.

    RESOLUTION ORDER, matching ROADMAP.md's "two rules most likely to be
    got wrong" exactly:

    1. TIER: a product-specific promotion (`promo['product_id'] ==
       product_id`) beats a category promotion (`promo['category_id'] ==
       category_id`, only when `category_id` is not None) on the SAME
       product, regardless of which percentage is larger -- a 10%
       product-specific rule wins over a 50% category-wide one. Every
       promotion row holds exactly one of `product_id`/`category_id` (API-
       validated at create/update time), so a row can only ever land in
       one tier, never both.
    2. Within a tier, the HIGHEST `discount_pct` wins -- covers the (rare,
       but not forbidden) case of two overlapping rules at the same tier.
    3. BEST PRICE WINS, ACROSS THE MANUAL/PROMO SPLIT: the winning tier's
       percentage (0 if no rule matched at all) is compared against
       `manual_pct` with `max()`, NEVER summed. See this module's own
       docstring for why summing is the dangerous mistake.
    4. `applied_promotion` is None whenever the MANUAL discount wins,
       INCLUDING an exact tie -- a promotion that did not itself lower the
       price beyond what the cashier already typed gave nothing away, and
       must not be snapshotted (`sale_item_promotions`) as if it had. It
       is also None, trivially, when no promotion matched at all.
    """
    product_matches = [p for p in promotions if p.get('product_id') == product_id]
    category_matches = (
        [p for p in promotions if category_id is not None and p.get('category_id') == category_id]
        if not product_matches else []
    )
    tier = product_matches or category_matches

    best_promo = max(tier, key=lambda p: p['discount_pct']) if tier else None
    promo_pct = best_promo['discount_pct'] if best_promo else 0
    manual_pct = manual_pct or 0

    if promo_pct > manual_pct:
        return promo_pct, best_promo
    # Manual wins, INCLUDING an exact tie (promo_pct == manual_pct): the
    # manual figure alone already accounts for the full charged discount,
    # so crediting the promotion here would snapshot a rule that changed
    # nothing about what the customer paid.
    return manual_pct, None
