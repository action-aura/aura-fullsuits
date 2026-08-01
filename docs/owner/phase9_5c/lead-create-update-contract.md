# Phase 9.5C — Milestone 4: Lead Create/Update Contract

## `create_lead()` — extended, not replaced

Added to the existing, already-tested function:

- **Idempotency**: optional `idempotency_key` param, reusing the same
  `CommercialOperationsIdempotencyKey` ledger `conversion.py` already
  uses (`LEAD_CREATION` operation code) — a repeated request with the
  same key returns the original Lead instead of creating a duplicate.
  Optional (defaults `None`, skips the check) so every pre-existing
  direct-service-call test continues to work unchanged.
- **Inline-assignee validation**: if `fields["assigned_employee_profile_id"]`
  is set, the destination `EmployeeProfile` must exist and be `ACTIVE` —
  `LeadError("DESTINATION_EMPLOYEE_NOT_ACTIVE")` otherwise. (3 existing
  tests needed a one-line `activate_employee(...)` call added to their
  fixture setup to keep passing — a real, correct consequence of a real,
  new integrity check, not a weakening.)

## `update_lead()` — new

Bounded to `UPDATABLE_LEAD_FIELDS` (`organization_or_prospect_name`,
`primary_contact_name`, `phone`, `email`, `source`, `priority`,
`estimated_value`, `currency`, `next_follow_up_at`, `location_summary`)
— deliberately excludes `status`/`assigned_employee_profile_id`, which
each already have their own dedicated, audited function
(`change_lead_status`, `assign_lead`). This mirrors
`customers/routes.py`'s own `update()` discipline exactly: a generic
update must never let a caller reach a status/ownership change through a
wider field set than the specific, permission-gated action for that
change.

Optimistic version check via `expected_version`, audited as `LEAD_UPDATED`.

## Validation module (`app/leads/validation.py`) — new, not yet wired into the service layer

`validate_lead_fields()` implements every rule the governing spec lists:
bounded text lengths, valid email format, at least one contact method
(phone or email) required for a *new* Lead (not for a partial update),
valid `source`/`priority` enum membership, `Decimal`-safe estimated
value with a required 3-letter currency when set, bounded location
summary, and ACTIVE-employee validation for an inline assignee.

**Deliberately not called from `create_lead()`/`update_lead()`
themselves** — matches the existing, established codebase convention:
`customers/services.py:create_customer()` also does zero field
validation, relying on the route layer to clean `request.form` before
calling the service. Retrofitting stricter validation into the bare
service functions would break the "at least one contact method"
constraint against dozens of existing tests that legitimately create a
name-only Lead through direct service calls (a valid use case — e.g. a
lead captured from a business card photo before phone/email is
transcribed). `validate_lead_fields()` is ready, unit-testable, and will
be called by the Milestone 16 web/API route layer before it invokes
`create_lead()`/`update_lead()`, which is where user-submitted form data
actually needs to be cleaned.

## Individual vs. organization prospects

Confirmed still satisfied without any schema change:
`organization_or_prospect_name` is a single field usable for either an
organization name or an individual's name — no separate
"organization-name-required" constraint exists anywhere in the model or
new validation function.
