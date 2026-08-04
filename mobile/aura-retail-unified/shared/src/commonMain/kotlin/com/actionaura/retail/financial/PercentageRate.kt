package com.actionaura.retail.financial

import com.ionspin.kotlin.bignum.decimal.BigDecimal

/**
 * A plain percentage value (0-100 conceptually, but see `clampToDiscountRange`
 * vs `trusted` below -- only discount_pct is clamped in the real Python
 * authority; tax_rate is never clamped because it is always a
 * server/core-trusted resolved value, never client input -- see
 * financial-invariant-catalog.md invariants #3/#6).
 */
// Real finding, this milestone's own build: BigDecimal.div(BigDecimal, DecimalMode)
// does not exist in bignum 0.3.10 (the compiler's own candidate list confirmed
// only div(Int/Long/Short/Byte) and div(BigDecimal) with no rounding-mode
// parameter). Multiplying by 0.01 instead of dividing by 100 is mathematically
// exact (100 is a power of ten) and needs no rounding-mode decision at all --
// avoids the whole division-precision question rather than working around it.
private val ONE_HUNDREDTH: BigDecimal = BigDecimal.parseString("0.01")
private val ZERO: BigDecimal = BigDecimal.fromInt(0)
private val ONE_HUNDRED: BigDecimal = BigDecimal.fromInt(100)

class PercentageRate private constructor(internal val raw: BigDecimal) {

    companion object {
        /**
         * Mirrors `pricing.clamp_discount_pct()` exactly: negative -> 0,
         * >100 -> 100, malformed -> 0. Never raises -- LEGACY_PARITY.
         */
        fun clampToDiscountRange(value: String): PercentageRate {
            val parsed = try {
                BigDecimal.parseString(value.trim())
            } catch (e: Exception) {
                return PercentageRate(ZERO)
            }
            val clamped = when {
                parsed.compareTo(ZERO) < 0 -> ZERO
                parsed.compareTo(ONE_HUNDRED) > 0 -> ONE_HUNDRED
                else -> parsed
            }
            return PercentageRate(clamped)
        }

        fun clampToDiscountRange(value: Double): PercentageRate =
            clampToDiscountRange(value.toString())

        /** For a resolved, trusted rate (e.g. product.tax_rate read from the repository) -- no clamping, matches Python's tax_rate handling. */
        fun trusted(value: Double): PercentageRate = PercentageRate(BigDecimal.fromDouble(value))

        val ZERO_RATE: PercentageRate = PercentageRate(ZERO)
    }

    /** `this / 100` as an exact fraction (via *0.01, mathematically identical, no rounding-mode needed), for multiplying against a Money/gross amount. */
    internal fun asFraction(): BigDecimal = raw * ONE_HUNDREDTH

    fun isZero(): Boolean = raw.isZero()

    override fun toString(): String = raw.toStringExpanded()
}
