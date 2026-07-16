# Android Source Inventory (Phase 4A)

Source of truth for this inventory: `AuraEnterprise/android/` in the original,
read-only monorepo (never modified by this phase). Captured 2026-07-16.

## Source project shape

The original Android app is **one Gradle project** (`AuraEnterprise/android/`,
`rootProject.name = "AuraEnterprise"`, single module `:app`) built as **two product
flavors** (`clinic`, `retail`) sharing one `applicationId` namespace
(`com.actionaura.enterprise`, overridden per-flavor to `com.actionaura.clinic` /
`com.actionaura.retail`) and one Chaquopy Python source tree staged from the
**repo root** at build time (`repoRoot = rootProject.projectDir.parentFile`).

| File | Role |
|---|---|
| `android/build.gradle` | Root buildscript: AGP 8.5.2, Kotlin/Compose-compiler 2.0.21, Chaquopy 16.0.0 classpaths |
| `android/settings.gradle` | `rootProject.name = "AuraEnterprise"`, `include ':app'` |
| `android/app/build.gradle` | 235 lines — namespace/applicationId, Chaquopy pip config, flavor dimension `"module"` (`clinic`/`retail`), staging `Copy` tasks pulling shared Python + web assets from the repo root, full dependency block |
| `android/gradle/wrapper/gradle-wrapper.properties` | Gradle 8.9 |
| `android/app/src/main/AndroidManifest.xml` | INTERNET + CAMERA permissions, `allowBackup="false"`, `networkSecurityConfig`, one exported `MainActivity`, no other components |
| `android/app/src/main/res/xml/network_security_config.xml` | Loopback-only cleartext, HTTPS elsewhere (already correct; reused verbatim) |
| `android/app/src/main/python/main.py` | Chaquopy entry point — imports `aura_core` (the shared monolith bootstrap) |
| `android/app/src/main/python/android_platform.py` | Pure path-mapping helper (`filesDir` → `{bundle, data}`); product-agnostic |
| `android/app/src/main/java/.../AssetInstaller.java` | Extracts `assets/bundle/**` (static/templates/config.json) into `filesDir/bundle` on first run / version bump; product-agnostic |
| `android/app/src/main/java/.../server/ServerBootstrap.kt` | Starts Chaquopy's Python interpreter, calls `main.start_server()` / `main.wait_until_ready()`; product-agnostic except package name |
| `android/app/src/main/java/.../MainActivity.kt` | Hosts the Compose `AppRoot()`; product-agnostic |
| `android/app/src/main/java/.../ui/AppRoot.kt` | 321 lines — nav shell, drawer, bottom nav, AI sheet; branches on `BuildConfig.FLAVOR` for clinic-vs-retail tabs/routes/suggestions |
| `android/app/src/main/java/.../ui/screens/DashboardScreen.kt` | 163 lines — one file holding both `ClinicMetrics` and `RetailMetrics`, gated by `BuildConfig.FLAVOR` |
| `android/app/src/main/java/.../ui/screens/SetupScreen.kt` | First-run admin-account screen; one `BuildConfig.FLAVOR`-gated label string |
| Clinic-only screens | `PatientsScreen.kt`, `PatientDetailScreen.kt`, `AppointmentsScreen.kt`, `BillingScreen.kt`, `ClinicExtraScreens.kt` (Doctors/Lab/Prescriptions) |
| Retail-only screens | `RetailScreens.kt` (795 lines, POS/cart/checkout/products), `RetailExtraScreens.kt` (1494 lines, Receivables/Payables/Suppliers/PurchaseOrders/Aging/DailyCash/Transactions/Customers), `BarcodeScanner.kt` (CameraX + ML Kit) |
| Shared screens/infra | `LoginScreen.kt`, `SettingsScreen.kt`, `Pickers.kt`, `net/ApiClient.kt`, `net/AuraApi.kt`, `net/Models.kt`, `ui/components/Components.kt`, `ui/i18n/{Strings,Num}.kt`, `ui/theme/*` |

Total Kotlin/Java: ~5,466 lines across both flavors combined (source measurement).

## What was and wasn't migrated

Migrated verbatim (product-agnostic, no source changes needed beyond package rename):
`android_platform.py`, `AssetInstaller.java`, `ServerBootstrap.kt`, `MainActivity.kt`,
`network_security_config.xml`, `ui/components/`, `ui/theme/`, `ui/i18n/`, `LoginScreen.kt`,
`SettingsScreen.kt`, `Pickers.kt`, `net/ApiClient.kt`, `net/AuraApi.kt`, `net/Models.kt`,
gradle wrapper, `proguard-rules.pro` (package references fixed).

Rewritten per product (removed `BuildConfig.FLAVOR` branching, kept only the relevant
product's logic): `ui/AppRoot.kt`, `ui/screens/DashboardScreen.kt`, `ui/screens/SetupScreen.kt`.

Rewritten from scratch (source depended on the monolith's `aura_core`, which does not
exist in `aura-fullsuits`): `python/main.py` — now imports
`products/{retail,clinic}/backend/app` directly, mirroring
`products/{retail,clinic}/desktop/launcher_{retail,clinic}.py`'s own bootstrap instead.

Rewritten from scratch (source built one Gradle project with two flavors; this phase
requires two independent projects): `settings.gradle`, `build.gradle`, `app/build.gradle`
— authored fresh per product, staging Python/assets from `aura-fullsuits/products/*` and
`aura-fullsuits/commercial_runtime` instead of the monolith repo root.

New, not present in source: `identity/CommercialIdentity.kt` (Phase 4G placeholder
identity contract), per-product `strings.xml` `app_name` (replaces the flavor
`resValue` mechanism), `keystore.properties.template` / `local.properties.template`
(copied from source, unchanged), `staging` build type (Phase 4J).

Deleted per product (dead code for that product once `BuildConfig.FLAVOR` branching
was removed): retail tree lost `PatientsScreen.kt`, `PatientDetailScreen.kt`,
`AppointmentsScreen.kt`, `ClinicExtraScreens.kt`, `BillingScreen.kt`; clinic tree lost
`RetailScreens.kt`, `RetailExtraScreens.kt`, `BarcodeScanner.kt`. Clinic's
`AndroidManifest.xml` also lost the (unused) `CAMERA` permission — see
`docs/privacy/clinic-android-data-boundary.md`.
