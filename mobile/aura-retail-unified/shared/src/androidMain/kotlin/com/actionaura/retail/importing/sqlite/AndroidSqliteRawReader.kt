package com.actionaura.retail.importing.sqlite

import android.database.sqlite.SQLiteDatabase

/**
 * M5.8.8 -- the real, `androidMain`-scoped `SqliteRawReader`
 * implementation, backed by the real Android platform's own
 * `android.database.sqlite.SQLiteDatabase` opened `OPEN_READONLY`
 * against a real temp-file copy of the untrusted uploaded bytes
 * (`AndroidSqliteImportDecoder` owns that temp file's lifecycle).
 * Every query here is bounded and explicit -- no SQL text from the
 * untrusted file is ever executed, only real, hardcoded
 * metadata/`PRAGMA` queries and explicit-column `SELECT`s this codebase
 * constructs itself.
 *
 * Real, disclosed limitation (`import-parser-decision.md`): this class
 * cannot be exercised by a unit test on this host (no Robolectric, no
 * device/emulator) -- the real decision pipeline it feeds
 * (`SqliteImportPipeline`) is proven instead via a fake implementation
 * of `SqliteRawReader` in `commonTest`.
 */
class AndroidSqliteRawReader(private val db: SQLiteDatabase) : SqliteRawReader {

    override fun listSchemaObjects(): List<SqliteSchemaObject> {
        val result = mutableListOf<SqliteSchemaObject>()
        db.rawQuery("SELECT name, type, sql FROM sqlite_master", null).use { cursor ->
            while (cursor.moveToNext()) {
                result += SqliteSchemaObject(
                    name = cursor.getString(0),
                    type = cursor.getString(1),
                    sql = if (cursor.isNull(2)) null else cursor.getString(2),
                )
            }
        }
        return result
    }

    override fun listColumns(tableName: String): List<SqliteColumnMeta> {
        val result = mutableListOf<SqliteColumnMeta>()
        db.rawQuery("PRAGMA table_info(\"${escapeIdentifier(tableName)}\")", null).use { cursor ->
            val nameIdx = cursor.getColumnIndexOrThrow("name")
            val typeIdx = cursor.getColumnIndexOrThrow("type")
            while (cursor.moveToNext()) {
                result += SqliteColumnMeta(cursor.getString(nameIdx), cursor.getString(typeIdx) ?: "")
            }
        }
        return result
    }

    override fun countRows(tableName: String): Long {
        db.rawQuery("SELECT COUNT(*) FROM \"${escapeIdentifier(tableName)}\"", null).use { cursor ->
            return if (cursor.moveToFirst()) cursor.getLong(0) else 0L
        }
    }

    override fun readRows(tableName: String, columnNames: List<String>, limit: Long): List<List<String?>> {
        if (columnNames.isEmpty()) return emptyList()
        val columnList = columnNames.joinToString(", ") { "\"${escapeIdentifier(it)}\"" }
        val result = mutableListOf<List<String?>>()
        db.rawQuery("SELECT $columnList FROM \"${escapeIdentifier(tableName)}\" LIMIT ?", arrayOf(limit.toString())).use { cursor ->
            while (cursor.moveToNext()) {
                result += (0 until cursor.columnCount).map { i -> if (cursor.isNull(i)) null else cursor.getString(i) }
            }
        }
        return result
    }

    override fun integrityCheckPasses(): Boolean {
        db.rawQuery("PRAGMA integrity_check", null).use { cursor ->
            return cursor.moveToFirst() && cursor.getString(0).equals("ok", ignoreCase = true)
        }
    }

    override fun close() {
        db.close()
    }

    /** Real defense against identifier injection via a crafted table/column name (SQLite identifiers cannot themselves be parameterized) -- a literal `"` is doubled per SQL identifier-quoting rules, never allowed to close the quote early. */
    private fun escapeIdentifier(identifier: String): String = identifier.replace("\"", "\"\"")
}
