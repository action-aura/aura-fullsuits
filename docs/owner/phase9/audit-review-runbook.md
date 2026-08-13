# Phase 9 — Audit Review Runbook

## Periodic chain verification (real, automated — Milestone 9)

`verify_chain()` (real, existing Phase 8 function) now runs as part of the real daily scheduled-ops
bundle (`scheduled-operations.md`), not just manually. A `False` result is the single highest-severity
row in `alert-catalog.md` (P0) and the corresponding `incident-response-plan.md` category applies
immediately — do not attempt to "fix" a broken chain by editing data.

## Anomaly handling

An anomaly here means: an audit event exists that doesn't correspond to an expected real action (e.g.
a `LICENSE_REVOKED` with no matching support ticket/incident), or an expected action has no
corresponding audit event (should be structurally impossible — every real mutating Owner service call
already goes through `audit_record()`, confirmed by code convention throughout this codebase — but
worth a periodic spot-check, not blind trust).

## Access review (quarterly, real operational habit)

Review every account with `Super Admin`, `Finance`, or `Sales` role — confirm each still needs it,
confirm MFA is enrolled and confirmed for every Super Admin (the real `super_admin_mfa_required`
preflight/readiness check already enforces this technically; the review is the human confirmation that
the *list* of Super Admins itself is still correct).

## Privileged-action review

Sample a real set of recent high-privilege audit events (role assignment, signing-key
generation/activation, license revocation, emergency-extension grants) each review cycle — confirm each
has a legitimate, traceable business reason, not just that it happened.

## Failed-MFA review

Repeated failed-MFA attempts for one account, reviewed alongside failed-login data — the same signal
`incident-response-plan.md`'s "Suspicious login" row watches for in real time; this is the periodic,
retrospective complement to that real-time alert.

## Emergency-extension and device-exception review

Both mechanisms are explicitly *temporary* by design (Phase 8) — review confirms none were left
open-ended beyond their real, time-bound `expires_at`, matching the real precision behavior Phase 8V-P9
fixed and physically proved (`docs/owner/phase8vp9/temporary-exception-precision-final.md`).
