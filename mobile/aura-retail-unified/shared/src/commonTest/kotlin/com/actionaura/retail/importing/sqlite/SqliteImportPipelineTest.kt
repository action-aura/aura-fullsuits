package com.actionaura.retail.importing.sqlite

import com.actionaura.retail.importing.ImportLimits
import com.actionaura.retail.importing.ImportResult
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs

private class FakeSqliteRawReader(
    private val schemaObjects: List<SqliteSchemaObject>,
    private val rowCounts: Map<String, Long>,
    private val columnsByTable: Map<String, List<SqliteColumnMeta>>,
    private val rowsByTable: Map<String, List<List<String?>>>,
    private val integrityOk: Boolean = true,
) : SqliteRawReader {
    var closed = false
        private set

    override fun listSchemaObjects() = schemaObjects
    override fun listColumns(tableName: String) = columnsByTable[tableName].orEmpty()
    override fun countRows(tableName: String) = rowCounts[tableName] ?: 0L
    override fun readRows(tableName: String, columnNames: List<String>, limit: Long) = rowsByTable[tableName].orEmpty()
    override fun integrityCheckPasses() = integrityOk
    override fun close() { closed = true }
}

class SqliteImportPipelineTest {

    @Test
    fun realEndToEndPipelinePicksTheBiggestTableAndComposesANormalizedTable() {
        val reader = FakeSqliteRawReader(
            schemaObjects = listOf(
                SqliteSchemaObject("customers", "table", "CREATE TABLE customers (id INTEGER, name TEXT)"),
                SqliteSchemaObject("products", "table", "CREATE TABLE products (id INTEGER, name TEXT, sku TEXT)"),
            ),
            rowCounts = mapOf("customers" to 3L, "products" to 100L),
            columnsByTable = mapOf(
                "products" to listOf(SqliteColumnMeta("id", "INTEGER"), SqliteColumnMeta("name", "TEXT"), SqliteColumnMeta("sku", "TEXT")),
            ),
            rowsByTable = mapOf(
                "products" to listOf(listOf("1", "Cola", "SKU-1"), listOf("2", "Juice", "SKU-2")),
            ),
        )
        val result = SqliteImportPipeline.run(reader, ImportLimits.DEFAULT)
        val table = (result as ImportResult.Success).value
        assertEquals(listOf("id", "name", "sku"), table.columns.map { it.rawHeader })
        assertEquals(2, table.rows.size)
    }

    @Test
    fun failedIntegrityCheckStopsThePipelineBeforeReadingAnyRows() {
        val reader = FakeSqliteRawReader(
            schemaObjects = listOf(SqliteSchemaObject("products", "table", "CREATE TABLE products (id INTEGER)")),
            rowCounts = mapOf("products" to 10L),
            columnsByTable = emptyMap(),
            rowsByTable = emptyMap(),
            integrityOk = false,
        )
        val result = SqliteImportPipeline.run(reader, ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.MalformedContent>(result.error)
    }

    @Test
    fun triggerInTheSchemaStopsThePipelineBeforeAnyTableIsRead() {
        val reader = FakeSqliteRawReader(
            schemaObjects = listOf(
                SqliteSchemaObject("products", "table", "CREATE TABLE products (id INTEGER)"),
                SqliteSchemaObject("evil_trg", "trigger", "CREATE TRIGGER evil_trg AFTER INSERT ON products BEGIN SELECT 1; END"),
            ),
            rowCounts = mapOf("products" to 10L),
            columnsByTable = emptyMap(),
            rowsByTable = emptyMap(),
        )
        val result = SqliteImportPipeline.run(reader, ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun excessiveRowCountOnTheChosenTableStopsBeforeReadingRows() {
        val limits = ImportLimits(maxSqliteRowCount = 5)
        val reader = FakeSqliteRawReader(
            schemaObjects = listOf(SqliteSchemaObject("products", "table", "CREATE TABLE products (id INTEGER)")),
            rowCounts = mapOf("products" to 1000L),
            columnsByTable = mapOf("products" to listOf(SqliteColumnMeta("id", "INTEGER"))),
            rowsByTable = mapOf("products" to (1..1000).map { listOf(it.toString()) }),
        )
        val result = SqliteImportPipeline.run(reader, limits)
        assertIs<ImportResult.Failure>(result)
        assertIs<com.actionaura.retail.importing.ImportError.LimitExceeded>(result.error)
    }
}
