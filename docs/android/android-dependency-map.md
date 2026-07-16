# Android Dependency Map (Phase 4A)

## Gradle / build toolchain (unchanged from source — Phase 4L: no upgrades unless build-breaking)

| Component | Version | Source |
|---|---|---|
| Android Gradle Plugin | 8.5.2 | `android/build.gradle` classpath |
| Gradle | 8.9 | `gradle/wrapper/gradle-wrapper.properties` |
| Kotlin | 2.0.21 | root `build.gradle` classpath |
| Compose compiler plugin | 2.0.21 | root `build.gradle` classpath |
| Chaquopy | 16.0.0 | root `build.gradle` classpath |
| Compose BOM | 2024.09.03 | `app/build.gradle` |
| compileSdk / targetSdk | 34 | `app/build.gradle` |
| minSdk | 26 | `app/build.gradle` |
| Java/JVM target | 17 | `app/build.gradle` |

## Android library dependencies (both products)

androidx.core-ktx 1.13.1, androidx.appcompat 1.7.0, activity-compose 1.9.3,
navigation-compose 2.8.3, lifecycle-runtime/viewmodel-compose 2.8.6, material3 (BOM),
material-icons-extended (BOM), retrofit 2.11.0, converter-gson 2.11.0, okhttp 4.12.0,
kotlinx-coroutines-android 1.8.1, coil-compose 2.7.0, kotlin-stdlib-jdk7/jdk8 2.0.21
(version-aligned constraints).

## Android library dependencies (retail only)

CameraX 1.3.4 (camera-core, camera-camera2, camera-lifecycle, camera-view), ML Kit
barcode-scanning 17.3.0 (bundled/offline model). Not included in the clinic build —
clinic has no camera/barcode feature (verified by source grep, zero matches).

## Chaquopy pip dependencies

The source project's `app/build.gradle` pip-installed the monolith's full
`requirements.txt` (Flask, Werkzeug, flask-cors, flask-login, flask-socketio,
python-socketio, python-engineio, simple-websocket, waitress, openpyxl, requests) —
because Android imported `aura_core`, which imports every subsystem's dependencies
regardless of which flavor was building.

This phase re-scanned actual imports in `products/{retail,clinic}/backend/` +
`commercial_runtime/` (`grep` over every `import`/`from` statement) and trimmed to
only what's used:

| Package | Retail | Clinic | Why |
|---|:-:|:-:|---|
| Flask 3.0.3 | yes | yes | web framework |
| Werkzeug 3.0.3 | yes | yes | Flask's dependency, pinned explicitly (matches desktop) |
| flask-cors 4.0.1 | yes | yes | CORS restricted to loopback origins |
| waitress 3.0.0 | yes | yes | production WSGI server (`main.py` `_run_server`) |
| openpyxl 3.1.2 | yes | no | retail import/export |
| requests 2.32.3 | yes | no | retail-only outbound HTTP usage |
| flask-login 0.6.3 | no | no | unused — was only for the monolith's legacy domain-demo login |
| flask-socketio / python-socketio / python-engineio / simple-websocket | no | no | unused by either product's backend |
| pandas | no | no | unused (was already excluded even in source's Android build — desktop-only) |
| gunicorn / eventlet | no | no | unused on Android in source too |

Not re-verified independently in this phase: the *transitive* dependencies each of
these pip packages pulls in (e.g. `requests` → `urllib3`/`certifi`/`idna`/
`charset_normalizer`) are resolved by Chaquopy's own pip step at build time, the same
way they were in the source project and in the Windows desktop build (Phase 2/3).

## Where Python code is staged from

Source: `repoRoot/{aura_core.py, app.py, config.py, license_validator.py, api/, core/,
database/}` (repo root = `AuraEnterprise/`, i.e. everything).

This phase, per product: `aura-fullsuits/products/{retail,clinic}/backend/**` +
`aura-fullsuits/commercial_runtime/**`, copied by the `stageAuraPython` Gradle task in
each product's `app/build.gradle` — see `android-migration-plan.md`. Neither product
stages the other's backend, and neither references the original `AuraEnterprise` repo.
