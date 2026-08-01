# Phase 9.5C — Milestone 24: Performance and Query Validation

## What was actually done (executed, not just inspected)

A representative-scale synthetic dataset was seeded directly against
`aura_owner_dev`: 100,000 `owner_leads` rows, ownership distributed
across 50 distinct `EmployeeProfile`s (~3.3% per-employee selectivity —
realistic for a real sales team's "my leads" filter, not a worst-case
100%-owned dataset). `EXPLAIN ANALYZE` was run for real against the
three highest-traffic CRM query shapes: the paginated own-leads list,
a status-filtered own-leads list, and the ownership-safe `COUNT(*)`
used for pagination totals.

**Real finding: a genuine, previously undocumented gap.** `pg_indexes`
showed `owner_leads` had no index beyond its primary key —
`assigned_employee_profile_id` / `created_by_employee_profile_id`
(the single most frequently executed predicate in the whole phase,
via `apply_ownership_filter()`) were unindexed, despite
`crm-index-and-query-plan.md`'s Milestone 20 claim that they were
"pre-existing from Phase 9.5A." That claim was false — verified
directly against the database, not assumed. The same audit found the
same gap across every other CRM child table's parent-lookup column
(`lead_id`/`customer_id`/`employee_profile_id` on interactions,
followups, notes, locations, contacts, status history, assignments).

At 100k rows / 3.3% selectivity, before indexing:

| Query | Plan | Execution time |
|---|---|---|
| Own-leads list (paginated) | Seq Scan | 41.9 ms |
| Own-leads list + status filter | Seq Scan | 15.2 ms |
| Own-leads COUNT(*) | Seq Scan | 14.5 ms |

Migration `a1f9c3d76e02` added 16 indexes covering every real
FK/ownership-filter column confirmed by grepping the actual query code
in `app/leads/`, `app/customers/`, `app/api_operations/crm.py` (not
speculative — each column is a real `.where()`/`.filter_by()`
predicate). After indexing, same dataset, same queries:

| Query | Plan | Execution time | Speedup |
|---|---|---|---|
| Own-leads list (paginated) | Bitmap Heap Scan + BitmapOr | 6.5 ms | 6.4x |
| Own-leads list + status filter | Bitmap Heap Scan + BitmapOr | 2.4 ms | 6.3x |
| Own-leads COUNT(*) | Bitmap Heap Scan + BitmapOr | 2.1 ms | 6.9x |

The synthetic dataset (100k Leads, 49 synthetic EmployeeProfiles/
StaffUsers) was deleted after the measurement; the 3 real pre-existing
test Leads that were incidentally touched by an overly broad UPDATE
during the redistribution step were restored to their correct
ownership and re-verified (`Wave 9.5C Conversion Retest Co`'s
converted Customer record was never touched — it lives in a separate
table and was independently re-confirmed still correctly assigned).

- `paginate()` (reused, unchanged) enforces a bounded page size and
  deterministic ordering on every list endpoint.
- Ownership-safe counts: `apply_ownership_filter()` always runs before
  the `COUNT(*)` — every "total" the UI shows is computed on the
  already-filtered set.
- No N+1: list views select only the parent row's own columns; detail
  views issue one query per child-collection type — a fixed, small
  number of queries per page load regardless of record count within
  each collection.

## Residual limitation

The 100k/3.3%-selectivity scenario is representative for a single
Owner tenant's realistic multi-year lead volume, not an upper bound.
No load/concurrency testing (simultaneous writers) was performed —
out of this milestone's scope, which is query-plan validation.
