package com.actionaura.retail.reporting.perf

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.data.sqldelight.SqlDelightProductRepository
import com.actionaura.retail.data.sqldelight.SqlDelightSettingsRepository
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.reporting.ReportPeriodFactory
import com.actionaura.retail.reporting.ReportScope
import com.actionaura.retail.reporting.SqlDelightDashboardRepository
import com.actionaura.retail.reporting.SqlDelightReportingRepository
import kotlinx.coroutines.test.runTest
import kotlinx.datetime.TimeZone
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * M5.7.6 -- real, instrumented proof of the N+1 query-count shape
 * `report-query-inventory.md` already flagged from reading the code:
 * `getSalesTrend` issues 2 real SELECTs per bucket (not one call for the
 * whole trend). Uses `CountingSqlDriver` to count real `executeQuery`
 * calls, not an estimate from timing.
 */
// M10 regression-stabilization: runTest(timeout = REPORTING_SCALE_DEADLOCK_GUARD_TIMEOUT) below is a real deadlock guard, not a performance requirement -- see ReportingScaleFixture.kt's own doc comment and m10-reporting-flake-investigation.md.
class ReportingQueryCountTest {

    private fun newSeededDb(): Triple<RetailDatabase, CountingSqlDriver, ReportingScaleFixture.Summary> {
        val raw = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        raw.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(raw)
        val counting = CountingSqlDriver(raw)
        val db = RetailDatabase(counting)
        val summary = ReportingScaleFixture.seed(db, counting)
        counting.reset()
        return Triple(db, counting, summary)
    }

    @Test
    fun oneSalesSummaryCallIssuesExactlyTwoRealQueries() = runTest(timeout = REPORTING_SCALE_DEADLOCK_GUARD_TIMEOUT) {
        val (db, driver, summary) = newSeededDb()
        val repo = SqlDelightReportingRepository(db, DatabaseWriteGate(), SqlDelightSettingsRepository(db, DatabaseWriteGate()))
        val period = ReportPeriodFactory.customRange(summary.startEpochMillis, summary.startEpochMillis + 86_400_000L)

        driver.reset()
        repo.getSalesSummary(ReportScope(summary.companyId), period)

        // 1 query for the base_currency setting lookup + 1 selectSalesForPeriod + 1 selectReturnsForPeriod.
        assertEquals(3, driver.queryCount, "getSalesSummary must issue exactly 3 real SELECTs (currency + sales + returns), not more")
    }

    @Test
    fun getSalesTrendIssuesExactlyTwoQueriesPerBucketARealNPlusOneShape() = runTest(timeout = REPORTING_SCALE_DEADLOCK_GUARD_TIMEOUT) {
        val (db, driver, summary) = newSeededDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val bucketCount = 30
        val buckets = ReportPeriodFactory.dailyBuckets(TimeZone.UTC, summary.startEpochMillis, summary.startEpochMillis + bucketCount * 86_400_000L)
            .mapIndexed { i, p -> p to "day-$i" }

        driver.reset()
        repo.getSalesTrend(ReportScope(summary.companyId), buckets)

        val expected = 1 /* currency */ + bucketCount * 2 /* sales + returns per bucket */
        assertEquals(expected, driver.queryCount, "getSalesTrend issues 2 real queries per bucket -- a real, confirmed N+1 shape, not a false alarm")
    }

    @Test
    fun topProductsCallIssuesExactlyTwoRealQueriesRegardlessOfResultSize() = runTest(timeout = REPORTING_SCALE_DEADLOCK_GUARD_TIMEOUT) {
        val (db, driver, summary) = newSeededDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val period = ReportPeriodFactory.customRange(summary.startEpochMillis, summary.endEpochMillis)

        driver.reset()
        repo.getTopProductsByQuantity(ReportScope(summary.companyId), period, 20L)

        // 1 currency + selectSaleItemsForPeriod + selectReturnItemsForPeriod -- NOT one Product lookup per result row.
        assertEquals(3, driver.queryCount, "Top Products must not issue one Product lookup per ranked result -- exactly 3 real queries regardless of the ~10,000-product catalog or the 20-result limit")
    }

    @Test
    fun fullDashboardCompositionIssuesABoundedExplainableQueryCount() = runTest(timeout = REPORTING_SCALE_DEADLOCK_GUARD_TIMEOUT) {
        val (db, driver, summary) = newSeededDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val dashboardRepo = SqlDelightDashboardRepository(repo, SqlDelightProductRepository(db, gate))
        val today = ReportPeriodFactory.customRange(summary.startEpochMillis, summary.startEpochMillis + 86_400_000L)
        val selected = ReportPeriodFactory.customRange(summary.startEpochMillis, summary.startEpochMillis + 30 * 86_400_000L)
        val trendBuckets = ReportPeriodFactory.dailyBuckets(TimeZone.UTC, summary.startEpochMillis, summary.startEpochMillis + 7 * 86_400_000L).mapIndexed { i, p -> p to "day-$i" }

        driver.reset()
        dashboardRepo.getDashboard(ReportScope(summary.companyId), today, selected, trendBuckets, 20L, 20L, 0L)

        // today(3) + selectedPeriod(3) + trend(1 currency + 7*2) + topByQty(3) + topByRevenue(3) + listLowStock(1) = 28.
        // Real, explainable, bounded by (trend bucket count), never by product/sale/branch catalog size.
        val expected = 3 + 3 + (1 + 7 * 2) + 3 + 3 + 1
        assertEquals(expected, driver.queryCount, "full dashboard composition must be a bounded, explainable query count driven only by trend-bucket count, never by catalog size")
        assertTrue(driver.queryCount < 50, "even the full dashboard composition stays well under 50 real queries at this scale")
    }
}
