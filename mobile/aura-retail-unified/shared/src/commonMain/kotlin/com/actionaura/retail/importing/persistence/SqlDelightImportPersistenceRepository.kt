package com.actionaura.retail.importing.persistence

import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.importing.ImportAuditEntry
import com.actionaura.retail.importing.ImportDryRun
import com.actionaura.retail.importing.ImportDryRunId
import com.actionaura.retail.importing.ImportProvenance
import kotlinx.coroutines.sync.withLock

/** Thin, locked wrapper -- every real query lives once in `ImportPersistenceCore`, reused (without a lock) by `ImportCommitExecutor`. */
class SqlDelightImportPersistenceRepository(
    private val db: RetailDatabase,
    private val gate: DatabaseWriteGate,
) : ImportPersistenceRepository {

    override suspend fun saveDryRun(dryRun: ImportDryRun) = gate.mutex.withLock {
        ImportPersistenceCore.saveDryRun(db, dryRun)
    }

    override suspend fun getDryRun(id: ImportDryRunId, companyId: Long): ImportDryRun? = gate.mutex.withLock {
        ImportPersistenceCore.getDryRun(db, id, companyId)
    }

    override suspend fun markDryRunConsumed(id: ImportDryRunId, companyId: Long, nowEpochMillis: Long): Boolean = gate.mutex.withLock {
        ImportPersistenceCore.markDryRunConsumed(db, id, companyId, nowEpochMillis)
    }

    override suspend fun saveProvenanceIdempotent(provenance: ImportProvenance, idempotencyKey: String): Pair<ImportProvenance, Boolean> = gate.mutex.withLock {
        db.transactionWithResult {
            ImportPersistenceCore.saveProvenanceIdempotent(db, provenance, idempotencyKey)
        }
    }

    override suspend fun getProvenanceByIdempotencyKey(idempotencyKey: String): ImportProvenance? = gate.mutex.withLock {
        ImportPersistenceCore.getProvenanceByIdempotencyKey(db, idempotencyKey)
    }

    override suspend fun getProvenanceById(importId: String, companyId: Long): ImportProvenance? = gate.mutex.withLock {
        ImportPersistenceCore.getProvenanceById(db, importId, companyId)
    }

    override suspend fun appendAuditEntry(entry: ImportAuditEntry) = gate.mutex.withLock {
        ImportPersistenceCore.appendAuditEntry(db, entry)
    }

    override suspend fun getAuditEntries(importId: String): List<ImportAuditEntry> = gate.mutex.withLock {
        ImportPersistenceCore.getAuditEntries(db, importId)
    }
}
