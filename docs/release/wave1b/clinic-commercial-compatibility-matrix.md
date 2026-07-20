# Aura Clinic -- Commercial Compatibility Matrix (Wave 1B, Part V)

Status vocabulary: PASS / PASS WITH LIMITATION / PHYSICALLY VERIFIED / SIMULATED / BUILD VERIFIED / NOT TESTED / NOT IMPLEMENTED / BLOCKED / DEFERRED TO WAVE 1C / DEFERRED TO OWNER PLATFORM / DEFERRED TO LATER HARDWARE ADAPTER.

| Area | Windows | Android | Notes |
|---|---|---|---|
| Install | PASS | PASS | Windows: Inno Setup, `lowest` privileges. Android: signed APK/AAB. |
| Upgrade (in place, data preserved) | PASS | PASS | Android signed-upgrade proven on physical device with real patient data surviving versionCode 1->2. |
| Uninstall (data preserved by default) | PASS | N/A | Same `/SUPPRESSMSGBOXES` silent-uninstall safety fix as Retail applies here (`if UninstallSilent() then Exit;`); two sequential confirmations worded specifically for patient/clinical data before any deletion. |
| Reinstall after uninstall (no re-onboarding) | PASS | N/A | Verified on Windows. |
| Onboarding | PASS | PASS | Both platforms confirmed. |
| Patients | PASS | PASS | Real patient records confirmed present and intact through this wave's unclean-shutdown/restart test; `clinic_independence_test.py` 5/5. |
| Appointments | PASS | PASS | Unlimited-date-range appointments and grouped-view work (fixed in the prior wave, unaffected by this one). |
| Invoices | PASS | PASS | `clinic_workflow_test.py` 29/29 includes invoice flows; invoice drill-down confirmed working from earlier wave. |
| Payments (financial authority) | PASS | PASS | `clinic_payment_wave0_test.py` 9/9; server-authoritative payment recording, no client-submitted-amount bug (MOB-003/MOB-004 class fixed earlier). |
| Backup / restore | PASS | PASS | `clinic_backup_restore_test.py` 4/4; same `product_code`/`schema_version` validation as Retail. |
| RBAC / role-aware access | PASS | PASS | `clinic_rbac_test.py` 24/24. |
| Privacy (PII handling, logging) | PASS | PASS | `clinic_privacy_test.py` 9/9; PII scan from an earlier wave cleared with zero leaks. |
| Screen protection (prevent screenshot/recording of patient data) | N/A | PASS | `FLAG_SECURE` confirmed working with barcode-adjacent testing in an earlier wave (Clinic has no barcode feature itself, but the FLAG_SECURE mechanism was verified functioning). |
| English / Arabic localization + RTL | PASS | PASS | `clinic_localization_test.py` 13/13. This wave: completed MOB-007 (99 new EN->AR strings across every screen), fixed two real gaps found only via real-device testing after automated coverage tests initially missed them (Patients/Billing nav titles, later `AppRoot.kt` drawer items/AI-sheet suggestions/Send icon) -- both confirmed fixed on physical device. |
| Offline behavior | NOT TESTED | NOT TESTED | Fully local/offline-first by architecture; no explicit network-loss simulation run this wave. |
| Restart / crash recovery | PASS | PASS | Real unclean-shutdown test this wave (hard `taskkill /F`), `PRAGMA quick_check` = `ok` before and after, real patient data intact. Android: covered by earlier wave's resilience testing (force-stop, background, screen lock, airplane mode). |
| Schema migration safety | PASS | N/A | Same `migration_safety.py` infrastructure as Retail; real pre-existing patient data confirmed to survive a real migration this wave. |
| Code signing | UNSIGNED | SIGNED AND VERIFIED | Windows: no certificate purchased this wave (explicit choice). Android: real production keystore (separate from Retail's), verified. |
| Customer data preservation policy | PASS (documented) | PASS (documented) | Same policy doc as Retail, patient-specific wording in uninstall confirmations. |
| Barcode scanner / receipt printer | NOT APPLICABLE | NOT APPLICABLE | Clinic has no point-of-sale or barcode concept -- never requested, never scoped, correctly absent (confirmed via manifest review: no CAMERA permission, no scanner code). |
| Multi-instance launch (Retail + Clinic together) | PASS (after fix) | N/A | Same port-race defect and fix as documented in the Retail matrix -- affects both products identically since both launchers shared the same buggy pattern. |

No unexplained FAIL remains in this matrix.
