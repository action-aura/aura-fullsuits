# Phase 4S — Android Device/Emulator Testing Guide

Status: **PROVEN** for what was actually checked in this environment;
**NOT TESTED** for everything requiring a real device or emulator, stated
explicitly rather than assumed.

## Environment reality (checked this phase)

```
$ echo $ANDROID_HOME / $ANDROID_SDK_ROOT
(both empty)
$ ls "C:\Users\Dell\AppData\Local\Android\Sdk"
build-tools  cmdline-tools  licenses  platform-tools  platforms
$ ls "C:\Users\Dell\AppData\Local\Android\Sdk\emulator"
No such file or directory
$ adb devices
List of devices attached
(empty)
```

**No Android emulator package is installed, and no physical device is
connected.** `adb` itself works (platform-tools present, daemon starts
successfully), but there is nothing for it to talk to. This is a hard
environment constraint, not a choice — see `android-risk-register.md` R17
and the original migration's R9.

## What this means for validation status

Every workflow requiring the app to actually run (onboarding, login,
checkout, camera scanning, RTL rendering, restart persistence, etc.) is
marked `NOT TESTED` in `retail-device-test-report.md`/
`clinic-device-test-report.md` — never claimed as tested. What *was*
validated for those same workflows, honestly labeled:

- **BUILD ONLY**: the code compiles, packages, and lints clean as part of
  a real Gradle build (proves syntactic/type correctness, not runtime
  behavior).
- **SOURCE REVIEW ONLY**: the relevant source was read and traced by hand
  to confirm the logic is correct (e.g. "checkout displays `r.data.total`"
  is provably true by reading `RetailScreens.kt`, independent of whether
  the checkout screen was ever actually opened).
- **UNIT TEST VERIFIED**: a real JVM unit/contract test exercises the
  logic directly (e.g. `SaleContractTest.kt`'s response-deserialization
  tests) without needing Android runtime.

## How to actually run the device smoke tests (for whoever has a device)

1. Install an emulator image (`sdkmanager "system-images;android-34;google_apis;x86_64"`
   then `avdmanager create avd ...`) or connect a physical device with USB
   debugging enabled.
2. `adb devices` must show it before proceeding.
3. Build and install: `./gradlew.bat installDebug` (or
   `adb install app/build/outputs/apk/debug/app-debug.apk`).
4. Follow the exact numbered smoke-test steps in
   `retail-device-test-report.md` / `clinic-device-test-report.md` — those
   documents already enumerate every step from the Phase 4S addendum
   checklist verbatim, ready to be executed and have their `NOT TESTED`
   statuses updated to real results.
5. For camera/barcode specifically: an emulator's virtual camera can
   satisfy CameraX's API surface but cannot scan a real barcode — genuine
   barcode-scanning validation requires a physical device with a camera
   pointed at a real or displayed barcode. Do not report camera scanning
   as validated from emulator testing alone.

## Synthetic data only

Any real device/emulator test run must use synthetic data only (matching
every other phase in this project) — e.g. `"LongRun Admin"` /
`"Test Patient"` style names, never real customer or patient information —
and any resulting database file must be deleted afterward, not left on the
test device or committed anywhere.
