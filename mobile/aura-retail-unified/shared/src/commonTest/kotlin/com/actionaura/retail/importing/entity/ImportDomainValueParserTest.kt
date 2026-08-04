package com.actionaura.retail.importing.entity

import com.actionaura.retail.financial.Money
import com.actionaura.retail.importing.ImportFieldParser
import com.actionaura.retail.importing.ImportValidationIssue
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertNull
import kotlin.test.assertTrue

class ImportDomainValueParserTest {

    @Test
    fun blankOrNullRawValueIsRealEmptyNeverAnError() {
        val (value1, issue1) = ImportDomainValueParser.parse(null, ImportFieldParser.MONEY, 2, "sell_price")
        val (value2, issue2) = ImportDomainValueParser.parse("   ", ImportFieldParser.MONEY, 2, "sell_price")
        assertEquals(ImportParsedValue.Empty, value1)
        assertEquals(ImportParsedValue.Empty, value2)
        assertNull(issue1)
        assertNull(issue2)
    }

    @Test
    fun exactMoneyParsesThroughTheRealM3Parser() {
        val (value, issue) = ImportDomainValueParser.parse("19.99", ImportFieldParser.MONEY, 2, "sell_price")
        assertNull(issue)
        assertEquals(Money.of(19.99), (value as ImportParsedValue.MoneyValue).value)
    }

    @Test
    fun malformedMoneyIsARealValidationIssueNeverCoercedToZero() {
        val (value, issue) = ImportDomainValueParser.parse("not-a-price", ImportFieldParser.MONEY, 2, "sell_price")
        assertNull(value)
        assertIs<ImportValidationIssue.UnparseableValue>(issue)
    }

    @Test
    fun exactQuantityParsesThroughTheRealM3Parser() {
        val (value, issue) = ImportDomainValueParser.parse("10", ImportFieldParser.QUANTITY, 2, "initial_stock")
        assertNull(issue)
        assertIs<ImportParsedValue.QuantityValue>(value)
    }

    @Test
    fun negativeQuantityIsARealValidationIssue() {
        val (value, issue) = ImportDomainValueParser.parse("-5", ImportFieldParser.QUANTITY, 2, "initial_stock")
        assertNull(value)
        assertIs<ImportValidationIssue.UnparseableValue>(issue)
    }

    @Test
    fun zeroStockIsARealValidBalanceNeverRejectedUnlikeStrictQuantity() {
        // Real bug found by ImportPerformanceAtScaleTest's own 2,000-row
        // real commit: `QUANTITY` (Quantity.parse) is strict-positive,
        // built for sale/return LINE quantities -- using it for
        // `initial_stock`/`loyalty_points` (real, zero-or-more BALANCES)
        // silently skipped every genuine "0" row. Fixed by routing those
        // two fields through `QUANTITY_ZERO_OR_MORE` (Quantity.zeroOrMore)
        // instead -- proven here directly.
        val (value, issue) = ImportDomainValueParser.parse("0", ImportFieldParser.QUANTITY_ZERO_OR_MORE, 2, "initial_stock")
        assertNull(issue)
        assertIs<ImportParsedValue.QuantityValue>(value)
    }

    @Test
    fun negativeZeroOrMoreQuantityIsStillARealValidationIssue() {
        val (value, issue) = ImportDomainValueParser.parse("-5", ImportFieldParser.QUANTITY_ZERO_OR_MORE, 2, "initial_stock")
        assertNull(value)
        assertIs<ImportValidationIssue.UnparseableValue>(issue)
    }

    @Test
    fun nonFiniteZeroOrMoreQuantityIsRejectedNeverSilentlyAccepted() {
        val (value, issue) = ImportDomainValueParser.parse("NaN", ImportFieldParser.QUANTITY_ZERO_OR_MORE, 2, "initial_stock")
        assertNull(value)
        assertIs<ImportValidationIssue.UnparseableValue>(issue)
    }

    @Test
    fun leadingZeroBarcodeTextIsPreservedExactly() {
        val (value, issue) = ImportDomainValueParser.parse("00123456", ImportFieldParser.TEXT, 2, "barcode")
        assertNull(issue)
        assertEquals("00123456", (value as ImportParsedValue.TextValue).value)
    }

    @Test
    fun validEmailParsesAndIsLowerCased() {
        val (value, issue) = ImportDomainValueParser.parse("John@Example.COM", ImportFieldParser.EMAIL, 2, "email")
        assertNull(issue)
        assertEquals("john@example.com", (value as ImportParsedValue.EmailValue).value)
    }

    @Test
    fun malformedEmailIsARealValidationIssue() {
        val (value, issue) = ImportDomainValueParser.parse("not-an-email", ImportFieldParser.EMAIL, 2, "email")
        assertNull(value)
        assertIs<ImportValidationIssue.UnparseableValue>(issue)
    }

    @Test
    fun validPercentageRateParses() {
        val (value, issue) = ImportDomainValueParser.parse("15", ImportFieldParser.PERCENTAGE_RATE, 2, "tax_rate")
        assertNull(issue)
        assertIs<ImportParsedValue.PercentageRateValue>(value)
    }

    @Test
    fun malformedPercentageRateIsARealValidationIssueNeverThrown() {
        val (value, issue) = ImportDomainValueParser.parse("not-a-rate", ImportFieldParser.PERCENTAGE_RATE, 2, "tax_rate")
        assertNull(value)
        assertIs<ImportValidationIssue.UnparseableValue>(issue)
    }

    @Test
    fun validIntegerParses() {
        val (value, issue) = ImportDomainValueParser.parse("10", ImportFieldParser.INTEGER, 2, "reorder_level")
        assertNull(issue)
        assertEquals(10L, (value as ImportParsedValue.IntegerValue).value)
    }

    @Test
    fun malformedIntegerIsARealValidationIssue() {
        val (value, issue) = ImportDomainValueParser.parse("ten", ImportFieldParser.INTEGER, 2, "reorder_level")
        assertNull(value)
        assertIs<ImportValidationIssue.UnparseableValue>(issue)
    }

    @Test
    fun statusValueIsLowerCasedButOtherwisePreserved() {
        val (value, issue) = ImportDomainValueParser.parse("ACTIVE", ImportFieldParser.STATUS, 2, "status")
        assertNull(issue)
        assertEquals("active", (value as ImportParsedValue.StatusValue).value)
    }

    @Test
    fun ambiguousLocaleNumberFormatIsHandledByTheRealM3ParserNotGuessedHere() {
        // "1,234" -- M5.8.10's own required ambiguous-format case. This
        // decoder-layer test only proves the value reaches the real M3
        // parser unmodified; the exact accept/reject decision belongs to
        // Money.parse's own documented contract (financial-invariant-catalog.md),
        // not reimplemented here.
        val (value, issue) = ImportDomainValueParser.parse("1,234.56", ImportFieldParser.MONEY, 2, "sell_price")
        // Whichever way Money.parse resolves it, this call must not throw and must return exactly one of value/issue, never both or neither.
        assertTrue((value != null) xor (issue != null))
    }
}
