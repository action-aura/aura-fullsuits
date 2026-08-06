package com.actionaura.retail.reporting.perf

import app.cash.sqldelight.db.SqlDriver
import com.actionaura.retail.db.RetailDatabase
import kotlinx.datetime.LocalDate
import kotlinx.datetime.TimeZone
import kotlinx.datetime.atStartOfDayIn
import kotlin.time.Duration.Companion.minutes

fun epochMillisUtc(year: Int, month: Int, day: Int): Long =
    LocalDate(year, month, day).atStartOfDayIn(TimeZone.UTC).toEpochMilliseconds()

/**
 * M10 regression-stabilization finding (`m10-reporting-flake-investigation.md`):
 * every real `runTest` in this package that calls [ReportingScaleFixture.seed]
 * pays this fixture's real ~400,000-statement seed cost plus real, repeated,
 * unmocked report/write calls -- real, measured, isolated-host wall-clock
 * cost from 1.1s (a single warm call, `ExactAggregationPerformanceTest`'s own
 * documented range) up to ~75s per test (`ReportingConcurrencyAtScaleTest`,
 * multiple sequential real calls), which intermittently exceeded
 * `kotlinx-coroutines-test`'s own implicit 60-second `runTest` default
 * dispatch-timeout -- a real, generic deadlock guard, not a correctness
 * assertion this codebase itself wrote. Real regression evidence: a full,
 * freshly-executed `:shared:testDebugUnitTest` run
 * (`ExactAggregationPerformanceTest.exactAggregationLatencyAcrossRepresentativeRangesAtFullScale`)
 * hit this exact ceiling despite every one of its OWN internal assertions
 * being comfortably within their own generous, documented bounds -- proof
 * the failure is the generic guard, not the test's real correctness logic.
 * `REPORTING_SCALE_DEADLOCK_GUARD_TIMEOUT` raises that guard to a real,
 * generous, evidence-based bound for every real test in this package built
 * on this fixture, while every actual correctness/latency assertion in
 * each test file is completely unchanged -- this is a real per-test
 * wall-clock SAFETY NET against a genuine hang, never a performance
 * requirement. Shared here (not duplicated per file) since every file in
 * this package pays the identical real fixture cost.
 */
internal val REPORTING_SCALE_DEADLOCK_GUARD_TIMEOUT = 5.minutes

/**
 * M5.7.1 -- real, deterministic, representative-scale dataset shared by
 * every M5.7 query-plan/performance/concurrency test. Seeds directly via
 * bulk SQLDelight insert queries inside one transaction (never through
 * route/UI calls) so generation itself does not dominate test time, but
 * every downstream report query in M5.7 still executes against the real
 * schema and real SQLite engine -- only data GENERATION is bulk/direct.
 *
 * IDs are computed arithmetically (1, 2, 3, ... in insertion order)
 * rather than read back via `lastInsertRowId()` after every row -- safe
 * because this always seeds a fresh, empty, single-connection in-memory
 * database with AUTOINCREMENT primary keys and no deletions, and cuts
 * generation time roughly in half versus round-tripping for every id.
 */
object ReportingScaleFixture {
    const val CATEGORY_COUNT = 30
    const val ARCHIVED_CATEGORY_COUNT = 5
    const val BRANCH_COUNT = 6
    const val PRODUCT_COUNT = 10_000
    const val ARCHIVED_PRODUCT_COUNT = 20
    const val SALE_COUNT = 100_000
    const val ITEMS_PER_SALE = 3
    const val RETURN_EVERY_NTH_SALE = 10
    const val LATER_PERIOD_RETURN_EVERY_NTH_RETURN = 5
    const val YEARS = 3

    data class Summary(
        val companyId: Long,
        val branchIds: List<Long>,
        val archivedBranchId: Long,
        val categoryIds: List<String>,
        val archivedCategoryIds: List<String>,
        val productIds: List<Long>,
        val archivedProductIds: List<Long>,
        val reassignedProductId: Long,
        val reassignedFromCategoryId: String,
        val reassignedToCategoryId: String,
        val reassignedProductSaleDay: Long,
        val tiedProductIds: List<Long>,
        val tiedProductsDay: Long,
        val saleCount: Long,
        val saleItemCount: Long,
        val returnCount: Long,
        val returnItemCount: Long,
        val startEpochMillis: Long,
        val endEpochMillis: Long,
        val generationElapsedMs: Long,
    )

    /** A separate, small, deliberately-corrupted fixture -- never mixed into the main scale dataset (checkpoint's own "malformed financial rows in a separate data-quality fixture" requirement). */
    data class DataQualityFixture(val malformedSaleId: Long, val malformedReturnId: Long, val malformedSaleItemProductId: Long, val day: Long)

