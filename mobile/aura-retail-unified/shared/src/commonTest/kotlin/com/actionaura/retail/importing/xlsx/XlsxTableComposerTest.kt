package com.actionaura.retail.importing.xlsx

import com.actionaura.retail.importing.ImportLimits
import com.actionaura.retail.importing.ImportResult
import com.actionaura.retail.importing.xlsx.XlsxSheetXmlReader.XlsxCell
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertNull

class XlsxTableComposerTest {

    @Test
    fun composesARealHeaderRowAndDataRows() {
        val cells = listOf(
            XlsxCell(0, 1, "name", false), XlsxCell(1, 1, "sku", false),
            XlsxCell(0, 2, "Cola", false), XlsxCell(1, 2, "SKU-1", false),
        )
        val result = XlsxTableComposer.compose(cells, ImportLimits.DEFAULT)
        val table = (result as ImportResult.Success).value
        assertEquals(listOf("name", "sku"), table.columns.map { it.rawHeader })
        assertEquals(1, table.rows.size)
        assertEquals(listOf("Cola", "SKU-1"), table.rows[0].cells)
    }

    @Test
    fun missingCellInARowBecomesRealNull() {
        val cells = listOf(
            XlsxCell(0, 1, "name", false), XlsxCell(1, 1, "sku", false),
            XlsxCell(0, 2, "Cola", false), // B2 never appears in the source at all
        )
        val result = XlsxTableComposer.compose(cells, ImportLimits.DEFAULT)
        val table = (result as ImportResult.Success).value
        assertNull(table.rows[0].cells[1])
    }

    @Test
    fun anyFormulaCellAnywhereRejectsTheWholeSheet() {
        val cells = listOf(
            XlsxCell(0, 1, "name", false),
            XlsxCell(0, 2, "Cola", false),
            XlsxCell(0, 3, "100", true), // a formula cell elsewhere in the sheet
        )
        val result = XlsxTableComposer.compose(cells, ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.UnsafeContent>(result.error)
    }

    @Test
    fun blankHeaderCellGetsARealSyntheticColumnName() {
        val cells = listOf(
            XlsxCell(0, 1, "name", false), XlsxCell(1, 1, null, false),
            XlsxCell(0, 2, "Cola", false), XlsxCell(1, 2, "extra", false),
        )
        val result = XlsxTableComposer.compose(cells, ImportLimits.DEFAULT)
        val table = (result as ImportResult.Success).value
        assertEquals("Col2", table.columns[1].rawHeader)
    }

    @Test
    fun emptyCellListIsRejectedAsNoHeaders() {
        val result = XlsxTableComposer.compose(emptyList(), ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.NoHeaders>(result.error)
    }

    @Test
    fun headerOnlySheetIsRejectedAsNoDataRows() {
        val cells = listOf(XlsxCell(0, 1, "name", false), XlsxCell(1, 1, "sku", false))
        val result = XlsxTableComposer.compose(cells, ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.NoDataRows>(result.error)
    }

    @Test
    fun leadingZeroBarcodeValueIsPreservedAsText() {
        val cells = listOf(
            XlsxCell(0, 1, "barcode", false),
            XlsxCell(0, 2, "00123456", false),
        )
        val result = XlsxTableComposer.compose(cells, ImportLimits.DEFAULT)
        val table = (result as ImportResult.Success).value
        assertEquals("00123456", table.rows[0].cells[0])
    }
}
