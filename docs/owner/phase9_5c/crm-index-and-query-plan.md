# Phase 9.5C — Milestone 20/24: Index and Query Plan

## Correction (Milestone 24 real-scale validation)

This document originally claimed that `owner_leads.assigned_employee_
profile_id` / `.created_by_employee_profile_id` and several other CRM
child-table FK columns were "pre-existing indexes ... from Phase
9.5A." That claim was **false** — verified directly against
`pg_indexes` on `aura_owner_dev`, not assumed. Every one of those
tables had no index beyond its primary key. See
`crm-performance-validation.md` for the real `EXPLAIN ANALYZE`
evidence (100k-row synthetic dataset) that surfaced this.

## Indexes added this phase (migration `a1f9c3d76e02`)

Every column below was confirmed, by grepping the real query code in
`app/leads/`, `app/customers/`, `app/api_operations/crm.py`, to be an
actual `.where()`/`.filter_by()` equality predicate — not speculative.

| Table.column | Real query pattern it serves |
|---|---|
| `owner_leads.assigned_employee_profile_id` | `apply_ownership_filter()` — the single most frequently executed CRM query in this phase |
| `owner_leads.created_by_employee_profile_id` | `apply_ownership_filter()` |
| `owner_customers.assigned_sales_staff_id` | `apply_ownership_filter()` for Customer, `customer_visible_to_actor()` |
| `owner_lead_assignments.lead_id` | assignment-history route/API query |
| `owner_lead_status_history.lead_id` | status-history route/API query |
| `owner_lead_interactions.lead_id` | interactions list on Lead detail |
| `owner_lead_interactions.employee_profile_id` | dashboard `recent_interactions` count |
| `owner_lead_followups.lead_id` | follow-ups list on Lead detail |
| `owner_lead_followups.employee_profile_id` | dashboard `followups_due_today`/`overdue` |
| `owner_lead_notes.lead_id` | `list_lead_notes_visible_to()` |
| `owner_customer_locations.lead_id` | location list on Lead detail |
| `owner_customer_locations.customer_id` | location list on Customer detail |
| `owner_customer_contacts.customer_id` | contacts list on Customer detail |
| `owner_customer_interactions.customer_id` | interactions list on Customer detail |
| `owner_customer_followups.customer_id` | follow-ups list on Customer detail |
| `owner_customer_notes.customer_id` | `list_customer_notes_visible_to()` |

## Indexes added earlier this phase (migrations `911ac2a05c12`/`870b08809d22`)

| Table.column | Real query pattern it serves |
|---|---|
| `owner_customer_assignments.customer_id` | `assign_customer()`'s "find current open assignment" lookup, assignment-history query |
| `owner_lead_contacts.lead_id` | `list_lead_contacts()`, `add_lead_contact()`'s primary-demotion `UPDATE` |

## Query plans — real `EXPLAIN ANALYZE`, not inspection-only

Unlike this document's original version, the query plans above were
verified by actually running `EXPLAIN ANALYZE` against a 100k-row
synthetic dataset with realistic (~3.3%) per-employee selectivity —
see `crm-performance-validation.md` for the before/after plans and
timings. Before indexing: `Seq Scan`, 14–42ms. After: `Bitmap Heap
Scan` + `BitmapOr`, 2–6.5ms — a 6–7x measured improvement, not an
estimate.
