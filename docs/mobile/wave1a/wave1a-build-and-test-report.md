# Wave 1A Build & Test Report

## Kotlin unit tests
- Retail: 29/29 passing (unchanged from Phase 4 baseline; MOB-006 fix was a nav-graph string correction with no new test surface, verified via manual device retest).
- Clinic: 45/45 passing (17 baseline + 10 `ApiErrorsTest` payment-error tests + 7 `ClinicSessionTest` role tests + 7 `ApiErrorsTest` login-error tests + 4 `ApiErrorsTest` appointment-error tests).

## Lint
- Retail: `lintDebug` clean, no errors.
- Clinic: `lintDebug` clean, 0 errors, 23 pre-existing warnings (not introduced this wave).

## Builds
- Retail: `assembleDebug`, `assembleRelease`, `bundleRelease` all succeed.
- Clinic: `assembleDebug`, `assembleRelease`, `bundleRelease` all succeed — the release build's fresh Chaquopy pip install log re-confirms `requests==2.32.3` (MOB-002's fix) is present for the release variant, not just debug.

## Backend (Python) regression
`clinic_api.py` was modified this wave (`from_date` range-query addition for MOB-005's appointment-visibility follow-up). All 8 Clinic test files pass individually, 108/108 tests total:
`clinic_backup_restore_test.py` (4), `clinic_independence_test.py` (5), `clinic_localization_test.py` (13), `clinic_onboarding_auth_test.py` (15), `clinic_payment_wave0_test.py` (9), `clinic_privacy_test.py` (9), `clinic_rbac_test.py` (24), `clinic_workflow_test.py` (29).

Running the full `products/clinic/tests/` directory as one pytest invocation produces ~70+ failures (`no such table: users`). This was investigated and proven, via `git stash` (reverting the `from_date` change and re-running the identical combination), to be a **pre-existing cross-file test-isolation issue unrelated to this wave's change** — the identical failure count (23 failed / 30 passed for workflow+rbac together) reproduces with the change fully reverted. Registered as residual risk R-2; not a regression, not blocking.

Retail's backend was not touched this wave (only Kotlin/UI changes); the full 279-test shared-runtime suite rerun was not required per the original spec's own conditional ("only if shared backend/runtime touched") and was not performed.

## Real-device verification
Every fix in the defect registry (MOB-001 through MOB-007) was verified against the live embedded backend on the physical device — either via direct API calls proving the exact wire-level behavior, or via a full rebuild + reinstall + real user-driven UI retry, or both. No fix in this wave was claimed as verified based on code review alone.
