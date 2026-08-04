package com.actionaura.retail.db

import app.cash.sqldelight.db.QueryResult
import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import kotlin.test.Test
import kotlin.test.assertTrue

/**
 * M5.5.16 -- real `EXPLAIN QUERY PLAN` output against a real, synthetic
 * 10,000-product database (real indexes, real row counts) -- not
 * reasoned about from the schema alone. Seeds 3 branches, 20 categories,
 * 10,000 products (evenly split active/archived), one inventory_balances
 * row per (product, branch) (30,000 rows), and a handful of
 * inventory_movements rows, then runs the real query plan for every
 * required query and asserts on the real plan text SQLite reports.
 *
 * `assertPlanUsesIndex` checks the plan does NOT contain a full
 * `SCAN <table>` on the large table being queried -- SQLite's own
 * `EXPLAIN QUERY PLAN` output says `SEARCH <table> USING INDEX ...` (or
 * `USING COVERING INDEX`) when an index is used, and `SCAN <table>` when
 * it is not. This is real output text, not inferred.
 */
class ProductInventoryQueryPlanTest {

    private fun newSeededDb(): Pair<RetailDatabase, app.cash.sqldelight.db.SqlDriver> {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        val db = RetailDatabase(driver)

        db.transaction {
            for (b in 1..3) {
                db.catalogQueries.insertBranch(1L, "Branch $b", null, null, 1000L)
            }
            for (c in 1..20) {
                db.catalogQueries.insertCategory(1L, "Category $c", null, 1000L)
            }
            for (p in 1..10_000) {
                val categoryId = ((p % 20) + 1).toLong()
                val status = if (p % 10 == 0) "archived" else "active" // 10% archived
                db.catalogQueries.insertProduct(
                    1L, "SKU-%05d".format(p), "0000%06d".format(p), "Product $p", "product $p", categoryId,
                    "1.00", "2.00", "10", "pcs", 5, 1000L, 1000L,
                )
                // insertProduct always writes status='active' -- flip the
                // 10% archived slice with a direct status update afterward.
                if (status == "archived") {
                    val id = db.catalogQueries.lastInsertRowId().executeAsOne()
                    db.catalogQueries.updateProductStatus("archived", 1000L, id, 1L)
                }
                val productId = db.catalogQueries.lastInsertRowId().executeAsOne()
                for (branchId in 1..3) {
                    db.inventoryQueries.upsertOpeningStock(1L, productId, branchId.toLong(), "50")
                }
                if (p % 500 == 0) {
                    db.inventoryQueries.insertMovement(
                        1L, productId, 1L, "MANUAL_RECEIPT", "10", "40", "50",
                        null, null, null, null, null, "tester", 1000L,
                    )
                }
            }
        }
        return db to driver
    }

    private fun explainPlan(driver: app.cash.sqldelight.db.SqlDriver, sql: String): String {
        val lines = mutableListOf<String>()
        driver.executeQuery(null, "EXPLAIN QUERY PLAN $sql", { cursor ->
            while (cursor.next().value) {
                lines += cursor.getString(3) ?: "" // EXPLAIN QUERY PLAN's `detail` column
            }
            QueryResult.Value(Unit)
        }, 0)
        return lines.joinToString("\n")
    }

    private fun assertNoFullScanOf(table: String, plan: String, queryLabel: String) {
        val hasFullScan = Regex("SCAN $table\\b(?! USING)").containsMatchIn(plan) ||
            plan.lines().any { it.trim().startsWith("SCAN $table") && "USING" !in it }
        assertTrue(!hasFullScan, "$queryLabel must not do a full table scan of $table -- real plan:\n$plan")
    }

    @Test
    fun productListByCompanyUsesIndex() {
        val (db, driver) = newSeededDb()
        val plan = explainPlan(driver, "SELECT * FROM products WHERE company_id = 1 AND status = 'active' ORDER BY name")
        assertNoFullScanOf("products", plan, "active product list")
    }

    @Test
    fun searchByNormalizedNamePrefixUsesIndex() {
        val (db, driver) = newSeededDb()
        val plan = explainPlan(driver, "SELECT * FROM products WHERE company_id = 1 AND status = 'active' AND normalized_name LIKE 'product 1%' ORDER BY normalized_name LIMIT 50")
        assertNoFullScanOf("products", plan, "normalized-name prefix search")
    }

