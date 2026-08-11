# Phase 9.5A — Execution Plan

Order of execution, grouped to minimize rework:

1. Entry gate, branch, baseline + audit docs (this set) — DONE.
2. Milestone 2 — bounded contexts.
3. Milestone 3 — multi-device licensing policy model (design + real migration/model, reusing
   `resolve_effective_device_limit()` as the enforcement authority).
4. Milestones 4-5 — employee profile + presence (design + real migration/model).
5. Milestones 6-9 — lead/customer domain, ownership/isolation, location, card/detail API view models.
6. Milestone 10-11 — catalog reuse confirmation + quote/order/invoice/payment lifecycle design.
7. Milestone 12 — commission domain.
8. Milestone 13 — expenses/mini financial ledger.
9. Milestone 14 — management notes.
10. Milestone 15 — daily activity snapshot.
11. Milestone 16 — admin dashboard contract.
12. Milestone 17 — RBAC/permission matrix extension (real code).
13. Milestone 18 — mobile authentication ADR (design only, per explicit instruction not to build the
    full stack unless needed to validate schema/contracts).
14. Milestone 19 — versioned internal API contract + OpenAPI.
15. Milestone 20 — consolidated data model / migration plan documents.
16. Milestone 21 — implement the real migration foundation (all new tables from Milestones 3-16, in
    one reviewed, tested migration set).
17. Milestone 22 — minimal real services proving the architecture.
18. Milestone 23 — audit event catalog expansion (real code).
19. Milestone 24+ — security/privacy/IDOR tests, plus whatever the (truncated) remainder of the
    governing spec requires once visible; final regression; final decision; documentation; git/tag
    strategy.

## Pacing note

This spec is large (24+ milestones, message truncated before the end). Design documents are written
with real depth; implementation is scoped strictly to "foundation" per the governing instruction's own
explicit boundary (models, migrations, minimal proving services — not full business-logic workflows or
UI). Where the spec's own milestone text says "do not implement the complete X workflow," that
boundary is treated as binding, not aspirational.
