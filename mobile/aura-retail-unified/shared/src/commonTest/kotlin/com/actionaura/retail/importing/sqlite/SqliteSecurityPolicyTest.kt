package com.actionaura.retail.importing.sqlite

import com.actionaura.retail.importing.ImportLimits
import com.actionaura.retail.importing.ImportResult
import kotlin.test.Test
import kotlin.test.assertIs
import kotlin.test.assertNull

private val VALID_SCHEMA = listOf(
    SqliteSchemaObject("products", "table", "CREATE TABLE products (id INTEGER, name TEXT)"),
    SqliteSchemaObject("products_idx", "index", "CREATE INDEX products_idx ON products(name)"),
)

class SqliteSecurityPolicyTest {

    @Test
    fun aRealValidPlainSchemaIsAccepted() {
        val result = SqliteSecurityPolicy.checkSchema(VALID_SCHEMA, ImportLimits.DEFAULT)
        assertIs<ImportResult.Success<Unit>>(result)
    }

    @Test
    fun anyTriggerRejectsTheWholeFile() {
        val schema = VALID_SCHEMA + SqliteSchemaObject("products_trg", "trigger", "CREATE TRIGGER products_trg AFTER INSERT ON products BEGIN SELECT 1; END")
        val result = SqliteSecurityPolicy.checkSchema(schema, ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun aVirtualTableIsRejected() {
        val schema = VALID_SCHEMA + SqliteSchemaObject("search_fts", "table", "CREATE VIRTUAL TABLE search_fts USING fts5(content)")
        val result = SqliteSecurityPolicy.checkSchema(schema, ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun aPlainViewIsAllowedSinceItHasNoSideEffects() {
        val schema = VALID_SCHEMA + SqliteSchemaObject("products_view", "view", "CREATE VIEW products_view AS SELECT * FROM products")
        val result = SqliteSecurityPolicy.checkSchema(schema, ImportLimits.DEFAULT)
        assertIs<ImportResult.Success<Unit>>(result)
    }

    @Test
    fun excessiveSchemaObjectCountIsRejected() {
        val limits = ImportLimits(maxSqliteTableCount = 1)
        val result = SqliteSecurityPolicy.checkSchema(VALID_SCHEMA, limits)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun aSchemaWithNoRealTablesIsRejected() {
        val schema = listOf(SqliteSchemaObject("some_view", "view", "CREATE VIEW some_view AS SELECT 1"))
        val result = SqliteSecurityPolicy.checkSchema(schema, ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun sqliteInternalTablesAreExcludedFromTheRealTableCount() {
        val schema = listOf(SqliteSchemaObject("sqlite_sequence", "table", "CREATE TABLE sqlite_sequence(name,seq)"))
        val result = SqliteSecurityPolicy.checkSchema(schema, ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result) // no REAL table besides the internal one -- must still be rejected as "no real tables"
    }

    @Test
    fun failedIntegrityCheckIsRejected() {
        val result = SqliteSecurityPolicy.checkIntegrity(false)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun passedIntegrityCheckIsAccepted() {
        val result = SqliteSecurityPolicy.checkIntegrity(true)
        assertIs<ImportResult.Success<Unit>>(result)
    }

    @Test
    fun excessiveRowCountIsRejected() {
        val result = SqliteSecurityPolicy.checkRowCount(200_000, ImportLimits.DEFAULT)
        assertIs<ImportResult.Failure>(result)
    }

    @Test
    fun theTableWithTheMostRealRowsIsChosenMatchingTheLegacyAuthoritysOwnHeuristic() {
        val small = SqliteSchemaObject("customers", "table", null) to 5L
        val big = SqliteSchemaObject("products", "table", null) to 5000L
        val chosen = SqliteSecurityPolicy.chooseTargetTable(listOf(small, big))
        kotlin.test.assertEquals("products", chosen?.name)
    }

    @Test
    fun emptyTableListChoosesNothing() {
        assertNull(SqliteSecurityPolicy.chooseTargetTable(emptyList()))
    }
}
