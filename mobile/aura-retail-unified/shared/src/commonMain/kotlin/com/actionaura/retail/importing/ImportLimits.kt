package com.actionaura.retail.importing

/**
 * M5.8.3 -- real, shared-application-layer resource limits, not a
 * UI-only restriction. Every decoder/pipeline stage receives this
 * explicitly and enforces it BEFORE expensive decoding whenever
 * possible (reject-early discipline). Defaults are real, chosen values
 * for a mobile device, not placeholders -- documented per field.
 */
data class ImportLimits(
    /** Raw uploaded byte count, checked from source metadata/streaming length before full decode. */
    val maxCompressedFileSizeBytes: Long = 25L * 1024 * 1024,
    /** Total decoded/decompressed byte budget (relevant to XLSX's ZIP container) -- the real zip-bomb defense. */
    val maxUncompressedSizeBytes: Long = 100L * 1024 * 1024,
    /** uncompressed / compressed ratio ceiling -- a real, additional zip-bomb defense independent of the absolute size cap. */
    val maxExpansionRatio: Int = 100,
    val maxRowCount: Int = 100_000,
    val maxColumnCount: Int = 200,
    val maxCellLength: Int = 4_000,
    val maxHeaderLength: Int = 200,
    val maxWorksheetCount: Int = 20,
    val maxZipEntryCount: Int = 200,
    val maxJsonDepth: Int = 32,
    val maxJsonArrayLength: Int = 100_000,
    val maxCsvFieldLength: Int = 4_000,
    val maxSqliteTableCount: Int = 200,
    val maxSqliteRowCount: Int = 100_000,
    val maxImportedEntityCount: Int = 100_000,
    val maxValidationIssueCount: Int = 500,
    val maxDryRunLifetimeMillis: Long = 30L * 60 * 1000,
    val maxConcurrentImportJobs: Int = 1,
) {
    companion object {
        val DEFAULT = ImportLimits()
    }
}

/** M5.8.3 -- a real, typed rejection, never a silent truncation or a generic crash. */
data class ImportLimitExceeded(val limitName: String, val limitValue: Long, val observedValue: Long) {
    fun asMessage(): String = "$limitName exceeded: limit=$limitValue observed=$observedValue"
}
