package com.actionaura.retail.importing

/** M5.8.10 -- stable, machine-readable codes; `field`/`detail` are internal English diagnostics, never localized prose (same discipline as `ImportError`). */
sealed class ImportValidationIssue(val code: String) {
    data class MissingRequiredField(val rowNumber: Long, val field: String) : ImportValidationIssue("MISSING_REQUIRED_FIELD")
    data class UnparseableValue(val rowNumber: Long, val field: String, val rawValue: String) : ImportValidationIssue("UNPARSEABLE_VALUE")
    data class AmbiguousNumberFormat(val rowNumber: Long, val field: String, val rawValue: String) : ImportValidationIssue("AMBIGUOUS_NUMBER_FORMAT")
    data class OrphanReference(val rowNumber: Long, val field: String, val referenceValue: String) : ImportValidationIssue("ORPHAN_REFERENCE")
    data class InvalidLeadingZeroLoss(val rowNumber: Long, val field: String, val rawValue: String) : ImportValidationIssue("LEADING_ZERO_AT_RISK")
}

data class ImportWarning(val code: String, val rowNumber: Long?, val detail: String)

enum class ImportDuplicateScope { WITHIN_FILE, AGAINST_DATABASE }

enum class ImportDuplicateDecision {
    INSERT,
    UPDATE_EXISTING,
    SKIP,
    REUSE_EXISTING_REFERENCE,
    GENERATE_DETERMINISTIC_MIGRATION_VALUE,
    REQUIRE_MANUAL_DECISION,
    BLOCK,
}

data class ImportDuplicate(
    val rowNumber: Long,
    val entityType: ImportEntityType,
    val scope: ImportDuplicateScope,
    val matchedField: String,
    val matchedValue: String,
    val decision: ImportDuplicateDecision,
)

data class ImportConflict(val rowNumber: Long, val entityType: ImportEntityType, val field: String, val reason: String)

/** M5.8.12 -- real dependency edge; `required = true` means a missing target reference blocks that row (never a silent placeholder). */
data class ImportDependency(val fromEntityType: ImportEntityType, val toEntityType: ImportEntityType, val required: Boolean)
