# Mobile Screen/Route Matrix (M6.0)

Real, cross-referenced matrix of every one of the 17 real navigation
routes in the existing Android Retail app (`android/aura-retail/`),
against the real current state of `mobile/aura-retail-unified/shared`.
Real evidence for every column's claim lives in
`presentation-authority-audit.md`. "M6 implementation status" reflects
this milestone's own scope (Category/Branch/Reporting/Import Center
vertical slices only, per M6.16-M6.19); everything else is honestly
marked `NOT_IN_M6`, not silently implied complete.

| # | Android route | Screen | User purpose | Current API dependency | Current local Flask dependency | Shared use case | Shared repository | Shared UI target (M6+) | Later milestone dependency | Android status | iOS status | M6 status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `dashboard` | `DashboardScreen` | Home: greeting, quick actions, 4 KPI tiles | `GET api/sub/retail/dashboard/stats` | Yes | Real (`DashboardRepository`) | `SqlDelightDashboardRepository` | `ReportingUiVerticalSlice` (dashboard cards) | none | Legacy: shipping | N/A (legacy is Android-only) | `IN_M6` (M6.18) |
| 2 | `pos` | `PosScreen` | Cart/checkout, barcode add-to-cart, hold/resume, payment | `GET products/customers/payment-methods`, `POST sales` | Yes | Partial (`InMemorySaleRepository` only, no SQLDelight-backed Sale repo) | none real yet | none this milestone | Full POS/checkout milestone (post-M6) | Legacy: shipping | N/A | `NOT_IN_M6` |
| 3 | `products` | `ProductsScreen` | Catalog list, add/edit product, stock adjust | `GET/POST/PATCH products`, `POST products/{id}/stock-adjust` | Yes | Real (`ProductRepository`) | `SqlDelightProductRepository` | full Product UI (post-M6) | Full catalog UI milestone (post-M6) | Legacy: shipping | N/A | `NOT_IN_M6` |
| 4 | `more` | `MoreScreen` | Hub linking to 11 sub-screens | none (pure nav) | No | n/a | n/a | `AppShell` secondary-destination pattern | none | Legacy: shipping | N/A | `IN_M6` (shell pattern only, M6.6) |
| 5 | `reports` | `ReportsScreen` | Period KPI dashboard + payment-method breakdown | `GET reports/summary`, `GET reports/payment-methods` | Yes | Real (`ReportingRepository`) | `SqlDelightReportingRepository` | `ReportingUiVerticalSlice` | none | Legacy: shipping | N/A | `IN_M6` (M6.18) |
| 6 | `transactions` | `TransactionsScreen` | Sales history + receipt detail | `GET sales/recent`, `GET sales/{id}` | Yes | None real yet | none | none this milestone | Full POS/Sale-history milestone (post-M6) | Legacy: shipping | N/A | `NOT_IN_M6` |
| 7 | `returns` | `ReturnsScreen` | List/process refunds | `GET/POST returns` | Yes | None (schema only, `Returns.sq`) | none | none this milestone | Returns milestone (post-M6) | Legacy: shipping | N/A | `NOT_IN_M6` |
| 8 | `customers` | `CustomersScreen` | Customer list, add, credit edit | `GET/POST/PATCH customers` | Yes | None (schema only, `Parties.sq`) | none | none this milestone | Customers milestone (post-M6) | Legacy: shipping | N/A | `NOT_IN_M6` |
| 9 | `receivables` | `ReceivablesScreen` | AR total, statement, record/void payment | `GET customers/receivables`, statement/payments endpoints | Yes | None (schema only, `Ledger.sq`) | none | none this milestone | AR milestone (post-M6) | Legacy: shipping | N/A | `NOT_IN_M6` |
| 10 | `suppliers` | `SuppliersScreen` | Supplier list, add | `GET/POST suppliers` | Yes | None (schema only, `Parties.sq`) | none | none this milestone | Suppliers milestone (post-M6) | Legacy: shipping | N/A | `NOT_IN_M6` |
| 11 | `purchase_orders` | `PurchaseOrdersScreen` | PO list, create, receive, pay | `GET/POST purchase-orders`, receive/pay endpoints | Yes | None (schema only, `Purchasing.sq`) | none | none this milestone | PO milestone (post-M6) | Legacy: shipping | N/A | `NOT_IN_M6` |
| 12 | `payables` | `PayablesScreen` | AP total, statement, record payment | `GET suppliers/payables`, statement/payments endpoints | Yes | None (schema only, `Ledger.sq`/`Payments.sq`) | none | none this milestone | AP milestone (post-M6) | Legacy: shipping | N/A | `NOT_IN_M6` |
| 13 | `cash_summary` | `DailyCashScreen` | Cash in/out/net by date | `GET reports/daily-cash` | Yes | None real yet (would extend `ReportingRepository`) | none | none this milestone | Reporting extension (post-M6) | Legacy: shipping | N/A | `NOT_IN_M6` |
| 14 | `aging` | `AgingScreen` | AR/AP aging buckets | `GET reports/aging` | Yes | None (derives from AR/AP, not yet built) | none | none this milestone | Aging milestone (post-M6, depends on AR/AP) | Legacy: shipping | N/A | `NOT_IN_M6` |
| 15 | `retail_settings` | `RetailSettingsScreen` | Language, credit policy, currency, payment methods, admin links | `GET/POST settings/credit`, `GET/POST payment-methods` | Yes | Partial (`SettingsRepository` exists, scope not cross-checked) | `SqlDelightSettingsRepository`(exists, unverified scope) | `Settings` route (shell only, M6.6/M6.7) | Full Settings UI (post-M6) | Legacy: shipping | N/A | `NOT_IN_M6` (route reserved only) |
| 16 | `backup` | `BackupRestoreScreen` | Admin-only backup create/list/restore | `POST/GET api/backup/*` | Yes | None (interface marker `BackupRepository` only, M16) | none | none this milestone | Backup/Restore milestone (M16) | Legacy: shipping | N/A | `NOT_IN_M6` |
| 17 | `licensing` | `LicensingScreen` | Activation/status/check-in/deactivate | none direct (via `LicensingCoordinator` → local `/api/licensing/*` + remote Owner) | Yes | None (interface markers `LicensingRepository`/`SessionRepository`/etc., M7-M10) | none | none this milestone | Licensing milestone (M7-M10) | Legacy: shipping | N/A | `NOT_IN_M6` |
| — | (dead, unrouted) | `SettingsScreen.kt` | Orphaned reference code, never wired to any route | none | No | n/a | n/a | n/a — legacy dead code, not ported | none | Legacy: dead code | N/A | `NOT_IN_M6` |

