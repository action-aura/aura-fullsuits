package com.actionaura.retail.usecases.inventory

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.RepositoryError
import com.actionaura.retail.data.StockMovementDirection
import com.actionaura.retail.data.StockMovementReason
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.data.sqldelight.SqlDelightBranchRepository
import com.actionaura.retail.data.sqldelight.SqlDelightInventoryRepository
import com.actionaura.retail.data.sqldelight.SqlDelightProductRepository
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate
import com.actionaura.retail.financial.Quantity
import com.actionaura.retail.usecases.branch.DeactivateBranchUseCase
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs

/** M5.5.7/M5.5.12 -- real, executed proof of the Inventory domain's use-case layer. */
class InventoryUseCasesTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    private suspend fun insertCola(productRepo: SqlDelightProductRepository) = (productRepo.insert(
        1L, "SKU-200", null, "Cola", "cola", null, Money.of(0.5), Money.of(1.5), PercentageRate.trusted(0.0), "can", 5, 1000L,
    ) as DomainResult.Success).value

    @Test
    fun adjustInventoryRejectsArchivedBranch() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchRepo = SqlDelightBranchRepository(db, gate)
        val productRepo = SqlDelightProductRepository(db, gate)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        branchRepo.insert(1L, "Second", null, null, 600L)
        val product = insertCola(productRepo)
        DeactivateBranchUseCase(branchRepo).execute(1L, branch.id)

        val useCase = AdjustInventoryUseCase(SqlDelightInventoryRepository(db, gate), productRepo, branchRepo)
        val result = useCase.execute(
            1L, product.id, branch.id, StockMovementDirection.INCREASE, Quantity.parse("10").getOrNull()!!,
            StockMovementReason.MANUAL_RECEIPT, null, null, null, null, null, "tester", 2000L,
        )
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.ValidationFailed>(result.error)
    }

    @Test
    fun adjustInventoryRejectsUnknownProduct() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchRepo = SqlDelightBranchRepository(db, gate)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        val useCase = AdjustInventoryUseCase(SqlDelightInventoryRepository(db, gate), SqlDelightProductRepository(db, gate), branchRepo)

        val result = useCase.execute(
            1L, 999L, branch.id, StockMovementDirection.INCREASE, Quantity.parse("10").getOrNull()!!,
            StockMovementReason.MANUAL_RECEIPT, null, null, null, null, null, "tester", 1000L,
        )
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.NotFound>(result.error)
    }

    @Test
    fun reconcileToCountedQuantityRecordsCorrectDeltaBothDirections() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchRepo = SqlDelightBranchRepository(db, gate)
        val productRepo = SqlDelightProductRepository(db, gate)
        val inventoryRepo = SqlDelightInventoryRepository(db, gate)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        val product = insertCola(productRepo)
        inventoryRepo.ensureOpeningStock(1L, product.id, branch.id, Quantity.zeroOrMore("50")!!)

        val reconcile = ReconcileInventoryUseCase(inventoryRepo, productRepo, branchRepo)

        // Counted HIGHER than system (found more than expected).
        val up = reconcile.execute(1L, product.id, branch.id, Quantity.zeroOrMore("55")!!, "physical count", "tester", null, 1000L)
        assertIs<DomainResult.Success<Quantity>>(up)
        assertEquals(Quantity.zeroOrMore("55")!!, up.value)

        // Counted LOWER than system (found less than expected, e.g. shrinkage).
        val down = reconcile.execute(1L, product.id, branch.id, Quantity.zeroOrMore("40")!!, "physical count", "tester", null, 2000L)
        assertIs<DomainResult.Success<Quantity>>(down)
        assertEquals(Quantity.zeroOrMore("40")!!, down.value)

        val history = GetMovementHistoryUseCase(inventoryRepo).execute(1L, product.id, 10)
        assertEquals(2, history.size)
        assertEquals("STOCK_TAKE_RECONCILE", history.first().movementType) // most recent first
    }

    @Test
    fun reconcileNeverOverwritesBlindly() = runTest {
        // Real proof reconcile is expressed as a DERIVED delta against the
        // lock-fresh prior balance, not a raw overwrite (unlike the
        // legacy importer's own documented gap,
        // product-inventory-authority-audit.md #3) -- quantity_before on
        // the recorded movement must equal what was actually there, not
        // an assumed value.
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchRepo = SqlDelightBranchRepository(db, gate)
        val productRepo = SqlDelightProductRepository(db, gate)
        val inventoryRepo = SqlDelightInventoryRepository(db, gate)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        val product = insertCola(productRepo)
        inventoryRepo.ensureOpeningStock(1L, product.id, branch.id, Quantity.zeroOrMore("73")!!)

        ReconcileInventoryUseCase(inventoryRepo, productRepo, branchRepo).execute(1L, product.id, branch.id, Quantity.zeroOrMore("80")!!, null, "tester", null, 1000L)

        val movement = GetMovementHistoryUseCase(inventoryRepo).execute(1L, product.id, 1).first()
        assertEquals(Quantity.zeroOrMore("73")!!, movement.quantityBefore)
        assertEquals(Quantity.zeroOrMore("80")!!, movement.quantityAfter)
        assertEquals(Quantity.zeroOrMore("7")!!, movement.quantity, "the recorded delta must be the real difference, not the counted value itself")
    }

    @Test
    fun getLowStockProductsReflectsSumAcrossBranches() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val branchRepo = SqlDelightBranchRepository(db, gate)
        val productRepo = SqlDelightProductRepository(db, gate)
        val inventoryRepo = SqlDelightInventoryRepository(db, gate)
        val branchA = branchRepo.insert(1L, "A", null, null, 500L)
        val branchB = branchRepo.insert(1L, "B", null, null, 600L)
        // reorder_level defaults to 5 in insertCola's own product (unit=can, reorderLevel=5).
        val product = insertCola(productRepo)
        inventoryRepo.ensureOpeningStock(1L, product.id, branchA.id, Quantity.zeroOrMore("2")!!)
        inventoryRepo.ensureOpeningStock(1L, product.id, branchB.id, Quantity.zeroOrMore("2")!!)
        // Total on hand = 4, reorder_level = 5 -- low stock.

        val lowStock = com.actionaura.retail.usecases.product.GetLowStockProductsUseCase(productRepo).execute(1L)
        assertEquals(1, lowStock.size)
        assertEquals(Quantity.zeroOrMore("4")!!, lowStock.first().totalOnHandAcrossBranches)
    }
}
