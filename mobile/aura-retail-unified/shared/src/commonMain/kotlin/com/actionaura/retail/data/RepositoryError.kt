package com.actionaura.retail.data

/**
 * M5.1 -- stable, machine-readable errors for the general repository layer
 * (catalog/inventory/settings). Deliberately separate from
 * `financial.FinancialError`, which is scoped specifically to sale/return
 * finalization (financial-error-code-map.md). Same discipline applies:
 * `code` is the stable identifier, no English/Arabic prose here --
 * localization happens strictly in a later UI-facing layer.
 */
sealed class RepositoryError(val code: String) {
    data class NotFound(val entity: String, val id: String) : RepositoryError("NOT_FOUND")
    data class InsufficientStock(val productId: String, val have: String, val requested: String) : RepositoryError("INSUFFICIENT_STOCK")
    data class LastActiveProtected(val entity: String, val id: String) : RepositoryError("LAST_ACTIVE_PROTECTED")
    data class DuplicateName(val entity: String, val name: String, val conflictingId: String) : RepositoryError("DUPLICATE_NAME")
    data class ValidationFailed(val entity: String, val reason: String) : RepositoryError("VALIDATION_FAILED")
    data class StaleUpdate(val entity: String, val id: String) : RepositoryError("STALE_UPDATE")
    data class IdempotencyConflict(val entity: String, val key: String) : RepositoryError("IDEMPOTENCY_CONFLICT")
    data class DuplicateValue(val entity: String, val field: String, val value: String) : RepositoryError("DUPLICATE_VALUE")

    /** M5.6.18 -- a requested scope/capability the caller's `ReportingAccessContext` does not grant (reporting-authorization-integration-boundary.md). */
    data class AccessDenied(val entity: String, val reason: String) : RepositoryError("ACCESS_DENIED")
}
