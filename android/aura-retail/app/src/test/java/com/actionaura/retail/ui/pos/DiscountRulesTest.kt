package com.actionaura.retail.ui.pos

import com.actionaura.retail.ui.CAP_DISCOUNT
import com.actionaura.retail.ui.CAP_SELL
import com.google.common.truth.Truth.assertThat
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Discount gating: capability allow AND deny, the cashier limit, and how the
 * percentage reaches each sale line (the way the desktop sends it).
 */
class DiscountRulesTest {

    private val cashierCaps = listOf(CAP_SELL, "retail.cash.close", "retail.refund")
    private val managerCaps = cashierCaps + CAP_DISCOUNT

    // ── who is offered what ──────────────────────────────────────────────────

    @Test
    fun a_holder_of_retail_discount_gets_full_access_up_to_100() {
        val a = discountAccess(managerCaps, cashierLimitPct = 0.0)
        assertEquals(DiscountAccess.Full, a)
        assertEquals(100.0, a.maxPct(), 0.0)
    }

    @Test
    fun a_cashier_without_the_capability_and_without_a_limit_gets_no_discount() {
        val a = discountAccess(cashierCaps, cashierLimitPct = 0.0)
        assertEquals(DiscountAccess.None, a)
        assertEquals(0.0, a.maxPct(), 0.0)
    }

    @Test
    fun a_cashier_gets_exactly_the_shops_limit_when_the_owner_set_one() {
        val a = discountAccess(cashierCaps, cashierLimitPct = 10.0)
        assertEquals(DiscountAccess.Limited(10.0), a)
        assertEquals(10.0, a.maxPct(), 0.0)
    }

    @Test
    fun capabilities_not_yet_known_fail_open_like_every_other_rendering_gate() {
        // RetailSession's documented default: null = "session not resolved yet". The
        // server re-gates the sale itself, so this only affects what is offered.
        assertEquals(DiscountAccess.Full, discountAccess(null, 0.0))
    }

    @Test
    fun an_empty_grant_list_is_a_resolved_answer_of_nothing() {
        assertEquals(DiscountAccess.None, discountAccess(emptyList(), 0.0))
    }

    // ── over the limit ───────────────────────────────────────────────────────

    @Test
    fun over_limit_reports_the_limit_and_at_or_under_reports_nothing() {
        val ltd = DiscountAccess.Limited(10.0)
        assertEquals(10.0, overLimit(10.5, ltd)!!, 0.0)
        assertNull(overLimit(10.0, ltd))
        assertNull(overLimit(0.0, ltd))
        assertEquals(0.0, overLimit(1.0, DiscountAccess.None)!!, 0.0)
        assertNull(overLimit(100.0, DiscountAccess.Full))
    }

    @Test
    fun clamp_matches_the_servers_clamp_discount_pct() {
        assertEquals(0.0, clampDiscountPct(-5.0), 0.0)
        assertEquals(0.0, clampDiscountPct(Double.NaN), 0.0)
        assertEquals(0.0, clampDiscountPct(null), 0.0)
        assertEquals(100.0, clampDiscountPct(250.0), 0.0)
        assertEquals(7.5, clampDiscountPct(7.5), 0.0)
        assertEquals(7.13, clampDiscountPct(7.1349), 0.0)
    }

    // ── how it travels: discount_pct on each line ────────────────────────────

    @Test
    fun the_whole_sale_percentage_goes_on_every_line_that_has_none_of_its_own() {
        val items = buildSaleItems(
            lines = listOf(CartLine("a", 2.0), CartLine("b", 1.0)),
            salePct = 10.0, access = DiscountAccess.Full,
        )
        assertThat(items.map { it.discount_pct }).containsExactly(10.0, 10.0)
        assertThat(items.map { it.product_id }).containsExactly("a", "b").inOrder()
    }

    @Test
    fun a_lines_own_percentage_wins_for_that_line_and_is_not_stacked() {
        val items = buildSaleItems(
            lines = listOf(CartLine("a", 2.0, ownPct = 5.0), CartLine("b", 1.0)),
            salePct = 10.0, access = DiscountAccess.Full,
        )
        assertEquals(5.0, items[0].discount_pct, 0.0)   // not 14.5
        assertEquals(10.0, items[1].discount_pct, 0.0)
    }

    @Test
    fun without_access_nothing_discounted_ever_reaches_the_wire_even_from_stale_state() {
        // Deny half: state left over from a manager's sign-in, then a cashier with no
        // limit signs in. The screen hides the control; this proves the request is clean too.
        val items = buildSaleItems(
            lines = listOf(CartLine("a", 1.0, ownPct = 50.0)),
            salePct = 20.0, access = DiscountAccess.None,
        )
        assertEquals(0.0, items.single().discount_pct, 0.0)
    }

    @Test
    fun quantities_and_ids_are_untouched() {
        val items = buildSaleItems(listOf(CartLine("a", 2.5)), 0.0, DiscountAccess.Full)
        assertEquals("a", items.single().product_id)
        assertEquals(2.5, items.single().quantity, 0.0)
    }
}
