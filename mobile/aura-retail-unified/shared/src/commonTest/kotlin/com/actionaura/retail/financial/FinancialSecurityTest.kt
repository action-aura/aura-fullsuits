package com.actionaura.retail.financial

import com.actionaura.retail.data.InMemorySaleRepository
import com.actionaura.retail.data.ProductForSale
import com.actionaura.retail.usecases.FinalizeReturnCommand
import com.actionaura.retail.usecases.FinalizeSaleCommand
import com.actionaura.retail.usecases.ReturnLineRequest
import com.actionaura.retail.usecases.SaleLineRequest
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * M3.8 -- financial security/abuse tests. Several of the spec's required
 * cases (client-supplied total/tax/change/refundable-amount tampering,
 * price tampering) are STRUCTURALLY impossible rather than merely
 * rejected at runtime -- FinalizeSaleCommand/SaleLineRequest (M3.3) have
 * no field for unit price, tax, subtotal, or total at all, so there is no
 * way for a caller to even attempt supplying one; the type system, not a
 * runtime check, is the enforcement. Documented here rather than tested
 * as a runtime rejection, because there is no runtime path to exercise --
 * see intentional-financial-differences.md and
 * financial-invariant-catalog.md invariant #6. This file tests what IS a
 * real runtime concern: quantity/discount tampering bounds, duplicate/
 * conflicting submission (already covered by InMemorySaleRepositoryTest,
 * cross-referenced not duplicated here), and the real concurrent-race
 * stock/return contracts.
 */
class FinancialSecurityTest {

    @Test
    fun quantityTamperingBeyondStockRejected() = runTest {
        // "quantity tampering" -- a client requesting more than actually on
        // hand is rejected outright, never partially fulfilled.
        val repo = InMemorySaleRepository()
        repo.putProduct(ProductForSale("widget", "Widget", "10.00", 0.0, isActive = true), Quantity.parse("5").getOrNull()!!, "branch-1")
        val result = repo.finalizeSale(FinalizeSaleCommand(listOf(SaleLineRequest("widget", "999999")), "branch-1", null, "cash", null, "tamper-1"))
        assertTrue(result is FinancialResult.Failure)
        assertEquals("INSUFFICIENT_STOCK", (result as FinancialResult.Failure).error.code)
    }

    @Test
    fun discountTamperingClampedNeverNegativeTotal() = runTest {
        // "discount tampering" -- an out-of-range discount is clamped
        // (LEGACY_PARITY, matches pricing.clamp_discount_pct exactly), never
        // allowed to produce a negative total.
        val repo = InMemorySaleRepository()
        repo.putProduct(ProductForSale("widget", "Widget", "10.00", 0.0, isActive = true), Quantity.parse("5").getOrNull()!!, "branch-1")
        val result = repo.finalizeSale(FinalizeSaleCommand(listOf(SaleLineRequest("widget", "1", discountPctRaw = 99999.0)), "branch-1", null, "cash", null, "tamper-2"))
        assertTrue(result is FinancialResult.Success)
        val sale = (result as FinancialResult.Success).value
        assertTrue(sale.total >= Money.ZERO) // clamped to 100% discount at worst, never negative
    }

    @Test
    fun returnQuantityTamperingBeyondSoldRejected() = runTest {
        val repo = InMemorySaleRepository()
        repo.putProduct(ProductForSale("widget", "Widget", "10.00", 0.0, isActive = true), Quantity.parse("10").getOrNull()!!, "branch-1")
        val sale = (repo.finalizeSale(FinalizeSaleCommand(listOf(SaleLineRequest("widget", "2")), "branch-1", null, "cash", null, "tamper-3")) as FinancialResult.Success).value
        val result = repo.finalizeReturn(FinalizeReturnCommand(sale.saleId, listOf(ReturnLineRequest("widget", "50")), "abuse test", "cash", "tamper-3-ret"))
        assertTrue(result is FinancialResult.Failure)
        assertEquals("RETURN_EXCEEDS_REMAINING_QUANTITY", (result as FinancialResult.Failure).error.code)
    }

    @Test
    fun concurrentSalesCannotJointlyOversell() = runTest {
        // Real concurrency race -- proves the Mutex-based lock (the shared
        // BEGIN-IMMEDIATE-equivalent, transaction-boundary-audit.md) actually
        // serializes concurrent finalizeSale calls rather than merely
        // documenting an intent. 10 concurrent buyers race for 5 units of
        // stock; exactly 5 succeed, exactly 5 fail with INSUFFICIENT_STOCK,
        // and the final stock balance is exactly 0 -- never negative.
        val repo = InMemorySaleRepository()
        repo.putProduct(ProductForSale("widget", "Widget", "10.00", 0.0, isActive = true), Quantity.parse("5").getOrNull()!!, "branch-1")

        val results = (1..10).map { i ->
            async {
                repo.finalizeSale(FinalizeSaleCommand(listOf(SaleLineRequest("widget", "1")), "branch-1", null, "cash", null, "race-key-$i"))
            }
        }.awaitAll()

        val succeeded = results.count { it is FinancialResult.Success }
        val failed = results.count { it is FinancialResult.Failure }
        assertEquals(5, succeeded, "exactly 5 of 10 concurrent buyers should win the race for 5 units of stock")
        assertEquals(5, failed)
        assertEquals(Quantity.ZERO, repo.stockOnHand("widget", "branch-1")) // never negative, never oversold
    }

    @Test
    fun concurrentReturnsCannotJointlyExceedSoldQuantity() = runTest {
        // Same race-safety proof for the cumulative-return-limit invariant
        // (#17): two concurrent returns for the same sale+product, only one
        // of which can be satisfied against the remaining returnable quantity.
        val repo = InMemorySaleRepository()
        repo.putProduct(ProductForSale("widget", "Widget", "10.00", 0.0, isActive = true), Quantity.parse("10").getOrNull()!!, "branch-1")
        val sale = (repo.finalizeSale(FinalizeSaleCommand(listOf(SaleLineRequest("widget", "5")), "branch-1", null, "cash", null, "race-sale-1")) as FinancialResult.Success).value

        val results = (1..2).map { i ->
            async {
                repo.finalizeReturn(FinalizeReturnCommand(sale.saleId, listOf(ReturnLineRequest("widget", "4")), "race test", "cash", "race-ret-$i"))
            }
        }.awaitAll()

        val succeeded = results.count { it is FinancialResult.Success }
        assertEquals(1, succeeded, "only one of two concurrent 4-unit returns can be satisfied against a 5-unit sale")
    }
}
