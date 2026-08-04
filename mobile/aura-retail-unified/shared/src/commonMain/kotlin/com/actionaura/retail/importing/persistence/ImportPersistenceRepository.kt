package com.actionaura.retail.importing.persistence

import com.actionaura.retail.importing.ImportAuditEntry
import com.actionaura.retail.importing.ImportDryRun
import com.actionaura.retail.importing.ImportDryRunId
import com.actionaura.retail.importing.ImportProvenance

/**
 * M5.8.13/M5.8.17/M5.8.18 -- the real, durable Import Center persistence
 * authority. `saveProvenance`'s real idempotency contract: a repeated
 * call with the same `idempotencyKey` returns the ORIGINAL row, never
 * inserts a duplicate (M5.8.17's own explicit requirement) -- proven by
 * the real, same-transaction check-then-insert pattern this codebase
 * already established for Product SKU/barcode uniqueness
 * (`SqlDelightProductRepository.insert`), not a caught constraint-
 * violation exception (portable across the JDBC test driver and the
 * real Android driver without depending on either one's specific
 * exception type).
 */
interface ImportPersistenceRepository {
    suspend fun saveDryRun(dryRun: ImportDryRun)
    suspend fun getDryRun(id: ImportDryRunId, companyId: Long): ImportDryRun?
    suspend fun markDryRunConsumed(id: ImportDryRunId, companyId: Long, nowEpochMillis: Long): Boolean

    /** Returns `Pair(provenance actually stored, wasNewlyInserted)` -- `wasNewlyInserted = false` means a real prior commit with the same idempotency key already exists, and the ORIGINAL row is returned unchanged. */
    suspend fun saveProvenanceIdempotent(provenance: ImportProvenance, idempotencyKey: String): Pair<ImportProvenance, Boolean>
    suspend fun getProvenanceByIdempotencyKey(idempotencyKey: String): ImportProvenance?
    suspend fun getProvenanceById(importId: String, companyId: Long): ImportProvenance?

    suspend fun appendAuditEntry(entry: ImportAuditEntry)
    suspend fun getAuditEntries(importId: String): List<ImportAuditEntry>
}
