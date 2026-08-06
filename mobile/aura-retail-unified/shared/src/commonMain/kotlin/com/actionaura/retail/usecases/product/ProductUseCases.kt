package com.actionaura.retail.usecases.product

import com.actionaura.retail.data.BranchRepository
import com.actionaura.retail.data.CategoryRepository
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.ProductRepository
import com.actionaura.retail.data.RepositoryError
import com.actionaura.retail.data.model.Product
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate
import com.actionaura.retail.financial.Quantity
import com.actionaura.retail.platform.UnicodeTextNormalizer

/**
 * M5.5.1-M5.5.4 -- the Product domain's use-case layer, one layer above
 * the M5.1/M5.5 `ProductRepository` (product-domain-contract.md). Field
 * validation, category-eligibility, and barcode/SKU shape rules live
 * here; barcode/SKU physical-uniqueness enforcement (the actual
 * concurrency-safe backstop) lives in the repository itself
 * (barcode-and-sku-contract.md), same split as `BranchRepository`'s
 * `LastActiveProtected` vs. `ArchiveCategoryUseCase`'s validation.
 */

internal const val MAX_PRODUCT_NAME_LENGTH = 200

internal fun validateName(name: String): DomainResult<String> {
    val trimmed = name.trim()
    if (trimmed.isEmpty()) return DomainResult.Failure(RepositoryError.ValidationFailed("product", "name must not be blank"))
    if (trimmed.length > MAX_PRODUCT_NAME_LENGTH) {
        return DomainResult.Failure(RepositoryError.ValidationFailed("product", "name exceeds $MAX_PRODUCT_NAME_LENGTH characters"))
    }
    return DomainResult.Success(trimmed)
}

internal fun validateSku(sku: String): DomainResult<String> {
    // "normalize whitespace" (M5.5.2) -- trim + collapse internal runs,
    // same pattern as Category name -- but the user-visible ORIGINAL case
    // is preserved (uniqueness is case-insensitive at the DB level via
    // COLLATE NOCASE, barcode-and-sku-contract.md -- the stored value
    // itself is never case-folded).
    val normalized = sku.trim().replace(Regex("\\s+"), " ")
    if (normalized.isEmpty()) return DomainResult.Failure(RepositoryError.ValidationFailed("product", "sku must not be blank"))
    return DomainResult.Success(normalized)
}

/**
 * "preserve exact textual identity after documented safe trimming" --
 * trims only, never collapses internal whitespace or changes case (case-
 * insensitive comparison happens at the DB level, not here) -- and never
 * reformats between barcode symbologies. A present-but-blank barcode is
 * rejected (use `null` for "no barcode"); a present barcode with control
 * characters is rejected.
 */
internal fun validateBarcode(barcode: String?): DomainResult<String?> {
    if (barcode == null) return DomainResult.Success(null)
    val trimmed = barcode.trim()
    if (trimmed.isEmpty()) {
        return DomainResult.Failure(RepositoryError.ValidationFailed("product", "barcode must not be blank when provided -- omit it (null) instead"))
    }
    if (trimmed.any { it.code < 0x20 }) {
        return DomainResult.Failure(RepositoryError.ValidationFailed("product", "barcode contains unsupported control characters"))
    }
    return DomainResult.Success(trimmed)
}

internal suspend fun validateCategoryAssignment(categoryRepository: CategoryRepository, companyId: Long, categoryId: String?): DomainResult<Unit> {
    if (categoryId == null) return DomainResult.Success(Unit)
    val category = categoryRepository.getById(companyId, categoryId)
        ?: return DomainResult.Failure(RepositoryError.NotFound("category", categoryId))
    if (!category.isActive) {
        return DomainResult.Failure(RepositoryError.ValidationFailed("product", "cannot assign an archived category"))
    }
    return DomainResult.Success(Unit)
}

class CreateProductUseCase(
    private val productRepository: ProductRepository,
    private val categoryRepository: CategoryRepository,
    private val normalizer: UnicodeTextNormalizer,
) {
    suspend fun execute(
        companyId: Long,
        sku: String,
        barcode: String?,
        name: String,
        categoryId: String?,
        costPrice: Money,
        sellPrice: Money,
        taxRate: PercentageRate,
        unit: String,
        reorderLevel: Long,
        nowEpochMillis: Long,
    ): DomainResult<Product> {
        val validName = (validateName(name) as? DomainResult.Success)?.value
            ?: return validateName(name) as DomainResult.Failure
        val validSku = (validateSku(sku) as? DomainResult.Success)?.value
            ?: return validateSku(sku) as DomainResult.Failure
        val barcodeResult = validateBarcode(barcode)
        if (barcodeResult is DomainResult.Failure) return barcodeResult
        val validBarcode = (barcodeResult as DomainResult.Success).value

        // CANONICAL_UNIFIED (product-inventory-authority-audit.md's own
        // classification): the legacy authority accepts negative prices
        // with zero validation -- a real, documented gap, not replicated.
        if (costPrice.isNegative()) return DomainResult.Failure(RepositoryError.ValidationFailed("product", "cost price must not be negative"))
        if (sellPrice.isNegative()) return DomainResult.Failure(RepositoryError.ValidationFailed("product", "sell price must not be negative"))

        val categoryCheck = validateCategoryAssignment(categoryRepository, companyId, categoryId)
        if (categoryCheck is DomainResult.Failure) return categoryCheck

        val normalizedName = normalizer.normalizeForComparison(validName)
        val trimmedUnit = unit.trim().ifEmpty { "pcs" }
        return productRepository.insert(
            companyId, validSku, validBarcode, validName, normalizedName, categoryId,
            costPrice, sellPrice, taxRate, trimmedUnit, reorderLevel, nowEpochMillis,
        )
    }
}

