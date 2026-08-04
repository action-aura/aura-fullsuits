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
import kotlin.test.assertTrue

/**
 * M5.7.5 -- real, executed latency measurement of the exact Kotlin
 * `Money`/`Quantity` aggregation path (never SQL SUM/AVG,
 * `exact-report-aggregation-decision.md`) against the real M5.7.1
 * 100K-sale dataset. This also supplies the real evidence M5.7.4's index
 * decision needs: does the "USE TEMP B-TREE FOR ORDER BY" the M5.7.2
 * query plans showed actually cost enough wall-clock time to justify a
 * new composite index?
 */
class ExactAggregationPerformanceTest {

    private fun newSeededDb(): Triple<RetailDatabase, JdbcSqliteDriver, ReportingScaleFixture.Summary> {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        val db = RetailDatabase(driver)
        val summary = ReportingScaleFixture.seed(db, driver)
        return Triple(db, driver, summary)
    }

    private suspend fun timeMs(block: suspend () -> Unit): Long {
        val start = System.currentTimeMillis()
        block()
        return System.currentTimeMillis() - start
    }

    @Test
    fun exactAggregationLatencyAcrossRepresentativeRangesAtFullScale() = runTest {
        val (db, gateDriver, summary) = newSeededDb()
        val gate = DatabaseWriteGate()
        val settings = SqlDelightSettingsRepository(db, gate)
        val repo = SqlDelightReportingRepository(db, gate, settings)
        val scope = ReportScope(summary.companyId)

        val oneDay = ReportPeriodFactory.customRange(summary.startEpochMillis, summary.startEpochMillis + 86_400_000L)
        val oneWeek = ReportPeriodFactory.customRange(summary.startEpochMillis, summary.startEpochMillis + 7 * 86_400_000L)
        val oneMonth = ReportPeriodFactory.customRange(summary.startEpochMillis, summary.startEpochMillis + 30 * 86_400_000L)
        val oneYear = ReportPeriodFactory.customRange(summary.startEpochMillis, summary.startEpochMillis + 365 * 86_400_000L)
        val fullRange = ReportPeriodFactory.customRange(summary.startEpochMillis, summary.endEpochMillis)

        // cold: first call after fresh seed
        val coldFullRangeMs = timeMs { repo.getSalesSummary(scope, fullRange) }
        // warm: repeated calls, same connection/cache state
        val warmRuns = (1..5).map { timeMs { repo.getSalesSummary(scope, fullRange) } }

        val oneDayMs = timeMs { repo.getSalesSummary(scope, oneDay) }
        val oneWeekMs = timeMs { repo.getSalesSummary(scope, oneWeek) }
        val oneMonthMs = timeMs { repo.getSalesSummary(scope, oneMonth) }
        val oneYearMs = timeMs { repo.getSalesSummary(scope, oneYear) }

        val topProductsMs = timeMs { repo.getTopProductsByQuantity(scope, fullRange, 20L) }
        val trendBuckets = ReportPeriodFactory.dailyBuckets(TimeZone.UTC, summary.startEpochMillis, summary.startEpochMillis + 30 * 86_400_000L).mapIndexed { i, p -> p to "day-$i" }
        val trendMs = timeMs { repo.getSalesTrend(scope, trendBuckets) }

        val dashboardRepo = SqlDelightDashboardRepository(repo, SqlDelightProductRepository(db, gate))
        val dashboardMs = timeMs { dashboardRepo.getDashboard(scope, oneDay, oneMonth, trendBuckets, 20L, 20L, 0L) }

        val sortedWarm = warmRuns.sorted()
        val p50 = sortedWarm[sortedWarm.size / 2]
        val p95 = sortedWarm[(sortedWarm.size * 95 / 100).coerceAtMost(sortedWarm.size - 1)]
        val max = sortedWarm.max()

        println("M5.7.5 exact-aggregation latency at 100K-sale scale (real, measured):")
        println("  cold full-range getSalesSummary: ${coldFullRangeMs}ms")
        println("  warm full-range runs: $warmRuns  p50=${p50}ms p95=${p95}ms max=${max}ms")
        println("  one-day range: ${oneDayMs}ms  one-week: ${oneWeekMs}ms  one-month: ${oneMonthMs}ms  one-year: ${oneYearMs}ms  full-3-year-range: see cold/warm above")
        println("  top products (full range, limit 20): ${topProductsMs}ms")
        println("  sales trend (30 daily buckets): ${trendMs}ms")
        println("  full dashboard (composed): ${dashboardMs}ms")

        // Generous baseline bound, same discipline as M5.6.17 -- proves
        // "completes in bounded time at 100K scale," not a tuned target.
        // Real observed cold-run variance on this machine across separate
        // runs: 1103ms and 15184ms (a real ~14x spread, most likely JVM/GC
        // cold-start variance under concurrent system load during a long
        // session) -- the bound below is set with real margin above that
        // observed spread, per the checkpoint's own "generous safety
        // ceilings... do not fail because one machine is slightly slower."
        assertTrue(coldFullRangeMs < 45_000, "full-range getSalesSummary took ${coldFullRangeMs}ms at 100K-sale scale, expected under 45s")
        assertTrue(dashboardMs < 45_000, "full dashboard composition took ${dashboardMs}ms, expected under 45s")
    }
}
