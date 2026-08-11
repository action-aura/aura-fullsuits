# Phase 9.5D — Discount and Price-Override Policy

## Trigger predicate

`catalog_for_sales.requires_line_approval(*, unit_price, override_unit_price, discount_amount, line_gross) -> bool` (Milestone 4) — a pure predicate, never itself creates or resolves an approval:

1. **Price override** — `override_unit_price is not None and override_unit_price != unit_price` (any custom price, whether higher or lower than catalog).
2. **Zero-price line** — `override_unit_price == 0` (or the catalog unit price itself is 0 and no override was given).
3. **Discount above threshold** — `discount_amount / line_gross * 100 > 10%` (the single global `DEFAULT_MAX_DISCOUNT_PERCENTAGE_WITHOUT_APPROVAL`).

## Real, named limitation carried from Milestone 4

There is no per-employee or per-plan discount-limit column anywhere in the existing schema (Milestone 1's audit). The 10% global threshold is the smallest real, working policy consistent with what actually exists — not a placeholder silently presented as complete. A genuine per-role/per-plan limit (e.g. SALES gets 5%, a Senior Sales role gets 15%) would require new catalog or RBAC schema, out of this phase's activation-focused scope per the audit's own classification.

## Reason-code taxonomy

`CommercialApproval.reason_code` ∈ `DISCOUNT_ABOVE_LIMIT` / `PRICE_OVERRIDE` / `ZERO_PRICE_LINE` / `OTHER_EXCEPTION` (the fourth reserved for a future non-pricing exception type, e.g. an expired-price override — not triggered by anything in this milestone's scope). Assigned in `quotes.py::add_quote_line()`: price-override branches to `PRICE_OVERRIDE` or `ZERO_PRICE_LINE` depending on whether the overridden price is exactly zero; anything else that triggered the predicate (necessarily the discount-threshold case, since override and zero-price are already handled) becomes `DISCOUNT_ABOVE_LIMIT`.

## Who approves

`pricing.override` (Milestone 1's audit: pre-seeded, granted to no role except via `SUPER_ADMIN`'s wildcard) governs custom pricing generally; the actual per-approval decision goes through `decide_approval()`, which enforces the self-approval block independent of permission — a `pricing.override` holder still cannot approve their own request.
