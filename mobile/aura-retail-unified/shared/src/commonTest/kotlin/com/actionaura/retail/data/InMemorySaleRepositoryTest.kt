package com.actionaura.retail.data

import com.actionaura.retail.financial.FinancialResult
import com.actionaura.retail.financial.Quantity
import com.actionaura.retail.usecases.FinalizeReturnCommand
import com.actionaura.retail.usecases.FinalizeSaleCommand
import com.actionaura.retail.usecases.ReturnLineRequest
import com.actionaura.retail.usecases.SaleLineRequest
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * M3.4 -- real invariant tests for the transactional sale/return service
 * boundary, each citing the exact financial-invariant-catalog.md entry it
 * proves.
 */
class InMemorySaleRepositoryTest {

    private fun repoWithWidget(stock: String = "10"): InMemorySaleRepository {
        val repo = InMemorySaleRepository()
        repo.putProduct(
            ProductForSale(productId = "widget", name = "Widget", sellPriceRaw = "20.00", taxRateRaw = 10.0, isActive = true),
            initialStock = Quantity.parse(stock).getOrNull()!!, branchId = "branch-1",
        )
        return repo
    }

    @Test
    fun fullSalePersistsAndDecrementsStock() = runTest {
        val repo = repoWithWidget(stock = "10")
        val result = repo.finalizeSale(
            FinalizeSaleCommand(
                lines = listOf(SaleLineRequest("widget", "3")), branchId = "branch-1", customerId = null,
                paymentMethod = "cash", amountPaidRaw = null, idempotencyKey = "key-1",
            )
        )
        assertTrue(result is FinancialResult.Success)
        val sale = (result as FinancialResult.Success).value
        assertEquals("Widget", sale.lines.first().productNameAtSale) // invariant #20 -- name snapshotted
        assertEquals(repo.stockOnHand("widget", "branch-1"), Quantity.parse("7").getOrNull()!!) // invariant #9 -- decremented
    }

    @Test
    fun insufficientStockRejectsAndPersistsNothing() = runTest {
        // Invariant #8 -- rejected outright, no partial persistence.
        val repo = repoWithWidget(stock = "2")
        val result = repo.finalizeSale(
            FinalizeSaleCommand(listOf(SaleLineRequest("widget", "5")), "branch-1", null, "cash", null, "key-2")
        )
        assertTrue(result is FinancialResult.Failure)
        assertEquals("INSUFFICIENT_STOCK", (result as FinancialResult.Failure).error.code)
        assertEquals(Quantity.parse("2").getOrNull()!!, repo.stockOnHand("widget", "branch-1")) // unchanged -- invariant #9
    }

    @Test
    fun idempotentRetrySamePayloadReturnsSameResult() = runTest {
        // Invariant #11.
        val repo = repoWithWidget()
        val cmd = FinalizeSaleCommand(listOf(SaleLineRequest("widget", "1")), "branch-1", null, "cash", null, "key-3")
        val first = (repo.finalizeSale(cmd) as FinancialResult.Success).value
        val second = (repo.finalizeSale(cmd) as FinancialResult.Success).value
        assertEquals(first.saleId, second.saleId)
        assertEquals(Quantity.parse("9").getOrNull()!!, repo.stockOnHand("widget", "branch-1")) // decremented exactly once, not twice
    }

    @Test
    fun idempotentRetryConflictingPayloadReturnsConflict() = runTest {
        // Invariant #12 -- CANONICAL_UNIFIED, the real Python gap this repository closes.
        val repo = repoWithWidget()
        val first = repo.finalizeSale(FinalizeSaleCommand(listOf(SaleLineRequest("widget", "1")), "branch-1", null, "cash", null, "key-4"))
        assertTrue(first is FinancialResult.Success)
        val second = repo.finalizeSale(FinalizeSaleCommand(listOf(SaleLineRequest("widget", "2")), "branch-1", null, "cash", null, "key-4"))
        assertTrue(second is FinancialResult.Failure)
        assertEquals("DUPLICATE_OPERATION_CONFLICT", (second as FinancialResult.Failure).error.code)
    }

    @Test
    fun walkInUnderpaymentRejectedRequiresCustomer() = runTest {
        // Invariant #13.
        val repo = repoWithWidget()
        val result = repo.finalizeSale(
            FinalizeSaleCommand(listOf(SaleLineRequest("widget", "1")), "branch-1", customerId = null, "cash", amountPaidRaw = "5.00", idempotencyKey = "key-5")
        )
        assertTrue(result is FinancialResult.Failure)
        assertEquals("CREDIT_SALE_REQUIRES_CUSTOMER", (result as FinancialResult.Failure).error.code)
    }

