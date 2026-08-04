package com.actionaura.retail.importing

import com.actionaura.retail.platform.PickedFile
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertIs

class PickedFileImportSourceTest {

    private fun descriptor(sizeBytes: Long) = ImportFileDescriptor(ImportSourceId("src"), "file.csv", sizeBytes, "text/csv", "csv")

    @Test
    fun aRealPickedFileWithinTheLimitReadsSuccessfully() = runTest {
        val bytes = "name,sku\nCola,SKU-1\n".encodeToByteArray()
        val source = PickedFileImportSource(PickedFile("file.csv", bytes), descriptor(bytes.size.toLong()))

        val result = source.readBounded(ImportLimits.DEFAULT)

        assertIs<ImportResult.Success<ByteArray>>(result)
        assertContentEquals(bytes, result.value)
    }

    @Test
    fun aRealPickedFileExceedingTheLimitIsRejected() = runTest {
        val bytes = ByteArray(1000)
        val tightLimits = ImportLimits(maxCompressedFileSizeBytes = 500L)
        val source = PickedFileImportSource(PickedFile("file.csv", bytes), descriptor(bytes.size.toLong()))

        val result = source.readBounded(tightLimits)

        assertIs<ImportResult.Failure>(result)
        assertIs<ImportError.LimitExceeded>(result.error)
    }
}
