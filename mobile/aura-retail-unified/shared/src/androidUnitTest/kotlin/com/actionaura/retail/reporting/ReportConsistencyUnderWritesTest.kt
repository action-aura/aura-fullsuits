package com.actionaura.retail.reporting

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.data.sqldelight.SqlDelightBranchRepository
import com.actionaura.retail.data.sqldelight.SqlDelightProductRepository
import com.actionaura.retail.data.sqldelight.SqlDelightSettingsRepository
import com.actionaura.retail.db.RetailDatabase
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.test.runTest
import kotlinx.datetime.LocalDate
import kotlinx.datetime.TimeZone
import kotlinx.datetime.atStartOfDayIn
import kotlin.test.Test
import kotlin.test.assertTrue

private fun epochMillisUtc(year: Int, month: Int, day: Int): Long =
    LocalDate(year, month, day).atStartOfDayIn(TimeZone.UTC).toEpochMilliseconds()

/**
 * M5.6.11 -- real, executed proof that a report read can never observe a
 * partially finalized Sale (a sale row with some but not all of its
 * `sale_items` rows present). Closes the M5.5 checkpoint's own deferred
 * "report read vs sale commit" scenario now that a real
 * `ReportingRepository` exists (`reporting-concurrency-report.md`).
 *
 * The real mechanism under test: a future `SaleRepository.finalize` that
 * wraps its whole sale+items write in one `gate.mutex.withLock { ... }`
 * (the same `DatabaseWriteGate` discipline already established for
 * Product/Inventory/Category/Branch/Catalog-import writes) is
 * automatically safe against concurrent report reads, because
 * `SqlDelightReportingRepository`'s own query methods acquire the SAME
 * gate. This test simulates that future writer directly (no
 * `SaleRepository` exists yet, M5.5.9/10's own documented scope
 * boundary) to prove the mechanism, not a specific use case.
 */
class ReportConsistencyUnderWritesTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    @Test
    fun aReportReadNeverObservesASaleRowWithoutItsCompleteSetOfSaleItems() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchId = SqlDelightBranchRepository(db, gate).insert(1L, "Main", null, null, 500L).id
        val productRepo = SqlDelightProductRepository(db, gate)
        val product = (productRepo.insert(1L, "SKU-X", null, "Item", "item", null, com.actionaura.retail.financial.Money.ZERO, com.actionaura.retail.financial.Money.of(1.0), com.actionaura.retail.financial.PercentageRate.trusted(0.0), "unit", 5, 1000L) as DomainResult.Success).value
        val repo = SqlDelightReportingRepository(db, gate, SqlDelightSettingsRepository(db, gate))
        val itemsPerSale = 5
        val period = ReportPeriodFactory.customRange(epochMillisUtc(2026, 1, 1), epochMillisUtc(2026, 1, 2))
        val observedIncompleteCounts = mutableListOf<Int>()

        coroutineScope {
            // Simulated future sale-finalization writer: whole sale+items
            // write happens inside ONE gate.mutex.withLock, with real
            // suspend points between item inserts to maximize the race
            // window against concurrent readers.
            val writer = launch {
                repeat(20) { saleIndex ->
                    gate.mutex.withLock {
                        db.salesQueries.insertSale(1L, null, branchId, null, "POS", "0.00", "0.00", "0.00", "50.00", "50.00", "0.00", "cash", null, null, null, epochMillisUtc(2026, 1, 1) + saleIndex)
                        val saleId = db.catalogQueries.lastInsertRowId().executeAsOne()
                        repeat(itemsPerSale) {
                            delay(1)
                            db.salesQueries.insertSaleItem(saleId, product.id, "Item", "1", "10.00", "0", "0", "10.00")
                        }
                    }
                }
            }
            val reader = launch {
                repeat(50) {
                    delay(1)
                    val top = repo.getTopProductsByQuantity(ReportScope(1L), period, 10L).metrics
                    val observedQuantity = top.firstOrNull { it.productId == product.id }?.netQuantity?.toString() ?: "0"
                    val observedCount = observedQuantity.removePrefix("-").substringBefore(".").toIntOrNull() ?: 0
                    if (observedCount % itemsPerSale != 0) observedIncompleteCounts += observedCount
                }
            }
            writer.join()
            reader.join()
        }

        assertTrue(observedIncompleteCounts.isEmpty(), "every report read must see a quantity that is a whole multiple of one complete sale's item count ($itemsPerSale), never a partial sale mid-write; saw: $observedIncompleteCounts")
    }
}