    @Test
    fun creditSaleWithinLimitSucceeds() = runTest {
        val repo = repoWithWidget()
        repo.putCustomer(InMemoryCustomer("cust-1", creditMode = "limited", creditLimit = com.actionaura.retail.financial.Money.parse("100.00").getOrNull()!!))
        val result = repo.finalizeSale(
            FinalizeSaleCommand(listOf(SaleLineRequest("widget", "1")), "branch-1", "cust-1", "cash", amountPaidRaw = "5.00", idempotencyKey = "key-6")
        )
        assertTrue(result is FinancialResult.Success)
    }

    @Test
    fun creditLimitExceededRejected() = runTest {
        val repo = repoWithWidget()
        repo.putCustomer(InMemoryCustomer("cust-2", creditMode = "limited", creditLimit = com.actionaura.retail.financial.Money.parse("5.00").getOrNull()!!))
        val result = repo.finalizeSale(
            FinalizeSaleCommand(listOf(SaleLineRequest("widget", "1")), "branch-1", "cust-2", "cash", amountPaidRaw = "0.00", idempotencyKey = "key-7")
        )
        assertTrue(result is FinancialResult.Failure)
        assertEquals("CREDIT_LIMIT_EXCEEDED", (result as FinancialResult.Failure).error.code)
    }

    @Test
    fun fullReturnRestoresStockAndRefundsFromSnapshot() = runTest {
        // Invariant #16 -- refund derived from the original sale's frozen unit
        // price, proven by changing the product's live price AFTER the sale and
        // confirming the refund still uses the sale-time price.
        val repo = repoWithWidget(stock = "10")
        val sale = (repo.finalizeSale(FinalizeSaleCommand(listOf(SaleLineRequest("widget", "3")), "branch-1", null, "cash", null, "sale-key-1")) as FinancialResult.Success).value

        repo.putProduct(ProductForSale("widget", "Widget", sellPriceRaw = "999.00", taxRateRaw = 10.0, isActive = true), initialStock = repo.stockOnHand("widget", "branch-1"), branchId = "branch-1")

        val ret = repo.finalizeReturn(FinalizeReturnCommand("sale-1", listOf(ReturnLineRequest("widget", "3")), "damaged", "cash", "return-key-1"))
        assertTrue(ret is FinancialResult.Success)
        val returnSnapshot = (ret as FinancialResult.Success).value
        // 3 * 20.00 * 1.10 tax = 66.00, NOT 3 * 999.00 -- proves the live-price change didn't leak in.
        assertEquals("66.00", returnSnapshot.refundTotal.toString())
        assertEquals(Quantity.parse("10").getOrNull()!!, repo.stockOnHand("widget", "branch-1")) // fully restored
    }

    @Test
    fun cumulativeReturnCannotExceedSoldQuantity() = runTest {
        // Invariant #17.
        val repo = repoWithWidget(stock = "10")
        repo.finalizeSale(FinalizeSaleCommand(listOf(SaleLineRequest("widget", "5")), "branch-1", null, "cash", null, "sale-key-2"))

        val firstReturn = repo.finalizeReturn(FinalizeReturnCommand("sale-1", listOf(ReturnLineRequest("widget", "3")), "r1", "cash", "ret-a"))
        assertTrue(firstReturn is FinancialResult.Success)

        val secondReturn = repo.finalizeReturn(FinalizeReturnCommand("sale-1", listOf(ReturnLineRequest("widget", "3")), "r2", "cash", "ret-b"))
        assertTrue(secondReturn is FinancialResult.Failure)
        assertEquals("RETURN_EXCEEDS_REMAINING_QUANTITY", (secondReturn as FinancialResult.Failure).error.code)
    }

    @Test
    fun returnAgainstUnknownSaleRejected() = runTest {
        val repo = repoWithWidget()
        val result = repo.finalizeReturn(FinalizeReturnCommand("no-such-sale", listOf(ReturnLineRequest("widget", "1")), "r", "cash", "ret-c"))
        assertTrue(result is FinancialResult.Failure)
        assertEquals("SALE_NOT_FOUND", (result as FinancialResult.Failure).error.code)
    }

    @Test
    fun emptySaleRejected() = runTest {
        val repo = repoWithWidget()
        val result = repo.finalizeSale(FinalizeSaleCommand(emptyList(), "branch-1", null, "cash", null, "key-empty"))
        assertTrue(result is FinancialResult.Failure)
        assertEquals("EMPTY_SALE", (result as FinancialResult.Failure).error.code)
    }
}
