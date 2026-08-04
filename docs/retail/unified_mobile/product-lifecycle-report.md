# Aura Retail Unified Mobile — Product Lifecycle Execution Report (M5.5.17)

Real, executed evidence for `usecases/product/ProductUseCases.kt`'s lifecycle operations, complementing `product-domain-contract.md`'s design decisions with a straight test-to-behavior mapping. All 22 `ProductUseCasesTest` cases plus the 4 `CreateProductWithInitialStockUseCaseTest` cases pass (`./gradlew :shared:testDebugUnitTest`).

| Lifecycle behavior | Test | Result |
|---|---|---|
| Create succeeds with valid input | `createValidProductSucceeds` | PASS |
| Arabic name preserved exactly | `createAcceptsArabicNameAndPreservesItExactly` | PASS |
| Name whitespace trimmed | `createTrimsLeadingAndTrailingWhitespaceFromName` | PASS |
| Blank name rejected | `createRejectsBlankName` | PASS |
| Negative cost/sell price rejected | `createRejectsNegativeCostAndSellPrice` | PASS |
| Active-category assignment succeeds | `createWithActiveCategorySucceeds` | PASS |
| Archived-category assignment rejected | `createRejectsArchivedCategoryAssignment` | PASS |
| Cross-business category assignment rejected | `createRejectsCrossBusinessCategoryAssignment` | PASS |
| Duplicate SKU rejected (case-insensitive) | `createRejectsDuplicateSkuCaseInsensitive` | PASS |
| Same SKU allowed across different companies | `createAllowsSameSkuInDifferentCompanies` | PASS |
| Duplicate barcode rejected | `createRejectsDuplicateBarcode` | PASS |
| Leading-zero barcode preserved exactly | `createPreservesLeadingZeroBarcodeExactly` | PASS |
| Long barcode preserved exactly | `createPreservesLongBarcode` | PASS |
| Null barcode allowed; multiple nulls don't conflict | `createAllowsNullBarcodeAndMultipleNullsDoNotConflict` | PASS |
| Blank-but-present barcode rejected | `createRejectsBlankButPresentBarcode` | PASS |
| SKU cannot be reused after archiving (LEGACY_PARITY) | `skuCannotBeReusedAfterArchiving` | PASS |
| Archive → reactivate round trip | `archiveThenReactivateRoundTrip` | PASS |
| Archiving category doesn't archive its products | `archivedProductCannotReceiveNewCategoryAssignmentButKeepsExistingOne` | PASS |
| Stale update rejected (optimistic concurrency) | `staleUpdateIsRejected` | PASS |
| Update can't steal another product's barcode | `updateDoesNotAllowChangingBarcodeToAnotherProductsBarcode` | PASS |
| Prefix search, incl. full-width NFKC fold | `searchFindsByNormalizedPrefixIncludingFullWidthVariant` | PASS |
| Archived product excluded from barcode scan | `findByBarcodeOnlyResolvesActiveProducts` | PASS |
| Product + opening stock is one atomic transaction | `createWithInitialStockPersistsProductAndOpeningStockTogether` | PASS |
| Zero initial stock creates no movement row (LEGACY_PARITY) | `zeroInitialStockCreatesProductButNoMovementRow` | PASS |
| Rejected creation leaves no product row (real atomicity) | `archivedBranchRejectsCreationAndPersistsNoProductEither` | PASS |
| Idempotent retry returns original, no duplicate | `retryWithSameIdempotencyKeyReturnsOriginalProductNotADuplicate` | PASS |

## No hard deletion anywhere

Confirmed by inspection, not a runtime-rejection test (there is no delete method to call): `ProductRepository`/`ProductUseCases.kt` expose no delete operation at all — only `setActive(..., false)` (archive). This is a structural guarantee, the same style M3's `FinancialSecurityTest` used for "structurally impossible, not merely rejected" cases (`intentional-financial-differences.md`).

## Historical snapshot immutability

Also structural, not a runtime test: no file under `usecases/product/` or `data/sqldelight/SqlDelightProductRepository.kt` writes to `sale_items`/`return_items` (`Sales.sq`/`Returns.sq`) at all — `product_name_at_sale`/`unit_price`/`tax_rate` on those tables are only ever written once, at sale/return finalization time (M3/M4), and the Product layer added in M5.5 has no code path that could touch them.

## Real, executed evidence

`./gradlew :shared:testDebugUnitTest`: `ProductUseCasesTest` 22/22, `CreateProductWithInitialStockUseCaseTest` 4/4 — both part of the full 169/169 suite.
