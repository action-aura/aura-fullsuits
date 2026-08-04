package com.actionaura.retail.importing

/**
 * M5.8.1 -- the Import Center's own typed result channel, mirroring
 * `data.DomainResult`/`financial.FinancialResult`'s established
 * per-domain-result-type pattern rather than forcing `RepositoryError`
 * (a different domain's stable error codes) onto import-specific
 * failures.
 */
sealed class ImportResult<out T> {
    data class Success<T>(val value: T) : ImportResult<T>()
    data class Failure(val error: ImportError) : ImportResult<Nothing>()

    fun getOrNull(): T? = (this as? Success)?.value

    inline fun <R> map(transform: (T) -> R): ImportResult<R> = when (this) {
        is Success -> Success(transform(value))
        is Failure -> this
    }
}

/**
 * Stable, machine-readable codes -- no localized prose (`detail` is an
 * internal, English, developer-facing diagnostic only, same discipline
 * `RepositoryError.ValidationFailed`'s own `reason` field already
 * established; a future UI layer maps `code` to a localized string, it
 * never displays `detail` directly).
 */
sealed class ImportError(val code: String) {
    data class LimitExceeded(val limit: ImportLimitExceeded) : ImportError("LIMIT_EXCEEDED")
    data class UnsupportedFormat(val detail: String) : ImportError("UNSUPPORTED_FORMAT")
    data class FormatContentMismatch(val declaredFormat: ImportFormat?, val detectedFormat: ImportFormat?, val detail: String) : ImportError("FORMAT_CONTENT_MISMATCH")
    data class MalformedContent(val detail: String) : ImportError("MALFORMED_CONTENT")
    data class NoHeaders(val detail: String) : ImportError("NO_HEADERS")
    data class NoDataRows(val detail: String) : ImportError("NO_DATA_ROWS")
    data class UnsafeContent(val detail: String) : ImportError("UNSAFE_CONTENT")
    data class UnknownEntity(val detail: String) : ImportError("UNKNOWN_ENTITY")
    data class AmbiguousEntity(val candidates: List<ImportEntityType>, val detail: String) : ImportError("AMBIGUOUS_ENTITY")
    data class DryRunNotFound(val dryRunId: ImportDryRunId) : ImportError("DRY_RUN_NOT_FOUND")
    data class DryRunExpired(val dryRunId: ImportDryRunId) : ImportError("DRY_RUN_EXPIRED")
    data class SourceMismatch(val detail: String) : ImportError("SOURCE_MISMATCH")
    data class SchemaMismatch(val detail: String) : ImportError("SCHEMA_MISMATCH")
    data class CommitConflict(val detail: String) : ImportError("COMMIT_CONFLICT")
    data class AccessDenied(val detail: String) : ImportError("ACCESS_DENIED")
    data class InternalFailure(val detail: String) : ImportError("INTERNAL_FAILURE")
}
