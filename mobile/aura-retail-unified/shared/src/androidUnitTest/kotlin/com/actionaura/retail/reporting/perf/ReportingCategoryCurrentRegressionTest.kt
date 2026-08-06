package com.actionaura.retail.reporting.perf

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.data.sqldelight.SqlDelightSettingsRepository
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.reporting.ReportPeriodFactory
import com.actionaura.retail.reporting.ReportScope
import com.actionaura.retail.reporting.SqlDelightReportingRepository
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertTrue

/**
 * M5.7.7 -- real regression proof of the M5.6.7 Category-current policy
 * (`historical-category-reporting-decision.md`'s Option B), retained
 * unchanged at M5.7 real scale -- no immutable Category-at-Sale snapshot
 * is silently added during this performance milestone. Uses
 * `ReportingScaleFixture`'s real `reassignedProductId` case: a Product
 * sold under Category A, later reassigned to Category B.
 */
// M10 regression-stabilization: runTest(timeout = REPORTING_SCALE_DEADLOCK_GUARD_TIMEOUT) below is a real deadlock guard, not a performance requirement -- see ReportingScaleFixture.kt's own doc comment and m10-reporting-flake-investigation.md.
class ReportingCategoryCurrentRegressionTest {

    private fun newSeededDb(): Triple<RetailDatabase, JdbcSqliteDriver, ReportingScaleFixture.Summary> {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        val db = RetailDatabase(driver)
        val summary = ReportingScaleFixture.seed(db, driver)
        return Triple(db, driver, summary)
    }

    @Test
    fun historicalReportFilteredByTheProductsCurrentCategoryIncludesTheSaleEvenThoughItWasSoldUnderADifferentCategory() = runTest(timeout = REPORTING_SCALE_DEADLOCK_GUARD_TIMEOUT) {
        val (db, driver, summary) = newSeededDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val saleDayPeriod = ReportPeriodFactory.customRange(summary.reassignedProductSaleDay, summary.reassignedProductSaleDay + 86_400_000L)

        val filteredByCurrentCategory = repo.getTopProductsByQuantity(
            ReportScope(summary.companyId, categoryId = summary.reassignedToCategoryId), saleDayPeriod, 50L,
        )

        assertTrue(
            filteredByCurrentCategory.metrics.any { it.productId == summary.reassignedProductId },
            "the sale must appear under the product's CURRENT category (reassignedToCategoryId), per the real, disclosed Option-B policy",
        )
    }

    @Test
    fun historicalReportFilteredByTheOriginalSaleTimeCategoryNoLongerIncludesTheSaleAfterReassignment() = runTest(timeout = REPORTING_SCALE_DEADLOCK_GUARD_TIMEOUT) {
        val (db, driver, summary) = newSeededDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val saleDayPeriod = ReportPeriodFactory.customRange(summary.reassignedProductSaleDay, summary.reassignedProductSaleDay + 86_400_000L)

        val filteredByOriginalCategory = repo.getTopProductsByQuantity(
            ReportScope(summary.companyId, categoryId = summary.reassignedFromCategoryId), saleDayPeriod, 50L,
        )

        assertTrue(
            filteredByOriginalCategory.metrics.none { it.productId == summary.reassignedProductId },
            "the sale must NOT appear under its original sale-time category after reassignment -- old results move, per historical-category-reporting-decision.md, not pretended otherwise",
        )
    }

    @Test
    fun theCategoryCurrentFilterQueryPlanRemainsIndexedAtFullScaleNotDegradedByReassignmentHistory() {
        val (_, driver, summary) = newSeededDb()
        val lines = mutableListOf<String>()
        driver.executeQuery(null,
            "EXPLAIN QUERY PLAN SELECT si.product_id, si.product_name_at_sale, si.quantity, si.line_total FROM sale_items si " +
                "JOIN sales s ON si.sale_id = s.id JOIN products p ON si.product_id = p.id " +
                "WHERE s.company_id = 1 AND s.status = 'completed' AND s.created_at >= ${summary.startEpochMillis} AND s.created_at < ${summary.endEpochMillis} " +
                "AND p.category_id = '${summary.reassignedToCategoryId}'",
            { cursor ->
                while (cursor.next().value) lines += cursor.getString(3) ?: ""
                app.cash.sqldelight.db.QueryResult.Value(Unit)
            }, 0,
        )
        val plan = lines.joinToString("\n")
        assertTrue("USING COVERING INDEX products_category_id" in plan, "the Category-current filter must remain indexed at full scale -- real plan:\n$plan")
    }
}
