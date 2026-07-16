# Aura Android Apps

Two independent Android products, each its own Gradle project:

- `aura-retail/` — Aura POS (`com.actionaura.retail`)
- `aura-clinic/` — Aura Clinic (`com.actionaura.clinic`)

They are not flavors of one app and do not depend on each other. Each stages its
own Python backend from `aura-fullsuits/products/{retail,clinic}/backend/` and
`aura-fullsuits/commercial_runtime/` at build time — neither reads from the other
product's backend, and neither reads from the original `AuraEnterprise` repository.
See `../docs/android/android-migration-plan.md` for how this was built and why.

## Prerequisites

- JDK 17 (this repo was built/verified with OpenJDK 17.0.19).
- Android SDK: platform 34, build-tools 34.0.0 (matches `compileSdk`/`targetSdk`).
- Set `sdk.dir` in each product's `local.properties` (copy from
  `local.properties.template` — `local.properties` itself is git-ignored,
  machine-specific).
- Network access on first build: Chaquopy downloads the Python 3.12 runtime + pip
  wheels for `arm64-v8a`/`x86_64` from `chaquo.com`/PyPI.

## Build

```
cd android/aura-retail   # or aura-clinic
./gradlew clean assembleDebug
./gradlew testDebugUnitTest lintDebug
./gradlew assembleStaging assembleRelease
```

Real results from this session (exact commands, timings, artifact checksums, and a
signing bug this phase's build itself caught and fixed) are in
`../docs/android/android-retail-build-report.md` and
`../docs/android/android-clinic-build-report.md`.

## Signing

No production signing key exists yet — `debug`/`staging`/`release` builds produced
in this repo are unsigned or debug-signed only. See
`../docs/android/signing-and-release-guide.md` before shipping anything.

## Further reading

- `../docs/android/android-source-inventory.md` — what was migrated vs. rewritten, and why
- `../docs/android/android-dependency-map.md` — exact versions, trimmed pip deps per product
- `../docs/android/android-risk-register.md` — known risks and how they were mitigated
- `../docs/android/environment-configuration.md` — debug/staging/release differences
- `../docs/android/device-testing-guide.md` — smoke-test steps; what still needs a real device
- `../docs/android/release-checklist.md` — pre-distribution checklist
- `../docs/android/android-retail-parity-matrix.md` / `android-clinic-parity-matrix.md` — per-feature status
- `../docs/privacy/clinic-android-data-boundary.md` — Clinic's data-handling review
