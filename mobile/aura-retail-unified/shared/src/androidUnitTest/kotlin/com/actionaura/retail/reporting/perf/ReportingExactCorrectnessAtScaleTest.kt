package com.actionaura.retail.reporting.perf

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.data.sqldelight.SqlDelightSettingsRepository
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.financial.CurrencyCode
import com.actionaura.retail.financial.Money
import com.actionaura.retail.reporting.ReportPeriodFactory
import com.actionaura.retail.reporting.ReportScope
import com.actionaura.retail.reporting.SqlDelightReportingRepository
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * M5.7.13 -- real, exact financial-correctness proof at 100K-sale scale,
 * closing the gap `ExactAggregationPerformanceTest` (timing-focused) and
 * `ReportingQueryPlanRegressionTest` (structural-bound-focused) leave
 * open: an exact hand-computed expected total, verified against the
 * real 100,001-sale dataset. `ReportingScaleFixture`'s own construction
 * uses exactly four known price points, letting this total be computed
 * by hand rather than approximated.
 */
// M10 regression-stabilization: runTest(timeout = REPORTING_SCALE_DEADLOCK_GUARD_TIMEOUT) below is a real deadlock guard, not a performance requirement -- see ReportingScaleFixture.kt's own doc comment and m10-reporting-flake-investigation.md.
class ReportingExactCorrectnessAtScaleTest {

    private fun newSeededDb(): Triple<RetailDatabase, JdbcSqliteDriver, ReportingScaleFixture.Summary> {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        val db = RetailDatabase(driver)
        val summary = ReportingScaleFixture.seed(db, driver)
        return Triple(db, driver, summary)
    }

    @Test
    fun grossSalesOverTheFullRangeExactlyMatchesTheFixturesOwnHandComputedTotal() = runTest(timeout = REPORTING_SCALE_DEADLOCK_GUARD_TIMEOUT) {
        val (db, driver, summary) = newSeededDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val fullRange = ReportPeriodFactory.customRange(summary.startEpochMillis, summary.endEpochMillis)

        // ReportingScaleFixture's own real construction: 3 tied-product
        // sales at 20.00, 50 archived-branch-history sales at 12.00, the
        // main loop (SALE_COUNT - 3 - 50) sales at 30.00, and 1
        // reassigned-product sale at 15.00.
        val expectedGross = Money.of(20.00) * 3 + Money.of(12.00) * 50 +
            Money.of(30.00) * (ReportingScaleFixture.SALE_COUNT - 3 - 50) + Money.of(15.00)

        val summaryResult = repo.getSalesSummary(ReportScope(summary.companyId), fullRange)
        val totals = summaryResult.totalsByCurrency.getValue(CurrencyCode("USD"))

        assertEquals(expectedGross, totals.grossSales, "gross sales over the full 100,001-sale range must exactly match the fixture's own hand-computed total, not merely 'a plausible-looking number'")
        assertEquals(100_001L, totals.transactionCount)
        assertEquals(totals.grossSales - totals.confirmedReturns, totals.netSales, "netSales must always equal grossSales - confirmedReturns exactly, even at 100K-sale scale")
    }

    private operator fun Money.times(count: Int): Money {
        var result = Money.ZERO
        repeat(count) { result += this }
        return result
    }
}
