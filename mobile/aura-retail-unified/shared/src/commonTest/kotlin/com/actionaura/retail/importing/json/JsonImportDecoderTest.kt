package com.actionaura.retail.importing.json

import com.actionaura.retail.importing.ImportFileDescriptor
import com.actionaura.retail.importing.ImportLimits
import com.actionaura.retail.importing.ImportResult
import com.actionaura.retail.importing.ImportSource
import com.actionaura.retail.importing.ImportSourceId
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertNull

private class FakeJsonSource(private val bytes: ByteArray) : ImportSource {
    override val descriptor = ImportFileDescriptor(ImportSourceId("fake"), "test.json", bytes.size.toLong(), "application/json", "json")
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

class JsonImportDecoderTest {
    private val decoder = JsonImportDecoder()

    private suspend fun decode(text: String, limits: ImportLimits = ImportLimits.DEFAULT) =
        decoder.decode(FakeJsonSource(text.encodeToByteArray()), limits)

    @Test
    fun rootArrayOfRowObjectsDecodesRealHeadersAndRows() = runTest {
        val result = decode("""[{"name":"Cola","sku":"SKU-1"},{"name":"Juice","sku":"SKU-2"}]""")
        val table = (result as ImportResult.Success).value
        assertEquals(listOf("name", "sku"), table.columns.map { it.rawHeader })
        assertEquals(2, table.rows.size)
        assertEquals(listOf("Cola", "SKU-1"), table.rows[0].cells)
    }

    @Test
    fun rootObjectWithOneNamedArrayIsSupported() = runTest {
        val result = decode("""{"products":[{"name":"Cola","sku":"SKU-1"}]}""")
        val table = (result as ImportResult.Success).value
        assertEquals(listOf("name", "sku"), table.columns.map { it.rawHeader })
        assertEquals(1, table.rows.size)
    }

    @Test
    fun rootObjectWithTwoArraysIsRejectedAsAmbiguous() = runTest {
        val result = decode("""{"products":[{"name":"Cola"}],"categories":[{"name":"Drinks"}]}""")
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun nullValueBecomesARealNullCellNotAnError() = runTest {
        val result = decode("""[{"name":"Cola","notes":null}]""")
        val table = (result as ImportResult.Success).value
        assertNull(table.rows[0].cells[1])
    }

    @Test
    fun numericLiteralIsPreservedAsExactText() = runTest {
        val result = decode("""[{"name":"Cola","price":19.99}]""")
        val table = (result as ImportResult.Success).value
        assertEquals("19.99", table.rows[0].cells[1], "a JSON number must be preserved as its own exact text, never round-tripped through Double first")
    }

    @Test
    fun leadingZeroBarcodeStringIsNeverStripped() = runTest {
        val result = decode("""[{"name":"Cola","barcode":"00123456"}]""")
        val table = (result as ImportResult.Success).value
        assertEquals("00123456", table.rows[0].cells[1])
    }

    @Test
    fun nanLiteralIsRejectedNotSilentlyAccepted() = runTest {
        val result = decode("""[{"name":"Cola","price":NaN}]""")
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun infinityLiteralIsRejectedNotSilentlyAccepted() = runTest {
        val result = decode("""[{"name":"Cola","price":Infinity}]""")
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun duplicateObjectKeyIsRejectedNotSilentlyResolvedToTheLastValue() = runTest {
        val result = decode("""[{"name":"Cola","name":"ColaAgain"}]""")
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.MalformedContent>(result.error)
    }

    @Test
    fun duplicateKeyInADifferentObjectIsNotAFalsePositive() = runTest {
        // The SAME key name appearing once in two DIFFERENT row objects is real, normal, and must not be rejected.
        val result = decode("""[{"name":"Cola"},{"name":"Juice"}]""")
        assertIs<ImportResult.Success<*>>(result)
    }

    @Test
    fun excessiveNestingDepthIsRejected() = runTest {
        val limits = ImportLimits(maxJsonDepth = 3)
        val deeplyNested = """[{"name":{"a":{"b":{"c":"too deep"}}}}]"""
        val result = decode(deeplyNested, limits)
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.LimitExceeded>(result.error)
    }

    @Test
    fun nestedObjectAsACellValueIsRejected() = runTest {
        val result = decode("""[{"name":"Cola","meta":{"a":1}}]""")
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.UnsafeContent>(result.error)
    }

    @Test
    fun nestedArrayAsACellValueIsRejected() = runTest {
        val result = decode("""[{"name":"Cola","tags":["a","b"]}]""")
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.UnsafeContent>(result.error)
    }

    @Test
    fun malformedJsonIsRejectedSafely() = runTest {
        val result = decode("""[{"name": "Cola" "sku": "SKU-1"}]""") // missing comma
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun emptyArrayIsRejectedAsNoDataRows() = runTest {
        val result = decode("[]")
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.NoDataRows>(result.error)
    }

    @Test
    fun nonArrayNonObjectRootIsRejected() = runTest {
        val result = decode("\"just a string\"")
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun excessiveArrayLengthIsRejected() = runTest {
        val limits = ImportLimits(maxJsonArrayLength = 2, maxRowCount = 100)
        val sb = StringBuilder("[")
        repeat(5) { if (it > 0) sb.append(","); sb.append("""{"name":"P$it"}""") }
        sb.append("]")
        val result = decode(sb.toString(), limits)
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.LimitExceeded>(result.error)
    }

    @Test
    fun nullByteContentIsRejectedAsUnsafe() = runTest {
        val prefix = """[{"name":"Cola""".encodeToByteArray()
        val suffix = """"}]""".encodeToByteArray()
        val bytes = ByteArray(prefix.size + 1 + suffix.size)
        prefix.copyInto(bytes, 0)
        bytes[prefix.size] = 0
        suffix.copyInto(bytes, prefix.size + 1)
        val result = decoder.decode(FakeJsonSource(bytes), ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.UnsafeContent>(result.error)
    }

    @Test
    fun arabicTextValueIsPreservedExactly() = runTest {
        val result = decode("""[{"name":"كولا","category":"مشروبات"}]""")
        val table = (result as ImportResult.Success).value
        assertEquals("كولا", table.rows[0].cells[0])
        assertEquals("مشروبات", table.rows[0].cells[1])
    }
}
