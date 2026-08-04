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
import kotlin.test.assertTrue

private fun epochMillisUtc(year: Int, month: Int, day: Int): Long =
    LocalDate(year, month, day).atStartOfDayIn(TimeZone.UTC).toEpochMilliseconds()

/**
 * M5.6.12 -- real, executed proof that malformed row data is excluded
 * from a computed total and surfaced as a `ReportingDataQualityIssue`,
 * never silently coerced to zero and folded into the result
 * (`reporting-data-quality-contract.md`).
 */
class ReportingDataQualityTest {

    private lateinit var rawDriver: JdbcSqliteDriver

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        rawDriver = driver
        return RetailDatabase(driver)
    }

    private suspend fun seedProduct(db: RetailDatabase, gate: DatabaseWriteGate, sku: String, name: String): Long {
        val repo = SqlDelightProductRepository(db, gate)
        val result = repo.insert(1L, sku, null, name, name.lowercase(), null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "unit", 5, 1000L)
        return (result as DomainResult.Success).value.id
    }

    private fun seedSale(db: RetailDatabase, branchId: Long?, createdAt: Long): Long {
        db.salesQueries.insertSale(1L, null, branchId, null, "POS", "0.00", "0.00", "0.00", "0.00", "0.00", "0.00", "cash", null, null, null, createdAt)
        return db.catalogQueries.lastInsertRowId().executeAsOne()
    }

    private fun dayPeriod(y: Int, m: Int, d: Int) = ReportPeriodFactory.customRange(epochMillisUtc(y, m, d), epochMillisUtc(y, m, d + 1))

    @Test
    fun malformedQuantityInASaleLineIsExcludedFromTopProductsAndSurfacedAsAnIssue() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L).id
        val good = seedProduct(db, gate, "SKU-G", "Good")
        val bad = seedProduct(db, gate, "SKU-B", "Bad")
        val saleId = seedSale(db, branchId, epochMillisUtc(2026, 1, 15))
        db.salesQueries.insertSaleItem(saleId, good, "Good", "3", "9.00", "0", "0", "9.00")
        db.salesQueries.insertSaleItem(saleId, bad, "Bad", "not-a-quantity", "9.00", "0", "0", "9.00")

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val result = repo.getTopProductsByQuantity(ReportScope(1L), dayPeriod(2026, 1, 15), 10L)

        assertEquals(listOf("Good"), result.metrics.map { it.productName }, "the malformed-quantity row must be excluded from the ranking entirely, never treated as quantity zero and included")
        assertTrue(result.dataQualityIssues.any { it is ReportingDataQualityIssue.MalformedQuantity }, "the malformed row must be surfaced, never silently dropped without a trace")
    }

    @Test
    fun malformedLineTotalInASaleLineIsExcludedFromNetRevenueAndSurfacedAsAnIssue() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L).id
        val bad = seedProduct(db, gate, "SKU-B2", "BadMoney")
        val saleId = seedSale(db, branchId, epochMillisUtc(2026, 1, 15))
        db.salesQueries.insertSaleItem(saleId, bad, "BadMoney", "2", "not-money", "0", "0", "not-money")

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val result = repo.getTopProductsByNetRevenue(ReportScope(1L), dayPeriod(2026, 1, 15), 10L)

        assertEquals(emptyList(), result.metrics, "a row with an unparseable line_total must be excluded, never counted as zero revenue")
        assertTrue(result.dataQualityIssues.any { it is ReportingDataQualityIssue.MalformedMoney })
    }

    @Test
    fun malformedRefundAmountInAReturnIsExcludedFromConfirmedReturnsAndSurfacedAsAnIssue() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L).id
        db.returnsQueries.insertReturn(1L, null, null, branchId, "POS", null, "cash", "not-money", null, epochMillisUtc(2026, 1, 15))

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val summary = repo.getSalesSummary(ReportScope(1L), dayPeriod(2026, 1, 15))
        val totals = summary.totalsByCurrency.values.first()

        assertEquals(Money.ZERO, totals.confirmedReturns, "a malformed refund_amount must never be coerced into the total")
        assertTrue(summary.dataQualityIssues.any { it is ReportingDataQualityIssue.MalformedMoney && it.table == "returns" })
    }
}
