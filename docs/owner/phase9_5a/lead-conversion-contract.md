# Phase 9.5A Milestone 6 — Lead Conversion Contract

## Real reuse found: duplicate detection already exists

`owner/app/customers/services.py`'s `find_duplicate_candidates(legal_name, commercial_registration_reference,
email, phone)` already implements exactly the "duplicate-aware" requirement — matches on
case-insensitive legal name, commercial-registration reference, or a `CustomerContact.business_email`
join. `LeadConversionService` calls this directly; no new duplicate-detection logic is built.

## `LeadConversionService.convert(lead, *, actor_staff_user_id, existing_customer_id=None, idempotency_key)`

1. **Transactional**: one DB transaction; either the full conversion (Customer creation/link + Lead
   update + audit event) commits, or none of it does.
2. **Idempotent**: keyed the same way `issue_license_key()` already does it (an `idempotency_key`
   checked against a small ledger table before any write) — a retried conversion request with the same
   key returns the already-converted result, never a second `Customer`/second conversion event.
3. **Duplicate-aware**: calls `find_duplicate_candidates()` using the Lead's own fields (`organization_or_prospect_name`
   as `legal_name`, contact email/phone) before creating anything. If candidates exist and
   `existing_customer_id` was not explicitly provided by the caller (i.e., the employee/manager hasn't
   already confirmed "yes, link to this existing customer"), the service returns a
   `DUPLICATE_CUSTOMER` result (Milestone 19's error catalog) instead of creating a new row — the
   caller must re-submit with `existing_customer_id` set to proceed deliberately.
4. **Permission-controlled**: requires `leads.convert` (Milestone 17).
5. **Preserves attribution**: the new (or linked existing) `Customer.assigned_sales_staff_id` is set
   from the Lead's own `assigned_employee_profile_id`'s underlying `staff_user_id` (Customer's existing
   ownership column expects a `StaffUser` id, not an `EmployeeProfile` id — the service resolves this,
   documented explicitly so the foreign-key type mismatch isn't a surprise during implementation).
   `Customer.converted_from_lead_id` (new column) always points back to the source Lead, preserving
   both creating- and assigned-employee attribution permanently, even if the Customer's assignment
   later changes.
6. On success: `Lead.status = "CONFIRMED"`, `Lead.converted_at = now()`, new `Customer` row (or link to
   `existing_customer_id`) with `lifecycle_status = "ACTIVE"` (the real fix to the dashboard's
   long-dead `active_customers` count — see `phase9-5a-baseline.md`), `LEAD_CONVERTED` audit event
   (Milestone 23) with before/after state.
7. Never creates a `Subscription`/`License` as a side effect — conversion produces a `Customer` only;
   subscription/license issuance remains its own separate, explicit, already-existing real workflow
   (Phase 6/8), matching the canonical flow's own explicit ordering (Customer Approval happens before
   Subscription, and Subscription/License are their own steps, not bundled into conversion).

## Real error catalog entries this contract needs (Milestone 19)

`DUPLICATE_CUSTOMER`, `DUPLICATE_LEAD` (reserved for lead-creation-time duplicate detection, a smaller
version of the same `find_duplicate_candidates()` reuse, not built as a separate mechanism),
`INVALID_STATE_TRANSITION` (converting a `LOST`/`ARCHIVED` lead), `IDEMPOTENCY_CONFLICT`.
