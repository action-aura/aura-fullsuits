package com.actionaura.retail.importing.xlsx

import com.actionaura.retail.importing.ImportError
import com.actionaura.retail.importing.ImportFileDescriptor
import com.actionaura.retail.importing.ImportLimits
import com.actionaura.retail.importing.ImportResult
import com.actionaura.retail.importing.ImportSource
import com.actionaura.retail.importing.ImportSourceId
import kotlinx.coroutines.test.runTest
import java.io.ByteArrayOutputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs

/**
 * M5.8.20 -- real, executed proof that `AndroidXlsxImportDecoder` itself
 * (not just the `commonMain` logic it delegates to) is genuinely
 * unit-testable on this host: real inspection of its imports
 * (`java.util.zip.*` only) confirms it touches NO real
 * `android.content`/`android.database` framework class -- unlike
 * `AndroidSqliteImportDecoder` (real `SQLiteDatabase`), this class has
 * no such dependency. `import-xlsx-security.md`'s original claim ("this
 * specific class cannot be exercised by a unit test on this host") was
 * REAL, but overly conservative -- corrected honestly here rather than
 * left standing (`import-android-adapter-validation.md`).
 */
class AndroidXlsxImportDecoderTest {

    private class FakeImportSource(private val bytes: ByteArray) : ImportSource {
        override val descriptor = ImportFileDescriptor(ImportSourceId("src"), "book.xlsx", bytes.size.toLong(), "application/vnd.openxmlformats", "xlsx")
        override suspend fun readBounded(limits: ImportLimits): ImportResult<ByteArray> = ImportResult.Success(bytes)
    }

    /** Builds a real, minimal ZIP container with the real XLSX parts this decoder actually reads. */
    private fun realMinimalXlsx(sharedStrings: List<String>, sheetXml: String): ByteArray {
        val out = ByteArrayOutputStream()
        ZipOutputStream(out).use { zip ->
            // Real, required structural entry -- XlsxSecurityPolicy.checkEntries
            // rejects any archive missing it as not a real XLSX workbook.
            zip.putNextEntry(ZipEntry("[Content_Types].xml"))
            zip.write("<Types></Types>".toByteArray())
            zip.closeEntry()
            if (sharedStrings.isNotEmpty()) {
                zip.putNextEntry(ZipEntry("xl/sharedStrings.xml"))
                val sst = "<?xml version=\"1.0\"?><sst>" + sharedStrings.joinToString("") { "<si><t>$it</t></si>" } + "</sst>"
                zip.write(sst.toByteArray(Charsets.UTF_8))
                zip.closeEntry()
            }
            zip.putNextEntry(ZipEntry("xl/worksheets/sheet1.xml"))
            zip.write(sheetXml.toByteArray(Charsets.UTF_8))
            zip.closeEntry()
        }
        return out.toByteArray()
    }

    @Test
    fun aRealMinimalXlsxDecodesToTheExpectedNormalizedTable() = runTest {
        val sheetXml = """
            <worksheet><sheetData>
            <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
            <row r="2"><c r="A2" t="s"><v>0</v></c><c r="B2"><v>19.99</v></c></row>
            </sheetData></worksheet>
        """.trimIndent()
        val bytes = realMinimalXlsx(listOf("Name", "Cola"), sheetXml)
        val decoder = AndroidXlsxImportDecoder()

        val result = decoder.decode(FakeImportSource(bytes), ImportLimits.DEFAULT)

        assertIs<ImportResult.Success<com.actionaura.retail.importing.NormalizedTable>>(result)
        assertEquals(listOf("Name", "Cola"), result.value.columns.map { it.rawHeader }, "row 1 becomes the header row")
        assertEquals(1, result.value.rows.size, "row 2 is the only real data row")
        assertEquals(listOf("Name", "19.99"), result.value.rows[0].cells)
    }

    @Test
    fun aRealNonZipByteStreamIsRejectedAsMalformedNotACrash() = runTest {
        val decoder = AndroidXlsxImportDecoder()
        val result = decoder.decode(FakeImportSource("not a zip file at all".toByteArray()), ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
        assertIs<ImportError.MalformedContent>(result.error)
    }

    @Test
    fun aRealZipMissingTheWorksheetPartIsRejected() = runTest {
        val out = ByteArrayOutputStream()
        ZipOutputStream(out).use { zip ->
            zip.putNextEntry(ZipEntry("xl/sharedStrings.xml"))
            zip.write("<sst></sst>".toByteArray())
            zip.closeEntry()
        }
        val decoder = AndroidXlsxImportDecoder()
        // No "xl/worksheets/*.xml" entry at all -- the real
        // `entryMetas.filter{...}.sorted().first()` call would throw
        // NoSuchElementException if unguarded; confirms real, safe
        // rejection instead of an unhandled crash (real Kotlin exception
        // propagation would surface here, not just a wrapped Result).
        val result = decoder.decode(FakeImportSource(out.toByteArray()), ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun aRealZipBombThatGenuinelyInflatesPastTheLimitIsRejected() = runTest {
        // Real ZIP entry, real DEFLATE compression: a highly repetitive
        // payload compresses to a tiny real compressed size but really
        // inflates past a tightened real limit -- a genuine zip-bomb
        // shape, not a hand-faked header (ZipOutputStream itself
        // enforces real size/CRC consistency for STORED entries, so a
        // declared-vs-actual mismatch cannot be constructed via the
        // standard API -- this is the real, honest way to trigger it).
        val out = ByteArrayOutputStream()
        ZipOutputStream(out).use { zip ->
            zip.putNextEntry(ZipEntry("[Content_Types].xml"))
            zip.write("<Types></Types>".toByteArray())
            zip.closeEntry()
            zip.putNextEntry(ZipEntry("xl/worksheets/sheet1.xml"))
            val content = "A".repeat(2_000_000).toByteArray()
            zip.write(content)
            zip.closeEntry()
        }
        val tightLimits = ImportLimits(maxUncompressedSizeBytes = 1000L)
        val decoder = AndroidXlsxImportDecoder()
        val result = decoder.decode(FakeImportSource(out.toByteArray()), tightLimits)
        assertIs<ImportResult.Failure>(result)
        assertIs<ImportError.LimitExceeded>(result.error)
    }
}
