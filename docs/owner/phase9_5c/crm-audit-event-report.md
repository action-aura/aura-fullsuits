# Phase 9.5C — Milestone 19: CRM Audit Event Report

## Reused authority

`app.audit.services.record()` (existing, unchanged) — every CRM action
this phase adds calls it with `actor_staff_user_id`, `action_code`,
`entity_type`, `entity_public_id`, and (where applicable)
`before_state`/`after_state`/`reason`. No second audit table or logging
path was introduced.

## Real action codes emitted (grep-verified against `app/leads/`, `app/customers/`)

| Code | Emitted by |
|---|---|
| `LEAD_CREATED` | `services.create_lead` |
| `LEAD_UPDATED` | `services.update_lead` |
| `LEAD_STATUS_CHANGED` | `services.change_lead_status` |
| `LEAD_ASSIGNED` / `LEAD_REASSIGNED` | `services.assign_lead` |
| `LEAD_NOTE_ADDED` | `services.add_lead_note` |
| `LEAD_CONTACT_ADDED` / `LEAD_CONTACT_UPDATED` | `contacts.add_lead_contact`/`update_lead_contact` |
| `LEAD_CONVERTED` | `conversion.convert` |
| `LEAD_INTERACTION_LOGGED` / `CUSTOMER_INTERACTION_LOGGED` | `engagement._log_interaction` |
| `LEAD_FOLLOWUP_CREATED` / `CUSTOMER_FOLLOWUP_CREATED` | `engagement._create_followup` |
| `LEAD_FOLLOWUP_COMPLETED` / `CUSTOMER_FOLLOWUP_COMPLETED` | `engagement._complete_followup` |
| `LEAD_FOLLOWUP_CANCELLED` / `CUSTOMER_FOLLOWUP_CANCELLED` | `engagement._cancel_followup` |
| `CUSTOMER_UPDATED` / `CUSTOMER_ARCHIVED` / `CUSTOMER_CONTACT_ADDED` / `CUSTOMER_NOTE_ADDED` | pre-existing (Customer CRUD, unchanged) |
| `CUSTOMER_ASSIGNED` / `CUSTOMER_REASSIGNED` | `customers.services.assign_customer` |
| `CUSTOMER_LOCATION_CAPTURED` | `services.capture_location` (pre-existing, Phase 9.5A) |
| `LOCATION_VERIFIED` | `location.verify_location` |

Every code from the governing spec's Milestone 19 checklist is covered
**except** `LEAD_ARCHIVED` and `LEAD_DUPLICATE_OVERRIDE`/`LEAD_LINKED_TO_
EXISTING_CUSTOMER` as distinct codes — real, named gaps:

- **Archive**: implemented by calling `change_lead_status(lead,
  "ARCHIVED", ...)`, which emits `LEAD_STATUS_CHANGED` (correct and
  sufficient — the audit record shows `before_state={"status": X},
  after_state={"status": "ARCHIVED"}`, fully reconstructable), not a
  separate `LEAD_ARCHIVED` code. Functionally complete, differently
  named than the spec's literal suggestion.
- **Duplicate override / link-to-existing**: `conversion.convert()`'s
  `existing_customer_id` path emits the same `LEAD_CONVERTED` code with
  `after_state` including `customer_id` — it does not currently
  distinguish "created new" vs. "linked to existing" via a different
  code or an explicit field. A real, named gap for a follow-up pass
  (cheap to add: one extra `after_state` key, e.g. `"linked_existing":
  true`), not implemented this wave.

## Payload safety (verified)

- No exact coordinates in any audit call — `capture_location()`/
  `verify_location()` both record only `source`/`verified`/location
  UUID (Milestone 12).
- No full note body — `LEAD_NOTE_ADDED`/`CUSTOMER_NOTE_ADDED` record only
  `entity_public_id`, never `after_state={"body": ...}` (grep-confirmed).
- No session token, password, MFA secret, license key, or other secret
  class appears in any CRM audit call — none of these values are ever in
  scope inside `app/leads/`/`app/customers/` service functions.

## Real, immutable audit chain

Unchanged from Phase 9.5B-R3/earlier — `record()`'s own hash-chaining
behavior is not modified by this phase; every new call site is simply a
new entry in the existing chain.
