package com.actionaura.retail.importing.sqlite

import com.actionaura.retail.importing.ImportLimits
import com.actionaura.retail.importing.ImportResult
import com.actionaura.retail.importing.NormalizedTable

/**
 * M5.8.8 -- the real, pure, `commonMain` orchestration of the whole
 * SQLite-import security pipeline (integrity check -> schema policy ->
 * target-table selection -> row-count bound -> bounded, explicit-column
 * read -> `NormalizedTable` composition), driven entirely through the
 * `SqliteRawReader` boundary. Fully unit-testable via a fake reader
 * (`SqliteImportPipelineTest.kt`) -- unlike XLSX's ZIP container, SQLite
 * reads have no byte-level container-format work that itself requires a
 * platform library, so the ENTIRE decision pipeline (not just isolated
 * pieces) lives here; only the real `SqliteRawReader` implementation
 * (`androidMain`'s `AndroidSqliteRawReader`, backed by
 * `android.database.sqlite.SQLiteDatabase` opened `OPEN_READONLY`) is
 * platform-specific and untestable on this host.
 */
object SqliteImportPipeline {

    fun run(reader: SqliteRawReader, limits: ImportLimits): ImportResult<NormalizedTable> {
        val integrityResult = SqliteSecurityPolicy.checkIntegrity(reader.integrityCheckPasses())
        if (integrityResult is ImportResult.Failure) return integrityResult

        val schemaObjects = reader.listSchemaObjects()
        val schemaResult = SqliteSecurityPolicy.checkSchema(schemaObjects, limits)
        if (schemaResult is ImportResult.Failure) return schemaResult

        val realTables = schemaObjects.filter { it.type == "table" && !it.name.startsWith("sqlite_") }
        val tablesWithCounts = realTables.map { it to reader.countRows(it.name) }
        val target = SqliteSecurityPolicy.chooseTargetTable(tablesWithCounts)
            ?: return ImportResult.Failure(com.actionaura.retail.importing.ImportError.MalformedContent("no real table to import"))

        val rowCount = tablesWithCounts.first { it.first.name == target.name }.second
        val rowCountResult = SqliteSecurityPolicy.checkRowCount(rowCount, limits)
        if (rowCountResult is ImportResult.Failure) return rowCountResult

        val columns = reader.listColumns(target.name)
        val columnNames = columns.map { it.name }
        val rows = reader.readRows(target.name, columnNames, limits.maxSqliteRowCount.toLong())

        return SqliteTableComposer.compose(columns, rows, limits)
    }
}
