package com.actionaura.retail.importing.persistence

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.importing.ImportDryRun
import com.actionaura.retail.importing.ImportDryRunId
import com.actionaura.retail.importing.ImportEntityType
import com.actionaura.retail.importing.ImportError
import com.actionaura.retail.importing.ImportFileDescriptor
import com.actionaura.retail.importing.ImportFormat
import com.actionaura.retail.importing.ImportMappingVersion
import com.actionaura.retail.importing.ImportResult
import com.actionaura.retail.importing.ImportSourceId
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertIs

class ImportCommitRevalidatorTest {

    private fun newRepo(): ImportPersistenceRepository {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return SqlDelightImportPersistenceRepository(RetailDatabase(driver), DatabaseWriteGate())
    }

    private fun realDryRun(
        id: String = "dry-run-1", companyId: Long = 1L, branchId: Long? = null,
        sourceHash: String = "hash-abc", schemaVersion: Int = 1,
        commitEligible: Boolean = true, expiresAtEpochMillis: Long = 5000L,
    ) = ImportDryRun(
        id = ImportDryRunId(id), sourceHash = sourceHash,
        sourceDescriptor = ImportFileDescriptor(ImportSourceId("src"), "products.csv", 1000L, "text/csv", "csv"),
        format = ImportFormat.CSV, decoderVersion = 1, mappingVersion = ImportMappingVersion.CURRENT,
        schemaVersion = schemaVersion, companyId = companyId, branchId = branchId,
        entityTypes = listOf(ImportEntityType.PRODUCTS), dependencies = emptyList(),
        totalRows = 10, validRows = 9, invalidRows = 1, warningCount = 0, duplicateCount = 0, conflictCount = 0,
        plannedInserts = 9, plannedUpdates = 0, plannedSkips = 1, plannedGeneratedValues = 0,
        validationIssues = emptyList(), duplicates = emptyList(), conflicts = emptyList(),
        commitEligible = commitEligible, createdAtEpochMillis = 1000L, expiresAtEpochMillis = expiresAtEpochMillis,
    )

    @Test
    fun aRealMatchingTokenRevalidatesSuccessfully() = runTest {
        val repo = newRepo()
        val dryRun = realDryRun()
        repo.saveDryRun(dryRun)
        val token = ImportCommitTokenFactory.issue(dryRun, "idem-1", "nonce-1", 1200L, 10_000L)

        val result = ImportCommitRevalidator.revalidate(token, repo, nowEpochMillis = 1300L)
        assertIs<ImportResult.Success<ImportDryRun>>(result)
    }

    @Test
    fun aMissingDryRunFailsWithDryRunNotFound() = runTest {
        val repo = newRepo()
        val dryRun = realDryRun()
        val token = ImportCommitTokenFactory.issue(dryRun, "idem-1", "nonce-1", 1200L, 10_000L)

        val result = ImportCommitRevalidator.revalidate(token, repo, nowEpochMillis = 1300L)
        assertIs<ImportResult.Failure>(result)
        assertIs<ImportError.DryRunNotFound>(result.error)
    }

    @Test
    fun anAlreadyConsumedDryRunFailsWithCommitConflict() = runTest {
        val repo = newRepo()
        val dryRun = realDryRun()
        repo.saveDryRun(dryRun)
        repo.markDryRunConsumed(dryRun.id, dryRun.companyId, 1250L)
        val token = ImportCommitTokenFactory.issue(dryRun, "idem-1", "nonce-1", 1200L, 10_000L)

        val result = ImportCommitRevalidator.revalidate(token, repo, nowEpochMillis = 1300L)
        assertIs<ImportResult.Failure>(result)
        assertIs<ImportError.CommitConflict>(result.error)
    }

