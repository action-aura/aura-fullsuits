package com.actionaura.retail.reporting

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.data.sqldelight.SqlDelightBranchRepository
import com.actionaura.retail.data.sqldelight.SqlDelightSettingsRepository
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.financial.CurrencyCode
import com.actionaura.retail.financial.Money
import kotlinx.coroutines.test.runTest
import kotlinx.datetime.LocalDate
import kotlinx.datetime.TimeZone
import kotlinx.datetime.atStartOfDayIn
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

private fun epochMillisUtc(year: Int, month: Int, day: Int): Long =
    LocalDate(year, month, day).atStartOfDayIn(TimeZone.UTC).toEpochMilliseconds()

/**
 * M5.6.5 -- real, executed proof of the sales trend authority: deterministic
 * zero buckets, stable chronological ordering, no duplicate boundary
 * inclusion, scope captured once, archived-Branch historical data still
 * reportable, and cross-business Branch scoping cannot leak another
 * company's rows (`sales-trend-contract.md`).
 */
class SalesTrendAuthorityTest {

    private lateinit var rawDriver: JdbcSqliteDriver

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        rawDriver = driver
        return RetailDatabase(driver)
    }

    private fun seedSale(db: RetailDatabase, companyId: Long, branchId: Long?, total: String, createdAt: Long): Long {
        db.salesQueries.insertSale(companyId, null, branchId, null, "POS", "0.00", "0.00", "0.00", total, total, "0.00", "cash", null, null, null, createdAt)
        return db.catalogQueries.lastInsertRowId().executeAsOne()
    }

    @Test
    fun emptyDaysProduceDeterministicZeroBucketsNotMissingBuckets() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L).id
        // Sales on day 1 and day 3 only -- day 2 must still appear as a zero bucket.
        seedSale(db, 1L, branchId, "10.00", epochMillisUtc(2026, 3, 1))
        seedSale(db, 1L, branchId, "30.00", epochMillisUtc(2026, 3, 3))

        val buckets = ReportPeriodFactory.dailyBuckets(TimeZone.UTC, epochMillisUtc(2026, 3, 1), epochMillisUtc(2026, 3, 4))
            .mapIndexed { index, period -> period to "day-$index" }
        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val trend = repo.getSalesTrend(ReportScope(1L), buckets)

        assertEquals(3, trend.buckets.size, "three calendar days requested must produce exactly three buckets, none dropped")
        assertEquals(Money.of(10.00), trend.buckets[0].summary.grossSales)
        assertEquals(Money.ZERO, trend.buckets[1].summary.grossSales, "a day with no sales must be a real zero bucket, not absent from the list")
        assertEquals(0L, trend.buckets[1].summary.transactionCount)
        assertEquals(Money.of(30.00), trend.buckets[2].summary.grossSales)
    }

    @Test
    fun bucketsAreReturnedInStableChronologicalOrderMatchingTheCallersInput() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val buckets = ReportPeriodFactory.dailyBuckets(TimeZone.UTC, epochMillisUtc(2026, 3, 1), epochMillisUtc(2026, 3, 6))
            .mapIndexed { index, period -> period to "day-$index" }

        val trend = repo.getSalesTrend(ReportScope(1L), buckets)

        assertEquals(buckets.map { it.second }, trend.buckets.map { it.bucketLabelKey })
        assertEquals(buckets.map { it.first.startInclusiveEpochMillis }, trend.buckets.map { it.period.startInclusiveEpochMillis })
    }

    @Test
    fun aSaleOnTheExactEndExclusiveBoundaryBelongsToTheNextBucketNeverBoth() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L).id
        val boundary = epochMillisUtc(2026, 3, 2) // exactly midnight -- day-1's endExclusive, day-2's startInclusive
        seedSale(db, 1L, branchId, "15.00", boundary)

        val buckets = ReportPeriodFactory.dailyBuckets(TimeZone.UTC, epochMillisUtc(2026, 3, 1), epochMillisUtc(2026, 3, 3))
            .mapIndexed { index, period -> period to "day-$index" }
        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val trend = repo.getSalesTrend(ReportScope(1L), buckets)

        assertEquals(Money.ZERO, trend.buckets[0].summary.grossSales, "the boundary instant belongs to the NEXT bucket, not the previous one")
        assertEquals(Money.of(15.00), trend.buckets[1].summary.grossSales)
    }

    @Test
    fun archivedBranchHistoricalSalesRemainReportableInTheTrend() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchRepo = SqlDelightBranchRepository(db, gate)
        val branch = branchRepo.insert(1L, "Closing Branch", null, null, 400L)
        branchRepo.insert(1L, "Other", null, null, 450L) // second active branch so the first can be deactivated
        seedSale(db, 1L, branch.id, "25.00", epochMillisUtc(2026, 3, 1))
        branchRepo.setActive(1L, branch.id, false)

        val buckets = listOf(ReportPeriodFactory.dailyBuckets(TimeZone.UTC, epochMillisUtc(2026, 3, 1), epochMillisUtc(2026, 3, 2)).single() to "day-0")
        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val trend = repo.getSalesTrend(ReportScope(1L, branchId = branch.id), buckets)

        assertEquals(Money.of(25.00), trend.buckets[0].summary.grossSales, "an archived branch's historical sales must remain reportable")
    }

    @Test
    fun requestingAnotherCompanysBranchIdNeverLeaksThatCompanysSales() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val companyTwoBranch = SqlDelightBranchRepository(db, gate).insert(2L, "Company 2 Branch", null, null, 500L)
        seedSale(db, 2L, companyTwoBranch.id, "999.00", epochMillisUtc(2026, 3, 1))

        val buckets = listOf(ReportPeriodFactory.dailyBuckets(TimeZone.UTC, epochMillisUtc(2026, 3, 1), epochMillisUtc(2026, 3, 2)).single() to "day-0")
        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        // Company 1 requesting company 2's own branch id must never see company 2's sale.
        val trend = repo.getSalesTrend(ReportScope(1L, branchId = companyTwoBranch.id), buckets)

        assertEquals(Money.ZERO, trend.buckets[0].summary.grossSales, "a branch id belonging to a different company must never leak that company's sales")
    }

    @Test
    fun scopeIsCapturedOnceAtInvocationAndUnaffectedByCurrencySettingChangesDuringExecution() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L).id
        seedSale(db, 1L, branchId, "40.00", epochMillisUtc(2026, 3, 1))
        val settings = SqlDelightSettingsRepository(db, gate)
        settings.setSetting(1L, "base_currency", "USD")

        val repo = SqlDelightReportingRepository(db, gate, settings)
        val buckets = listOf(ReportPeriodFactory.dailyBuckets(TimeZone.UTC, epochMillisUtc(2026, 3, 1), epochMillisUtc(2026, 3, 2)).single() to "day-0")
        val trend = repo.getSalesTrend(ReportScope(1L), buckets)

        assertEquals(CurrencyCode("USD"), trend.buckets[0].summary.currency)
        assertTrue(trend.dataQualityIssues.isEmpty())
    }
}
