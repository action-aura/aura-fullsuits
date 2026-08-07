package com.actionaura.retail.platform

import android.content.Context
import app.cash.sqldelight.db.QueryResult
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
        //
        // Task 10 (multi-device-sync-foundation) real bug found by this
        // task's own first-ever real, on-device (not androidUnitTest/JVM)
        // run of this app: `PRAGMA journal_mode=WAL` AND
        // `PRAGMA busy_timeout=30000` -- unlike the boolean-setter
        // `PRAGMA foreign_keys=ON` -- each RETURN a one-row result (the
        // resulting mode/timeout value), and Android's real
        // `SQLiteStatement.executeUpdateDelete` (what `driver.execute`
        // calls into, `AndroidSqliteDriver.kt`) throws "Queries can be
        // performed using SQLiteDatabase query or rawQuery methods only"
        // for any statement that returns a row set. Invisible in every
        // prior milestone's own testing (all against `JdbcSqliteDriver`,
        // the JVM/JDBC driver, never the real `AndroidSqliteDriver` this
        // factory actually constructs) -- confirmed live: `AuraAppContainer`'s
        // very first real construction on a real device crashed here
        // before any real screen or sync code ever ran, and crashed AGAIN
        // on `busy_timeout` after the first fix attempt only fixed
        // `journal_mode` -- fixed by using `executeQuery` (the real
        // cursor-consuming path) instead of `execute` for both.
        driver.executeQuery(null, "PRAGMA journal_mode=WAL", { QueryResult.Unit }, 0, null)
        driver.executeQuery(null, "PRAGMA busy_timeout=30000", { QueryResult.Unit }, 0, null)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        return driver
    }
}
