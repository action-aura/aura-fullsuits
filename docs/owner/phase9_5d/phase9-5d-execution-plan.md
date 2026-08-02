# Phase 9.5D — Execution Plan

## Ordering rationale

Milestones execute in the order the spec presents them — each is a real dependency of the next (financial calculation authority must exist before Quotes can compute totals; Quotes must exist before Approvals gate them; approved+accepted Quotes must exist before Orders; Orders before Invoices; Invoices before Payments; confirmed+allocated Payments before Refunds and Fulfillment; Fulfillment before Commission earning).

1. Entry Gate (done — see `phase9-5d-baseline.md`)
2. Milestone 1 — audit existing commercial authorities (in progress, delegated to a research agent; output feeds every later milestone's "reuse vs. build" decision)
3. Milestone 2 — canonical commercial funnel contract (state machine, permissions, audit actions per transition)
4. Milestone 3 — financial calculation authority (Decimal service, snapshot rules, rounding/allocation policy) — built before any document type, since every document needs it
5. Milestone 4 — catalog/pricing integration for sales (reuses Milestone 1's audit output)
6. Milestone 5 — Quotes (domain, versioning, numbering)
7. Milestone 6 — approvals (pricing/discount exceptions)
8. Milestone 7 — customer acceptance + Lead/Quote/Customer boundary (calls Phase 9.5C conversion service)
9. Milestone 8 — Sales Orders
10. Milestone 9 — Commercial Invoices
11. Milestone 10 — payment recording/confirmation (audits existing Payment authority first)
12. Milestone 11 — payment allocation
13. Milestone 12 — refunds (including entitlement consequence policy)
14. Milestone 13 — subscription/license fulfillment orchestration (calls canonical services, never writes licensing rows directly)
15. Milestone 14 — commission policy
16. Milestone 15 — commission ledger
17. Milestone 16 — RBAC/segregation of duties (extends the permission registry incrementally as each domain above is built, then a final consolidated audit pass)
18. Milestone 17 — commercial dashboards
19. Milestone 18 — `/api/operations/v1` commercial API
20. Milestone 19 — web routes/UI
21. Milestone 20 — i18n/RTL (extended incrementally alongside 5-19, then a final completeness pass — matching the Phase 9.5C precedent of "every new string translated immediately," not deferred to the end)
22. Milestone 21 — audit events (wired incrementally alongside each domain's service functions, then a final coverage report)
23. Milestone 22 — database/migrations/indexes/numbering (as received before truncation; migrations land incrementally per milestone, this is the final consolidated validation pass)
24. Remaining milestones (23+, if any) — unknown due to spec truncation; see `phase9-5d-baseline.md`'s truncation note

## Practical execution note

i18n/RTL and audit-event wiring are NOT deferred to Milestones 20/21 in practice — every new template/route/service function is built with EN+AR strings and audit calls from its first commit (Non-Negotiable Principle 18), matching how Phase 9.5C actually worked despite i18n/audit being numbered as late milestones there too. Milestones 20/21 are the *completeness verification* passes, not the *first implementation* passes.

## Testing discipline carried forward from 9.5C

- Postgres advisory-lock test-suite serialization remains in effect — no concurrent edits to migration/route/template files while a test run is in flight (this corrupted results twice in the 9.5C wave).
- Every financial calculation gets property-based/table-driven tests (Decimal precision, rounding, boundary amounts) in addition to the standard unit/integration tests — this phase's equivalent of 9.5C's IDOR test suite.
- Real browser validation (multi-viewport, both locales) is not optional — it found 4 genuine defects in 9.5C that no automated test caught.
- Real `EXPLAIN ANALYZE` at representative synthetic scale for every new high-traffic query — 9.5C found a phase-wide missing-index gap this way.
