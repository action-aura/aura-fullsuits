package com.actionaura.retail.ui.pos

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class LineMenuAndKeypadTest {

    // ── line menu ────────────────────────────────────────────────────────────
    @Test
    fun a_price_of_its_own_needs_a_reason() {
        val out = applyLineMenu("", "3.000", "", 3.5, DiscountAccess.Full, 3)
        assertThat(out.error).isEqualTo(LineMenuError.REASON_REQUIRED)
        val ok = applyLineMenu("", "3.000", " Damaged ", 3.5, DiscountAccess.Full, 3)
        assertThat(ok.error).isNull()
        assertThat(ok.unitPriceOverride).isEqualTo(3.0)
        assertThat(ok.priceReason).isEqualTo("Damaged")
    }

    @Test
    fun the_list_price_is_no_override_so_it_needs_no_reason() {
        val out = applyLineMenu("", "3.500", "", 3.5, DiscountAccess.Full, 3)
        assertThat(out.error).isNull()
        assertThat(out.unitPriceOverride).isNull()
        assertThat(out.priceReason).isNull()
    }

    @Test
    fun a_percentage_is_capped_by_the_access_allow_and_deny() {
        assertThat(applyLineMenu("10", "", "", 1.0, DiscountAccess.Limited(10.0), 3).ownPct).isEqualTo(10.0)
        val over = applyLineMenu("10.5", "", "", 1.0, DiscountAccess.Limited(10.0), 3)
        assertThat(over.error).isEqualTo(LineMenuError.PCT_TOO_HIGH)
        assertThat(over.limitPct).isEqualTo(10.0)
        assertThat(applyLineMenu("5", "", "", 1.0, DiscountAccess.None, 3).error).isEqualTo(LineMenuError.PCT_TOO_HIGH)
        assertThat(applyLineMenu("", "", "", 1.0, DiscountAccess.None, 3).ownPct).isNull()
    }

    @Test
    fun a_bad_price_is_named() {
        assertThat(applyLineMenu("", "-1", "x", 1.0, DiscountAccess.Full, 3).error).isEqualTo(LineMenuError.PRICE_NEGATIVE)
        assertThat(applyLineMenu("", "abc", "x", 1.0, DiscountAccess.Full, 3).error).isEqualTo(LineMenuError.PRICE_INVALID)
    }

    @Test
    fun a_price_is_rounded_to_the_currency_decimals() {
        assertThat(applyLineMenu("", "2.6666", "x", 3.0, DiscountAccess.Full, 3).unitPriceOverride).isEqualTo(2.667)
    }

    // ── keypad ───────────────────────────────────────────────────────────────
    @Test
    fun the_keypad_edits_like_a_calculator() {
        var v = ""
        for (k in listOf(".", "6", "5", "0")) v = padEdit(v, k, 3)
        assertThat(v).isEqualTo("0.650")
        assertThat(padEdit(v, "1", 3)).isEqualTo("0.650")           // a 4th decimal is ignored, never rounded
        assertThat(padEdit("0", "5", 3)).isEqualTo("5")             // no leading zero
        assertThat(padEdit("", "00", 3)).isEqualTo("")              // "00" on an empty box does nothing
        assertThat(padEdit("1.5", ".", 3)).isEqualTo("1.5")         // one dot only
        assertThat(padEdit("12", KEY_BACK, 3)).isEqualTo("1")
        assertThat(padEdit("5", ".", 0)).isEqualTo("5")             // a currency with no decimals has no dot
        assertThat(padEdit("123456789", "1", 3)).isEqualTo("123456789")  // nine digits at most
    }

    @Test
    fun the_pin_pad_takes_four_digits_and_nothing_else() {
        var p = ""
        for (k in listOf("1", "2", "3", "4", "5")) p = pinEdit(p, k)
        assertThat(p).isEqualTo("1234")
        assertThat(pinEdit(p, "x")).isEqualTo("1234")
        assertThat(pinEdit(p, ".")).isEqualTo("1234")
        assertThat(pinEdit(p, KEY_BACK)).isEqualTo("123")
        assertThat(pinEdit("", KEY_BACK)).isEqualTo("")
    }
}
