# Release-Candidate Artifact Manifest (Wave 1B, Part U)

Version: `1.0.0-rc.1` (both products, all platforms). Built after the port-race fix in `build-and-test-gates-report.md` -- checksums below reflect the final, fixed binaries, not the earlier pre-fix builds.

## Windows

| Artifact | Path | SHA-256 | Status |
|---|---|---|---|
| Aura Retail installer | `dist/installers/AuraRetail-Setup-1.0.0-rc.1.exe` | `4318def84d8beb14ccdd74ff0c1a949304fe6f4a1ac6fc85ffdacb35cd70cb2e` | **UNSIGNED** (no code-signing certificate -- see `windows-code-signing-guide.md`), **INSTALLER VERIFIED** (clean install / upgrade / uninstall / reinstall lifecycle tested, data-preservation confirmed, see `retail-windows-installer-report.md`) |
| Aura Clinic installer | `dist/installers/AuraClinic-Setup-1.0.0-rc.1.exe` | `5fd10135825840580dd7169c244221471bc12363c62d101937a894cd036cdcba` | **UNSIGNED**, **INSTALLER VERIFIED** (same lifecycle testing, see `clinic-windows-installer-report.md`) |

Both installers will trigger a SmartScreen "unrecognized publisher" warning on first run until a real code-signing certificate is purchased and applied (see `windows-code-signing-guide.md` / `products/sign_windows_release.ps1` -- infrastructure ready, no certificate obtained this wave, by explicit choice).

## Android

| Artifact | Path | SHA-256 | Status |
|---|---|---|---|
| Aura Retail APK | `android/aura-retail/app/build/outputs/apk/release/app-release.apk` | `fd307cb417a35bbe96358579e1d329c059b3f7a395da42fb5cf4097bb6545471` | **SIGNED AND VERIFIED** (real production keystore, `apksigner verify` confirms cert fingerprint `CA:E6:B1:...:B7:D3:2D`), **DEVICE VERIFIED** (signed-upgrade + real sale + barcode scan + receipt share, physical Infinix X6528) |
| Aura Retail AAB | `android/aura-retail/app/build/outputs/bundle/release/app-release.aab` | `1b00c667f66d326d0523a10dca5159f3a376aae4df8ddd0961369c6aa21d75f4` | **SIGNED AND VERIFIED** (`jarsigner -verify`: jar verified) |
| Aura Clinic APK | `android/aura-clinic/app/build/outputs/apk/release/app-release.apk` | `49a4c337217beadb844639aa50168b30abf0ca7f739a560264a1848ac2221c76` | **SIGNED AND VERIFIED** (cert fingerprint `35:50:80:...:0B:BB:F2`), **DEVICE VERIFIED** (signed-upgrade + real patient/appointment/invoice flow, physical device) |
| Aura Clinic AAB | `android/aura-clinic/app/build/outputs/bundle/release/app-release.aab` | `7a15e00984c1bfe422799c853952cb7cc0d02d9a243c05c4551df06bcaf88723` | **SIGNED AND VERIFIED** |

Neither AAB has been uploaded to Google Play (no Play Console account work was in scope for Wave 1B) -- both are release-ready bundles, not published listings.

## Hardware-dependent features (status carried from Parts L-P, unchanged by this wave's fixes)

| Feature | Status |
|---|---|
| USB/Bluetooth HID keyboard-wedge barcode scanner (Windows) | **PHYSICALLY VERIFIED** -- pre-existing, proven engine, unchanged this wave |
| USB/Bluetooth HID keyboard-wedge barcode scanner (Android) | **PHYSICALLY VERIFIED** -- built and device-tested this wave (`HidScanDetector.kt`, `MainActivity.dispatchKeyEvent`) |
| Camera-based barcode scanning | **NOT IN SCOPE** this wave (no existing implementation found; HID keyboard-wedge is the supported input method) |
| Serial / BLE / vendor-SDK scanner adapters | **NOT IMPLEMENTED** -- contracts only (`ScannerAdapter.kt`), disabled by default, no hardware to validate against |
| Receipt printing -- Windows OS print spooler | **BUILD VERIFIED** (syntax-checked, served-correctly confirmed) -- **NOT click-through tested** against a real print dialog (no interactive session available); honestly disclosed, not claimed as done |
| Receipt printing -- Android share fallback | **DEVICE VERIFIED** -- real sale, real share-sheet trigger, confirmed working on physical device |
| Direct Bluetooth/USB ESC/POS thermal printing | **NOT IMPLEMENTED** -- no hardware to validate against |

## What changed in this wave's very last pass
A real concurrency defect (cross-product port race on simultaneous cold launch) was found during Part T's restart-persistence testing, fixed in both launchers, and both Windows exes + both installers were rebuilt from the fixed source -- the checksums above are the fixed, final artifacts. See `build-and-test-gates-report.md` for the full defect narrative and `wave1b-defect-registry.md` for its registry entry.

## Explicitly not claimed
Consistent with this wave's instruction not to overstate readiness: nothing in this manifest is described as "production-ready," "enterprise-grade," "commercially ready," "safe for paid customers," or "universally compatible with all scanners/printers." Every status label above is exactly what was verified, no more.
