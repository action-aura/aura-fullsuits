# Aura Clinic Android — Build Guide

Status: **PROVEN** (every command below was actually run in this
environment this phase; see `clinic-build-report.md` for full output).
Identical structure to `retail-build-guide.md` -- only the project
directory and package name differ.

## Prerequisites

Same as Retail (JDK 17, Android SDK compileSdk/targetSdk 34).

## Build commands (exact, real)

```
cd aura-fullsuits/android/aura-clinic

./gradlew.bat clean
./gradlew.bat :app:testDebugUnitTest   # 17 tests, JVM only
./gradlew.bat lintDebug
./gradlew.bat assembleDebug            # -> app/build/outputs/apk/debug/app-debug.apk
./gradlew.bat assembleRelease          # -> app/build/outputs/apk/release/app-release-unsigned.apk
./gradlew.bat bundleRelease            # -> app/build/outputs/bundle/release/app-release.aab (unsigned)
```

## What the build actually does

Same staging mechanism as Retail, staging from
`products/clinic/backend/`, `commercial_runtime/`, and
`products/clinic/frontend/`. No CameraX/ML Kit dependencies (Clinic has no
barcode feature) — this is a smaller dependency graph than Retail's.

## Installing to a device (not exercised this phase — no device available)

```
adb install app/build/outputs/apk/debug/app-debug.apk
```
