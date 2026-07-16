# Android Migration Plan (Phase 4)

## Goal

Turn the source monorepo's single Gradle project (two flavors, one shared
`applicationId` namespace, Python staged from the repo root) into **two fully
independent Gradle projects** inside `aura-fullsuits/android/`, each buildable
without any access to the original `AuraEnterprise` repository at build or runtime.

## Target layout

```
aura-fullsuits/android/
  aura-retail/
    settings.gradle          rootProject.name = "AuraRetail"
    build.gradle              buildscript classpath (AGP/Kotlin/Compose/Chaquopy)
    gradle.properties, gradlew, gradlew.bat, gradle/wrapper/
    keystore.properties.template, local.properties.template
    app/
      build.gradle            namespace com.actionaura.retail, applicationId com.actionaura.retail
      proguard-rules.pro
      src/main/
        AndroidManifest.xml
        java/com/actionaura/retail/...   (retail-only screens + shared infra)
        python/main.py, android_platform.py
        res/...
  aura-clinic/
    (mirror structure)        namespace/applicationId com.actionaura.clinic
```

## Why two independent projects, not two flavors of one project

The task requires this explicitly. It also happens to satisfy Phase 4G's "distinct
namespace" requirement more rigorously than the source's own flavor split did: the
source shared one Java/Kotlin package (`com.actionaura.enterprise`, with per-flavor
`applicationId` override only) and one `BuildConfig.FLAVOR`-branched source tree.
Two independent projects mean the retail and clinic codebases cannot accidentally
reference each other, cannot leak a shared `BuildConfig` flag, and can evolve (add
retail-only or clinic-only screens/dependencies) without touching the other product.

## Migration steps taken

1. **Scaffold**: copied `gradle/`, `gradlew(.bat)`, `gradle.properties`,
   `keystore.properties.template`, `local.properties.template`, `proguard-rules.pro`,
   and the full `app/src/main/{java,python,res}` tree + `AndroidManifest.xml` from the
   source project into both `android/aura-retail/` and `android/aura-clinic/`.
2. **Package rename**: `com.actionaura.enterprise` → `com.actionaura.retail` /
   `com.actionaura.clinic` (directory move + `sed` across all `.kt`/`.java`/`.pro`
   files), verified zero leftover references.
3. **Delete the other product's screens** from each tree (see
   `android-source-inventory.md` for the exact file list per product).
4. **Split `BuildConfig.FLAVOR`-branched files** (`AppRoot.kt`, `DashboardScreen.kt`,
   `SetupScreen.kt`) into clean, single-product versions — same UI/business logic for
   the kept product, dead branch removed entirely, no `BuildConfig.FLAVOR` references
   left anywhere (`BuildConfig` itself is regenerated per-project by AGP with only
   fields this phase defined: `PRODUCT_CODE`, `BUILD_ENV`).
5. **Rewrite `python/main.py`** per product: no longer imports `aura_core` (doesn't
   exist here); instead adds `products/{retail,clinic}/backend` to `sys.path` and
   calls `app.init_app()` + `waitress.serve(app.app, ...)` in a background thread —
   the same pattern as `products/{retail,clinic}/desktop/launcher_{retail,clinic}.py`,
   adapted to Chaquopy's `start_server(files_dir) -> port` /
   `wait_until_ready(port, timeout)` call contract expected by `ServerBootstrap.kt`
   (contract unchanged, so the Kotlin side needed no edits).
6. **Author `settings.gradle` / root `build.gradle` / `app/build.gradle` from
   scratch** per product: no `flavorDimensions`/`productFlavors`; `namespace` and
   `applicationId` hardcoded to the single product; Chaquopy `pip{}` trimmed to only
   the packages that product's backend actually imports (see
   `android-dependency-map.md`); two new Gradle tasks —
   `stageAuraPython` (copies `products/{product}/backend/**` +
   `commercial_runtime/**` from `aura-fullsuits`, excluding `__pycache__`/`.pyc`/tests)
   and `stageAuraAssets` (copies `products/{product}/frontend/**`, consumed by
   `AssetInstaller.java`'s `assets/bundle/**` extraction) — both wired into
   `preBuild` and the Python/asset merge tasks with explicit `dependsOn`, mirroring
   the source's own staging-task wiring pattern (which is proven to work).
7. **Fix per-product resources**: `strings.xml` now defines `app_name` directly
   (`"Aura POS"` / `"Aura Clinic"`) since the flavor `resValue` mechanism no longer
   exists; clinic's manifest dropped the unused `CAMERA` permission (Phase 4F).
8. **Add build environments** (Phase 4J): `debug` (existing), `staging` (new —
   release-shaped/non-debuggable, `applicationIdSuffix ".staging"`, `matchingFallbacks
   = ['debug']` so it resolves external dependency variants), `release` (existing,
   conditionally signed if `keystore.properties` exists). Each sets a
   `BuildConfig.BUILD_ENV` string field.
9. **Add commercial-identity placeholders** (Phase 4G): new
   `identity/CommercialIdentity.kt` per product exposing `PRODUCT_CODE`
   (`AURA_RETAIL`/`AURA_CLINIC`), a persisted random `installationId` (SharedPreferences,
   product-namespaced prefs file), `environment`, `appVersion`, a placeholder
   `SCHEMA_VERSION`, and a `tenantId` that is hardcoded `null` — not wired into any
   license/activation logic, per the explicit phase-4 exclusion.
10. **`.gitignore` update**: source's single-project patterns
    (`android/local.properties`, `android/.gradle/`, ...) rewritten to the two-project
    layout (`android/*/local.properties`, `android/*/.gradle/`, ...), plus an explicit
    `android/*/app/build/outputs/` exclusion for APK/AAB artifacts.

## Explicitly not done in this phase

Per the task's exclusions: no Owner Control Center, no license issuance/activation
enforcement, no subscription-expiration enforcement, no customer-specific package
generation, no payment integration, no production VPS deployment, no automatic remote
update execution, no Aura Core integration. `CommercialIdentity.kt` is scaffolding
only — nothing calls it yet.

## Build/test evidence

See `android-retail-build-report.md` and `android-clinic-build-report.md` for the
exact Gradle commands executed and their results, and the two parity matrices for
per-workflow PASS/LIMITATION status.
