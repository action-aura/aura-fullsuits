# Release Gates

Statuses: PASS · CONDITIONAL PASS · FAIL · NOT APPLICABLE.

## GATE 1 — Safe for internal testing

Requirements: application starts; no destructive schema issue; no known
credential bypass; synthetic data only.

| | Retail Windows | Retail Android | Clinic Windows | Clinic Android |
|---|---|---|---|---|
| Application starts | PASS (real smoke test) | PASS (real build; not run on device, but starts per source) | PASS (real smoke test) | PASS (same basis) |
| No destructive schema issue | PASS | PASS | PASS | PASS |
| No known credential bypass | PASS (`08`) | PASS | PASS | PASS |
| **Gate 1 overall** | **CONDITIONAL PASS** — starts, but AUDIT-001 means "testing" is limited to developer-inserted accounts, not a real first-run flow | **CONDITIONAL PASS** — same, plus never run on a device | **PASS** | **CONDITIONAL PASS** — never run on a device |

## GATE 2 — Safe for controlled pilot

Requirements: core workflows work; financial P0/P1 resolved; privacy P0/P1
resolved; backups exist; support process exists.

| | Retail Windows | Retail Android | Clinic Windows | Clinic Android |
|---|---|---|---|---|
| Core workflows work | **FAIL** — cannot onboard (AUDIT-001) | **FAIL** — same, plus no tax/discount (AUDIT-002) | PASS | PASS (architecturally; unverified on device) |
| Financial P0/P1 resolved | **FAIL** — AUDIT-003/004 open | **FAIL** — AUDIT-002/003/004 open | **FAIL** — AUDIT-011/012 open | **FAIL** — same (shared backend) |
| Privacy P0/P1 resolved | PASS (no P0/P1 privacy finding for Retail) | PASS | **FAIL** — AUDIT-020 is P2, not P1, so technically PASS at the P0/P1 bar, but flagged as a real gap | Same as Windows — technically PASS at the P0/P1 bar |
| Backups exist | **FAIL** — AUDIT-019 | **FAIL** | **FAIL** | **FAIL** |
| Support process exists | Not assessed by this audit (organizational, not technical) — **NOT APPLICABLE** to a code audit | Same | Same | Same |
| **Gate 2 overall** | **FAIL** | **FAIL** | **FAIL** | **FAIL** |

Note: Clinic's privacy row technically passes the stated P0/P1 threshold
(AUDIT-020 is classified P2), but the backup and payment-financial failures
are independently sufficient to fail Gate 2 regardless.

## GATE 3 — Safe for paid SMB customers

Requirements: financial correctness; reliable data persistence; role
enforcement; onboarding; backup/restore; commercial packaging; basic
licensing readiness; documented support.

| | Retail Windows | Retail Android | Clinic Windows | Clinic Android |
|---|---|---|---|---|
| Financial correctness | **FAIL** | **FAIL** | **FAIL** (payment gaps) | **FAIL** |
| Reliable data persistence | CONDITIONAL PASS — proven for the happy path, real gaps in 2 routes (AUDIT-017/018) | Same, unverified on device | CONDITIONAL PASS | Same, unverified on device |
| Role enforcement | PASS (subsystem-level; no fine-grained role model, but not proven broken) | PASS | CONDITIONAL PASS — write-side correct, read-side gap (AUDIT-020) | Same |
| Onboarding | **FAIL** (AUDIT-001) | **FAIL** | PASS | PASS (unverified on device) |
| Backup/restore | **FAIL** | **FAIL** | **FAIL** | **FAIL** |
| Commercial packaging | **FAIL** — unsigned, no installer (AUDIT-022) | **FAIL** — unsigned (AUDIT-023) | **FAIL** — same as Windows | **FAIL** — same as Android Retail |
| Basic licensing readiness | NOT APPLICABLE — explicitly out of scope for every phase to date by design, not a defect | Same | Same | Same |
| Documented support | **FAIL** — no customer-facing docs/support contact found anywhere | Same | Same | Same |
| **Gate 3 overall** | **FAIL** | **FAIL** | **FAIL** | **FAIL** |

## GATE 4 — Enterprise grade

Requirements: mature RBAC; auditability; high test coverage; observability;
controlled updates; disaster recovery; scale validation; security hardening;
operational documentation; compliance-readiness controls; consistent
cross-platform behavior.

| | Retail | Clinic |
|---|---|---|
| Mature RBAC | **FAIL** — subsystem-level only, no fine-grained roles | CONDITIONAL PASS — real doctor-role gating exists, but read-side gap (AUDIT-020) |
| Auditability | CONDITIONAL PASS — `audit_log` exists and is used | CONDITIONAL PASS — `clinic_audit_log` exists, more consistently invoked per the routes sampled |
| High test coverage | **FAIL** — real gaps proven by this audit (every defect found was untested; Android has zero tests) | **FAIL** — same |
| Observability | **FAIL** — no metrics, no health endpoint confirmed | **FAIL** — same |
| Controlled updates | **FAIL** — not built | **FAIL** — not built |
| Disaster recovery | **FAIL** — no backup/restore at all | **FAIL** — same |
| Scale validation | **FAIL** — not measured at target scale, structural index gaps found | **FAIL** — same |
| Security hardening | CONDITIONAL PASS — strong fundamentals, real financial-trust gaps | CONDITIONAL PASS — strongest of the two, still has payment-validation gaps |
| Operational documentation | CONDITIONAL PASS — extensive internal docs, no customer-facing ops docs | Same |
| Compliance-readiness controls | **FAIL** — no privacy notice, no data-retention policy found, no export capability | **FAIL** — same, more consequential given patient data |
| Consistent cross-platform behavior | **FAIL** — proven inconsistent (`05`) | PASS — proven consistent by architecture (`05`) |
| **Gate 4 overall** | **FAIL** | **FAIL** |

## Summary

No product/platform combination passes Gate 2 or beyond today. Clinic passes
Gate 1 cleanly (Windows) or conditionally (Android, pending device
verification); Retail passes Gate 1 only conditionally on both platforms due
to the onboarding gap. This is a consistent, evidence-based picture across
every gate: **nothing in this audit's scope should be sold to a paying
customer today**, and Clinic is closer to being ready than Retail on every
gate examined.
