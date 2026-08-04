# Aura Retail Unified Mobile — Barcode and SKU Contract (M5.5.2)

## The real cross-layer inconsistency this contract resolves

`product-inventory-authority-audit.md` #4 found the legacy product to already be internally inconsistent: the Python backend's SKU uniqueness check is case-sensitive (`retail_api.py:254`, plain `=`, no `COLLATE NOCASE`), while the existing Android app's own client-side barcode/SKU scan matching (`ProductLookup.kt:11-15`) is case-**insensitive**. Neither half is "the" legacy authority to faithfully port — porting either alone would either break the already-shipped Android scan UX or introduce a new case-sensitive-vs-insensitive surprise. **Resolved, deliberately, as CANONICAL_UNIFIED**: both `sku` and `barcode` columns are declared `COLLATE NOCASE` in the unified schema (`Catalog.sq`), making storage-level uniqueness AND lookup comparison consistently case-insensitive everywhere. The stored byte value itself is never case-folded — only comparison and uniqueness enforcement are.

## SKU

- Required, non-blank after trim; internal whitespace runs collapsed to one space (`validateSku`, `ProductUseCases.kt`).
- Uniqueness: unconditional across **all** statuses, company-scoped (`products_company_sku`) — **LEGACY_PARITY**, not an invented rule: the real legacy check (`retail_api.py:253-257`) already ignores `status` when checking for an existing SKU, so a SKU is permanently reserved company-wide once used, even after the product is soft-deleted. Proven by `skuCannotBeReusedAfterArchiving` (below).
- Immutable after creation — matches the legacy authority exactly (`sku` is not in `update_product`'s patchable whitelist, `retail_api.py:295`); `UpdateProductUseCase`/`ProductRepository.update` structurally cannot change it (no `sku` parameter).
- User-visible original casing is always preserved in storage; only comparison is case-insensitive.

## Barcode

- Optional (`null` = no barcode). A **present-but-blank** string (after trim) is rejected — `validateBarcode` treats `null` and `""` as semantically different: omit the field entirely for "no barcode," never send blank.
- Trimmed only — no internal whitespace collapsing, no reformatting, no attempt to convert between barcode symbologies ("do not normalize two distinct valid barcode symbologies into one value" — this contract performs no symbology-aware transformation at all, only whitespace trimming).
- Rejects control characters (any codepoint < `0x20`).
- Never parsed as a number anywhere in the model, repository, or use-case layer — always `String`. Leading zeros, long codes, and alphabetic formats survive byte-for-byte (proven by `createPreservesLeadingZeroBarcodeExactly`, `createPreservesLongBarcode`).
- Uniqueness: `products_company_barcode`, a **partial** unique index — `NULL`/`''` are exempt (never conflict with each other or anything else — "do not make multiple null values conflict"), but this is **unconditional across statuses**, unlike SKU's own equally-unconditional rule stated for a different reason: the spec's own explicit instruction — "do not allow an archived Product barcode to be silently reused" — is what makes this the correct policy here, not a coincidence of matching SKU's rule. **NEW_COMPLETE_PRODUCT_REQUIREMENT**: the legacy authority enforces no barcode uniqueness at all (#2/#4).

## Real, executed evidence

`ProductUseCasesTest.kt` (`./gradlew :shared:testDebugUnitTest`): `createRejectsDuplicateSkuCaseInsensitive`, `createAllowsSameSkuInDifferentCompanies`, `createRejectsDuplicateBarcode`, `createPreservesLeadingZeroBarcodeExactly`, `createPreservesLongBarcode`, `createAllowsNullBarcodeAndMultipleNullsDoNotConflict`, `createRejectsBlankButPresentBarcode`, `updateDoesNotAllowChangingBarcodeToAnotherProductsBarcode`.

## Real concurrency mechanism (not an app-level pre-check)

Unlike the legacy authority's own race-prone `SELECT`-then-`INSERT` (#2/#5), duplicate detection happens **inside the same transaction** as the write, in `SqlDelightProductRepository.insert`/`update` — the same TOCTOU discipline `BranchRepository.setActive` already established in M5.1. SQLite's single-writer transaction serialization is the actual backstop proving "exactly one wins" under concurrent creation; real concurrent-coroutine proof is in `stock-concurrency-report.md`.
