# Enterprise-Grade Scorecard

Scores are 0-100, evidence-based against findings in `00`-`22`. A score
reflects "how good is this today," not "how good could it become" — no credit
is given for planned/deferred work. Per the audit's own rule: **enterprise-
grade additionally requires zero unresolved P0, financial P1, data-integrity
P1, security P1, or privacy P1** regardless of averaged score — see the
per-entity gate check at the bottom of each table.

## Aura Retail — Windows

| Dimension | Score | Basis |
|---|---|---|
| Functional completeness | 45 | Core POS/inventory/sales solid, but AUDIT-001 (no onboarding) makes the product unreachable by a real user; no export, no printing |
| Financial correctness | 25 | AUDIT-003/004/005/006/007/008/009 — server trusts client money, returns unvalidated, discount unclamped |
| Data integrity | 55 | FKs not enforced (AUDIT-016), one untransacted route (AUDIT-017), but tenant isolation is otherwise clean, WAL mode used correctly |
| Security | 70 | Strong auth/hashing/lockout (`08`), no LAN exposure, no injection beyond one low-severity case; undermined by the financial-trust gaps which are really a security-adjacent design flaw |
| Privacy | 80 | No PII logging found; not a patient-data product, lower inherent exposure |
| Reliability | 50 | Real smoke test passed for what it covers, but core financial writes are unvalidated and onboarding is broken |
| Offline resilience | 75 | Fully local architecture, WAL mode, real restart-persistence proof |
| Performance | 55 | Fast at measured (small) scale; missing indexes are a real structural risk at claimed target scale (`17`) |
| Scalability | 40 | Same basis, unverified at real target scale |
| Usability | 40 | Cannot be onboarded; once bypassed, functional UI |
| Accessibility | 20 | Not assessed at all (`15`) — treated as a real gap, not assumed adequate |
| Localization | 85 | Real, tested, working (en/ar) |
| Platform consistency | N/A here (single-platform score) | — |
| Test maturity | 55 | 116 real tests exist and pass in isolation; combined-run pollution (AUDIT-010) undermines day-to-day trust in the suite; zero coverage of the actual defects found in this audit |
| Deployment maturity | 35 | Real, working PyInstaller build; unsigned, no installer |
| Packaging maturity | 40 | Same basis |
| Update readiness | 5 | Explicitly not built, by design/scope, at any phase to date |
| Backup and recovery | 0 | None exists at all (AUDIT-019) |
| Auditability | 55 | `audit_log` table exists and is written to on key actions; not independently confirmed comprehensive |
| Observability | 30 | Basic startup/lifecycle logging only; no metrics, no health endpoint confirmed |
| Maintainability | 60 | Clean, well-commented code throughout everything read in this audit; the defects found are logic gaps, not code-quality problems |
| Supportability | 30 | No customer-facing docs, no structured error codes, no support contact found |
| Documentation | 55 | Extensive internal engineering docs; zero customer-facing docs |
| Commercial readiness | 15 | Cannot onboard a real customer today |
| **Enterprise readiness** | **10** | **Gate failure: unresolved P0 (AUDIT-001, AUDIT-002-equivalent-N/A-on-Windows, AUDIT-004), financial P1 (AUDIT-003), data-integrity findings, no backup** |

**Gate check**: FAILS enterprise-grade (unresolved P0: AUDIT-001, AUDIT-004;
unresolved financial P1: AUDIT-003; unresolved backup gap: AUDIT-019).

## Aura Retail — Android

Same underlying backend as Windows, so most dimensions inherit Windows' score
except where the client itself differs:

| Dimension | Score | Basis |
|---|---|---|
| Functional completeness | 35 | Same onboarding gap, PLUS no discount UI at all, PLUS zero runtime verification (never run on a device) |
| Financial correctness | 10 | Everything Windows has, plus the Android-specific tax/discount omission (AUDIT-002) — the single worst finding in this audit |
| Data integrity | 55 | Same backend, same findings |
| Security | 68 | Same backend security posture; Android adds the `FLAG_SECURE` gap (AUDIT-021, minor) |
| Privacy | 78 | Same basis as Windows, minor `FLAG_SECURE` deduction |
| Reliability | 20 | Never run on a real device or emulator — BUILD ONLY for all runtime behavior |
| Offline resilience | 70 | Architecturally sound, unverified in practice |
| Performance | 40 | Never measured on-device at all |
| Scalability | 35 | Same basis |
| Usability | 20 | Cannot onboard; missing discount UI; never used on a real screen |
| Accessibility | 15 | Not assessed |
| Localization | 60 | Source migrated and compiles; visual RTL correctness unverified |
| Test maturity | 10 | Zero automated tests of any kind exist for either Android app |
| Deployment maturity | 40 | Real, working Gradle builds for all three variants |
| Packaging maturity | 45 | Clean project structure, correct manifest hygiene |
| Update readiness | 5 | Not built |
| Backup and recovery | 0 | None, plus an actively misleading "coming soon" UI element (AUDIT-027) |
| Auditability | 55 | Same backend |
| Observability | 25 | No crash reporting, no analytics, nothing beyond what the backend logs |
| Maintainability | 60 | Clean Kotlin/Compose code |
| Supportability | 25 | Same gaps as Windows, plus no device-testing history to draw on |
| Documentation | 60 | Phase 4 produced genuinely thorough internal Android docs |
| Commercial readiness | 5 | Cannot onboard, cannot correctly charge tax, never run on a device |
| **Enterprise readiness** | **5** | **Gate failure: same as Windows plus the Android-specific P0 (AUDIT-002)** |

