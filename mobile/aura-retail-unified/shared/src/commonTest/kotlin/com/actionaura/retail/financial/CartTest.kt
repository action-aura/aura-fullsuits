package com.actionaura.retail.financial

import kotlin.test.Test
import kotlin.test.assertEquals

/** M5.5.11 -- real proof `Cart.branchId` ("cart retains its originating Branch") survives every pure cart transformation. */
class CartTest {

    private fun sampleLine(productId: String = "p1") =
        createCartLine(productId, Money.of(10.0), Quantity.parse("1").getOrNull()!!, PercentageRate.ZERO_RATE, PercentageRate.ZERO_RATE, TaxMode.AFTER_DISCOUNT)

    @Test
    fun branchIdSurvivesAddLine() {
        val cart = Cart(branchId = 7L, lines = emptyList(), mode = TaxMode.AFTER_DISCOUNT).addLine(sampleLine())
        assertEquals(7L, cart.branchId)
    }

    @Test
    fun branchIdSurvivesUpdateQuantity() {
        val cart = Cart(branchId = 7L, lines = listOf(sampleLine()), mode = TaxMode.AFTER_DISCOUNT)
            .updateLineQuantity("p1", Quantity.parse("2").getOrNull()!!)
        assertEquals(7L, cart.branchId)
    }

    @Test
    fun branchIdSurvivesDiscountAndRemove() {
        val cart = Cart(branchId = 7L, lines = listOf(sampleLine()), mode = TaxMode.AFTER_DISCOUNT)
            .applyLineDiscount("p1", PercentageRate.clampToDiscountRange("10"))
            .removeLine("p1")
        assertEquals(7L, cart.branchId)
    }
}
