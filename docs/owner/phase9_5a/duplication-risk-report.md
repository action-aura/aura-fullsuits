# Phase 9.5A — Duplication Risk Report

## Highest-risk area: Lead vs Customer

**Risk**: building a new `Lead` table without accounting for `Customer.lifecycle_status="LEAD"` would
leave two competing "this is a lead" concepts in the same database, confusing every future dashboard
count and API contract.

**Resolution**: `Lead` is a genuinely new, separate table (pre-qualification pipeline: status history,
assignment, interactions, follow-ups — none of which exist on `Customer` today). `Customer` remains the
post-conversion record, reused as-is. `Customer.lifecycle_status="LEAD"` (the default for every
existing and any future directly-created `Customer` row) is left untouched — not renamed, not
backfilled, not treated as authoritative lead data — and `LeadConversionService` is the only code path
that creates a `Customer` row from this phase's new Lead pipeline, always setting
`lifecycle_status="ACTIVE"` at creation. No migration touches existing `Customer` rows. Zero risk to
Phase 5-8 behavior (confirmed via full-codebase grep: nothing branches on `lifecycle_status=="LEAD"`
specifically; only `=="ARCHIVED"` is checked, in one template and one test, both unaffected).

## Payment vs Commercial Invoice

**Risk**: `PaymentRecord` already exists; a naive "Payment" model for the new commercial-invoice flow
would duplicate it.

**Resolution**: New `CommercialInvoice`/`CommercialInvoiceItem` models represent the *document*
(line-item snapshot, states DRAFT/ISSUED/PARTIALLY_PAID/PAID/VOID/REFUNDED/PARTIALLY_REFUNDED).
Confirmation of payment against that invoice reuses the existing `PaymentRecord` (already has
`subscription_id`; extended with a nullable `commercial_invoice_id` FK, additive column, not a new
payment system). Refunds are a genuinely new, separate `CommercialRefund` record (no existing refund
document exists — confirmed in the audit).

## Notification vs new alerting

**Risk**: building a new alert/notification table for dashboard or daily-report alerting.

**Resolution**: `InternalNotification` already exists, already has `notification_type`/`severity`/
`dedup_key` (idempotency) — reused directly for any new alert type this phase's daily-snapshot or
device-policy work needs, via new `notification_type` values only.

## Management note vs customer note

**Risk**: conflating the existing per-customer `CustomerNote` with the new standalone management note.

**Resolution**: kept genuinely separate — `CustomerNote` is always customer-scoped (unchanged);
`SharedManagementNote` (new) is never customer-scoped, has its own visibility/status/pin model. No
shared table, no confusing overlap; a UI may later choose to display both together, but the data model
stays distinct.

## Device-slot / device-limit

**Risk**: the highest-stakes duplication risk in this entire phase — `DeviceSlotException`,
`resolve_effective_device_limit()`, `License.device_limit`, `Subscription.device_allowance` are all
real, hardened, physically-proven-in-production (Phase 8V-P9) commercial-enforcement code. Any new
"device policy" table that recomputed or shadowed this logic would be a severe regression risk.

**Resolution**: the new `device_policy_profiles`/`device_policy_platform_rules` layer (Milestone 3) is
strictly a *catalog-level default* and *per-platform sub-limit* concept that sits **above**
`License.device_limit` — it never replaces, recomputes, or bypasses `resolve_effective_device_limit()`.
The existing function remains the single source of truth for "how many devices may this license have
right now"; the new layer only answers "what platform combinations are the plan allowed to permit" and
"should the Nth activation require approval" — genuinely new questions, not overlapping ones.

## RBAC roles

**Risk**: creating new roles (e.g. "MANAGER") when SUPER_ADMIN already covers Bahaa/Awab's needs.

**Resolution**: no new roles created. Both Bahaa and Awab are separate `SUPER_ADMIN` accounts (already
supported — RBAC is account-independent). Only new *permissions* are added to existing roles.
