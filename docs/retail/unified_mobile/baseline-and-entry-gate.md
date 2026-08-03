# Aura Retail Unified Mobile — Baseline & Entry Gate

## Git state (verified, real evidence)

```
$ git status --short          -> (clean)
$ git branch --show-current   -> phase9.5/expenses-reporting-management-collaboration (before switch)
$ git rev-parse HEAD          -> bd12681...
$ git rev-list -n 1 aura-owner-expenses-reporting-phase9-5e-complete
                               -> bd12681...  (identical -- tag == HEAD exactly)
$ git tag --list | wc -l      -> 24 (all historical tags present, none moved)
```

New branch created directly from the tag:

```
git switch -c feat/retail-unified-mobile-android-ios aura-owner-expenses-reporting-phase9-5e-complete
```

## Legacy repository re-check (read-only, unchanged)

```
$ cd AuraEnterprise\AuraEnterprise
$ git rev-parse HEAD -> 414e6ea5ca9fc698fe075a4ae4464cebf0b7bf34
```

Identical to the hash recorded at Phase 9.5E's own close. Zero commits landed there across the entirety of Phase 9.5E and this entry gate. Real uncommitted WIP present (`core/crm/services/*`, `core/retail/pricing.py`, `docs/retail/RETAIL_SECURITY_PHASE_1.md`, 20 untracked files, 22 modified files) is the user's own separate, pre-existing work, unrelated to and untouched by this session -- same conclusion as every prior phase's re-proof.

## Current Android application — real inspected state

**Application identity** (`android/aura-retail/app/build.gradle`):

