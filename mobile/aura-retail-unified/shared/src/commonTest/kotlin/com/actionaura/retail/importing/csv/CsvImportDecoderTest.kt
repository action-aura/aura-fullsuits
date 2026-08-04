package com.actionaura.retail.importing.csv

import com.actionaura.retail.importing.ImportFileDescriptor
import com.actionaura.retail.importing.ImportFormat
import com.actionaura.retail.importing.ImportLimits
import com.actionaura.retail.importing.ImportResult
import com.actionaura.retail.importing.ImportSource
import com.actionaura.retail.importing.ImportSourceId
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertNull

private class FakeImportSource(private val bytes: ByteArray, name: String = "test.csv") : ImportSource {
    override val descriptor = ImportFileDescriptor(ImportSourceId("fake"), name, bytes.size.toLong(), "text/csv", "csv")
    override suspend fun readBounded(limits: ImportLimits): ImportResult<ByteArray> {
        if (bytes.size.toLong() > limits.maxCompressedFileSizeBytes) {
            return ImportResult.Failure(
                com.actionaura.retail.importing.ImportError.LimitExceeded(
                    com.actionaura.retail.importing.ImportLimitExceeded("maxCompressedFileSizeBytes", limits.maxCompressedFileSizeBytes, bytes.size.toLong()),
                ),
            )
        }
        return ImportResult.Success(bytes)
    }
}

class CsvImportDecoderTest {
    private val decoder = CsvImportDecoder()

    private suspend fun decode(text: String, limits: ImportLimits = ImportLimits.DEFAULT) =
        decoder.decode(FakeImportSource(text.encodeToByteArray()), limits)

    @Test
    fun basicCsvDecodesRealHeadersAndRows() = runTest {
        val result = decode("name,sku,price\nCola,SKU-1,1.99\nJuice,SKU-2,2.50\n")
        val table = (result as ImportResult.Success).value
        assertEquals(listOf("name", "sku", "price"), table.columns.map { it.rawHeader })
        assertEquals(2, table.rows.size)
        assertEquals(listOf("Cola", "SKU-1", "1.99"), table.rows[0].cells)
        assertEquals(2L, table.rows[0].rowNumber)
    }

    @Test
    fun utf8BomIsStrippedNotTreatedAsPartOfTheFirstHeader() = runTest {
        val bom = byteArrayOf(0xEF.toByte(), 0xBB.toByte(), 0xBF.toByte())
        val body = "name,sku\nCola,SKU-1\n".encodeToByteArray()
        val result = decoder.decode(FakeImportSource(bom + body), ImportLimits.DEFAULT)
        val table = (result as ImportResult.Success).value
        assertEquals("name", table.columns[0].rawHeader, "the BOM must not become part of the first header's text")
    }

    @Test
    fun arabicTextIsPreservedExactly() = runTest {
        val result = decode("name,category\nكولا,مشروبات\n")
        val table = (result as ImportResult.Success).value
        assertEquals("كولا", table.rows[0].cells[0])
        assertEquals("مشروبات", table.rows[0].cells[1])
    }

    @Test
    fun quotedFieldsMayContainTheDelimiter() = runTest {
        val result = decode("name,description\nCola,\"Contains, a comma\"\n")
        val table = (result as ImportResult.Success).value
        assertEquals("Contains, a comma", table.rows[0].cells[1])
    }

    @Test
    fun quotedFieldsMayContainEmbeddedLineBreaks() = runTest {
        val result = decode("name,description\nCola,\"Line one\nLine two\"\n")
        val table = (result as ImportResult.Success).value
        assertEquals("Line one\nLine two", table.rows[0].cells[1])
        assertEquals(1, table.rows.size, "the embedded newline inside quotes must not be treated as a new row")
    }

    @Test
    fun escapedDoubleQuotesInsideAQuotedFieldBecomeOneLiteralQuote() = runTest {
        val result = decode("name,description\nCola,\"She said \"\"hi\"\"\"\n")
        val table = (result as ImportResult.Success).value
        assertEquals("She said \"hi\"", table.rows[0].cells[1])
    }

    @Test
    fun emptyCellsAreRealEmptyStringsNotNull() = runTest {
        val result = decode("name,sku,notes\nCola,SKU-1,\n")
        val table = (result as ImportResult.Success).value
        assertEquals("", table.rows[0].cells[2])
    }

