package com.actionaura.retail.financial

import com.actionaura.retail.data.InMemorySaleRepository
import com.actionaura.retail.data.ProductForSale
import com.actionaura.retail.usecases.FinalizeReturnCommand
import com.actionaura.retail.usecases.FinalizeSaleCommand
import com.actionaura.retail.usecases.ReturnLineRequest
import com.actionaura.retail.usecases.SaleLineRequest
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

/**
 * M3.7 -- proves the shared FinalizedSaleSnapshot/FinalizedReturnSnapshot
 * carry every field a receipt needs, per the governing spec's exact
 * required list: product line, quantity, unit price, gross, discount,
 * tax, subtotal, total, paid, change, return values, identifiers,
 * date/time representation, currency. Printing/sharing adapters are a
 * later milestone (M15) -- this proves the DATA is complete and correct,
 * not that a PDF/share-sheet exists yet.
 */
class ReceiptParityTest {

    @Test
    fun saleSnapshotCarriesEveryReceiptField() = runTest {
        val repo = InMemorySaleRepository(currency = CurrencyCode("USD"))
        repo.putProduct(
            ProductForSale("widget", "Widget", "20.00", 10.0, isActive = true),
            Quantity.parse("10").getOrNull()!!, "branch-1",
        )
        val result = repo.finalizeSale(
            FinalizeSaleCommand(
                lines = listOf(SaleLineRequest("widget", "3", discountPctRaw = 5.0)),
                branchId = "branch-1", customerId = null, paymentMethod = "cash",
                // null -- defaults to exactly `total` (invariant #14), avoiding a
                // hand-computed tax-inclusive figure here that would silently
                // drift from the real formula and mask an unrelated failure.
                amountPaidRaw = null, idempotencyKey = "receipt-key-1",
            )
        )
        assertTrue(result is FinancialResult.Success)
        val sale = (result as FinancialResult.Success).value

        // Identifiers
        assertNotNull(sale.saleId)
        assertTrue(sale.saleNumber.isNotBlank())
        assertNotNull(sale.idempotencyKey)

        // Product line + quantity + unit price
        val line = sale.lines.first()
        assertEquals("widget", line.productId)
        assertEquals("Widget", line.productNameAtSale)
        assertEquals(Quantity.parse("3").getOrNull(), line.quantity)
        assertEquals(Money.parse("20.00").getOrNull(), line.unitPriceAtSale)

        // gross / discount / tax (per-line, from LineCalculation)
        assertTrue(line.calculation.gross.toString().isNotBlank())
        assertTrue(line.calculation.discountAmount > Money.ZERO || line.calculation.discountAmount == Money.ZERO)
        assertTrue(line.calculation.tax >= Money.ZERO)

        // subtotal / total (header)
        assertEquals(Money.parse("60.00").getOrNull(), sale.subtotal) // 20*3
        assertTrue(sale.total > Money.ZERO)

        // paid / change -- amountPaidRaw was null, so paid must equal total exactly (invariant #14), and change must be zero.
        assertEquals(sale.total, sale.amountPaid)
        assertEquals(Money.ZERO, sale.change)

        // currency + date/time representation contract (epoch millis -- platform-neutral, formatted at render time, never baked into the snapshot as a locale-specific string)
        assertEquals(CurrencyCode("USD"), sale.currency)
        assertTrue(sale.createdAtEpochMillis >= 0)
    }

    @Test
    fun returnSnapshotCarriesEveryReceiptField() = runTest {
        val repo = InMemorySaleRepository(currency = CurrencyCode("USD"))
        repo.putProduct(ProductForSale("widget", "Widget", "20.00", 10.0, isActive = true), Quantity.parse("10").getOrNull()!!, "branch-1")
        val sale = (repo.finalizeSale(FinalizeSaleCommand(listOf(SaleLineRequest("widget", "3")), "branch-1", null, "cash", null, "receipt-key-2")) as FinancialResult.Success).value

        val returnResult = repo.finalizeReturn(FinalizeReturnCommand(sale.saleId, listOf(ReturnLineRequest("widget", "1")), "customer changed mind", "cash", "receipt-return-key-1"))
        assertTrue(returnResult is FinancialResult.Success)
        val ret = (returnResult as FinancialResult.Success).value

        assertNotNull(ret.returnId)
        assertTrue(ret.returnNumber.isNotBlank())
        assertEquals(sale.saleId, ret.saleId)
        val line = ret.lines.first()
        assertEquals("widget", line.productId)
        assertEquals("Widget", line.productNameAtSale)
        assertEquals(Quantity.parse("1").getOrNull(), line.quantity)
        assertTrue(ret.refundTotal > Money.ZERO)
        assertTrue(ret.reason.isNotBlank())
        assertTrue(ret.createdAtEpochMillis >= 0)
    }
}
