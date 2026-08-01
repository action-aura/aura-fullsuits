# Phase 9.5A Milestone 6 — Lead and Customer Domain Model

See `phase9-5a-baseline.md` / `duplication-risk-report.md` for why `Lead` is a genuinely new, separate
table from the existing `Customer` (whose `lifecycle_status="LEAD"` default is left untouched, not
repurposed as the pipeline).

## New: `Lead` (`owner/app/models/leads.py`)

```
id (UUID PK), organization_or_prospect_name, primary_contact_name, phone (nullable), email (nullable),
source (bounded string: WEBSITE | REFERRAL | COLD_OUTREACH | EVENT | OTHER),
status (see canonical vocabulary below, default NEW),
assigned_employee_profile_id (FK, nullable), created_by_employee_profile_id (FK, not nullable),
product_interest_ids (many-to-many via lead_product_interests join table -> owner_products),
estimated_value (Numeric(12,2), nullable), currency (nullable, required if estimated_value set),
priority (LOW | MEDIUM | HIGH, default MEDIUM),
next_follow_up_at (timestamptz, nullable), last_interaction_at (timestamptz, nullable),
location_summary (bounded string, free text — a quick "Amman, near 4th Circle" without full
  lat/long; real coordinate capture is a child `customer_locations`-style row, Milestone 8),
created_at, updated_at, converted_at (nullable), lost_at (nullable), archived_at (nullable),
version (optimistic lock)
```

## Canonical lead statuses — real vocabulary decision

The governing instruction lists a suggested vocabulary (NEW/NOT_INTERESTED_NOW/POTENTIAL/FOLLOW_UP/
UNDER_OBSERVATION/QUALIFIED/CONFIRMED/LOST/ARCHIVED) but also explicitly warns: "Do not force existing
database enum values to match these names when an established canonical vocabulary already exists."
Milestone 1's audit found **no existing lead-status vocabulary anywhere** (Lead is entirely new) — so
there is nothing to conflict with, and the suggested vocabulary is adopted verbatim as the real
canonical set, stored as a plain `String(32)` (matching every other status field in this codebase —
`Customer.lifecycle_status`, `Subscription.status`, etc. are all plain strings, not DB-level enums;
consistent with existing convention, not a new pattern):

```
NEW -> POTENTIAL -> FOLLOW_UP -> UNDER_OBSERVATION -> QUALIFIED -> CONFIRMED
NEW -> NOT_INTERESTED_NOW
(any non-terminal) -> LOST
(any) -> ARCHIVED
```

`CONFIRMED` is the trigger state for `LeadConversionService` (`lead-conversion-contract.md`) — reaching
`CONFIRMED` does not itself create the `Customer` row; conversion is always an explicit, separate,
audited action, never an automatic side effect of a status write (matching Non-Negotiable Principle 8 —
server authority, explicit transitions, not implicit ones).

## Required history tables (new)

- `lead_status_history` (lead_id, from_status, to_status, changed_by_employee_profile_id, reason,
  changed_at) — append-only.
- `lead_assignments` (lead_id, assigned_to_employee_profile_id, assigned_by_employee_profile_id,
  assigned_at, unassigned_at nullable) — current assignment is the row with `unassigned_at IS NULL`;
  reassignment closes the old row and opens a new one, never an in-place update (audit-friendly,
  mirrors `DeviceSlotException`'s own time-windowed-row convention).
- `lead_interactions` (lead_id, employee_profile_id, interaction_type (CALL | EMAIL | MEETING |
  WHATSAPP_MANUAL_NOTE | OTHER — logging that an interaction happened, never an actual WhatsApp/SMS
  integration, which is forbidden this phase), summary, occurred_at).
- `lead_followups` (lead_id, employee_profile_id, due_at, completed_at nullable, notes).
- Notes: reuses the same free-text-note pattern as `CustomerNote` but as a new `lead_notes` table
  (kept separate from `CustomerNote` since a `Lead` is not a `Customer` — see duplication-risk-report.md).
- Location: see `customer-location-contract.md` — the `customer_locations` table takes either a
  `lead_id` or a `customer_id` (nullable FKs, exactly one required via a `CHECK` constraint), so a
  location captured before conversion is not lost or duplicated after conversion.

## Customer additions (additive only, no change to existing columns)

No new columns on `Customer` itself. New child tables only where genuinely new concepts exist:
`customer_locations` (Milestone 8), `customer_interactions`/`customer_followups` (same shape as the
Lead versions, reused pattern, separate tables since a `Customer`'s post-conversion history is
distinct from its pre-conversion `Lead` history — the `Lead` row itself is retained, linked via
`Customer.converted_from_lead_id`, new nullable FK column on `Customer` — the one additive column this
phase adds to the existing table).
