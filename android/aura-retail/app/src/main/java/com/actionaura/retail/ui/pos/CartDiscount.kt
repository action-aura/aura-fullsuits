package com.actionaura.retail.ui.pos

import java.math.BigDecimal
import java.math.RoundingMode

/**
 * The sale-level discount the desktop's UI-3a added, as the phone carries it:
 * an AMOUNT off ("JD 0.650 off") or a TARGET total ("make it 10.000").
 *
 * INTENT ONLY, like everything the phone sends. The phone sends the amount or
 * the target and a reason; the SERVER spreads it over the lines in minor units,
 * proves it adds up exactly, applies tax and decides whether it is within the
 * cashier's limit (core/retail/cart_discount.py). Nothing here distributes a
 * discount. The functions below produce DISPLAY figures -- an estimate, labelled
 * as one on screen -- and the round-down CHIP values; the sale's real total is
 * whatever the server answers.
 */
enum class CartDiscountKind { AMOUNT, TARGET }

data class CartDiscount(
    val kind: CartDiscountKind,
    /** Minor units: the discount for [CartDiscountKind.AMOUNT], the wanted total for [CartDiscountKind.TARGET]. */
    val minor: Long,
    /** The reason text exactly as it will be sent (already in the cashier's language). */
    val reason: String,
    /** [cartSignature] of the cart it was worked out for. */
    val sig: String,
)

/** The desktop's reasons (`_DISC_REASONS`), in its order. Keys are English; the screen translates them. */
val DISCOUNT_REASONS: List<String> = listOf("Damaged", "Loyal customer", "Round down", "Other")
const val REASON_OTHER = "Other"
const val REASON_ROUND_DOWN = "Round down"

/** The server keeps at most this many characters of a reason (`DISCOUNT_REASON_MAX_LEN`). */
const val DISCOUNT_REASON_MAX = 120

/** The "Round total down to" steps the desktop offers (`_dsRoundDownChips`). */
val ROUND_DOWN_STEPS: List<Double> = listOf(0.25, 0.5, 1.0)

/**
 * The text of a reason as it travels: a preset sends its (translated) label, "Other" sends what
 * was typed. Trimmed and capped at what the server stores. Blank = no reason picked.
 */
fun discountReasonText(preset: String?, other: String, translate: (String) -> String): String {
    if (preset.isNullOrBlank()) return ""
    val raw = if (preset == REASON_OTHER) other else translate(preset)
    return raw.trim().take(DISCOUNT_REASON_MAX)
}

/**
 * The sale's `discount_reason`: the sheet's reason when a cart discount is on, otherwise the
 * distinct reasons of the lines that carry a price of their own, joined "; " and capped (the
 * desktop's rule: one sale-level field, so each overridden line's reason rides along).
 */
fun saleDiscountReason(discount: CartDiscount?, lines: List<CartLine>): String? {
    discount?.reason?.takeIf { it.isNotBlank() }?.let { return it }
    val whys = lines.filter { it.unitPriceOverride != null }
        .mapNotNull { it.priceReason?.trim()?.takeIf { r -> r.isNotEmpty() } }.distinct()
    return whys.takeIf { it.isNotEmpty() }?.joinToString("; ")?.take(DISCOUNT_REASON_MAX)
}

/**
 * Totals a chip may round down to: the nearest 0.250 / 0.500 / 1.000 STRICTLY below [baseTotalMinor],
 * distinct, each above zero. Not checked for reachability -- that needs the server's pricing; an
 * unreachable one is refused by the server with the nearest totals named, and the screen shows that.
 */
fun roundDownTargets(baseTotalMinor: Long, dp: Int): List<Long> {
    val out = ArrayList<Long>()
    for (step in ROUND_DOWN_STEPS) {
        val stepM = toMinor(step, dp)
        if (stepM < 1L) continue
        var target = Math.floorDiv(baseTotalMinor, stepM) * stepM
        if (target >= baseTotalMinor) target -= stepM
        if (target <= 0L || target in out) continue
        out += target
    }
    return out
}

/**
 * DISPLAY estimate of the total after an AMOUNT discount: the amount comes off the pre-tax
 * subtotal in proportion to each line's gross (the server's rule, minus its minor-unit residue
 * handling), then tax on what is left. Within a fil or two of the server; labelled an estimate.
 */
