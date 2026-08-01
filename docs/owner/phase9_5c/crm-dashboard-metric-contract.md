# Phase 9.5C — Milestone 14: CRM Dashboard Metric Contract

## `app.leads.dashboard.employee_crm_dashboard(actor_employee_profile_id)`

| Metric | Definition | Ownership boundary |
|---|---|---|
| `own_active_leads` | Count of `Lead` rows where actor is creator or assignee, `status NOT IN (CONFIRMED, LOST, ARCHIVED)` | `apply_ownership_filter()` |
| `new_leads`/`potential_leads`/`qualified_leads`/`converted_leads`/`lost_leads` | Same ownership scope, filtered to the named status | Same |
| `followups_due_today` | `LeadFollowup` rows assigned to actor, open, `due_at` within the server's current UTC calendar day | `employee_profile_id == actor` (direct, not via ownership filter — followups aren't Lead/Customer rows) |
| `followups_overdue` | Same, `due_at < now` | Same |
| `recent_interactions` | `LeadInteraction` rows logged by actor in the last 7 days | `employee_profile_id == actor` |

## `app.leads.dashboard.management_crm_dashboard()`

| Metric | Definition | Ownership boundary |
|---|---|---|
| `total_active_leads` | Sum of all non-terminal-status Lead counts, no filter | `leads.view_all`-gated at the route layer |
| `leads_by_status` | `GROUP BY Lead.status`, no filter | Same |
| `leads_by_employee` | `GROUP BY Lead.assigned_employee_profile_id`, no filter | Same |
| `conversions_by_employee` | Same, filtered to `status = CONFIRMED` | Same |
| `unassigned_leads` | `assigned_employee_profile_id IS NULL`, non-terminal status | Same |
| `reassignment_required_leads` | Active Lead whose current assignee's `EmployeeProfile.employment_status != 'ACTIVE'` — a live join, not a stored flag | Same |
| `followups_due_today`/`followups_overdue` | Global (no `employee_profile_id` filter) | Same |

## Explicitly absent, per the governing spec's own instruction

No revenue, invoice total, payment total, or commission metric appears
anywhere in either function — confirmed by the field lists above; no
`payments`/`invoices`/`commissions` table is queried anywhere in
`app/leads/dashboard.py`.

## Not yet implemented from the spec's full list

`duplicate_review_queue` and `location_verification_queue` (management
dashboard) are not built this wave — both are straightforward queries
(unauthorized-duplicate-flag / unverified-`CustomerLocation` rows
respectively) but were not implemented given this wave's time budget.
Named here as a real, tracked gap, not silently omitted.

## Route/drill-down

`GET /leads/dashboard` (`leads.crm_dashboard`) renders `leads/
dashboard.html`, branching on whether the actor holds `leads.view_all`.
No separate drill-down links from dashboard metrics to filtered list
views were wired this wave (e.g. clicking "Unassigned leads" does not
yet deep-link to `/leads?assigned=none`) — another named, tracked gap.

## Tests preventing unauthorized count leakage

Not yet added as dedicated dashboard tests this wave — the underlying
`apply_ownership_filter()` behavior is already covered by the Phase
9.5A/9.5C ownership test suite; a dashboard-specific test (two employees,
confirm each only sees their own counts) is tracked for Milestone 21.
