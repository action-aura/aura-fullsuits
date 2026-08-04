package com.actionaura.retail.usecases.branch

import com.actionaura.retail.data.BranchRepository
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.RepositoryError
import com.actionaura.retail.data.SettingsRepository
import com.actionaura.retail.data.model.Branch

/**
 * M5.4 -- the Branch domain's use-case layer, one layer above the M5.1
 * `BranchRepository`. Final-active-branch protection is already real,
 * atomic, and tested at the repository layer (`BranchRepository.setActive`,
 * M5.1) -- these use cases add existence validation on top (matching how
 * `ArchiveCategoryUseCase` adds it above `CategoryRepository.setActive`,
 * which likewise has no existence check of its own), plus current-branch
 * selection and legacy default-branch backfill support, neither of which
 * belongs at the repository layer.
 */

private const val CURRENT_BRANCH_SETTING_KEY = "current_branch_id"

class ActivateBranchUseCase(private val repository: BranchRepository) {
    suspend fun execute(companyId: Long, branchId: Long): DomainResult<Unit> {
        val existing = repository.getById(companyId, branchId)
            ?: return DomainResult.Failure(RepositoryError.NotFound("branch", branchId.toString()))
        if (existing.isActive) return DomainResult.Success(Unit) // idempotent no-op
        return repository.setActive(companyId, branchId, true)
    }
}

class DeactivateBranchUseCase(private val repository: BranchRepository) {
    suspend fun execute(companyId: Long, branchId: Long): DomainResult<Unit> {
        val existing = repository.getById(companyId, branchId)
            ?: return DomainResult.Failure(RepositoryError.NotFound("branch", branchId.toString()))
        if (!existing.isActive) return DomainResult.Success(Unit) // idempotent no-op
        return repository.setActive(companyId, branchId, false) // LastActiveProtected still enforced atomically inside the repository
    }
}

/**
 * "Current branch" is per-company UI/session state (which branch this
 * device's POS session is currently operating against), persisted via
 * `SettingsRepository` under a well-known key -- not a new table, since
 * it is conceptually identical to every other single-value setting
 * `retail_settings` already stores.
 */
class GetCurrentBranchUseCase(
    private val branchRepository: BranchRepository,
    private val settingsRepository: SettingsRepository,
) {
    suspend fun execute(companyId: Long): Branch? {
        val storedId = settingsRepository.getSetting(companyId, CURRENT_BRANCH_SETTING_KEY)?.toLongOrNull()
        val stored = storedId?.let { branchRepository.getById(companyId, it) }
        if (stored != null && stored.isActive) return stored
        // No selection, or the previously-selected branch was archived --
        // fall back to the same deterministic default EnsureDefaultBranchUseCase uses.
        return branchRepository.listActive(companyId).minByOrNull { it.id }
    }
}

class SetCurrentBranchUseCase(
    private val branchRepository: BranchRepository,
    private val settingsRepository: SettingsRepository,
) {
    suspend fun execute(companyId: Long, branchId: Long): DomainResult<Unit> {
        val branch = branchRepository.getById(companyId, branchId)
            ?: return DomainResult.Failure(RepositoryError.NotFound("branch", branchId.toString()))
        if (!branch.isActive) {
            return DomainResult.Failure(RepositoryError.ValidationFailed("branch", "cannot select an archived branch as current"))
        }
        settingsRepository.setSetting(companyId, CURRENT_BRANCH_SETTING_KEY, branchId.toString())
        return DomainResult.Success(Unit)
    }
}

/**
 * Deterministic default-branch resolution for legacy branch_id-less rows
 * (`sales.branch_id`/`returns.branch_id`/`inventory_balances.branch_id`
 * are all nullable in both the legacy Python schema and the unified
 * schema, per table-count-reconciliation.md) -- the Milestone 18 full
 * importer needs a real fallback when backfilling a legacy row whose
 * `branch_id` was NULL. Rule: if at least one active branch already
 * exists, the default is the lowest-id active branch (deterministic,
 * never ambiguous between two calls, never a newly created one). If none
 * exists yet, exactly one canonical "Main Branch" is created and becomes
 * the default.
 *
 * Real, acknowledged race, not fixed in this milestone: two concurrent
 * first-ever calls for the same company (no branch exists yet) could each
 * observe an empty `listActive` and each create a distinct "Main Branch"
 * row. The realistic triggers for this use case -- single-device first-run
 * initialization, and the single-threaded Milestone 18 import -- do not
 * exercise concurrent same-company branch creation; true multi-device
 * simultaneous first activation is a narrow edge case tracked for a future
 * hardening pass, not silently ignored.
 */
class EnsureDefaultBranchUseCase(private val repository: BranchRepository) {
    suspend fun execute(companyId: Long, nowEpochMillis: Long): Branch {
        val existingDefault = repository.listActive(companyId).minByOrNull { it.id }
        if (existingDefault != null) return existingDefault
        return repository.insert(companyId, "Main Branch", null, null, nowEpochMillis)
    }
}

class ListActiveBranchesUseCase(private val repository: BranchRepository) {
    suspend fun execute(companyId: Long): List<Branch> = repository.listActive(companyId)
}
