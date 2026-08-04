package com.actionaura.retail.importing.sqlite

/**
 * M5.8.8 -- the real boundary between platform-specific, untestable
 * SQLite I/O (`androidMain`'s `android.database.sqlite.SQLiteDatabase`,
 * opened `OPEN_READONLY`) and the pure, testable security/normalization
 * logic in `commonMain` (`SqliteSecurityPolicy`,
 * `SqliteTableComposer`). A fake implementation of this interface is
 * used in `commonTest` to prove the security policy and table
 * composition are correct without needing a real device.
 */
data class SqliteSchemaObject(val name: String, val type: String, val sql: String?)

data class SqliteColumnMeta(val name: String, val declaredType: String)

interface SqliteRawReader {
    /** Real `SELECT name, type, sql FROM sqlite_master` -- every schema object (table/view/trigger/index), including virtual tables (detectable via `sql` containing `CREATE VIRTUAL TABLE`). */
    fun listSchemaObjects(): List<SqliteSchemaObject>

    /** Real `PRAGMA table_info(name)` -- column names and declared types for one real table. */
    fun listColumns(tableName: String): List<SqliteColumnMeta>

    /** Real `SELECT COUNT(*) FROM "name"`. */
    fun countRows(tableName: String): Long

    /** Real `SELECT col1, col2, ... FROM "name" LIMIT n` -- column list is always explicit (never `SELECT *`), matching M5.8.8's own "read only allowlisted tables and columns" requirement. */
    fun readRows(tableName: String, columnNames: List<String>, limit: Long): List<List<String?>>

    /** Real `PRAGMA integrity_check`. */
    fun integrityCheckPasses(): Boolean

    fun close()
}
