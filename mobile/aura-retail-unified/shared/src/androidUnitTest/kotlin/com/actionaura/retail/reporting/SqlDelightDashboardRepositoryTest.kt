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
import com.actionaura.retail.financial.Quantity
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
 * M5.6.8/M5.6.9 -- real, executed proof of the canonical Dashboard
 * authority: it delegates every figure to `ReportingRepository`/
 * `ProductRepository` rather than recomputing anything itself
 * (`dashboard-authority-contract.md`).
 */
class SqlDelightDashboardRepositoryTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    private fun seedSale(db: RetailDatabase, branchId: Long?, total: String, createdAt: Long): Long {
        db.salesQueries.insertSale(1L, null, branchId, null, "POS", "0.00", "0.00", "0.00", total, total, "0.00", "cash", null, null, null, createdAt)
        return db.catalogQueries.lastInsertRowId().executeAsOne()
    }

    @Test
    fun dashboardComposesTodayAndSelectedPeriodExactlyMatchingTheReportingRepositoryItDelegatesTo() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L).id
        seedSale(db, branchId, "20.00", epochMillisUtc(2026, 1, 15))
        seedSale(db, branchId, "5.00", epochMillisUtc(2026, 1, 10))

        val settings = SqlDelightSettingsRepository(db, gate)
        val reportingRepo = SqlDelightReportingRepository(db, gate, settings)
        val productRepo = SqlDelightProductRepository(db, gate)
        val dashboardRepo = SqlDelightDashboardRepository(reportingRepo, productRepo)

        val today = ReportPeriodFactory.customRange(epochMillisUtc(2026, 1, 15), epochMillisUtc(2026, 1, 16))
        val selected = ReportPeriodFactory.customRange(epochMillisUtc(2026, 1, 1), epochMillisUtc(2026, 1, 16))
        val trendBuckets = listOf(today to "today")

        val snapshot = dashboardRepo.getDashboard(ReportScope(1L), today, selected, trendBuckets, 5L, 5L, 999_000L)

        val expectedToday = reportingRepo.getSalesSummary(ReportScope(1L), today).totalsByCurrency.getValue(CurrencyCode("USD"))
        val expectedSelected = reportingRepo.getSalesSummary(ReportScope(1L), selected).totalsByCurrency.getValue(CurrencyCode("USD"))

        assertEquals(expectedToday, snapshot.today, "the dashboard's 'today' figure must exactly match ReportingRepository.getSalesSummary, never a separately computed value")
        assertEquals(expectedSelected, snapshot.selectedPeriod)
        assertEquals(999_000L, snapshot.lastRefreshedEpochMillis, "snapshot time must come from the injected clock parameter, never a live system clock read")
    }

    @Test
    fun lowStockCountAndPreviewExactlyMatchProductRepositoryListLowStock() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val productRepo = SqlDelightProductRepository(db, gate)
        val branchId = SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L).id
        val low = (productRepo.insert(1L, "SKU-LOW", null, "Low Stock Item", "low stock item", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "unit", 10, 1000L) as DomainResult.Success).value
        val ok = (productRepo.insert(1L, "SKU-OK", null, "Well Stocked Item", "well stocked item", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "unit", 5, 1000L) as DomainResult.Success).value
        val inventoryRepo = com.actionaura.retail.data.sqldelight.SqlDelightInventoryRepository(db, gate)
        inventoryRepo.ensureOpeningStock(1L, low.id, branchId, Quantity.zeroOrMore("2")!!)
        inventoryRepo.ensureOpeningStock(1L, ok.id, branchId, Quantity.zeroOrMore("50")!!)

        val settings = SqlDelightSettingsRepository(db, gate)
        val reportingRepo = SqlDelightReportingRepository(db, gate, settings)
        val dashboardRepo = SqlDelightDashboardRepository(reportingRepo, productRepo)

        val period = ReportPeriodFactory.customRange(epochMillisUtc(2026, 1, 1), epochMillisUtc(2026, 1, 2))
        val expectedLowStock = productRepo.listLowStock(1L)
        val snapshot = dashboardRepo.getDashboard(ReportScope(1L), period, period, listOf(period to "p"), 5L, 10L, 0L)

        assertEquals(expectedLowStock.size.toLong(), snapshot.lowStockCount)
        assertEquals(expectedLowStock.map { it.product.id }.toSet(), snapshot.lowStockPreview.map { it.productId }.toSet())
        assertTrue(snapshot.lowStockPreview.any { it.productName == "Low Stock Item" })
        assertTrue(snapshot.lowStockPreview.none { it.productName == "Well Stocked Item" })
    }

    @Test
    fun topProductsOnTheDashboardExactlyMatchTheReportingRepositoryRankings() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L).id
        val productRepo = SqlDelightProductRepository(db, gate)
        val product = (productRepo.insert(1L, "SKU-T", null, "Top Item", "top item", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "unit", 5, 1000L) as DomainResult.Success).value
        val saleId = seedSale(db, branchId, "10.00", epochMillisUtc(2026, 1, 15))
        db.salesQueries.insertSaleItem(saleId, product.id, "Top Item", "3", "10.00", "0", "0", "10.00")

        val settings = SqlDelightSettingsRepository(db, gate)
        val reportingRepo = SqlDelightReportingRepository(db, gate, settings)
        val dashboardRepo = SqlDelightDashboardRepository(reportingRepo, productRepo)
        val period = ReportPeriodFactory.customRange(epochMillisUtc(2026, 1, 15), epochMillisUtc(2026, 1, 16))

        val snapshot = dashboardRepo.getDashboard(ReportScope(1L), period, period, listOf(period to "p"), 5L, 5L, 0L)
        val expectedByRevenue = reportingRepo.getTopProductsByNetRevenue(ReportScope(1L), period, 5L).metrics

        assertEquals(expectedByRevenue, snapshot.topProductsByNetRevenue)
    }
}
