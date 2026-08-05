package com.actionaura.retail.ui.branch

import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.model.Branch
import com.actionaura.retail.di.AuraAppContainer
import com.actionaura.retail.presentation.AuraViewModel
import com.actionaura.retail.presentation.LoadState
import com.actionaura.retail.presentation.UiEffect
import com.actionaura.retail.presentation.UiMessage
import com.actionaura.retail.presentation.toUiMessage
import com.actionaura.retail.ui.category.nowEpochMillis

/**
 * M6.17 -- real Branch list ViewModel, using the real M5.4
 * `ListActiveBranchesUseCase`/`GetCurrentBranchUseCase`/
 * `SetCurrentBranchUseCase`/`ActivateBranchUseCase`/
 * `DeactivateBranchUseCase`. `DeactivateBranchUseCase` enforces the
 * real, atomic last-active-branch protection at the repository layer
 * (`RepositoryError.LastActiveProtected`) -- this ViewModel never
 * re-implements that check, only surfaces the real failure via
 * `toUiMessage()`.
 *
 * Real, disclosed scope: `BranchRepository` has no `insert`-wrapping
 * use case with duplicate-name detection (unlike Category) -- real,
 * confirmed audit of `usecases/branch/BranchUseCases.kt`: no such use
 * case exists. Creation here calls `branchRepository.insert` directly,
 * matching the real, existing authority's own scope exactly, not
 * inventing a validation rule that does not exist yet.
 */
data class BranchListUiState(
    val branches: List<Branch> = emptyList(),
    val currentBranchId: Long? = null,
    val loadState: LoadState = LoadState.Idle,
)

sealed interface BranchListEffect : UiEffect {
    data class ShowMessage(val message: UiMessage) : BranchListEffect
}

class BranchListViewModel(
    private val container: AuraAppContainer,
    private val companyId: Long = 1L,
    dispatcher: kotlinx.coroutines.CoroutineDispatcher = kotlinx.coroutines.Dispatchers.Default,
) : AuraViewModel<BranchListUiState, BranchListEffect>(BranchListUiState(), dispatcher) {

    fun load() = launchOnDefault {
        setState { it.copy(loadState = LoadState.Loading) }
        val branches = container.listActiveBranchesUseCase.execute(companyId)
        val current = container.getCurrentBranchUseCase.execute(companyId)
        setState { it.copy(branches = branches, currentBranchId = current?.id, loadState = LoadState.Success) }
    }

    fun onSelectCurrent(branchId: Long) = launchOnDefault {
        when (val result = container.setCurrentBranchUseCase.execute(companyId, branchId)) {
            is DomainResult.Success -> load()
            is DomainResult.Failure -> sendEffect(BranchListEffect.ShowMessage(result.error.toUiMessage()))
        }
    }

    fun onArchive(branchId: Long) = launchOnDefault {
        when (val result = container.deactivateBranchUseCase.execute(companyId, branchId)) {
            is DomainResult.Success -> load()
            is DomainResult.Failure -> sendEffect(BranchListEffect.ShowMessage(result.error.toUiMessage()))
        }
    }

    fun onReactivate(branchId: Long) = launchOnDefault {
        when (val result = container.activateBranchUseCase.execute(companyId, branchId)) {
            is DomainResult.Success -> load()
            is DomainResult.Failure -> sendEffect(BranchListEffect.ShowMessage(result.error.toUiMessage()))
        }
    }

    fun onCreate(name: String) = launchOnDefault {
        val trimmed = name.trim()
        if (trimmed.isEmpty()) return@launchOnDefault
        container.branchRepository.insert(companyId, trimmed, null, null, nowEpochMillis())
        load()
    }
}
