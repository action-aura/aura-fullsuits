# Aura Retail Android — Build Guide

Status: **PROVEN** (every command below was actually run in this
environment this phase; see `retail-build-report.md` for full output).

## Prerequisites

- JDK 17 (this environment: Microsoft build 17.0.19)
- Android SDK with `compileSdk`/`targetSdk` 34 platform + build-tools
  installed (`local.properties` must point `sdk.dir` at it — see
  `android-environment-configuration.md`)
- No Android emulator or physical device required to build (only to run)

## One-time setup

```
cd aura-fullsuits/android/aura-retail
# local.properties is git-ignored; create it if missing:
echo "sdk.dir=C:\\Users\\<you>\\AppData\\Local\\Android\\Sdk" > local.properties
```

## Build commands (exact, real)

```
cd aura-fullsuits/android/aura-retail

# Wrapper validation + clean
./gradlew.bat clean

# Unit/contract tests (29 tests, JVM only, no device)
./gradlew.bat :app:testDebugUnitTest

# Lint
./gradlew.bat lintDebug

# Debug APK
./gradlew.bat assembleDebug
# -> app/build/outputs/apk/debug/app-debug.apk

# Unsigned release APK
./gradlew.bat assembleRelease
# -> app/build/outputs/apk/release/app-release-unsigned.apk

# Release AAB (also unsigned -- see android-signing-guide.md)
./gradlew.bat bundleRelease
# -> app/build/outputs/bundle/release/app-release.aab
```

## What the build actually does

`stageAuraAssets`/`stageAuraPython` (custom Gradle tasks, run automatically
before compilation) copy `products/retail/backend/`,
`commercial_runtime/`, and `products/retail/frontend/` from
`aura-fullsuits` (computed as `rootProject.projectDir.parentFile.parentFile`)
into the Chaquopy Python source tree / `assets/bundle/`. This means any
backend fix (e.g. Wave 0, Phase 3.7) is picked up automatically on the next
Android build with zero Android-side code change required for that file.

## Installing to a device (not exercised this phase — no device available)

```
adb install app/build/outputs/apk/debug/app-debug.apk
```