class UpdateProductUseCase(
    private val productRepository: ProductRepository,
    private val categoryRepository: CategoryRepository,
    private val normalizer: UnicodeTextNormalizer,
) {
    suspend fun execute(
        companyId: Long,
        id: Long,
        barcode: String?,
        name: String,
        categoryId: String?,
        costPrice: Money,
        sellPrice: Money,
        taxRate: PercentageRate,
        unit: String,
        reorderLevel: Long,
        expectedUpdatedAtEpochMillis: Long,
        nowEpochMillis: Long,
    ): DomainResult<Product> {
        val validName = (validateName(name) as? DomainResult.Success)?.value
            ?: return validateName(name) as DomainResult.Failure
        val barcodeResult = validateBarcode(barcode)
        if (barcodeResult is DomainResult.Failure) return barcodeResult
        val validBarcode = (barcodeResult as DomainResult.Success).value

        if (costPrice.isNegative()) return DomainResult.Failure(RepositoryError.ValidationFailed("product", "cost price must not be negative"))
        if (sellPrice.isNegative()) return DomainResult.Failure(RepositoryError.ValidationFailed("product", "sell price must not be negative"))

        val categoryCheck = validateCategoryAssignment(categoryRepository, companyId, categoryId)
        if (categoryCheck is DomainResult.Failure) return categoryCheck

        val normalizedName = normalizer.normalizeForComparison(validName)
        val trimmedUnit = unit.trim().ifEmpty { "pcs" }
        return productRepository.update(
            companyId, id, validBarcode, validName, normalizedName, categoryId,
            costPrice, sellPrice, taxRate, trimmedUnit, reorderLevel, expectedUpdatedAtEpochMillis, nowEpochMillis,
        )
    }
}

/**
 * A focused subset of `UpdateProductUseCase` for the one field the POS/
 * catalog-management UI changes most often in isolation -- still routes
 * through the same optimistic-concurrency-checked `ProductRepository.update`
 * (M5.5.4: "Do not trust Category state loaded earlier in the UI" --
 * `validateCategoryAssignment` re-reads the category fresh here, not from
 * a caller-supplied snapshot).
 */
class AssignProductCategoryUseCase(
    private val productRepository: ProductRepository,
    private val categoryRepository: CategoryRepository,
    private val normalizer: UnicodeTextNormalizer,
) {
    suspend fun execute(companyId: Long, productId: Long, categoryId: String?, expectedUpdatedAtEpochMillis: Long, nowEpochMillis: Long): DomainResult<Product> {
        val current = productRepository.getById(companyId, productId)
            ?: return DomainResult.Failure(RepositoryError.NotFound("product", productId.toString()))
        val categoryCheck = validateCategoryAssignment(categoryRepository, companyId, categoryId)
        if (categoryCheck is DomainResult.Failure) return categoryCheck

        return productRepository.update(
            companyId, productId, current.barcode, current.name, normalizer.normalizeForComparison(current.name), categoryId,
            current.costPrice, current.sellPrice, current.taxRate, current.unit, current.reorderLevel,
            expectedUpdatedAtEpochMillis, nowEpochMillis,
        )
    }
}

class ArchiveProductUseCase(private val productRepository: ProductRepository) {
    suspend fun execute(companyId: Long, productId: Long, nowEpochMillis: Long): DomainResult<Unit> {
        val existing = productRepository.getById(companyId, productId)
            ?: return DomainResult.Failure(RepositoryError.NotFound("product", productId.toString()))
        if (!existing.isActive) return DomainResult.Success(Unit) // idempotent no-op
        productRepository.setActive(companyId, productId, false, nowEpochMillis)
        return DomainResult.Success(Unit)
    }
}

/**
 * Real, structural finding (product-domain-contract.md): unlike Category
 * reactivation, Product reactivation needs NO barcode/SKU re-check --
 * both remain permanently reserved to this product even while archived
 * (products_company_sku is unconditional, products_company_barcode's
 * partial index excludes only NULL/blank, not archived rows --
 * barcode-and-sku-contract.md) -- so no OTHER product could have taken
 * either value in the meantime. "Category eligibility" only applies to a
 * NEW assignment (M5.5.3); reactivation does not change categoryId, so
 * there is nothing there to revalidate either. Business ownership is
 * enforced structurally by `getById(companyId, ...)`.
 */
