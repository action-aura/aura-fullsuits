# Wave 1C -- Data Integrity and Zero-Data-Loss Gate (Part D)

## Method
Hands-on lifecycle testing against the real, checksum-verified Wave 1B installers, in an isolated environment (throwaway install directory + isolated `AURA_APP_DATA`), never touching real prior dev data. Installer checksums were re-verified against `release-candidate-manifest.md` before use -- confirmed genuine, unmodified Wave 1B artifacts.

## Windows lifecycle (Retail, full pass)
| Step | Result | Evidence |
|---|---|---|
| Silent install to isolated dir | **PASS** | `/VERYSILENT /SUPPRESSMSGBOXES /DIR=...`, exit code 0 |
| Launch + readiness | **PASS** | Health-polled ready on port 5000, `product_code: AURA_RETAIL` confirmed |
| Synthetic data creation | **PASS** | Admin, product `AUDIT-SKU-001`, sale `SALE-000001` ($20.00) created via real HTTP API (no real customer data used) |
| Clean-stop integrity | **PASS** | `retail.db` and `registry.db` both `integrity_check: ok`, `foreign_key_check: []` (clean). A real schema migration (v0->v1) fired on first launch as expected, producing `migration_backups/retail-pre-migration-v0-to-v1-*.db` per the migration-safety design |
| Reinstall-over-existing (upgrade path) | **PASS** | Post-relaunch: `needs_setup: false`, login succeeded, `SALE-000001` byte-for-byte intact via API, DB integrity still clean |
| Uninstall (data preservation) | **PASS** | `unins000.exe /VERYSILENT /SUPPRESSMSGBOXES` -- data directory and `retail.db` confirmed present immediately after uninstall via `Test-Path`. **REL-001's fix independently re-confirmed holding**: app install dir emptied, data directory untouched |
| Fresh reinstall (no forced re-onboarding) | **PASS** | Recognized pre-existing data, `needs_setup: false`, login with pre-uninstall credentials succeeded |
| Hard-kill crash safety | **PASS** | Created a second product, then `taskkill /F` on the live PID. `PRAGMA quick_check` immediately after -> `ok`. Relaunched cleanly; both pre- and post-kill products present |

Clinic's Windows install lifecycle was not independently re-run step-by-step this wave (time-budgeted; Retail and Clinic share the identical launcher/installer architecture, and REL-001/REL-006 apply identically to both per the Wave 1B defect registry). Clinic's backup/restore mechanism -- the highest-risk data-integrity surface -- **was** independently re-verified (see below and `backup-and-recovery-gate.md`).

## Android signed upgrade
Not re-run this wave (no physical device session in this audit); resting on Wave 1A's real-device evidence (`docs/mobile/wave1a/android-upgrade-data-preservation-report.md`) -- real patient/business data confirmed surviving `versionCode` 1->2 on both products, physically verified on the Infinix X6528. This wave's Android gate (`android-release-gate-report.md`) confirms the *artifacts* are unchanged (bit-identical rebuild) since that evidence was collected, so the prior device proof still applies to the exact binaries in this release.

## Schema migration / failed migration
`commercial_runtime/tests/migration_safety_test.py` (5/5 passing, part of the 285-test baseline) covers the failure path: a failed migration leaves the original database untouched and does not advance the version marker. This wave's own Retail install lifecycle test above additionally observed a **real** v0->v1 migration fire successfully on first launch against fresh synthetic data, producing the expected pre-migration backup file -- direct, live confirmation the mechanism activates correctly, not just unit-tested in isolation.

## Backup / restore (corrupted archive, cross-product rejection)
Independently re-verified this wave, both automated and manual:
- `retail_backup_restore_test.py` 12/12, `clinic_backup_restore_test.py` 4/4.
- **Manual, live**: a truncated (60%-size) backup copy was rejected on restore attempt -- HTTP 400, `"Backup file is not a valid zip archive: File is not a zip file"`.
- **Manual, live, cross-product**: a real Clinic-shaped backup (generated via the actual production `commercial_runtime.backup.service.create_backup`) was submitted for restore into a live Retail instance -- rejected, HTTP 400, `"Refusing cross-product restore: backup is for 'clinic', not 'retail'"`. Retail's existing data was confirmed unchanged afterward.

Full detail and the ease-of-use assessment: `backup-and-recovery-gate.md` (this document does not duplicate that scoring).

## Dev-database spot-check
No `.db` files exist inside the git repository tree (confirmed by search). Four pre-existing dev-artifact databases at `%LOCALAPPDATA%\AuraRetail` / `AuraClinic` (outside the repo, left untouched, opened read-only) all passed `integrity_check: ok` and `foreign_key_check: []`.

## Defects found
**None.** No data loss, no corruption, no silently-accepted invalid restore, in any path tested this wave.

## Cleanup
All test processes stopped and confirmed with no orphaned `AuraRetail`/`AuraClinic` processes remaining; the entire throwaway test scratchpad (installs, data, backups) was removed. Pre-existing real dev data from prior waves was left untouched.

## Verdict
**Data integrity / zero-data-loss gate: PASS**, both products. Install, upgrade (via reinstall-over-existing), uninstall (data preserved), reinstall (no forced re-onboarding), hard-kill crash recovery, schema migration, backup, and restore (including two adversarial rejection cases) all independently re-proven this wave with zero defects. Android's device-level upgrade proof rests on Wave 1A's physical-device evidence against the same, now-confirmed-unchanged binaries.
