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
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs

/**
 * M5.5.9/M5.5.10 -- real proof of the Product/Inventory integration
 * boundary a future SQLite-backed SaleRepository/ReturnRepository will
 * compose (sale-return-inventory-integration.md). Deliberately NOT a new
 * SaleRepository -- exercises the real M5.1/M5.4/M5.5 primitives directly,
 * simulating what a sale/return finalization step will do with them.
 */
class ProductInventorySaleReturnBoundaryTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    private suspend fun setup(db: RetailDatabase, gate: DatabaseWriteGate = DatabaseWriteGate()): Triple<SqlDelightProductRepository, SqlDelightBranchRepository, SqlDelightInventoryRepository> {
        val productRepo = SqlDelightProductRepository(db, gate)
        val branchRepo = SqlDelightBranchRepository(db, gate)
        val inventoryRepo = SqlDelightInventoryRepository(db, gate)
        return Triple(productRepo, branchRepo, inventoryRepo)
    }

    @Test
    fun saleRejectsArchivedProduct() = runTest {
        val db = newDb()
        val (productRepo, branchRepo, _) = setup(db)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        val product = (productRepo.insert(1L, "SKU-300", null, "Cola", "cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value
        productRepo.setActive(1L, product.id, false, 2000L)

        // Simulated sale-finalization step 1: load authoritative product state.
        val loaded = productRepo.getById(1L, product.id)!!
        assertEquals(false, loaded.isActive, "an archived product must be detectable before any stock mutation is attempted")
    }

    @Test
    fun saleRejectsArchivedBranch() = runTest {
        val db = newDb()
        val (productRepo, branchRepo, _) = setup(db)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        branchRepo.insert(1L, "Second", null, null, 600L)
        com.actionaura.retail.usecases.branch.DeactivateBranchUseCase(branchRepo).execute(1L, branch.id)

        val loaded = branchRepo.getById(1L, branch.id)!!
        assertEquals(false, loaded.isActive, "an archived branch must be detectable before any stock mutation is attempted")
    }

    @Test
    fun saleDecrementsStockExactlyOnceAndLinksTheMovementToTheSale() = runTest {
        val db = newDb()
        val (productRepo, branchRepo, inventoryRepo) = setup(db)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        val product = (productRepo.insert(1L, "SKU-301", null, "Cola", "cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value
        inventoryRepo.ensureOpeningStock(1L, product.id, branch.id, Quantity.zeroOrMore("10")!!)

        val simulatedSaleId = 42L
        val result = inventoryRepo.adjustStock(
            1L, product.id, branch.id, StockMovementDirection.DECREASE, Quantity.parse("3").getOrNull()!!,
            StockMovementReason.SALE, "SALE-0001", simulatedSaleId, null, "sale-idem-1", null, "cashier", 2000L,
        )
        assertIs<DomainResult.Success<Quantity>>(result)
        assertEquals(Quantity.zeroOrMore("7")!!, result.value)

        val movement = inventoryRepo.listMovementHistory(1L, product.id, 1).first()
        assertEquals("SALE", movement.movementType)
        assertEquals(simulatedSaleId, movement.relatedSaleId)
    }

    @Test
    fun saleRejectsInsufficientStockAndLeavesBalanceUnchanged() = runTest {
        val db = newDb()
        val (productRepo, branchRepo, inventoryRepo) = setup(db)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        val product = (productRepo.insert(1L, "SKU-302", null, "Cola", "cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value
        inventoryRepo.ensureOpeningStock(1L, product.id, branch.id, Quantity.zeroOrMore("2")!!)

        val result = inventoryRepo.adjustStock(
            1L, product.id, branch.id, StockMovementDirection.DECREASE, Quantity.parse("3").getOrNull()!!,
            StockMovementReason.SALE, null, 99L, null, null, null, "cashier", 2000L,
        )
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.InsufficientStock>(result.error)
        assertEquals(Quantity.zeroOrMore("2")!!, inventoryRepo.getStockOnHand(1L, product.id, branch.id))
    }

    @Test
    fun retryingSaleStockDecrementWithSameKeyDoesNotDoubleDecrement() = runTest {
        val db = newDb()
        val (productRepo, branchRepo, inventoryRepo) = setup(db)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        val product = (productRepo.insert(1L, "SKU-303", null, "Cola", "cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value
        inventoryRepo.ensureOpeningStock(1L, product.id, branch.id, Quantity.zeroOrMore("10")!!)

        val first = inventoryRepo.adjustStock(
            1L, product.id, branch.id, StockMovementDirection.DECREASE, Quantity.parse("4").getOrNull()!!,
            StockMovementReason.SALE, null, 7L, null, "sale-idem-retry", null, "cashier", 2000L,
        )
        assertIs<DomainResult.Success<Quantity>>(first)

        val retry = inventoryRepo.adjustStock(
            1L, product.id, branch.id, StockMovementDirection.DECREASE, Quantity.parse("4").getOrNull()!!,
            StockMovementReason.SALE, null, 7L, null, "sale-idem-retry", null, "cashier", 3000L,
        )
        assertIs<DomainResult.Success<Quantity>>(retry)
        assertEquals(Quantity.zeroOrMore("6")!!, inventoryRepo.getStockOnHand(1L, product.id, branch.id), "a retried decrement with the same idempotency key must not double-apply")
    }

    @Test
    fun returnRestoresStockExactlyOnceAndLinksTheMovementToTheReturn() = runTest {
        val db = newDb()
        val (productRepo, branchRepo, inventoryRepo) = setup(db)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        val product = (productRepo.insert(1L, "SKU-304", null, "Cola", "cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value
        inventoryRepo.ensureOpeningStock(1L, product.id, branch.id, Quantity.zeroOrMore("5")!!)

        val simulatedReturnId = 88L
        val result = inventoryRepo.adjustStock(
            1L, product.id, branch.id, StockMovementDirection.INCREASE, Quantity.parse("2").getOrNull()!!,
            StockMovementReason.RETURN, "RET-0001", null, simulatedReturnId, "return-idem-1", null, "cashier", 2000L,
        )
        assertIs<DomainResult.Success<Quantity>>(result)
        assertEquals(Quantity.zeroOrMore("7")!!, result.value)

        val movement = inventoryRepo.listMovementHistory(1L, product.id, 1).first()
        assertEquals("RETURN", movement.movementType)
        assertEquals(simulatedReturnId, movement.relatedReturnId)
    }
}
