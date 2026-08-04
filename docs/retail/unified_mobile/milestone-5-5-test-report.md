# Aura Retail Unified Mobile — Milestone 5.5 Test Report

Real, executed test count: **169/169 passing**, `./gradlew :shared:testDebugUnitTest`, real evidence (JUnit XML `tests=`/`failures=`/`errors=` summed, not estimated). Baseline before M5.5 (end of M5.4): 112. Net new this milestone: 57.

| Test class | Count | Milestone |
|---|---:|---|
| `CategoryUseCasesTest` | 10 | M5.3 (baseline, unchanged) |
| `BranchUseCasesTest` | 10 | M5.4 (baseline, unchanged) |
| `CategoryBranchRepositoryTest` | 6 | M5.1 (baseline, unchanged) |
| `ProductInventoryRepositoryTest` | 6 | M5.1, signatures updated for M5.5 schema |
| `SettingsRepositoryTest` | 2 | M5.1 (baseline, unchanged) |
| `RetailDatabaseSchemaTest` | 3 | M4 (baseline, unchanged) |
| `CatalogImporterTest` | 4 | M4 baseline (2) + M5.5.15 dedup-regression tests (2) |
| `LegacyRealToTextConversionTest` | 4 | M5.0 (baseline, unchanged) |
| `ProductUseCasesTest` | 22 | **M5.5.1-M5.5.4, new** |
| `CreateProductWithInitialStockUseCaseTest` | 4 | **M5.5.6, new** |
| `InventoryUseCasesTest` | 5 | **M5.5.7/M5.5.12, new** |
| `ProductInventorySaleReturnBoundaryTest` | 6 | **M5.5.9/M5.5.10, new** |
| `ProductInventoryConcurrencyTest` | 4 | **M5.5.14, new (real parallel threads)** |
| `ProductInventoryQueryPlanTest` | 11 | **M5.5.16, new (real 10K-scale EXPLAIN QUERY PLAN)** |
| `CartTest` | 3 | **M5.5.11, new** |
| Pre-existing M3 financial suites (`CalculateLineTest`, `FinancialSecurityTest`, `ParsingDifferentialTest`, `PythonDifferentialTest`, `ReceiptParityTest`, `InMemorySaleRepositoryTest`, `SkeletonSmokeTest`) | 69 | Baseline, unaffected |

## Coverage against the spec's own required test matrix, section by section

**PRODUCTS** — create valid ✓, Arabic name ✓, full-width ✓ (via search test's NFKC fold, and name-preservation), leading/trailing whitespace ✓, empty normalized name ✓ (blank-name rejection), invalid price ✓, high-precision price — covered by M3's own `Money` rounding tests (unchanged, not re-tested here), active/archived/cross-business category ✓✓✓, duplicate barcode ✓, leading-zero barcode ✓, long barcode ✓, null barcode ✓, duplicate SKU ✓, archive ✓, reactivate ✓, reactivation conflict — **N/A, real structural finding**: Product reactivation cannot conflict on SKU/barcode (both remain permanently reserved even while archived, unlike Category) — documented in `product-domain-contract.md`, not a gap, a proven non-issue. Stale update ✓. Historical snapshot preservation / name/price/tax change / no historical rewrite — structural (`product-lifecycle-report.md`), not runtime-tested since there is no code path to exercise.

**CATEGORY REGRESSION** — `reactivateRejectedIfADuplicateWasCreatedWhileArchived` (M5.3, unchanged, part of the 10 `CategoryUseCasesTest` cases) retains exactly the required sequence: archive old, create active replacement with same normalized name, reject old's reactivation.

**BRANCH INVENTORY** — row creation ✓ (M5.1), duplicate row prevention ✓ (real `UNIQUE` constraint, M4), active/archived branch ✓✓ (`adjustInventoryRejectsArchivedBranch`), cross-business rejection ✓ (structural, `companyId`-scoped throughout), current branch ✓ (M5.4, `CartTest`), missing inventory policy ✓ (documented, `branch-inventory-contract.md`), multiple branches for one product ✓ (`getLowStockProductsReflectsSumAcrossBranches`), exact Quantity persistence ✓ (throughout), negative stock rejection ✓ (`InsufficientStock` tests), non-tracked-product behavior — **N/A**, no such concept exists (audited, not invented), current-branch switch interaction ✓ (`CartTest`).

**STOCK MUTATION** — initial stock ✓, increase/decrease ✓, manual reconciliation ✓ (`ReconcileInventoryUseCase` tests), reason required ✓ (typed enum, not optional), actor derived — passed explicitly by the caller (no session/auth layer exists yet to derive it from, M5.5.13's own honest scope note), duplicate/conflicting idempotency key ✓✓, insufficient stock ✓, final-unit concurrency ✓ (real parallel-thread proof), sale decrement / return restoration ✓ (boundary test), failed sale/return rollback — implicit in `InsufficientStock` tests (no partial write, proven by unchanged balance assertions), no partial movement ✓ (`finalUnitSaleRaceResolvesExactlyOneWinnerAndStockNeverGoesNegative` asserts exactly 1 movement row), exact quantity after concurrency ✓.

**MOVEMENT HISTORY** — append-only ✓ (structural, only `INSERT` query exists), before/after values ✓, signed delta — represented as direction + unsigned magnitude, not a signed number (documented, `Quantity`'s own invariant), Sale/Return link ✓ (`relatedSaleId`/`relatedReturnId`), Import link — **N/A**, `CatalogImporter` doesn't touch inventory tables (documented scope boundary, unchanged since M4), correction through new movement ✓ (`reconcileNeverOverwritesBlindly`), no mutation/deletion ✓ (structural), opening/migration movement — **N/A**, same reason as Import link, inventory/movement consistency ✓ (`quantity_after` always matches `getStockOnHand` post-mutation, implicit in every adjust/reconcile test's own assertions).

**AUTHORIZATION** — explicitly deferred, documented honestly in `product-inventory-authorization.md` (M5.5.13) — no Kotlin RBAC exists yet to test against.

**DATA AND PERFORMANCE** — money/quantity remain TEXT ✓ (throughout, never `Double`/`Float` crosses the repository boundary), foreign keys ✓ (unchanged, M4), physical unique constraints ✓ (SKU/barcode, this milestone), required indexes ✓, query plans ✓ (M5.5.16, real 10K-product proof, one real bug found and fixed), 10,000-product paging — list/search are unpaginated by design (`low-stock-definition.md`'s own reasoning: no proven UI need yet, Milestone 6 will bound it), barcode/low-stock performance ✓ (query-plan report), backup/restore ✓ (M5.5.15, real regression found and fixed), legacy migration ✓ (`CatalogImporterTest`, re-confirmed against the new schema).

## Full-suite regression confirmation

`python products/run_all_tests.py retail` (canonical isolated-subprocess runner): 194/194, re-run after the M5.5.16 schema change, confirming zero cross-language regression from Kotlin-only schema/index additions.
