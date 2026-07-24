# Phase 7V — rc.2 Release-Candidate Manifest

Commit as of this manifest: `869887a5bf20b7abdb13887cf1c13a4c69d06e63` (docs commit will follow;
final commit hash recorded in `PHASE7V-RELEASE-VALIDATION-HANDOVER.md`).
Build timestamp: 2026-07-24 (this session).

**This manifest does not call rc.2 production-ready.** See
`phase7-final-release-validation-decision.md` for the actual gate verdicts.

## Windows

| | Clinic | Retail |
|---|---|---|
| Version | 1.0.0-rc.2 | 1.0.0-rc.2 |
| Installer | `dist/installers/AuraClinic-Setup-1.0.0-rc.2.exe` | `dist/installers/AuraRetail-Setup-1.0.0-rc.2.exe` |
| SHA-256 | `e6ade67954d0cf3a7d2f1b063749c20b0c9fa605d987e89b8d675a0ac9d2d878` | `e7e5a033d60dddea4a60727ec9a686ddf5dff04edb5a3159b910c562dcc97c24` |
| Size | 14,655,130 bytes | 15,430,226 bytes |
| Signing status | **UNSIGNED** (no Authenticode certificate available in this environment — SmartScreen "unrecognized publisher" warning expected, unchanged from Wave 1B) | **UNSIGNED** (same) |
| Schema version | 1 | 1 |
| Licensing contract version | v1 (Phase 6/7 protocol) | v1 |
| Financial contract version | N/A | `retail-pricing-v2-wave0` |
| Trusted Owner key-set | Not bundled (no production Owner instance exists in this environment to generate against — required before a real release cut, see `release-artifact-security-inspection.md`) | Not bundled (same) |
| Test-report references | `windows-rc2-build-report.md`, `windows-rc1-to-rc2-installer-validation.md`, `windows-commercial-build-security-review.md` | same |
| Known limitations | Unsigned installer; RESTRICTED state not live-exercised (policy-dependent); Retail full installer-level upgrade-with-data cycle not performed this session | see left |

## Android

| | Clinic | Retail |
|---|---|---|
| Version | 1.0.0-rc.2 | 1.0.0-rc.2 |
| versionCode | 3 | 3 |
| APK | `android/aura-clinic/app/build/outputs/apk/release/app-release.apk` | `android/aura-retail/app/build/outputs/apk/release/app-release.apk` |
| APK SHA-256 | `452a4c87111a193b8581f2ca14b71607d322ebc24b5977f88a0aecf93887a36d` | `33792b86cb48d23052428d59cbe6582ec518de1b07ac4922235adaefe7a455df` |
| APK size | 51,655,957 bytes | 65,266,605 bytes |
| AAB | `android/aura-clinic/app/build/outputs/bundle/release/app-release.aab` | `android/aura-retail/app/build/outputs/bundle/release/app-release.aab` |
| AAB SHA-256 | `109a67ec04b9f97c7cbe855d9f5181221dde3d3ab57a13e94d8f7e7b2f990cdf` | `087136d91b1068545d03cc4dc5a1d3a0897f606bd819ca0a1924fd956a2a20dc` |
| AAB size | 32,437,894 bytes | 39,308,430 bytes |
| Signing status | **SIGNED**, production keystore, cert `35:50:80:...:0B:BB:F2` (matches rc.1) | **SIGNED**, production keystore, cert `CA:E6:B1:...:B7:D3:2D` (matches rc.1) |
| Schema version | 1 | 1 |
| Licensing contract version | v1 (shared core with Windows) | v1 |
| Trusted Owner key-set | Not bundled (same reasoning as Windows) | Not bundled |
| Test-report references | `android-rc2-signed-build-report.md`, `android-certificate-continuity.md` | same |
| Known limitations | Physical-device validation NOT VERIFIED this session (no device connected); test source files bundled in APK (hygiene, not security) | same, plus barcode/receipt-share regressions NOT VERIFIED (physical) |

## Not overwritten

rc.1 artifacts (`AuraClinic-Setup-1.0.0-rc.1.exe`, `AuraRetail-Setup-1.0.0-rc.1.exe`) remain
untouched in `dist/installers/`. rc.1 Android signed artifacts are not present as files in this
checkout (only their checksums survive in `docs/release/wave1b/release-candidate-manifest.md`) and
were therefore not at risk of being overwritten.

## Release-gate status

See `phase7-final-release-validation-decision.md`. Summary: **CONDITIONAL PASS** pending physical
Android device validation.