**Gate check**: FAILS enterprise-grade (all of Windows' gate failures, plus
AUDIT-002).

## Aura Retail — Overall product

Weighted toward the worse of the two platforms where a customer would
reasonably deploy either (a mixed Windows-till/Android-tablet estate is a
realistic real-world scenario for a retail chain): **Enterprise readiness: 8**.
FAILS enterprise-grade and, per `24`, fails even the basic "safe for paid
customers" bar today.

## Aura Clinic — Windows

| Dimension | Score | Basis |
|---|---|---|
| Functional completeness | 75 | Broad, complete feature set; only real gap found is missing invoice states (AUDIT-015) and no document-upload feature located |
| Financial correctness | 60 | Invoice computation is genuinely strong (server-side, clamped); payment validation/idempotency gaps (AUDIT-011/012) are real and serious |
| Data integrity | 65 | FKs correctly enforced (unlike Retail); two untransacted money-writing routes (AUDIT-018) |
| Security | 78 | Same strong shared auth posture as Retail, plus a real, diff-verified historical IDOR fix with regression coverage |
| Privacy | 55 | AUDIT-020 (over-broad clinical read access) is a genuine, patient-data-relevant finding that meaningfully caps this score for a clinical product |
| Reliability | 80 | The single most thoroughly real-smoke-tested workflow in the whole audit (13/13 steps, including restart/secret-key-reuse) |
| Offline resilience | 80 | Same architecture, well-proven |
| Performance | 55 | Not independently benchmarked in this pass; same missing-index structural risk (patient name search) |
| Scalability | 45 | Unverified at real target scale |
| Usability | 65 | Real, working onboarding through to daily use |
| Accessibility | 20 | Not assessed |
| Localization | 85 | Real, tested, working |
| Test maturity | 70 | 95 real tests pass in isolation, with the strongest regression coverage of either product (IDOR-fix suite); same combined-run pollution caveat (AUDIT-010) |
| Deployment maturity | 40 | Real, working build; unsigned, no installer |
| Packaging maturity | 42 | Same basis, slightly ahead of Retail (inherited the static-asset fix from day one) |
| Update readiness | 5 | Not built |
| Backup and recovery | 0 | None exists (AUDIT-019) — same as Retail, and arguably more serious given patient data |
| Auditability | 65 | `clinic_audit_log` + explicit `_audit()` calls at every sensitive write observed |
| Observability | 30 | Same basic-logging-only posture as Retail |
| Maintainability | 65 | Clean code, clear Phase-3-fix comments documenting past remediation |
| Supportability | 32 | Same customer-facing-docs gap as Retail |
| Documentation | 55 | Same basis as Retail |
| Commercial readiness | 45 | Materially ahead of Retail — a real customer COULD onboard and use it today, with real limitations |
| **Enterprise readiness** | **28** | **Gate failure: financial P1 (AUDIT-011/012), privacy finding (AUDIT-020), no backup (AUDIT-019) — but meaningfully ahead of Retail's 10** |

**Gate check**: FAILS enterprise-grade (financial P1s unresolved, no backup) —
but does NOT have any unresolved P0.

## Aura Clinic — Android

| Dimension | Score | Basis |
|---|---|---|
| Functional completeness | 65 | Same backend completeness; zero runtime verification |
| Financial correctness | 58 | Same as Windows — server-computed, so Android inherits Clinic's (better) financial architecture correctly, structurally (AUDIT-011/012 still apply, shared) |
| Data integrity | 65 | Same backend |
| Security | 76 | Same posture, minor `FLAG_SECURE` deduction |
| Privacy | 50 | Same AUDIT-020 finding, plus `FLAG_SECURE` (AUDIT-021) which matters more here given patient data on-screen |
| Reliability | 20 | Never run on a device |
| Offline resilience | 75 | Architecturally sound, unverified |
| Performance | 35 | Never measured |
| Scalability | 35 | Unverified |
| Usability | 30 | Never used on a real screen; source suggests a complete, thoughtful UI (patient-detail back-stack handling, etc.) |
| Accessibility | 15 | Not assessed |
| Localization | 60 | Compiles; visual correctness unverified |
| Test maturity | 10 | Zero Android automated tests |
| Deployment maturity | 40 | Real, working builds |
| Packaging maturity | 45 | Clean structure |
| Update readiness | 5 | Not built |
| Backup and recovery | 0 | None, same misleading placeholder issue |
| Auditability | 65 | Same backend |
| Observability | 25 | Same as Retail Android |
| Maintainability | 62 | Clean code |
| Supportability | 28 | Same gaps |
| Documentation | 60 | Same Phase 4 documentation quality |
| Commercial readiness | 30 | Financially sound architecture, but literally never run — cannot be described as ready without device validation |
| **Enterprise readiness** | **18** | **Same financial/privacy/backup gate failures as Windows, plus zero device verification** |

**Gate check**: FAILS enterprise-grade — same reasons as Clinic Windows, plus
no runtime verification of any kind.

## Aura Clinic — Overall product

**Enterprise readiness: 22**. FAILS enterprise-grade, but is the stronger of
the two products by a wide margin on nearly every dimension — see `24` for the
full comparative verdict.
