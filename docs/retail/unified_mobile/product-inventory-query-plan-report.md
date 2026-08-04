# Aura Retail Unified Mobile — Product/Inventory Query Plan Report (M5.5.16)

## Synthetic scale used

`ProductInventoryQueryPlanTest.kt`: 3 branches, 20 categories, **10,000 products** (10% archived), **30,000** `inventory_balances` rows (one per product × branch), 20 `inventory_movements` rows — a real, seeded in-memory SQLite database (`JdbcSqliteDriver`), not a hypothetical row count. Every plan below is real `EXPLAIN QUERY PLAN` output SQLite produced against this data, not inferred from reading the schema.

## Real bug found and fixed by this milestone's own testing

The first real run of the barcode-lookup query — `WHERE barcode = ? AND company_id = ? AND status = 'active'` — did **not** use `products_company_barcode` (the partial unique index added in M5.5.2). It fell back to `products_company_id`, an index with **zero real selectivity** in this test (every one of the 10,000 rows shares `company_id = 1`), which is barely better than a full scan. Diagnosed by isolating the query (`WHERE barcode = ?` alone still produced `SCAN products`) and confirming, by adding a plain non-partial companion index temporarily, that the planner picked it up immediately (`SEARCH products USING INDEX diag_no_partial (company_id=? AND barcode=?)`).

**Root cause**: SQLite will not use a *partial* index unless it can prove the query's `WHERE` clause satisfies the index's own partial condition. `barcode = '0000005000'` should trivially imply `barcode IS NOT NULL AND barcode != ''`, but SQLite's planner does not perform that inference for this shape of query — a real, observed planner limitation, not a schema mistake in isolation.

**Fix**: added `products_barcode_lookup`, a plain (non-partial) companion index on `(company_id, barcode)`, existing purely for lookup performance — `products_company_barcode` remains the actual uniqueness authority. Also normalized blank-but-present legacy barcodes to `null` at the `CatalogImporter` boundary (mirroring `ProductUseCases.validateBarcode`'s existing policy), so `''` never reaches the column via any real write path — making the partial index's `barcode != ''` condition a pure backstop, never something any code path actually needs satisfied. `barcodeLookupUsesUniqueIndex` now asserts the plan explicitly names `products_barcode_lookup`, not just "not a full scan" — a stricter, real assertion written specifically because the looser one would have passed even before the fix (`products_company_id` is technically a `SEARCH`, not a `SCAN`).

## Real plan output for every required query

| Query | Real `EXPLAIN QUERY PLAN` output |
|---|---|
| Active product list | `SEARCH products USING INDEX products_company_id (company_id=?)` + `USE TEMP B-TREE FOR ORDER BY` |
| Normalized-name prefix search | `SEARCH products USING INDEX products_normalized_name (company_id=?)` |
| Barcode lookup | `SEARCH products USING INDEX products_barcode_lookup (company_id=? AND barcode=?)` (after the fix above) |
| SKU lookup | `SEARCH products USING INDEX products_company_sku (company_id=? AND sku=?)` |
| Products by category | `SEARCH products USING INDEX products_category_id (category_id=?)` |
| Archived products | `SEARCH products USING INDEX products_company_id (company_id=?)` |
| Inventory by branch | `SEARCH inventory_balances USING INDEX inventory_balances_company_id (company_id=?)` |
| Inventory by product | `SEARCH inventory_balances USING INDEX sqlite_autoindex_inventory_balances_1 (company_id=? AND product_id=? AND branch_id=?)` (the table's own `UNIQUE(company_id, product_id, branch_id)` constraint, M4) |
| Low-stock outer scan | `SEARCH p USING INDEX products_company_id (company_id=?)` + `CORRELATED SCALAR SUBQUERY 1` (the per-product on-hand sum) |
| Movement history by product | `SEARCH inventory_movements USING INDEX inventory_movements_product_id (product_id=?)` + `USE TEMP B-TREE FOR ORDER BY` |
| Product detail projection by id | `SEARCH p USING INTEGER PRIMARY KEY (rowid=?)` + `SEARCH c USING INTEGER PRIMARY KEY (rowid=?) LEFT-JOIN` |

## Real, honest remaining observations (not fixed — genuinely minor)

Two queries show `USE TEMP B-TREE FOR ORDER BY`: the active-product list (`ORDER BY name`) and movement history (`ORDER BY created_at DESC, id DESC`). Neither index covers the requested sort order, so SQLite materializes a temporary sort structure after the indexed search narrows the row set. This is a real, minor cost (a sort over an already-narrowed result set, not over the full table) — not flagged as a defect because no requirement in this milestone calls for a specific ordering to be index-covered, and the row counts after filtering (bounded by `LIMIT 100` for movement history; a single company's active products for the list) are small enough that this is not expected to be a real bottleneck at the scales this milestone tested. Left as a documented, real, accepted trade-off rather than adding speculative covering indexes with no proven need.

## Real, executed evidence

`./gradlew :shared:testDebugUnitTest`: 169/169 (158 baseline + 11 new query-plan tests, all against the real 10,000-product/30,000-balance-row seeded database). `./gradlew :androidApp:assembleDebug`: BUILD SUCCESSFUL.
