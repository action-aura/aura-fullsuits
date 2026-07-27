# Phase 8 — Final Decision (Milestone 8, Parts Z-AC+)

Following the same "only tag what genuinely passed with real evidence, say so honestly when a step
is blocked" discipline Phase 7V-F used (`docs/licensing/phase7v-final/phase7-final-release-validation-decision-v2.md`).

## Overall verdict — **CONDITIONAL PASS**

The commercial-operations backend Phase 8 set out to build — renewal lifecycle, expiry/past-due
governance, pilot lifecycle, emergency extensions, manual activation approval, device-slot
administration, reconciliation, role-based queues, dashboard extensions, the assertion-schema
extension, and cross-platform PENDING handling — is complete, real, and covered by real evidence
this session. What remains unfinished is the internal Owner UI (routes/templates) for Milestones
4-6's capability and physical device validation of the Part AB scenarios — both genuinely blocked
by this environment (no browser-driven UI-build session was run for the new workflows; no adb, no
connected physical device, no Gradle instrumented/on-device test run available here).

## 1. Schema and migrations — **PASS**

Four Phase 8 migrations (`8646da2df010`, `0b1d294dfb40`, `65397e1b63e1`, `0f8d55b753ed`) verified
against a synthetic, populated Phase-7-head database this session:

- Seeded a fresh database migrated only to the Phase 7 head revision (`60f363ee66e8`) with realistic
  rows across 56 tables (313 rows: staff, roles/permissions, catalog, 5 customers/subscriptions/
  licenses/installations, audit log, activation events).
- `alembic upgrade head` ran clean, zero errors, introducing exactly the 12 new Phase 8 tables
  (`owner_renewal_requests` + status history, `owner_payment_correction_history`,
  `owner_commercial_policies`, `owner_internal_notifications`, `owner_pilot_records` + status
  history + extensions, `owner_emergency_extensions`, `owner_activation_policies`,
  `owner_pending_activations`, `owner_device_slot_exceptions`), all empty post-migration as
  expected.
- **Zero data loss**: all 56 pre-existing tables have identical row counts before and after.
  Spot-checked subscription and license rows byte-for-byte intact.
- `alembic downgrade` back to the Phase 7 head, then `upgrade head` again: both directions clean,
  data still intact after the full round-trip.

## 2. Automated test matrix — **PASS** (scoped to what Phase 8 could have regressed)

| Suite | Result |
|---|---|
| `owner/tests/` | **357 passed** (full suite, one process) |
| `commercial_runtime/licensing_contracts/tests/` | **214 passed** (full suite, one process) |
| `android/aura-clinic` `./gradlew testDebugUnitTest` | **82/82 passed**, real build (found and fixed one real regression: Milestone 7's new `"PENDING"` protocol-string comparison tripped the Clinic `HardcodedStringAuditTest` i18n heuristic — allow-listed alongside the existing `SUCCESS`/`RESTRICTED`/etc. entries, same pattern) |
| `android/aura-retail` `./gradlew testDebugUnitTest` | **BUILD SUCCESSFUL**, no test failures |

**Deliberately not re-run**: the full `products/retail/tests`/`products/clinic/tests` business-logic
suites (POS, accounting, CRM, HR) — Phase 8 made zero Python changes to either product, only to
`licensing.js` (not covered by pytest) and the shared `commercial_runtime.licensing_contracts`
package (which *is* covered above). Re-running suites unrelated to what changed would not have
added evidence, and the baseline discovery doc (`phase8-scope-and-baseline.md`) already documents a
pre-existing, unrelated test-isolation issue in those suites when run as one combined invocation.

## 3. Part AB scenarios (7 commercial lifecycle scenarios × 4 build targets) — **NOT VERIFIED (environment-blocked)**

No physical Android device, no `adb`, no Gradle instrumented/on-device test runner is available in
this environment. Milestones 2-7 exercised every scenario's *service-layer and HTTP-API* behavior
with real Postgres-backed integration tests (concurrency tests, self-approval rejection, idempotent
retries, the self-healing PENDING-to-SUCCESS activation retry, etc.) — genuinely real evidence, just
not the physical on-device evidence Part AB itself asks for. This mirrors Phase 7V-F's own
"CONDITIONAL PASS... physical lifecycle beyond that not completed" verdict for Android exactly, for
the same reason: an external hardware-connectivity dependency, not a code defect found this session.

## 4. Data-boundary check (Part AC) — **PASS** (structural equivalent, not live traffic capture)

A live network traffic capture isn't possible without a running deployment and physical/emulated
client traffic. Built the structural equivalent instead: `test_phase8_data_boundary.py` walks every
new Phase 8 surface's actual field names and message content (role-based queue snapshots,
reconciliation findings, notification title/message, the nine new assertion-payload fields) for
patient/sales/inventory/financial-secret markers, reusing the exact technique
`assertions.py::_guard_payload()` already applies structurally to the signed assertion. All clean.

