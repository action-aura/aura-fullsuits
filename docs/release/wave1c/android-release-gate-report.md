# Wave 1C -- Android Release Gate Report (Part G)

## Method
Full independent re-run against the tagged Wave 1B source (`commercial-packaging-wave1b-complete`, `157521c`): unit tests, lint (debug + release), `assembleRelease`/`bundleRelease`, signature verification, checksum comparison, manifest/gradle safety checks. Many Gradle tasks correctly reported UP-TO-DATE since no source changed since the tag -- expected, not a shortcut.

## Retail Android
| Check | Result |
|---|---|
| Unit tests | **36/36 passing**, 0 skipped (`BarcodeDebounceTest` 5, `HidScanDetectorTest` 7, `ProductLookupTest` 5, `SaleContractTest` 12, `ReadinessContractTest` 2, `NumTest` 5) |
| lintDebug | 0 errors, 27 warnings -- BUILD SUCCESSFUL |
| lintRelease | 0 errors, 27 warnings -- BUILD SUCCESSFUL. `@Suppress("RestrictedApi")` on `MainActivity.kt:46`'s `dispatchKeyEvent` confirmed still present; no `RestrictedApi` finding in the release report |
| assembleRelease / bundleRelease | BUILD SUCCESSFUL, signed `app-release.apk` + `app-release.aab` produced |
| Signature | `apksigner verify --print-certs` exit 0. `CN=Action Aura, OU=Aura Retail, O=Action Aura, L=Amman, ST=Amman, C=JO`, SHA-256 cert digest `cae6b18450c14a71eba47545e5b3ba52089eef0397d5881f4c1dc7d5a4b7d32d` -- matches `android-production-signing-policy.md`'s recorded fingerprint |
| Checksum vs. Wave 1B baseline | APK `fd307cb4...` **MATCH** (bit-identical rebuild). AAB `1b00c667...` **MATCH** |
| Manifest | Only `.MainActivity` exported; `android:allowBackup="false"`; no `android:debuggable` anywhere in the manifest (build.gradle's `debug{}` block only, `release{}` has no `debuggable` key, defaults false); declares `INTERNET` + optional runtime `CAMERA` |
| Release build hygiene | `minifyEnabled false` (Proguard files referenced but inactive) |

## Clinic Android
| Check | Result |
|---|---|
| Unit tests | **49/49 passing**, 0 skipped (`PrivacyLoggingGuardTest` 3, `ApiErrorsTest` 21, `PaymentContractTest` 12, `ReadinessContractTest` 2, `ClinicSessionTest` 7, `HardcodedStringAuditTest` 1, `StringsCoverageTest` 3) |
| lintDebug | 0 errors, 23 warnings -- BUILD SUCCESSFUL |
| lintRelease | 0 errors, 23 warnings -- BUILD SUCCESSFUL. No `dispatchKeyEvent` override present (expected -- HID scanning is Retail-only) |
| assembleRelease / bundleRelease | BUILD SUCCESSFUL, signed `app-release.apk` + `app-release.aab` produced |
| Signature | exit 0. `CN=Action Aura, OU=Aura Clinic, O=Action Aura, L=Amman, ST=Amman, C=JO`, SHA-256 cert digest `35508048cee7776ca94a45a9f04c6a870edf98729429482a8ad44e89dd0bbbf2` -- matches recorded fingerprint |
| Checksum vs. Wave 1B baseline | APK `49a4c337...` **MATCH**. AAB `7a15e009...` **MATCH** |
| Manifest | Only `.MainActivity` exported; `allowBackup="false"`; non-debuggable release; declares `INTERNET` only (explicit in-manifest comment: no camera/barcode feature -- correct, Clinic has none) |

## Interpretation of bit-identical rebuilds
Both products' freshly rebuilt release APK/AAB are byte-identical to the Wave 1B-tagged artifacts already on disk. This is stronger evidence than a checksum-only identity check (`release-candidate-identity-verification.md`) -- it proves the *source at the tag* deterministically reproduces the *exact* shipped binary, not just that the shipped binary hasn't been swapped.

## Additional spot-checks required by the spec, covered elsewhere
Retail-specific (authoritative sale/return/stock, CameraX, ML Kit, HID input, receipt sharing) and Clinic-specific (patient workflow, appointment, invoice, payment, payment errors, screen protection, role-aware navigation) items are covered by: `financial-release-gate-report.md` (sale/return/payment authority), `clinic-privacy-release-gate.md` (screen protection, role navigation), `hardware-commercial-claim-review.md` (CameraX/ML Kit/HID/receipt sharing), and the Wave 1A device-validation evidence (physical onboarding/login/core-workflow proof, one device model).

## Verdict
**Android release gate: PASS**, both products. Zero test failures, zero lint errors, both release builds signed and verified with certificates matching the recorded production-signing policy, and both artifacts bit-identical to the already-shipped Wave 1B binaries. No regression, no new defect, since the tag.
