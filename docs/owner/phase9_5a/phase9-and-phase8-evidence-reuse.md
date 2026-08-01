# Phase 9.5A — Phase 9/Phase 8 Evidence Reuse Decision

## Reused as-is, not re-verified this phase

- All Phase 8/8V-P/8V-P9 commercial-licensing correctness (activation, renewal, expiry, grace,
  emergency extension, device-limit enforcement, stale-assertion rejection, audit chain).
- All Phase 9 infrastructure work (staging Postgres hardening, backup/restore, scheduler, structured
  logging, security headers, dependency remediation, canonical Retail test runner).

No source in `commercial_runtime/`, `owner/app/licensing*`, `owner/app/commercial_ops/device_slot_ops.py`
(the enforcement function itself), or any product (`products/retail`, `products/clinic`,
`android/`) is touched by this phase.

## Not reused — must be freshly established this phase

- Test totals: this phase adds new models/migrations/services/tests; the 987/987 Phase 9 baseline is
  a starting reference, not a substitute for this phase's own regression (Milestone 24+).
- Any claim about pilot/staging readiness — unaffected and unchanged by this phase (still CONDITIONAL
  PASS per Phase 9's own decision; this phase does not touch deployment).

## Rationale

Phase 9.5A's scope is *new commercial-operations domain modeling*, entirely orthogonal to Phase 8's
licensing correctness and Phase 9's deployment/operational hardening. Reusing their functional
evidence is correct; this phase's own new code needs its own new evidence.