## 5. Full internal Owner UI workflow set — **NOT DONE**

Milestones 4, 5, and 6 each explicitly deferred routes/templates for their new capability (pilots,
emergency extensions, pending activations, device-slot exceptions, activation-policy management,
role-based queue views) to "later" — consistently, by design, documented in each milestone's own
design doc. That backlog was never picked up. This is real, scoped, and estimable remaining work,
not a hidden gap: every capability behind it is fully built, tested, and callable from Python today.

## What is safe to rely on today

- Every Phase 8 service module (renewals, pilots, emergency extensions, activation policy,
  device-slot ops, reconciliation, queues) is production-shaped: real concurrency handling, real
  audit trail, real permission gating, real separation-of-duties, deny-by-default throughout.
- `AUTOMATIC` activation mode (the only mode ever exercised in production, since `MANUAL_APPROVAL`/
  `RISK_REVIEW` require an explicit opt-in `ActivationPolicy` row that does not exist anywhere by
  default) is provably byte-for-byte unchanged from pre-Phase-8 behavior — the entire manual-approval
  code path was built additively and never touches the default path.
- The assertion schema extension is live and already flowing on every activation/check-in in this
  codebase's test suite, with zero client-side parsing changes required on either platform (verified
  by research into how each client actually consumes the payload, not assumed).

## What must happen before Phase 8 can be called fully complete

1. Build the internal Owner UI workflow set for Milestones 4-6's backlog.
2. Enable `MANUAL_APPROVAL` against a real Android/Windows build in a connected-device session and
   run the physical Scenario 6 (device replacement) and the manual-approval scenario end to end.
3. Run the remaining 6 Part AB scenarios physically across all 4 build targets.
4. A follow-up decision doc closing out items 1-3, at which point (and only then) this tag's
   "conditional" qualifier can be dropped.

## Tag

`aura-owner-commercial-ops-phase8-conditional-complete` — reflects genuine, real, tested completion
of the backend/service layer and cross-platform PENDING-handling foundation, explicitly conditional
on the UI and physical-validation work in the section above.

## Phase 8V update (additive — this section only, historical verdict above unchanged)

Phase 8V (`docs/owner/phase8v/`) closed item 1 above in full (the entire internal Owner UI backlog,
real RBAC/MFA/audit-tested) and made real, wire-level progress on item 3 (3 of 7 Part AB scenarios
now have genuine cross-package HTTP proof, not just backend-service tests — see
`docs/owner/phase8v/validation-scenarios-evidence.md`). It also found and fixed a real defect
(`commercial_runtime`'s client-side assertion allowlist rejecting every Milestone 7 field) that this
document's own original evidence could not have caught, since no test at the time exercised the real
cross-package wire path — see `docs/owner/phase8v/phase8v-security-review.md`.

**This tag still represents the correct state of the world.** Item 2 (physical Android/Windows
validation of all 7 scenarios) remains genuinely open — Phase 8V disclosed, before starting any
work, that no physical device is available in that session's environment, and did not fabricate that
evidence. The conditional qualifier stays until a session with device access completes items 2-3.
See `docs/owner/phase8v/phase8-final-decision.md` for the full accounting.
