package com.actionaura.retail.ui.i18n

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

/** Phase 4Q: rounding/formatting contract tests for the display-layer number
 * helpers used throughout the POS UI. These format server-authoritative
 * values for display; they do not compute financial authority themselves
 * (see SaleContractTest for that boundary). */
class NumTest {

    /**
     * Pin the currency these cases format in.
     *
     * `money()` became currency-aware (the Jordanian dinar has THREE decimal
     * places, and the shipped default is now JOD, not the hard-coded dollar
     * these tests were written against). The two cases below are about FORMAT
     * MECHANICS -- western digits, and HALF_UP rounding at the display boundary
     * -- and neither is about which currency a shop is configured for. Their
     * assertions are therefore unchanged; only the dependency is made explicit.
     *
     * That coupling was the real flaw: a rounding test should not break because
     * a commercial default changed. Currency behaviour has its own case below.
     *
     * Reset in @After because `Currency` is process-global and these tests share
     * a JVM -- leaving it pinned would silently decide the format for every
     * other suite that runs after this one.
     */
    @Before
    fun pinCurrency() {
        Currency.apply("$", 2)
    }

    @After
    fun restoreCurrency() {
        Currency.apply("JD", 3)
    }

    @Test
    fun money_formats_two_decimals_western_digits() {
        assertEquals("$88.00", money(88.0))
        assertEquals("$0.00", money(0.0))
        assertEquals("$1234.50", money(1234.5))
    }

    /**
     * The behaviour the format mechanics above deliberately do not cover.
     *
     * A Jordanian shop's dinar carries fils -- 2.375 JOD is a real price, and
     * rendering it as 2.38 loses money on screen that the server did persist.
     * A multi-letter mark takes a space; a single-character one hugs its digits,
     * matching the web till's rule exactly (subsystem-retail.js::
     * _currencyPrefix) so the two clients cannot drift.
     */
    @Test
    fun money_follows_the_configured_currency_not_a_hard_coded_dollar() {
        Currency.apply("JD", 3)
        assertEquals("JD 2.375", money(2.375))
        assertEquals("JD 0.000", money(0.0))

        Currency.apply("$", 2)
        assertEquals("$2.38", money(2.375))
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
