package com.actionaura.retail.importing.xlsx

import com.actionaura.retail.importing.ImportDecoder
import com.actionaura.retail.importing.ImportError
import com.actionaura.retail.importing.ImportFormat
import com.actionaura.retail.importing.ImportLimitExceeded
import com.actionaura.retail.importing.ImportLimits
import com.actionaura.retail.importing.ImportResult
import com.actionaura.retail.importing.ImportSource
import com.actionaura.retail.importing.NormalizedTable
import java.io.ByteArrayOutputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream

/**
 * M5.8.7 -- the real, `androidMain`-scoped XLSX decoder
 * (`import-parser-decision.md`'s own Option-B choice: `java.util.zip`
 * is a real, native part of the Android platform, not a smuggled-in
 * JVM-desktop dependency). Every SECURITY DECISION is delegated to the
 * real, unit-tested `commonMain` logic (`XlsxSecurityPolicy`,
 * `XlsxSheetXmlReader`, `XlsxTableComposer`) -- this class does only
 * the real byte-level ZIP I/O those functions cannot do themselves.
 *
 * Real, disclosed limitation (`import-parser-decision.md`,
 * `import-xlsx-security.md`): this specific class cannot be exercised
 * by a unit test on this host (no Robolectric, no device/emulator) --
 * only the pure logic it delegates to is proven here.
 */
class AndroidXlsxImportDecoder : ImportDecoder {
    override val format: ImportFormat = ImportFormat.XLSX

    override suspend fun decode(source: ImportSource, limits: ImportLimits): ImportResult<NormalizedTable> {
        val bytesResult = source.readBounded(limits)
        val bytes = when (bytesResult) {
            is ImportResult.Success -> bytesResult.value
            is ImportResult.Failure -> return bytesResult
        }

        // Real, bounded first pass: enumerate every entry's real
        // compressed/uncompressed size from the ZIP stream itself
        // (`ZipEntry.size`/`getCompressedSize` are read from the real
        // local/central-directory metadata) BEFORE extracting any
        // entry's bytes -- the zip-bomb defense must reject before
        // inflating, not after.
        val entryMetas = mutableListOf<XlsxZipEntryMeta>()
        try {
            ZipInputStream(bytes.inputStream()).use { zis ->
                var entry: ZipEntry? = zis.nextEntry
                while (entry != null) {
                    val uncompressedSize = if (entry.size >= 0) entry.size else countBytesBounded(zis, limits.maxUncompressedSizeBytes)
                    entryMetas += XlsxZipEntryMeta(entry.name, entry.compressedSize.coerceAtLeast(0), uncompressedSize)
                    zis.closeEntry()
                    entry = zis.nextEntry
                }
            }
        } catch (e: Exception) {
            return ImportResult.Failure(ImportError.MalformedContent("not a real ZIP/XLSX container: ${e.message}"))
        }

        val policyResult = XlsxSecurityPolicy.checkEntries(entryMetas, limits)
        if (policyResult is ImportResult.Failure) return policyResult

        val sharedStringsXml = extractEntryText(bytes, "xl/sharedStrings.xml", limits)
        val worksheetName = entryMetas.map { it.name }.filter { it.startsWith("xl/worksheets/") && it.endsWith(".xml") }.sorted().first()
        val sheetXmlResult = extractEntryTextOrFail(bytes, worksheetName, limits)
        val sheetXml = when (sheetXmlResult) {
            is ImportResult.Success -> sheetXmlResult.value
            is ImportResult.Failure -> return sheetXmlResult
        }

        val sharedStrings = sharedStringsXml?.let { XlsxSheetXmlReader.readSharedStrings(it) } ?: emptyList()
        val cellsResult = XlsxSheetXmlReader.readCells(sheetXml, sharedStrings, limits)
        val cells = when (cellsResult) {
            is ImportResult.Success -> cellsResult.value
            is ImportResult.Failure -> return cellsResult
        }

        return XlsxTableComposer.compose(cells, limits)
    }

    /** Reads one named ZIP entry's content as UTF-8 text, bounded, or null if the entry does not exist (e.g. a workbook with no shared strings table -- valid when every cell is an inline string or number). */
    private fun extractEntryText(bytes: ByteArray, entryName: String, limits: ImportLimits): String? {
        ZipInputStream(bytes.inputStream()).use { zis ->
            var entry: ZipEntry? = zis.nextEntry
            while (entry != null) {
                if (entry.name == entryName) {
                    return readBounded(zis, limits.maxUncompressedSizeBytes)?.toString(Charsets.UTF_8)
                }
                zis.closeEntry()
                entry = zis.nextEntry
            }
        }
        return null
    }

    private fun extractEntryTextOrFail(bytes: ByteArray, entryName: String, limits: ImportLimits): ImportResult<String> {
        val text = extractEntryText(bytes, entryName, limits)
            ?: return ImportResult.Failure(ImportError.MalformedContent("worksheet part \"$entryName\" is missing from the archive"))
        return ImportResult.Success(text)
    }

    private fun readBounded(zis: ZipInputStream, maxBytes: Long): ByteArray? {
        val out = ByteArrayOutputStream()
        val buffer = ByteArray(8192)
        var total = 0L
        while (true) {
            val n = zis.read(buffer)
            if (n < 0) break
            total += n
            if (total > maxBytes) return null // real zip-bomb backstop during extraction itself, not just from metadata
            out.write(buffer, 0, n)
        }
        return out.toByteArray()
    }

    private fun countBytesBounded(zis: ZipInputStream, maxBytes: Long): Long {
        val buffer = ByteArray(8192)
        var total = 0L
        while (true) {
            val n = zis.read(buffer)
            if (n < 0) break
            total += n
            if (total > maxBytes) return total // let the caller's own limit check reject it
        }
        return total
    }
}
