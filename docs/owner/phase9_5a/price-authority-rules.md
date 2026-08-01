# Phase 9.5A — Price Authority Rules

1. Every commercial line item (`QuoteLine`/`SalesOrderLine`/`CommercialInvoiceItem`) stores a snapshot:
   `plan_id` or `addon_id`, `price_version_id` (FK to the exact `PlanPrice`/`Addon` price row used),
   `description` (copied at creation, never a live join to the catalog for display), `quantity`,
   `unit_price` (Numeric, copied from the price version), `discount_amount` (nullable, Numeric),
   `line_total` (server-computed, `Decimal`, never client-supplied).
2. A line item is immutable once its parent document leaves `DRAFT` — correcting a mistake on an
   `ISSUED` invoice means a new document (credit note pattern via `CommercialRefund`, Milestone 11) or
   voiding and reissuing, never an in-place edit of a non-draft line.
3. Historical `PlanPrice` rows are never deleted or overwritten — `PlanPrice.effective_until` is set to
   close a price out; the row itself remains, so every historical invoice's `price_version_id` still
   resolves to a real, readable historical price forever.
4. `pricing.override` permission is required for `overridden_unit_price` to differ from the catalog
   `unit_price` on any line; every such override is a `PRICE_OVERRIDDEN` audit event (Milestone 23)
   carrying both the catalog price and the overridden price.
5. Employees without `pricing.manage`/`catalog.manage` can never create/edit a `Plan`/`PlanPrice`/
   `Addon` row — read-only access to the catalog is the SALES default (already the real, existing RBAC
   posture — `catalog.view` only, confirmed in Milestone 1's audit).
