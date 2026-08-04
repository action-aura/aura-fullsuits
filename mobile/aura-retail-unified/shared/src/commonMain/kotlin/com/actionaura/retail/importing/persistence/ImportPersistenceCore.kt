package com.actionaura.retail.importing.persistence

import com.actionaura.retail.db.Import_dry_runs
import com.actionaura.retail.db.Import_provenance
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.importing.ImportAuditEntry
import com.actionaura.retail.importing.ImportDryRun
import com.actionaura.retail.importing.ImportDryRunId
import com.actionaura.retail.importing.ImportEntityType
import com.actionaura.retail.importing.ImportFileDescriptor
import com.actionaura.retail.importing.ImportFormat
import com.actionaura.retail.importing.ImportMappingVersion
import com.actionaura.retail.importing.ImportOutcome
import com.actionaura.retail.importing.ImportProvenance

/**
 * M5.8.15 -- plain, lock-free persistence primitives operating directly
 * on `db`. Exists so `ImportCommitExecutor` (which already holds
 * `DatabaseWriteGate.mutex` for the whole real commit transaction) can
 * write dry-run-consumed/provenance/audit rows WITHOUT going through
 * `SqlDelightImportPersistenceRepository`'s own `gate.mutex.withLock`
 * wrapper -- `Mutex` is non-reentrant (established rule, see
 * `SqlDelightCategoryRepository`'s own KDoc), so a nested acquisition
 * from inside an already-locked block would deadlock. The repository
 * itself now delegates here under its own single lock acquisition, so
 * there is exactly one real implementation of each query, never two.
 */
internal object ImportPersistenceCore {

    fun saveDryRun(db: RetailDatabase, dryRun: ImportDryRun) {
        db.importQueries.insertDryRun(
            dryRun.id.value, dryRun.companyId, dryRun.branchId, dryRun.sourceHash,
            dryRun.sourceDescriptor.displayName, dryRun.format.name, dryRun.decoderVersion.toLong(),
            dryRun.mappingVersion.value.toLong(), dryRun.schemaVersion.toLong(),
            joinEntityTypes(dryRun.entityTypes), dryRun.totalRows, dryRun.validRows, dryRun.invalidRows,
            dryRun.warningCount, dryRun.duplicateCount, dryRun.conflictCount, dryRun.plannedInserts,
            dryRun.plannedUpdates, dryRun.plannedSkips, dryRun.plannedGeneratedValues,
            if (dryRun.commitEligible) 1L else 0L, dryRun.createdAtEpochMillis, dryRun.expiresAtEpochMillis,
        )
    }

    fun getDryRun(db: RetailDatabase, id: ImportDryRunId, companyId: Long): ImportDryRun? =
        db.importQueries.selectDryRunById(id.value, companyId).executeAsOneOrNull()?.toDomain()

    fun markDryRunConsumed(db: RetailDatabase, id: ImportDryRunId, companyId: Long, nowEpochMillis: Long): Boolean {
        db.importQueries.markDryRunConsumed(nowEpochMillis, id.value, companyId)
        return db.importQueries.changes().executeAsOne() > 0L
    }

    /** Real, same-transaction check-then-insert -- see `import-idempotency-report.md`. Caller must already be inside a `db.transactionWithResult` block for this to be atomic. */
    fun saveProvenanceIdempotent(db: RetailDatabase, provenance: ImportProvenance, idempotencyKey: String): Pair<ImportProvenance, Boolean> {
        val existing = db.importQueries.selectProvenanceByIdempotencyKey(idempotencyKey).executeAsOneOrNull()
        if (existing != null) return existing.toDomain() to false
        db.importQueries.insertProvenance(
            provenance.importId, provenance.dryRunId.value, provenance.sourceHash, provenance.safeFileName,
            provenance.format.name, provenance.companyId, provenance.branchId, joinEntityTypes(provenance.entityTypes),
            provenance.mappingVersion.value.toLong(), provenance.schemaVersion.toLong(), provenance.actorId,
            provenance.startedAtEpochMillis, provenance.completedAtEpochMillis, provenance.outcome.name,
            joinCounts(provenance.insertedCounts), joinCounts(provenance.updatedCounts),
            provenance.skippedCount, provenance.warningCount, idempotencyKey,
        )
        return provenance to true
    }

