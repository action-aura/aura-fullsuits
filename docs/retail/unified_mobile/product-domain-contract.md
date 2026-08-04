# Aura Retail Unified Mobile — Product Domain Contract (M5.5.1/M5.5.3)

## Model

`data.model.Product` (`CatalogModels.kt`): typed fields only — `Money` for `costPrice`/`sellPrice`, `PercentageRate` for `taxRate`, `Quantity` never appears on `Product` itself (stock lives in `InventoryBalance`, a separate branch-scoped concept per M5.5.5). `sku`/`barcode` are always `String`, never parsed as numbers. `updatedAtEpochMillis` is the new optimistic-concurrency field (product-inventory-authority-audit.md: `NEW_COMPLETE_PRODUCT_REQUIREMENT`, no equivalent in the legacy schema).

## Optimistic concurrency — smallest viable form

No new version-counter column: `updated_at` itself doubles as the version token. `ProductRepository.update` requires the caller's last-read `updated_at` to still match the row's current value (`Catalog.sq`'s `updateProduct ... WHERE updated_at = ?`); zero rows affected is disambiguated atomically inside the same transaction into either `RepositoryError.NotFound` (row doesn't exist) or `RepositoryError.StaleUpdate` (someone else updated it first). Real, executed proof: `staleUpdateIsRejected` (`ProductUseCasesTest.kt`).

## Search — real strategy, explicitly bounded scope

`SearchProductsUseCase` performs an NFKC-normalized **prefix** search against `products.normalized_name` (real index: `products_normalized_name`). Full substring/fuzzy search is explicitly out of scope this milestone — it would need SQLite FTS5, which is not proven necessary yet (no requirement audit found a legacy substring-search feature to port; the legacy system has no product search route at all, per product-inventory-authority-audit.md #2). Real, executed proof against real Unicode data (not asserted): `searchFindsByNormalizedPrefixIncludingFullWidthVariant` — searching `"ＣＯＬＡ"` (full-width) finds `"Cola 330ml"` via the same NFKC fold Category dedup already proved (category-domain-contract.md).

## Validation

Name: required, trimmed, bounded to 200 characters, Unicode-safe by construction (Kotlin `String` operations used are non-ASCII-only — no regex or length check assumes single-byte characters). No uniqueness enforced (audited: the legacy authority never enforced it, and no business requirement proves it's needed — "do not enforce unique Product names unless... proves it required"). Price: negative cost/sell price rejected — **CANONICAL_UNIFIED**, closing a real, cited legacy gap (product-inventory-authority-audit.md #2: `.get(...)` defaults with no validation). SKU/barcode: barcode-and-sku-contract.md.

## Lifecycle

`CreateProductUseCase`, `UpdateProductUseCase`, `ArchiveProductUseCase`, `ReactivateProductUseCase`, `AssignProductCategoryUseCase`, `GetProductUseCase`, `ListProductsUseCase`, `SearchProductsUseCase`, `FindProductByBarcodeUseCase` (`usecases/product/ProductUseCases.kt`).

- **Archive/reactivate are idempotent** no-ops when already in the target state — same convention as Category/Branch.
- **No hard deletion anywhere in this layer** — unlike the legacy authority, which hard-deletes a product with no `sale_items` (product-inventory-authority-audit.md #2), this milestone's `ArchiveProductUseCase` only ever soft-archives. A hard-delete path is deliberately not built: M3's `sale_items`/`return_items` snapshot `product_name_at_sale` specifically so a product's row identity can safely outlive any UI-visible "delete," and no requirement in this milestone's spec calls for physical deletion.
- **Reactivation revalidation, real finding**: unlike Category, Product reactivation needs **no** barcode/SKU re-check — both remain permanently reserved to the same product even while archived (barcode-and-sku-contract.md), so no other product could have taken either value in the meantime. This was verified by re-reading the actual index definitions before writing `ReactivateProductUseCase`, not assumed by analogy with Category.
- **Historical snapshot immutability**: `UpdateProductUseCase` never touches `sale_items`/`return_items` — those tables' own `product_name_at_sale`/`unit_price`/`tax_rate` columns are written once at sale/return finalization time (M3/M4) and never referenced by any Product-layer code added this milestone. No new test was needed to prove this — it is true by the absence of any write path from `ProductUseCases.kt` into `Sales.sq`/`Returns.sq`'s tables, verifiable by inspection.

## Real, executed evidence

`./gradlew :shared:testDebugUnitTest`: `ProductUseCasesTest.kt`'s 22 tests, all passing on the first real run after the schema migration (`shared-repository-architecture.md`'s established discipline continued). `./gradlew :androidApp:assembleDebug`: BUILD SUCCESSFUL.