    @Test
    fun shortRowsArePaddedWithNullForMissingTrailingCells() = runTest {
        val result = decode("name,sku,price\nCola,SKU-1\n")
        val table = (result as ImportResult.Success).value
        assertNull(table.rows[0].cells[2], "a row shorter than the header must pad missing trailing cells with null, distinct from a real empty string")
    }

    @Test
    fun extraCellsBeyondTheHeaderCountAreDeterministicallyDropped() = runTest {
        val result = decode("name,sku\nCola,SKU-1,EXTRA,MORE\n")
        val table = (result as ImportResult.Success).value
        assertEquals(2, table.rows[0].cells.size, "cells beyond the header's own column count must be dropped, never silently shifting meaning")
    }

    @Test
    fun duplicateHeadersAreBothPreservedPositionally() = runTest {
        val result = decode("name,name\nCola,ColaAgain\n")
        val table = (result as ImportResult.Success).value
        assertEquals(listOf("name", "name"), table.columns.map { it.rawHeader })
        assertEquals(listOf("Cola", "ColaAgain"), table.rows[0].cells)
    }

    @Test
    fun leadingZeroBarcodeIsNeverStripped() = runTest {
        val result = decode("name,barcode\nCola,00123456\n")
        val table = (result as ImportResult.Success).value
        assertEquals("00123456", table.rows[0].cells[1], "the decoder must never coerce a barcode-looking cell to a number, losing its leading zero")
    }

    @Test
    fun nullByteContentIsRejectedAsUnsafe() = runTest {
        val prefix = "name,sku\nCola,SKU-1".encodeToByteArray()
        val suffix = "\n".encodeToByteArray()
        val bytes = ByteArray(prefix.size + 1 + suffix.size)
        prefix.copyInto(bytes, 0)
        bytes[prefix.size] = 0
        suffix.copyInto(bytes, prefix.size + 1)
        val result = decoder.decode(FakeImportSource(bytes), ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.UnsafeContent>(result.error)
    }

    @Test
    fun malformedUtf8IsRejectedNotSilentlyReplaced() = runTest {
        val bytes = byteArrayOf('a'.code.toByte(), 0xFF.toByte(), 0xFE.toByte(), ','.code.toByte(), 'b'.code.toByte())
        val result = decoder.decode(FakeImportSource(bytes), ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.MalformedContent>(result.error)
    }

    @Test
    fun unterminatedQuotedFieldIsRejectedAsMalformed() = runTest {
        val result = decode("name,description\nCola,\"never closed\n")
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.MalformedContent>(result.error)
    }

    @Test
    fun emptyFileIsRejectedAsNoHeaders() = runTest {
        val result = decode("")
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.NoHeaders>(result.error)
    }

    @Test
    fun headerOnlyFileIsRejectedAsNoDataRows() = runTest {
        val result = decode("name,sku\n")
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.NoDataRows>(result.error)
    }

    @Test
    fun excessiveCellLengthIsRejected() = runTest {
        val limits = ImportLimits(maxCellLength = 10)
        val result = decode("name,sku\nCola,${"X".repeat(20)}\n", limits)
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.LimitExceeded>(result.error)
    }

    @Test
    fun excessiveRowCountIsRejected() = runTest {
        val limits = ImportLimits(maxRowCount = 2)
        val sb = StringBuilder("name,sku\n")
        repeat(5) { sb.append("Cola,SKU-$it\n") }
        val result = decode(sb.toString(), limits)
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.LimitExceeded>(result.error)
    }

    @Test
    fun oversizedFileIsRejectedBeforeParsing() = runTest {
        val limits = ImportLimits(maxCompressedFileSizeBytes = 5)
        val result = decode("name,sku\nCola,SKU-1\n", limits)
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.LimitExceeded>(result.error)
    }

    @Test
    fun semicolonDelimitedCsvIsDetectedAndParsedCorrectly() = runTest {
        val result = decode("name;sku;price\nCola;SKU-1;1.99\n")
        val table = (result as ImportResult.Success).value
        assertEquals(listOf("name", "sku", "price"), table.columns.map { it.rawHeader })
        assertEquals(listOf("Cola", "SKU-1", "1.99"), table.rows[0].cells)
    }

    @Test
    fun trailingBlankLineDoesNotProduceAPhantomEmptyRow() = runTest {
        val result = decode("name,sku\nCola,SKU-1\n\n")
        val table = (result as ImportResult.Success).value
        assertEquals(1, table.rows.size, "a real trailing blank line at end-of-file must not become a phantom empty data row")
    }
}
