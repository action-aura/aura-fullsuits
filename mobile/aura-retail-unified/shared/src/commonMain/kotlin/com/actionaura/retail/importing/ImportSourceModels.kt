package com.actionaura.retail.importing

/**
 * M5.8.1 -- real, supported source formats. Confirmed by
 * `import-authority-audit.md`'s real audit of the legacy Retail import
 * authority: CSV, XLSX, JSON, SQLite -- not a speculative list.
 */
enum class ImportFormat { CSV, XLSX, JSON, SQLITE }

data class ImportSourceId(val value: String)

/** Never a raw platform path -- `displayName` is a sanitized, safe-to-log filename only. */
data class ImportFileDescriptor(
    val sourceId: ImportSourceId,
    val displayName: String,
    val declaredSizeBytes: Long?,
    val declaredMimeType: String?,
    val declaredExtension: String?,
)

/**
 * Platform adapters (Android SAF content URI, future iOS security-scoped
 * URL) implement this. `commonMain` business logic never touches a raw
 * file path or platform file handle -- only this contract
 * (`import-parser-decision.md`'s own "platform decoders may handle...
 * business rules must remain shared" boundary).
 */
interface ImportSource {
    val descriptor: ImportFileDescriptor

    /**
     * Reads the ENTIRE source into memory, bounded by
     * `limits.maxCompressedFileSizeBytes` -- real, disclosed scope
     * decision (`import-parser-decision.md`): whole-file-in-memory up to
     * a hard, enforced cap, not a streaming parser. Returns
     * `ImportError.LimitExceeded` if the real source exceeds the limit,
     * checked incrementally where the underlying platform API supports
     * it (never after allocating the full oversized buffer).
     */
    suspend fun readBounded(limits: ImportLimits): ImportResult<ByteArray>
}

data class NormalizedColumn(val index: Int, val rawHeader: String)

/** Positional: `cells[i]` corresponds to `columns[i]`. `null` = the source had no value for that column in this row (never coerced to `""`, which is a real empty string, a different real state). */
data class NormalizedRow(val rowNumber: Long, val cells: List<String?>)

/**
 * The one shared contract every decoder (CSV/XLSX/JSON/SQLite,
 * `commonMain` or platform-specific) produces -- business rules
 * (entity detection, mapping, validation, duplicates, dry-run, commit)
 * consume ONLY this, never a format-specific or platform-specific type
 * (`import-parser-decision.md`'s own structural guarantee).
 */
data class NormalizedTable(
    val columns: List<NormalizedColumn>,
    val rows: List<NormalizedRow>,
    val sourceFormat: ImportFormat,
)

/**
 * The single shared decoding entry point. `commonMain` implementations
 * exist for CSV/JSON; XLSX/SQLite are implemented in `androidMain`
 * (`import-parser-decision.md`), all satisfying this same interface.
 */
interface ImportDecoder {
    val format: ImportFormat
    suspend fun decode(source: ImportSource, limits: ImportLimits): ImportResult<NormalizedTable>
}
