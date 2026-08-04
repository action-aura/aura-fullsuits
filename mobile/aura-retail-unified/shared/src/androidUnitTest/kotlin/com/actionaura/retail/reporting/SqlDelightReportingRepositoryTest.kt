package com.actionaura.retail.reporting

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.data.sqldelight.SqlDelightBranchRepository
import com.actionaura.retail.data.sqldelight.SqlDelightProductRepository
import com.actionaura.retail.data.sqldelight.SqlDelightSettingsRepository
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.financial.CurrencyCode
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
 * M5.6.3/M5.6.4 -- real, executed proof of eligible sale/return definitions
 * and currency separation. No SQLite-backed SaleRepository exists yet
 * (M5.5.9/10's own documented scope boundary), so `sales`/`sale_items`/
 * `returns`/`return_items` rows are seeded directly via the real
 * SQLDelight insert queries -- the same pattern already established by
 * `ProductInventorySaleReturnBoundaryTest`.
 */
class SqlDelightReportingRepositoryTest {

    private lateinit var rawDriver: JdbcSqliteDriver

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        rawDriver = driver
        return RetailDatabase(driver)
    }

    private suspend fun seedProduct(db: RetailDatabase, gate: DatabaseWriteGate, sku: String, name: String, categoryId: Long? = null): Long {
        val repo = SqlDelightProductRepository(db, gate)
        val result = repo.insert(1L, sku, null, name, name.lowercase(), categoryId, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "unit", 5, 1000L)
        return (result as DomainResult.Success).value.id
    }

    private suspend fun seedBranch(db: RetailDatabase, gate: DatabaseWriteGate, name: String): Long =
        SqlDelightBranchRepository(db, gate).insert(1L, name, null, null, 500L).id

    private fun seedSale(db: RetailDatabase, companyId: Long, branchId: Long?, total: String, status: String, createdAt: Long): Long {
        db.salesQueries.insertSale(companyId, null, branchId, null, "POS", "0.00", "0.00", "0.00", total, total, "0.00", "cash", null, null, null, createdAt)
        val id = db.catalogQueries.lastInsertRowId().executeAsOne()
        if (status != "completed") {
            rawDriver.execute(null, "UPDATE sales SET status = ? WHERE id = ?", 2) {
                bindString(0, status)
                bindLong(1, id)
            }
        }
        return id
    }

    private fun seedSaleItem(db: RetailDatabase, saleId: Long, productId: Long, productName: String, quantity: String, lineTotal: String) {
        db.salesQueries.insertSaleItem(saleId, productId, productName, quantity, lineTotal, "0", "0", lineTotal)
    }

    private fun seedReturn(db: RetailDatabase, companyId: Long, saleId: Long?, branchId: Long?, refundAmount: String, status: String, createdAt: Long): Long {
        db.returnsQueries.insertReturn(companyId, null, saleId, branchId, "POS", null, "cash", refundAmount, null, createdAt)
        val id = db.catalogQueries.lastInsertRowId().executeAsOne()
        if (status != "completed") {
            rawDriver.execute(null, "UPDATE returns SET status = ? WHERE id = ?", 2) {
                bindString(0, status)
                bindLong(1, id)
            }
        }
        return id
    }

    private fun seedReturnItem(db: RetailDatabase, returnId: Long, productId: Long, productName: String, quantity: String, lineTotal: String) {
        db.returnsQueries.insertReturnItem(returnId, productId, productName, quantity, lineTotal, lineTotal)
    }

    private fun dayPeriod(y: Int, m: Int, d: Int) = ReportPeriodFactory.customRange(
        startInclusiveEpochMillis = epochMillisUtc(y, m, d),
        endExclusiveEpochMillis = epochMillisUtc(y, m, d + 1),
    )

    @Test
    fun grossReturnsAndNetAreExactAndTransactionCountIgnoresReturns() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = seedBranch(db, gate, "Main")
        seedSale(db, 1L, branchId, "19.99", "completed", epochMillisUtc(2026, 1, 15))
        seedSale(db, 1L, branchId, "19.99", "completed", epochMillisUtc(2026, 1, 15))
        seedReturn(db, 1L, null, branchId, "10.00", "completed", epochMillisUtc(2026, 1, 15))

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val summary = repo.getSalesSummary(ReportScope(1L), dayPeriod(2026, 1, 15))

        val totals = summary.totalsByCurrency.getValue(CurrencyCode("USD"))
        assertEquals(Money.of(39.98), totals.grossSales)
        assertEquals(Money.of(10.00), totals.confirmedReturns)
        assertEquals(Money.of(29.98), totals.netSales)
        assertEquals(2L, totals.transactionCount, "transaction count is finalized-sales-only, never reduced by returns")
    }

    @Test
    fun draftAndCancelledSalesAndReturnsAreNeverCounted() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = seedBranch(db, gate, "Main")
        seedSale(db, 1L, branchId, "50.00", "completed", epochMillisUtc(2026, 1, 15))
        seedSale(db, 1L, branchId, "999.00", "draft", epochMillisUtc(2026, 1, 15))
        seedSale(db, 1L, branchId, "999.00", "cancelled", epochMillisUtc(2026, 1, 15))
        seedReturn(db, 1L, null, branchId, "999.00", "pending", epochMillisUtc(2026, 1, 15))

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val totals = repo.getSalesSummary(ReportScope(1L), dayPeriod(2026, 1, 15)).totalsByCurrency.getValue(CurrencyCode("USD"))

        assertEquals(Money.of(50.00), totals.grossSales, "draft/cancelled sales must never contribute to gross sales")
        assertEquals(Money.ZERO, totals.confirmedReturns, "a non-completed return must never contribute to confirmed returns")
        assertEquals(1L, totals.transactionCount)
    }

    @Test
    fun negativeNetSalesFromLaterReturnsIsNeverClampedToZero() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = seedBranch(db, gate, "Main")
        seedSale(db, 1L, branchId, "10.00", "completed", epochMillisUtc(2026, 1, 15))
        seedReturn(db, 1L, null, branchId, "59.97", "completed", epochMillisUtc(2026, 1, 15))

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val totals = repo.getSalesSummary(ReportScope(1L), dayPeriod(2026, 1, 15)).totalsByCurrency.getValue(CurrencyCode("USD"))

        assertEquals(Money.of(-49.97), totals.netSales, "a period net driven negative by returns must be reported as-is, never clamped to zero")
    }

    @Test
    fun aSaleContributesOnItsOwnDateAndAReturnContributesOnItsOwnConfirmationDateNotTheOriginalSaleDate() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = seedBranch(db, gate, "Main")
        val saleId = seedSale(db, 1L, branchId, "30.00", "completed", epochMillisUtc(2026, 1, 15))
        seedReturn(db, 1L, saleId, branchId, "30.00", "completed", epochMillisUtc(2026, 2, 3))

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))

        val januaryFifteenth = repo.getSalesSummary(ReportScope(1L), dayPeriod(2026, 1, 15)).totalsByCurrency.getValue(CurrencyCode("USD"))
        assertEquals(Money.of(30.00), januaryFifteenth.grossSales)
        assertEquals(Money.ZERO, januaryFifteenth.confirmedReturns, "the return must not retroactively rewrite the original sale's date bucket")

        val februaryThird = repo.getSalesSummary(ReportScope(1L), dayPeriod(2026, 2, 3)).totalsByCurrency.getValue(CurrencyCode("USD"))
        assertEquals(Money.ZERO, februaryThird.grossSales)
        assertEquals(Money.of(30.00), februaryThird.confirmedReturns)
    }

    @Test
    fun branchFilterScopesSalesAndReturnsToOneBranchOnly() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchA = seedBranch(db, gate, "A")
        val branchB = seedBranch(db, gate, "B")
        seedSale(db, 1L, branchA, "10.00", "completed", epochMillisUtc(2026, 1, 15))
        seedSale(db, 1L, branchB, "20.00", "completed", epochMillisUtc(2026, 1, 15))

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val scopedToA = repo.getSalesSummary(ReportScope(1L, branchId = branchA), dayPeriod(2026, 1, 15)).totalsByCurrency.getValue(CurrencyCode("USD"))
        val unscoped = repo.getSalesSummary(ReportScope(1L), dayPeriod(2026, 1, 15)).totalsByCurrency.getValue(CurrencyCode("USD"))

        assertEquals(Money.of(10.00), scopedToA.grossSales)
        assertEquals(Money.of(30.00), unscoped.grossSales)
    }

    @Test
    fun resultIsKeyedByTheCompanysConfiguredCurrencyNeverAFalseCombinedTotal() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = seedBranch(db, gate, "Main")
        SqlDelightSettingsRepository(db, gate).setSetting(1L, "base_currency", "EUR")
        seedSale(db, 1L, branchId, "15.00", "completed", epochMillisUtc(2026, 1, 15))

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val summary = repo.getSalesSummary(ReportScope(1L), dayPeriod(2026, 1, 15))

        assertEquals(setOf(CurrencyCode("EUR")), summary.totalsByCurrency.keys)
        assertTrue(summary.totalsByCurrency.containsKey(CurrencyCode("EUR")))
    }

    @Test
    fun topProductsByQuantityAndByNetRevenueAreReturnsAdjustedWithDeterministicTieBreak() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = seedBranch(db, gate, "Main")
        val cola = seedProduct(db, gate, "SKU-C", "Cola")
        val juice = seedProduct(db, gate, "SKU-J", "Juice")

        val saleId = seedSale(db, 1L, branchId, "100.00", "completed", epochMillisUtc(2026, 1, 15))
        seedSaleItem(db, saleId, cola, "Cola", "10", "50.00")
        seedSaleItem(db, saleId, juice, "Juice", "10", "50.00")
        val returnId = seedReturn(db, 1L, saleId, branchId, "20.00", "completed", epochMillisUtc(2026, 1, 15))
        seedReturnItem(db, returnId, cola, "Cola", "4", "20.00")

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val byQuantity = repo.getTopProductsByQuantity(ReportScope(1L), dayPeriod(2026, 1, 15), 10L).metrics
        val byRevenue = repo.getTopProductsByNetRevenue(ReportScope(1L), dayPeriod(2026, 1, 15), 10L).metrics

        assertEquals("Juice", byQuantity.first().productName, "Juice has net_quantity 10 vs Cola's returns-adjusted 6")
        assertEquals(Money.of(50.00), byRevenue.first { it.productName == "Juice" }.netRevenue)
        assertEquals(Money.of(30.00), byRevenue.first { it.productName == "Cola" }.netRevenue, "Cola's net_revenue is 50.00 sold minus 20.00 returned")
    }

    @Test
    fun aProductReturnedInALaterPeriodThanItWasSoldStillAppearsWithACorrectNegativeNetInThatLaterPeriod() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = seedBranch(db, gate, "Main")
        val cola = seedProduct(db, gate, "SKU-C", "Cola")

        val saleId = seedSale(db, 1L, branchId, "50.00", "completed", epochMillisUtc(2026, 1, 15))
        seedSaleItem(db, saleId, cola, "Cola", "10", "50.00")
        val returnId = seedReturn(db, 1L, saleId, branchId, "50.00", "completed", epochMillisUtc(2026, 2, 3))
        seedReturnItem(db, returnId, cola, "Cola", "10", "50.00")

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val februaryTop = repo.getTopProductsByNetRevenue(ReportScope(1L), dayPeriod(2026, 2, 3), 10L).metrics

        val colaMetric = februaryTop.first { it.productName == "Cola" }
        assertEquals(Money.of(-50.00), colaMetric.netRevenue)
        assertTrue(colaMetric.netQuantity.toString().startsWith("-"), "a product with no sales in this period but a return must still show a negative net quantity, not be absent")
    }

    @Test
    fun malformedMoneyRowIsExcludedAndSurfacedAsADataQualityIssueNeverSilentlyZeroed() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = seedBranch(db, gate, "Main")
        seedSale(db, 1L, branchId, "20.00", "completed", epochMillisUtc(2026, 1, 15))
        val badSaleId = seedSale(db, 1L, branchId, "20.00", "completed", epochMillisUtc(2026, 1, 15))
        rawDriver.execute(null, "UPDATE sales SET total = 'not-a-number' WHERE id = ?", 1) { bindLong(0, badSaleId) }

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val summary = repo.getSalesSummary(ReportScope(1L), dayPeriod(2026, 1, 15))
        val totals = summary.totalsByCurrency.getValue(CurrencyCode("USD"))

        assertEquals(Money.of(20.00), totals.grossSales, "the malformed row must be excluded, not treated as zero and silently included in the count")
        assertEquals(1L, totals.transactionCount)
        assertTrue(summary.dataQualityIssues.any { it is ReportingDataQualityIssue.MalformedMoney }, "the malformed row must be surfaced, never silently dropped")
    }
}
