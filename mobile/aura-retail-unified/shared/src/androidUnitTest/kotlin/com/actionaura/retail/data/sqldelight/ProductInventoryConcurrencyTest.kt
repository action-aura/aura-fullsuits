package com.actionaura.retail.data.sqldelight

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.RepositoryError
import com.actionaura.retail.data.StockMovementDirection
import com.actionaura.retail.data.StockMovementReason
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate
import com.actionaura.retail.financial.Quantity
import com.actionaura.retail.usecases.product.CreateProductUseCase
import com.actionaura.retail.platform.AndroidUnicodeTextNormalizer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * M5.5.14 -- REAL concurrency proof, genuinely parallel OS threads
 * (`Dispatchers.Default`), not `runTest`'s cooperative single-threaded
 * test dispatcher. This distinction matters: `SqlDelightXxxRepository`'s
 * `db.transactionWithResult { ... }` blocks run to completion
 * synchronously with no internal suspension point, so launching
 * "concurrent" coroutines on `runTest`'s own virtual-time dispatcher would
 * execute them fully serialized (no interleaving ever occurs, since there
 * is nothing to suspend on mid-transaction) -- a real race requires actual
 * parallel threads contending for the same underlying JDBC connection,
 * exactly like M3's `FinancialSecurityTest` proved for the in-memory
 * Mutex-guarded repository, but here proving SQLite's OWN transaction
 * serialization is the real backstop (stock-concurrency-report.md).
 */
class ProductInventoryConcurrencyTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    private val normalizer = AndroidUnicodeTextNormalizer()

    @Test
    fun concurrentDuplicateSkuCreationResolvesToExactlyOneWinner() = runTest {
        val db = newDb()
        val productRepo = SqlDelightProductRepository(db)
        val categoryRepo = SqlDelightCategoryRepository(db)
        val useCase = CreateProductUseCase(productRepo, categoryRepo, normalizer)

        val results = (1..20).map {
            async(Dispatchers.Default) {
                useCase.execute(1L, "SKU-RACE-1", null, "Cola $it", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
            }
        }.awaitAll()

        val succeeded = results.count { it is DomainResult.Success }
        val duplicateRejected = results.count { it is DomainResult.Failure && it.error is RepositoryError.DuplicateValue }
        assertEquals(1, succeeded, "exactly one of 20 concurrent same-SKU creations must win")
        assertEquals(19, duplicateRejected, "every other attempt must be rejected as a real duplicate, not silently lost or duplicated")
        assertEquals(1, productRepo.listActive(1L).size, "exactly one product row must exist after the race")
    }

    @Test
    fun concurrentDuplicateBarcodeCreationResolvesToExactlyOneWinner() = runTest {
        val db = newDb()
        val productRepo = SqlDelightProductRepository(db)
        val categoryRepo = SqlDelightCategoryRepository(db)
        val useCase = CreateProductUseCase(productRepo, categoryRepo, normalizer)

        val results = (1..20).map { i ->
            async(Dispatchers.Default) {
                useCase.execute(1L, "SKU-RACE-BC-$i", "000777", "Cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L)
            }
        }.awaitAll()

        val succeeded = results.count { it is DomainResult.Success }
        val duplicateRejected = results.count { it is DomainResult.Failure && it.error is RepositoryError.DuplicateValue }
        assertEquals(1, succeeded, "exactly one of 20 concurrent same-barcode creations must win")
        assertEquals(19, duplicateRejected)
        assertEquals(1, productRepo.listActive(1L).size)
    }

    @Test
    fun finalUnitSaleRaceResolvesExactlyOneWinnerAndStockNeverGoesNegative() = runTest {
        val db = newDb()
        val productRepo = SqlDelightProductRepository(db)
        val branchRepo = SqlDelightBranchRepository(db)
        val inventoryRepo = SqlDelightInventoryRepository(db)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        val product = (productRepo.insert(1L, "SKU-RACE-2", null, "Cola", "cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value
        inventoryRepo.ensureOpeningStock(1L, product.id, branch.id, Quantity.zeroOrMore("1")!!)

        // 10 concurrent "sale" attempts race for the last 1 unit of stock.
        val results = (1..10).map { i ->
            async(Dispatchers.Default) {
                inventoryRepo.adjustStock(
                    1L, product.id, branch.id, StockMovementDirection.DECREASE, Quantity.parse("1").getOrNull()!!,
                    StockMovementReason.SALE, null, i.toLong(), null, "final-unit-race-$i", null, "cashier", 2000L,
                )
            }
        }.awaitAll()

        val succeeded = results.count { it is DomainResult.Success }
        val insufficientStock = results.count { it is DomainResult.Failure && it.error is RepositoryError.InsufficientStock }
        assertEquals(1, succeeded, "exactly one of 10 concurrent buyers should win the race for the last unit")
        assertEquals(9, insufficientStock)
        assertEquals(Quantity.ZERO, inventoryRepo.getStockOnHand(1L, product.id, branch.id), "stock must be exactly zero, never negative")

        val history = inventoryRepo.listMovementHistory(1L, product.id, 20)
        assertEquals(1, history.size, "exactly one movement row -- no partial/duplicate movement from a losing attempt")
    }

    @Test
    fun concurrentStockIncreasesAllApplyWithNoLostUpdates() = runTest {
        val db = newDb()
        val productRepo = SqlDelightProductRepository(db)
        val branchRepo = SqlDelightBranchRepository(db)
        val inventoryRepo = SqlDelightInventoryRepository(db)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        val product = (productRepo.insert(1L, "SKU-RACE-3", null, "Cola", "cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value

        val results = (1..15).map { i ->
            async(Dispatchers.Default) {
                inventoryRepo.adjustStock(
                    1L, product.id, branch.id, StockMovementDirection.INCREASE, Quantity.parse("1").getOrNull()!!,
                    StockMovementReason.MANUAL_RECEIPT, null, null, null, "concurrent-increase-$i", null, "tester", 2000L,
                )
            }
        }.awaitAll()

        assertEquals(15, results.count { it is DomainResult.Success })
        assertEquals(Quantity.zeroOrMore("15")!!, inventoryRepo.getStockOnHand(1L, product.id, branch.id), "every concurrent increase must apply -- real proof no read-modify-write update was silently lost")
        assertEquals(15, inventoryRepo.listMovementHistory(1L, product.id, 20).size)
    }
}
