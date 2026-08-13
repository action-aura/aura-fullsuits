# Phase 9.5D — Duplication Risk Report

Real risks the Milestone 1 audit surfaced, each with an explicit resolution decision. No item below is left ambiguous going into Milestone 2+.

## Risk 1: Building a new Quote/Order/Invoice/Refund model instead of using the existing Phase 9.5A schema

**Risk**: `app/models/commercial_sales.py` already defines `Quote`, `QuoteLine`, `SalesOrder`, `SalesOrderLine`, `CommercialInvoice`, `CommercialInvoiceItem`, `CommercialRefund` — fully fielded, migrated, unreferenced by any code. A developer unaware of this (or assuming "foundation-only" means "needs replacing") could build a second, competing model.

**Resolution**: Build service/route/UI layers directly on top of these exact models. This is the audit's single most important finding — confirmed by the reuse matrix. No new commercial-sales model is introduced this phase.

## Risk 2: Building a generic `Approval` model

**Risk**: Milestone 6 (pricing/discount approvals) could naively introduce a generic `Approval`/`ApprovalRequest` table with a polymorphic `target_type`/`target_id`, since no such model currently exists.

**Resolution**: The codebase has an established, repeated convention instead — `RenewalRequest`, `PendingActivation`, `PilotRecord` each use a document-specific `status` enum + dedicated `approve_*()` function + `*StatusHistory` table. `Quote.status`/`SalesOrder.status` already have their enum columns waiting for exactly this pattern. Milestone 6 follows this per-document convention. A generic polymorphic Approval table would itself be the exact anti-pattern this audit exists to prevent.

## Risk 3: Reimplementing ownership filtering per new model

**Risk**: `apply_ownership_filter()` (`app/leads/ownership.py:24`) currently only branches for `Lead`/`Customer`; a developer building Quote/Order/Invoice ownership checks could write a parallel, slightly-different filter function per model — exactly the class of bug (fail-open ownership checks) that caused a real IDOR in Phase 9.5C.

**Resolution**: Extend `apply_ownership_filter()` itself with new branches for each new model, reusing the exact same function every route calls — never a second, parallel implementation.

## Risk 4: A second idempotency-key table

**Risk**: Given 6+ new mutating operation types (Quote create, Order confirm, Invoice issue, Payment confirm, Refund confirm, Commission payout), a developer could reach for a dedicated idempotency table per operation type.

**Resolution**: `CommercialOperationsIdempotencyKey` is explicitly a shared ledger by design (already used by `LEAD_CONVERSION`). New operations mint a new `OPERATION_CODE` string and reuse the same table — confirmed as the intended pattern by the model's own docstring.

## Risk 5: A second stable-error base class

**Risk**: `commercial_ops/renewal_requests.py`'s `InvalidRenewalTransitionError` already hand-rolls the `StableCodeError` shape without subclassing it (a pre-9.5B-R3 relic). Copying that file as a template for new `QuoteError`/`SalesOrderError`/etc. would propagate a second inconsistent error-class shape.

**Resolution**: New error classes subclass `app/commercial_ops/errors.py::StableCodeError` directly, following `app/leads/errors.py::LeadError`'s pattern — confirmed correct and current. `InvalidRenewalTransitionError` is left as-is (out of scope to fix opportunistically this phase; not blocking).

## Risk 6: Writing Subscription/License rows directly from sales code

**Risk**: The most severe risk in the whole phase, explicitly named in Non-Negotiable Principle 5. Fulfillment code under a new `commercial_sales`/`fulfillment` module could take the shortcut of `db_session.add(Subscription(...))` or `db_session.add(License(...))` directly, since the fields are all visible in the model file.

**Resolution**: Fulfillment orchestration (Milestone 13) calls `issue_license_key()` for licenses and the `create_subscription()`/renewal-request pipeline for subscriptions — verified by the same grep-for-constructor-calls method Phase 9.5C used to prove `leads/conversion.py` never touches commercial-fulfillment models. This check is repeated at Milestone 13's close.

## Risk 7: Placing new code under `commercial_ops/` instead of `commercial_sales/`

**Risk**: `commercial_ops/` is the larger, more active existing module (renewals/pilots/emergency-extensions/device-governance/preflight) and could seem like the natural home for "more commercial operations." It is not — it's a distinct, already-complete domain (Phase 8).

**Resolution**: All new Quote/SalesOrder/Invoice/Refund/Commission service and route code lives under `app/commercial_sales/` (Phase 9.5A's own module name for this domain, currently holding only `device_policy.py`), keeping the Phase 8 renewal/pilot/device domain and the Phase 9.5D sales-document domain cleanly separated, matching the existing module-naming intent.

## Risk 8: Adding new RBAC permission codes for concepts that already exist

**Risk**: A developer unaware of the pre-seeded permission set could add `quote.submit`/`quote_create`/etc. variants, fragmenting the naming convention.

**Resolution**: `quotes.create/approve`, `orders.create/approve`, `invoices.create/issue`, `pricing.override`, `payments.confirm`, `refunds.create/approve`, and the full `commissions.*` set are already seeded and role-mapped in `app/staff/seed_data.py`. Milestone 16 wires routes/services to check these existing codes; it does not re-seed them. Any genuinely new permission need (e.g. `orders.cancel` if not already present) is checked against the seed file before assuming it must be added.
