# Phase 9.5C — Milestone 20: Migration Impact Report

## Decision: prefer the Phase 9.5A schema (confirmed, followed)

The core CRM tables (`owner_leads`, `owner_lead_*`, `owner_customer_
locations/interactions/followups`) already existed and were already
migrated (Phase 9.5A) — see `existing-crm-foundation-audit.md`. This
phase created **zero duplicate tables**; every migration below is a
real, additive extension proven necessary by a real implementation gap
found during Milestones 2-13.

## 4 new migrations, each tied to a real, named requirement

| Revision | Purpose | Driven by |
|---|---|---|
| `911ac2a05c12` | `owner_lead_assignments.reason`; `owner_lead_notes`/`owner_customer_notes.visibility`+`archived_at`; `owner_customers.version`; new `owner_customer_assignments` table | Milestone 3 (Customer had no assignment history at all) + Milestone 11 (no note visibility concept existed) |
| `fe581c2bb967` | `owner_lead_followups`/`owner_customer_followups.cancelled_at` | Milestone 10 (couldn't distinguish open vs. cancelled without it) |
| `870b08809d22` | `owner_customer_contacts.notes`/`.version`/`.archived_at`; new `owner_lead_contacts` table | Milestone 8 (Leads had no contact concept; Customer contacts had no optimistic lock/soft-delete) |
| `dd408948bf89` | `owner_customer_locations.verified_by_employee_profile_id`/`.verified_at`/`.verification_reason` | Milestone 12 (verification recorded *that*, never *who/when/why*) |

## Empty-DB and populated-DB validation

- **Empty DB**: every fresh test-suite run (`tests/conftest.py`'s
  session-scoped `_migrated_schema` fixture) runs `alembic upgrade head`
  from scratch against `aura_owner_test` — this has happened dozens of
  times across this wave's development (every `pytest` invocation), each
  one a real empty-to-head validation. Confirmed passing (see
  `final-deterministic-regression-report.md` for the final run).
- **Populated DB**: `aura_owner_dev` carries real pre-existing data from
  earlier Owner phases (15 customers, 5 staff users, confirmed via direct
  query). All 4 migrations were applied to this populated database
  (not just the empty test DB) during development, with zero errors.

## Downgrade/upgrade round-trip — actually executed

```
alembic downgrade 338d06dece44   (back to the R3 baseline, reversing all 4 new migrations)
alembic upgrade head              (forward again)
```

Both directions completed with zero errors. Post-round-trip
`compare_metadata()` diff: `[]` (zero drift). Post-round-trip data
check: `owner_customers` still 15 rows, `owner_staff_users` still 5 rows
— **no data loss** across the full reversible cycle.

## No schema drift (final, executed check)

```python
compare_metadata(MigrationContext.configure(engine.connect()), Base.metadata)
```

Returns `[]` against `aura_owner_dev` at the final migration head —
confirmed twice: once after the initial forward application of all 4
migrations, once again after the downgrade/upgrade round trip above.

## Indexes added, justified

- `owner_customer_assignments.customer_id` — every `assign_customer()`
  call queries `WHERE customer_id = ? AND unassigned_at IS NULL`; without
  an index this is a full scan on every reassignment.
- `owner_lead_contacts.lead_id` — every `list_lead_contacts()` /
  `add_lead_contact()` primary-demotion query filters by `lead_id`.

Both mirror the existing `LeadAssignment`/`CustomerContact` tables'
own (pre-existing) indexing pattern — not a new indexing philosophy.

See `crm-index-and-query-plan.md` for the full query-pattern
justification (Milestone 24 cross-reference).
