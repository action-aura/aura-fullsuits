package com.actionaura.retail.importing.xlsx

import com.actionaura.retail.importing.ImportLimits
import com.actionaura.retail.importing.ImportResult
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertTrue

class XlsxSheetXmlReaderTest {

    @Test
    fun readsSharedStringsRealShape() {
        val xml = """<?xml version="1.0"?><sst><si><t>Cola</t></si><si><t>Juice</t></si></sst>"""
        val result = XlsxSheetXmlReader.readSharedStrings(xml)
        assertEquals(listOf("Cola", "Juice"), result)
    }

    @Test
    fun readsRichTextRunsConcatenated() {
        val xml = """<sst><si><r><t>Co</t></r><r><t>la</t></r></si></sst>"""
        assertEquals(listOf("Cola"), XlsxSheetXmlReader.readSharedStrings(xml))
    }

    @Test
    fun decodesXmlEntitiesInSharedStrings() {
        val xml = """<sst><si><t>A &amp; B &lt;test&gt;</t></si></sst>"""
        assertEquals(listOf("A & B <test>"), XlsxSheetXmlReader.readSharedStrings(xml))
    }

    @Test
    fun readsSharedStringCellsAndNumericCells() {
        val sharedStrings = listOf("Cola", "SKU-1")
        val sheet = """
            <worksheet><sheetData>
            <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
            <row r="2"><c r="A2" t="s"><v>0</v></c><c r="B2"><v>19.99</v></c></row>
            </sheetData></worksheet>
        """.trimIndent()
        val result = XlsxSheetXmlReader.readCells(sheet, sharedStrings, ImportLimits.DEFAULT)
        val cells = (result as ImportResult.Success).value
        assertEquals(4, cells.size)
        val a1 = cells.first { it.rowIndex == 1L && it.columnIndex == 0 }
        assertEquals("Cola", a1.rawValue)
        val b2 = cells.first { it.rowIndex == 2L && it.columnIndex == 1 }
        assertEquals("19.99", b2.rawValue)
    }

    @Test
    fun columnLetterToIndexRealCases() {
        assertEquals(0, XlsxSheetXmlReader.columnLetterToIndex("A1"))
        assertEquals(25, XlsxSheetXmlReader.columnLetterToIndex("Z9"))
        assertEquals(26, XlsxSheetXmlReader.columnLetterToIndex("AA1"))
        assertEquals(27, XlsxSheetXmlReader.columnLetterToIndex("AB100"))
    }

    @Test
    fun formulaCellIsFlaggedNotSilentlyTrustedViaItsCachedValue() {
        val sheet = """
            <worksheet><sheetData>
            <row r="1"><c r="A1"><f>SUM(B1:B2)</f><v>100</v></c></row>
            </sheetData></worksheet>
        """.trimIndent()
        val result = XlsxSheetXmlReader.readCells(sheet, emptyList(), ImportLimits.DEFAULT)
        val cells = (result as ImportResult.Success).value
        assertTrue(cells.single().hasFormula, "a cell containing <f> must be flagged, its cached <v> must never be silently trusted")
    }

    @Test
    fun inlineStringCellIsReadDirectlyWithoutASharedStringsLookup() {
        val sheet = """<worksheet><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Direct Text</t></is></c></row></sheetData></worksheet>"""
        val result = XlsxSheetXmlReader.readCells(sheet, emptyList(), ImportLimits.DEFAULT)
        val cells = (result as ImportResult.Success).value
        assertEquals("Direct Text", cells.single().rawValue)
    }

    @Test
    fun emptyCellIsRealNullNotAnErrorOrEmptyString() {
        val sheet = """<worksheet><sheetData><row r="1"><c r="A1"/><c r="B1" t="s"><v>0</v></c></row></sheetData></worksheet>"""
        val result = XlsxSheetXmlReader.readCells(sheet, listOf("value"), ImportLimits.DEFAULT)
        val cells = (result as ImportResult.Success).value
        val empty = cells.first { it.columnIndex == 0 }
        assertEquals(null, empty.rawValue)
    }

    @Test
    fun excessiveCellLengthIsRejected() {
        val limits = ImportLimits(maxCellLength = 10)
        val sheet = """<worksheet><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>${"X".repeat(20)}</t></is></c></row></sheetData></worksheet>"""
        val result = XlsxSheetXmlReader.readCells(sheet, emptyList(), limits)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun excessiveRowCountIsRejected() {
        val limits = ImportLimits(maxRowCount = 2)
        val sb = StringBuilder("<worksheet><sheetData>")
        repeat(5) { i -> sb.append("""<row r="${i + 1}"><c r="A${i + 1}" t="inlineStr"><is><t>v</t></is></c></row>""") }
        sb.append("</sheetData></worksheet>")
        val result = XlsxSheetXmlReader.readCells(sb.toString(), emptyList(), limits)
        assertIs<ImportResult.Failure>(result)
    }
}
