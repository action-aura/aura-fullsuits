# Aura Retail Unified Mobile — Android Feature Parity Matrix (Milestone 1)

> **Product Owner Scope Override (post-Milestone-1):** the "6 items pending
> product-owner decision" classification below (categories, branches,
> reports/sales-trend, reports/top-products, import API, plus the dead
> Clinic surface) has been superseded by an authoritative product-scope
> decision: Aura Retail Unified Mobile is a complete Retail mobile product,
> not a strict translation of the current 17-route Android navigation
> graph. Every one of those 6 items now has a real classification and
> implementation path in `complete-retail-capability-matrix.md` — read that
> document as authoritative for those items. This document is preserved
> unmodified below as the honest historical record of what Milestone 1's
> first pass actually found and concluded at the time.

Built by reading the real repository: `android/aura-retail/app/src/main/java/com/actionaura/retail/` (34 Kotlin files, navigation graph in `ui/AppRoot.kt`), `net/AuraApi.kt` (the actual Retrofit interface calling the embedded backend), and `products/retail/backend/api/retail_api.py` (48 routes / 17 tables). Every row below is grounded in a real file:line, not summarized from memory.

**Statuses used:** AUDITED, SHARED_IMPLEMENTATION_REQUIRED, ANDROID_ADAPTER_REQUIRED, IOS_ADAPTER_REQUIRED, NOT_APPLICABLE (with reason), OUT_OF_SCOPE (with reason).

## Real finding: the Android build carries dead Clinic-domain API surface

