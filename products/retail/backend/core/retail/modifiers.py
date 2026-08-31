"""
Aura Retail -- modifiers resolver, wave 1 (ROADMAP.md's 2026-08-31 "retail
schema v26 CLAIMED for restaurant modifiers, wave 1" entry; schema v26,
database/schema.py's `_migrate_add_modifiers`).

Pure functions only -- no Flask import, no database WRITE, unit-testable on
their own with plain dict/list fixtures, mirroring core.retail.promotions'
own shape exactly. `load_product_modifier_groups` and
`read_sale_item_modifiers` are the two functions that touch a connection,
and both only ever SELECT.

THE INTEGRATION CONTRACT, mirroring core.retail.promotions: a modifier
selection never computes a persisted financial total. It resolves to an
EFFECTIVE UNIT PRICE, which the caller (api/retail_api.py's `create_sale`)
feeds through the EXISTING `core.retail.pricing.calculate_line` -- the only
module in this backend allowed to compute one. This module must never
import `pricing`; it hands back a price and a list of snapshot rows, and
nothing else touches `sale_items.unit_price`/`line_total`.

A modifier is not a variant and does not share variants' mechanism (design
doc docs/launch-readiness/variants-and-modifiers-design.md, section 2): it
carries no stock, no barcode, no cost movement -- it is a thing you SAY
ABOUT a line, never a thing you count. See database/schema.py's
RETAIL_SCHEMA_VERSION v26 comment for the full design summary.

THE CLAMP (design doc section 4.3): `effective_unit_price =
max(0, base_unit_price + sum(price_delta))`. A negative delta ("small
-0.30") may lower a line, but bad config must never produce a NEGATIVE
line, and must never block a sale -- the identical posture
`core.retail.pricing.clamp_discount_pct` already takes on a discount
outside [0, 100].

REQUIRED VS OPTIONAL (design doc section 4.5): `min_select >= 1` means the
group is REQUIRED wherever it is attached; `max_select` NULL means
unlimited; `min=0,max=1` is optional-single-choice; `min=1,max=1` is a
forced choice. Both directions are load-bearing and mutation-proven in
products/retail/tests/retail_modifiers_test.py: a sale that skips a
required group with no default is refused, AND an ordinary sale (no
groups attached, or every required group satisfied by a default) still
sells with byte-identical behaviour to an install with no modifiers at all
-- ENGINEERING's "prove both directions of anything that both denies and
allows".

THE SNAPSHOT, AND WHY IT IS NEVER RE-READ (design doc section 4.4's
"never re-read for money" rule, ROADMAP.md's v26 entry): `sale_item_
modifiers` records group name/option name/price delta/cost delta AS
APPLIED, at the instant the sale that used them was written.
`read_sale_item_modifiers` below is the ONE sanctioned way anything in
this product (a receipt reprint, a sale-detail screen, a return screen
listing a sale's lines) is meant to read that back -- it reads ONLY the
snapshot columns, never joins `modifier_options`, so an option edited or
deleted after the sale can never change what a past sale is shown to have
charged. See products/retail/tests/retail_modifiers_test.py's mutation
proof M2, which swaps this function's SELECT for a live join and proves
the test that edits an option after the fact goes red.
"""
from __future__ import annotations


