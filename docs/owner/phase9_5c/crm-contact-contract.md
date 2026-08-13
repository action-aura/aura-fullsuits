# Phase 9.5C — Milestone 8: Contact Contract

## Reuse decision

`CustomerContact` (existing, from earlier Owner phases) — reused
unchanged in shape, extended additively (`notes`, `version`,
`archived_at` — Milestone 20 migration `870b08809d22`). Not replaced,
not duplicated.

`LeadContact` (new) — Leads had no existing contact concept at all, so
this is a genuinely new table, not a second authority for an existing
one. Field shape and the primary-contact rule are copied exactly from
`CustomerContact` so Milestone 13's conversion can carry a Lead's
contacts across with a straight 1:1 field mapping
(`contact_fields_for_customer_copy()`).

## Primary-contact policy — one rule, enforced transactionally, both sides

Real gap found and fixed on **both** the pre-existing `CustomerContact`
path and the new `LeadContact` path: neither previously prevented two
contacts on the same parent from both being marked `is_primary`. Fixed
identically in both `app.customers.services.add_contact()` and
`app.leads.contacts.add_lead_contact()`/`update_lead_contact()`: setting
a new primary demotes any existing primary for the same parent record
in the same database transaction (a single `UPDATE ... WHERE parent_id =
? AND archived_at IS NULL` before the insert/update), never a
read-then-write race. Verified by
`test_setting_new_primary_lead_contact_demotes_old_one` and
`test_setting_new_primary_customer_contact_demotes_old_one`.

## No orphan contact

Both `LeadContact.lead_id` and `CustomerContact.customer_id` are
`nullable=False` foreign keys — a contact cannot exist without a parent
at the database level, not just by service-layer convention.

## Ownership inherited from parent

Neither contact table has its own ownership/assignment concept — access
to a contact is entirely governed by access to its parent Lead/Customer
(enforced at the route layer, Milestone 16, via the same
`apply_ownership_filter()`/`customer_visible_to_actor()` checks already
used for the parent record itself — a contact route must load and
authorize the parent before ever touching the child row by its own
UUID, closing the exact "child-resource IDOR" risk the spec names).

## Normalization

Contact `business_phone`/`business_email` participate in the existing
duplicate-detection normalization (`normalize_phone()`,
case-insensitive email match) unchanged — no second normalization
scheme introduced for Lead contacts.

## Optimistic locking and soft-delete

Both tables now carry `version` (new on `CustomerContact`, native on the
new `LeadContact`) and `archived_at` — `update_lead_contact()`
demonstrates the version-check pattern (`test_update_lead_contact_stale_
version_rejected`); no hard-delete function exists for either table
(Non-Negotiable Domain Rule 12).

## Audit

`LEAD_CONTACT_ADDED`/`LEAD_CONTACT_UPDATED` (new codes, following the
existing free-form convention) alongside the pre-existing
`CUSTOMER_CONTACT_ADDED`.
