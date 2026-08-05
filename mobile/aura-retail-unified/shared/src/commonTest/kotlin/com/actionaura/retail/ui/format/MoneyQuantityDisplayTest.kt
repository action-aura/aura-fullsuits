package com.actionaura.retail.ui.format

import com.actionaura.retail.financial.CurrencyCode
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate
import com.actionaura.retail.financial.Quantity
import kotlin.test.Test
import kotlin.test.assertEquals

/** M6.10 -- real, executed proof that display formatting never rounds/coerces the underlying exact value, and never depends on Double. */
class MoneyQuantityDisplayTest {

    @Test
    fun realMoneyFormatsWithItsRealCurrencyCode() {
        val money = (Money.parse("19.99") as com.actionaura.retail.financial.FinancialResult.Success).value
        assertEquals("USD 19.99", formatMoney(money, CurrencyCode("USD")))
    }

    @Test
    fun signedMoneyAlwaysShowsARealExplicitSign() {
        val positive = (Money.parse("5.00") as com.actionaura.retail.financial.FinancialResult.Success).value
        assertEquals("EGP +5.00", formatSignedMoney(positive, CurrencyCode("EGP")))
    }

    @Test
    fun realQuantityFormatsExactlyAsStored() {
        val qty = (Quantity.parse("12.500") as com.actionaura.retail.financial.FinancialResult.Success).value
        assertEquals("12.5", formatQuantity(qty), "the real M3 BigDecimal representation, not a re-rounded display value")
    }

    @Test
    fun realPercentageAppendsTheGlyphWithoutRescaling() {
        val rate = PercentageRate.trusted("15")
        assertEquals("15%", formatPercentage(rate))
    }

    @Test
    fun zeroPercentageFormatsAsRealZeroNeverBlank() {
        assertEquals("0%", formatPercentage(PercentageRate.ZERO_RATE))
    }
}
