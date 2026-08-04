package com.actionaura.retail.data

import com.actionaura.retail.financial.Quantity

/**
 * M5.1 -- direct stock-balance plumbing for flows OUTSIDE sale/return
 * finalization (manual stock takes, purchase-order receiving, opening
 * balances) -- `SaleRepository`/`ReturnRepository` own their own stock
 * mutation atomically as part of the sale/return transaction (M3's
 * transaction-boundary-audit.md) and never go through this interface.
 *
 * `Quantity` cannot represent a negative delta (M3's own invariant --
 * Quantity.parse/zeroOrMore both reject negative values), so direction and
 * magnitude are passed separately rather than as a signed quantity. For
 * the same reason a DECREASE that would take the balance below zero has no
 * representable result -- there is no "allow negative" option, it always
 * fails with `RepositoryError.InsufficientStock`.
 */
enum class StockMovementDirection { INCREASE, DECREASE }

interface InventoryRepository {
    suspend fun getStockOnHand(companyId: Long, productId: Long, branchId: Long): Quantity
    suspend fun ensureOpeningStock(companyId: Long, productId: Long, branchId: Long, opening: Quantity)

    /** Atomic read-adjust-write against a lock-fresh balance, same TOCTOU discipline as `BranchRepository.setActive`. */
    suspend fun adjustStock(
        companyId: Long,
        productId: Long,
        branchId: Long,
        direction: StockMovementDirection,
        amount: Quantity,
        movementType: String,
        reference: String?,
        notes: String?,
        createdBy: String,
        nowEpochMillis: Long,
    ): DomainResult<Quantity>
}
