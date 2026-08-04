package com.actionaura.retail.financial

/**
 * Mirrors core/retail/pricing.py's TAX_AFTER_DISCOUNT/TAX_BEFORE_DISCOUNT
 * exactly (financial-invariant-catalog.md invariant #4). An unrecognized
 * value falling back to AFTER_DISCOUNT is handled by callers using
 * `TaxMode.parseOrDefault`, not by allowing an invalid enum state to exist
 * -- Kotlin's type system makes the Python "any value that isn't one of
 * the two known strings" case structurally impossible past the parse
 * boundary, which is a stricter (never weaker) guarantee than Python's
 * runtime fallback.
 */
enum class TaxMode(val pythonValue: String) {
    AFTER_DISCOUNT("after_discount"),
    BEFORE_DISCOUNT("before_discount");

    companion object {
        val DEFAULT = AFTER_DISCOUNT

        fun parseOrDefault(value: String?): TaxMode =
            entries.firstOrNull { it.pythonValue == value } ?: DEFAULT
    }
}
