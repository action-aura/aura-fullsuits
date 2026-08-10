# Phase 9 — Staging Acceptance Evidence

Consolidated real evidence index (detail lives in the referenced documents — not duplicated here).

| Acceptance item | Evidence |
|---|---|
| Real staging database, hardened | `postgresql-hardening.md` |
| Real migrations applied | `migration-runbook.md`, `staging-deployment-report.md` item 10 |
| Real RBAC/catalog/offline-policy seed | `staging-deployment-report.md` item 11 |
| Real signing key generated + activated | `postgresql-hardening.md`, `secrets-and-key-management.md` |
| Real license pepper (not dev placeholder) | `postgresql-hardening.md` — `license_pepper_configured: OK` (not `WARNING`) |
| Real liveness/readiness | `health-readiness-contract.md` |
| Real staff account + MFA | `restore-drill-report.md` (created, then survived a full backup/restore cycle) |
| Real full login + MFA flow | `restore-drill-report.md` — real password auth + real TOTP code, end to end |
| Real commercial data (customer/subscription/license) | `restore-drill-report.md` |
| Real audit-chain verification | `restore-drill-report.md`, re-verified again in `scheduled-operations.md` |
| Real backup + restore drill | `restore-drill-report.md` — 2-second real restore time |
| Real scheduled operations | `scheduled-operations.md` |
| Real structured/redacted logging | `observability-architecture.md` |
| Real security-header fix (HSTS staging gap) | `tls-and-security-headers.md` |
| Real dependency vulnerability remediation | `dependency-risk-register.md` — 32 CVEs, 0 remaining |
| Real full regression post-remediation | 987/987 across Owner/commercial_runtime/Retail/Clinic |

## What is NOT in this evidence set (by necessity, not oversight)

Real remote reachability, real public TLS, real staging-connected product builds, real physical
Android/Windows staging activation, real private distribution — all genuinely blocked on
infrastructure (real host + domain) unavailable this session. See `staging-deployment-report.md`'s own
gap table and `phase9-gate-matrix.md`.
