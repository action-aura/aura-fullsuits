package com.actionaura.retail.reporting.perf

import app.cash.sqldelight.db.QueryResult
import app.cash.sqldelight.db.SqlDriver
import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.db.RetailDatabase
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * M5.7.1 -- real, executed proof that `ReportingScaleFixture` produces
 * exactly the row counts it claims, plus real machine/environment
 * evidence for `reporting-scale-dataset.md`. This is the ONLY test in
 * M5.7 whose job is to validate the fixture itself; every other M5.7
 * test file uses `ReportingScaleFixture.seed(db, driver)` as a given.
 */
// M10 regression-stabilization: runTest(timeout = REPORTING_SCALE_DEADLOCK_GUARD_TIMEOUT) below is a real deadlock guard, not a performance requirement -- see ReportingScaleFixture.kt's own doc comment and m10-reporting-flake-investigation.md.
class ReportingScaleDatasetTest {

    private fun newDb(): Pair<RetailDatabase, SqlDriver> {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver) to driver
    }

    @Test
    fun fixtureProducesExactlyTheClaimedRowCountsAndCharacteristics() = runTest(timeout = REPORTING_SCALE_DEADLOCK_GUARD_TIMEOUT) {
        val (db, driver) = newDb()
        val summary = ReportingScaleFixture.seed(db, driver)

        val realProductCount = countRows(driver, "products")
        val realCategoryCountAll = countRows(driver, "categories")
        val realBranchCount = countRows(driver, "branches")
        val realSaleCount = countRows(driver, "sales")
        val realSaleItemCount = countRows(driver, "sale_items")
        val realReturnCount = countRows(driver, "returns")
        val realReturnItemCount = countRows(driver, "return_items")
        val realArchivedProductCount = countRowsWhere(driver, "products", "status = 'inactive'")
        val realArchivedCategoryCount = countRowsWhere(driver, "categories", "status = 'archived'")
        val realArchivedBranchCount = countRowsWhere(driver, "branches", "status = 'archived'")
        val realLaterPeriodReturnCount = countRowsWhere(driver, "returns", "created_at > (SELECT s.created_at FROM sales s WHERE s.id = returns.sale_id) + 40*86400000")
        val realArchivedBranchSaleCount = countRowsWhere(driver, "sales", "branch_id = ${summary.archivedBranchId}")

        assertEquals(ReportingScaleFixture.PRODUCT_COUNT, realProductCount)
        assertEquals(ReportingScaleFixture.CATEGORY_COUNT, realCategoryCountAll)
        assertEquals(ReportingScaleFixture.BRANCH_COUNT, realBranchCount)
        assertTrue(realSaleCount >= ReportingScaleFixture.SALE_COUNT, "expected at least ${ReportingScaleFixture.SALE_COUNT} sales, got $realSaleCount")
        assertTrue(realSaleItemCount > realSaleCount, "sale_items must materially outnumber sales")
        assertEquals(ReportingScaleFixture.ARCHIVED_PRODUCT_COUNT, realArchivedProductCount)
        assertEquals(ReportingScaleFixture.ARCHIVED_CATEGORY_COUNT, realArchivedCategoryCount)
        assertEquals(1, realArchivedBranchCount)
        assertTrue(realArchivedBranchSaleCount > 0, "the archived branch must have real historical sales, not zero")
        assertTrue(realLaterPeriodReturnCount > 0, "at least one return must be finalized in a later period than its originating sale")
        assertTrue(realReturnCount > 0 && realReturnItemCount > 0)

        val runtimeName = System.getProperty("java.vendor") + " " + System.getProperty("java.version")
        val osName = System.getProperty("os.name") + " " + System.getProperty("os.version")

        println("M5.7.1 real fixture evidence:")
        println("  products=$realProductCount categories=$realCategoryCountAll (archived=$realArchivedCategoryCount) branches=$realBranchCount (archived=$realArchivedBranchCount)")
        println("  sales=$realSaleCount sale_items=$realSaleItemCount returns=$realReturnCount return_items=$realReturnItemCount")
        println("  archivedProductCount=$realArchivedProductCount archivedBranchSaleCount=$realArchivedBranchSaleCount laterPeriodReturnCount=$realLaterPeriodReturnCount")
        println("  reassignedProductId=${summary.reassignedProductId} from=${summary.reassignedFromCategoryId} to=${summary.reassignedToCategoryId}")
        println("  tiedProductIds=${summary.tiedProductIds} tiedDay=${summary.tiedProductsDay}")
        println("  dateRange=[${summary.startEpochMillis}, ${summary.endEpochMillis}) spanning $YEARS_LABEL years")
        println("  generationElapsedMs=${summary.generationElapsedMs}")
        println("  runtime=$runtimeName os=$osName")
    }

    private fun countRows(driver: SqlDriver, table: String): Int =
        countRowsWhere(driver, table, "1=1")

    private fun countRowsWhere(driver: SqlDriver, table: String, whereClause: String): Int {
        var result = 0
        driver.executeQuery(null, "SELECT COUNT(*) FROM $table WHERE $whereClause", { cursor ->
            if (cursor.next().value) result = (cursor.getLong(0) ?: 0L).toInt()
            QueryResult.Value(Unit)
        }, 0)
        return result
    }

    private val YEARS_LABEL get() = ReportingScaleFixture.YEARS
}
