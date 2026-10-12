package com.actionaura.retail.ui.pos

import com.google.common.truth.Truth.assertThat
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Split tender arithmetic, in the 3-decimal dinar the shop actually trades in.
 *
 * The cases are chosen so a float implementation, a rounding slip, or a
 * dropped server rule each turns one of them red:
 *  - 0.1 + 0.2 style pairs (Double addition is wrong at the third decimal);
 *  - the allow side (a cash part larger than the remainder is CHANGE, and is
 *    chargeable) as well as the deny side (a card part larger than the
 *    remainder is refused, as the server refuses it).
 */
class SplitTenderTest {

    private val dp = 3

    private fun cash(minor: Long) = TenderPartMinor(METHOD_CASH, minor)
    private fun card(minor: Long) = TenderPartMinor("card", minor)

    // ── exact minor units ────────────────────────────────────────────────────

    @Test
    fun toMinor_is_exact_where_double_arithmetic_is_not() {
        // 0.1 + 0.2 is 0.30000000000000004 in a Double; as minor units it is 300.
        assertEquals(300L, toMinor(0.1, dp) + toMinor(0.2, dp))
        assertEquals(11832L, toMinor(11.832, dp))
        assertEquals(1L, toMinor(0.001, dp))
        assertEquals(5L, toMinor(0.05, 2))
    }

    @Test
    fun parseAmountMinor_accepts_arabic_digits_and_refuses_junk_and_extra_decimals() {
        assertEquals(5000L, parseAmountMinor("5", dp))
        assertEquals(6832L, parseAmountMinor("6.832", dp))
        // Arabic-Indic digits with the Arabic decimal mark, as an Arabic keyboard types them.
        assertEquals(6832L, parseAmountMinor("٦٫٨٣٢", dp))
        assertNull(parseAmountMinor("5.0005", dp))   // a half fil is a typo, not an amount
        assertNull(parseAmountMinor("0", dp))
        assertNull(parseAmountMinor("-3", dp))
        assertNull(parseAmountMinor("abc", dp))
        assertNull(parseAmountMinor("", dp))
        assertNull(parseAmountMinor(null, dp))
    }

    @Test
    fun minorToInput_round_trips_at_the_currency_precision() {
        assertEquals("5.000", minorToInput(5000L, dp))
        assertEquals("0.001", minorToInput(1L, dp))
        assertEquals("12", minorToInput(12L, 0))
    }

    // ── Remaining and coverage, at three decimals ────────────────────────────

    @Test
    fun remaining_shrinks_as_parts_are_added_and_never_goes_negative() {
        val due = 11832L
        assertEquals(11832L, summarizeSplit(due, emptyList(), false).remainingMinor)
        val s = summarizeSplit(due, listOf(card(5000L)), false)
        assertEquals(6832L, s.remainingMinor)
        assertThat(s.chargeable).isFalse()
        assertEquals(SplitProblem.SHORT, s.problem)

        val over = summarizeSplit(due, listOf(card(5000L), cash(10000L)), false)
        assertEquals(0L, over.remainingMinor)
    }

    @Test
    fun charge_is_enabled_exactly_when_the_parts_cover_the_total_to_the_fil() {
        val due = 11832L
        // One fil short: not chargeable. Exactly covered: chargeable. (Mutation: >= -> > or off-by-one flips one of these.)
        assertEquals(SplitProblem.SHORT, summarizeSplit(due, listOf(card(5000L), cash(6831L)), false).problem)
        val exact = summarizeSplit(due, listOf(card(5000L), cash(6832L)), false)
        assertNull(exact.problem)
        assertThat(exact.chargeable).isTrue()
        assertEquals(0L, exact.changeMinor)
    }

    @Test
    fun a_cash_part_above_the_remainder_is_change_and_still_chargeable() {
        // card 5.000 + cash 10.000 against 11.832 -> 3.168 comes back.
        val s = summarizeSplit(11832L, listOf(card(5000L), cash(10000L)), false)
        assertNull(s.problem)
        assertEquals(3168L, s.changeMinor)
    }

