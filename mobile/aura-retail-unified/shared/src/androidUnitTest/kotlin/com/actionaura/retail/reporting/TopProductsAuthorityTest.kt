package com.actionaura.retail.reporting

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.data.sqldelight.SqlDelightBranchRepository
import com.actionaura.retail.data.sqldelight.SqlDelightCategoryRepository
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

private fun epochMillisUtc(year: Int, month: Int, day: Int): Long =
    LocalDate(year, month, day).atStartOfDayIn(TimeZone.UTC).toEpochMilliseconds()

/**
 * M5.6.6/M5.6.7 -- real, executed proof of the top-products authority:
 * deterministic tie-break, bounded limit, CURRENT-category filtering
 * (`historical-category-reporting-decision.md`'s Option B), archived
 * Product history remains reportable, and immutable historical display
 * name via `product_name_at_sale` (`top-products-contract.md`).
 */
class TopProductsAuthorityTest {

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

    private fun seedSale(db: RetailDatabase, branchId: Long?, createdAt: Long): Long {
        db.salesQueries.insertSale(1L, null, branchId, null, "POS", "0.00", "0.00", "0.00", "0.00", "0.00", "0.00", "cash", null, null, null, createdAt)
        return db.catalogQueries.lastInsertRowId().executeAsOne()
    }

    private fun seedSaleItem(db: RetailDatabase, saleId: Long, productId: Long, productName: String, quantity: String, lineTotal: String) {
        db.salesQueries.insertSaleItem(saleId, productId, productName, quantity, lineTotal, "0", "0", lineTotal)
    }

    private fun dayPeriod(y: Int, m: Int, d: Int) = ReportPeriodFactory.customRange(epochMillisUtc(y, m, d), epochMillisUtc(y, m, d + 1))

    @Test
    fun tieOnMetricBreaksByProductNameThenById() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L).id
        val zebra = seedProduct(db, gate, "SKU-Z", "Zebra")
        val apple = seedProduct(db, gate, "SKU-A", "Apple")
        val saleId = seedSale(db, branchId, epochMillisUtc(2026, 1, 15))
        seedSaleItem(db, saleId, zebra, "Zebra", "5", "25.00")
        seedSaleItem(db, saleId, apple, "Apple", "5", "25.00")

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val topByQuantity = repo.getTopProductsByQuantity(ReportScope(1L), dayPeriod(2026, 1, 15), 10L).metrics

        assertEquals(listOf("Apple", "Zebra"), topByQuantity.map { it.productName }, "equal net_quantity must tie-break by product name ascending, not insertion/id order")
    }

    @Test
    fun limitBoundsTheResultToTheTopNByMetric() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L).id
        val saleId = seedSale(db, branchId, epochMillisUtc(2026, 1, 15))
        val products = (1..5).map { i -> seedProduct(db, gate, "SKU-$i", "Product-$i") }
        products.forEachIndexed { index, productId ->
            seedSaleItem(db, saleId, productId, "Product-${index + 1}", "${index + 1}", "${index + 1}.00")
        }

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val top = repo.getTopProductsByQuantity(ReportScope(1L), dayPeriod(2026, 1, 15), 2L).metrics

        assertEquals(2, top.size)
        assertEquals(listOf("Product-5", "Product-4"), top.map { it.productName })
    }

    @Test
    fun categoryFilterUsesTheProductsCurrentCategoryNotAHistoricalSnapshot() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L).id
        val categoryRepo = SqlDelightCategoryRepository(db, gate)
        val drinks = categoryRepo.insert(1L, "Drinks", null, 100L).id
        val snacks = categoryRepo.insert(1L, "Snacks", null, 100L).id
        val cola = seedProduct(db, gate, "SKU-C", "Cola", categoryId = drinks)
        val chips = seedProduct(db, gate, "SKU-P", "Chips", categoryId = snacks)
        val saleId = seedSale(db, branchId, epochMillisUtc(2026, 1, 15))
        seedSaleItem(db, saleId, cola, "Cola", "3", "9.00")
        seedSaleItem(db, saleId, chips, "Chips", "3", "9.00")

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val drinksOnly = repo.getTopProductsByQuantity(ReportScope(1L, categoryId = drinks), dayPeriod(2026, 1, 15), 10L).metrics
        assertEquals(listOf("Cola"), drinksOnly.map { it.productName })

        // Re-categorize Cola into Snacks -- filtering by "Drinks" must now exclude it (CURRENT category, Option B).
        val productRepo = SqlDelightProductRepository(db, gate)
        productRepo.update(1L, cola, null, "Cola", "cola", snacks, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "unit", 5, 1000L, 2000L)
        val drinksAfterRecategorize = repo.getTopProductsByQuantity(ReportScope(1L, categoryId = drinks), dayPeriod(2026, 1, 15), 10L).metrics
        assertEquals(emptyList(), drinksAfterRecategorize, "Option B: historical results move when the product's current category changes, per historical-category-reporting-decision.md")
    }

    @Test
    fun archivedProductsHistoricalSalesRemainReportable() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L).id
        val product = seedProduct(db, gate, "SKU-D", "Discontinued")
        val saleId = seedSale(db, branchId, epochMillisUtc(2026, 1, 15))
        seedSaleItem(db, saleId, product, "Discontinued", "4", "8.00")
        SqlDelightProductRepository(db, gate).setActive(1L, product, false, 2000L)

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val top = repo.getTopProductsByQuantity(ReportScope(1L), dayPeriod(2026, 1, 15), 10L).metrics

        assertEquals(listOf("Discontinued"), top.map { it.productName }, "an archived product's historical sales must remain reportable")
    }

    @Test
    fun renamedProductDisplaysTheImmutableHistoricalSnapshotNameNotTheCurrentName() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L).id
        val product = seedProduct(db, gate, "SKU-R", "Old Name")
        val saleId = seedSale(db, branchId, epochMillisUtc(2026, 1, 15))
        seedSaleItem(db, saleId, product, "Old Name", "2", "4.00")
        SqlDelightProductRepository(db, gate).update(1L, product, null, "New Name", "new name", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "unit", 5, 1000L, 2000L)

        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val top = repo.getTopProductsByQuantity(ReportScope(1L), dayPeriod(2026, 1, 15), 10L).metrics

        assertEquals("Old Name", top.single().productName, "the report must display the sale-time snapshot name, not the product's current renamed name")
    }
}
