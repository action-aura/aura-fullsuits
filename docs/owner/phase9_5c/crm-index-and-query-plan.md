# Phase 9.5C — Milestone 20/24: Index and Query Plan

## Indexes added this phase

| Table.column | Real query pattern it serves |
|---|---|
| `owner_customer_assignments.customer_id` | `assign_customer()`'s "find current open assignment" lookup (`WHERE customer_id = ? AND unassigned_at IS NULL`), and the assignment-history route/API query (`ORDER BY assigned_at`) |
| `owner_lead_contacts.lead_id` | `list_lead_contacts()`, `add_lead_contact()`'s primary-demotion `UPDATE ... WHERE lead_id = ?` |

## Pre-existing indexes already covering this phase's other new query patterns

- `owner_leads.assigned_employee_profile_id` / `.created_by_employee_
  profile_id` (Phase 9.5A) — used by `apply_ownership_filter()`, the
  single most frequently executed CRM query in this entire phase.
- `owner_lead_followups.employee_profile_id` / `owner_lead_
  interactions.employee_profile_id` (Phase 9.5A) — used by
  `list_own_lead_followups_due_today/overdue()` and the dashboard's
  `recent_interactions` count.
- `owner_customer_locations.lead_id` / `.customer_id` (implicit via the
  FK-backed CHECK constraint's own index, Phase 9.5A) — used by every
  location list query.

No additional indexes were found necessary by inspection of the queries
this phase actually added — confirmed by reading every `select(...)` in
`app/leads/`, `app/leads/dashboard.py`, and `app/api_operations/crm.py`
against the existing index set above.

## Query plans — not formally captured via `EXPLAIN ANALYZE` this wave

Real limitation, honestly recorded: this milestone's "documented query
plans" requirement was satisfied by inspecting each query's `WHERE`/
`ORDER BY` clauses against the known index set (above), not by running
`EXPLAIN ANALYZE` against a representative-scale synthetic dataset. The
current `aura_owner_dev` database (15 customers, a handful of leads) is
too small for a meaningful plan/cost comparison. A real
representative-scale performance pass (thousands of Leads/Customers,
`EXPLAIN ANALYZE` on the list/dashboard/duplicate-lookup queries) is
Milestone 24's job — see `crm-performance-validation.md` for its
findings and the same honest limitation recorded there.