fun estimateAfterAmountMinor(lines: List<EstimateLine>, discountMinor: Long, dp: Int): Long {
    val hundred = BigDecimal(100)
    val gross = lines.map { (BigDecimal(it.unitPrice.toString()) * BigDecimal(it.quantity.toString())).setScale(dp, RoundingMode.HALF_UP) }
    val g = gross.fold(BigDecimal.ZERO) { a, b -> a + b }
    if (g.signum() <= 0) return 0L
    val d = BigDecimal.valueOf(discountMinor.coerceAtLeast(0L), dp).min(g)
    var total = BigDecimal.ZERO
    lines.forEachIndexed { i, l ->
        val share = d * gross[i] / g
        val taxable = gross[i] - share
        val tax = taxable * BigDecimal(l.taxRatePct.toString()) / hundred
        total += taxable + tax
    }
    return total.movePointRight(dp).setScale(0, RoundingMode.HALF_UP).toLong()
}

/** Pre-tax, pre-discount subtotal in minor units (what an amount discount is measured against). */
fun grossSubtotalMinor(lines: List<EstimateLine>, dp: Int): Long =
    lines.fold(BigDecimal.ZERO) { a, l ->
        a + (BigDecimal(l.unitPrice.toString()) * BigDecimal(l.quantity.toString())).setScale(dp, RoundingMode.HALF_UP)
    }.movePointRight(dp).setScale(0, RoundingMode.HALF_UP).toLong()

/** The estimated total the sale is expected to come to, whichever way it is discounted. */
fun estimateWithDiscountMinor(lines: List<EstimateLine>, discount: CartDiscount?, dp: Int): Long = when (discount?.kind) {
    null -> estimateDueMinor(lines, dp)
    CartDiscountKind.AMOUNT -> estimateAfterAmountMinor(lines, discount.minor, dp)
    CartDiscountKind.TARGET -> discount.minor
}

/**
 * The discount as a percentage of the pre-discount cart, for the "is this over my limit" question
 * (the server measures an amount against the pre-discount subtotal). For a target total it is the
 * share of the estimated base total taken off, which equals the subtotal share when tax is uniform.
 */
fun effectiveDiscountPct(discount: CartDiscount, grossSubtotalMinor: Long, baseTotalMinor: Long): Double = when (discount.kind) {
    CartDiscountKind.AMOUNT ->
        if (grossSubtotalMinor <= 0L) 0.0 else discount.minor * 100.0 / grossSubtotalMinor
    CartDiscountKind.TARGET ->
        if (baseTotalMinor <= 0L) 0.0 else (baseTotalMinor - discount.minor).coerceAtLeast(0L) * 100.0 / baseTotalMinor
}

/**
 * Will the server ask for a manager's PIN for this sale? A session that holds `retail.discount`
 * (the discount capability, which also covers price override today) acts alone. Anyone else needs
 * a manager for ANY price of their own, and for a cart discount that is beyond the shop's cashier
 * limit (no limit set = every cart discount). A PREDICTION from the estimate: the server decides,
 * and a refusal that says "ask a manager" opens the same sheet (see [ApiFailure]).
 */
fun managerApprovalNeeded(
    holdsDiscountCapability: Boolean, cashierLimitPct: Double,
    discount: CartDiscount?, effectivePct: Double, hasPriceOverride: Boolean,
): Boolean {
    if (holdsDiscountCapability) return false
    if (hasPriceOverride) return true
    if (discount == null) return false
    return cashierLimitPct <= 0.0 || effectivePct > cashierLimitPct + 1e-9
}

/** What is wrong with a typed discount, judged on the estimate (the server's check is the real one). */
enum class DraftCheck { OK, EMPTY, TOO_BIG, TARGET_HIGH }

fun checkDraft(kind: CartDiscountKind, valueMinor: Long, grossSubtotalMinor: Long, baseTotalMinor: Long): DraftCheck = when {
    valueMinor <= 0L -> DraftCheck.EMPTY
    kind == CartDiscountKind.AMOUNT && valueMinor > grossSubtotalMinor -> DraftCheck.TOO_BIG
    kind == CartDiscountKind.TARGET && valueMinor > baseTotalMinor -> DraftCheck.TARGET_HIGH
    kind == CartDiscountKind.TARGET && valueMinor == baseTotalMinor -> DraftCheck.EMPTY
    else -> DraftCheck.OK
}
