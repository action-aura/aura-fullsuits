package com.actionaura.retail.ui.pos

import com.actionaura.retail.ui.i18n.parseNum

/**
 * What a cashier typed in ONE line's menu (item discount %, a price of its own, why), turned into
 * the line's new state -- or the first thing wrong with it. Pure, so the rules are testable; the
 * dialog only draws the answer. The desktop's `_lineMenuApply` is the model.
 *
 *  - The % is capped at what [access] allows (a typed 0 or blank = no percentage of its own).
 *  - A price equal to the product's list price is no override at all (nothing to approve or explain).
 *  - A price of its own needs a reason -- the server refuses an override without one, so a given-away
 *    line is never anonymous.
 *  - A price that is not a number, or is below zero, is refused; the price is rounded to the currency's decimals like every other
 *    amount the cashier types.
 */
enum class LineMenuError { PCT_TOO_HIGH, PRICE_NEGATIVE, PRICE_INVALID, REASON_REQUIRED }

data class LineMenuOutcome(
    val ownPct: Double?,
    val unitPriceOverride: Double?,
    val priceReason: String?,
    val error: LineMenuError? = null,
    /** The limit, for [LineMenuError.PCT_TOO_HIGH]. */
    val limitPct: Double = 0.0,
)

fun applyLineMenu(
    pctText: String, priceText: String, reasonText: String,
    listPrice: Double, access: DiscountAccess, dp: Int,
): LineMenuOutcome {
    val asked = clampDiscountPct(parseNum(pctText))
    val max = access.maxPct()
    if (asked > max + 1e-9) {
        return LineMenuOutcome(null, null, null, LineMenuError.PCT_TOO_HIGH, limitPct = max)
    }
    val ownPct = asked.takeIf { it > 0.0 }
    val typedPrice = priceText.trim().takeIf { it.isNotEmpty() }
    val raw = typedPrice?.let { parseNum(it) }
    if (typedPrice != null && (raw == null || raw.isNaN() || raw.isInfinite())) {
        return LineMenuOutcome(ownPct, null, null, LineMenuError.PRICE_INVALID)
    }
    val price = raw?.let { fromMinor(toMinor(it, dp), dp) }
    if (price != null && price < 0.0) {
        return LineMenuOutcome(ownPct, null, null, LineMenuError.PRICE_NEGATIVE)
    }
    val override = price?.takeIf { toMinor(it, dp) != toMinor(listPrice, dp) }
    val why = reasonText.trim().take(DISCOUNT_REASON_MAX)
    if (override != null && why.isEmpty()) {
        return LineMenuOutcome(ownPct, null, null, LineMenuError.REASON_REQUIRED)
    }
    return LineMenuOutcome(ownPct, override, override?.let { why })
}
