# Aura Retail — Android Build Report (Phase 4)

Environment: Windows 11, this session. `java -version` → OpenJDK 17.0.19 (Microsoft
build). Android SDK at `%LOCALAPPDATA%\Android\Sdk` (platform 34, build-tools 34.0.0).
No emulator/device connected — see `device-testing-guide.md`.

## Commands executed, in order, from `android/aura-retail/`

```
./gradlew --version
./gradlew clean assembleDebug --stacktrace
./gradlew testDebugUnitTest lintDebug --stacktrace
./gradlew assembleStaging assembleRelease --stacktrace
./gradlew assembleStaging --stacktrace     # re-run after fixing the staging signingConfig (see below)
```

## Results

| Command | Result | Time |
|---|---|---|
| `clean assembleDebug` | BUILD SUCCESSFUL, 51 actionable tasks (50 executed, 1 up-to-date) | 7m 33s |
| `testDebugUnitTest lintDebug` | BUILD SUCCESSFUL | 4m 33s |
| `assembleStaging assembleRelease` | BUILD SUCCESSFUL, 117 actionable tasks (101 executed, 13 from cache, 3 up-to-date) | 8m 17s |
| `assembleStaging` (re-run, signing fix) | BUILD SUCCESSFUL, 60 actionable tasks (12 executed, 48 up-to-date) | 34s |

`testDebugUnitTest`: **0 tests** — the source Android project has no test source
sets (`app/src/test`, `app/src/androidTest` do not exist in
`AuraEnterprise/android/app/src`); Gradle correctly reports `NO-SOURCE`, not a
failure.

`lintDebug`: **0 errors, 27 warnings** — full report at
`app/build/reports/lint-results-debug.{html,txt,xml}`. All warnings are pre-existing/
cosmetic: outdated dependency versions available upstream (kept pinned per Phase 4L —
"preserve existing working versions"), a redundant manifest `android:label`, missing
adaptive-icon monochrome variant, and one `UnusedAttribute` (`enableOnBackInvokedCallback`
targets API 33+, minSdk is 26 — harmless, ignored on older devices).

## A real bug this phase's build caught and fixed

The `staging` build type was declared with `initWith debug`, which — as the real
Gradle build first revealed — also copies `debug`'s `signingConfig` (the
auto-generated Android debug key), not just `debuggable`/`applicationIdSuffix`. The
first `assembleStaging` run produced an APK signed with the debug key
(`apksigner verify` showed `CN=Android Debug`), contradicting the intended design
("staging = release-shaped, production-key-or-unsigned"). Fixed in
`app/build.gradle`'s `staging {}` block by explicitly resetting `signingConfig null`
before conditionally applying `signingConfigs.release`. Re-verified after the fix:
the output file is now named `app-staging-unsigned.apk` (AGP renames unsigned
outputs) and `apksigner verify` reports `ERROR: Missing META-INF/MANIFEST.MF` —
genuinely unsigned, matching `signing-and-release-guide.md`.

## Artifacts produced

| File | Size (bytes) | SHA-256 |
|---|---|---|
| `app/build/outputs/apk/debug/app-debug.apk` | 70,329,513 | `bc8539b41bad314bebd1d1a240f8517082d25cf3d223af024f417856f39325fe` |
| `app/build/outputs/apk/staging/app-staging-unsigned.apk` | 64,367,935 | `77cbb6e47c57a011d7ad5dc5054a6f0eaa4808e719ca05b8d1ba790016d5dd8e` |
| `app/build/outputs/apk/release/app-release-unsigned.apk` | 64,336,667 | `1387b000b63c432385fb9e6341aee34751acea4a80c916a332243fb7057ce03a` |

Package identity (from `aapt2 dump badging` on the debug APK):
`com.actionaura.retail.debug`, `versionCode=1`, `versionName=1.0.0`,
`minSdk=26`, `targetSdk=34`, `compileSdk=34`. Permissions: `INTERNET`, `CAMERA`.

**None of these APKs are signed with a production key.** `app-debug.apk` carries
Android's standard auto-generated debug signature (fine for local install/testing
only). `app-staging-unsigned.apk` and `app-release-unsigned.apk` are genuinely
unsigned — do not distribute them as-is. See `signing-and-release-guide.md`.

Per policy, none of these APK files are committed to git — `.gitignore` excludes
`android/*/app/build/outputs/`.

## What this build proves, and what it doesn't

Proves: the migrated source compiles under Kotlin 2.0.21/AGP 8.5.2, Chaquopy 16.0.0
successfully resolves and installs its trimmed pip dependency set for both
`arm64-v8a` and `x86_64`, all Compose/Retrofit/CameraX/ML Kit dependencies resolve
without conflict, resources merge and package cleanly, and lint finds no errors.

Does not prove: that the app actually runs correctly on a device — no emulator or
physical device was available this phase. See `android-retail-parity-matrix.md` for
the items marked `REQUIRES PHYSICAL DEVICE VALIDATION`.
