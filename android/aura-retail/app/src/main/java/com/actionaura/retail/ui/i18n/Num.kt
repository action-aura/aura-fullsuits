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

/**
 * The shop's currency, as resolved by the SERVER.
 *
 * Set once from `GET /settings/tax`, which returns the mark and the decimal
 * count already resolved by `core/retail/pricing.py` -- the single source of
 * truth for both. The phone deliberately holds no table of its own: the number
 * of decimal places is not cosmetic (the Jordanian dinar has THREE, 1000 fils,
 * and persisted money is rounded to exactly that), so a second copy here could
 * drift and the symptom would be the till DISPLAYING a different number from
 * the one it charges.
 *
 * Defaults are the Jordanian ones, matching the server's own default, so a
 * screen that renders money before the first settings response still shows
 * something correct for the home market rather than dollars.
 */
object Currency {
    @Volatile var symbol: String = "JD"
    @Volatile var decimals: Int = 3

    /** Apply a settings response. Ignores nulls so a partial or older payload
     *  leaves the working defaults standing rather than blanking the mark. */
    fun apply(symbol: String?, decimals: Int?) {
        symbol?.let { this.symbol = it }
        decimals?.let { if (it in 0..4) this.decimals = it }
    }
}

/**
 * Currency for display: "JD 1234.500", "$1234.50".
 *
 * Western digits and '.' decimal, device-locale independent -- unchanged and
 * deliberate, so a phone set to an Arabic locale does not render Arabic-Indic
 * digits into a figure the shopkeeper is comparing against a printed receipt.
 *
 * A single-character mark hugs its digits, a multi-letter one takes a space,
 * matching the web till's rule exactly (subsystem-retail.js::_currencyPrefix).
 * The rule is on LENGTH rather than a per-currency list so it is right for
 * currencies nobody has added yet.
 */
fun money(v: Double): String {
    val body = String.format(Locale.US, "%.${Currency.decimals}f", v)
    val sym = Currency.symbol
    return when {
        sym.isEmpty() -> body
        sym.length == 1 -> sym + body
        else -> "$sym $body"
    }
}

/** Bare amount with no mark, at the shop's currency precision. */
fun amount(v: Double): String = String.format(Locale.US, "%.${Currency.decimals}f", v)

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

private const val LRI = '⁦'   // LEFT-TO-RIGHT ISOLATE
private const val PDI = '⁩'   // POP DIRECTIONAL ISOLATE

/**
 * Wrap a strictly left-to-right token so Arabic cannot reorder its insides.
 *
 * A version string is the worked example and the reason this exists. In an
 * RTL paragraph, Unicode's bidirectional algorithm treats `1.0.0-rc.5` as
 * neutral-separated runs and lays them out right to left, so the app's own
 * footer rendered **`rc.5-1.0.0`** in Arabic. Seen on a real Mi Note 10 on
 * 2026-09-09 -- not caught by any test, because every test asserts the string
 * the code passes in, and the code passes in the correct one. The damage
 * happens in the text engine, which only a screenshot can see.
 *
 * The desktop hit the identical bug in the same week: the brand lockup read
 * "AuraRetail" in Arabic until `direction: ltr; unicode-bidi: isolate` was put
 * on it. This is that fix, spelled the way a string can carry itself.
 *
 * Isolate rather than the older LRM/RLM embedding marks: an isolate also stops
 * the token from disturbing the direction of the text AROUND it, which
 * embedding does not, so this stays correct if the version is ever dropped
 * into the middle of a sentence rather than sitting alone in a footer.
 *
 * Apply to identifiers, versions, codes, URLs, phone numbers and file paths.
 * NOT to money or quantities: those are formatted by [fmtQty] and friends and
 * are meant to follow the paragraph, which is what an Arabic reader expects.
 */
fun ltrIsolate(s: String?): String {
    val text = s ?: return ""
    if (text.isEmpty()) return ""
    // Idempotent: wrapping twice would nest isolates and is a no-op visually,
    // but it makes the string compare unequal to itself in tests and logs.
    if (text.first() == LRI && text.last() == PDI) return text
    return "$LRI$text$PDI"
}
