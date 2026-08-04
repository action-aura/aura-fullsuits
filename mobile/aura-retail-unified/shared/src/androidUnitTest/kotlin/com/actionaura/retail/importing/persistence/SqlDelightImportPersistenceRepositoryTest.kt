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
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

class SqlDelightImportPersistenceRepositoryTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    private fun realDryRun(id: String = "dry-run-1", companyId: Long = 1L) = ImportDryRun(
        id = ImportDryRunId(id),
        sourceHash = "hash-abc",
        sourceDescriptor = ImportFileDescriptor(ImportSourceId("src-1"), "products.csv", 1000L, "text/csv", "csv"),
        format = ImportFormat.CSV,
        decoderVersion = 1,
        mappingVersion = ImportMappingVersion.CURRENT,
        schemaVersion = 1,
        companyId = companyId,
        branchId = null,
        entityTypes = listOf(ImportEntityType.PRODUCTS, ImportEntityType.CATEGORIES),
        dependencies = emptyList(),
        totalRows = 10, validRows = 9, invalidRows = 1,
        warningCount = 2, duplicateCount = 1, conflictCount = 0,
        plannedInserts = 8, plannedUpdates = 1, plannedSkips = 1, plannedGeneratedValues = 0,
        validationIssues = emptyList(), duplicates = emptyList(), conflicts = emptyList(),
        commitEligible = true,
        createdAtEpochMillis = 1000L, expiresAtEpochMillis = 2000L,
    )

    @Test
    fun realDryRunRoundTripsExactly() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        val dryRun = realDryRun()

        repo.saveDryRun(dryRun)
        val loaded = repo.getDryRun(dryRun.id, dryRun.companyId)

        assertEquals(dryRun.sourceHash, loaded?.sourceHash)
        assertEquals(dryRun.entityTypes, loaded?.entityTypes)
        assertEquals(dryRun.totalRows, loaded?.totalRows)
        assertEquals(dryRun.commitEligible, loaded?.commitEligible)
        assertNull(loaded?.consumedAtEpochMillis, "a freshly saved dry-run must never already be consumed")
    }

    @Test
    fun aDryRunFromAnotherCompanyIsNeverReturned() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        repo.saveDryRun(realDryRun(companyId = 1L))

        val loaded = repo.getDryRun(ImportDryRunId("dry-run-1"), companyId = 2L)
        assertNull(loaded, "cross-business access to a dry-run must never succeed")
    }

    @Test
    fun markingADryRunConsumedIsRealAndPersists() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        val dryRun = realDryRun()
        repo.saveDryRun(dryRun)

        val marked = repo.markDryRunConsumed(dryRun.id, dryRun.companyId, 1500L)
        assertTrue(marked)

        val reloaded = repo.getDryRun(dryRun.id, dryRun.companyId)
        assertEquals(1500L, reloaded?.consumedAtEpochMillis)
    }

    @Test
    fun consumingAnAlreadyConsumedDryRunFailsRealAndIdempotently() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        val dryRun = realDryRun()
        repo.saveDryRun(dryRun)
        repo.markDryRunConsumed(dryRun.id, dryRun.companyId, 1500L)

        val secondAttempt = repo.markDryRunConsumed(dryRun.id, dryRun.companyId, 1600L)
        assertFalse(secondAttempt, "a second consume attempt on an already-consumed dry-run must report failure, not silently succeed again")

        val reloaded = repo.getDryRun(dryRun.id, dryRun.companyId)
        assertEquals(1500L, reloaded?.consumedAtEpochMillis, "the original consumption timestamp must never be overwritten by a later attempt")
    }

    private fun realProvenance(importId: String = "import-1", companyId: Long = 1L) = ImportProvenance(
        importId = importId,
        sourceHash = "hash-abc",
        safeFileName = "products.csv",
        format = ImportFormat.CSV,
        companyId = companyId,
        branchId = null,
        entityTypes = listOf(ImportEntityType.PRODUCTS),
        mappingVersion = ImportMappingVersion.CURRENT,
        dryRunId = ImportDryRunId("dry-run-1"),
        actorId = "tester",
        startedAtEpochMillis = 1000L,
        completedAtEpochMillis = 1100L,
        outcome = ImportOutcome.COMMITTED,
        insertedCounts = mapOf(ImportEntityType.PRODUCTS to 8L),
        updatedCounts = mapOf(ImportEntityType.PRODUCTS to 1L),
        skippedCount = 1L,
        warningCount = 2L,
        schemaVersion = 1,
    )

    @Test
    fun realProvenanceRoundTripsExactly() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        repo.saveDryRun(realDryRun())
        val provenance = realProvenance()

        val (stored, wasNew) = repo.saveProvenanceIdempotent(provenance, "idem-key-1")
        assertTrue(wasNew)
        assertEquals(provenance.insertedCounts, stored.insertedCounts)
        assertEquals(provenance.updatedCounts, stored.updatedCounts)

        val loaded = repo.getProvenanceById(provenance.importId, provenance.companyId)
        assertEquals(mapOf(ImportEntityType.PRODUCTS to 8L), loaded?.insertedCounts)
    }

    @Test
    fun repeatedCommitWithTheSameIdempotencyKeyReturnsTheOriginalRowNeverADuplicate() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        repo.saveDryRun(realDryRun())
        val first = realProvenance(importId = "import-1")
        val second = realProvenance(importId = "import-2") // a different real importId, but the SAME idempotency key

        val (storedFirst, wasNewFirst) = repo.saveProvenanceIdempotent(first, "idem-key-1")
        val (storedSecond, wasNewSecond) = repo.saveProvenanceIdempotent(second, "idem-key-1")

        assertTrue(wasNewFirst)
        assertFalse(wasNewSecond, "a repeated commit with the same idempotency key must never insert a second row")
        assertEquals(storedFirst.importId, storedSecond.importId, "the SECOND call must return the ORIGINAL row's real identity, not its own")
    }

    @Test
    fun auditEntriesAreRealAndOrderedByTimestamp() = runTest {
        val db = newDb()
        val gate = DatabaseWriteGate()
        val repo = SqlDelightImportPersistenceRepository(db, gate)
        repo.appendAuditEntry(com.actionaura.retail.importing.ImportAuditEntry("import-1", 2000L, "COMMIT_STARTED", "real detail"))
        repo.appendAuditEntry(com.actionaura.retail.importing.ImportAuditEntry("import-1", 1000L, "DRY_RUN_ISSUED", "real detail"))

        val entries = repo.getAuditEntries("import-1")
        assertEquals(listOf("DRY_RUN_ISSUED", "COMMIT_STARTED"), entries.map { it.eventCode })
    }
}
