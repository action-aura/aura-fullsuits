package com.actionaura.retail.financial

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * M3.2 -- curated parity cases against the real, exact vectors read from
 * products/retail/tests/retail_pricing_test.py (Part A) during the M3.0
 * audit. Each test's expected values are the literal numbers asserted in
 * the real Python test file, not independently re-derived -- this is the
 * first, hand-verified slice of the M3.5 differential suite; the full
 * generated-fixture harness follows in M3.5 proper.
 */
class CalculateLineTest {

    private fun money(v: String) = Money.parse(v).getOrNull()!!
    private fun qty(v: String) = Quantity.parse(v).getOrNull()!!

    @Test
    fun saleWithoutDiscount_afterMode() {
        // retail_pricing_test.py::test_sale_without_discount_after_mode
        val r = calculateLine(money("100"), qty("1"), discountPct = PercentageRate.clampToDiscountRange(0.0),
            taxRate = PercentageRate.trusted(15.0), mode = TaxMode.AFTER_DISCOUNT)
        assertEquals(money("100.00"), r.taxableAmount)
        assertEquals(money("15.00"), r.tax)
        assertEquals(money("115.00"), r.total)
    }

    @Test
    fun saleWithDiscount_afterMode() {
        // retail_pricing_test.py::test_sale_with_discount_after_mode
        val r = calculateLine(money("100"), qty("1"), discountPct = PercentageRate.clampToDiscountRange(10.0),
            taxRate = PercentageRate.trusted(15.0), mode = TaxMode.AFTER_DISCOUNT)
        assertEquals(money("10.00"), r.discountAmount)
        assertEquals(money("90.00"), r.taxableAmount)
        assertEquals(money("13.50"), r.tax)
        assertEquals(money("103.50"), r.total)
    }

    @Test
    fun taxBeforeDiscountMode() {
        // retail_pricing_test.py::test_tax_before_discount_mode
        val r = calculateLine(money("100"), qty("1"), discountPct = PercentageRate.clampToDiscountRange(10.0),
            taxRate = PercentageRate.trusted(15.0), mode = TaxMode.BEFORE_DISCOUNT)
        assertEquals(money("100.00"), r.taxableAmount)
        assertEquals(money("15.00"), r.tax)
        assertEquals(money("10.00"), r.discountAmount)
        assertEquals(money("105.00"), r.total)
    }

    @Test
    fun defaultModeIsAfterDiscount() {
        // retail_pricing_test.py::test_tax_after_discount_mode_is_the_default
        assertEquals(TaxMode.AFTER_DISCOUNT, TaxMode.DEFAULT)
    }

    @Test
    fun beforeAndAfterModesDivergeWhenDiscountNonzero() {
        // retail_pricing_test.py::test_before_and_after_discount_modes_diverge_when_discount_is_nonzero
        val after = calculateLine(money("100"), qty("1"), PercentageRate.clampToDiscountRange(20.0), PercentageRate.trusted(10.0), TaxMode.AFTER_DISCOUNT)
        val before = calculateLine(money("100"), qty("1"), PercentageRate.clampToDiscountRange(20.0), PercentageRate.trusted(10.0), TaxMode.BEFORE_DISCOUNT)
        assertTrue(after.tax != before.tax)
        assertTrue(after.total != before.total)
        assertTrue(before.tax > after.tax)
    }

    @Test
    fun beforeAndAfterModesAgreeWhenNoDiscount() {
        // retail_pricing_test.py::test_before_and_after_discount_modes_agree_when_no_discount
        val after = calculateLine(money("100"), qty("1"), PercentageRate.clampToDiscountRange(0.0), PercentageRate.trusted(10.0), TaxMode.AFTER_DISCOUNT)
        val before = calculateLine(money("100"), qty("1"), PercentageRate.clampToDiscountRange(0.0), PercentageRate.trusted(10.0), TaxMode.BEFORE_DISCOUNT)
        assertEquals(after, before)
    }

    @Test
    fun unknownModeFallsBackToDefault() {
        // retail_pricing_test.py::test_unknown_mode_falls_back_to_default
        assertEquals(TaxMode.DEFAULT, TaxMode.parseOrDefault("nonsense"))
        assertEquals(TaxMode.DEFAULT, TaxMode.parseOrDefault(null))
        assertEquals(TaxMode.DEFAULT, TaxMode.parseOrDefault(""))
    }

    @Test
    fun zeroTaxRate() {
        // retail_pricing_test.py::test_zero_tax_rate
        val r = calculateLine(money("50"), qty("2"), PercentageRate.clampToDiscountRange(10.0), PercentageRate.trusted(0.0), TaxMode.AFTER_DISCOUNT)
        assertEquals(Money.ZERO, r.tax)
        assertEquals(r.taxableAmount, r.total)
    }

    @Test
    fun discountClampedAboveOneHundred() {
        // Mirrors pricing.clamp_discount_pct's real, exact clamp behavior.
        val rate = PercentageRate.clampToDiscountRange(150.0)
        val r = calculateLine(money("100"), qty("1"), rate, PercentageRate.ZERO_RATE, TaxMode.AFTER_DISCOUNT)
        assertEquals(money("100.00"), r.discountAmount) // clamped to 100% -> full discount
    }

    @Test
    fun discountClampedBelowZero() {
        val rate = PercentageRate.clampToDiscountRange(-10.0)
        val r = calculateLine(money("100"), qty("1"), rate, PercentageRate.ZERO_RATE, TaxMode.AFTER_DISCOUNT)
        assertEquals(Money.ZERO, r.discountAmount)
    }

    @Test
    fun changeCalculation() {
        // create_sale(): change = max(0, paid - total) -- invariant #15
        assertEquals(money("5.00"), calculateChange(money("105.00"), money("100.00")))
        assertEquals(Money.ZERO, calculateChange(money("80.00"), money("100.00"))) // underpayment -> 0 change, not negative
    }

    @Test
    fun quantityRejectsNaNAndInfinity_closedPythonGap() {
        // financial-invariant-catalog.md invariant #7a -- real Python defect closed here.
        assertTrue(Quantity.parse("nan") is FinancialResult.Failure)
        assertTrue(Quantity.parse("NaN") is FinancialResult.Failure)
        assertTrue(Quantity.parse("inf") is FinancialResult.Failure)
        assertTrue(Quantity.parse("Infinity") is FinancialResult.Failure)
        assertTrue(Quantity.parse("-inf") is FinancialResult.Failure)
    }

    @Test
    fun quantityRejectsNonPositive() {
        val zero = Quantity.parse("0")
        val negative = Quantity.parse("-1")
        assertTrue(zero is FinancialResult.Failure)
        assertTrue((zero as FinancialResult.Failure).error.code == "NON_POSITIVE_QUANTITY")
        assertTrue(negative is FinancialResult.Failure)
    }

    @Test
    fun fractionalQuantitySupported() {
        // Python's float(quantity) accepts decimals (e.g. weight-based items) -- not an integer-only type.
        val r = calculateLine(money("10"), qty("1.5"), PercentageRate.ZERO_RATE, PercentageRate.ZERO_RATE, TaxMode.AFTER_DISCOUNT)
        assertEquals(money("15.00"), r.total)
    }
}