## Real, additional M6-only destinations (no legacy Android equivalent)

These exist only because M5.8's Import Center has no legacy-app screen
at all (the legacy app has no import feature) — M6.19 introduces them
fresh, backed entirely by real M5.8 use cases:

| Route | Purpose | Shared use case | Shared repository | M6 status |
|---|---|---|---|---|
| `ImportHome` | Import history/entry | `ImportPersistenceRepository.getProvenanceById`/audit queries | `SqlDelightImportPersistenceRepository` | `IN_M6` |
| `ImportFileSelection` | Choose source file | `PickedFileImportSource` (platform `FilePicker`) | n/a (adapter) | `IN_M6` |
| `ImportInspection` | Format/entity detection | `ImportFormatDetector`, `ImportEntityDetector` | n/a (pure logic) | `IN_M6` |
| `ImportMapping` | Column mapping review | `ImportEntityDetector.suggestMapping` | n/a | `IN_M6` |
| `ImportDryRun` | Validation/duplicate/plan summary | dry-run issuance (M5.8.13) | `SqlDelightImportPersistenceRepository` | `IN_M6` |
| `ImportCommit` | Commit confirmation/progress | `ImportCommitExecutor.commit` | `SqlDelightImportPersistenceRepository` | `IN_M6` |
| `ImportResult` | Immutable commit result | `ImportCommitResult` | n/a | `IN_M6` |

## Real, additional M6-only destinations for Category/Branch

The legacy app has no dedicated Category or Branch management screens
at all (Categories are implicit in the Products form; Branches don't
exist as a legacy concept) — M6.16/M6.17 introduce these fresh:

| Route | Purpose | Shared use case | Shared repository | M6 status |
|---|---|---|---|---|
| `Categories` | List/search/archive/reactivate | `CategoryRepository` | `SqlDelightCategoryRepository` | `IN_M6` |
| `CategoryCreate`/`CategoryEdit` | Create/edit | `CategoryRepository.insert`/domain update | `SqlDelightCategoryRepository` | `IN_M6` |
| `Branches` | List/select current/archive/reactivate | `BranchRepository` | `SqlDelightBranchRepository` | `IN_M6` |
| `BranchDetails` | Edit | `BranchRepository` | `SqlDelightBranchRepository` | `IN_M6` |

## Summary counts

- 17 real legacy Android routes audited.
- 4 real vertical slices in M6 scope (Dashboard/Reports folded into
  one Reporting slice, plus new Category/Branch/Import routes with no
  legacy equivalent): **Category, Branch, Reporting, Import Center**.
- 14 legacy routes explicitly `NOT_IN_M6` (POS, Products full UI,
  Transactions, Returns, Customers, Receivables, Suppliers, Purchase
  Orders, Payables, Cash Summary, Aging, Backup, Licensing, Settings
  full UI) — each has a real, cited later-milestone dependency, none
  silently dropped.
- 1 dead legacy file (`SettingsScreen.kt`) confirmed orphaned, not
  ported.
