package com.actionaura.retail.importing.xlsx

import com.actionaura.retail.importing.ImportError
import com.actionaura.retail.importing.ImportFormat
import com.actionaura.retail.importing.ImportLimitExceeded
import com.actionaura.retail.importing.ImportLimits
import com.actionaura.retail.importing.ImportResult
import com.actionaura.retail.importing.NormalizedColumn
import com.actionaura.retail.importing.NormalizedRow
import com.actionaura.retail.importing.NormalizedTable

/**
 * M5.8.7 -- pure, `commonMain`, real composition of already-extracted
 * `XlsxSheetXmlReader.XlsxCell` values into the one shared
 * `NormalizedTable` contract every decoder produces. Row 1's cells
 * become headers (matching every real Retail XLSX export's own
 * convention, same as CSV/JSON); formula cells are rejected here, never
 * silently trusted via their cached `<v>` result
 * (M5.8.7's own explicit canonical policy).
 */
object XlsxTableComposer {

    fun compose(cells: List<XlsxSheetXmlReader.XlsxCell>, limits: ImportLimits): ImportResult<NormalizedTable> {
        if (cells.isEmpty()) return ImportResult.Failure(ImportError.NoHeaders("worksheet has no cells at all"))

        val formulaCell = cells.firstOrNull { it.hasFormula }
        if (formulaCell != null) {
            return ImportResult.Failure(ImportError.UnsafeContent("formula cell at row ${formulaCell.rowIndex}, column ${formulaCell.columnIndex + 1} is not supported -- cached formula results are never treated as trusted values"))
        }

        val rowNumbers = cells.map { it.rowIndex }.distinct().sorted()
        val headerRowNumber = rowNumbers.first()
        val headerCells = cells.filter { it.rowIndex == headerRowNumber }.sortedBy { it.columnIndex }
        val columnCount = (headerCells.maxOfOrNull { it.columnIndex } ?: -1) + 1
        if (columnCount == 0) return ImportResult.Failure(ImportError.NoHeaders("the header row has no cells"))
        if (columnCount > limits.maxColumnCount) {
            return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxColumnCount", limits.maxColumnCount.toLong(), columnCount.toLong())))
        }

        val headers = MutableList(columnCount) { i -> "Col${i + 1}" }
        for (c in headerCells) {
            val text = c.rawValue?.trim().orEmpty()
            if (text.length > limits.maxHeaderLength) {
                return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxHeaderLength", limits.maxHeaderLength.toLong(), text.length.toLong())))
            }
            if (text.isNotEmpty() && c.columnIndex in headers.indices) headers[c.columnIndex] = text
        }
        val columns = headers.mapIndexed { i, h -> NormalizedColumn(i, h) }

        val dataRowNumbers = rowNumbers.filter { it != headerRowNumber }
        if (dataRowNumbers.isEmpty()) return ImportResult.Failure(ImportError.NoDataRows("worksheet has a header row but no data rows"))
        if (dataRowNumbers.size > limits.maxRowCount) {
            return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxRowCount", limits.maxRowCount.toLong(), dataRowNumbers.size.toLong())))
        }

        val cellsByRow = cells.filter { it.rowIndex != headerRowNumber }.groupBy { it.rowIndex }
        val rows = dataRowNumbers.sorted().map { rowNum ->
            val rowCells = cellsByRow[rowNum].orEmpty().associateBy { it.columnIndex }
            val values = MutableList<String?>(columnCount) { null }
            for (i in 0 until columnCount) {
                values[i] = rowCells[i]?.rawValue
            }
            NormalizedRow(rowNum, values)
        }

        return ImportResult.Success(NormalizedTable(columns, rows, ImportFormat.XLSX))
    }
}
