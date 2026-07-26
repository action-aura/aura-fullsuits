# Phase 8 — Proposed Implementation Plan

The governing spec's Part A is explicit: "Do not implement before documenting the authoritative
state mappings." Given the real scale found during baseline discovery (see
`phase8-scope-and-baseline.md`), this plan proposes breaking the spec's ~30 parts into reviewable,
independently-shippable milestones rather than attempting all of it in one unbroken pass. Each
milestone below ends in a state where existing tests are green, new tests exist for the new
surface, and nothing downstream is left half-wired.

## Milestone 1 — Authoritative state model + renewal data model (Parts B, D, Z-subset)

- The deterministic state-resolution service from Part B (subscription + license + installation +
  product-local state → one authoritative answer, reason code, required action).
- New tables: `owner_renewal_requests`, `owner_renewal_status_history`,
  `owner_payment_correction_history` (closing the Part F gap found in baseline: `correct_payment()`
  currently mutates in place with no history row).
- Renewal-date calculation rules (Part D) as a pure, heavily-unit-tested module — no API/UI yet.
- No behavior change to any existing route. Purely additive schema + pure-function services.

## Milestone 2 — Renewal workflow engine (Parts C, E, K)

- Renewal request lifecycle (`DRAFT→QUOTED→AWAITING_CONFIRMATION→AWAITING_PAYMENT→
  PAYMENT_RECORDED→APPROVED→APPLIED`, plus `REJECTED/CANCELLED/VOIDED`) built on Milestone 1's
  tables.
- The atomic renewal-application transaction (Part E's 22-step sequence), with idempotency-key and
  optimistic-lock concurrency tests (two simultaneous approvals must not double-extend).
- Assertion-refresh marking (Part X, the subset needed for renewal) so an existing installation
  picks up the renewal on its next check-in with no license-key re-entry (Part K), reusing the
  Phase 7V-A-proven check-in → signed-assertion → local-state-machine pipeline unchanged.
- Internal Owner UI: create/review/approve/apply/cancel renewal only (a slice of Part U).
- Physical validation: Scenario 1 (early renewal) and Scenario 2 (renewal after expiry) from Part
  AB, on at least one Windows and one physical Android target, following the same
  evidence-over-narrative discipline as Phase 7V-A (real screenshots/API captures, not claimed
  results).

## Milestone 3 — Expiry/past-due governance + notifications (Parts G, H, I, J, R-subset)

- Commercial policy model (distinct from the existing technical offline policy — Part I is
  explicit these must not be conflated).
- Expiry-warning schedules, internal notification center, deduplication.
- Safe expiry/past-due/suspension enforcement through the existing signed-assertion +
  capability-guard pipeline only (Part J: no second enforcement path).
- Scheduled-job safety (Part R): CLI commands first (`flask commercial expiry-scan`,
  `notification-scan`), advisory-lock-based idempotency: no production scheduler daemon yet, per
  the spec's own instruction.
- Physical validation: Scenario 3 (past-due) end to end.

## Milestone 4 — Pilot lifecycle + emergency extensions (Parts M, N)

- Pilot state machine and records.
- Emergency extension with MFA gate, max duration, stacking rules, automatic expiry.
- Physical validation: Scenarios 4 and 5.

## Milestone 5 — Manual activation approval + device-slot operations (Parts O, P)

- Configurable activation modes (`AUTOMATIC|MANUAL_APPROVAL|RISK_REVIEW`).
- Device-slot administration: replacement, release, temporary exception, over-limit remediation
  without silent deactivation.
- Physical validation: Scenario 6, and the downgrade-below-active-count exception path
  (Scenario 7's device-side half).

## Milestone 6 — Reconciliation, operational queues, dashboard (Parts Q, S, T)

- Reconciliation engine, report-only by default, CLI-runnable, dry-run supported.
- Role-based queues (Sales/Finance/Support/Super Admin/Viewer).
- Dashboard extensions (commercial-operations metadata only, per the explicit exclusion list).

## Milestone 7 — Product UX, assertion schema finalization, security/fraud test pass (Parts U-Y)

- Full internal Owner UI workflow set.
- Product renewal/expiry UX strings (English + Arabic, Windows + Android, both products).
- Assertion schema extensions finalized, Kotlin/Python conformance fixtures updated.
- Part Y's full fraud/security control checklist run as explicit tests, not just claimed.

## Milestone 8 — Migrations, full test matrix, end-to-end scenarios, release evidence (Parts Z-AC+)

- Final schema migration verified against a populated synthetic Phase 7 database (zero data loss).
- Full Part AA test matrix.
- All 7 Part AB scenarios run across all 4 build targets, with physical Android evidence where the
  spec requires it (not fabricated — Phase 7V-A's own discipline: if a physical step is blocked,
  say so honestly rather than claim a result).
- Data-boundary traffic capture (Part AC) confirming no patient/sales/financial-detail leakage to
  product-Owner traffic.
- Final Phase 8 decision document + tag, following the exact same "only tag what genuinely passed
  with real evidence" discipline Phase 7V-A used.

## Why phased rather than one pass

Three concrete reasons, not just caution for its own sake:

1. **The spec's own non-negotiable principles are financial/security-critical** (no local payment
   claims, no hidden extensions, deny-by-default, no data hostage-taking). Each of those is much
   easier to get right — and much easier for a human reviewer to actually check — in a
   500-line diff than a 15,000-line one.
2. **Milestone 2 (renewal) is the one thing every other milestone either depends on or mirrors.**
   Getting its idempotency/concurrency/atomicity story right first, and proven right with real
   concurrency tests, means Milestones 3-5 (which all reuse the same "apply a commercial change,
   mark for assertion refresh, physically confirm on a real device" shape) can move faster and with
   more confidence.
3. **Physical Android validation is the expensive, fragile part** (Phase 7V-A's own session spent
   a large fraction of its time on device connectivity issues, none of which were product bugs).
   Batching all physical validation into one final pass (Milestone 8) risks re-discovering the same
   kind of environmental issue after a huge amount of code is already written and waiting on it.
   Validating incrementally (one scenario per milestone, starting in Milestone 2) surfaces that
   risk early instead.

## Recommendation

Start with Milestone 1. It is pure schema + pure-function work with no behavior change to any
existing route, fully covered by new unit tests, and it directly unblocks every later milestone.
Confirm the state-resolution service's output shape and the renewal-date rules before building the
workflow engine on top of them in Milestone 2 — those two are the pieces every other part of Phase
8 reads from.
