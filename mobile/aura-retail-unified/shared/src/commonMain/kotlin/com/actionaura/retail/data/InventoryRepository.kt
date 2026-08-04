package com.actionaura.retail.data

import com.actionaura.retail.data.model.InventoryMovement
import com.actionaura.retail.financial.Quantity

/**
 * M5.1/M5.5 -- direct stock-balance plumbing for flows OUTSIDE sale/return
 * finalization (manual stock takes, purchase-order receiving, opening
 * balances) -- `SaleRepository`/`ReturnRepository` own their own stock
 * mutation atomically as part of the sale/return transaction (M3's
 * transaction-boundary-audit.md) and never go through this interface.
 *
 * `Quantity` cannot represent a negative delta (M3's own invariant), so
 * direction and magnitude are passed separately. A DECREASE that would
 * take the balance below zero has no representable result -- there is no
 * "allow negative" option, it always fails with
 * `RepositoryError.InsufficientStock`.
 */
enum class StockMovementDirection { INCREASE, DECREASE }

/**
 * M5.5.7 -- real, named reasons (NOT free text, unlike the legacy
 * `movement_type` column -- product-inventory-authority-audit.md #3's own
 * cited gap: no enum/CHECK constraint restricts it there).
 */
enum class StockMovementReason {
    INITIAL_STOCK,
    MANUAL_RECEIPT,
    MANUAL_CORRECTION_INCREASE,
    MANUAL_CORRECTION_DECREASE,
    SALE,
    RETURN,
    IMPORT,
    MIGRATION,
    RESTORE_RECONCILIATION,
    STOCK_TAKE_RECONCILE,
}

interface InventoryRepository {
    suspend fun getStockOnHand(companyId: Long, productId: Long, branchId: Long): Quantity
    suspend fun ensureOpeningStock(companyId: Long, productId: Long, branchId: Long, opening: Quantity)

    /**
     * Atomic read-adjust-write against a lock-fresh balance, same TOCTOU
     * discipline as `BranchRepository.setActive`. `idempotencyKey`, when
     * supplied, makes a retry with the identical payload return the
     * original result instead of double-applying (same idempotent-retry
     * contract `InMemorySaleRepository` established in M3) -- a retry with
     * the SAME key but a DIFFERENT direction/amount/reason returns
     * `RepositoryError.IdempotencyConflict` rather than silently applying
     * the new payload.
     */
    suspend fun adjustStock(
        companyId: Long,
        productId: Long,
        branchId: Long,
        direction: StockMovementDirection,
        amount: Quantity,
        reason: StockMovementReason,
        reference: String?,
        relatedSaleId: Long?,
        relatedReturnId: Long?,
        idempotencyKey: String?,
        notes: String?,
        createdBy: String,
        nowEpochMillis: Long,
    ): DomainResult<Quantity>

    /**
     * M5.5.7 -- stock-take / physical-count reconciliation: sets on-hand to
     * a counted absolute value, but still expressed and audited as a
     * derived delta against the lock-fresh prior balance -- never a raw
     * overwrite (unlike the legacy importer's real, documented gap,
     * product-inventory-authority-audit.md #3). Confirmed absent from the
     * legacy authority entirely (#3/#6) -- a real
     * NEW_COMPLETE_PRODUCT_REQUIREMENT, not a port.
     */
    suspend fun reconcileStock(
        companyId: Long,
        productId: Long,
        branchId: Long,
        countedQuantity: Quantity,
        notes: String?,
        createdBy: String,
        idempotencyKey: String?,
        nowEpochMillis: Long,
    ): DomainResult<Quantity>

    suspend fun listMovementHistory(companyId: Long, productId: Long, limit: Long): List<InventoryMovement>
}