class ReactivateProductUseCase(private val productRepository: ProductRepository) {
    suspend fun execute(companyId: Long, productId: Long, nowEpochMillis: Long): DomainResult<Product> {
        val existing = productRepository.getById(companyId, productId)
            ?: return DomainResult.Failure(RepositoryError.NotFound("product", productId.toString()))
        if (existing.isActive) return DomainResult.Success(existing) // idempotent no-op
        productRepository.setActive(companyId, productId, true, nowEpochMillis)
        return DomainResult.Success(productRepository.getById(companyId, productId)!!)
    }
}

class GetProductUseCase(private val productRepository: ProductRepository) {
    suspend fun execute(companyId: Long, productId: Long): Product? = productRepository.getById(companyId, productId)
}

class ListProductsUseCase(private val productRepository: ProductRepository) {
    suspend fun execute(companyId: Long): List<Product> = productRepository.listActive(companyId)
}

class SearchProductsUseCase(
    private val productRepository: ProductRepository,
    private val normalizer: UnicodeTextNormalizer,
) {
    suspend fun execute(companyId: Long, query: String, limit: Long = 50): List<Product> {
        val normalizedPrefix = normalizer.normalizeForComparison(query.trim())
        if (normalizedPrefix.isEmpty()) return emptyList()
        return productRepository.searchActiveByNormalizedNamePrefix(companyId, normalizedPrefix, limit)
    }
}

/** Scan-time lookup -- active products only (matching `getByBarcode`'s own already-active-only query). */
class FindProductByBarcodeUseCase(private val productRepository: ProductRepository) {
    suspend fun execute(companyId: Long, barcode: String): Product? = productRepository.getByBarcode(companyId, barcode.trim())
}

/** M5.5.12 -- low-stock-definition.md's chosen policy: real rows, not just a count (unlike the legacy dashboard's own count-only query). */
class GetLowStockProductsUseCase(private val productRepository: ProductRepository) {
    suspend fun execute(companyId: Long) = productRepository.listLowStock(companyId)
}

/**
 * M5.5.6 -- product creation with opening stock, as one real transaction
 * (`ProductRepository.insertWithInitialStock`). Reuses the exact same
 * field validation `CreateProductUseCase` runs, plus one more: the target
 * branch must exist and be active -- "target Branch must be active."
 */
class CreateProductWithInitialStockUseCase(
    private val productRepository: ProductRepository,
    private val categoryRepository: CategoryRepository,
    private val branchRepository: BranchRepository,
    private val normalizer: UnicodeTextNormalizer,
) {
    suspend fun execute(
        companyId: Long,
        sku: String,
        barcode: String?,
        name: String,
        categoryId: String?,
        costPrice: Money,
        sellPrice: Money,
        taxRate: PercentageRate,
        unit: String,
        reorderLevel: Long,
        branchId: Long,
        initialStock: Quantity,
        createdBy: String,
        idempotencyKey: String?,
        nowEpochMillis: Long,
    ): DomainResult<Product> {
        val validName = (validateName(name) as? DomainResult.Success)?.value
            ?: return validateName(name) as DomainResult.Failure
        val validSku = (validateSku(sku) as? DomainResult.Success)?.value
            ?: return validateSku(sku) as DomainResult.Failure
        val barcodeResult = validateBarcode(barcode)
        if (barcodeResult is DomainResult.Failure) return barcodeResult
        val validBarcode = (barcodeResult as DomainResult.Success).value

        if (costPrice.isNegative()) return DomainResult.Failure(RepositoryError.ValidationFailed("product", "cost price must not be negative"))
        if (sellPrice.isNegative()) return DomainResult.Failure(RepositoryError.ValidationFailed("product", "sell price must not be negative"))

        val categoryCheck = validateCategoryAssignment(categoryRepository, companyId, categoryId)
        if (categoryCheck is DomainResult.Failure) return categoryCheck

        val branch = branchRepository.getById(companyId, branchId)
            ?: return DomainResult.Failure(RepositoryError.NotFound("branch", branchId.toString()))
        if (!branch.isActive) {
            return DomainResult.Failure(RepositoryError.ValidationFailed("product", "cannot receive opening stock into an archived branch"))
        }

        val normalizedName = normalizer.normalizeForComparison(validName)
        val trimmedUnit = unit.trim().ifEmpty { "pcs" }
        return productRepository.insertWithInitialStock(
            companyId, validSku, validBarcode, validName, normalizedName, categoryId,
            costPrice, sellPrice, taxRate, trimmedUnit, reorderLevel,
            branchId, initialStock, createdBy, idempotencyKey, nowEpochMillis,
        )
    }
}
