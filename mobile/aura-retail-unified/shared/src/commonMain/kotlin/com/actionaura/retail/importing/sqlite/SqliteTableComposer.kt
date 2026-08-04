package com.actionaura.retail.importing.sqlite

import com.actionaura.retail.importing.ImportError
import com.actionaura.retail.importing.ImportFormat
import com.actionaura.retail.importing.ImportLimitExceeded
import com.actionaura.retail.importing.ImportLimits
import com.actionaura.retail.importing.ImportResult
import com.actionaura.retail.importing.NormalizedColumn
import com.actionaura.retail.importing.NormalizedRow
import com.actionaura.retail.importing.NormalizedTable

/**
 * M5.8.8 -- pure, `commonMain` composition of already-read SQLite rows
 * (via the real, bounded, explicit-column `SELECT` a platform
 * `SqliteRawReader` performs) into the one shared `NormalizedTable`
 * contract every decoder produces.
 */
object SqliteTableComposer {

    fun compose(columns: List<SqliteColumnMeta>, rows: List<List<String?>>, limits: ImportLimits): ImportResult<NormalizedTable> {
        if (columns.isEmpty()) return ImportResult.Failure(ImportError.NoHeaders("the chosen table has no columns"))
        if (columns.size > limits.maxColumnCount) {
            return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxColumnCount", limits.maxColumnCount.toLong(), columns.size.toLong())))
        }
        for (c in columns) {
            if (c.name.length > limits.maxHeaderLength) {
                return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxHeaderLength", limits.maxHeaderLength.toLong(), c.name.length.toLong())))
            }
        }
        if (rows.isEmpty()) return ImportResult.Failure(ImportError.NoDataRows("the chosen table has zero rows"))
        if (rows.size > limits.maxRowCount) {
            return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxRowCount", limits.maxRowCount.toLong(), rows.size.toLong())))
        }

        val normalizedColumns = columns.mapIndexed { i, c -> NormalizedColumn(i, c.name) }
        val normalizedRows = rows.mapIndexed { idx, row ->
            for (cell in row) {
                if (cell != null && cell.length > limits.maxCellLength) {
                    return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxCellLength", limits.maxCellLength.toLong(), cell.length.toLong())))
                }
            }
            NormalizedRow((idx + 2).toLong(), row)
        }
        return ImportResult.Success(NormalizedTable(normalizedColumns, normalizedRows, ImportFormat.SQLITE))
    }
}
