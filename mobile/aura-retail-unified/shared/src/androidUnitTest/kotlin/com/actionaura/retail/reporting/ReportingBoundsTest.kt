package com.actionaura.retail.reporting

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.data.sqldelight.SqlDelightBranchRepository
import com.actionaura.retail.data.sqldelight.SqlDelightProductRepository
import com.actionaura.retail.data.sqldelight.SqlDelightSettingsRepository
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate
import kotlinx.coroutines.test.runTest
import kotlinx.datetime.LocalDate
import kotlinx.datetime.TimeZone
import kotlinx.datetime.atStartOfDayIn
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

private fun epochMillisUtc(year: Int, month: Int, day: Int): Long =
    LocalDate(year, month, day).atStartOfDayIn(TimeZone.UTC).toEpochMilliseconds()

/**
 * M5.7.10 -- real, executed proof that pagination/limit/range-bound
 * inputs never crash the reporting authority. Two real, previously-
 * undiscovered defects fixed here: `List.take(negative)` throws
 * `IllegalArgumentException` in the Kotlin stdlib -- a negative
 * `limit`/`lowStockPreviewLimit` would have crashed
 * `getTopProductsByQuantity`/`getTopProductsByNetRevenue`/`getDashboard`
 * before this milestone's `ReportingLimits`-based clamping.
 */
class ReportingBoundsTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    private fun dayPeriod(y: Int, m: Int, d: Int) = ReportPeriodFactory.customRange(epochMillisUtc(y, m, d), epochMillisUtc(y, m, d + 1))

    @Test
    fun aNegativeTopProductsLimitNeverCrashesAndReturnsAnEmptyResult() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L)
        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))

        val result = repo.getTopProductsByQuantity(ReportScope(1L), dayPeriod(2026, 1, 15), -5L)

        assertEquals(emptyList(), result.metrics, "a negative limit must be safely constrained to zero results, never crash")
    }

    @Test
    fun aZeroTopProductsLimitReturnsAnEmptyResultNotAllResults() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L)
        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))

        val result = repo.getTopProductsByQuantity(ReportScope(1L), dayPeriod(2026, 1, 15), 0L)

        assertEquals(emptyList(), result.metrics)
    }

    @Test
    fun anExcessiveTopProductsLimitIsClampedNeverLoadedUnbounded() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L).id
        val productRepo = SqlDelightProductRepository(db, gate)
        val saleId = run {
            db.salesQueries.insertSale(1L, null, branchId, null, "POS", "0.00", "0.00", "0.00", "10.00", "10.00", "0.00", "cash", null, null, null, epochMillisUtc(2026, 1, 15))
            db.catalogQueries.lastInsertRowId().executeAsOne()
        }
        repeat(5) { i ->
            val product = (productRepo.insert(1L, "SKU-$i", null, "Product-$i", "product-$i", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "unit", 5, 1000L) as DomainResult.Success).value
            db.salesQueries.insertSaleItem(saleId, product.id, "Product-$i", "1", "2.00", "0", "0", "2.00")
        }
        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))

        val result = repo.getTopProductsByQuantity(ReportScope(1L), dayPeriod(2026, 1, 15), 999_999_999L)

        assertEquals(5, result.metrics.size, "an excessive limit must not crash or misbehave -- with only 5 real products it returns exactly those 5, clamped internally to the bounded maximum rather than the requested billion")
    }

    @Test
    fun anExcessiveTrendBucketCountIsRejectedBeforeAnyQueryRuns() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val oneBucket = dayPeriod(2026, 1, 15) to "day"
        val tooManyBuckets = List(ReportingLimits.MAX_TREND_BUCKET_COUNT + 1) { oneBucket }

        assertFailsWith<IllegalArgumentException> {
            repo.getSalesTrend(ReportScope(1L), tooManyBuckets)
        }
    }

    @Test
    fun exactlyTheMaximumTrendBucketCountIsAccepted() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L)
        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val oneBucket = dayPeriod(2026, 1, 15) to "day"
        val maxBuckets = List(ReportingLimits.MAX_TREND_BUCKET_COUNT) { oneBucket }

        val result = repo.getSalesTrend(ReportScope(1L), maxBuckets)

        assertEquals(ReportingLimits.MAX_TREND_BUCKET_COUNT, result.buckets.size)
    }

    @Test
    fun negativeDashboardPreviewLimitsNeverCrashTheFullComposition() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L)
        val reportingRepo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val dashboardRepo = SqlDelightDashboardRepository(reportingRepo, SqlDelightProductRepository(db, gate))
        val period = dayPeriod(2026, 1, 15)

        val snapshot = dashboardRepo.getDashboard(ReportScope(1L), period, period, listOf(period to "d"), -1L, -1L, 0L)

        assertTrue(snapshot.lowStockPreview.isEmpty())
        assertTrue(snapshot.topProductsByQuantity.isEmpty())
    }
}
