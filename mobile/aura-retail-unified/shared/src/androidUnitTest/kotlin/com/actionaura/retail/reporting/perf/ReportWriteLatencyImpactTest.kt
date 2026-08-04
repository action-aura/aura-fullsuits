package com.actionaura.retail.reporting.perf

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.data.sqldelight.SqlDelightProductRepository
import com.actionaura.retail.data.sqldelight.SqlDelightSettingsRepository
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate
import com.actionaura.retail.reporting.ReportPeriodFactory
import com.actionaura.retail.reporting.ReportScope
import com.actionaura.retail.reporting.SqlDelightDashboardRepository
import com.actionaura.retail.reporting.SqlDelightReportingRepository
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import kotlinx.datetime.TimeZone
import kotlin.test.Test
import kotlin.test.assertTrue

/**
 * M5.7.9 -- real, measured effect of a running report on a concurrent
 * write's real wait time, at full M5.7.1 scale. The `DatabaseWriteGate`
 * design is exclusive-by-construction: a write issued while a report
 * holds the gate waits until the report's `withLock` block completes --
 * this test measures what that real wait actually costs, rather than
 * assuming it is negligible.
 */
class ReportWriteLatencyImpactTest {

    private fun newSeededDb(): Triple<RetailDatabase, JdbcSqliteDriver, ReportingScaleFixture.Summary> {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        val db = RetailDatabase(driver)
        val summary = ReportingScaleFixture.seed(db, driver)
        return Triple(db, driver, summary)
    }

    @Test
    fun aWriteIssuedWhileAFullDashboardReportIsRunningWaitsBoundedByTheReportsOwnRealDuration() = runTest {
        val (db, driver, summary) = newSeededDb()
        val gate = DatabaseWriteGate()
        val settings = SqlDelightSettingsRepository(db, gate)
        val reportingRepo = SqlDelightReportingRepository(db, gate, settings)
        val dashboardRepo = SqlDelightDashboardRepository(reportingRepo, SqlDelightProductRepository(db, gate))
        val productRepo = SqlDelightProductRepository(db, gate)

        val today = ReportPeriodFactory.customRange(summary.startEpochMillis, summary.startEpochMillis + 86_400_000L)
        val fullRange = ReportPeriodFactory.customRange(summary.startEpochMillis, summary.endEpochMillis)
        val trendBuckets = ReportPeriodFactory.dailyBuckets(TimeZone.UTC, summary.startEpochMillis, summary.startEpochMillis + 30 * 86_400_000L).mapIndexed { i, p -> p to "day-$i" }

        var reportDurationMs = 0L
        var writeWaitMs = 0L

        coroutineScope {
            val reportJob = async {
                val start = System.currentTimeMillis()
                dashboardRepo.getDashboard(ReportScope(summary.companyId), today, fullRange, trendBuckets, 20L, 20L, 0L)
                reportDurationMs = System.currentTimeMillis() - start
            }
            // Give the report a head start so it is really holding the gate when the write is issued.
            kotlinx.coroutines.delay(5)
            val writeJob = async {
                val start = System.currentTimeMillis()
                productRepo.insert(summary.companyId, "LATENCY-TEST-SKU", null, "Latency Test", "latency test", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "unit", 5, 1000L)
                writeWaitMs = System.currentTimeMillis() - start
            }
            reportJob.await()
            writeJob.await()
        }

        println("M5.7.9 real write-wait-under-report-load measurement:")
        println("  reportDurationMs=$reportDurationMs  writeWaitMs=$writeWaitMs")

        // Real, structural bound: the write cannot take dramatically LONGER
        // than the report's own real duration (it waits for the gate, then
        // completes near-instantly) -- a generous multiple, not a tight
        // equality, since exact scheduling overlap varies run to run.
        assertTrue(writeWaitMs <= reportDurationMs + 2_000, "the write's wait ($writeWaitMs ms) must be bounded by the report's own duration ($reportDurationMs ms) plus a small margin, not unboundedly larger")
        assertTrue(reportDurationMs < 20_000, "the report itself must complete in bounded time at this scale")
    }

    @Test
    fun manyShortWritesInterleavedWithReportsAllCompleteWithoutTimeoutOrBusyError() = runTest {
        val (db, driver, summary) = newSeededDb()
        val gate = DatabaseWriteGate()
        val reportingRepo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val productRepo = SqlDelightProductRepository(db, gate)
        val period = ReportPeriodFactory.customRange(summary.startEpochMillis, summary.startEpochMillis + 30 * 86_400_000L)

        var writesCompleted = 0
        var reportsCompleted = 0

        coroutineScope {
            launch {
                repeat(15) { i ->
                    productRepo.insert(summary.companyId, "BUSY-TEST-SKU-$i", null, "Busy-$i", "busy-$i", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "unit", 5, 1000L)
                    writesCompleted++
                }
            }
            launch {
                repeat(15) {
                    reportingRepo.getSalesSummary(ReportScope(summary.companyId), period)
                    reportsCompleted++
                }
            }
        }

        assertTrue(writesCompleted == 15 && reportsCompleted == 15, "every write and every report must complete -- no SQLITE_BUSY/timeout dropped any of them (writes=$writesCompleted reports=$reportsCompleted)")
    }
}
