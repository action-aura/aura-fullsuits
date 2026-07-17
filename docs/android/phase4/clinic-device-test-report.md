# Aura Clinic Android — Device/Emulator Test Report

Status: **NOT TESTED** for all device-dependent steps (no device/emulator
available — see `android-device-testing-guide.md`).

| # | Step | Status | Note |
|---|---|---|---|
| 1 | Install APK | NOT TESTED | Real debug APK exists (`clinic-build-report.md`), never installed |
| 2 | Launch | NOT TESTED | |
| 3 | Start embedded backend | SOURCE REVIEW ONLY | Same pattern as Retail, traced not executed |
| 4 | Reach `/api/health` | SOURCE REVIEW ONLY | Route + fixed readiness-URL confirmed present in the staged code this build packaged |
| 5 | Initialize clean database | NOT TESTED | Backend logic proven by `clinic_onboarding_auth_test.py` (dev-mode) |
| 6 | Complete onboarding | NOT TESTED | |
| 7 | Create first admin account | NOT TESTED | `CreateAdminRequest`/`CreateAdminResponse` field mapping UNIT TEST VERIFIED (`PaymentContractTest.kt`) |
| 8 | Log in | NOT TESTED | |
| 9 | Add synthetic patient | NOT TESTED | |
| 10 | Search patient | NOT TESTED | |
| 11 | Open patient profile | NOT TESTED | |
| 12 | Create appointment | NOT TESTED | |
| 13 | Create invoice | NOT TESTED | Backend logic unchanged/proven by `clinic_workflow_test.py` |
| 14 | Record partial payment | UNIT TEST VERIFIED (request/response shape) | `PaymentContractTest.kt`'s $100-invoice worked example (40 paid → 60 outstanding, `partial` status) — proves the *contract*, not a running app |
| 15 | Attempt duplicate payment | UNIT TEST VERIFIED (request shape only) | `CreatePaymentRequest` now always carries a fresh `idempotency_key`; dedup itself is backend logic proven in `clinic_payment_wave0_test.py` |
| 16 | Attempt overpayment | UNIT TEST VERIFIED (response-shape only) | Rejection-response deserialization proven; the actual HTTP-error-vs-JSON-body nuance is a documented limitation (see `android-residual-risk-register.md`) — Retrofit throws on non-2xx by default, so `PaymentSheet`'s `r.status` check is not the path a real 400 response takes; the generic catch block still prevents a false "success" |
| 17 | Complete valid remaining payment | UNIT TEST VERIFIED (response shape) | $60 after $40 → `paid`, `outstanding_balance: 0.0`, proven via contract test |
| 18 | Restart | NOT TESTED | |
| 19 | Verify persistence | NOT TESTED | |
| 20 | Create backup | NOT TESTED | No backup UI screen exists in either app (grep-confirmed) — backend `/api/backup/create` unchanged and proven at the Python level only |
| 21 | Switch language | NOT TESTED | |
| 22 | Verify RTL | NOT TESTED | `supportsRtl="true"` confirmed in manifest |
| 23 | Verify role navigation | NOT TESTED (also: NOT PRESENT IN SOURCE) | `clinic_role` is deserialized (UNIT TEST VERIFIED round-trip) but never consumed by any navigation-gating code (grep-confirmed) — see `android-risk-register.md` R19 |
| 24 | Inspect Logcat for sensitive data | SOURCE REVIEW ONLY + UNIT TEST VERIFIED | Zero `Log`/`println`/`HttpLoggingInterceptor` calls, now regression-guarded by `PrivacyLoggingGuardTest.kt` — genuine Logcat inspection during a live run was never performed (no device) |
| 25 | Close and restart | NOT TESTED | |

## What actually was proven this phase

Real `assembleDebug`/`assembleRelease`/`bundleRelease` succeed; 17/17
Kotlin unit/contract tests pass (payment worked-example table, onboarding
field mapping, idempotency-key generation, role-field round-trip, and the
privacy-logging static guards); 279/279 backend Python tests pass
including the two files touched this phase
(`products/clinic/backend/api/clinic_api.py`'s privacy fix, verified via
`clinic_workflow_test.py` and the full clinic suite).

## Honest bottom line — the one real known gap

`PaymentSheet`'s success/failure branching (`if (r.status == "success")`)
is written for a JSON body Retrofit would only deserialize on a 2xx
response; the actual overpayment/zero-amount rejections are HTTP 400,
which Retrofit's default suspend-function behavior turns into a thrown
`HttpException` instead — meaning the rejection currently surfaces through
the generic `catch (e: Exception) { error = "Couldn't reach the server" }`
path with a **misleading message** (the request did reach the server; it
was correctly rejected), not through the intended `r.message` branch. The
payment is still correctly **not** recorded either way — this is a UX
-message accuracy gap, not a financial-authority or data-integrity gap —
but it is not fixed in this phase (the same generic-catch pattern is
pre-existing and consistent throughout the rest of both apps' codebases;
fixing it project-wide would be a larger, out-of-scope UX pass). Recorded
honestly in `android-residual-risk-register.md`.
