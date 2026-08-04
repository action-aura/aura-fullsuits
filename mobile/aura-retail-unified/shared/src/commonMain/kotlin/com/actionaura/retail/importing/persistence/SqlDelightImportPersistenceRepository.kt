package com.actionaura.retail.importing.persistence

import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
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
import com.actionaura.retail.importing.ImportSourceId
import kotlinx.coroutines.sync.withLock

class SqlDelightImportPersistenceRepository(
    private val db: RetailDatabase,
    private val gate: DatabaseWriteGate,
) : ImportPersistenceRepository {

    override suspend fun saveDryRun(dryRun: ImportDryRun) = gate.mutex.withLock {
        db.importQueries.insertDryRun(
            dryRun.id.value, dryRun.companyId, dryRun.branchId, dryRun.sourceHash,
            dryRun.sourceDescriptor.displayName, dryRun.format.name, dryRun.decoderVersion.toLong(),
            dryRun.mappingVersion.value.toLong(), dryRun.schemaVersion.toLong(),
            joinEntityTypes(dryRun.entityTypes), dryRun.totalRows, dryRun.validRows, dryRun.invalidRows,
            dryRun.warningCount, dryRun.duplicateCount, dryRun.conflictCount, dryRun.plannedInserts,
            dryRun.plannedUpdates, dryRun.plannedSkips, dryRun.plannedGeneratedValues,
            if (dryRun.commitEligible) 1L else 0L, dryRun.createdAtEpochMillis, dryRun.expiresAtEpochMillis,
        )
        Unit
    }

    override suspend fun getDryRun(id: ImportDryRunId, companyId: Long): ImportDryRun? = gate.mutex.withLock {
        db.importQueries.selectDryRunById(id.value, companyId).executeAsOneOrNull()?.toDomain()
    }

    override suspend fun markDryRunConsumed(id: ImportDryRunId, companyId: Long, nowEpochMillis: Long): Boolean = gate.mutex.withLock {
        db.importQueries.markDryRunConsumed(nowEpochMillis, id.value, companyId)
        db.importQueries.changes().executeAsOne() > 0L
    }

    override suspend fun saveProvenanceIdempotent(provenance: ImportProvenance, idempotencyKey: String): Pair<ImportProvenance, Boolean> = gate.mutex.withLock {
        db.transactionWithResult {
            // Real, same-transaction check-then-insert -- the exact
            // pattern this codebase already established for Product SKU
            // uniqueness (SqlDelightProductRepository.insert), portable
            // across the JDBC test driver and the real Android driver.
            val existing = db.importQueries.selectProvenanceByIdempotencyKey(idempotencyKey).executeAsOneOrNull()
            if (existing != null) {
                return@transactionWithResult existing.toDomain() to false
            }
            db.importQueries.insertProvenance(
                provenance.importId, provenance.dryRunId.value, provenance.sourceHash, provenance.safeFileName,
                provenance.format.name, provenance.companyId, provenance.branchId, joinEntityTypes(provenance.entityTypes),
                provenance.mappingVersion.value.toLong(), provenance.schemaVersion.toLong(), provenance.actorId,
                provenance.startedAtEpochMillis, provenance.completedAtEpochMillis, provenance.outcome.name,
                joinCounts(provenance.insertedCounts), joinCounts(provenance.updatedCounts),
                provenance.skippedCount, provenance.warningCount, idempotencyKey,
            )
            provenance to true
        }
    }

    override suspend fun getProvenanceByIdempotencyKey(idempotencyKey: String): ImportProvenance? = gate.mutex.withLock {
        db.importQueries.selectProvenanceByIdempotencyKey(idempotencyKey).executeAsOneOrNull()?.toDomain()
    }

    override suspend fun getProvenanceById(importId: String, companyId: Long): ImportProvenance? = gate.mutex.withLock {
        db.importQueries.selectProvenanceById(importId, companyId).executeAsOneOrNull()?.toDomain()
    }

    override suspend fun appendAuditEntry(entry: ImportAuditEntry) = gate.mutex.withLock {
        db.importQueries.insertAuditEntry(entry.importId, entry.timestampEpochMillis, entry.eventCode, entry.detail)
        Unit
    }

    override suspend fun getAuditEntries(importId: String): List<ImportAuditEntry> = gate.mutex.withLock {
        db.importQueries.selectAuditEntriesForImport(importId).executeAsList()
            .map { ImportAuditEntry(it.import_id, it.timestamp, it.event_code, it.detail) }
    }

    private fun joinEntityTypes(types: List<ImportEntityType>): String = types.joinToString(",") { it.name }
    private fun splitEntityTypes(text: String): List<ImportEntityType> =
        if (text.isBlank()) emptyList() else text.split(",").map { ImportEntityType.valueOf(it) }

    private fun joinCounts(counts: Map<ImportEntityType, Long>): String = counts.entries.joinToString(",") { "${it.key.name}:${it.value}" }
    private fun splitCounts(text: String): Map<ImportEntityType, Long> =
        if (text.isBlank()) emptyMap() else text.split(",").associate { pair ->
            val (k, v) = pair.split(":")
            ImportEntityType.valueOf(k) to v.toLong()
        }

    private fun Import_dry_runs.toDomain(): ImportDryRun = ImportDryRun(
        id = ImportDryRunId(id),
        sourceHash = source_hash,
        sourceDescriptor = ImportFileDescriptor(ImportSourceId(id), source_display_name, null, null, null),
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
