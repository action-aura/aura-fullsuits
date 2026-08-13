# Phase 9.5D — Milestone 4: Catalog and Commercial Pricing

`app/commercial_sales/catalog_for_sales.py` — read-only wrapper over the existing catalog authority (`app/catalog/`, `app/models/catalog.py`, Milestone 1's audit: FULLY IMPLEMENTED AND REUSABLE). No mutating path; no second Product/Plan/Addon authority.

## `describe_plan_for_sale(plan_id, as_of=None) -> dict | None`

Returns `None` (never a stale/cached fallback) unless: parent `Product.is_sellable and is_active`, `Plan.effective_date <= as_of < retirement_date` (or no retirement date), and a current `PlanPrice` row (`effective_until IS NULL`) exists. Returned shape includes `price_version_id`/`unit_price`/`currency` (the exact snapshot fields Milestone 5's Quote-line creation must copy), `supported_platform_codes`, `entitlement_summary`, and discount/override policy flags.

## `describe_addon_for_sale(addon_id) -> dict | None`

Same shape, gated on `availability_status == "AVAILABLE"` (the real enum value — not `"ACTIVE"`, verified against `app/catalog/services.py::ALLOWED_ADDON_STATUSES = ("DRAFT", "PLANNED", "PILOT", "AVAILABLE", "RETIRED")` after an initial wrong assumption was caught by the test suite). **Named limitation**: `Addon` has no `PlanPrice`-style versioned price history — `addon.id` itself stands in for `price_version_id` since there's no separate historical price row to snapshot against; an Addon price change is not individually versioned the way Plan prices are.

## Discount/approval policy — real, minimal, honestly scoped

The existing catalog schema has no per-plan/per-addon/per-employee discount-limit column (Milestone 1's audit). Introducing one would itself be new catalog schema, outside this read-only module's scope. The real implementation: a single global `DEFAULT_MAX_DISCOUNT_PERCENTAGE_WITHOUT_APPROVAL = 10%` threshold — `requires_line_approval()` returns `True` for any price override, any zero-price line, or any discount exceeding that percentage of the line's gross. **Named limitation**: a true per-employee or per-plan discount limit is not implemented this phase; the global threshold is the smallest real, working policy consistent with what the existing schema actually supports. Milestone 6 consumes this predicate to decide whether a `CommercialApproval` row is required — it does not itself create or resolve one.

## Employee cannot fabricate a catalog item through a Quote line

Every Quote/SalesOrder line's `plan_id`/`addon_id`/`price_version_id`/`unit_price` must come from a `describe_plan_for_sale()`/`describe_addon_for_sale()` call at line-creation time — never accepted as opaque client-supplied values matched against nothing. A `None` result (inactive/expired/not found) is a hard rejection at the service layer (Milestone 5), not silently ignored.

## Tests

`tests/test_phase9_5d_catalog_for_sales.py` — 13 tests: sellable/not-sellable boundary cases (no current price, retired, not-yet-effective, inactive product, nonexistent plan) for both Plan and Addon, plus the `requires_line_approval()` predicate's 5 real branches (normal line, price override, zero price, discount within/exceeding threshold).
