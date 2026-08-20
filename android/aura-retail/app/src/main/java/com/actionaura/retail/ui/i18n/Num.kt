package com.actionaura.retail.ui.i18n

import java.util.Locale

/**
 * Locale-robust number parsing & formatting for the retail UI.
 *
 * Two problems this guards against, both surfaced once Arabic shipped:
 *  1. On an Arabic-locale device the numeric/decimal keyboard can emit Arabic-Indic
 *     digits (٠١٢…) and an Arabic decimal mark (٫). Kotlin's [String.toDoubleOrNull]
 *     only understands ASCII "0-9." and would silently return null → the price/qty
 *     becomes 0. [parseNum] normalizes those first.
 *  2. Quantities/stock are Doubles (a product can be sold by kg/L), so truncating with
 *     `.toInt()` turns 2.5 kg into "2". [fmtQty] shows the real fractional value.
 *
 * Formatting is pinned to [Locale.US] so money/quantities are deterministic (Western
 * digits, '.' decimal) regardless of the device locale and parse cleanly on round-trip.
 */

private const val AR_INDIC_ZERO = '٠'      // ٠ .. ٩  (U+0660–U+0669)
private const val AR_INDIC_NINE = '٩'
private const val EXT_INDIC_ZERO = '۰'     // ۰ .. ۹  (U+06F0–U+06F9)
private const val EXT_INDIC_NINE = '۹'

/** Parse user-entered text into a Double, tolerating Arabic-Indic digits & separators. */
fun parseNum(s: String?): Double? {
    if (s.isNullOrBlank()) return null
    val sb = StringBuilder(s.length)
    for (ch in s.trim()) {
        when {
            ch in AR_INDIC_ZERO..AR_INDIC_NINE   -> sb.append('0' + (ch - AR_INDIC_ZERO))
            ch in EXT_INDIC_ZERO..EXT_INDIC_NINE -> sb.append('0' + (ch - EXT_INDIC_ZERO))
            ch == '٫' || ch == ',' || ch == '،' -> sb.append('.')  // decimal mark variants
            ch == '٬' || ch.isWhitespace()       -> { /* thousands sep / spaces → drop */ }
            ch == '+'                            -> { /* tolerate a leading '+' */ }
            else                                 -> sb.append(ch)   // keep ASCII digits, '-', '.'
        }
    }
    return sb.toString().toDoubleOrNull()
}

/** Parse user-entered text into an Int (e.g. reorder level), Arabic-digit tolerant. */
fun parseIntFlexible(s: String?): Int? = parseNum(s)?.let { if (it.isFinite()) it.toInt() else null }

/** Currency: "$1234.50" — Western digits / '.' decimal, device-locale independent. */
fun money(v: Double): String = String.format(Locale.US, "$%.2f", v)

/** Bare 2-decimal amount, locale independent. */
fun amount(v: Double): String = String.format(Locale.US, "%.2f", v)

/** Length of a till PIN. Mirrors user_accounts.PIN_LENGTH. */
const val PIN_LENGTH = 4

/**
 * Fold a typed PIN onto ASCII digits, or return null when it is not exactly
 * [PIN_LENGTH] decimal digits.
 *
 * A deliberate, exact mirror of `user_accounts._normalize_pin()` on the
 * server, and it has to stay one: the server hashes the FOLDED form, so a
 * client that folded differently (or not at all) would send a PIN that hashes
 * to something the same person can never reproduce from another keypad. That
 * is the whole failure this function exists to prevent -- an Arabic soft
 * keyboard emits U+0660..U+0669, so "١٢٣٤" and "1234" are the same PIN to a
 * human and two different secrets to a hash.
 *
 * [parseNum] above cannot be reused for this: it parses to a Double, and a
 * Double has no leading zeros, so the perfectly valid PIN "0042" would come
 * back as 42. A PIN is a fixed-width digit STRING, not a number.
 *
 * Category check first, exactly like the server: `Character.DECIMAL_DIGIT_NUMBER`
 * is Unicode category Nd, so superscripts and other numeric-ish forms are
 * refused rather than quietly folded to a digit.
 */
fun normalizePin(raw: String?): String? {
    val text = raw?.trim() ?: return null
    if (text.length != PIN_LENGTH) return null
    val sb = StringBuilder(PIN_LENGTH)
    for (ch in text) {
        if (Character.getType(ch) != Character.DECIMAL_DIGIT_NUMBER.toInt()) return null
        val d = Character.digit(ch, 10)
        if (d < 0) return null
        sb.append('0' + d)
    }
    return sb.toString()
}

/** Quantity for display: whole numbers print clean ("3"), fractions are trimmed
 *  ("2.5", "1.25") with no truncation. Rounds to 3 dp to absorb float noise. */
fun fmtQty(v: Double): String {
    val r = Math.round(v * 1000.0) / 1000.0
    if (r == Math.floor(r) && !r.isInfinite()) return r.toLong().toString()
    return String.format(Locale.US, "%.3f", r).trimEnd('0').trimEnd('.')
}