    fun getProvenanceByIdempotencyKey(db: RetailDatabase, idempotencyKey: String): ImportProvenance? =
        db.importQueries.selectProvenanceByIdempotencyKey(idempotencyKey).executeAsOneOrNull()?.toDomain()

    fun getProvenanceById(db: RetailDatabase, importId: String, companyId: Long): ImportProvenance? =
        db.importQueries.selectProvenanceById(importId, companyId).executeAsOneOrNull()?.toDomain()

    fun appendAuditEntry(db: RetailDatabase, entry: ImportAuditEntry) {
        db.importQueries.insertAuditEntry(entry.importId, entry.timestampEpochMillis, entry.eventCode, entry.detail)
    }

    fun getAuditEntries(db: RetailDatabase, importId: String): List<ImportAuditEntry> =
        db.importQueries.selectAuditEntriesForImport(importId).executeAsList()
            .map { ImportAuditEntry(it.import_id, it.timestamp, it.event_code, it.detail) }

    fun joinEntityTypes(types: List<ImportEntityType>): String = types.joinToString(",") { it.name }
    fun splitEntityTypes(text: String): List<ImportEntityType> =
        if (text.isBlank()) emptyList() else text.split(",").map { ImportEntityType.valueOf(it) }

    fun joinCounts(counts: Map<ImportEntityType, Long>): String = counts.entries.joinToString(",") { "${it.key.name}:${it.value}" }
    fun splitCounts(text: String): Map<ImportEntityType, Long> =
        if (text.isBlank()) emptyMap() else text.split(",").associate { pair ->
            val (k, v) = pair.split(":")
            ImportEntityType.valueOf(k) to v.toLong()
        }

    private fun Import_dry_runs.toDomain(): ImportDryRun = ImportDryRun(
        id = ImportDryRunId(id),
        sourceHash = source_hash,
        sourceDescriptor = ImportFileDescriptor(com.actionaura.retail.importing.ImportSourceId(id), source_display_name, null, null, null),
        format = ImportFormat.valueOf(format),
        decoderVersion = decoder_version.toInt(),
        mappingVersion = ImportMappingVersion(mapping_version.toInt()),
        schemaVersion = schema_version.toInt(),
        companyId = company_id,
        branchId = branch_id,
        entityTypes = splitEntityTypes(entity_types),
        dependencies = emptyList(),
        totalRows = total_rows,
        validRows = valid_rows,
        invalidRows = invalid_rows,
        warningCount = warning_count,
        duplicateCount = duplicate_count,
        conflictCount = conflict_count,
        plannedInserts = planned_inserts,
        plannedUpdates = planned_updates,
        plannedSkips = planned_skips,
        plannedGeneratedValues = planned_generated_values,
        validationIssues = emptyList(),
        duplicates = emptyList(),
        conflicts = emptyList(),
        commitEligible = commit_eligible != 0L,
        createdAtEpochMillis = created_at,
        expiresAtEpochMillis = expires_at,
        consumedAtEpochMillis = consumed_at,
    )

    private fun Import_provenance.toDomain(): ImportProvenance = ImportProvenance(
        importId = import_id,
        sourceHash = source_hash,
        safeFileName = safe_file_name,
        format = ImportFormat.valueOf(format),
        companyId = company_id,
        branchId = branch_id,
        entityTypes = splitEntityTypes(entity_types),
        mappingVersion = ImportMappingVersion(mapping_version.toInt()),
        dryRunId = ImportDryRunId(dry_run_id),
        actorId = actor_id,
        startedAtEpochMillis = started_at,
        completedAtEpochMillis = completed_at,
        outcome = ImportOutcome.valueOf(outcome),
        insertedCounts = splitCounts(inserted_counts),
        updatedCounts = splitCounts(updated_counts),
        skippedCount = skipped_count,
        warningCount = warning_count,
        schemaVersion = schema_version.toInt(),
    )
}
