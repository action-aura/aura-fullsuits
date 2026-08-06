package com.actionaura.retail.reporting.perf

import app.cash.sqldelight.db.QueryResult
import app.cash.sqldelight.db.SqlDriver
import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.data.sqldelight.SqlDelightSettingsRepository
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.reporting.ReportPeriodFactory
import com.actionaura.retail.reporting.ReportScope
import com.actionaura.retail.reporting.SqlDelightReportingRepository
import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import kotlinx.coroutines.test.runTest

/**
 * M5.7.11 -- real REGRESSION assertions (not just capture, unlike
 * `ReportingQueryPlanTest.kt`) on the plan characteristics
 * `reporting-query-plan-report.md` established as real findings. Asserts
 * meaningful, SQLite-version-tolerant characteristics (an index name
 * substring is present; a prohibited full scan is absent) rather than
 * comparing complete raw `EXPLAIN QUERY PLAN` text, which real SQLite
 * version differences would make brittle.
 */
// M10 regression-stabilization: runTest(timeout = REPORTING_SCALE_DEADLOCK_GUARD_TIMEOUT) below is a real deadlock guard, not a performance requirement -- see ReportingScaleFixture.kt's own doc comment and m10-reporting-flake-investigation.md.
class ReportingQueryPlanRegressionTest {

    companion object {
        private val driver: SqlDriver by lazy {
            val d = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
            d.execute(null, "PRAGMA foreign_keys=ON", 0)
            RetailDatabase.Schema.create(d)
            d
        }
        private val db: RetailDatabase by lazy { RetailDatabase(driver) }
        val summary: ReportingScaleFixture.Summary by lazy { ReportingScaleFixture.seed(db, driver) }
    }

    private fun explainPlan(sql: String): String {
        summary
        val lines = mutableListOf<String>()
        driver.executeQuery(null, "EXPLAIN QUERY PLAN $sql", { cursor ->
            while (cursor.next().value) lines += cursor.getString(3) ?: ""
            QueryResult.Value(Unit)
        }, 0)
        return lines.joinToString("\n")
    }

    private fun assertNoFullScanOfAnyReportingTable(plan: String) {
        for (table in listOf("sales", "returns", "sale_items", "return_items", "products")) {
            val hasFullScan = plan.lines().any { it.trim().let { line -> line.startsWith("SCAN $table") && "USING" !in line } }
            assertFalse(hasFullScan, "REGRESSION: $table now shows a full table scan -- real plan:\n$plan")
        }
    }

    @Test
    fun salesByBranchStaysIndexedOnSalesBranchId() {
        val branchId = summary.branchIds[0]
        val plan = explainPlan("SELECT id, branch_id, total, created_at FROM sales WHERE company_id = 1 AND status = 'completed' AND created_at >= ${summary.startEpochMillis} AND created_at < ${summary.endEpochMillis} AND branch_id = $branchId ORDER BY created_at")
        assertTrue("USING INDEX sales_branch_id" in plan, "REGRESSION: Branch-filtered sales query no longer uses sales_branch_id -- real plan:\n$plan")
        assertNoFullScanOfAnyReportingTable(plan)
    }

    @Test
    fun salesAllBranchesStaysIndexedOnSalesCompanyId() {
        val plan = explainPlan("SELECT id, branch_id, total, created_at FROM sales WHERE company_id = 1 AND status = 'completed' AND created_at >= ${summary.startEpochMillis} AND created_at < ${summary.endEpochMillis} ORDER BY created_at")
        assertTrue("USING INDEX sales_company_id" in plan, "REGRESSION: all-branch sales query no longer uses sales_company_id -- real plan:\n$plan")
        assertNoFullScanOfAnyReportingTable(plan)
    }

    @Test
    fun returnsStayIndexedOnReturnsCompanyIdRegardlessOfBranchFilter() {
        val branchId = summary.branchIds[0]
        val planAllBranches = explainPlan("SELECT id, branch_id, refund_amount, created_at FROM returns WHERE company_id = 1 AND status = 'completed' AND created_at >= ${summary.startEpochMillis} AND created_at < ${summary.endEpochMillis} ORDER BY created_at")
        val planOneBranch = explainPlan("SELECT id, branch_id, refund_amount, created_at FROM returns WHERE company_id = 1 AND status = 'completed' AND created_at >= ${summary.startEpochMillis} AND created_at < ${summary.endEpochMillis} AND branch_id = $branchId ORDER BY created_at")
        for (plan in listOf(planAllBranches, planOneBranch)) {
            assertTrue("USING INDEX returns_company_id" in plan, "REGRESSION: returns query no longer uses returns_company_id -- real plan:\n$plan")
            assertNoFullScanOfAnyReportingTable(plan)
        }
    }

    @Test
    fun topProductsJoinStaysIndexedOnSaleItemsSaleIdAndProductsPrimaryKey() {
        val plan = explainPlan(
            "SELECT si.product_id, si.product_name_at_sale, si.quantity, si.line_total FROM sale_items si " +
                "JOIN sales s ON si.sale_id = s.id JOIN products p ON si.product_id = p.id " +
                "WHERE s.company_id = 1 AND s.status = 'completed' AND s.created_at >= ${summary.startEpochMillis} AND s.created_at < ${summary.endEpochMillis}",
        )
        assertTrue("USING INDEX sale_items_sale_id" in plan, "REGRESSION: sale_items join no longer uses sale_items_sale_id -- real plan:\n$plan")
        assertTrue("USING INTEGER PRIMARY KEY" in plan, "REGRESSION: products join no longer uses its primary key -- real plan:\n$plan")
        assertNoFullScanOfAnyReportingTable(plan)
    }

    @Test
    fun categoryFilteredTopProductsStaysOnTheCoveringIndex() {
        val categoryId = summary.categoryIds[0]
        val plan = explainPlan(
            "SELECT si.product_id, si.product_name_at_sale, si.quantity, si.line_total FROM sale_items si " +
                "JOIN sales s ON si.sale_id = s.id JOIN products p ON si.product_id = p.id " +
                "WHERE s.company_id = 1 AND s.status = 'completed' AND s.created_at >= ${summary.startEpochMillis} AND s.created_at < ${summary.endEpochMillis} AND p.category_id = $categoryId",
        )
        assertTrue("USING COVERING INDEX products_category_id" in plan, "REGRESSION: Category filter no longer uses the covering index -- real plan:\n$plan")
        assertNoFullScanOfAnyReportingTable(plan)
    }

    @Test
    fun realQueryResultsStayExactAndBoundedAtFullScale() = runTest(timeout = REPORTING_SCALE_DEADLOCK_GUARD_TIMEOUT) {
        val gate = DatabaseWriteGate()
        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val fullRange = ReportPeriodFactory.customRange(summary.startEpochMillis, summary.endEpochMillis)

        val top = repo.getTopProductsByQuantity(ReportScope(summary.companyId), fullRange, 20L)
        assertTrue(top.metrics.size <= 20, "REGRESSION: Top Products must never return more than the requested limit, even at 10,000-product scale")

        val summaryResult = repo.getSalesSummary(ReportScope(summary.companyId), fullRange)
        val totals = summaryResult.totalsByCurrency.values.first()
        assertTrue(totals.transactionCount >= 100_000L, "REGRESSION: full-range transaction count must still reflect the real ~100K-sale dataset, got ${totals.transactionCount}")
        assertTrue(summaryResult.dataQualityIssues.isEmpty(), "REGRESSION: the real, well-formed 100K-sale dataset must produce zero data-quality issues")
    }
}
