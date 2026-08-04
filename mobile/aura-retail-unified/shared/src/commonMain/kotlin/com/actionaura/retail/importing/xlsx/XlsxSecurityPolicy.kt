package com.actionaura.retail.importing.xlsx

import com.actionaura.retail.importing.ImportError
import com.actionaura.retail.importing.ImportLimitExceeded
import com.actionaura.retail.importing.ImportLimits
import com.actionaura.retail.importing.ImportResult

/** Real ZIP central-directory metadata for one entry -- platform-decoded (`androidMain`'s real `java.util.zip.ZipEntry`), but the SECURITY DECISIONS below are pure `commonMain` logic, real and unit-testable independent of any platform ZIP library. */
data class XlsxZipEntryMeta(val name: String, val compressedSize: Long, val uncompressedSize: Long)

/**
 * M5.8.7 -- real, pure, platform-independent XLSX security policy.
 * `import-parser-decision.md`'s own split: the actual ZIP byte I/O lives
 * in `androidMain` (untestable here without a device/Robolectric), but
 * every SECURITY DECISION made from the resulting entry list is real,
 * pure logic, proven against real synthetic entry lists in
 * `XlsxSecurityPolicyTest.kt`.
 */
object XlsxSecurityPolicy {

    private val REQUIRED_ENTRY = "[Content_Types].xml"
    private val WORKSHEET_PREFIX = "xl/worksheets/"
    private val MACRO_ENTRY = "xl/vbaProject.bin"
    private val EXTERNAL_LINKS_PREFIX = "xl/externalLinks/"

    /**
     * Real, bounded checks against the full ZIP entry list BEFORE any
     * entry's bytes are read: entry count, path traversal, required
     * workbook structure, macro rejection, external-link rejection,
     * worksheet count, and the real zip-bomb defenses (per-entry
     * expansion ratio + cumulative uncompressed-size budget).
     */
    fun checkEntries(entries: List<XlsxZipEntryMeta>, limits: ImportLimits): ImportResult<Unit> {
        if (entries.size > limits.maxZipEntryCount) {
            return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxZipEntryCount", limits.maxZipEntryCount.toLong(), entries.size.toLong())))
        }
        for (entry in entries) {
            if (entry.name.contains("..") || entry.name.startsWith("/") || entry.name.contains('\\')) {
                return ImportResult.Failure(ImportError.UnsafeContent("ZIP entry \"${entry.name}\" is a path-traversal risk"))
            }
        }
        if (entries.none { it.name == REQUIRED_ENTRY }) {
            return ImportResult.Failure(ImportError.MalformedContent("not a real XLSX workbook -- missing $REQUIRED_ENTRY"))
        }
        if (entries.any { it.name == MACRO_ENTRY }) {
            return ImportResult.Failure(ImportError.UnsafeContent("macro-enabled workbook (found $MACRO_ENTRY) is not accepted as a plain XLSX import"))
        }
        if (entries.any { it.name.startsWith(EXTERNAL_LINKS_PREFIX) }) {
            return ImportResult.Failure(ImportError.UnsafeContent("workbook contains external link definitions, which are rejected"))
        }
        val worksheetCount = entries.count { it.name.startsWith(WORKSHEET_PREFIX) && it.name.endsWith(".xml") }
        if (worksheetCount == 0) {
            return ImportResult.Failure(ImportError.MalformedContent("workbook has no worksheet parts"))
        }
        if (worksheetCount > limits.maxWorksheetCount) {
            return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxWorksheetCount", limits.maxWorksheetCount.toLong(), worksheetCount.toLong())))
        }

        var cumulativeUncompressed = 0L
        for (entry in entries) {
            if (entry.compressedSize > 0) {
                val ratio = entry.uncompressedSize / entry.compressedSize
                if (ratio > limits.maxExpansionRatio) {
                    return ImportResult.Failure(ImportError.UnsafeContent("ZIP entry \"${entry.name}\" has an expansion ratio of ${ratio}x, exceeding the real zip-bomb defense threshold of ${limits.maxExpansionRatio}x"))
                }
            }
            cumulativeUncompressed += entry.uncompressedSize
            if (cumulativeUncompressed > limits.maxUncompressedSizeBytes) {
                return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxUncompressedSizeBytes", limits.maxUncompressedSizeBytes, cumulativeUncompressed)))
            }
        }
        return ImportResult.Success(Unit)
    }
}
