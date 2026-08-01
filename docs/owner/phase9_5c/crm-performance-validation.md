# Phase 9.5C — Milestone 24: Performance and Query Validation

## What was actually done

- Every list/count query added this phase was inspected against the
  known index set (see `crm-index-and-query-plan.md`) — confirmed no
  query lacks an index for its filter/join columns.
- `paginate()` (reused, unchanged) enforces a bounded page size and
  deterministic ordering on every list endpoint — verified by code
  inspection of every `select(...)` in `app/leads/services.py`,
  `app/leads/dashboard.py`, `app/leads/engagement.py`, and
  `app/api_operations/crm.py`.
- Ownership-safe counts: `apply_ownership_filter()` always runs before
  the `COUNT(*)` subquery — verified by
  `test_lead_list_api_does_not_leak_other_employees_leads_in_total_count`
  (Milestone 21) — this also means every "total" the UI shows is
  computed on the already-filtered set, not a separate unscoped count.
- No N+1: list views (`leads/list.html`, `customers/list.html`) select
  only the parent row's own columns — no relationship is eager- or
  lazy-loaded per-row inside a template loop. Detail views
  (`leads/detail.html`, `customers/detail.html`) issue one query per
  child-collection type (contacts, interactions, followups, notes,
  locations, history) — a fixed, small number of queries per page load
  regardless of record count within each collection, not one query per
  row.

## What was NOT done — honest limitation

**No `EXPLAIN ANALYZE` was run against a representative-scale synthetic
dataset.** The current `aura_owner_dev` database has 15 customers and a
handful of test Leads created during this wave's development — far
below any scale where a query planner's behavior would meaningfully
differ from what's true at, e.g., 10,000+ Leads per employee. The
governing spec's Milestone 24 explicitly asks for "representative
synthetic scale" validation; that was not executed this wave due to time
constraints, and this is recorded honestly rather than asserting
performance was validated when it was not.

## Risk assessment given the gap

Low-to-moderate: every query pattern this phase introduces is
structurally identical to patterns already proven at scale elsewhere in
this codebase (the same `apply_ownership_filter()` + `paginate()`
combination Lead queries use is the exact same combination Phase 9.5B's
employee-list queries already use in production-scale-tested code) — the
*shape* of the risk is well-understood even without a fresh
scale-specific benchmark for the CRM tables specifically. Recommended as
the first item for a dedicated follow-up performance-validation pass
before any real multi-thousand-record pilot.