    // ── the server's refusals, mirrored (deny half) ──────────────────────────

    @Test
    fun a_card_part_larger_than_what_is_left_is_refused_because_only_cash_gives_change() {
        val s = summarizeSplit(11832L, listOf(card(5000L), TenderPartMinor("mobile", 7000L)), false)
        assertEquals(SplitProblem.NON_CASH_OVERPAID, s.problem)
        assertEquals(0L, s.changeMinor)
    }

    @Test
    fun a_cash_part_that_would_all_come_back_as_change_is_refused() {
        // card already covers it; the cash would be 100% change.
        val s = summarizeSplit(5000L, listOf(card(5000L), cash(1000L)), false)
        assertEquals(SplitProblem.CASH_NOT_NEEDED, s.problem)
    }

    @Test
    fun a_part_on_account_needs_a_customer_and_must_be_exactly_the_rest() {
        val parts = listOf(cash(5000L), TenderPartMinor(METHOD_CREDIT, 6832L))
        assertEquals(SplitProblem.CREDIT_NEEDS_CUSTOMER, summarizeSplit(11832L, parts, hasCustomer = false).problem)
        assertNull(summarizeSplit(11832L, parts, hasCustomer = true).problem)
        // credit above the remainder: refused, never silently change.
        val tooMuch = listOf(cash(5000L), TenderPartMinor(METHOD_CREDIT, 7000L))
        assertEquals(SplitProblem.CREDIT_OVERPAID, summarizeSplit(11832L, tooMuch, hasCustomer = true).problem)
    }

    @Test
    fun the_same_method_twice_is_merged_and_more_than_six_parts_is_refused() {
        val merged = addTenderPart(addTenderPart(emptyList(), "Cash", 5000L), "cash", 3000L)
        assertEquals(listOf(cash(8000L)), merged)

        val seven = (1..7).map { TenderPartMinor("m$it", 1000L) }
        assertEquals(SplitProblem.TOO_MANY_PARTS, summarizeSplit(7000L, seven, false).problem)
    }

    @Test
    fun removing_a_part_gives_the_amount_back_to_remaining() {
        val parts = listOf(card(5000L), cash(6832L))
        val after = removeTenderPart(parts, 0)
        assertEquals(listOf(cash(6832L)), after)
        assertEquals(5000L, summarizeSplit(11832L, after, false).remainingMinor)
    }

    @Test
    fun wire_tenders_carry_the_parts_in_order_with_exact_amounts() {
        val wire = toWireTenders(listOf(card(5000L), cash(6832L)), dp)
        assertThat(wire.map { it.method }).containsExactly("card", "cash").inOrder()
        assertThat(wire.map { it.amount }).containsExactly(5.0, 6.832).inOrder()
    }

    // ── the display estimate ─────────────────────────────────────────────────

    @Test
    fun estimate_applies_discount_then_tax_per_line_rounding_once_each() {
        // 2.500 x 3 = 7.500 gross; 7.5% off = 0.5625 -> 0.563 (half-up); taxable 6.937;
        // 16% tax = 1.10992 -> 1.110; line = 8.047. The same figures were run through
        // the server's own calculate_line (JOD) and agree -- see the ROADMAP note.
        val m = estimateDueMinor(listOf(EstimateLine(2.5, 3.0, 7.5, 16.0)), dp)
        assertEquals(8047L, m)
    }

    @Test
    fun estimate_of_a_plain_cart_is_the_sum_of_price_times_quantity() {
        val m = estimateDueMinor(
            listOf(EstimateLine(1.25, 2.0, 0.0, 0.0), EstimateLine(0.333, 3.0, 0.0, 0.0)), dp)
        assertEquals(2500L + 999L, m)
    }
}
