# Aura Retail — Functional Audit

Status legend: PASS (present and works per source/tests) · GAP (missing or
broken) · PLATFORM-DIFF (present on one platform, not the other — see `05`/`19`
for the financial-specific case, `19` for full parity detail) · UNVERIFIED
(needs a device/printer/scanner this environment doesn't have).

| Feature | Windows | Android | Notes |
|---|---|---|---|
| **Onboarding / account creation** | **PROVEN BROKEN — no self-service path exists at all** | **PROVEN BROKEN — same shared backend** | See "CRITICAL FINDING" immediately below this table. |
| Login/logout | PASS | PASS | Shared `commercial_runtime` auth, same code path both platforms |
| Dashboard | PASS | PASS | Server-computed, same route both platforms (`05`) |
| Products/categories/suppliers | PASS | PASS | CRUD routes present, company-scoped (`06`) |
| Inventory / stock adjustments | PASS, with a gap | PASS, with a gap | Server never prevents negative stock (`03`/`06`); client-side guard exists in Android (`addOne()`), none confirmed in web JS |
| POS / cart | PASS | **PASS WITH A SEVERE GAP** | Android cart has no discount UI and no tax computation at all (`03`) |
| Sales / receipts | PASS | **PASS WITH A SEVERE GAP** | Same as above — every Android sale is under-taxed/undiscounted |
| Discounts | PASS (web) | **GAP — no discount UI exists on Android** | `03` |
| Taxes | PASS (web, correct default mode) | **GAP — never applied on Android** | `03` |
| Returns/refunds | PASS functionally, **GAP on validation** | Same (shared server route) | No original-sale linkage/quantity/duplicate check on either platform (`03`) |
| Reports | PASS (dashboard/trend routes exist) | Not independently re-verified for full report parity in Android UI within this pass | UNVERIFIED for exhaustive report-screen-by-report-screen parity |
| Import (CSV/Excel/.db) | PASS | N/A — no import UI found in Android (import is a web-only workflow) | `import_api.py` exists and is tested (`retail_import_export_test.py`, 25/25 pass); no Android screen invokes it |
| Export | **NOT PRESENT IN SOURCE** | **NOT PRESENT IN SOURCE** | Confirmed absent from `retail_api.py`/`import_api.py` — matches the pre-existing, already-documented finding that export never existed in the original source; correctly not fabricated in the extraction |
| Barcode keyboard-wedge scanner | UNVERIFIED | N/A | Not independently tested with real hardware |
| Android camera barcode scanner | N/A | **BUILD ONLY** — `BarcodeScanner.kt` (CameraX + ML Kit) compiles and packages (Phase 4); camera behavior itself REQUIRES PHYSICAL DEVICE VALIDATION, never exercised on real hardware in any phase to date | See `docs/android/android-retail-parity-matrix.md` |
| Unknown-barcode handling / product creation by scan | UNVERIFIED | UNVERIFIED | Depends on the above |
| Printing / receipts | **NOT PRESENT** | **NOT PRESENT** | No `window.print()`/print call found in `subsystem-retail.js`; no print integration found in Android Kotlin source |
| Sharing | **NOT PRESENT** | **NOT PRESENT** | No share-intent code found in Android; no equivalent web feature |
| Localization (English/Arabic) | PASS | PASS | `retail_localization_test.py` 18/18 pass isolated; Android `Strings.kt`/`AppLocale` migrated and compiles (Phase 4) |
| RTL | PASS (structural) | **BUILD ONLY / REQUIRES PHYSICAL DEVICE VALIDATION** | Web CSS RTL not independently re-verified visually this pass; Android's `LocalLayoutDirection` wiring compiles but was never visually confirmed on-device |
| Theme | PASS | PASS | Present both platforms, not independently re-derived beyond confirming source exists |
| Settings | PASS | PASS | `SettingsScreen.kt` exists; server-side settings routes exist (`/settings/tax`, etc.) |
| Users/roles | PASS (tenant-scoped, `commercial_runtime`) | Same (shared backend) | No Retail-specific fine-grained role model was found comparable to Clinic's `@require_clinic_role` — Retail's authorization is subsystem-level, not role-level within the subsystem; flagged, not necessarily a defect, since POS staff typically share one operational role in practice |
| Offline operation | PASS by architecture | PASS by architecture | Both platforms run a fully local embedded server + SQLite; no network dependency for core operation on either platform (not independently stress-tested for "airplane mode mid-transaction" in this pass) |
| Startup/shutdown | PASS (Windows, real smoke test) | BUILD ONLY (Android, no device) | `docs/build/retail-windows-build-report.md` |
| Restart persistence | PASS (Windows, real smoke test) | UNVERIFIED (Android, no device) | Same source |
| Large database behavior | UNVERIFIED | UNVERIFIED | See `17` |

## CRITICAL FINDING: Retail has no self-service way to create the first user account

**PROVEN, by direct source read, cross-checked three ways:**

1. `products/retail/backend/app.py` registers `auth_bp` and `retail_bp`/`import_bp`
   only — it **never imports or registers `commercial_runtime.identity.onboarding_routes.onboarding_bp`**.
   Compare directly: `products/clinic/backend/app.py` line 56
   (`from commercial_runtime.identity.onboarding_routes import onboarding_bp`)
   and line 62 (`app.register_blueprint(onboarding_bp)`) — both present in
   Clinic, both **absent** in Retail.
2. `commercial_runtime/identity/auth_routes.py` (the blueprint Retail *does*
   register) contains exactly four routes: `/api/auth/login`, `/logout`,
   `/language`, `/active-modules` — no account-creation route of any kind.
3. `products/retail/backend/app.py::init_app()` calls only
   `init_registry_db()` and `init_retail()` — no default-admin seeding, no
   demo user, nothing. `retail_pricing_test.py`/`retail_security_test.py`'s own
   test fixtures create test users by **inserting directly into the SQLite
   `users` table via raw SQL** (`_make_company_user()`), not through any API —
   because there is no API to do it through.

**Consequence**: a fresh Retail installation — Windows or Android — has a
working server, a working database, and a working login page, but **no way for
a real customer to ever create the first account and log in**, short of direct
database access (a support engineer manually `INSERT`-ing a row, exactly as the
test suite does). The Phase 2B Windows smoke test's own wording confirms this
was already effectively known but not flagged as a defect at the time: step 3
"reach login/onboarding" notes "`POST /api/auth/login` responds (400 with no
credentials, 200 **with a real inserted user**" — i.e., the smoke test itself
depended on a manually inserted user, the same workaround a real customer
cannot perform.

On Android specifically, `SetupScreen.kt` **does exist** and does call
`ApiClient.get().createAdmin(...)` (confirmed present in the Android Retail
source, migrated from the shared UI code) — but since the corresponding server
route is never registered in Retail's Flask app, this call would receive a 404
from a real running Retail server. This was never caught by Phase 4's Android
build/lint/test pass because none of those check runtime route availability —
only that the code compiles.

**Classified P0** in the defect registry — this is more fundamental than any
other Retail finding in this audit: without it, no other feature (POS, tax,
returns, dashboard) is reachable by an actual first-time customer at all. It
is likely the reason no dedicated Retail onboarding smoke-test step exists in
any prior phase's documentation — the gap has been present since the original
Phase 1/2 extraction and has not been exercised end-to-end by any human or
automated test to date.

## Commercial impact summary

The single most commercially significant functional finding for Retail is the
missing onboarding path above — it blocks first use entirely, on both
platforms, full stop. The Android POS tax/discount gap (`03`) is the second
most significant — not a UX nitpick, it makes Android unsuitable as a POS
terminal for any tax-registered business until fixed. The returns-validation
gap (`03`) is a close third — it is exploitable identically on both platforms
and represents a direct cash-loss vector available to any cashier.
