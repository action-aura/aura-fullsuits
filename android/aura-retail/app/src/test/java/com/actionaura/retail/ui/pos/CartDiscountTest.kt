package com.actionaura.retail.ui.pos

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class CartDiscountTest {

    private fun disc(kind: CartDiscountKind, minor: Long, reason: String = "Damaged") = CartDiscount(kind, minor, reason, "sig")

    // ── round down chips (desktop _dsRoundDownChips) ─────────────────────────
    @Test
    fun round_down_goes_to_the_nearest_step_strictly_below() {
        // 10.650 -> 10.500 (0.25 and 0.5 steps), 10.000 (1.0 step)
        assertThat(roundDownTargets(10_650, 3)).containsExactly(10_500L, 10_000L).inOrder()
    }

    @Test
    fun a_total_already_on_a_step_is_pushed_one_step_down_not_left_alone() {
        // 10.500 is on the 0.25 and 0.5 steps: strictly below means 10.250 / 10.000
        assertThat(roundDownTargets(10_500, 3)).containsExactly(10_250L, 10_000L).inOrder()
        assertThat(roundDownTargets(10_000, 3)).containsExactly(9_750L, 9_500L, 9_000L).inOrder()
    }

    @Test
    fun a_tiny_total_offers_no_chip_at_or_below_zero() {
        assertThat(roundDownTargets(200, 3)).isEmpty()
        assertThat(roundDownTargets(0, 3)).isEmpty()
    }

    @Test
    fun a_two_decimal_currency_uses_the_same_steps() {
        assertThat(roundDownTargets(1_065, 2)).containsExactly(1_050L, 1_000L).inOrder()
    }

    // ── estimates ────────────────────────────────────────────────────────────
    private val brief = listOf(EstimateLine(unitPrice = 5.000, quantity = 1.0, discountPct = 0.0, taxRatePct = 16.0),
        EstimateLine(unitPrice = 5.750, quantity = 1.0, discountPct = 0.0, taxRatePct = 16.0))

    @Test
    fun an_amount_discount_comes_off_before_tax() {
        // subtotal 10.750, tax 16% = 12.470 ; 0.650 off the net -> (10.100) * 1.16 = 11.716
        assertThat(grossSubtotalMinor(brief, 3)).isEqualTo(10_750L)
        assertThat(estimateWithDiscountMinor(brief, null, 3)).isEqualTo(12_470L)
        assertThat(estimateWithDiscountMinor(brief, disc(CartDiscountKind.AMOUNT, 650), 3)).isEqualTo(11_716L)
    }

    @Test
    fun a_target_total_is_its_own_estimate() {
        assertThat(estimateWithDiscountMinor(brief, disc(CartDiscountKind.TARGET, 12_000), 3)).isEqualTo(12_000L)
    }

    @Test
    fun an_amount_larger_than_the_cart_cannot_make_a_negative_estimate() {
        assertThat(estimateAfterAmountMinor(brief, 99_000, 3)).isEqualTo(0L)
    }

    // ── draft checks ─────────────────────────────────────────────────────────
    @Test
    fun the_sheet_refuses_the_impossible_on_the_spot() {
        val g = 10_750L; val t = 12_470L
        assertThat(checkDraft(CartDiscountKind.AMOUNT, 0, g, t)).isEqualTo(DraftCheck.EMPTY)
        assertThat(checkDraft(CartDiscountKind.AMOUNT, 650, g, t)).isEqualTo(DraftCheck.OK)
        assertThat(checkDraft(CartDiscountKind.AMOUNT, 10_751, g, t)).isEqualTo(DraftCheck.TOO_BIG)
        assertThat(checkDraft(CartDiscountKind.TARGET, 12_000, g, t)).isEqualTo(DraftCheck.OK)
        assertThat(checkDraft(CartDiscountKind.TARGET, 12_471, g, t)).isEqualTo(DraftCheck.TARGET_HIGH)
        assertThat(checkDraft(CartDiscountKind.TARGET, 12_470, g, t)).isEqualTo(DraftCheck.EMPTY)
    }

    // ── who is asked for a PIN (allow AND deny) ──────────────────────────────
    @Test
    fun a_holder_of_the_discount_capability_is_never_asked() {
        assertThat(managerApprovalNeeded(true, 0.0, disc(CartDiscountKind.AMOUNT, 900), 99.0, true)).isFalse()
    }

    @Test
    fun a_cashier_with_no_limit_is_asked_for_any_cart_discount_and_any_own_price() {
        assertThat(managerApprovalNeeded(false, 0.0, disc(CartDiscountKind.AMOUNT, 1), 0.01, false)).isTrue()
        assertThat(managerApprovalNeeded(false, 0.0, null, 0.0, true)).isTrue()
        // and not asked when nothing needs approval
        assertThat(managerApprovalNeeded(false, 0.0, null, 0.0, false)).isFalse()
    }

    @Test
    fun a_cashier_within_the_limit_is_not_asked_over_it_is() {
        assertThat(managerApprovalNeeded(false, 10.0, disc(CartDiscountKind.AMOUNT, 100), 9.99, false)).isFalse()
        assertThat(managerApprovalNeeded(false, 10.0, disc(CartDiscountKind.AMOUNT, 100), 10.0, false)).isFalse()
        assertThat(managerApprovalNeeded(false, 10.0, disc(CartDiscountKind.AMOUNT, 100), 10.01, false)).isTrue()
        // an own price is asked about whatever the limit is
        assertThat(managerApprovalNeeded(false, 50.0, null, 0.0, true)).isTrue()
    }

    @Test
    fun the_effective_percentage_follows_the_servers_measure() {
        assertThat(effectiveDiscountPct(disc(CartDiscountKind.AMOUNT, 650), 10_750, 12_470)).isWithin(1e-6).of(650 * 100.0 / 10_750)
        assertThat(effectiveDiscountPct(disc(CartDiscountKind.TARGET, 11_223), 10_750, 12_470)).isWithin(1e-3).of(10.0)
        assertThat(effectiveDiscountPct(disc(CartDiscountKind.AMOUNT, 5), 0, 0)).isEqualTo(0.0)
    }

    // ── reasons ──────────────────────────────────────────────────────────────
    @Test
    fun a_preset_reason_travels_translated_and_other_travels_as_typed_and_capped() {
        assertThat(discountReasonText("Damaged", "ignored") { "T:$it" }).isEqualTo("T:Damaged")
        assertThat(discountReasonText(REASON_OTHER, "  my reason  ") { "T:$it" }).isEqualTo("my reason")
        assertThat(discountReasonText(REASON_OTHER, "x".repeat(500)) { it }).hasLength(DISCOUNT_REASON_MAX)
        assertThat(discountReasonText(null, "typed") { it }).isEmpty()
        assertThat(discountReasonText(REASON_OTHER, "   ") { it }).isEmpty()
    }

    @Test
    fun the_sale_reason_is_the_sheets_or_the_joined_price_reasons() {
        val lines = listOf(
            CartLine("a", 1.0, unitPriceOverride = 1.0, priceReason = "Damaged"),
            CartLine("b", 1.0, unitPriceOverride = 2.0, priceReason = "Damaged"),
            CartLine("c", 1.0, unitPriceOverride = 3.0, priceReason = "Loyal customer"),
            CartLine("d", 1.0, priceReason = "stale, no override"),
        )
        assertThat(saleDiscountReason(null, lines)).isEqualTo("Damaged; Loyal customer")
        assertThat(saleDiscountReason(disc(CartDiscountKind.TARGET, 1, "Round down"), lines)).isEqualTo("Round down")
        assertThat(saleDiscountReason(null, listOf(CartLine("d", 1.0)))).isNull()
    }
}