`net/AuraApi.kt` lines 26-73 define a full Clinic Retrofit surface (patients, appointments, prescriptions, invoices, doctors, services, lab-expenses) inherited from the pre-split shared-flavor `AppRoot.kt` (per that file's own header comment). `ui/AppRoot.kt`'s `retailGraph()` (the actual navigation graph, lines 268-288) contains **zero** Clinic routes or screens. This code is unreachable in the Retail-only build. **OUT_OF_SCOPE for this initiative** (Clinic iOS is explicitly out of scope per the governing spec) — flagged here so it is not silently carried into the shared KMP core as if it were a real Retail feature, and so it can be deleted from `AuraApi.kt` during the Milestone 5 shared-repository migration rather than copied forward.

## Real finding: backend routes with no current Android caller

Comparing `retail_api.py`'s 48 routes against every call site in `AuraApi.kt`, these backend capabilities exist but are **not currently reachable from the Android app**:

| Route | Status | Note |
|---|---|---|
| `GET/POST /categories` | ANDROID_ADAPTER_REQUIRED or NOT_APPLICABLE — needs product-owner call | No category-management screen exists; products may use a free-text or hardcoded category field (to confirm in Milestone 5 during ProductsScreen migration) |
| `GET/POST /branches` | Same — needs product-owner call | No branch-management screen; single-branch usage assumed but not confirmed |
| `GET /reports/sales-trend` | Same | Not wired to `ReportsScreen` |
| `GET /reports/top-products` | Same | Not wired to `ReportsScreen` |
| `api/import_api.py` (6 routes) | Same | No import UI exists on Android at all |
| `DELETE /demo-wipe`, `POST /demo-seed` | NOT_APPLICABLE | Dev/demo-only, never end-user-facing; not a parity item |

These are **not silently dropped** — each needs an explicit product-owner decision (build the Android/iOS screen for real parity, or mark NOT_APPLICABLE with a documented reason) before Milestone 1 can be marked fully closed. Recorded here, not assumed either way.

## Navigation graph (real, from `AppRoot.kt` `retailGraph()`)

16 routes + 3 pre-shell phases (`SETUP`/`LOGIN`/`LOADING`) + a `licensing`/`backup` sub-screen pair reached via Settings, not the bottom nav.

| # | Route | Screen composable | Bottom-nav tab? |
|---|---|---|---|
| 1 | `dashboard` | `DashboardScreen` | Yes (tab 1) |
| 2 | `pos` | `PosScreen` | Yes (tab 2) |
| 3 | `products` | `ProductsScreen` | Yes (tab 3) |
| 4 | `more` | `MoreScreen` | Yes (tab 4, hub for the rest) |
| 5 | `reports` | `ReportsScreen` | via More |
| 6 | `transactions` | `TransactionsScreen` | via More |
| 7 | `returns` | `ReturnsScreen` | via More |
| 8 | `customers` | `CustomersScreen` | via More |
| 9 | `receivables` | `ReceivablesScreen` | via More |
| 10 | `suppliers` | `SuppliersScreen` | via More |
| 11 | `purchase_orders` | `PurchaseOrdersScreen` | via More |
| 12 | `payables` | `PayablesScreen` | via More |
| 13 | `cash_summary` | `DailyCashScreen` | via More |
| 14 | `aging` | `AgingScreen` | via More |
| 15 | `retail_settings` | `RetailSettingsScreen` | via drawer |
| 16 | `backup` | `BackupRestoreScreen` | via Settings |
| 17 | `licensing` | `LicensingScreen` | via Settings |

Plus: `SetupScreen` (first-run onboarding/admin creation), `LoginScreen`, `LoadingScreen` (splash while `ServerBootstrap` boots), and an **`AiSheet`** bottom sheet reachable from every screen's top bar — real finding: this is an explicit non-functional stub (`"Aura AI is coming soon ✨"`, `AppRoot.kt:260`), not a real feature. **NOT_APPLICABLE for parity purposes** — nothing to port, the placeholder itself may be ported as-is or dropped, product-owner call.

## Feature inventory

| Feature code | Name | Screen | Backend route(s) | DB tables | Hardware | Offline behavior | Migration target | Status |
|---|---|---|---|---|---|---|---|---|
| RETAIL-ONBOARD-01 | First-run admin setup | `SetupScreen` | `POST /api/onboarding/create-admin`, `GET /api/onboarding/status` | (auth) | none | required (no server to reach) | shared `usecases/OnboardingUseCase` | AUDITED |
| RETAIL-AUTH-01 | Login/session/logout | `LoginScreen`, drawer logout | `POST/GET/POST /api/auth/{login,session,logout}` | (auth) | none | session persists via cookie (`ApiClient.init` — persistent login cookie) | shared `repositories/SessionRepository` | AUDITED |
| RETAIL-DASH-01 | Dashboard KPIs | `DashboardScreen` | `GET /api/sub/retail/dashboard/stats` | sales, products, inventory_balances | none | fully offline (local DB) | shared `usecases/DashboardUseCase` | AUDITED |
| RETAIL-POS-01 | Product grid + search | `PosScreen`, `ProductTile` | `GET /api/sub/retail/products` | products, categories | none | fully offline | shared `repositories/ProductRepository` | AUDITED |
| RETAIL-POS-02 | Cart + checkout + totals | `PosScreen` | `POST /api/sub/retail/sales` | sales, sale_items, inventory_balances, inventory_movements | none | fully offline; server-authoritative totals (financial core, Milestone 3) | shared `financial/` + `usecases/FinalizeSaleUseCase` | SHARED_IMPLEMENTATION_REQUIRED (financial core is the Milestone 3 centerpiece) |
| RETAIL-POS-03 | Barcode scan-to-cart | `PosScreen`, `BarcodeScanner.kt` | (local product lookup only) | products | Camera (CameraX/ML Kit); external HID keyboard scanner | fully offline | shared scanner contract (Milestone 14) + platform camera adapters | ANDROID_ADAPTER_REQUIRED / IOS_ADAPTER_REQUIRED |
| RETAIL-POS-04 | Receipt + share after sale | `PaymentSuccess`, `shareReceipt` | (local render) | sales, sale_items | none | fully offline | shared `receipts/` (Milestone 15) | SHARED_IMPLEMENTATION_REQUIRED |
| RETAIL-PROD-01 | Products list/search | `ProductsScreen` | `GET /api/sub/retail/products` | products | none | fully offline | shared `repositories/ProductRepository` | AUDITED |
| RETAIL-PROD-02 | Add/edit product | `AddProductSheet`, `EditProductSheet` | `POST/PATCH /api/sub/retail/products{,/{id}}` | products | none | fully offline | shared `usecases/ManageProductUseCase` | AUDITED |
| RETAIL-PROD-03 | Stock adjustment | `EditProductSheet` (implied) | `POST /api/sub/retail/products/{id}/stock-adjust` | inventory_balances, inventory_movements | none | fully offline | shared, part of financial/inventory core | SHARED_IMPLEMENTATION_REQUIRED |
| RETAIL-TXN-01 | Sales history / transactions | `TransactionsScreen`, `SaleRow`, `SaleDetailSheet` | `GET /api/sub/retail/sales/{recent,{id}}` | sales, sale_items | none | fully offline | shared `repositories/SalesRepository` | AUDITED |
| RETAIL-RET-01 | Returns list + process return | `ReturnsScreen`, `ProcessReturnSheet` | `GET/POST /api/sub/retail/returns` | returns, return_items, inventory_balances | none | fully offline; refund-vs-sold-quantity invariant (financial core) | shared `usecases/ProcessReturnUseCase` | SHARED_IMPLEMENTATION_REQUIRED |
| RETAIL-CUST-01 | Customers list/search/add | `CustomersScreen`, `AddCustomerSheet` | `GET/POST /api/sub/retail/customers` | customers | none | fully offline | shared `repositories/CustomerRepository` | AUDITED |
| RETAIL-CUST-02 | Customer credit settings | `CustomerCreditSheet` | `PATCH /api/sub/retail/customers/{id}` | customers | none | fully offline | shared, part of AR core | AUDITED |
| RETAIL-AR-01 | Receivables (AR) list | `ReceivablesScreen` | `GET /api/sub/retail/customers/receivables` | customers, sales, payments | none | fully offline | shared `usecases/ReceivablesUseCase` | AUDITED |
| RETAIL-AR-02 | Customer statement | `StatementSheet` | `GET /api/sub/retail/customers/{id}/statement` | customers, sales, payments | none | fully offline | shared | AUDITED |
| RETAIL-AR-03 | Record customer payment | `StatementSheet` (implied) | `POST /api/sub/retail/customers/{id}/payments` | payments, customers | none | fully offline | shared, financial-invariant-bearing | SHARED_IMPLEMENTATION_REQUIRED |
| RETAIL-AR-04 | Void payment | (implied, via statement) | `POST /api/sub/retail/payments/{id}/void` | payments | none | fully offline | shared | AUDITED |
| RETAIL-SUPP-01 | Suppliers list/add | `SuppliersScreen`, `AddSupplierSheet` | `GET/POST /api/sub/retail/suppliers` | suppliers | none | fully offline | shared `repositories/SupplierRepository` | AUDITED |
| RETAIL-PO-01 | Purchase orders list/detail | `PurchaseOrdersScreen`, `PoRow`, `PoDetailSheet` | `GET /api/sub/retail/purchase-orders{,/{id}}` | purchase_orders, purchase_order_items | none | fully offline | shared `repositories/PurchaseOrderRepository` | AUDITED |
| RETAIL-PO-02 | Create purchase order | `CreatePoSheet` | `POST /api/sub/retail/purchase-orders` | purchase_orders, purchase_order_items | none | fully offline | shared | AUDITED |
| RETAIL-PO-03 | Receive purchase order | `PoDetailSheet` (implied) | `POST /api/sub/retail/purchase-orders/{id}/receive` | purchase_order_items, inventory_balances, inventory_movements | none | fully offline; stock-mutation invariant | shared, financial-core-adjacent | SHARED_IMPLEMENTATION_REQUIRED |
| RETAIL-AP-01 | Payables (AP) list | `PayablesScreen` | `GET /api/sub/retail/suppliers/payables` | suppliers, purchase_orders, payments | none | fully offline | shared | AUDITED |
| RETAIL-AP-02 | Supplier statement | `SupplierStatementSheet` | `GET /api/sub/retail/suppliers/{id}/statement` | suppliers, purchase_orders, payments | none | fully offline | shared | AUDITED |
| RETAIL-AP-03 | Pay purchase order / supplier payment | (implied) | `POST /api/sub/retail/purchase-orders/{id}/pay`, `POST /api/sub/retail/suppliers/{id}/payments` | payments, purchase_orders | none | fully offline; financial-invariant-bearing | shared | SHARED_IMPLEMENTATION_REQUIRED |
| RETAIL-RPT-01 | Reports summary | `ReportsScreen`, `StatCard` | `GET /api/sub/retail/reports/summary` | sales, returns | none | fully offline | shared `usecases/ReportsUseCase` | AUDITED |
| RETAIL-RPT-02 | Payment-methods report | `ReportsScreen` (implied) | `GET /api/sub/retail/reports/payment-methods` | payments | none | fully offline | shared | AUDITED |
| RETAIL-CASH-01 | Daily cash summary | `DailyCashScreen` | `GET /api/sub/retail/reports/daily-cash` | sales, payments | none | fully offline | shared | AUDITED |
| RETAIL-AGE-01 | AR/AP aging | `AgingScreen`, `agingColor` | `GET /api/sub/retail/reports/aging` | customers/suppliers, sales/purchase_orders | none | fully offline | shared | AUDITED |
| RETAIL-SET-01 | Settings (credit, tax, payment methods) | `RetailSettingsScreen` | `GET/POST /api/sub/retail/settings/credit`, `.../settings/tax` (backend only — no confirmed Android call site for `/settings/tax` in `AuraApi.kt`; needs Milestone 5 confirmation), `GET/POST /api/sub/retail/payment-methods` | (settings tables, TBD) | none | fully offline | shared `repositories/SettingsRepository` | AUDITED (tax settings call-site to re-verify) |
| RETAIL-BACKUP-01 | Backup / list / restore | `BackupRestoreScreen` | `POST /api/backup/create`, `GET /api/backup/list`, `POST /api/backup/restore` | (whole DB) | filesystem | fully offline | shared `backup/` (Milestone 16) | SHARED_IMPLEMENTATION_REQUIRED |
| RETAIL-LIC-01 | Licensing status/activation | `LicensingScreen`, `licensing/*.kt` | Owner licensing endpoints (`licensing/OwnerClient.kt`) | (secure storage, not business DB) | Android Keystore | signed offline lease (already designed — `licensing/Canonical.kt`, `DeviceIdentity.kt`) | shared `licensing/` (Milestones 7-11) — **this is the richest existing asset to reuse, not rebuild** | AUDITED |
| RETAIL-BC-01 | Barcode camera scan (continuous) | `BarcodeScanner.kt` | n/a | n/a | Camera (CameraX + ML Kit) | fully offline | shared scanner contract + Android CameraX adapter + iOS AVFoundation/Vision adapter | ANDROID_ADAPTER_REQUIRED / IOS_ADAPTER_REQUIRED |
| RETAIL-BC-02 | External HID scanner | `HidScanBus.kt`, `HidScanDetector.kt`, `ScannerAdapter.kt` | n/a | n/a | Physical HID barcode scanner (keyboard-wedge) | fully offline | shared scanner contract + Android `KeyEvent` adapter + iOS `UIKeyCommand`/software-keyboard adapter | ANDROID_ADAPTER_REQUIRED / IOS_ADAPTER_REQUIRED |
| RETAIL-I18N-01 | English/Arabic + RTL | `ui/i18n/{Strings,Num}.kt`, `LocalLayoutDirection` flip in `AppRoot.kt` | n/a | n/a | none | fully offline | shared string/number resources (Milestone 17) | AUDITED |
| RETAIL-THEME-01 | Light/dark mode | `ui/theme/*.kt` | n/a | n/a | none | fully offline | shared Compose Multiplatform theme | AUDITED |

## Permissions (real, from `AndroidManifest.xml`)

Only `CAMERA` and `INTERNET` (the latter for `127.0.0.1` loopback to the embedded server, not external network — real finding to carry into the iOS `NSAppTransportSecurity`/local-loopback design in Milestone 19, and into the Milestone 2 architecture decision to eliminate the loopback-server pattern entirely).

## Status summary

| Status | Count |
|---|---|
| AUDITED (real screen/route/table identified, straightforward migration) | 21 |
| SHARED_IMPLEMENTATION_REQUIRED (financial-invariant-bearing, needs the Milestone 3 core, not a mechanical port) | 8 |
| ANDROID_ADAPTER_REQUIRED / IOS_ADAPTER_REQUIRED (hardware-bound) | 2 (barcode camera, HID scanner) |
| NOT_APPLICABLE | 1 (AI-sheet stub) |
| OUT_OF_SCOPE | 1 (dead Clinic API surface) |
| Needs product-owner decision before status can be assigned | 6 backend routes (categories, branches, sales-trend, top-products, import API) |

## What Milestone 1 has NOT yet done (honest, not silently skipped)

- Full per-field data-dictionary mapping of every screen's form fields to backend request/response DTOs (`net/Models.kt`, 405 lines) — needed before Milestone 5's use-case implementation, not before Milestone 1's own close.
- `RetailSettingsScreen`'s exact settings surface (118 lines, not yet read line-by-line) — tax settings call-site needs confirmation.
- Physical-device evidence references (prior Android validation docs under `docs/android/`, `docs/mobile/wave1a/`) — to be cross-referenced in Milestone 24, not duplicated here.
- The categories/branches/reports-trend/import-API product-owner decision, listed above, blocks nothing else and can proceed in parallel.

Milestone 1 status: **substantially complete, real evidence throughout, 6 items pending an explicit product-owner call** (not a blocker for Milestone 2's architecture work, which does not depend on those 6 items).
