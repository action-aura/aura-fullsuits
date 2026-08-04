package com.actionaura.retail.data.sqldelight

import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.InventoryRepository
import com.actionaura.retail.data.RepositoryError
import com.actionaura.retail.data.StockMovementDirection
import com.actionaura.retail.data.parseStoredQuantity
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.financial.Quantity

/** M5.1 -- real, SQLDelight-backed `InventoryRepository`. */
class SqlDelightInventoryRepository(private val db: RetailDatabase) : InventoryRepository {

    override suspend fun getStockOnHand(companyId: Long, productId: Long, branchId: Long): Quantity {
        val raw = db.inventoryQueries.selectStockOnHand(companyId, productId, branchId).executeAsOneOrNull()
        return raw?.let { parseStoredQuantity(it) } ?: Quantity.ZERO
    }

    override suspend fun ensureOpeningStock(companyId: Long, productId: Long, branchId: Long, opening: Quantity) {
        // INSERT OR IGNORE -- no-op if a balance row already exists, matching
        // the legacy create_product() semantics this mirrors
        // (sqlite-replace-safety-audit.md's upsertOpeningStock note).
        db.inventoryQueries.upsertOpeningStock(companyId, productId, branchId, opening.toString())
    }

    override suspend fun adjustStock(
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
    ): DomainResult<Quantity> = db.transactionWithResult {
        // Ensures a balance row exists (no-op if one already does), then
        // reads it back inside this same transaction -- a lock-fresh read
        // taken atomically with the write, same discipline as
        // BranchRepository.setActive and SaleRepository (M3's
        // transaction-boundary-audit.md).
        db.inventoryQueries.upsertOpeningStock(companyId, productId, branchId, "0")
        val current = parseStoredQuantity(
            db.inventoryQueries.selectStockOnHand(companyId, productId, branchId).executeAsOneOrNull() ?: "0",
        )
        val newQuantity = when (direction) {
            StockMovementDirection.INCREASE -> current + amount
            StockMovementDirection.DECREASE -> {
                if (amount > current) {
                    return@transactionWithResult DomainResult.Failure(
                        RepositoryError.InsufficientStock(productId.toString(), current.toString(), amount.toString()),
                    )
                }
                current - amount
            }
        }
        db.inventoryQueries.decrementStock(newQuantity.toString(), companyId, productId, branchId)
        db.inventoryQueries.insertMovement(
            companyId, productId, branchId, movementType,
            amount.toString(), reference, notes, createdBy, nowEpochMillis,
        )
        DomainResult.Success(newQuantity)
    }
}