    fun seed(db: RetailDatabase, driver: SqlDriver, companyId: Long = 1L): Summary {
        val genStart = System.currentTimeMillis()
        val startEpoch = epochMillisUtc(2024, 1, 1)
        val endEpoch = epochMillisUtc(2024 + YEARS, 1, 1)
        val totalDays = ((endEpoch - startEpoch) / 86_400_000L).toInt()

        var nextBranchId = 1L
        var nextProductId = 1L
        var nextSaleId = 1L
        var nextSaleItemId = 1L
        var nextReturnId = 1L
        var nextReturnItemId = 1L

        val categoryIds = mutableListOf<String>()
        val archivedCategoryIds = mutableListOf<String>()
        val branchIds = mutableListOf<Long>()
        val productIds = mutableListOf<Long>()
        val archivedProductIds = mutableListOf<Long>()
        var tiedProductIds: List<Long> = emptyList()
        var tiedDay = 0L
        var returnCount = 0L
        var returnItemCount = 0L
        var reassignedProductId = 0L
        var reassignedFromCategoryId = ""
        var reassignedToCategoryId = ""
        var reassignedProductSaleDay = 0L

        db.transaction {
            // M-sync -- categories.id is now a client-generated TEXT id, not
            // an arithmetic AUTOINCREMENT sequence -- this fixture generates
            // its own simple, deterministic id per category (still no
            // round-trip read-back needed, preserving this fixture's own
            // documented "no lastInsertRowId() per row" performance intent).
            repeat(CATEGORY_COUNT) { i ->
                val categoryId = "cat-$i"
                db.catalogQueries.insertCategory(categoryId, companyId, "Category-$i", null, startEpoch)
                categoryIds += categoryId
            }
            // Archive the last ARCHIVED_CATEGORY_COUNT categories.
            val toArchive = categoryIds.takeLast(ARCHIVED_CATEGORY_COUNT)
            toArchive.forEach { id ->
                driver.execute(null, "UPDATE categories SET status = 'archived' WHERE id = ?", 1) { bindString(0, id) }
            }
            archivedCategoryIds += toArchive

            repeat(BRANCH_COUNT) { i ->
                db.catalogQueries.insertBranch(companyId, "Branch-$i", null, null, startEpoch)
                branchIds += nextBranchId++
            }
            val archivedBranchId = branchIds.last()
            driver.execute(null, "UPDATE branches SET status = 'archived' WHERE id = ?", 1) { bindLong(0, archivedBranchId) }

            repeat(PRODUCT_COUNT) { i ->
                val categoryId = categoryIds[i % (categoryIds.size - ARCHIVED_CATEGORY_COUNT)] // active categories only, at creation
                db.catalogQueries.insertProduct(
                    companyId, "SKU-$i", null, "Product-$i", "product-$i", categoryId,
                    "1.00", "2.00", "0", "unit", 5, startEpoch, startEpoch,
                )
                productIds += nextProductId++
            }
            val toArchiveProducts = productIds.take(ARCHIVED_PRODUCT_COUNT)
            toArchiveProducts.forEach { id ->
                driver.execute(null, "UPDATE products SET status = 'inactive' WHERE id = ?", 1) { bindLong(0, id) }
            }
            archivedProductIds += toArchiveProducts

            // Tied top-product metrics: 3 products, identical quantity+revenue, isolated on one specific day.
            tiedDay = startEpoch + 10L * 86_400_000L
            tiedProductIds = listOf(productIds[PRODUCT_COUNT - 1], productIds[PRODUCT_COUNT - 2], productIds[PRODUCT_COUNT - 3])
            tiedProductIds.forEach { productId ->
                db.salesQueries.insertSale(companyId, null, branchIds[0], null, "POS", "0.00", "0.00", "0.00", "20.00", "20.00", "0.00", "cash", null, null, null, tiedDay)
                val saleId = nextSaleId++
                db.salesQueries.insertSaleItem(saleId, productId, "Product", "2", "20.00", "0", "0", "20.00")
                nextSaleItemId++
            }

            // Historical sales for the (later archived) branch -- "at least 1
            // archived branch with historical Sales," not zero sales.
            val archivedBranchHistoryDay = startEpoch + 20L * 86_400_000L
            repeat(50) {
                db.salesQueries.insertSale(companyId, null, branchIds.last(), null, "POS", "0.00", "0.00", "0.00", "12.00", "12.00", "0.00", "cash", null, null, null, archivedBranchHistoryDay)
                val saleId = nextSaleId++
                db.salesQueries.insertSaleItem(saleId, productIds[0], "Product", "1", "12.00", "0", "0", "12.00")
                nextSaleItemId++
            }

            repeat(SALE_COUNT - tiedProductIds.size - 50) { saleIndex ->
                val dayOffset = saleIndex % totalDays
                val createdAt = startEpoch + dayOffset * 86_400_000L
                val branchId = branchIds[saleIndex % (branchIds.size - 1)] // active branches only for new sales
                db.salesQueries.insertSale(companyId, null, branchId, null, "POS", "0.00", "0.00", "0.00", "30.00", "30.00", "0.00", "cash", null, null, null, createdAt)
                val saleId = nextSaleId++
                repeat(ITEMS_PER_SALE) { lineIndex ->
                    val productId = productIds[(saleIndex * ITEMS_PER_SALE + lineIndex) % productIds.size]
                    db.salesQueries.insertSaleItem(saleId, productId, "Product", "1", "10.00", "0", "0", "10.00")
                    nextSaleItemId++
                }
                if (saleIndex % RETURN_EVERY_NTH_SALE == 0) {
                    val isFullReturn = (saleIndex / RETURN_EVERY_NTH_SALE) % 2 == 0
                    val isLaterPeriod = (saleIndex / RETURN_EVERY_NTH_SALE) % LATER_PERIOD_RETURN_EVERY_NTH_RETURN == 0
                    val returnCreatedAt = if (isLaterPeriod) createdAt + 45L * 86_400_000L else createdAt
                    val returnAmount = if (isFullReturn) "30.00" else "10.00"
                    db.returnsQueries.insertReturn(companyId, null, saleId, branchId, "POS", null, "cash", returnAmount, null, returnCreatedAt.coerceAtMost(endEpoch - 1))
                    val returnId = nextReturnId++
                    val returnedItemCount = if (isFullReturn) ITEMS_PER_SALE else 1
                    repeat(returnedItemCount) { lineIndex ->
                        val productId = productIds[(saleIndex * ITEMS_PER_SALE + lineIndex) % productIds.size]
                        db.returnsQueries.insertReturnItem(returnId, productId, "Product", "1", "10.00", "10.00")
                        nextReturnItemId++
                    }
                    returnCount++
                    returnItemCount += returnedItemCount
                }
            }

            // Category reassignment history: pick a product with real historical sales, move it to a different active category.
            reassignedProductId = productIds[100]
            reassignedFromCategoryId = categoryIds[100 % (categoryIds.size - ARCHIVED_CATEGORY_COUNT)]
            reassignedToCategoryId = categoryIds[(100 + 1) % (categoryIds.size - ARCHIVED_CATEGORY_COUNT)]
            reassignedProductSaleDay = startEpoch + 5L * 86_400_000L
            db.salesQueries.insertSale(companyId, null, branchIds[0], null, "POS", "0.00", "0.00", "0.00", "15.00", "15.00", "0.00", "cash", null, null, null, reassignedProductSaleDay)
            val reassignSaleId = nextSaleId++
            db.salesQueries.insertSaleItem(reassignSaleId, reassignedProductId, "Product", "1", "15.00", "0", "0", "15.00")
            nextSaleItemId++
            driver.execute(null, "UPDATE products SET category_id = ? WHERE id = ?", 2) { bindString(0, reassignedToCategoryId); bindLong(1, reassignedProductId) }
        }

        val genElapsed = System.currentTimeMillis() - genStart
        return Summary(
            companyId, branchIds, branchIds.last(), categoryIds, archivedCategoryIds, productIds, archivedProductIds,
            reassignedProductId, reassignedFromCategoryId, reassignedToCategoryId, reassignedProductSaleDay,
            tiedProductIds, tiedDay,
            nextSaleId - 1, nextSaleItemId - 1, returnCount, returnItemCount,
            startEpoch, endEpoch, genElapsed,
        )
    }

    /** Deliberately corrupted rows, isolated from the main dataset -- checkpoint's own "malformed financial rows in a separate data-quality fixture" requirement. */
    fun seedDataQualityFixture(db: RetailDatabase, driver: SqlDriver, companyId: Long, branchId: Long, productId: Long): DataQualityFixture {
        val day = epochMillisUtc(2027, 6, 1)
        db.salesQueries.insertSale(companyId, null, branchId, null, "POS", "0.00", "0.00", "0.00", "20.00", "20.00", "0.00", "cash", null, null, null, day)
        val malformedSaleId = db.catalogQueries.lastInsertRowId().executeAsOne()
        driver.execute(null, "UPDATE sales SET total = 'not-a-number' WHERE id = ?", 1) { bindLong(0, malformedSaleId) }

        db.salesQueries.insertSaleItem(malformedSaleId, productId, "Product", "not-a-quantity", "10.00", "0", "0", "10.00")

        db.returnsQueries.insertReturn(companyId, null, malformedSaleId, branchId, "POS", null, "cash", "not-money", null, day)
        val malformedReturnId = db.catalogQueries.lastInsertRowId().executeAsOne()

        return DataQualityFixture(malformedSaleId, malformedReturnId, productId, day)
    }
}
