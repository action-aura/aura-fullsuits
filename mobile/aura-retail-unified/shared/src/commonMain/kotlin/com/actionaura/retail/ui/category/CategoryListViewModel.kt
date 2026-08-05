package com.actionaura.retail.ui.category

import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.model.Category
import com.actionaura.retail.di.AuraAppContainer
import com.actionaura.retail.presentation.AuraViewModel
import com.actionaura.retail.presentation.LoadState
import com.actionaura.retail.presentation.SearchState
import com.actionaura.retail.presentation.UiEffect
import com.actionaura.retail.presentation.UiMessage
import com.actionaura.retail.presentation.toUiMessage
import kotlinx.datetime.Clock

/**
 * M6.16 -- the real Category list ViewModel. Uses the real M5.2/M5.3
 * `ListActiveCategoriesUseCase`/`ArchiveCategoryUseCase`/
 * `ReactivateCategoryUseCase` via `AuraAppContainer` -- never queries
 * SQLDelight directly (`presentation-architecture.md`'s own explicit
 * rule).
 *
 * Real, disclosed scope: `CategoryRepository` (M5.1) has no
 * `listArchived`/`listAll` query -- only `listActive` and `getById`.
 * This screen therefore shows ACTIVE categories only, with a real
 * archive action; browsing already-archived categories is a real,
 * disclosed gap (would need a new repository query, out of this
 * milestone's scope) -- `category-ui-vertical-slice.md` records this
 * honestly rather than faking an "archived" tab with no real data
 * behind it.
 */
data class CategoryListUiState(
    val categories: List<Category> = emptyList(),
    val searchState: SearchState = SearchState(),
    val loadState: LoadState = LoadState.Idle,
) {
    val visibleCategories: List<Category> get() =
        if (searchState.appliedQuery.isBlank()) categories
        else categories.filter { it.name.contains(searchState.appliedQuery, ignoreCase = true) }
}

sealed interface CategoryListEffect : UiEffect {
    data class ShowMessage(val message: UiMessage) : CategoryListEffect
}

class CategoryListViewModel(
    private val container: AuraAppContainer,
    private val companyId: Long = 1L,
    dispatcher: kotlinx.coroutines.CoroutineDispatcher = kotlinx.coroutines.Dispatchers.Default,
) : AuraViewModel<CategoryListUiState, CategoryListEffect>(CategoryListUiState(), dispatcher) {

    fun load() = launchOnDefault {
        setState { it.copy(loadState = LoadState.Loading) }
        val categories = container.listActiveCategoriesUseCase.execute(companyId)
        setState { it.copy(categories = categories, loadState = LoadState.Success) }
    }

    fun onSearchQueryChange(query: String) {
        setState { it.copy(searchState = it.searchState.copy(rawQuery = query, appliedQuery = query)) }
    }

    fun onArchive(categoryId: Long) = launchOnDefault {
        when (val result = container.archiveCategoryUseCase.execute(companyId, categoryId)) {
            is DomainResult.Success -> load()
            is DomainResult.Failure -> sendEffect(CategoryListEffect.ShowMessage(result.error.toUiMessage()))
        }
    }

    fun onReactivate(categoryId: Long) = launchOnDefault {
        when (val result = container.reactivateCategoryUseCase.execute(companyId, categoryId)) {
            is DomainResult.Success -> load()
            is DomainResult.Failure -> sendEffect(CategoryListEffect.ShowMessage(result.error.toUiMessage()))
        }
    }
}

internal fun nowEpochMillis(): Long = Clock.System.now().toEpochMilliseconds()
