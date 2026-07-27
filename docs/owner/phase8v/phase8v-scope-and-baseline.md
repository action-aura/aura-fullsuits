# Phase 8V — Scope and Baseline

## Starting point, verified

- Conditional tag: `aura-owner-commercial-ops-phase8-conditional-complete` -> `7150564af95bd75cd475df57a6b42ae9ad4b3fb5`
  (the "Phase 8 M8: final decision doc" commit).
- `HEAD` at the same commit, branch `master`, working tree clean.
- Owner baseline: 357 passed. `commercial_runtime/licensing_contracts`: 214 passed. Android Clinic
  Kotlin unit tests: 82/82 (real `./gradlew testDebugUnitTest`). Android Retail: build successful.
  These are re-confirmed, not re-derived from memory, at the start of this phase (see
  `phase8v-automated-test-report.md`).

## Two corrections to the governing brief's assumed doc set

The instructions for this phase list a `docs/owner/phase8/` file set (handover doc,
`commercial-operations-domain-map.md`, `payment-record-governance.md`, `internal-notification-center.md`,
`pilot-license-policy.md`, `emergency-extension-policy.md`, `manual-activation-approval-design.md`,
`device-slot-operations-design.md`, `commercial-reconciliation-design.md`, `commercial-operations-rbac.md`,
`product-renewal-ux.md`, `phase8-test-report.md`, `phase8-security-review.md`,
`phase8-residual-risk-register.md`) that does not exist under those names. The actual Phase 8 design
docs, one per milestone, are:

| Assumed name | Actual doc |
|---|---|
| domain map / handover | `phase8-scope-and-baseline.md`, `phase8-implementation-plan.md` |
| subscription/license governance | `subscription-license-governance.md` |
| renewal design | `renewal-lifecycle-design.md` |
| payment-record governance | (subsection of `renewal-lifecycle-design.md` -- no dedicated doc) |
| commercial policy / notifications | `expiry-and-past-due-policy.md` |
| pilot / emergency-extension policy | `pilot-and-emergency-extension-design.md` |
| manual-activation / device-slot design | `manual-activation-and-device-slots.md` |
| reconciliation / queues / dashboard | `reconciliation-queues-and-dashboard.md` |
| RBAC matrix | inline in `app/staff/seed_data.py` -- no separate matrix doc exists |
| product renewal UX / assertion schema / security checklist | `assertion-schema-and-product-ux.md` |
| final decision | `phase8-final-decision.md` |

Read these instead. Nothing in this reconciliation changes Phase 8V's actual objective.

## Hard environment constraint, stated up front (not discovered mid-phase)

No `adb`, no connected physical Android device, no Android emulator running, and no LAN/USB path to
a device exist in this environment (reconfirmed at the start of this phase — see
`phase8v-physical-validation-report.md`). Every physical-Android-only requirement in the governing
brief (Parts Q-W's Android legs, Part Y's real-device traffic capture, Part Z's logcat capture, and
the Definition-of-Done items that require them) **cannot be produced with real evidence in this
environment**, and per the brief's own rule ("do not fabricate physical Android or Windows results")
will not be fabricated. This is disclosed here, before any implementation work, exactly as Phase 7V-A
and Phase 7V-F both did when their own device sessions were interrupted.

What IS achievable and real in this environment, and is what this phase actually delivers:

- The complete internal Owner UI backlog (routes + templates) for Milestones 4-6, server-side RBAC,
  recent-auth/MFA gates, and audit coverage for every new action.
- Real, Postgres-backed, HTTP-level lifecycle validation of all seven Part AB scenarios' *service and
  API* behavior (the Windows desktop product talks to Owner over the same HTTP contract a physical
  Android device would -- this is genuinely real traffic, just not from a physical Android handset).
- Real Kotlin builds (`./gradlew testDebugUnitTest`, `assembleRelease`/`lintRelease` where
  practical) for both Android products -- real evidence of compile/lint/unit-test correctness,
  explicitly distinguished from a physical on-device run.
- Structural traffic-content and log-privacy verification (the same technique already proven in
  Milestone 7/8's data-boundary work), reused and extended for every new Phase 8V surface.
- Full migration reconfirmation against a freshly synthesized Phase-7-head database.
- A full, itemized final regression and an honest final release-gate decision.

## What this phase will NOT produce

- A physical Android device validation report with real on-device screenshots/logcat.
- A final `aura-commercial-licensing-operations-phase8-complete` tag -- the governing brief's own
  Part AF explicitly blocks that tag while "physical Android renewal validation is incomplete" or
  "any Part AB scenario remains unverified" (physically). Both are true here for reasons outside this
  session's control. The tag will not be created dishonestly.
- A from-scratch Arabic/RTL localization system for the Owner internal control-room UI. Owner has
  never had one across Phases 5-8 (it is a staff-only internal tool, English-only by consistent
  design, unlike the Retail/Clinic *product* UIs, which do have `tr()`-based Arabic support). Building
  one now would be a large, unscoped, un-precedented addition, not a "closure" of deferred Phase 8
  work. Noted honestly in `phase8v-residual-risk-register.md` rather than silently skipped or faked
  with a handful of hardcoded strings.
