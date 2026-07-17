# Phase 4A — Android Dependency Map

Status: **PROVEN** (read directly from the build files that a real Gradle
build just consumed successfully). Supplements the original
`docs/android/android-dependency-map.md` (unchanged, still accurate for
Chaquopy pip trimming rationale) with the exact versions confirmed in this
phase's real builds.

## Build toolchain (both products, identical)

| Component | Version | Source |
|---|---|---|
| Gradle | 8.9 | `gradle/wrapper/gradle-wrapper.properties` |
| Android Gradle Plugin (AGP) | 8.5.2 | root `build.gradle` buildscript classpath |
| Kotlin | 2.0.21 | root `build.gradle` buildscript classpath |
| Compose compiler plugin | 2.0.21 | root `build.gradle` buildscript classpath |
| Compose BOM | 2024.09.03 | `app/build.gradle` |
| Chaquopy | 16.0.0 | root `build.gradle` buildscript classpath |
| JDK (this build) | 17.0.19 (Microsoft build) | `java -version` in this environment |
| compileSdk / targetSdk | 34 | `app/build.gradle` |
| minSdk | 26 | `app/build.gradle` |

## Direct Android dependencies (both products unless noted)

- `androidx.core:core-ktx:1.13.1`, `androidx.appcompat:appcompat:1.7.0`
- Compose: `activity-compose:1.9.3`, `ui`, `ui-graphics`, `material3`,
  `material-icons-extended`, `navigation-compose:2.8.3`,
  `lifecycle-runtime-compose:2.8.6`, `lifecycle-viewmodel-compose:2.8.6`
- Networking: `retrofit:2.11.0`, `converter-gson:2.11.0`, `okhttp:4.12.0`,
  `kotlinx-coroutines-android:1.8.1`
- `coil-compose:2.7.0` (image loading)
- **Retail only**: CameraX `1.3.4` (`camera-core`/`camera-camera2`/
  `camera-lifecycle`/`camera-view`), `com.google.mlkit:barcode-scanning:17.3.0`
- **New this phase (both)**: `junit:junit:4.13.2` (test), `gson:2.11.0`
  (test, for contract-test JSON fixtures — the app already depends on Gson
  transitively via `converter-gson` for production use; declared directly
  under `testImplementation` since Gradle test source sets don't
  automatically inherit `implementation`-scoped transitive deps for direct
  use in test code). Retail additionally: `com.google.truth:truth:1.4.4`
  (declared but not required by the tests actually written — plain JUnit
  assertions were sufficient; harmless unused test dependency, not removed
  to avoid unnecessary churn).

## Chaquopy Python dependencies

Unchanged from the original migration's trim (see the original
`android-dependency-map.md` for the full exhaustive-grep methodology) —
this phase did not add or remove any Python package dependency, only fixed
two files' logic (`main.py`'s readiness URL) and two `print()` statements
in `clinic_api.py` (already-staged, not a new dependency).

## What changed in this phase

- `app/build.gradle` (both): added `testImplementation` entries listed
  above. No version bumps, no new production dependency, no Chaquopy
  config change.
- No new native library, no new permission, no new Gradle plugin.

## Independence

Every dependency above resolves from `google()`/`mavenCentral()`/
`https://chaquo.com/maven` (public repositories) or from
`aura-fullsuits/products`+`commercial_runtime` via the `stageAuraPython`
task — nothing resolves from `AuraEnterprise`. Confirmed by the real build
succeeding with `AuraEnterprise` never referenced in any task's file I/O
(see `android-source-inventory.md`'s independence section for the grep
evidence).
