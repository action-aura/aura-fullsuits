package com.actionaura.retail.financial

import com.ionspin.kotlin.bignum.decimal.BigDecimal
import com.ionspin.kotlin.bignum.decimal.RoundingMode

/**
 * M3.1 -- the canonical currency representation. Mirrors
 * products/retail/backend/core/retail/pricing.py's `_money()` exactly:
 * every value is quantized to 2 decimal places with round-half-away-from-
 * zero (Python's ROUND_HALF_UP is the same rule, different name -- see
 * docs/retail/unified_mobile/money-decimal-decision.md) at construction
 * time, never left un-rounded at rest.
 *
 * BigDecimal (com.ionspin.kotlin:bignum) is a private implementation
 * detail, referenced only in this file and Quantity.kt/PercentageRate.kt --
 * every other file in this codebase uses these wrapper types, never
 * BigDecimal directly. See money-decimal-decision.md for why.
 */
private val CURRENCY_ROUNDING = RoundingMode.ROUND_HALF_AWAY_FROM_ZERO
private const val CURRENCY_SCALE = 2

class Money private constructor(internal val raw: BigDecimal) : Comparable<Money> {

    companion object {
        val ZERO: Money = Money(BigDecimal.fromInt(0))

        /**
         * Parses a canonical decimal string into Money, rounding to 2dp.
         * Rejects NaN/Infinity/malformed input explicitly (closes the real
         * Python gap: products/retail/backend/api/retail_api.py's
         * `float(item.get('quantity'))` silently accepts "nan"/"inf" --
         * see financial-invariant-catalog.md invariant #7a. Money itself is
         * never parsed from client input in the real Python authority --
         * this parse path exists for internal construction and for the
         * differential-test harness's fixture loading, not for a "trust
         * client-submitted total" code path, which the shared core must
         * never have per invariant #6.)
         */
        fun parse(value: String): FinancialResult<Money> {
            val trimmed = value.trim()
            if (trimmed.isEmpty()) return FinancialResult.Failure(FinancialError.InvalidPrice(value))
            val lower = trimmed.lowercase()
            if (lower == "nan" || lower == "infinity" || lower == "-infinity" ||
                lower == "inf" || lower == "-inf"
            ) {
                return FinancialResult.Failure(FinancialError.InvalidPrice(value))
            }
            return try {
                val parsed = BigDecimal.parseString(trimmed)
                FinancialResult.Success(fromBigDecimal(parsed))
            } catch (e: Exception) {
                FinancialResult.Failure(FinancialError.InvalidPrice(value))
            }
        }

        /** For values already known-valid (e.g. a resolved product price from a trusted repository read). */
        fun of(value: Double): Money = fromBigDecimal(BigDecimal.fromDouble(value))

        internal fun fromBigDecimal(value: BigDecimal): Money =
            Money(value.roundToDigitPositionAfterDecimalPoint(CURRENCY_SCALE.toLong(), CURRENCY_ROUNDING))
    }

    operator fun plus(other: Money): Money = fromBigDecimal(raw + other.raw)
    operator fun minus(other: Money): Money = fromBigDecimal(raw - other.raw)

    /** Multiplying Money by a plain rate/percentage fraction (e.g. discount, tax) -- the core operation `calculate_line` performs. */
    internal fun timesFraction(fraction: BigDecimal): Money = fromBigDecimal(raw * fraction)

    fun isNegative(): Boolean = raw.isNegative
    fun isZero(): Boolean = raw.isZero()

    fun coerceAtLeastZero(): Money = if (isNegative()) ZERO else this

    override fun compareTo(other: Money): Int = raw.compareTo(other.raw)
    override fun equals(other: Any?): Boolean = other is Money && raw.compareTo(other.raw) == 0
    override fun hashCode(): Int = raw.toStringExpanded().hashCode()

    /**
     * Deterministic, never-scientific-notation, always-exactly-2dp string.
     * Real bug found by this milestone's own test
     * (fullReturnRestoresStockAndRefundsFromSnapshot expected "66.00", got
     * "66"): bignum's `toStringExpanded()` after
     * `roundToDigitPositionAfterDecimalPoint` strips trailing zeros rather
     * than preserving the fixed 2dp scale -- so the padding below is done
     * explicitly rather than trusted to the library's own formatting.
     */
    override fun toString(): String {
        val expanded = raw.toStringExpanded()
        val dotIndex = expanded.indexOf('.')
        return when {
            dotIndex == -1 -> "$expanded.00"
            expanded.length - dotIndex - 1 == CURRENCY_SCALE -> expanded
            expanded.length - dotIndex - 1 < CURRENCY_SCALE -> expanded + "0".repeat(CURRENCY_SCALE - (expanded.length - dotIndex - 1))
            else -> expanded.substring(0, dotIndex + 1 + CURRENCY_SCALE) // defensive -- should be unreachable given construction always rounds to 2dp
        }
    }
}

fun max(a: Money, b: Money): Money = if (a >= b) a else b
