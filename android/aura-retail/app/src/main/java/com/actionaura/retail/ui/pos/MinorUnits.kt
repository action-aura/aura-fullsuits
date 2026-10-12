package com.actionaura.retail.ui.pos

import com.actionaura.retail.ui.i18n.parseNum
import java.math.BigDecimal
import java.math.RoundingMode

/**
 * Money held as a whole number of the currency's smallest unit (a fil for the
 * Jordanian dinar: 1 JD = 1000), so that "the parts cover the total" and
 * "Remaining" are exact integer questions instead of floating-point ones.
 *
 * WHY THIS EXISTS. `0.1 + 0.2 != 0.3` in a Double, and a split-payment screen
 * that asks "is 5.000 + 6.832 >= 11.832?" in Doubles will, on some pair of
 * amounts, say no and leave the Charge button dead on a payment that is
 * exactly right -- or say yes on one that is a fil short and let the server's
 * `TENDERS_SHORT` refuse it in front of the customer. The desktop avoids this
 * the same way (`_toMinor` / `_fromMinor` in subsystem-retail.js); this is the
 * phone's twin of that pair.
 *
 * The decimal places are passed in (not read from the global `Currency`) so a
 * unit test can pin the 3-dp dinar and the 2-dp dollar without mutating
 * process-wide state. Callers on a screen pass `Currency.decimals`.
 *
 * DISPLAY AND SHAPE ONLY. Nothing here decides what a sale costs -- the
 * server is the only pricing authority (docs/architecture/
 * financial-authority-contracts.md). These helpers only keep the amounts the
 * cashier TYPES exact, and turn them back into the numbers the server reads.
 */
fun toMinor(amount: Double, dp: Int): Long {
    require(dp in 0..4) { "unsupported decimal places: $dp" }
    if (amount.isNaN() || amount.isInfinite()) return 0L
    // Double.toString is the shortest string that round-trips, so 0.1 stays
    // "0.1" rather than 0.1000000000000000055...; HALF_UP then matches the
    // server's ROUND_HALF_UP (core/retail/pricing.py).
    return BigDecimal(amount.toString()).setScale(dp, RoundingMode.HALF_UP).movePointRight(dp).toLong()
}

fun fromMinor(minor: Long, dp: Int): Double = BigDecimal.valueOf(minor, dp).toDouble()

/**
 * What a cashier typed in an amount box, in minor units -- or null when it is
 * not a usable positive amount. Accepts Arabic-Indic digits and the Arabic
 * decimal mark (parseNum), because the numeric keyboard on an Arabic-locale
 * phone can emit them. A value with more decimals than the currency has is
 * refused rather than silently rounded: "5.0005" on a dinar is a typo the
 * cashier should see, not a half-fil the till swallows.
 */
fun parseAmountMinor(text: String?, dp: Int): Long? {
    val v = parseNum(text) ?: return null
    if (v.isNaN() || v.isInfinite() || v <= 0.0) return null
    val exact = BigDecimal(v.toString())
    if (exact.stripTrailingZeros().scale() > dp) return null
    return toMinor(v, dp).takeIf { it > 0L }
}

/** Fixed-dp text for an amount box ("5.000"), always '.' and Western digits. */
fun minorToInput(minor: Long, dp: Int): String =
    BigDecimal.valueOf(minor, dp).setScale(dp, RoundingMode.UNNECESSARY).toPlainString()
