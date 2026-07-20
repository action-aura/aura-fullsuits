# Aura Retail -- Commercial Compatibility Matrix (Wave 1B, Part V)

Status vocabulary: PASS / PASS WITH LIMITATION / PHYSICALLY VERIFIED / SIMULATED / BUILD VERIFIED / NOT TESTED / NOT IMPLEMENTED / BLOCKED / DEFERRED TO WAVE 1C / DEFERRED TO OWNER PLATFORM / DEFERRED TO LATER HARDWARE ADAPTER.

| Area | Windows | Android | Notes |
|---|---|---|---|
| Install | PASS | PASS | Windows: Inno Setup, `lowest` privileges, non-admin. Android: signed APK/AAB, standard install. |
| Upgrade (in place, data preserved) | PASS | PASS | Windows: same `AppId`, reinstall over existing install preserves `%LOCALAPPDATA%\AuraRetail`. Android: signed-upgrade proven on physical device (versionCode 1->2, real data survived). |
| Uninstall (data preserved by default) | PASS | N/A | Windows: installer only removes what it installed; data deletion is opt-in with two sequential confirmations, and is skipped entirely on a silent uninstall (`if UninstallSilent() then Exit;` -- a real bug fixed this wave). Android has no "uninstall while preserving data" concept (OS-level app uninstall always removes app-private storage; documented, not a defect). |
| Reinstall after uninstall (no re-onboarding) | PASS | N/A | Windows: verified -- reinstalling after a data-preserving uninstall restores straight to login, no onboarding repeat. |
| Onboarding | PASS | PASS | Both platforms: tenant/admin creation flow works end to end. |
| Financial authority (server-computed totals) | PASS | PASS | MOB-001 (client-submitted `amount_paid` bug) fixed in an earlier wave; this wave additionally fixed the receipt display mixing `payload`/cart data with the authoritative server response (Part P). |
| Returns | PASS | NOT TESTED | Windows: `retail_returns_wave0_test.py`, 10/10 pass, plus earlier real-device verification. Android: no dedicated returns UI exists to test this wave. |
| Stock / inventory movements | PASS | NOT TESTED | Windows: covered by `retail_import_export_test.py`, `retail_pricing_test.py`; real inventory_movements rows confirmed present in live DB during this wave's restart-persistence check. |
| Backup / restore | PASS | PASS | `retail_backup_restore_test.py` 12/12; validates `product_code`/`schema_version` before restoring, rejects incompatible/corrupted backups. |
| English / Arabic localization | PASS | PASS | `retail_localization_test.py` 18/18; Android language switcher added this wave (was previously missing -- real bug reported by user and fixed). |
| USB/Bluetooth HID barcode scanner | PHYSICALLY VERIFIED | PHYSICALLY VERIFIED | Windows: pre-existing, proven engine (discovered, not built, this wave). Android: built and device-tested this wave (`HidScanDetector.kt`, 7/7 unit tests, real device scan-to-cart confirmed). |
| Camera-based barcode scanning | NOT IMPLEMENTED | NOT IMPLEMENTED | Out of scope this wave; no existing implementation found. |
| Serial / BLE / vendor-SDK scanners | DEFERRED TO LATER HARDWARE ADAPTER | DEFERRED TO LATER HARDWARE ADAPTER | Contract defined (`ScannerAdapter.kt`), disabled by default, no hardware to validate against. |
| Receipt printing | BUILD VERIFIED | PASS WITH LIMITATION | Windows: OS print spooler via hidden iframe, syntax/serve-correctness confirmed, not click-through tested against a real print dialog. Android: no direct printer protocol -- real, working share-sheet fallback (device-verified), documented honestly as a fallback, not native printing. |
| Direct ESC/POS thermal printing | NOT IMPLEMENTED | NOT IMPLEMENTED | No hardware to validate against; OS-print-spooler / share-sheet paths cover the practical use case instead. |
| Offline behavior | NOT TESTED | NOT TESTED | Both products are fully local/offline-first by architecture (no cloud dependency exists to go offline from), but no explicit network-loss simulation was run this wave. |
| Restart / crash recovery | PASS | PASS | This wave: real unclean-shutdown test (hard `taskkill /F`, no graceful exit) on Windows, `PRAGMA quick_check` = `ok` before and after, real test data intact. Android: OS-level process lifecycle already covered by earlier wave's resilience testing (force-stop, background, screen lock). |
| Schema migration safety | PASS | N/A (Android has no separate schema-migration path this wave) | Pre-migration backup + version-marker + rollback-on-failure (`migration_safety.py`), 5/5 tests, real-data end-to-end confirmed. |
| Code signing | UNSIGNED | SIGNED AND VERIFIED | Windows: no certificate purchased this wave (explicit choice); infrastructure ready. Android: real production keystore, verified. |
| Customer data preservation policy | PASS (documented) | PASS (documented) | `customer-data-preservation-policy.md` -- fully local, zero cloud upload, explicit ownership statement. |
| Multi-instance launch (Retail + Clinic together) | PASS (after fix) | N/A | A real port-race defect was found and fixed this wave -- see `wave1b-defect-registry.md` REL-XXX. |

No unexplained FAIL remains in this matrix.
