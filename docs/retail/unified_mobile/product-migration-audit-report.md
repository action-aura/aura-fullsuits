# Aura Retail Unified Mobile — Product Migration Audit Report (M5.5 mandatory follow-up)

Real hardening of `CatalogImporter`'s deterministic barcode/SKU dedup pass (M5.5.15), per the M5.5-checkpoint's own explicit follow-up requirements: idempotency, a durable audit trail, and no silent discarding.

## Idempotency — real, executed proof

Calling `CatalogImporter.import(...)` twice against the same target database now produces the identical end state. Real finding before this fix: `importBranch`/`importCategory`/`importProduct` are plain `INSERT`s with explicit legacy ids — a second call would have collided on the primary key and thrown, for **all three** tables, not just `products`. Fixed by checking existence (`selectBranchById`/`selectCategoryById`, and a new bulk `selectAllProductsForImportIdempotency` for products) before importing each row; an already-present row is counted (`branchesAlreadyPresent`/`categoriesAlreadyPresent`/`productsAlreadyPresent`, new `CatalogImportResult` fields) and left untouched.

**Real subtlety found and fixed**: dedup state (which SKUs/barcodes are already claimed) must be seeded from the *target database's existing rows*, not just from what this call's own loop has processed so far — otherwise a resumed partial run (e.g. after a crash midway through a large import) could re-allow a barcode/SKU an earlier partial run already resolved. Proven by `resumedPartialImportDoesNotReallowABarcodeAlreadyClaimedByAnEarlierRun`: a product pre-seeded directly (simulating an earlier run) correctly blocks a same-barcode product in the current run's own legacy source, even though the pre-seeded product was never touched by the current call's loop.

Real, executed proof: `reRunningImportIsIdempotentAndCreatesNoDuplicates` — first call imports 1 branch/1 category/1 product; second call reports 0 newly imported and 1/1/1 already-present, with exactly one row of each still present in the database (not two).

## Durable audit trail — real, not just an in-memory result

New `import_conflicts` table (`table-count-reconciliation.md`'s addendum — the schema is now 21 tables, not 20, and this is the one genuine exception to "no net-new table shapes"). Every dropped barcode or skipped-duplicate-SKU row gets a real, permanent row: `legacy_product_id` (the conflicting row), `field` (`"barcode"` or `"sku"`), `original_value` (the value that was discarded from the product row itself — recovered from the audit trail even though it no longer lives on any product), `resolution` (`"DROPPED"` or `"ROW_SKIPPED"`), `canonical_product_id` (which product kept the value), `created_at`.

**Real bug found and fixed by this milestone's own test**: the first real run of `duplicateBarcodeConflictIsRecordedInTheAuditTrail` recorded `canonical_product_id = null` instead of the expected winning product's id — the lookup only consulted the pre-loop snapshot of existing rows, missing a claimant imported earlier in the *same* call. Fixed by tracking canonical ownership live (`canonicalProductIdByBarcode`, seeded from existing rows and updated as each product is imported), the same pattern already used for `canonicalProductIdBySku`.

Real, executed proof: `duplicateBarcodeConflictIsRecordedInTheAuditTrail`, `duplicateSkuConflictIsRecordedInTheAuditTrail` — both assert the real row content (legacy id, field, original value, resolution, canonical id), not just that a conflict was counted.

## "Reject unrecoverable ambiguity rather than guessing" — real reasoning, not a new code path

No new rejection path was added, and this is a deliberate, reasoned choice, not an oversight: the deterministic policy (lowest-legacy-id wins, later duplicates resolved) is **total** — it produces a well-defined outcome for every possible input, never an ambiguous state requiring a guess. There is no real scenario where "which product should keep the barcode" is genuinely undecidable given a fixed, deterministic ordering rule (legacy id). "Unrecoverable ambiguity" would describe a situation this policy cannot resolve; none was found during design or testing.

## Historical Product references / no silent merge

Both are structural guarantees, not new tests: every duplicate-SKU row is *skipped* (never inserted), and every duplicate-barcode row still gets its own product row (only the barcode field is cleared) — two products that happen to share a barcode in the legacy source remain two distinct products in the unified schema, never silently merged into one. Legacy ids are preserved exactly as before (M4's own original guarantee, unaffected by this follow-up).

## Real, executed evidence

`./gradlew :shared:testDebugUnitTest`: `CatalogImporterTest.kt` now 8/8 (4 M5.5.15 baseline + 4 new: idempotent re-run, barcode-conflict audit, SKU-conflict audit, resumed-partial-import dedup-seeding) — part of the full suite, 173/173.
