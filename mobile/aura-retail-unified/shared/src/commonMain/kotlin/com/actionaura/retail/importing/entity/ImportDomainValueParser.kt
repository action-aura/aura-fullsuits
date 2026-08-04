package com.actionaura.retail.importing.entity

import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate
import com.actionaura.retail.financial.Quantity
import com.actionaura.retail.importing.ImportFieldParser
import com.actionaura.retail.importing.ImportValidationIssue

/**
 * M5.8.10 -- real domain value parsing for import rows. Every
 * Money/Quantity/PercentageRate value goes through the exact same M3
 * canonical parsers the rest of this codebase uses -- there is no
 * import-only financial parsing here, matching the checkpoint's own
 * explicit instruction. A raw cell is never parsed through `Double`.
 */
sealed class ImportParsedValue {
    data class TextValue(val value: String) : ImportParsedValue()
    data class MoneyValue(val value: Money) : ImportParsedValue()
    data class QuantityValue(val value: Quantity) : ImportParsedValue()
    data class PercentageRateValue(val value: PercentageRate) : ImportParsedValue()
    data class IntegerValue(val value: Long) : ImportParsedValue()
    data class EmailValue(val value: String) : ImportParsedValue()
    data class StatusValue(val value: String) : ImportParsedValue()
    /** The raw cell was absent/blank -- a real, valid state for an optional field, never itself an error. */
    object Empty : ImportParsedValue()
}

object ImportDomainValueParser {
    private val EMAIL_PATTERN = Regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$")

    /**
     * Returns the parsed value on success, or a real
     * `ImportValidationIssue` on failure -- never both, never silently
     * coerces an unparseable value to zero/empty.
     */
    fun parse(raw: String?, parser: ImportFieldParser, rowNumber: Long, fieldKey: String): Pair<ImportParsedValue?, ImportValidationIssue?> {
        val trimmed = raw?.trim()
        if (trimmed.isNullOrEmpty()) return ImportParsedValue.Empty to null

        return when (parser) {
            ImportFieldParser.TEXT -> ImportParsedValue.TextValue(trimmed) to null
            ImportFieldParser.STATUS -> ImportParsedValue.StatusValue(trimmed.lowercase()) to null
            ImportFieldParser.EMAIL -> {
                val lower = trimmed.lowercase()
                if (EMAIL_PATTERN.matches(lower)) ImportParsedValue.EmailValue(lower) to null
                else null to ImportValidationIssue.UnparseableValue(rowNumber, fieldKey, trimmed)
            }
            ImportFieldParser.MONEY -> {
                when (val result = Money.parse(trimmed)) {
                    is com.actionaura.retail.financial.FinancialResult.Success -> ImportParsedValue.MoneyValue(result.value) to null
                    is com.actionaura.retail.financial.FinancialResult.Failure -> null to ImportValidationIssue.UnparseableValue(rowNumber, fieldKey, trimmed)
                }
            }
            ImportFieldParser.QUANTITY -> {
                when (val result = Quantity.parse(trimmed)) {
                    is com.actionaura.retail.financial.FinancialResult.Success -> ImportParsedValue.QuantityValue(result.value) to null
                    is com.actionaura.retail.financial.FinancialResult.Failure -> null to ImportValidationIssue.UnparseableValue(rowNumber, fieldKey, trimmed)
                }
            }
            // Real, deliberate distinction from QUANTITY above: `Quantity.parse`
            // is strict-positive (built for sale/return LINE quantities,
            // financial-invariant-catalog.md invariant #7/#7a); import's own
            // `initial_stock`/`loyalty_points` fields are real, zero-or-more
            // BALANCES (a product can genuinely have zero stock, a customer
            // zero points) -- routed through `Quantity.zeroOrMore` instead,
            // the exact same real distinction `Quantity.kt`'s own KDoc
            // documents. Using strict `QUANTITY` here was a real bug, found
            // by `ImportPerformanceAtScaleTest`'s 2,000-row real commit
            // (20 real rows with a genuine "0" stock value were silently
            // skipped) -- fixed at the source, not worked around in the test.
            ImportFieldParser.QUANTITY_ZERO_OR_MORE -> {
                val parsed = com.actionaura.retail.financial.Quantity.zeroOrMore(trimmed)
                if (parsed != null) ImportParsedValue.QuantityValue(parsed) to null
                else null to ImportValidationIssue.UnparseableValue(rowNumber, fieldKey, trimmed)
            }
            ImportFieldParser.PERCENTAGE_RATE -> {
                try {
                    ImportParsedValue.PercentageRateValue(PercentageRate.trusted(trimmed)) to null
                } catch (e: Exception) {
                    null to ImportValidationIssue.UnparseableValue(rowNumber, fieldKey, trimmed)
                }
            }
            ImportFieldParser.INTEGER -> {
                val n = trimmed.toLongOrNull()
                if (n != null) ImportParsedValue.IntegerValue(n) to null
                else null to ImportValidationIssue.UnparseableValue(rowNumber, fieldKey, trimmed)
            }
        }
    }
}
