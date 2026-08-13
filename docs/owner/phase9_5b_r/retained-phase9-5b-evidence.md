# Phase 9.5B-R — Retained Phase 9.5B Evidence

Required reading, done in full before writing code:

- `docs/owner/phase9_5b/PHASE9-5B-EMPLOYEE-MANAGEMENT-PORTAL-HANDOVER.md` — file map, real bugs fixed.
- `docs/owner/phase9_5b/phase9-5b-final-decision.md` — verdict PASS, 6 real bugs, tag `61e507e`.
- `docs/owner/phase9_5b/final-gate-matrix.md` — 27/28 PASS, gate #18 (Arabic/RTL) explicitly
  NOT VERIFIED/DEFERRED — this is the exact gate Phase 9.5B-R exists to close.
- `docs/owner/phase9_5b/final-residual-risk-register.md` — 5 real deferrals, all still valid, none
  touched by this phase (i18n doesn't change onboarding/mobile-auth/commission-plan-UI/heartbeat-rate-limit
  gaps).
- `docs/owner/phase9_5b/final-regression-report.md` — 548/548 baseline this phase must not regress.
- `docs/owner/phase9_5b/employee-list-ui-contract.md`, `employee-detail-ui-contract.md`,
  `employee-self-profile-contract.md` — real field/section inventory used to build the string audit.
- `docs/owner/phase9_5b/first-login-and-mfa-flow.md` — confirms auth/MFA/setup routes are fully reused
  from Phase 4/5/6, unchanged; this phase localizes their templates without touching their logic.
- `docs/owner/phase9_5b/employee-audit-implementation.md` — real audit action-code list, used for the
  audit-label localization mapping (Milestone 11).
- `docs/owner/phase9_5a/internal-api-contract.md` — confirms `/api/operations/v1` standards (stable error
  codes, no translated field names) — directly informs `api-localization-boundary.md`.
- `docs/owner/phase9_5a/mobile-authentication-adr.md` (the real file; spec's named
  `mobile-technology-adr.md` doesn't exist) — confirms no mobile client/API consumes translated text yet.

## What this phase must not re-litigate

Every real bug fixed in Phase 9.5B (suspension not blocking login, last-SUPER_ADMIN gap, presence-session
revocation gap, employee-number race, preflight false-failure) is closed and unchanged — this phase only
adds a presentation layer on top of that already-correct logic. No employee-lifecycle, RBAC, or session
behavior is touched.
