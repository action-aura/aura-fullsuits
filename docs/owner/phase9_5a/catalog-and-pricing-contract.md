# Phase 9.5A Milestone 10 — Catalog, Pricing, and Device Policy Contract

## Fully reused (zero new catalog tables)

`Product`, `Platform`, `ProductPlatform`, `Plan`, `PlanPrice` (effective-dated history), `Addon`,
`EntitlementDefinition` — all real, existing, unchanged. `device_policy_profiles`/
`device_policy_platform_rules` (Milestone 3) are the one addition, and they hang off `Plan`, not a
rewrite of the catalog itself.

## Employee-facing catalog read contract

`GET /api/operations/v1/catalog/plans?product_id=&active_only=true` — returns only `Plan` rows where
`lifecycle_status` is in the real active set and `Plan.effective_date <= today <
(retirement_date OR infinity)`, each with its **current** `PlanPrice` (the row with the latest
`effective_from <= today` and no `effective_until` or a future one) — never a historical price, and
never all historical prices in the employee-facing list response (available only via the admin/
management `pricing.view` + explicit history endpoint).

## Price authority (real rule, see `price-authority-rules.md` for the full statement)

Employees select an existing `Plan`/`PlanPrice`/`Addon` by ID — `QuoteLine`/`SalesOrderLine`/
`CommercialInvoiceItem` (Milestone 11) always snapshot `plan_id`, `price_version_id` (the specific
`PlanPrice.id` used, not just the amount), `unit_price`, and `description` at creation time. No line
item ever accepts a client-supplied `unit_price` unless the caller holds a new `pricing.override`
permission (not granted to SALES by default — Milestone 17) and the override amount + reason are
captured as distinct auditable fields (`overridden_unit_price`, `override_reason`), never silently
replacing the catalog price in the same field.

## What's explicitly deferred

Maximum-permitted-discount enforcement, "requires management approval above X% discount" workflow —
the schema has the `overridden_unit_price`/`override_reason` fields ready, but the approval-gate logic
itself (a `quotes.approve`-style workflow branch keyed on discount size) is deferred to whichever later
phase implements the full Quote/Order UI — this phase proves the schema can represent it, not that the
gate is live.