| Field | Value |
|---|---|
| `applicationId` | `com.actionaura.retail` |
| `namespace` | `com.actionaura.retail` |
| `versionCode` | `6` |
| `versionName` | `1.0.0-rc.5` |
| `minSdk` | 26 |
| `targetSdk` / `compileSdk` | 34 |
| ABIs | `arm64-v8a`, `x86_64` (Chaquopy's Python 3.12 pre-built-wheel constraint) |
| Signing | `signingConfigs.release` reads `keystore.properties` (gitignored, not present in this checkout) |

**Architecture (confirmed by reading the actual source, not assumed):** The app is a native Kotlin/Compose UI shell that, on launch, boots a **full embedded Flask+waitress HTTP server in-process via Chaquopy** (`ServerBootstrap.kt` -> Python `main.start_server()`), bound to `127.0.0.1` on a free port. The Compose UI is an HTTP client of that local server (`net/ApiClient.kt`, `net/AuraApi.kt`) -- there is effectively **no native business logic**; almost everything (pricing, sales, returns, inventory, reports, receivables/payables) lives in `products/retail/backend/` Python and is staged into the APK at build time (`app.build.gradle`'s `stagedPyOut`/`stageSharedPython` tasks).

**Kotlin source**: 34 files under `app/src/main/java/com/actionaura/retail/`:

| Package | Contents |
|---|---|
| `barcode/` | `BarcodeDebounce`, `HidScanBus`, `HidScanDetector`, `ProductLookup`, `ScannerAdapter` -- camera + external HID scanner handling |
| `identity/` | `CommercialIdentity` -- tenant/license/installation identity placeholders |
| `licensing/` | `Canonical`, `DeviceIdentity`, `LicensingCoordinator`, `OwnerClient` -- real Owner licensing client already exists on Android |
| `net/` | `ApiClient`, `AuraApi`, `Models` -- HTTP client to the local embedded Flask server |
| `server/` | `ServerBootstrap` -- Chaquopy/Flask lifecycle |
| `ui/` | `AppRoot`, `RetailSession`, `components/Components`, `i18n/{Num,Strings}`, `screens/{BackupRestoreScreen,BarcodeScanner,DashboardScreen,LicensingScreen,LoginScreen,Pickers,RetailExtraScreens,RetailScreens,SettingsScreen,SetupScreen}`, `theme/{Color,Shape,Type,Theme}` |
| (root) | `MainActivity` |

**Permissions** (`AndroidManifest.xml`): `CAMERA`, `INTERNET` only.

**Existing tests**: `app/src/test/` has 10 JVM unit test files, **69 tests, 0 failures** (real `./gradlew testDebugUnitTest` run, this entry gate):

```
BarcodeDebounceTest (5), HidScanDetectorTest (7), ProductLookupTest (5),
CanonicalTest (10), DeviceIdentityTest (11), Ed25519CrossVerifyTest (4),
OwnerClientTest (8), SaleContractTest (12), ReadinessContractTest (2),
NumTest (5)
= 69 tests, 0 failures, BUILD SUCCESSFUL
```

**No `androidTest/` (instrumentation test) directory exists.** This is a real, pre-existing gap -- not introduced by this initiative -- to be closed as part of Milestone 21 (test architecture), not silently inherited.

## Retail backend (Python) — real inspected state

`products/retail/backend/api/retail_api.py`: **48 Flask routes** (dashboard stats, categories, products incl. stock-adjust, customers incl. statement/payments/receivables, suppliers incl. statement/payments/payables, purchase orders incl. receive/pay, sales create/list/detail, returns, 6 report endpoints, branches, credit/tax settings, payment-methods, payment void, demo-seed/wipe) + 6 more in `api/import_api.py`.

`products/retail/backend/database/schema.py`: **17 tables** (`products`, `categories`, `customers`, `suppliers`, `sales`, `sale_items`, `returns`, `return_items`, `purchase_orders`, `purchase_order_items`, `inventory_balances`, `inventory_movements`, `payments`, `branches`, `tax_rates`, `journal_entries`, `audit_log`).

`products/retail/tests/`: 12 test files, **194 tests, 0 failures** (verified identical to the count recorded in Phase 9.5E's own final regression, same commit).

**Real finding**: the Python backend's route surface (48 routes / 17 tables: full purchasing/supplier/receivables-payables/branches/journal-entries) is materially **larger** than what the current 34-file Android Compose UI actually exposes (`RetailScreens.kt`/`RetailExtraScreens.kt` cover POS/products/dashboard/backup/settings/licensing -- purchasing, suppliers, branches, and journal entries do not have obvious Android screens yet, pending Milestone 1's full per-screen inventory to confirm). This must not be silently assumed either way -- Milestone 1 will enumerate every screen against every route and record which backend capabilities are/aren't currently Android-reachable.

## Baseline test counts (same commit as the Phase 9.5E tag)

| Suite | Count | Result |
|---|---|---|
| Owner (`owner/tests/`) | 972 | 0 failed (Phase 9.5E final regression, same HEAD) |
| Retail Python (`products/retail/tests/`) | 194 | 0 failed |
| Clinic (`products/clinic/tests/`) | 135 | 0 failed |
| commercial_runtime (`commercial_runtime/tests/`) | 5 | 0 failed |
| licensing_contracts (`commercial_runtime/licensing_contracts/tests/`) | 230 | 0 failed |
| **Android unit** (`android/aura-retail/app/src/test/`) | **69** | **0 failed (this entry gate)** |
| Android instrumentation (`androidTest/`) | **0 -- directory does not exist** | N/A |
| iOS (any) | **0 -- no iOS target exists yet** | N/A |

## Environment constraint (recorded per explicit user decision)

This is a Windows development environment: **no macOS, no Xcode, no Apple Developer signing identity, no physical iPhone.** Per the user's explicit direction for this initiative: iOS-buildable source, Xcode project files, and signing configuration will be written as real code/config throughout this effort, but cannot be compiled, run, or physically tested here. Milestones 19 (Xcode target), 25 (physical iPhone QA), and 27 (App Store/TestFlight submission) are tracked as **BLOCKED-ENVIRONMENT** until executed on a Mac with a physical device -- never silently skipped, never falsely marked PASS.

## Scope decision (per explicit user direction)

This is a multi-week, 28-milestone program. Per the user's explicit choice, it will be executed **milestone-by-milestone across many sessions**, matching the exact discipline established across Phase 9.5A-9.5E: real audits before code, real tests before PASS claims, one commit per milestone, a living gate matrix, and a final tag only once every gate reads PASS with real evidence.

## Entry gate status: PASS

All six entry-gate steps complete. Proceeding to Milestone 1 (complete Android feature inventory).
