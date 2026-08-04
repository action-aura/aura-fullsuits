package com.actionaura.retail.financial

import com.ionspin.kotlin.bignum.decimal.BigDecimal

/**
 * M3.1 -- mirrors the real Python authority's `quantity` handling
 * (products/retail/backend/api/retail_api.py: `qty = float(item.get('quantity'))`
 * then `qty <= 0` rejected) with one deliberate, documented correction:
 * Python's `float()` accepts "nan"/"inf" without raising, and `nan <= 0`
 * is False in IEEE-754, so a NaN quantity silently passes both checks in
 * the real backend today (financial-invariant-catalog.md invariant #7a).
 * `Quantity.parse` explicitly rejects NaN/Infinity -- CANONICAL_UNIFIED,
 * not a port of the defect.
 *
 * Supports fractional quantities (e.g. 1.5 kg for a weight-based product),
 * matching Python's float(quantity) accepting decimals -- this is NOT an
 * integer-only type.
 */
class Quantity private constructor(internal val raw: BigDecimal) : Comparable<Quantity> {

    companion object {
        fun parse(value: String): FinancialResult<Quantity> {
            val trimmed = value.trim()
            if (trimmed.isEmpty()) return FinancialResult.Failure(FinancialError.InvalidQuantity(value))
            val lower = trimmed.lowercase()
            if (lower == "nan" || lower == "infinity" || lower == "-infinity" ||
                lower == "inf" || lower == "-inf"
            ) {
                return FinancialResult.Failure(FinancialError.InvalidQuantity(value))
            }
            val parsed = try {
                BigDecimal.parseString(trimmed)
            } catch (e: Exception) {
                return FinancialResult.Failure(FinancialError.InvalidQuantity(value))
            }
            if (parsed.compareTo(BigDecimal.fromInt(0)) <= 0) {
                return FinancialResult.Failure(FinancialError.NonPositiveQuantity(value))
            }
            return FinancialResult.Success(Quantity(parsed))
        }

        /** For values already known-valid (e.g. summing already-validated quantities). */
        internal fun fromBigDecimal(value: BigDecimal): Quantity = Quantity(value)
    }

    operator fun times(price: Money): Money = Money.fromBigDecimal(raw * price.raw)
    operator fun minus(other: Quantity): Quantity = Quantity(raw - other.raw)
    operator fun plus(other: Quantity): Quantity = Quantity(raw + other.raw)

    fun isPositive(): Boolean = raw.compareTo(BigDecimal.fromInt(0)) > 0
    fun isZeroOrNegative(): Boolean = raw.compareTo(BigDecimal.fromInt(0)) <= 0

    override fun compareTo(other: Quantity): Int = raw.compareTo(other.raw)
    override fun equals(other: Any?): Boolean = other is Quantity && raw.compareTo(other.raw) == 0
    override fun hashCode(): Int = raw.toStringExpanded().hashCode()
    override fun toString(): String = raw.toStringExpanded()
}
