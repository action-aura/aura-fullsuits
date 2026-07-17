package com.actionaura.retail.ui.i18n

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Phase 4Q: rounding/formatting contract tests for the display-layer number
 * helpers used throughout the POS UI. These format server-authoritative
 * values for display; they do not compute financial authority themselves
 * (see SaleContractTest for that boundary). */
class NumTest {

    @Test
    fun money_formats_two_decimals_western_digits() {
        assertEquals("$88.00", money(88.0))
        assertEquals("$0.00", money(0.0))
        assertEquals("$1234.50", money(1234.5))
    }

    @Test
    fun money_rounds_half_up_at_the_display_boundary() {
        // String.format's HALF_UP rounding for display -- the actual
        // financial rounding authority is server-side Decimal/ROUND_HALF_UP
        // (core/retail/pricing.py); this only proves display doesn't
        // introduce its own truncation/half-even surprise.
        assertEquals("$1.24", money(1.235))
        assertEquals("$1.23", money(1.234))
    }

    @Test
    fun parseNum_tolerates_arabic_indic_digits_and_decimal_mark() {
        assertEquals(19.5, parseNum("١٩٫٥"))   // Arabic-Indic "19.5"
        assertEquals(100.0, parseNum("100"))
    }

    @Test
    fun parseNum_blank_or_null_is_null_not_zero() {
        assertNull(parseNum(null))
        assertNull(parseNum(""))
        assertNull(parseNum("   "))
    }

    @Test
    fun fmtQty_trims_whole_numbers_keeps_fractions() {
        assertEquals("3", fmtQty(3.0))
        assertEquals("2.5", fmtQty(2.5))
        assertEquals("1.25", fmtQty(1.25))
    }
}
