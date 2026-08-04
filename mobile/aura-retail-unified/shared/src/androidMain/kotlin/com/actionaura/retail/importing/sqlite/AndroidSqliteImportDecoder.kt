package com.actionaura.retail.importing.sqlite

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import com.actionaura.retail.importing.ImportDecoder
import com.actionaura.retail.importing.ImportError
import com.actionaura.retail.importing.ImportFormat
import com.actionaura.retail.importing.ImportLimits
import com.actionaura.retail.importing.ImportResult
import com.actionaura.retail.importing.ImportSource
import com.actionaura.retail.importing.NormalizedTable
import java.io.File
import java.util.UUID

/**
 * M5.8.8 -- the real `androidMain` SQLite decoder. `SQLiteDatabase`
 * requires a real file path (it cannot open an in-memory byte buffer
 * directly for an arbitrary foreign file), so the bounded, already
 * size-checked bytes are written to a real temp file in the app's own
 * private cache directory, opened `OPEN_READONLY`, and the temp file is
 * always deleted afterward -- including on every failure path.
 *
 * Real, disclosed limitation: cannot be exercised by a unit test on
 * this host (real `android.content.Context`/`SQLiteDatabase`, no
 * Robolectric/device). The real decision pipeline it delegates to
 * (`SqliteImportPipeline`) is proven via a fake reader in `commonTest`.
 */
class AndroidSqliteImportDecoder(private val context: Context) : ImportDecoder {
    override val format: ImportFormat = ImportFormat.SQLITE

    override suspend fun decode(source: ImportSource, limits: ImportLimits): ImportResult<NormalizedTable> {
        val bytesResult = source.readBounded(limits)
        val bytes = when (bytesResult) {
            is ImportResult.Success -> bytesResult.value
            is ImportResult.Failure -> return bytesResult
        }

        val tempFile = File(context.cacheDir, "import-${UUID.randomUUID()}.sqlite")
        try {
            tempFile.writeBytes(bytes)
            val db = try {
                SQLiteDatabase.openDatabase(tempFile.absolutePath, null, SQLiteDatabase.OPEN_READONLY)
            } catch (e: Exception) {
                return ImportResult.Failure(ImportError.MalformedContent("not a real SQLite database: ${e.message}"))
            }
            val reader = AndroidSqliteRawReader(db)
            try {
                return SqliteImportPipeline.run(reader, limits)
            } finally {
                reader.close()
            }
        } finally {
            tempFile.delete()
        }
    }
}
