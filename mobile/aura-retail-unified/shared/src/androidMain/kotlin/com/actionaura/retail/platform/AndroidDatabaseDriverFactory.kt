package com.actionaura.retail.platform

import android.content.Context
import app.cash.sqldelight.db.SqlDriver
import app.cash.sqldelight.driver.android.AndroidSqliteDriver
import com.actionaura.retail.db.RetailDatabase

/**
 * M4 -- real Android SQLDelight driver. Database file lives at
 * `<filesDir>/databases/retail_unified.db` -- deliberately a DIFFERENT
 * path from the legacy Python-era database
 * (`<filesDir>/data/database/subsystems/retail.db`, android-database-
 * audit.md) so both can exist on disk simultaneously during the
 * Milestone-18 migration window; the migration importer (`data/migration/`)
 * reads the legacy file and writes into this one, never touching or
 * deleting the legacy file itself (per the governing spec's explicit
 * "retain the original database until success... never delete source data
 * automatically" requirement).
 */
class AndroidDatabaseDriverFactory(private val context: Context) : DatabaseDriverFactory {
    override fun createDriver(): SqlDriver {
        val driver = AndroidSqliteDriver(
            schema = RetailDatabase.Schema,
            context = context,
            name = "retail_unified.db",
        )
        // Real pragmas from android-database-audit.md, reproduced exactly --
        // foreign_keys is NOT persisted in the SQLite file, must be set on
        // every connection, matching the real Python authority's own
        // schema.py::_conn() comment verbatim.
        driver.execute(null, "PRAGMA journal_mode=WAL", 0)
        driver.execute(null, "PRAGMA busy_timeout=30000", 0)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        return driver
    }
}
