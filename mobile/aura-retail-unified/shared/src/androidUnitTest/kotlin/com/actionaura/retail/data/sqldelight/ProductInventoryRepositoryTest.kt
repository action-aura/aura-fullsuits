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
import kotlin.test.assertNull

/** M5.1 -- real, executed proof of the SQLDelight-backed Product/Inventory repositories. */
class ProductInventoryRepositoryTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    private suspend fun insertCola(db: RetailDatabase, gate: DatabaseWriteGate) = (SqlDelightProductRepository(db, gate).insert(
        companyId = 1L, sku = "SKU-001", barcode = "0000000001", name = "Cola 330ml", normalizedName = "cola 330ml", categoryId = null,
        costPrice = Money.of(0.5), sellPrice = Money.of(1.99), taxRate = PercentageRate.trusted(10.0),
        unit = "can", reorderLevel = 24, nowEpochMillis = 1000L,
    ) as DomainResult.Success).value

    @Test
    fun productInsertPreservesTypedMoneyAndRateNotRawDouble() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val product = insertCola(db, gate)

        // Real proof the round trip through the TEXT column and back
        // produces exact typed values, not float artifacts -- same
        // discipline as RetailDatabaseSchemaTest's own sell_price assertion.
        assertEquals(Money.of(1.99), product.sellPrice)
        assertEquals("1.99", product.sellPrice.toString())
        assertEquals(PercentageRate.trusted(10.0).toString(), product.taxRate.toString())
    }

    @Test
    fun productLookupByBarcodeAndSku() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val product = insertCola(db, gate)
        val repo = SqlDelightProductRepository(db, gate)

        assertEquals(product.id, repo.getByBarcode(1L, "0000000001")?.id)
        assertEquals(product.id, repo.getBySku(1L, "SKU-001")?.id)
        assertNull(repo.getByBarcode(1L, "does-not-exist"))
    }

    @Test
    fun archivedProductExcludedFromBarcodeLookupButNotFromGetById() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val product = insertCola(db, gate)
        val repo = SqlDelightProductRepository(db, gate)
        repo.setActive(1L, product.id, false, 3000L)

        assertNull(repo.getByBarcode(1L, "0000000001"), "barcode lookup is scan-time and must only resolve active products")
        assertEquals(product.id, repo.getById(1L, product.id)?.id, "getById is not status-filtered")
    }

    @Test
    fun openingStockThenIncreaseThenDecrease() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val product = insertCola(db, gate)
        val inventory = SqlDelightInventoryRepository(db, gate)

        assertEquals(Quantity.ZERO, inventory.getStockOnHand(1L, product.id, 1L))

        inventory.ensureOpeningStock(1L, product.id, 1L, Quantity.zeroOrMore("100")!!)
        assertEquals(Quantity.zeroOrMore("100")!!, inventory.getStockOnHand(1L, product.id, 1L))

        val afterReceipt = inventory.adjustStock(
            1L, product.id, 1L, StockMovementDirection.INCREASE, Quantity.parse("50").getOrNull()!!,
            StockMovementReason.MANUAL_RECEIPT, "PO-1", null, null, null, null, "tester", 2000L,
        )
        assertIs<DomainResult.Success<Quantity>>(afterReceipt)
        assertEquals(Quantity.zeroOrMore("150")!!, afterReceipt.value)

        val afterSale = inventory.adjustStock(
            1L, product.id, 1L, StockMovementDirection.DECREASE, Quantity.parse("30").getOrNull()!!,
            StockMovementReason.MANUAL_CORRECTION_DECREASE, null, null, null, null, null, "tester", 3000L,
        )
        assertIs<DomainResult.Success<Quantity>>(afterSale)
        assertEquals(Quantity.zeroOrMore("120")!!, afterSale.value)
    }

    @Test
    fun decreaseBeyondOnHandFailsInsufficientStockAndLeavesBalanceUnchanged() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val product = insertCola(db, gate)
        val inventory = SqlDelightInventoryRepository(db, gate)
        inventory.ensureOpeningStock(1L, product.id, 1L, Quantity.zeroOrMore("10")!!)

        val result = inventory.adjustStock(
            1L, product.id, 1L, StockMovementDirection.DECREASE, Quantity.parse("11").getOrNull()!!,
            StockMovementReason.MANUAL_CORRECTION_DECREASE, null, null, null, null, null, "tester", 2000L,
        )
        assertIs<DomainResult.Failure>(result)
        assertIs<RepositoryError.InsufficientStock>(result.error)
        assertEquals(Quantity.zeroOrMore("10")!!, inventory.getStockOnHand(1L, product.id, 1L), "a rejected decrease must not mutate the balance")
    }

    @Test
    fun adjustStockWorksWithNoPriorOpeningStockRow() = runTest {
        // ensureOpeningStock is never called first -- adjustStock's own
        // upsertOpeningStock("0") call inside its transaction must create
        // the row on demand.
        val db = newDb()
        val gate = DatabaseWriteGate()
        val product = insertCola(db, gate)
        val inventory = SqlDelightInventoryRepository(db, gate)

        val result = inventory.adjustStock(
            1L, product.id, 1L, StockMovementDirection.INCREASE, Quantity.parse("5").getOrNull()!!,
            StockMovementReason.MANUAL_RECEIPT, null, null, null, null, null, "tester", 1000L,
        )
        assertIs<DomainResult.Success<Quantity>>(result)
        assertEquals(Quantity.zeroOrMore("5")!!, result.value)
    }
}
