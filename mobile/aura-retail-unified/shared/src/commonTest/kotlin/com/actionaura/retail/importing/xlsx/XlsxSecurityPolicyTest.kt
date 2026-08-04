package com.actionaura.retail.importing.xlsx

import com.actionaura.retail.importing.ImportLimits
import com.actionaura.retail.importing.ImportResult
import kotlin.test.Test
import kotlin.test.assertIs

private val VALID_ENTRIES = listOf(
    XlsxZipEntryMeta("[Content_Types].xml", 200, 500),
    XlsxZipEntryMeta("_rels/.rels", 100, 200),
    XlsxZipEntryMeta("xl/workbook.xml", 300, 600),
    XlsxZipEntryMeta("xl/sharedStrings.xml", 200, 400),
    XlsxZipEntryMeta("xl/worksheets/sheet1.xml", 500, 1000),
)

class XlsxSecurityPolicyTest {

    @Test
    fun aRealValidMinimalWorkbookStructureIsAccepted() {
        val result = XlsxSecurityPolicy.checkEntries(VALID_ENTRIES, ImportLimits.DEFAULT)
        assertIs<ImportResult.Success<Unit>>(result)
    }

    @Test
    fun missingContentTypesIsRejectedAsNotARealWorkbook() {
        val entries = VALID_ENTRIES.filterNot { it.name == "[Content_Types].xml" }
        val result = XlsxSecurityPolicy.checkEntries(entries, ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun macroEnabledWorkbookIsRejected() {
        val entries = VALID_ENTRIES + XlsxZipEntryMeta("xl/vbaProject.bin", 1000, 2000)
        val result = XlsxSecurityPolicy.checkEntries(entries, ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun externalLinksAreRejected() {
        val entries = VALID_ENTRIES + XlsxZipEntryMeta("xl/externalLinks/externalLink1.xml", 100, 200)
        val result = XlsxSecurityPolicy.checkEntries(entries, ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun pathTraversalEntryNameIsRejected() {
        val entries = VALID_ENTRIES + XlsxZipEntryMeta("../../etc/passwd", 10, 20)
        val result = XlsxSecurityPolicy.checkEntries(entries, ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun absolutePathEntryNameIsRejected() {
        val entries = VALID_ENTRIES + XlsxZipEntryMeta("/etc/passwd", 10, 20)
        val result = XlsxSecurityPolicy.checkEntries(entries, ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun noWorksheetPartsIsRejected() {
        val entries = VALID_ENTRIES.filterNot { it.name.startsWith("xl/worksheets/") }
        val result = XlsxSecurityPolicy.checkEntries(entries, ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun excessiveWorksheetCountIsRejected() {
        val limits = ImportLimits(maxWorksheetCount = 2)
        val entries = VALID_ENTRIES + (1..5).map { XlsxZipEntryMeta("xl/worksheets/sheet$it.xml", 100, 200) }
        val result = XlsxSecurityPolicy.checkEntries(entries, limits)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun excessiveEntryCountIsRejected() {
        val limits = ImportLimits(maxZipEntryCount = 3)
        val result = XlsxSecurityPolicy.checkEntries(VALID_ENTRIES, limits)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun realZipBombExpansionRatioIsRejected() {
        // Real zip-bomb shape: a tiny compressed entry expanding to an enormous uncompressed size.
        val bombEntry = XlsxZipEntryMeta("xl/worksheets/sheet1.xml", 100, 100_000_000)
        val entries = VALID_ENTRIES.filterNot { it.name == "xl/worksheets/sheet1.xml" } + bombEntry
        val result = XlsxSecurityPolicy.checkEntries(entries, ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun cumulativeUncompressedSizeBudgetIsEnforcedAcrossManySmallEntries() {
        val limits = ImportLimits(maxUncompressedSizeBytes = 1000, maxExpansionRatio = 1000)
        val manySmallEntries = VALID_ENTRIES + (1..50).map { XlsxZipEntryMeta("xl/media/image$it.png", 10, 100) }
        val result = XlsxSecurityPolicy.checkEntries(manySmallEntries, limits)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun zeroCompressedSizeEntryDoesNotCrashTheRatioCheck() {
        // A real, valid ZIP "stored" (uncompressed) directory entry can report compressedSize=0 -- must not divide by zero.
        val entries = VALID_ENTRIES + XlsxZipEntryMeta("xl/media/", 0, 0)
        val result = XlsxSecurityPolicy.checkEntries(entries, ImportLimits.DEFAULT)
        assertIs<ImportResult.Success<Unit>>(result)
    }
}
