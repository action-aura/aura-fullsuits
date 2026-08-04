package com.actionaura.retail.usecases

import com.actionaura.retail.financial.Cart
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate
import com.actionaura.retail.financial.Quantity
import com.actionaura.retail.financial.TaxMode
import com.actionaura.retail.financial.addLine
import com.actionaura.retail.financial.createCartLine
import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * M5.5 mandatory follow-up (Cart Branch identity) -- real proof that
 * `Cart.toFinalizeSaleCommand` always sources `branchId` from the cart
 * itself, never from a separate, UI-suppliable value -- "UI parameters
 * cannot replace the Cart's Branch identity."
 */
class CartToSaleCommandTest {

    @Test
    fun commandBranchIdAlwaysMatchesCartBranchId() {
        val cart = Cart(branchId = 42L, lines = emptyList(), mode = TaxMode.AFTER_DISCOUNT)
        val command = cart.toFinalizeSaleCommand(customerId = null, paymentMethod = "cash", amountPaidRaw = null, idempotencyKey = "k1")
        assertEquals("42", command.branchId)
    }

    @Test
    fun differentCartsProduceCommandsWithTheirOwnDistinctBranchId() {
        // There is no shared/global "current branch" input to this
        // function at all -- each cart's own branchId is the only source,
        // proven by two carts producing two different command branchIds.
        val cartA = Cart(branchId = 1L, lines = emptyList(), mode = TaxMode.AFTER_DISCOUNT)
        val cartB = Cart(branchId = 2L, lines = emptyList(), mode = TaxMode.AFTER_DISCOUNT)

        assertEquals("1", cartA.toFinalizeSaleCommand(null, "cash", null, "k1").branchId)
        assertEquals("2", cartB.toFinalizeSaleCommand(null, "cash", null, "k2").branchId)
    }

    @Test
    fun lineDiscountAndQuantitySurviveConversion() {
        val line = createCartLine(
            "product-9", Money.of(10.0), Quantity.parse("3").getOrNull()!!,
            PercentageRate.clampToDiscountRange("10"), PercentageRate.trusted(0.0), TaxMode.AFTER_DISCOUNT,
        )
        val cart = Cart(branchId = 1L, lines = emptyList(), mode = TaxMode.AFTER_DISCOUNT).addLine(line)
        val command = cart.toFinalizeSaleCommand(null, "cash", null, "k1")

        assertEquals(1, command.lines.size)
        assertEquals("product-9", command.lines.first().productId)
        assertEquals("3", command.lines.first().quantityRaw)
        assertEquals(10.0, command.lines.first().discountPctRaw)
    }
}
