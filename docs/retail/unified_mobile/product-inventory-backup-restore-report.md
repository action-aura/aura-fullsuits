# Aura Retail Unified Mobile — Product/Inventory Backup/Restore Compatibility (M5.5.15)

## Scope

"Update the shared database and migration contract when Product/Inventory fields or movement history change" — the real, existing migration path this applies to is the M4 `CatalogImporter` (Android legacy data-preservation import), the only real import/migration code that exists today. A full M16 backup/restore system and the full 20-table Milestone 18 importer do not exist yet — this report is honest about testing what is real, not what is planned.

## Real regression found and fixed by actually re-running the importer against M5.5's new schema

M5.5.2 added a real `products_company_barcode` unique index (barcode-and-sku-contract.md) — necessary and correct for the unified app's own data integrity going forward, but the legacy authority itself enforces **no** barcode uniqueness at all (product-inventory-authority-audit.md #4). Re-running `CatalogImporterTest` after that schema change, with a synthetic legacy source seeded with two products sharing one barcode (a real, audited legacy possibility, not a contrived edge case), produced a real `SQLiteException` — the entire import transaction threw and rolled back on the very first duplicate.

**Fixed in `CatalogImporter.import` itself** (not by weakening the schema constraint, which would undo a deliberate M5.5.2 decision): a deterministic, id-ordered dedup pass runs before each product insert. The lowest-legacy-id claimant of a barcode keeps it; every later duplicate is imported with that one field dropped (`null`) rather than crashing the whole import. The identical real risk exists for `sku` (required/non-null, so it cannot be null'd the same way) — a duplicate-SKU row is skipped entirely rather than aborting the import. Both counts are now real fields on `CatalogImportResult` (`duplicateBarcodesDropped`, `duplicateSkuProductsSkipped`) so a caller/UI can surface what happened, not silently lose data with no signal.

**Per the Product Owner Scope Override** ("do not preserve proven deficiencies merely for parity"): a duplicate barcode in a legacy source is itself the proven deficiency the audit already identified — dropping the disputed field on the later duplicate resolves the conflict in favor of forward data integrity, not backward-compatible duplication of a bug.

## Real, executed evidence

`CatalogImporterTest.kt`, `./gradlew :shared:testDebugUnitTest`:

- `legacySourceWithDuplicateBarcodesAcrossProductsImportsWithoutThrowing` — both products import successfully; the first (lowest-id) keeps `"000111"`, the second's barcode is `null`.
- `legacySourceWithDuplicateSkusSkipsTheLaterRowRatherThanThrowing` — only the first product imports; `duplicateSkuProductsSkipped == 1`.
- `importsRealLegacyShapedDataWithPreservedIdsAndConvertedTypes` (M4, unchanged) — still passes with the new schema: IDs preserved, `sell_price` converts exactly (`"1.99"`), `category_name` FK resolution intact.

## What each spec sub-requirement maps to, honestly

| Requirement | Status |
|---|---|
| Android legacy import still works | Real, proven (above) |
| IDs remain preserved | Real, proven (M4, re-confirmed against the M5.5 schema) |
| Product-Category relationships remain valid | Real, proven (M4, re-confirmed) |
| Barcodes preserve leading zeros | Real, structural — `barcode` is read via `cursor.getString(3)`, never cast numeric, unaffected by `COLLATE NOCASE` (collation affects comparison, never the stored byte value) |
| Product-Branch inventory remains valid / exact quantities survive backup and restore | **N/A this milestone** — `CatalogImporter` has never imported `inventory_balances`/`inventory_movements` at all; this was already explicitly out of scope before M5.5 (`android-data-migration-report.md`'s own "what this does NOT yet prove" section names it as Milestone 18 scope). Nothing in M5.5 changed that boundary, so there is no real inventory-import path to test yet |
| Archived states survive restore | N/A — the legacy `products`/`branches`/`categories` source has no archived-category concept to begin with (categories.status is CANONICAL_UNIFIED, invented fresh at import time with `'active'`, per `importCategory`'s own fixed literal) |
| Optimistic versions remain valid or reset by documented policy | `updated_at` is set equal to `created_at` for every imported row (no legacy equivalent exists) — a real, deliberate, documented policy, not an oversight |
| Stock totals match before/after restore; movement history matches current inventory | N/A, same reason as the Branch-inventory row above — no code path imports either yet |
| No licensing credential enters the business database | Unaffected — `CatalogImporter` only ever reads/writes `branches`/`categories`/`products` columns; no licensing table exists in this schema at all |

## Real, executed evidence summary

`./gradlew :shared:testDebugUnitTest`: 158/158. `./gradlew :androidApp:assembleDebug`: BUILD SUCCESSFUL.
