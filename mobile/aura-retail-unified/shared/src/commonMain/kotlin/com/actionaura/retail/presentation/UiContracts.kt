package com.actionaura.retail.presentation

/**
 * M6.1 -- the shared presentation vocabulary every screen's `UiState`
 * is built from. Real, deliberate separation from `data.RepositoryError`/
 * `importing.ImportError`: those are domain result types, consumed HERE
 * to produce a `UiMessage`, never displayed directly (`screen-state-contract.md`).
 */

/** Real, stable, localizable message -- `key` is looked up through the shared localization authority (M6.11), never displayed as raw English text directly from here. */
data class UiMessage(val key: String, val args: List<String> = emptyList(), val severity: UiMessageSeverity = UiMessageSeverity.ERROR)

enum class UiMessageSeverity { INFO, SUCCESS, WARNING, ERROR }

/** One field's real validation state within a form -- `messageKey` is null when the field is currently valid. */
data class UiFieldError(val fieldKey: String, val messageKey: String?)

/** Real, generic content-loading lifecycle -- distinct from `SubmissionState` (a user-initiated write), matching M6.14's own required state list. */
sealed interface LoadState {
    data object Idle : LoadState
    data object Loading : LoadState
    data object Refreshing : LoadState
    data object Success : LoadState
    data class Error(val message: UiMessage) : LoadState
    data object Offline : LoadState
}

/** Real, generic write/submit lifecycle for a form or destructive action. */
sealed interface SubmissionState {
    data object Idle : SubmissionState
    data object Submitting : SubmissionState
    data object Success : SubmissionState
    data class Error(val message: UiMessage) : SubmissionState
}

/** Real, bounded pagination state -- `hasMore` is the real, server/repository-reported signal, never inferred from `items.size`. */
data class PaginationState<T>(
    val items: List<T> = emptyList(),
    val hasMore: Boolean = true,
    val isLoadingMore: Boolean = false,
    val loadState: LoadState = LoadState.Idle,
)

/** Real, debounced search query state -- `appliedQuery` is what the last real repository call used, distinct from `rawQuery` (what the field currently shows), so a fast typist never sees a stale result flash the wrong query's data (`list-search-pagination-contract.md`). */
data class SearchState(val rawQuery: String = "", val appliedQuery: String = "")

/** Real, generic multi-select state for list screens (e.g. bulk archive). */
data class SelectionState<K>(val selectedIds: Set<K> = emptySet(), val isSelectionMode: Boolean = false)

/** Real, generic pending-confirmation state for a destructive/irreversible action -- the action itself is never executed until the real confirmation callback fires (`AuraDestructiveConfirmation`, M6.8). */
data class ConfirmationState<T>(val pending: T? = null)

/**
 * One-time, real UI effect -- navigation, a snackbar, a focus request.
 * Never stored inside `UiState` (a `StateFlow` re-delivers its last value
 * to every new collector, which would replay a stale one-time event) --
 * consumed exclusively through `AuraViewModel.effects`, a `Channel`-backed
 * `Flow` (M6.2's own explicit "no one-time events stored forever inside
 * state" rule).
 */
interface UiEffect
