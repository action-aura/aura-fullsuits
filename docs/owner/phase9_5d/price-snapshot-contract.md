# Phase 9.5D — Price Snapshot Contract

Extends `docs/owner/phase9_5a/price-authority-rules.md` (unchanged, cited not restated) with the Milestone 3 calculation service's own guarantee.

## What's already guaranteed by the existing Phase 9.5A schema (unchanged)

- Every `QuoteLine`/`SalesOrderLine`/`CommercialInvoiceItem` stores `plan_id`/`addon_id`, `price_version_id` (FK to the exact `PlanPrice` row used), `description` (copied at creation, never a live catalog join for display), `quantity`, `unit_price` (copied from the price version at creation).
- `PlanPrice` rows are never deleted or overwritten — `effective_until` closes a price out, the row itself remains forever readable.
- A line item is immutable once its parent document leaves `DRAFT` (correcting a mistake on an `ISSUED` invoice means a new document — `CommercialRefund` — or void-and-reissue, never an in-place edit).

## What Milestone 3 adds: the calculation service has zero catalog dependency

`app/commercial_sales/calculator.py` never queries `Plan`/`PlanPrice`/`Addon` — every function takes the already-resolved `unit_price` (or `override_unit_price`) as a plain `Decimal` argument. This is a structural, not just a policy, guarantee of immutability: there is no code path by which a live catalog price change could reach an already-computed `LineResult`/`DocumentResult`, because the calculator never looks the price up itself — the caller (Quote/Order/Invoice creation service, Milestone 5/8/9) is responsible for resolving the *current* price at line-creation time and passing the resolved value in.

Proven by `test_price_snapshot_immutability_property`: calling `calculate_line()` twice with the identical `LineInput` (same `unit_price` Decimal) always produces the identical `LineResult` — there is no hidden global/catalog state the function could have consulted differently between calls.

## Consuming services' responsibility (Milestones 5/8/9)

When a Quote/SalesOrder/CommercialInvoice line is created:

1. Resolve the *current* `PlanPrice`/`Addon` price row for the selected Plan/Addon (via the existing catalog read service, per Milestone 1's audit — `catalog/services.py`).
2. Record that price row's UUID as `price_version_id` and its `unit_price` value on the new line — both fields are copied once, at creation time.
3. Pass the copied `unit_price` (never a fresh catalog lookup) into `calculate_line()`/`calculate_document()`.
4. Never re-resolve `price_version_id` or `unit_price` on any later read of that same line — the stored values are the permanent historical record.

This means a later `PlanPrice` change (a new price row with a later `effective_from`) has zero effect on any Quote/Order/Invoice line already created — exactly Non-Negotiable Principle 10 ("price snapshots are immutable... historical documents must not change when the current catalog price changes"), enforced structurally by the calculator's own lack of catalog access, not merely by service-layer discipline that could be bypassed by a future careless call site.

## Verification method for later milestones

Each of Milestones 5/8/9's own contract docs will include a test that mutates the live `PlanPrice`/`Addon` price after a Quote/SalesOrder/CommercialInvoice is created and asserts the historical document's stored `unit_price`/`price_version_id`/line totals are unchanged — matching the exact verification method already used elsewhere in this codebase (e.g. Phase 9.5C's `test_converted_customer_defaults_to_converting_actor_when_lead_unassigned`-style direct-reproduction testing) rather than asserting the property only by code inspection.
