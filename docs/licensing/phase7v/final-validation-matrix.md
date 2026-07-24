# Phase 7V — Final Validation Matrix (Part Q)

> **Phase 7V-F update:** see `docs/licensing/phase7v-final/final-validation-matrix.md` for the
> current matrix — Retail Windows upgrade and live restricted-mode rows below moved from NOT
> VERIFIED to PASS, and a real P0 in trusted-time offline enforcement was found and fixed. This
> document preserved unmodified below for history.

Legend: **PASS** / **FAIL** / **NOT VERIFIED** / **N/A**. No mandatory row is marked PASS without
evidence cited elsewhere in `docs/licensing/phase7v/`.

## OWNER

| Row | Result | Evidence |
|---|---|---|
| Full suite | **PASS** (186/186) | `owner-regression-closure.md` |
| Backup/restore (real pg_dump/pg_restore) | **PASS** (7/7) | `owner-regression-closure.md`, `toolchain-closure-report.md` |
| Activation | **PASS** | included in 186/186 |
| Replay | **PASS** | included in 186/186 |
| Rate limits | **PASS** | included in 186/186 |
| Idempotency | **PASS** | included in 186/186 |
| Signing keys | **PASS** | included in 186/186 |
| Concurrency | **PASS** | included in 186/186 |
| Assertions | **PASS** | included in 186/186 |
| Data boundary | **PASS** | included in 186/186 |
| Audit | **PASS** | included in 186/186 |

## PRODUCT PYTHON

| Row | Result | Evidence |
|---|---|---|
| Clinic backend | **PASS** (11 files) | `product-test-runner-closure.md` |
| Retail backend | **PASS** (12 files) | `product-test-runner-closure.md` |
| licensing_contracts | **PASS** (22 files) | `product-test-runner-closure.md` |
| Capability guards | **PASS** | `test_capability_guard.py`, `test_flask_guard.py`, `*_capability_guard_test.py` — all in the 46/46 |
| Migration safety | **PASS** | `*_phase7_migration_test.py`, `migration_safety_test.py` |
| Financial regression | **PASS** | `retail_financial_authority_test.py`, `wave1c_financial_gate_test.py` (both products), + live 88.00 re-confirmation |
| Returns | **PASS** | `retail_returns_wave0_test.py` |
| Payments | **PASS** | `clinic_payment_wave0_test.py` |
| Backup/restore (product-side SQLite) | **PASS** | `*_backup_restore_test.py`, + live `create_backup()` call in Part F |
| Launcher | **PASS** | `launcher_support_test.py` |
| REL-006 | **N/A** | no such tracked item found in this repo's docs/tests; not applicable |
| Combined isolated runner | **PASS** (46 files, 585 tests, 0 failed) | `product-test-runner-closure.md` |

## ANDROID CLINIC

| Row | Result | Evidence |
|---|---|---|
| Unit tests | **PASS** | `testReleaseUnitTest` in `android-rc2-signed-build-report.md` |
| Licensing tests | **PASS** | unit-level, see above |
| Assertion tests | **PASS** | `test_assertion_verifier.py` (shared core) |
| State-machine tests | **PASS** | `test_state_machine.py` (shared core) |
| Kotlin/Python sync tests | **PASS** (unit) | `test_internal_sync_routes.py` |
| Arabic audit | **PASS** | `HardcodedStringAuditTest` (part of `testReleaseUnitTest`) |
| Privacy regression | **NOT VERIFIED** (physical) | no device — `android-clinic-physical-licensing-report.md` |
| lintRelease | **PASS** (after fix) | `android-rc2-signed-build-report.md` |
| assembleRelease | **PASS** | same |
| bundleRelease | **PASS** | same |
| Signing verification | **PASS** | `android-certificate-continuity.md` |
| Physical upgrade | **NOT VERIFIED** | `android-physical-signed-upgrade-evidence.md` |
| Physical lifecycle | **NOT VERIFIED** | `android-clinic-physical-licensing-report.md` |

## ANDROID RETAIL

| Row | Result | Evidence |
|---|---|---|
| Unit tests | **PASS** | `android-rc2-signed-build-report.md` |
| Licensing tests | **PASS** | unit-level |
| Assertion tests | **PASS** | shared core |
| State-machine tests | **PASS** | shared core |
| Kotlin/Python sync tests | **PASS** (unit) | shared core |
| Financial regressions | **PASS** | live 88.00 re-confirmation + 26+10 unit tests |
| Barcode regression | **NOT VERIFIED** (physical) | no device |
| Receipt-share regression | **NOT VERIFIED** (physical) | no device |
| lintRelease | **PASS** (after fix) | `android-rc2-signed-build-report.md` |
| assembleRelease | **PASS** | same |
| bundleRelease | **PASS** | same |
| Signing verification | **PASS** | `android-certificate-continuity.md` |
| Physical upgrade | **NOT VERIFIED** | `android-physical-signed-upgrade-evidence.md` |
| Physical lifecycle | **NOT VERIFIED** | `android-retail-physical-licensing-report.md` |

## WINDOWS

| Row | Result | Evidence |
|---|---|---|
| Frozen build | **PASS** (both products, after fixing a stale-cache startup crash) | `windows-rc2-build-report.md` |
| Installer build | **PASS** (both products) | `windows-rc2-build-report.md` |
| Clean install | **PASS** | `windows-rc1-to-rc2-installer-validation.md` |
| rc.1 → rc.2 upgrade | **PASS** (Clinic, full cycle with real data); **NOT VERIFIED** (Retail, no pre-existing rc.1 data to upgrade from this session) | `windows-rc1-to-rc2-installer-validation.md` |
| Restart | **PASS** | same doc |
| Offline | **PASS** (live, real Owner outage) | same doc |
| Restricted mode | **PASS** for WARNING/GRACE_PERIOD (live); **NOT VERIFIED** for RESTRICTED (policy-config-dependent, plan used WARN_ONLY) | same doc |
| Uninstall/reinstall | **PASS** (Clinic) | same doc |
| Data preservation | **PASS** | same doc, SQLite integrity checks |
| Security artifact inspection | **PASS** | `release-artifact-security-inspection.md`, `windows-commercial-build-security-review.md` |

## Summary of mandatory gaps

1. Physical Android device validation (Parts J–N): **NOT VERIFIED**, no device connected.
2. Retail Windows full installer-level rc.1→rc.2 upgrade with created data: **NOT VERIFIED** this
   session (no pre-existing rc.1 Retail install with data; activation/financial/deactivation
   lifecycle on the fixed frozen exe was verified live instead).
3. `RESTRICTED` license state: **NOT VERIFIED live** (reachable only under a non-`WARN_ONLY` plan
   policy; fully covered at the unit level).

Per the governing spec's Definition of Done, gap #1 alone is sufficient to withhold the final
Phase 7V closing tag this session.
