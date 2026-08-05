package com.actionaura.retail.presentation

import com.actionaura.retail.data.RepositoryError

/**
 * M6.16/M6.17 -- the real, shared `RepositoryError -> UiMessage`
 * mapping boundary. Every M6+ ViewModel calling a real M5.x use case
 * maps its `DomainResult.Failure.error` through this, never displays
 * `RepositoryError` fields directly (M6.14's own "map stable domain
 * errors into localized presentation messages" rule).
 */
fun RepositoryError.toUiMessage(): UiMessage = when (this) {
    is RepositoryError.NotFound -> UiMessage("error.not_found", listOf(entity))
    is RepositoryError.InsufficientStock -> UiMessage("error.insufficient_stock", listOf(productId))
    is RepositoryError.LastActiveProtected -> UiMessage("branch.last_active_protected")
    is RepositoryError.DuplicateName -> UiMessage("error.duplicate_name", listOf(name))
    is RepositoryError.ValidationFailed -> UiMessage("error.validation_failed", listOf(reason))
    is RepositoryError.StaleUpdate -> UiMessage("error.stale_update", listOf(entity))
    is RepositoryError.IdempotencyConflict -> UiMessage("error.generic")
    is RepositoryError.DuplicateValue -> UiMessage("error.duplicate_name", listOf(value))
    is RepositoryError.AccessDenied -> UiMessage("error.access_denied")
}
