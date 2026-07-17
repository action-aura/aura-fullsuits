# Aura Clinic Android — Build Report (Phase 4R, 2026-07-17)

Real builds, actually run in this environment. No claim below is estimated.

## Environment

Identical to `retail-build-report.md`'s environment section.

## Results

| Command | Result |
|---|---|
| `./gradlew.bat clean lintDebug assembleDebug assembleRelease` | **BUILD SUCCESSFUL** — 2m 15s |
| `./gradlew.bat :app:testDebugUnitTest` | **BUILD SUCCESSFUL** — 17/17 tests, 0 failures |
| `./gradlew.bat bundleRelease` | **BUILD SUCCESSFUL** — 8s |

Lint: **0 errors, 23 warnings** (same class of pre-existing deprecation
warnings as Retail — `Icons.Filled.X` AutoMirrored migrations, etc.).

## Artifacts

| File | Build type | Size | SHA-256 |
|---|---|---|---|
| `app/build/outputs/apk/debug/app-debug.apk` | debug | 55,898,247 bytes | `ecce2ec42f054deeb2b260124a0d150f413c05785b685dd2858b2d29d23072d7` |
| `app/build/outputs/apk/release/app-release-unsigned.apk` | release, **unsigned** | 50,272,355 bytes | `c497723e2ff8069c7c424b3029373e27b49bc9ac47ad977730e7c8bd0b3dc055` |
| `app/build/outputs/bundle/release/app-release.aab` | release, **unsigned** | 31,064,008 bytes | `96245e69c749f25c99f6d770d2e322fc05f1c4efdd818094478f4dc328a00707` |

`applicationId com.actionaura.clinic` (debug: `.debug` suffix),
`namespace com.actionaura.clinic`, `versionCode 1`, `versionName "1.0.0"`,
`minSdk 26`, `targetSdk 34`. Smaller artifact than Retail's equivalent
build type throughout (no CameraX/ML Kit dependency).

## Signing verification

Same method as Retail: `jarsigner -verify` against the AAB reports
"jar is unsigned." No `keystore.properties` on disk for this project
either. Both release artifacts are genuinely unsigned.

## Manifest inspection

Single exported component (`MainActivity`), `android:allowBackup="false"`,
loopback-only cleartext exception, **no CAMERA permission** (Clinic has no
barcode feature — confirmed absent, matching source), no FileProvider.

## Regression fixes verified in this artifact

- `main.py`'s readiness check targets `/api/health` (source-verified in
  the staged Python).
- `clinic_api.py`'s two PII-log-leak-capable `print()` statements are
  replaced with type-only logging in the staged Python this build
  actually packaged.

Neither is runtime-verified against a real launched app (no device/
emulator available) — see `clinic-device-test-report.md`.
