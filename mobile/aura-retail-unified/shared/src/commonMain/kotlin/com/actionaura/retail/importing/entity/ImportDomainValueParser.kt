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
