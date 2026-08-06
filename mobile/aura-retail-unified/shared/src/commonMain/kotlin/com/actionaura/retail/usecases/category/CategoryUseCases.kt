package com.actionaura.retail.usecases.category

import com.actionaura.retail.data.CategoryRepository
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.RepositoryError
import com.actionaura.retail.data.model.Category
import com.actionaura.retail.platform.UnicodeTextNormalizer

/**
 * M5.2/M5.3 -- the Category domain's use-case layer. Everything the thin
 * M5.1 `CategoryRepository` deliberately leaves undecided (duplicate-name
 * detection with Arabic/Unicode normalization, archive/reactivate
 * semantics) lives here, one layer above the repository -- per the
 * governing spec's required dependency direction: use case -> repository
 * interface -> repository implementation -> SQLDelight.
 *
 * Historical-relationship preservation is a structural property, not use-
 * case logic: `CategoryRepository.setActive` is a pure status `UPDATE` on
 * `categories` alone (M5.1) -- it never touches `products.category_id`,
 * so an archived category's existing product associations (and every
 * already-finalized sale/return line's own `product_name_at_sale`
 * snapshot, DIFF-03) are structurally untouched by archiving. Proven by
 * `CategoryUseCasesTest.archivingCategoryPreservesExistingProductAssociation`.
 */
class CreateCategoryUseCase(
    private val repository: CategoryRepository,
    private val normalizer: UnicodeTextNormalizer,
) {
    suspend fun execute(companyId: Long, name: String, description: String?, nowEpochMillis: Long): DomainResult<Category> {
        val trimmedName = name.trim()
        if (trimmedName.isEmpty()) {
            return DomainResult.Failure(RepositoryError.ValidationFailed("category", "name must not be blank"))
        }
        val normalizedCandidate = normalizer.normalizeForComparison(trimmedName)
        val conflict = repository.listActive(companyId)
            .firstOrNull { normalizer.normalizeForComparison(it.name) == normalizedCandidate }
        if (conflict != null) {
            return DomainResult.Failure(RepositoryError.DuplicateName("category", trimmedName, conflict.id))
        }
        val created = repository.insert(companyId, trimmedName, description?.trim()?.ifEmpty { null }, nowEpochMillis)
        return DomainResult.Success(created)
    }
}

class ArchiveCategoryUseCase(private val repository: CategoryRepository) {
    suspend fun execute(companyId: Long, categoryId: String): DomainResult<Unit> {
        val existing = repository.getById(companyId, categoryId)
            ?: return DomainResult.Failure(RepositoryError.NotFound("category", categoryId))
        if (!existing.isActive) return DomainResult.Success(Unit) // idempotent no-op, matches repository-layer retry conventions elsewhere in this codebase
        repository.setActive(companyId, categoryId, false)
        return DomainResult.Success(Unit)
    }
}

class ReactivateCategoryUseCase(
    private val repository: CategoryRepository,
    private val normalizer: UnicodeTextNormalizer,
) {
    suspend fun execute(companyId: Long, categoryId: String): DomainResult<Category> {
        val existing = repository.getById(companyId, categoryId)
            ?: return DomainResult.Failure(RepositoryError.NotFound("category", categoryId))
        if (existing.isActive) return DomainResult.Success(existing) // idempotent no-op

        // A different active category may have been created with the same
        // normalized name while this one was archived -- reactivating
        // without re-checking would recreate exactly the duplicate-name
        // state creation-time dedup exists to prevent.
        val normalizedCandidate = normalizer.normalizeForComparison(existing.name)
        val conflict = repository.listActive(companyId)
            .firstOrNull { normalizer.normalizeForComparison(it.name) == normalizedCandidate }
        if (conflict != null) {
            return DomainResult.Failure(RepositoryError.DuplicateName("category", existing.name, conflict.id))
        }
        repository.setActive(companyId, categoryId, true)
        return DomainResult.Success(repository.getById(companyId, categoryId)!!)
    }
}

class ListActiveCategoriesUseCase(private val repository: CategoryRepository) {
    suspend fun execute(companyId: Long): List<Category> = repository.listActive(companyId)
}