    @Test
    fun anExpiredDryRunFailsWithDryRunExpired() = runTest {
        val repo = newRepo()
        val dryRun = realDryRun(expiresAtEpochMillis = 2000L)
        repo.saveDryRun(dryRun)
        val token = ImportCommitTokenFactory.issue(dryRun, "idem-1", "nonce-1", 1200L, 10_000L)

        val result = ImportCommitRevalidator.revalidate(token, repo, nowEpochMillis = 3000L)
        assertIs<ImportResult.Failure>(result)
        assertIs<ImportError.DryRunExpired>(result.error)
    }

    @Test
    fun aChangedSourceFileFailsWithSourceMismatch() = runTest {
        val repo = newRepo()
        val dryRun = realDryRun(sourceHash = "hash-original")
        repo.saveDryRun(dryRun)
        // Real, deliberate token/dry-run mismatch: simulates the file having changed between preview and commit.
        val staleToken = ImportCommitTokenFactory.issue(dryRun, "idem-1", "nonce-1", 1200L, 10_000L).copy(sourceHash = "hash-modified")

        val result = ImportCommitRevalidator.revalidate(staleToken, repo, nowEpochMillis = 1300L)
        assertIs<ImportResult.Failure>(result)
        assertIs<ImportError.SourceMismatch>(result.error)
    }

    @Test
    fun aSchemaVersionMismatchFailsWithSchemaMismatch() = runTest {
        val repo = newRepo()
        val dryRun = realDryRun(schemaVersion = 1)
        repo.saveDryRun(dryRun)
        val staleToken = ImportCommitTokenFactory.issue(dryRun, "idem-1", "nonce-1", 1200L, 10_000L).copy(schemaVersion = 2)

        val result = ImportCommitRevalidator.revalidate(staleToken, repo, nowEpochMillis = 1300L)
        assertIs<ImportResult.Failure>(result)
        assertIs<ImportError.SchemaMismatch>(result.error)
    }

    @Test
    fun aBranchChangeFailsWithSourceMismatch() = runTest {
        val repo = newRepo()
        val dryRun = realDryRun(branchId = 10L)
        repo.saveDryRun(dryRun)
        val staleToken = ImportCommitTokenFactory.issue(dryRun, "idem-1", "nonce-1", 1200L, 10_000L).copy(branchId = 20L)

        val result = ImportCommitRevalidator.revalidate(staleToken, repo, nowEpochMillis = 1300L)
        assertIs<ImportResult.Failure>(result)
        assertIs<ImportError.SourceMismatch>(result.error)
    }

    @Test
    fun aTokenClaimingADifferentCompanyThanTheRealDryRunNeverSucceeds() = runTest {
        val repo = newRepo()
        val dryRun = realDryRun(companyId = 1L)
        repo.saveDryRun(dryRun)
        // A real attacker/bug scenario: the token claims companyId=1 (matching what it will look up with),
        // but the ACTUAL stored dry-run's real companyId must independently match too -- getDryRun itself
        // already scopes by companyId, so a token for a different company simply finds nothing.
        val foreignToken = ImportCommitTokenFactory.issue(dryRun.copy(companyId = 2L), "idem-1", "nonce-1", 1200L, 10_000L)

        val result = ImportCommitRevalidator.revalidate(foreignToken, repo, nowEpochMillis = 1300L)
        assertIs<ImportResult.Failure>(result)
        assertIs<ImportError.DryRunNotFound>(result.error, "a cross-company token must never resolve to a real dry-run at all, not merely be rejected after the fact")
    }

    @Test
    fun aDryRunThatWasNeverCommitEligibleFailsWithCommitConflict() = runTest {
        val repo = newRepo()
        val dryRun = realDryRun(commitEligible = false)
        repo.saveDryRun(dryRun)
        val token = ImportCommitTokenFactory.issue(dryRun, "idem-1", "nonce-1", 1200L, 10_000L)

        val result = ImportCommitRevalidator.revalidate(token, repo, nowEpochMillis = 1300L)
        assertIs<ImportResult.Failure>(result)
        assertIs<ImportError.CommitConflict>(result.error)
    }
}
