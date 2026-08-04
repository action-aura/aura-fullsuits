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

private fun epochMillisUtc(year: Int, month: Int, day: Int): Long =
    LocalDate(year, month, day).atStartOfDayIn(TimeZone.UTC).toEpochMilliseconds()

/**
 * M5.6.13 -- remaining real financial-correctness matrix cases not
 * already covered by `SqlDelightReportingRepositoryTest` (19.99+19.99
 * gross, unclamped negative net) or `SqliteTextMoneyAggregationTest`
 * (M5.6.1's own proof that SQL SUM/AVG is unsafe -- this file proves the
 * Kotlin-side accumulation this repository actually uses instead is
 * exact).
 */
class FinancialCorrectnessMatrixTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    private fun seedSale(db: RetailDatabase, companyId: Long, branchId: Long?, total: String, createdAt: Long): Long {
        db.salesQueries.insertSale(companyId, null, branchId, null, "POS", "0.00", "0.00", "0.00", total, total, "0.00", "cash", null, null, null, createdAt)
        return db.catalogQueries.lastInsertRowId().executeAsOne()
    }

    private fun seedReturn(db: RetailDatabase, companyId: Long, branchId: Long?, refundAmount: String, createdAt: Long): Long {
        db.returnsQueries.insertReturn(companyId, null, null, branchId, "POS", null, "cash", refundAmount, null, createdAt)
        return db.catalogQueries.lastInsertRowId().executeAsOne()
    }

    private fun dayPeriod(y: Int, m: Int, d: Int) = ReportPeriodFactory.customRange(epochMillisUtc(y, m, d), epochMillisUtc(y, m, d + 1))

    @Test
    fun cleanSubtractionOfReturnsFromGrossIsExact() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L).id
        seedSale(db, 1L, branchId, "59.97", epochMillisUtc(2026, 1, 15))
        seedReturn(db, 1L, branchId, "19.99", epochMillisUtc(2026, 1, 15))

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val totals = repo.getSalesSummary(ReportScope(1L), dayPeriod(2026, 1, 15)).totalsByCurrency.getValue(CurrencyCode("USD"))

        assertEquals(Money.of(39.98), totals.netSales)
    }

    @Test
    fun manySmallLegacyMigratedValuesAccumulateExactlyNeverBinaryFloatDrift() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L).id
        // The classic float-drift trap: 1000 sales of "0.10" each. A naive
        // Double accumulation famously lands on 99.99999999999986, not
        // 100.00 -- this repository must never do that (exact-report-
        // aggregation-decision.md).
        repeat(1000) { seedSale(db, 1L, branchId, "0.10", epochMillisUtc(2026, 1, 15)) }

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val totals = repo.getSalesSummary(ReportScope(1L), dayPeriod(2026, 1, 15)).totalsByCurrency.getValue(CurrencyCode("USD"))

        assertEquals(Money.of(100.00), totals.grossSales)
        assertEquals(1000L, totals.transactionCount)
    }

    @Test
    fun twoCompaniesWithDifferentConfiguredCurrenciesNeverContaminateEachOthersTotals() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val settings = SqlDelightSettingsRepository(db, gate)
        settings.setSetting(1L, "base_currency", "USD")
        settings.setSetting(2L, "base_currency", "EUR")
        val branch1 = SqlDelightBranchRepository(db, gate).insert(1L, "Company 1 Main", null, null, 500L).id
        val branch2 = SqlDelightBranchRepository(db, gate).insert(2L, "Company 2 Main", null, null, 500L).id
        seedSale(db, 1L, branch1, "70.00", epochMillisUtc(2026, 1, 15))
        seedSale(db, 2L, branch2, "35.00", epochMillisUtc(2026, 1, 15))

        val repo = SqlDelightReportingRepository(db, gate, settings)
        val company1Totals = repo.getSalesSummary(ReportScope(1L), dayPeriod(2026, 1, 15))
        val company2Totals = repo.getSalesSummary(ReportScope(2L), dayPeriod(2026, 1, 15))

        assertEquals(setOf(CurrencyCode("USD")), company1Totals.totalsByCurrency.keys)
        assertEquals(Money.of(70.00), company1Totals.totalsByCurrency.getValue(CurrencyCode("USD")).grossSales)
        assertEquals(setOf(CurrencyCode("EUR")), company2Totals.totalsByCurrency.keys)
        assertEquals(Money.of(35.00), company2Totals.totalsByCurrency.getValue(CurrencyCode("EUR")).grossSales)
    }
}
