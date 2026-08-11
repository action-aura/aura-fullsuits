# Phase 9 Milestone 16 — Incident Response Plan

No secret values appear anywhere in this document — only category, procedure, and role.

| Category | Severity | Detection | Immediate containment | Recovery |
|---|---|---|---|---|
| Owner unavailable | P0 | `/health/live` alert | Check container status, restart `owner` service | `disaster-recovery-runbook.md` if restart doesn't resolve |
| PostgreSQL unavailable | P0 | `/health/ready` `database_connectivity: FAIL` | Check `db` container/host disk/memory | `disaster-recovery-runbook.md` |
| Staging host unreachable | P0 | External uptime check fails | Confirm via out-of-band access (console, not SSH if SSH is the thing that's down) | `disaster-recovery-runbook.md` |
| TLS certificate issue | P1 | Caddy log / browser warning report | Confirm Caddy's ACME renewal didn't fail; check DNS still points correctly | Manual cert renewal trigger if automatic renewal failed |
| Signing-key issue | P0 | `active_signing_key_exists: FAIL` | Do not attempt to guess-fix; follow `key-rotation-runbook.md`'s emergency section | Rotate, regenerate trust anchor for future builds |
| License-pepper mismatch | P0 | `license_pepper_configured`/`self_test_roundtrip: FAIL` | Stop — do not attempt to "fix" by guessing a pepper value | Restore `.env.staging` from its own secure backup |
| Activation failure spike | P2 | `ACTIVATION_FAILED` rate alert | Check Owner logs (redacted, correlation-ID traceable) for the real `reason_code` | Depends on root cause — usually a client-side config issue, not server |
| Check-in failure spike | P2 | `CHECK_IN_FAILED` rate alert | Same | Same |
| Stale-assertion rejection (unexpected) | **P1** | `ASSERTION_STALE_REJECTED` outside a known test | **Possible replay attempt** — preserve logs before any other action, do not restart the affected installation's session | Follow the compromised-device-identity row below |
| Device-limit anomaly | P2 | `device-limit-scan` finding outside an expected exception | Review via Owner UI, confirm no unauthorized device-slot exception was granted | Manual `release_device_slot`/`replace_device_slot` |
| Incorrect commercial state | P1 | Customer report or `reconcile` finding | Reproduce via read-only queries first, never a direct DB edit | Use the real Owner service functions (transition_*), never `UPDATE` by hand |
| Backup failure | P0 | Scheduler job `ok: false` | Check disk space / DB connectivity first (most common causes) | Manual re-run; escalate if it fails twice |
| Restore failure | P0 | Only ever discovered during a drill or a real DR event | Do not restore over the live database mid-diagnosis | Try an older backup; if all fail, this is the most severe possible finding — full incident |
| Disk full | P0 | node-exporter alert | Identify what's growing (logs? backups not pruning?) before deleting anything | Prune per retention policy, never ad hoc |
| High error rate | P1 | HTTP 5xx alert | Check recent deploys/migrations first | Rollback per `migration-runbook.md` if a recent migration is the cause |
| Suspicious login | P1 | Repeated failed-login alert from one source, or a login from an unexpected pattern | Do not lock the legitimate account without confirming first | Force password reset if confirmed malicious |
| MFA loss | P2 | Staff support request | Verify identity out-of-band before any reset | `reset_mfa()` (real, existing, audited Phase 4 function) — never a silent bypass |
| Compromised staff account | P0 | Any of the above escalated, or direct report | Disable the account immediately (`disable_staff`, real existing function), review its recent audit trail | Reset credentials, MFA, review every action it took during the suspected window |
| Compromised device identity | P1 | Confirmed replay/stale-assertion pattern tied to one device | Revoke that installation, do not simply "wait and see" | Real device replacement flow (Phase 8, already proven) |
| Suspected secret exposure | P0 | Accidental commit, log leak, etc. | Rotate the specific secret immediately (`key-rotation-runbook.md`) | Confirm via a real secret scan (Milestone 10 tooling) that nothing else leaked alongside it |
| Suspected customer-domain data leakage | P0 | Any report of patient/sales/receipt data appearing where it shouldn't | Stop the specific data flow immediately | Should be structurally impossible per `network-and-trust-boundaries.md` — if it happens, this is itself evidence of an undiscovered architecture violation and needs a full review, not just a patch |

## Closure criteria (every category)

The incident is not closed until: root cause is understood (not just "it stopped happening"), the
specific fix is verified (not assumed), and — for P0/P1 — a post-incident review is written, even a
short one, and filed alongside `daily-pilot-review-template.md` for that day.

## Escalation and communication

See `support-escalation-matrix.md` and `incident-communication-templates.md`.
