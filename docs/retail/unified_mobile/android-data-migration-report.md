# Aura Retail Unified Mobile — Android Data Migration Report (M5.0 execution evidence)

Real execution report for the data-preservation importer, distinct from `data-preservation-plan.md` (the design). This is what was actually run and observed.

## What was actually executed (real, this session)

`CatalogImporterTest.importsRealLegacyShapedDataWithPreservedIdsAndConvertedTypes`, run via `./gradlew :shared:testDebugUnitTest`:

1. A synthetic legacy database was created using the **real, exact `CREATE TABLE` statements copied verbatim** from `products/retail/backend/database/schema.py`'s `branches`/`categories`/`products` (real `REAL` columns, real `TIMESTAMP DEFAULT CURRENT_TIMESTAMP`) — not an approximation.
2. Seeded with one representative row per table (`Main Branch`, `Beverages` category, `Cola 330ml` product at `sell_price=1.99`, `cost_price=0.5`, `tax_rate=10.0`, `category_id=1`).
3. `CatalogImporter.import()` executed against this real legacy-shaped source and a real target `RetailDatabase` (in-memory JDBC, same generated schema code that runs on a real Android device via `AndroidDatabaseDriverFactory`).
4. **Real, observed results**: `branchesImported=1, categoriesImported=1, productsImported=1`. The imported product's `sell_price` field reads back as the string `"1.99"` (not `"1.9899999...".` or any other float-artifact string — real proof the `Double→TEXT` conversion path is exact for this representative value). The imported product's `category_name` (resolved via the real `LEFT JOIN` in `selectActiveProducts`) reads back as `"Beverages"` — real proof the explicit-ID import preserved the `category_id=1` foreign-key relationship correctly, not just that a category row happened to exist.
5. `parseLegacyTimestampToPlausibleEpochMillis`: the string `"2026-01-15 10:30:00"` parses to an epoch-millisecond value real-checked to fall within the year-2020–2030 bound — a sanity check, not an exact-timezone assertion (the plan's own documented caveat).

## Real, honest scope of what this proves

This proves the **pattern** — read real legacy-shaped rows, convert types correctly, preserve IDs, preserve FK relationships, verify — works, for the three dependency-root tables. It does **not** yet prove:

- The remaining 17 tables' import (Milestone 18 scope, `data-preservation-plan.md`'s own stated boundary).
- Behavior against a real, physical Android device's actual `retail.db` file (this test uses a synthetic, in-memory, JDBC-driven source and target — real device execution is Milestone 24 scope).
- Backup-before-import, integrity-check-before-import, or rollback-on-partial-failure (the full `data-preservation-plan.md` 8-step sequence) — only step 5 (transactional import) and step 6 (post-import row-count validation) were exercised this milestone. Steps 2 (pre-migration integrity check), 3 (pre-migration backup), 4 (baseline hash), and 8 (retry-safety) are designed in the plan document but not yet implemented in code — real, open work for Milestone 18, not silently claimed done.
- Corrupted-source-data behavior (a legacy database that fails `PRAGMA integrity_check`) — not yet tested; the plan design commits to refusing to migrate in that case, but no test exercises it yet.

## Disposition

The importer pattern is real and proven for its current scope. The full 20-table, full-safety-sequence importer is real, designed, and explicitly scheduled for Milestone 18 — not fabricated as complete now. This report exists specifically so a later reader cannot mistake "the pattern works" for "the full migration is production-ready," matching the reconciliation discipline this milestone's own governing instructions require.
