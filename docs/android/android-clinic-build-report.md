# Aura Clinic — Android Build Report (Phase 4)

Environment: Windows 11, this session. `java -version` → OpenJDK 17.0.19 (Microsoft
build). Android SDK at `%LOCALAPPDATA%\Android\Sdk` (platform 34, build-tools 34.0.0).
No emulator/device connected — see `device-testing-guide.md`.

## Commands executed, in order, from `android/aura-clinic/`

```
./gradlew clean assembleDebug --stacktrace
./gradlew testDebugUnitTest lintDebug --stacktrace
./gradlew assembleStaging assembleRelease --stacktrace
./gradlew assembleStaging --stacktrace     # re-run after fixing the staging signingConfig (see below)
```

## Results

| Command | Result | Time |
|---|---|---|
| `clean assembleDebug` | BUILD SUCCESSFUL, 51 actionable tasks (44 executed, 6 from cache, 1 up-to-date) | 5m 45s |
| `testDebugUnitTest lintDebug` | BUILD SUCCESSFUL | 50s |
| `assembleStaging assembleRelease` | BUILD SUCCESSFUL, 117 actionable tasks (99 executed, 15 from cache, 3 up-to-date) | 6m 6s |
| `assembleStaging` (re-run, signing fix) | BUILD SUCCESSFUL, 60 actionable tasks (12 executed, 48 up-to-date) | 1m 24s |

`testDebugUnitTest`: **0 tests** — the source Android project has no test source
sets; Gradle correctly reports `NO-SOURCE`, not a failure.

`lintDebug`: **0 errors, 23 warnings** — full report at
`app/build/reports/lint-results-debug.{html,txt,xml}`. Same categories as retail
(outdated dependency versions kept pinned per Phase 4L, redundant manifest label,
missing adaptive-icon monochrome variant); retail's extra `CAMERA`-permission-related
lint findings are absent here because clinic's manifest never declares that
permission (Phase 4F).

## The same real bug this phase's build caught and fixed (shared with retail)

`staging`'s `initWith debug` also copied `debug`'s signingConfig (the Android debug
key) — the first `assembleStaging` produced a debug-key-signed APK, not the intended
"release-shaped, production-key-or-unsigned" staging channel. Fixed identically to
retail: `app/build.gradle`'s `staging {}` now explicitly resets `signingConfig null`
before conditionally applying `signingConfigs.release`. Re-verified: output renamed
to `app-staging-unsigned.apk`, `apksigner verify` confirms `ERROR: Missing
META-INF/MANIFEST.MF` — genuinely unsigned.

## Artifacts produced

| File | Size (bytes) | SHA-256 |
|---|---|---|
| `app/build/outputs/apk/debug/app-debug.apk` | 55,881,867 | `bd8f9496b2bfa3ea08fb7ab92c87b7cca7b24f3894a56831d1372620b4f3d067` |
| `app/build/outputs/apk/staging/app-staging-unsigned.apk` | 50,287,239 | `d9d2c1f6b29b8dde5fd34f57dcb1741a92657e0f00d6b26ec95558cac3a9b454` |
| `app/build/outputs/apk/release/app-release-unsigned.apk` | 50,255,971 | `f2065cc9605c7c28e6de3113098cb52a6d80904cf984a4cdb7551abb0baa85c3` |

Package identity (from `aapt2 dump badging` on the debug APK):
`com.actionaura.clinic.debug`, `versionCode=1`, `versionName=1.0.0`,
`minSdk=26`, `targetSdk=34`, `compileSdk=34`. Permissions: `INTERNET` only
(no `CAMERA` — confirmed absent, matching the source-inventory/privacy-doc finding
that clinic has no camera feature).

**None of these APKs are signed with a production key.** Same caveats as retail —
see `signing-and-release-guide.md`.

Per policy, none of these APK files are committed to git — `.gitignore` excludes
`android/*/app/build/outputs/`.

## What this build proves, and what it doesn't

Proves: the migrated source compiles under Kotlin 2.0.21/AGP 8.5.2, Chaquopy 16.0.0
successfully resolves and installs its trimmed pip dependency set (Flask/Werkzeug/
flask-cors/waitress only — no openpyxl/requests, correctly excluded) for both
`arm64-v8a` and `x86_64`, and lint finds no errors.

Does not prove: that the app actually runs correctly on a device — no emulator or
physical device was available this phase. See `android-clinic-parity-matrix.md` for
the items marked `REQUIRES PHYSICAL DEVICE VALIDATION`.
