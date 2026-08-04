# Aura Retail Unified Mobile — Category Domain Contract (M5.3)

## Scope

Everything the M5.1 `CategoryRepository` deliberately left undecided: duplicate-name detection (with real Arabic/Unicode normalization, not a hand-waved case-insensitive check), archive/reactivate semantics, and historical-relationship preservation. Implemented in `usecases/category/CategoryUseCases.kt`.

## Duplicate detection: real NFKC normalization, not ASCII case-folding

`platform.UnicodeTextNormalizer` (new Milestone-2-style platform contract, `PlatformContracts.kt`) trims, collapses internal whitespace, applies Unicode NFKC compatibility normalization, then lowercases. NFKC is not achievable in pure Kotlin common code (no ICU in the common stdlib) — it is a platform contract per ADR-2 (interfaces, not `expect`/`actual`), real-implemented on Android as `AndroidUnicodeTextNormalizer` using `java.text.Normalizer.normalize(value, Normalizer.Form.NFKC)` (real JDK/Android stdlib, no external dependency). The iOS implementation (Foundation's `precomposedStringWithCompatibilityMapping`) is not yet written — no `iosMain` source set exists to write it into on this Windows host (M2's own real finding), tracked as Milestone-19 scope like every other iOS platform implementation.

`CreateCategoryUseCase`/`ReactivateCategoryUseCase` normalize the candidate name and every existing **active** category's name the same way, and reject on any match — real, executed proof (`CategoryUseCasesTest`, `./gradlew :shared:testDebugUnitTest`):

- `createRejectsExactDuplicateNameCaseInsensitive` — `"Beverages"` vs `"BEVERAGES"`.
- `createRejectsFullWidthVariantAsDuplicateViaRealNfkcFolding` — `"Cola"` vs `"ＣＯＬＡ"` (full-width Latin, U+FF21-FF3A range), a real NFKC compatibility fold, not a contrived example.
- `createRejectsArabicPresentationFormVariantAsDuplicateViaRealNfkcFolding` — a name containing U+FE8D (ARABIC LETTER ALEF ISOLATED FORM) is rejected as a duplicate of the same name using the standard U+0627 codepoint — a real Unicode NFKC fold an Arabic input method could plausibly produce, proven against the real `java.text.Normalizer`, not asserted from documentation.
- `createAllowsGenuinelyDifferentNames` — proves the check is not over-broad.

## Real bug found and fixed this milestone

M4's `Catalog.sq` declared `CREATE UNIQUE INDEX categories_company_name ON categories(company_id, name)` — an unconditional, all-statuses uniqueness constraint. The first real run of `reactivateRejectedIfADuplicateWasCreatedWhileArchived` failed with `SQLITE_CONSTRAINT_UNIQUE`: the test's own second `CreateCategoryUseCase.execute(1L, "Beverages", ...)` call — deliberately made *while the first "Beverages" is archived*, to exercise the exact scenario the test name describes — was rejected by the database itself, before the use case's own (active-rows-only) dedup check ever ran. This is a genuine schema/use-case mismatch, not a test bug: the intended M5.3 UX is that an archived name becomes reusable, and the use-case layer's dedup check was already correctly written to only compare against active rows — the schema's blanket constraint was stricter than the actual business rule.

**Fix**: `categories_company_name` is now a partial unique index — `... WHERE status = 'active'` — the same partial-index pattern already used by `returns_idempotency` (`Returns.sq`). This makes the database-level constraint match the intended business rule exactly (exact-string uniqueness among active rows only), while the richer NFKC-normalized check continues to live in the use-case layer above it as the real duplicate-detection logic. The database constraint is now a defense-in-depth backstop for the exact-string case (e.g. two concurrent devices both creating "Beverages" at once), not the primary dedup mechanism.

## Archive/reactivate semantics

- **Idempotent**: archiving an already-archived category, or reactivating an already-active one, is a no-op success, not an error — matches this codebase's existing idempotent-retry conventions (M3's `InMemorySaleRepository`).
- **Reactivate re-runs dedup**: if a new category was created with the same normalized name while the original was archived, reactivation is rejected with `DuplicateName` rather than silently producing two active categories with the same name — proven by `reactivateRejectedIfADuplicateWasCreatedWhileArchived`.
- **Unknown category**: both operations return `RepositoryError.NotFound`, proven by `archiveUnknownCategoryReturnsNotFound`.

## Historical-relationship preservation

Structural, not use-case logic: `CategoryRepository.setActive` (M5.1) is a pure `UPDATE categories SET status = ?` — it never touches `products.category_id`. An archived category's existing product associations, and every already-finalized sale/return line's own `product_name_at_sale` snapshot (DIFF-03, unaffected either way since that column already denormalizes the name at sale time), remain exactly as they were.

Real, executed proof: `archivingCategoryPreservesExistingProductAssociation` creates a category and a product referencing it, archives the category, then confirms `ProductRepository.getById` still resolves both `categoryId` (unchanged FK) and `categoryName` (the `LEFT JOIN` in `selectProductById` is not status-filtered, so an archived category's name still resolves).

## Real, executed evidence

`./gradlew :shared:testDebugUnitTest`: 102/102 (92 M5.1 baseline + 10 new `CategoryUseCasesTest` cases). `./gradlew :androidApp:assembleDebug`: BUILD SUCCESSFUL.
