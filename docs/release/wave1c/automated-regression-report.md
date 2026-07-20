# Wave 1C -- Automated Regression Report (Part B)

## Python / backend (via `products/run_all_tests.py`, per-file subprocess isolation)
**285/285 passing, 18/18 files, 0 failed.**

| File | Tests | Result |
|---|---|---|
| `products/retail/tests/launcher_support_test.py` | 16 | PASS (includes REL-006 regression test) |
| `products/retail/tests/retail_backup_restore_test.py` | 12 | PASS |
| `products/retail/tests/retail_financial_authority_test.py` | 10 | PASS |
| `products/retail/tests/retail_import_export_test.py` | 25 | PASS |
| `products/retail/tests/retail_localization_test.py` | 18 | PASS |
| `products/retail/tests/retail_onboarding_wave0_test.py` | 8 | PASS |
| `products/retail/tests/retail_pricing_test.py` | 26 | PASS |
| `products/retail/tests/retail_returns_wave0_test.py` | 10 | PASS |
| `products/retail/tests/retail_security_test.py` | 47 | PASS |
| `products/clinic/tests/clinic_backup_restore_test.py` | 4 | PASS |
| `products/clinic/tests/clinic_independence_test.py` | 5 | PASS |
| `products/clinic/tests/clinic_localization_test.py` | 13 | PASS |
| `products/clinic/tests/clinic_onboarding_auth_test.py` | 15 | PASS |
| `products/clinic/tests/clinic_payment_wave0_test.py` | 9 | PASS |
| `products/clinic/tests/clinic_privacy_test.py` | 9 | PASS |
| `products/clinic/tests/clinic_rbac_test.py` | 24 | PASS |
| `products/clinic/tests/clinic_workflow_test.py` | 29 | PASS |
| `commercial_runtime/tests/migration_safety_test.py` | 5 | PASS |

Plus two new Wave 1C audit-only files (not counted in the 285 baseline, run and reported separately in `financial-release-gate-report.md`): `products/retail/tests/wave1c_financial_gate_test.py` (4/4) and `products/clinic/tests/wave1c_financial_gate_test.py` (8/8).

Environment: Windows 11 Pro, Python 3.11.9, repo at tag `commercial-packaging-wave1b-complete` (`157521c`).

## Android Retail
- Unit tests: **36/36 passing**, 0 skipped
- `lintDebug`: 0 errors, 27 warnings -- BUILD SUCCESSFUL
- `lintRelease`: 0 errors, 27 warnings -- BUILD SUCCESSFUL (RestrictedApi suppression confirmed still in place and effective)
- `assembleRelease` / `bundleRelease`: BUILD SUCCESSFUL, signed APK+AAB produced, bit-identical to the Wave 1B-shipped artifacts

## Android Clinic
- Unit tests: **49/49 passing**, 0 skipped
- `lintDebug`: 0 errors, 23 warnings -- BUILD SUCCESSFUL
- `lintRelease`: 0 errors, 23 warnings -- BUILD SUCCESSFUL
- `assembleRelease` / `bundleRelease`: BUILD SUCCESSFUL, signed APK+AAB produced, bit-identical to the Wave 1B-shipped artifacts

Full detail (signing verification, certificate fingerprints, manifest safety checks): `android-release-gate-report.md`.

## Windows
- `launcher_support_test.py` (16/16, part of the 285 above) includes the REL-006 port-race regression test (`test_bind_free_socket_holds_the_port_exclusively`).
- `commercial_runtime/tests/migration_safety_test.py` (5/5) covers schema-migration safety.
- A full hands-on install/launch/version-endpoint/product-identity/upgrade/uninstall/reinstall/crash-recovery pass was additionally run this wave against the real installers (not just unit tests) -- see `windows-release-gate-report.md` and `data-integrity-and-zero-loss-gate.md` for the complete narrative and results (all PASS, zero defects found).

## Grand total
**285 (Python baseline) + 12 (Wave 1C financial audit tests) + 36 (Android Retail) + 49 (Android Clinic) = 382 automated test executions this wave, 382 passed, 0 failed, 0 skipped**, plus a full hands-on Windows lifecycle pass and two independent manual backup/restore adversarial tests (corrupted archive, cross-product restore) outside the pytest suite. No mandatory suite was skipped.
