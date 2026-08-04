package com.actionaura.retail.importing

data class ImportDryRunId(val value: String)

/**
 * M5.8.13 -- the real, immutable dry-run authority. `sourceHash` covers
 * the EXACT decoded bytes (not the filename) -- a later file with the
 * same name but different content can never reuse this dry-run
 * (`import-dry-run-contract.md`). No field on this class is ever
 * mutated after construction; a changed plan requires a NEW
 * `ImportDryRunId`.
 */
data class ImportDryRun(
    val id: ImportDryRunId,
    val sourceHash: String,
    val sourceDescriptor: ImportFileDescriptor,
    val format: ImportFormat,
    val decoderVersion: Int,
    val mappingVersion: ImportMappingVersion,
    val schemaVersion: Int,
    val companyId: Long,
    val branchId: Long?,
    val entityTypes: List<ImportEntityType>,
    val dependencies: List<ImportDependency>,
    val totalRows: Long,
    val validRows: Long,
    val invalidRows: Long,
    val warningCount: Long,
    val duplicateCount: Long,
    val conflictCount: Long,
    val plannedInserts: Long,
    val plannedUpdates: Long,
    val plannedSkips: Long,
    val plannedGeneratedValues: Long,
    val validationIssues: List<ImportValidationIssue>,
    val duplicates: List<ImportDuplicate>,
    val conflicts: List<ImportConflict>,
    val commitEligible: Boolean,
    val createdAtEpochMillis: Long,
    val expiresAtEpochMillis: Long,
    /** M5.8.14 -- non-null once a real commit has consumed this dry-run; a second commit attempt against the same dry-run is a real, detected conflict, never silently allowed to proceed. */
    val consumedAtEpochMillis: Long? = null,
)

/**
 * M5.8.14 -- bounded commit authorization tied to the exact dry-run.
 * `idempotencyKey` makes a repeated commit with identical identity
 * return the original result (M5.8.17); `nonce` distinguishes two
 * otherwise-identical commit attempts issued deliberately (e.g. a real
 * "commit again anyway" user action after a first commit's outcome was
 * lost), never silently coalesced with a genuine retry.
 */
data class ImportCommitToken(
    val dryRunId: ImportDryRunId,
    val sourceHash: String,
    val mappingVersion: ImportMappingVersion,
    val schemaVersion: Int,
    val companyId: Long,
    val branchId: Long?,
    val idempotencyKey: String,
    val nonce: String,
    val issuedAtEpochMillis: Long,
    val expiresAtEpochMillis: Long,
)

enum class ImportOutcome { COMMITTED, FAILED, ROLLED_BACK }

data class ImportCommitResult(
    val importId: String,
    val dryRunId: ImportDryRunId,
    val outcome: ImportOutcome,
    val insertedCounts: Map<ImportEntityType, Long>,
    val updatedCounts: Map<ImportEntityType, Long>,
    val skippedCount: Long,
    val warningCount: Long,
    val committedAtEpochMillis: Long,
)

/** M5.8.18 -- durable, queryable record. Never stores full source-file contents or sensitive row data (`import-provenance-audit.md`). */
data class ImportProvenance(
    val importId: String,
    val sourceHash: String,
    val safeFileName: String,
    val format: ImportFormat,
    val companyId: Long,
    val branchId: Long?,
    val entityTypes: List<ImportEntityType>,
    val mappingVersion: ImportMappingVersion,
    val dryRunId: ImportDryRunId,
    val actorId: String,
    val startedAtEpochMillis: Long,
    val completedAtEpochMillis: Long?,
    val outcome: ImportOutcome,
    val insertedCounts: Map<ImportEntityType, Long>,
    val updatedCounts: Map<ImportEntityType, Long>,
    val skippedCount: Long,
    val warningCount: Long,
    val schemaVersion: Int,
)

data class ImportAuditEntry(val importId: String, val timestampEpochMillis: Long, val eventCode: String, val detail: String)
