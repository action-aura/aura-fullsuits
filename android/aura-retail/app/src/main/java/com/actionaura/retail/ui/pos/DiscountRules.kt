package com.actionaura.retail.ui.pos

import com.actionaura.retail.net.SaleItemReq
import com.actionaura.retail.ui.CAP_DISCOUNT
import com.actionaura.retail.ui.holdsCapability
import java.math.BigDecimal
import java.math.RoundingMode

/**
 * Who may give a discount at the phone's till, and how it travels.
 *
 * A mirror of the desktop's three states (`_discountMode` in
 * frontend/subsystem-retail.js), decided by capability and the shop's own
 * cashier limit:
 *   Full    -- holds `retail.discount`: 0 to 100.
 *   Limited -- no capability, but the owner allowed up to N percent
 *              (`cashier_discount_limit_pct` in the shop settings): capped at N.
 *   None    -- no capability and the limit is 0: no discount control at all.
 *
 * THE SERVER IS THE AUTHORITY. `create_sale` refuses an over-limit discount with
 * 403 DISCOUNT_OVER_LIMIT (and a message that names the limit), and refuses any
 * discount from a session with neither the capability nor a limit. This file
 * only decides what the screen OFFERS and, as a belt on top of the braces,
 * refuses to put a percentage on the wire that the screen should never have
 * let the cashier type -- so a stale value left in state after a role change
 * cannot ride into a sale. No manager-PIN override exists in this wave: the
 * message says "Ask a manager", and the manager signs in.
 *
 * HOW A DISCOUNT TRAVELS: as `discount_pct` on each sale line, exactly as the
 * desktop sends it. The server derives the money from the percentage; the
 * phone never sends a discount amount. The whole-sale percentage is applied to
 * every line that does not carry its own; a line's own percentage wins for
 * that line (the two are never stacked -- a "10% off the sale" and a "5% off
 * this item" is 5% on that item, not 14.5%).
 */
sealed interface DiscountAccess {
    data object Full : DiscountAccess
    data class Limited(val limitPct: Double) : DiscountAccess
    data object None : DiscountAccess
}

fun discountAccess(capabilities: List<String>?, cashierLimitPct: Double): DiscountAccess = when {
    holdsCapability(capabilities, CAP_DISCOUNT) -> DiscountAccess.Full
    cashierLimitPct > 0.0 -> DiscountAccess.Limited(cashierLimitPct.coerceAtMost(100.0))
    else -> DiscountAccess.None
}

/** The most a person with this access may type; 0 when they may not discount. */
fun DiscountAccess.maxPct(): Double = when (this) {
    DiscountAccess.Full -> 100.0
    is DiscountAccess.Limited -> limitPct
    DiscountAccess.None -> 0.0
}

/** 0..100, two decimals, NaN / infinity / negative -> 0. Mirrors clamp_discount_pct. */
fun clampDiscountPct(p: Double?): Double {
    if (p == null || p.isNaN() || p.isInfinite() || p <= 0.0) return 0.0
    return BigDecimal(p.coerceAtMost(100.0).toString()).setScale(2, RoundingMode.HALF_UP).toDouble()
}

/** The limit if [pct] is above what [access] allows, else null (nothing wrong). */
fun overLimit(pct: Double, access: DiscountAccess): Double? {
    val asked = clampDiscountPct(pct)
    if (asked <= 0.0) return null
    val max = access.maxPct()
    return if (asked > max + 1e-9) max else null
}

/** Percentage one line travels with: its own if it has one, else the whole-sale one. */
fun effectiveLinePct(lineOwnPct: Double?, salePct: Double): Double =
    clampDiscountPct(lineOwnPct ?: salePct)

/**
 * The `items` array of a sale request. Product id, quantity, `discount_pct`, and (ANDROID-3) the
 * picked `modifier_option_ids` and a line's own `unit_price_override` -- all INTENT, the desktop's
 * names. The server resolves price and tax from the product row and the option ids (Wave 0
 * financial-authority contract), so no computed money is ever sent: an override is a price the
 * cashier chose, and the server demands a reason and, below the cashier's authority, a manager.
 * Under [DiscountAccess.None] every line goes out at 0% whatever the state holds. While a
 * sale-level amount / target discount is on ([cartDiscountOn]) every line goes at 0% too: the
 * server refuses the pair, and the desktop sends the same.
 */
fun buildSaleItems(
    lines: List<CartLine>,
    salePct: Double,
    access: DiscountAccess,
    cartDiscountOn: Boolean = false,
): List<SaleItemReq> = lines.map { l ->
    val pct = if (access == DiscountAccess.None || cartDiscountOn) 0.0 else effectiveLinePct(l.ownPct, salePct)
    SaleItemReq(
        product_id = l.productId, quantity = l.quantity, discount_pct = pct,
        modifier_option_ids = l.modifierOptionIds.takeIf { it.isNotEmpty() },
        unit_price_override = l.unitPriceOverride,
    )
}
