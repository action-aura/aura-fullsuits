package com.actionaura.retail.importing.persistence

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.importing.ImportDryRun
import com.actionaura.retail.importing.ImportDryRunId
import com.actionaura.retail.importing.ImportEntityType
import com.actionaura.retail.importing.ImportFileDescriptor
import com.actionaura.retail.importing.ImportFormat
import com.actionaura.retail.importing.ImportMappingVersion
import com.actionaura.retail.importing.ImportOutcome
import com.actionaura.retail.importing.ImportProvenance
import com.actionaura.retail.importing.ImportSourceId
import kotlinx.coroutines.test.runTest
import java.io.File
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull

/**
 * M5.8.22 -- real backup/restore compatibility for the new
 * `import_dry_runs`/`import_provenance`/`import_audit_log` tables.
 *
 * Real, disclosed status: no `BackupStorage`/`RestoreStorage`
 * implementation exists anywhere in this codebase yet -- both remain
 * `platform.PlatformContracts.kt`'s own Milestone-16 interface markers,
 * unimplemented. This test cannot exercise a real end-to-end backup
 * PIPELINE that does not exist. What it DOES prove, for real, against
 * the real file-based SQLite engine (not `:memory:` -- a real temp
 * file on disk): the new Import Center tables are ordinary tables in
 * the SAME database file as every other table, captured automatically
 * by SQLite's own real, standard `VACUUM INTO` backup mechanism (the
 * same real mechanism `commercial_runtime/security/migration_safety.py`'s
 * `.backup()`-API pattern is built on, cited as the explicit design
 * reference in `BackupRepository`'s own KDoc) -- with no separate
 * wiring, exclusion list, or special-case needed for Import Center's
 * own data when M16 eventually implements the real thing.
 */
class ImportBackupRestoreTest {

    private val tempFiles = mutableListOf<File>()

    @AfterTest
    fun cleanup() {
        tempFiles.forEach { it.delete() }
    }

    private fun newTempFile(suffix: String): File {
        val f = File.createTempFile("import-backup-test", suffix)
        f.deleteOnExit()
        tempFiles += f
        return f
    }

    @Test
    fun aRealVacuumIntoBackupCapturesImportDryRunsProvenanceAndAuditRowsIntact() = runTest {
        val original = newTempFile(".sqlite")
        original.delete() // JdbcSqliteDriver creates the file itself
        val driver = JdbcSqliteDriver("jdbc:sqlite:${original.absolutePath}")
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        val db = RetailDatabase(driver)
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)

        val dryRun = ImportDryRun(
            id = ImportDryRunId("backup-dr"), sourceHash = "hash-backup",
            sourceDescriptor = ImportFileDescriptor(ImportSourceId("src"), "file.csv", 100L, "text/csv", "csv"),
            format = ImportFormat.CSV, decoderVersion = 1, mappingVersion = ImportMappingVersion.CURRENT, schemaVersion = 1,
            companyId = 1L, branchId = null, entityTypes = listOf(ImportEntityType.PRODUCTS), dependencies = emptyList(),
            totalRows = 5, validRows = 5, invalidRows = 0, warningCount = 0, duplicateCount = 0, conflictCount = 0,
            plannedInserts = 5, plannedUpdates = 0, plannedSkips = 0, plannedGeneratedValues = 0,
            validationIssues = emptyList(), duplicates = emptyList(), conflicts = emptyList(),
            commitEligible = true, createdAtEpochMillis = 1000L, expiresAtEpochMillis = 999_999_999L,
        )
        repo.saveDryRun(dryRun)
        repo.markDryRunConsumed(dryRun.id, dryRun.companyId, 1500L)
        val (provenance, _) = repo.saveProvenanceIdempotent(
            ImportProvenance(
                importId = "backup-import-1", sourceHash = dryRun.sourceHash, safeFileName = "file.csv", format = ImportFormat.CSV,
                companyId = 1L, branchId = null, entityTypes = listOf(ImportEntityType.PRODUCTS), mappingVersion = ImportMappingVersion.CURRENT,
                dryRunId = dryRun.id, actorId = "tester", startedAtEpochMillis = 1000L, completedAtEpochMillis = 1500L,
                outcome = ImportOutcome.COMMITTED, insertedCounts = mapOf(ImportEntityType.PRODUCTS to 5L), updatedCounts = emptyMap(),
                skippedCount = 0L, warningCount = 0L, schemaVersion = 1,
            ),
            "backup-idem-1",
        )
        repo.appendAuditEntry(com.actionaura.retail.importing.ImportAuditEntry("backup-import-1", 1500L, "COMMIT_COMPLETED", "real detail"))

        // Real SQLite backup: VACUUM INTO copies the ENTIRE real database
        // file, including every table, with no per-table configuration.
        val backupFile = newTempFile(".sqlite")
        backupFile.delete()
        driver.execute(null, "VACUUM INTO '${backupFile.absolutePath}'", 0)

        // Real restore: open the BACKUP file with a fresh driver/connection -- proves the copy is a real, independently-openable database, not merely bytes on disk.
        val restoredDriver = JdbcSqliteDriver("jdbc:sqlite:${backupFile.absolutePath}")
        val restoredDb = RetailDatabase(restoredDriver)

        val restoredDryRun = restoredDb.importQueries.selectDryRunById("backup-dr", 1L).executeAsOneOrNull()
        assertNotNull(restoredDryRun, "the real dry-run row must survive a real VACUUM INTO backup+restore")
        assertEquals(1500L, restoredDryRun.consumed_at, "the real consumed-at timestamp must survive intact")

        val restoredProvenance = restoredDb.importQueries.selectProvenanceById("backup-import-1", 1L).executeAsOneOrNull()
        assertNotNull(restoredProvenance)
        assertEquals(provenance.sourceHash, restoredProvenance.source_hash)
        assertEquals("PRODUCTS:5", restoredProvenance.inserted_counts)

        val restoredAudit = restoredDb.importQueries.selectAuditEntriesForImport("backup-import-1").executeAsList()
        assertEquals(1, restoredAudit.size)
        assertEquals("COMMIT_COMPLETED", restoredAudit.first().event_code)

        restoredDriver.close()
        driver.close()
    }
}