class ModifierValidationError(Exception):
    """Raised when a sale line's modifier selection cannot be resolved: an
    unknown/tombstoned/foreign option, an option chosen from a group not
    attached to this product, a per-group selection count outside
    [min_select, max_select], or a required group with no selection and no
    default to fall back on. `.message` NAMES the offending group (design
    doc section 4.3: "Choose a Size for 'Burger'") -- the till has to tell
    the cashier what to fix, never just refuse with a code.
    """
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def load_product_modifier_groups(conn, company_id, product_id):
    """Every LIVE modifier group attached to `product_id`, each carrying its
    own live options.

    Called once PER LINE'S PRODUCT inside create_sale's item loop -- NOT
    hoisted to once-per-sale the way `promotions.load_active_promotions`
    is, because attachment is scoped to one product, not shared across a
    sale's whole line set the way "which promotions are live right now" is.
    A real shop attaches a handful of groups to a handful of products, so
    this is one small indexed join per line, not a catalogue-wide scan.

    TENANCY, independently on every join -- `product_modifier_groups`,
    `modifier_groups` AND `modifier_options` are each filtered to
    `company_id=?` on their own, not merely inherited through the
    product's company. AUDIT-032C's supplier_id lesson, repeated for
    modifiers: an unscoped join here would let a group or option belonging
    to ANOTHER company be attached to or resolved against a product of
    this one, exactly the cross-tenant leak shape this codebase has
    already shipped once on a write. See
    products/retail/tests/retail_modifiers_test.py's tenancy mutation
    proof (M4): dropping the `g.company_id = pmg.company_id` join
    condition below is what that proof mutates.

    Returns a list of dicts, each:
        {'id', 'name', 'min_select', 'max_select',
         'options': [{'id', 'name', 'price_delta', 'cost_delta',
                      'is_default'}, ...]}

    Deleted/inactive groups or options -- and anything belonging to
    another company -- are never returned. An install with no modifiers
    configured at all gets `[]` from one empty indexed probe: the same
    "invisible unless opted in" cost class
    `promotions.load_active_promotions` already established for a
    no-promotions install (design doc section 4.11).
    """
    groups = conn.execute(
        "SELECT g.id, g.name, g.min_select, g.max_select "
        "FROM product_modifier_groups pmg "
        "JOIN modifier_groups g ON g.id = pmg.group_id AND g.company_id = pmg.company_id "
        "WHERE pmg.company_id=? AND pmg.product_id=? "
        "AND pmg.deleted_at_utc IS NULL "
        "AND g.deleted_at_utc IS NULL AND g.status='active' "
        "ORDER BY pmg.sort_order, g.name",
        (company_id, product_id),
    ).fetchall()

    result = []
    for g in groups:
        options = conn.execute(
            "SELECT id, name, price_delta, cost_delta, is_default FROM modifier_options "
            "WHERE company_id=? AND group_id=? "
            "AND deleted_at_utc IS NULL AND status='active' "
            "ORDER BY sort_order, name",
            (company_id, g['id']),
        ).fetchall()
        result.append({
            'id': g['id'], 'name': g['name'],
            'min_select': g['min_select'], 'max_select': g['max_select'],
            'options': [dict(o) for o in options],
        })
    return result


