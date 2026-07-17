# Aura Clinic Android — Parity Matrix (Phase 4V)

Every FAIL below is explained; none are unexplained.

| Area | Status | Note |
|---|---|---|
| Build system | BUILD VERIFIED | Real `clean`/`assembleDebug`/`assembleRelease`/`bundleRelease` succeed (`clinic-build-report.md`) |
| Compose | BUILD VERIFIED | Compiles clean; UI never rendered (no device) |
| Chaquopy/backend | BUILD VERIFIED | Python source stages/compiles; server startup never exercised on-device |
| Health readiness | PASS WITH DOCUMENTED LIMITATION | Fixed this phase (`/api/health`), same caveats as Retail |
| Onboarding | REQUIRES PHYSICAL DEVICE | Field mapping UNIT TEST VERIFIED (`PaymentContractTest.kt`); UI flow never run |
| Authentication | REQUIRES PHYSICAL DEVICE | |
| Database | REQUIRES PHYSICAL DEVICE | |
| Dashboard | REQUIRES PHYSICAL DEVICE | |
| Patients | REQUIRES PHYSICAL DEVICE | |
| Patient profile | REQUIRES PHYSICAL DEVICE | |
| Appointments | REQUIRES PHYSICAL DEVICE | |
| Visits | REQUIRES PHYSICAL DEVICE | |
| Prescriptions | REQUIRES PHYSICAL DEVICE | |
| Invoices | REQUIRES PHYSICAL DEVICE | Invoice creation itself is staff-discretionary (client-supplied discount/tax_rate) by design — not a Wave 0 financial-authority target, unchanged this phase |
| Payments | PASS | **Fixed this phase.** Idempotency-key gap closed, response now typed and checked, worked-example contract (0/negative/40/duplicate/70/60 on a $100 invoice) fully unit-tested against the corrected Wave 0 API shape |
| Payment idempotency | PASS | `CreatePaymentRequest.idempotency_key` now always populated with a fresh UUID per attempt |
| Doctors | REQUIRES PHYSICAL DEVICE | Unchanged from prior migration |
| Lab expenses | REQUIRES PHYSICAL DEVICE | Backend privacy fix applied this phase (PII-log-leak in the accounting-sync block), UI unchanged |
| Notes | REQUIRES PHYSICAL DEVICE | |
| Roles | PASS WITH DOCUMENTED LIMITATION | `clinic_role` field round-trips correctly (unit-tested); **no client-side role-gated navigation exists in source** — backend RBAC (`clinic_rbac_test.py`) is the real enforcement boundary; not a regression, and not added this phase (would be a new feature, out of migration-correction scope) |
| Localization | REQUIRES PHYSICAL DEVICE | |
| RTL | REQUIRES PHYSICAL DEVICE | `supportsRtl="true"` confirmed |
| Privacy/logging | PASS | Zero `Log`/`println`/`HttpLoggingInterceptor` in Kotlin (regression-guarded this phase); two backend PII-log-leak paths fixed this phase (`clinic_api.py`); `allowBackup=false`; no CAMERA permission (no barcode feature) |
| Backup | NOT PRESENT IN SOURCE | No backup/restore UI screen exists (grep-confirmed); backend endpoint proven at the Python level only |
| Offline operation | SOURCE REVIEW ONLY | Architecture unchanged; never observed on a device |
| Restart persistence | REQUIRES PHYSICAL DEVICE | |
| Security configuration | PASS | Loopback-only cleartext exception, single exported component, no FileProvider |
| Signing | PASS WITH DOCUMENTED LIMITATION | Unsigned by design, verified via `jarsigner` this phase |
