package com.actionaura.retail.data

import com.actionaura.retail.data.model.Product
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate
import com.actionaura.retail.financial.Quantity

/** M5.5.12 -- a product whose total on-hand quantity, summed across all branches, is at or below its reorder level (product-inventory-authority-audit.md #6: the legacy authority's own low_stock definition, ported as a real row list instead of just a count). */
data class LowStockProduct(val product: Product, val totalOnHandAcrossBranches: Quantity)

/**
 * M5.1/M5.5 -- thin, typed plumbing over the products table. Pricing/
 * reorder/barcode-dedup/optimistic-concurrency BUSINESS RULES are the
 * Milestone 5.5 use-case layer's job (product-domain-contract.md) --
 * `normalizedName` is accepted as a caller-supplied parameter here rather
 * than computed inside the repository, matching how `CategoryRepository`
 * never computes its own NFKC normalization either (that stays in
 * `CreateCategoryUseCase`, which owns the `UnicodeTextNormalizer`
 * dependency).
 */
interface ProductRepository {
    suspend fun listActive(companyId: Long): List<Product>
    suspend fun getById(companyId: Long, id: Long): Product?
    suspend fun getByBarcode(companyId: Long, barcode: String): Product?
    suspend fun getBySku(companyId: Long, sku: String): Product?

    /** Real-index-backed prefix search on the normalized name (products_normalized_name) -- product-domain-contract.md's documented search-strategy scope. */
    suspend fun searchActiveByNormalizedNamePrefix(companyId: Long, normalizedPrefix: String, limit: Long): List<Product>

    suspend fun listLowStock(companyId: Long): List<LowStockProduct>

    /**
     * `DuplicateValue` on `sku` or `barcode` is detected atomically inside
     * the same transaction as the write (same TOCTOU discipline as
     * `BranchRepository.setActive`) -- this is the real mechanism that
     * makes "concurrent duplicate SKU/barcode creation" resolve to exactly
     * one winner, not an app-level pre-check race like the legacy
     * authority's own (product-inventory-authority-audit.md #2/#5).
     */
    suspend fun insert(
        companyId: Long,
        sku: String,
        barcode: String?,
        name: String,
        normalizedName: String,
        categoryId: Long?,
        costPrice: Money,
        sellPrice: Money,
        taxRate: PercentageRate,
        unit: String,
        reorderLevel: Long,
        nowEpochMillis: Long,
    ): DomainResult<Product>

    /**
     * Optimistic-concurrency update: fails with `RepositoryError.StaleUpdate`
     * if `expectedUpdatedAtEpochMillis` no longer matches the row's current
     * `updated_at` (someone else updated it first), or
     * `RepositoryError.NotFound` if the row does not exist at all under this
     * company -- the repository disambiguates the two atomically inside one
     * transaction rather than leaving that to the caller.
     */
    suspend fun update(
        companyId: Long,
        id: Long,
        barcode: String?,
        name: String,
        normalizedName: String,
        categoryId: Long?,
        costPrice: Money,
        sellPrice: Money,
        taxRate: PercentageRate,
        unit: String,
        reorderLevel: Long,
        expectedUpdatedAtEpochMillis: Long,
        nowEpochMillis: Long,
    ): DomainResult<Product>

    suspend fun setActive(companyId: Long, id: Long, active: Boolean, nowEpochMillis: Long)

    /**
     * M5.5.6 -- product creation plus opening stock as ONE transaction
     * (spec's own explicit requirement). This lives on `ProductRepository`
     * rather than being composed from `insert()` + a separate
     * `InventoryRepository` call because the use-case layer has no access
     * to a shared transaction across two different repository interfaces
     * -- only a concrete repository implementation holding the real
     * `RetailDatabase` can wrap both writes atomically. `initialStock`
     * negative is structurally impossible (`Quantity` cannot hold a
     * negative value, M3's own invariant) -- "negative initial stock is
     * rejected" is therefore satisfied by the type system, not a runtime
     * check. `idempotencyKey` (optional) makes a retry return the
     * original product rather than double-creating.
     */
    suspend fun insertWithInitialStock(
        companyId: Long,
        sku: String,
        barcode: String?,
        name: String,
        normalizedName: String,
        categoryId: Long?,
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
    ): DomainResult<Product>
}