def resolve_line_modifiers(base_unit_price, groups, selected_option_ids):
    """Resolve ONE sale line's modifier selection against the groups
    ACTUALLY attached to its product (`groups`, `load_product_modifier_
    groups`'s return) and the option ids the client submitted
    (`selected_option_ids` -- client-supplied INTENT only, an id list,
    NEVER a price; AUDIT-002/003's "server is the sole financial
    authority" contract, unchanged by this feature).

    Returns `(effective_unit_price, snapshot_rows)` on success, where
    `snapshot_rows` is a list of dicts ready to become `sale_item_
    modifiers` rows, in selection order:
        {'modifier_option_id', 'group_name', 'name', 'price_delta', 'cost_delta'}

    Raises `ModifierValidationError` (see its own docstring for the
    message contract) when:
      * a selected id does not resolve to any option in `groups` -- this
        covers unknown, tombstoned, AND cross-tenant/cross-product ids
        alike, because `load_product_modifier_groups` already scoped
        `groups` to this product/company; anything not in it is, from
        this line's point of view, simply not attached here.
      * a group's selection COUNT falls outside [min_select, max_select]
        (`max_select is None` means unlimited, so only the floor binds).
      * a group with `min_select >= 1` (required) receives ZERO
        selections and has no `is_default` option to fall back on.

    DEFAULTS (design doc section 4.9, "the common case is one tap"): a
    required group (`min_select >= 1`) that receives NO explicit selection
    resolves SILENTLY to its `is_default` option, if one exists. This is
    what lets a plain tap -- no modifier payload at all -- still satisfy
    every required group on a product whose defaults are configured, with
    no picker and no server refusal. A required group with no default and
    no selection is refused; see `ModifierValidationError`. See
    products/retail/tests/retail_modifiers_test.py's mutation proof M3,
    which disables the required-group check entirely and proves the
    skipped-required-choice test goes red.

    THE CLAMP (design doc section 4.3): `effective_unit_price =
    max(0, base_unit_price + sum(price_delta))`. See this module's own
    docstring for why: bad config must lower a line, never invert it into
    a negative, and must never itself block a sale (the clamp always
    succeeds; only the validation checks above ever raise). See mutation
    proof M5, which drops the price-delta sum from this calculation
    entirely.

    INVISIBLE UNLESS OPTED IN: `groups == []` (nothing attached to this
    product) with an EMPTY `selected_option_ids` returns
    `(base_unit_price, [])` -- byte-identical to a sale with no modifiers
    feature at all. `groups == []` with a NON-EMPTY `selected_option_ids`
    raises (every id is "not in `groups`" by construction) rather than
    silently dropping the selection -- design doc section 4.11: naming an
    option that resolves to nothing attached is refused, never ignored,
    because silently dropping a "no peanuts" modifier is a safety bug, not
    a pricing one.
    """
    selected_option_ids = list(selected_option_ids or [])

    options_by_id = {}
    group_of_option = {}
    for g in groups:
        for o in g['options']:
            options_by_id[o['id']] = o
            group_of_option[o['id']] = g

    unknown = [oid for oid in selected_option_ids if oid not in options_by_id]
    if unknown:
        raise ModifierValidationError(
            f"Selected modifier option(s) are not available for this product: {unknown}."
        )

    selections_by_group = {}
    for oid in selected_option_ids:
        gid = group_of_option[oid]['id']
        selections_by_group.setdefault(gid, []).append(oid)

    resolved = []  # list of (group_dict, option_dict), selection order within a group
    for g in groups:
        chosen = list(selections_by_group.get(g['id'], []))
        min_select = g['min_select'] or 0
        max_select = g['max_select']

        if not chosen and min_select >= 1:
            default = next((o for o in g['options'] if o['is_default']), None)
            if default is None:
                raise ModifierValidationError(f"Choose a {g['name']} for this item.")
            chosen = [default['id']]

        if len(chosen) < min_select or (max_select is not None and len(chosen) > max_select):
            bound = 'unlimited' if max_select is None else str(max_select)
            raise ModifierValidationError(
                f"'{g['name']}' needs between {min_select} and {bound} selection(s); got {len(chosen)}."
            )

        for oid in chosen:
            resolved.append((g, options_by_id[oid]))

    price_delta_total = sum(o['price_delta'] for _, o in resolved)
    effective_unit_price = max(0.0, float(base_unit_price) + float(price_delta_total))

    snapshot_rows = [
        {
            'modifier_option_id': o['id'],
            'group_name': g['name'],
            'name': o['name'],
            'price_delta': o['price_delta'],
            'cost_delta': o['cost_delta'],
        }
        for g, o in resolved
    ]
    return effective_unit_price, snapshot_rows


def read_sale_item_modifiers(conn, company_id, sale_item_id):
    """Every modifier snapshot recorded for one sale line, AS APPLIED -- the
    sanctioned read path for a receipt reprint, a sale-detail screen, or a
    return screen listing a sale's lines with their modifier sub-text.

    Reads ONLY `sale_item_modifiers` (the immutable snapshot) -- NEVER
    joins `modifier_options` for a name or a price, because an option can
    be edited or deleted after the sale that used it, and this function's
    entire job is to show what the customer was ACTUALLY charged, not what
    `modifier_options` says today. `modifier_option_id` is returned as
    provenance only (audit trail -- "which config row was this," useful
    for a sales-by-modifier report) and must never be fed back into a
    price calculation.

    See products/retail/tests/retail_modifiers_test.py's mutation proof
    M2: swapping the SELECT below for one that joins `modifier_options`
    and reads its live `name`/`price_delta` is exactly the bug this
    function exists to make impossible, and the mutation proof turns the
    "editing an option after the fact" test red.
    """
    rows = conn.execute(
        "SELECT modifier_option_id, group_name_snapshot AS group_name, "
        "name_snapshot AS name, price_delta_snapshot AS price_delta, "
        "cost_delta_snapshot AS cost_delta "
        "FROM sale_item_modifiers WHERE company_id=? AND sale_item_id=? ORDER BY id",
        (company_id, sale_item_id),
    ).fetchall()
    return [dict(r) for r in rows]
