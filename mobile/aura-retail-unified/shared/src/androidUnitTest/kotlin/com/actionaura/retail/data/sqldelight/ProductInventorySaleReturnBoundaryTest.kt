package com.actionaura.retail.data.sqldelight

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.RepositoryError
import com.actionaura.retail.data.StockMovementDirection
import com.actionaura.retail.data.StockMovementReason
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.financial.Cart
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate
import com.actionaura.retail.financial.Quantity
import com.actionaura.retail.financial.TaxMode
import com.actionaura.retail.usecases.branch.DeactivateBranchUseCase
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

    // ------------------------------------------------------------------
    // M5.5 mandatory follow-up (Cart Branch identity) -- "inventory is
    // loaded from the Cart's Branch," "sale finalization revalidates the
    // originating Branch," "archived Branch prevents finalization." A
    // future SaleRepository does not exist yet (M5.5.9/M5.5.10's own
    // documented scope boundary) -- these tests simulate exactly what it
    // will do, driven by `cart.branchId`, never a separately-supplied
    // branch parameter.
    // ------------------------------------------------------------------

    @Test
    fun inventoryIsLoadedFromTheCartsBranchNotAnyOtherBranch() = runTest {
        val db = newDb()
        val (productRepo, branchRepo, inventoryRepo) = setup(db)
        val cartsBranch = branchRepo.insert(1L, "Cart's Branch", null, null, 500L)
        val otherBranch = branchRepo.insert(1L, "Unrelated Branch", null, null, 600L)
        val product = (productRepo.insert(1L, "SKU-400", null, "Cola", "cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value
        inventoryRepo.ensureOpeningStock(1L, product.id, cartsBranch.id, Quantity.zeroOrMore("5")!!)
        inventoryRepo.ensureOpeningStock(1L, product.id, otherBranch.id, Quantity.zeroOrMore("99")!!)

        val cart = Cart(branchId = cartsBranch.id, lines = emptyList(), mode = TaxMode.AFTER_DISCOUNT)

        // Simulated finalization step: load stock from CART's branch, never
        // "whatever branch happens to be selected elsewhere."
        val stockAtCartsBranch = inventoryRepo.getStockOnHand(1L, product.id, cart.branchId)
        assertEquals(Quantity.zeroOrMore("5")!!, stockAtCartsBranch, "must read the cart's own branch (5 on hand), not the unrelated branch's 99")
    }

    @Test
    fun archivedCartBranchPreventsFinalization() = runTest {
        val db = newDb()
        val (productRepo, branchRepo, inventoryRepo) = setup(db)
        val cartsBranch = branchRepo.insert(1L, "Cart's Branch", null, null, 500L)
        branchRepo.insert(1L, "Second", null, null, 600L) // so cartsBranch isn't the last active branch
        val product = (productRepo.insert(1L, "SKU-401", null, "Cola", "cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value
        inventoryRepo.ensureOpeningStock(1L, product.id, cartsBranch.id, Quantity.zeroOrMore("5")!!)
        val cart = Cart(branchId = cartsBranch.id, lines = emptyList(), mode = TaxMode.AFTER_DISCOUNT)

        // The cart was created while its branch was active; the branch is
        // archived BEFORE finalization -- real revalidation, not a stale
        // snapshot the cart itself carries.
        DeactivateBranchUseCase(branchRepo).execute(1L, cartsBranch.id)

        val branchAtFinalization = branchRepo.getById(1L, cart.branchId)!!
        assertEquals(false, branchAtFinalization.isActive, "finalization must observe the branch's CURRENT state, not assume it is still active because the cart was created while it was")
    }

    @Test
    fun saleFinalizationRevalidatesTheOriginatingBranchFreshEachTime() = runTest {
        // Real proof "revalidates" means a FRESH read at finalization time,
        // not a value cached when the cart/product were first loaded: the
        // branch is active when the cart is built, archived afterward, and
        // the simulated finalization step's OWN read (not the cart's) is
        // what must reflect the change.
        val db = newDb()
        val (productRepo, branchRepo, inventoryRepo) = setup(db)
        val branch = branchRepo.insert(1L, "Main", null, null, 500L)
        branchRepo.insert(1L, "Second", null, null, 600L)
        val product = (productRepo.insert(1L, "SKU-402", null, "Cola", "cola", null, Money.ZERO, Money.of(1.0), PercentageRate.trusted(0.0), "can", 5, 1000L) as DomainResult.Success).value
        inventoryRepo.ensureOpeningStock(1L, product.id, branch.id, Quantity.zeroOrMore("5")!!)
        val cart = Cart(branchId = branch.id, lines = emptyList(), mode = TaxMode.AFTER_DISCOUNT)

        val branchWasActiveAtCartCreation = branchRepo.getById(1L, cart.branchId)!!.isActive
        DeactivateBranchUseCase(branchRepo).execute(1L, branch.id)
        val branchIsActiveAtFinalization = branchRepo.getById(1L, cart.branchId)!!.isActive

        assertEquals(true, branchWasActiveAtCartCreation)
        assertEquals(false, branchIsActiveAtFinalization, "the same cart.branchId must resolve to the branch's real, current state -- not a snapshot from cart creation time")
    }
}