    @Test
    fun barcodeLookupUsesUniqueIndex() {
        // Real, non-obvious requirement (stock-concurrency-report.md-style
        // finding, documented in product-inventory-query-plan-report.md):
        // this must use products_barcode_lookup specifically, not just
        // "any index" -- the partial products_company_barcode index was
        // PROVEN by real EXPLAIN QUERY PLAN output to never be chosen by
        // SQLite's planner for this query shape, silently degrading to a
        // near-full scan (company_id alone has zero selectivity across a
        // single-tenant 10K-row table) before the lookup index was added.
        val (db, driver) = newSeededDb()
        val plan = explainPlan(driver, "SELECT * FROM products WHERE barcode = '0000005000' AND company_id = 1 AND status = 'active'")
        assertTrue(plan.contains("products_barcode_lookup"), "barcode lookup must use the real, dedicated lookup index -- plan:\n$plan")
    }

    @Test
    fun skuLookupUsesUniqueIndex() {
        val (db, driver) = newSeededDb()
        val plan = explainPlan(driver, "SELECT * FROM products WHERE sku = 'SKU-05000' AND company_id = 1")
        assertNoFullScanOf("products", plan, "sku lookup")
    }

    @Test
    fun productsByCategoryUsesIndex() {
        val (db, driver) = newSeededDb()
        val plan = explainPlan(driver, "SELECT * FROM products WHERE company_id = 1 AND category_id = 5 AND status = 'active'")
        assertNoFullScanOf("products", plan, "products by category")
    }

    @Test
    fun archivedProductsQueryUsesIndex() {
        val (db, driver) = newSeededDb()
        val plan = explainPlan(driver, "SELECT * FROM products WHERE company_id = 1 AND status = 'archived'")
        assertNoFullScanOf("products", plan, "archived products")
    }

    @Test
    fun inventoryByBranchUsesIndex() {
        val (db, driver) = newSeededDb()
        val plan = explainPlan(driver, "SELECT * FROM inventory_balances WHERE company_id = 1 AND branch_id = 1")
        assertNoFullScanOf("inventory_balances", plan, "inventory by branch")
    }

    @Test
    fun inventoryByProductUsesIndex() {
        val (db, driver) = newSeededDb()
        val plan = explainPlan(driver, "SELECT quantity_on_hand FROM inventory_balances WHERE company_id = 1 AND product_id = 500 AND branch_id = 1")
        assertNoFullScanOf("inventory_balances", plan, "inventory by product")
    }

    @Test
    fun lowStockQueryUsesIndexOnProductsOuterScan() {
        val (db, driver) = newSeededDb()
        val plan = explainPlan(
            driver,
            """
            SELECT p.*, (SELECT CAST(SUM(CAST(quantity_on_hand AS REAL)) AS TEXT) FROM inventory_balances WHERE company_id = 1 AND product_id = p.id) AS qty
            FROM products p WHERE p.company_id = 1 AND p.status = 'active'
            """.trimIndent(),
        )
        assertNoFullScanOf("products", plan, "low-stock outer product scan")
    }

    @Test
    fun movementHistoryByProductUsesIndex() {
        val (db, driver) = newSeededDb()
        val plan = explainPlan(driver, "SELECT * FROM inventory_movements WHERE company_id = 1 AND product_id = 500 ORDER BY created_at DESC, id DESC LIMIT 100")
        assertNoFullScanOf("inventory_movements", plan, "movement history by product")
    }

    @Test
    fun productDetailProjectionByIdUsesPrimaryKey() {
        val (db, driver) = newSeededDb()
        val plan = explainPlan(driver, "SELECT p.*, c.name FROM products p LEFT JOIN categories c ON p.category_id = c.id WHERE p.id = 500 AND p.company_id = 1")
        // Primary-key lookups show up as "SEARCH products USING INTEGER PRIMARY KEY (rowid=?)" -- never a SCAN.
        assertNoFullScanOf("products", plan, "product detail projection by id")
    }
}
