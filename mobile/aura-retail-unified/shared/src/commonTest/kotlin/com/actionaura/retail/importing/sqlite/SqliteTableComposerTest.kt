package com.actionaura.retail.importing.sqlite

import com.actionaura.retail.importing.ImportLimits
import com.actionaura.retail.importing.ImportResult
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs

class SqliteTableComposerTest {

    @Test
    fun composesRealColumnsAndRows() {
        val columns = listOf(SqliteColumnMeta("name", "TEXT"), SqliteColumnMeta("sku", "TEXT"))
        val rows = listOf(listOf("Cola", "SKU-1"), listOf("Juice", "SKU-2"))
        val result = SqliteTableComposer.compose(columns, rows, ImportLimits.DEFAULT)
        val table = (result as ImportResult.Success).value
        assertEquals(listOf("name", "sku"), table.columns.map { it.rawHeader })
        assertEquals(2, table.rows.size)
        assertEquals(listOf("Cola", "SKU-1"), table.rows[0].cells)
    }

    @Test
    fun leadingZeroBarcodeValueIsPreservedAsText() {
        val columns = listOf(SqliteColumnMeta("barcode", "TEXT"))
        val rows = listOf(listOf("00123456"))
        val result = SqliteTableComposer.compose(columns, rows, ImportLimits.DEFAULT)
        val table = (result as ImportResult.Success).value
        assertEquals("00123456", table.rows[0].cells[0])
    }

    @Test
    fun nullCellIsPreservedAsRealNull() {
        val columns = listOf(SqliteColumnMeta("name", "TEXT"), SqliteColumnMeta("notes", "TEXT"))
        val rows = listOf(listOf("Cola", null))
        val result = SqliteTableComposer.compose(columns, rows, ImportLimits.DEFAULT)
        val table = (result as ImportResult.Success).value
        assertEquals(null, table.rows[0].cells[1])
    }

    @Test
    fun noColumnsIsRejectedAsNoHeaders() {
        val result = SqliteTableComposer.compose(emptyList(), emptyList(), ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.NoHeaders>(result.error)
    }

    @Test
    fun noRowsIsRejectedAsNoDataRows() {
        val columns = listOf(SqliteColumnMeta("name", "TEXT"))
        val result = SqliteTableComposer.compose(columns, emptyList(), ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.NoDataRows>(result.error)
    }

    @Test
    fun excessiveRowCountIsRejected() {
        val limits = ImportLimits(maxRowCount = 2)
        val columns = listOf(SqliteColumnMeta("name", "TEXT"))
        val rows = (1..5).map { listOf("Product-$it") }
        val result = SqliteTableComposer.compose(columns, rows, limits)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun excessiveCellLengthIsRejected() {
        val limits = ImportLimits(maxCellLength = 5)
        val columns = listOf(SqliteColumnMeta("name", "TEXT"))
        val rows = listOf(listOf("this is way too long"))
        val result = SqliteTableComposer.compose(columns, rows, limits)
        assertIs<ImportResult.Failure>(result)
    }
}
