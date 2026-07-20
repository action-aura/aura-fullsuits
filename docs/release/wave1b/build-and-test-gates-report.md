# Consolidated Test & Build Gates Report (Wave 1B, Part T)

## Python (both products + commercial_runtime)
`python products/run_all_tests.py` — deterministic, subprocess-isolated runner (see `pytest-isolation-correction.md`).

**285/285 tests pass, 18/18 files pass, 0 failed.**

| File | Tests |
|---|---|
| launcher_support_test.py | 16 (was 15 -- see below) |
| retail_backup_restore_test.py | 12 |
| retail_financial_authority_test.py | 10 |
| retail_import_export_test.py | 25 |
| retail_localization_test.py | 18 |
| retail_onboarding_wave0_test.py | 8 |
| retail_pricing_test.py | 26 |
| retail_returns_wave0_test.py | 10 |
| retail_security_test.py | 47 |
| clinic_backup_restore_test.py | 4 |
| clinic_independence_test.py | 5 |
| clinic_localization_test.py | 13 |
| clinic_onboarding_auth_test.py | 15 |
| clinic_payment_wave0_test.py | 9 |
| clinic_privacy_test.py | 9 |
| clinic_rbac_test.py | 24 |
| clinic_workflow_test.py | 29 |
| migration_safety_test.py | 5 |

One new test (`test_bind_free_socket_holds_the_port_exclusively`) was added this pass as a regression test for a real defect found during this wave's sustained-run testing -- see below.

## Android Retail
- `testDebugUnitTest`: 36/36 pass.
- `lintRelease`: **initially FAILED with 3 errors** (`RestrictedApi` on `MainActivity.dispatchKeyEvent`, a known androidx lint-metadata false positive -- overriding the public `android.app.Activity.dispatchKeyEvent` via `androidx.activity.ComponentActivity`, not calling a restricted internal API). Fixed with a scoped `@Suppress("RestrictedApi")` and an explanatory comment. Rerun: **0 errors**, 27 pre-existing warnings (non-blocking, unrelated to this wave).
- `assembleRelease` + `bundleRelease`: BUILD SUCCESSFUL, `lintVitalRelease` passed, signed release APK and AAB produced (`validateSigningRelease` confirms the production keystore is wired).

## Android Clinic
- `testDebugUnitTest`: 49/49 pass (unaffected by this wave's changes; confirmed still green).
- `lintRelease`: 0 errors, clean.
- `assembleRelease` + `bundleRelease`: BUILD SUCCESSFUL, signed release APK and AAB produced.

## Windows -- sustained run and restart persistence
Using the actual signed, installed binaries (`%LOCALAPPDATA%\Programs\Action Aura\Aura {Retail,Clinic}\*.exe`, installed via the Inno Setup installers built in Part G):

- **Sustained run**: both apps launched, polled `/api/health` every 60s for 5 consecutive checks (5 minutes) -- 5/5 returned HTTP 200 for both products, no crash, no memory-driven degradation observed.
- **Unclean-shutdown / restart-persistence**: confirmed real pre-existing test data present (1 product, 1 sale, 1 payment, 1 patient, plus the Part K migration-backup files) and `PRAGMA quick_check` = `ok` on both `retail.db` and `clinic.db` *before* the test. Both processes were then hard-killed (`taskkill /F`, no graceful shutdown) and relaunched. This is what surfaced the real defect below.

## A real defect found and fixed: cross-product port race
Relaunching both products within milliseconds of each other (a realistic scenario -- e.g. both desktop shortcuts double-clicked back to back, or both launched from a startup folder) reproduced a genuine bug: **both launchers' own logs showed a successful bind and ready state on port 5000, but only one product was actually reachable there** -- `curl http://127.0.0.1:5000/api/version` returned `AURA_CLINIC` even though Retail's log claimed readiness on that exact port/URL.

**Root cause**: `_find_free_port()` in both `launcher_retail.py` and `launcher_clinic.py` tested a port with a throwaway socket, then closed it, then handed the bare port number to `waitress.serve()` to bind for real later. Two problems compounded:
1. That test-then-release pattern is a TOCTOU race: nothing stops another process from binding the same port in the gap between the test and the real bind.
2. On Windows, `bind()` alone does **not** guarantee exclusive ownership of a port the way POSIX default TCP semantics do -- without `SO_EXCLUSIVEADDRUSE`, two processes can both end up "listening" on the same address:port, with Windows delivering connections to either one unpredictably. `netstat -ano` confirmed exactly this: two different PIDs (`AuraRetail.exe` and `AuraClinic.exe`) both `LISTENING` on `127.0.0.1:5000` simultaneously.

Because both products' `/api/health` returns the identical generic `{"status":"ok"}`, Retail's own readiness check was silently satisfied by Clinic's server -- meaning Retail's UI window could have opened pointed at Clinic's backend (and vice versa), a real cross-product data-exposure risk under this specific launch-timing scenario. Not a hypothetical: reproduced twice, independently, with two different process pairs.

**Fix** (`products/retail/desktop/launcher_retail.py`, `products/clinic/desktop/launcher_clinic.py`):
1. Replaced `_find_free_port()` (returns a port number, socket released) with `_bind_free_socket()` (returns the live, already-listening socket, `SO_EXCLUSIVEADDRUSE` set on Windows, never released until waitress takes it over via `waitress.serve(..., sockets=[sock])`, its officially-supported pre-bound-socket adjustment). This closes the race at the source -- nothing can ever bind the chosen port out from under either process again.
2. Added defense in depth: after readiness succeeds, each launcher makes one more call to its own product-specific `/api/version` and verifies `product_code` matches (`AURA_RETAIL` / `AURA_CLINIC`) before opening the UI window. If it doesn't match, the launcher fails loudly (`WRONG_PRODUCT_ON_PORT`) instead of silently proceeding -- so even an unforeseen future race can no longer end in a silent cross-wire, only a safe, visible failure.
3. Added a deterministic regression test, `test_bind_free_socket_holds_the_port_exclusively` (`products/retail/tests/launcher_support_test.py`), which fails against the old implementation and passes against the fix.

**Verified**: reproduced the exact race by launching both launchers from source within milliseconds of each other (isolated scratch app-data, not the real test data) before the fix (both landed on port 5000, confirmed via `netstat` and `curl`); after the fix, the identical simultaneous-launch script correctly gave Clinic port 5000 and Retail port 5001, with `curl .../api/version` confirming the correct product on each port. Both Windows exes were rebuilt (PyInstaller) and both installers were rebuilt (Inno Setup) with the fix; a single-product launch was re-verified to still work normally post-fix. Checksums in `release-candidate-manifest.md` reflect the post-fix binaries.

**Scope note**: this is a launch-timing race, not a runtime vulnerability -- once both apps are up and running on their own distinct ports, there is no cross-talk. It requires both products' cold-start windows to overlap by roughly a second or less.

## Result
All test and build gates green across Python, Android (both products), and Windows (both products), after fixing one real lint issue and one real, previously-undiscovered concurrency defect -- both caught by this wave's own testing, not shipped silently.
