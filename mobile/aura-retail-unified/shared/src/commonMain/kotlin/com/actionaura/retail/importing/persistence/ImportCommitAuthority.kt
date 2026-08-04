package com.actionaura.retail.importing.persistence

import com.actionaura.retail.importing.ImportCommitToken
import com.actionaura.retail.importing.ImportDryRun
import com.actionaura.retail.importing.ImportError
import com.actionaura.retail.importing.ImportResult

/** M5.8.14 -- issues a bounded commit token tied exactly to one real dry-run's identity. */
object ImportCommitTokenFactory {
    fun issue(dryRun: ImportDryRun, idempotencyKey: String, nonce: String, nowEpochMillis: Long, tokenLifetimeMillis: Long): ImportCommitToken =
        ImportCommitToken(
            dryRunId = dryRun.id,
            sourceHash = dryRun.sourceHash,
            mappingVersion = dryRun.mappingVersion,
            schemaVersion = dryRun.schemaVersion,
            companyId = dryRun.companyId,
            branchId = dryRun.branchId,
            idempotencyKey = idempotencyKey,
            nonce = nonce,
            issuedAtEpochMillis = nowEpochMillis,
            expiresAtEpochMillis = nowEpochMillis + tokenLifetimeMillis,
        )
}

/**
 * M5.8.14 -- real commit-time revalidation. Never trusts a client-
 * submitted planned-insert count or any other cached number -- the
 * dry-run is re-read from durable storage and checked against the
 * token's own claimed identity field by field. A client cannot widen
 * scope (e.g. claim a different `companyId`) by anything it submits in
 * the token, since every check below compares the token's fields
 * against the REAL, independently stored dry-run, never the other way
 * around.
 */
object ImportCommitRevalidator {

    suspend fun revalidate(token: ImportCommitToken, persistence: ImportPersistenceRepository, nowEpochMillis: Long): ImportResult<ImportDryRun> {
        val dryRun = persistence.getDryRun(token.dryRunId, token.companyId)
            ?: return ImportResult.Failure(ImportError.DryRunNotFound(token.dryRunId))

        if (dryRun.consumedAtEpochMillis != null) {
            return ImportResult.Failure(ImportError.CommitConflict("dry-run ${token.dryRunId.value} was already committed at ${dryRun.consumedAtEpochMillis}"))
        }
        if (nowEpochMillis >= token.expiresAtEpochMillis || nowEpochMillis >= dryRun.expiresAtEpochMillis) {
            return ImportResult.Failure(ImportError.DryRunExpired(token.dryRunId))
        }
        if (dryRun.sourceHash != token.sourceHash) {
            return ImportResult.Failure(ImportError.SourceMismatch("source hash changed since the dry-run was issued -- the file was modified"))
        }
        if (dryRun.mappingVersion != token.mappingVersion || dryRun.schemaVersion != token.schemaVersion) {
            return ImportResult.Failure(ImportError.SchemaMismatch("mapping or schema version changed since the dry-run was issued"))
        }
        if (dryRun.companyId != token.companyId) {
            return ImportResult.Failure(ImportError.AccessDenied("token company does not match the real dry-run's own company"))
        }
        if (dryRun.branchId != token.branchId) {
            return ImportResult.Failure(ImportError.SourceMismatch("Branch scope changed since the dry-run was issued"))
        }
        if (!dryRun.commitEligible) {
            return ImportResult.Failure(ImportError.CommitConflict("dry-run ${token.dryRunId.value} was never eligible for commit"))
        }
        return ImportResult.Success(dryRun)
    }
}
