package com.actionaura.retail.data.sqldelight

import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.InventoryRepository
import com.actionaura.retail.data.RepositoryError
import com.actionaura.retail.data.StockMovementDirection
import com.actionaura.retail.data.StockMovementReason
import com.actionaura.retail.data.model.InventoryMovement
import com.actionaura.retail.data.parseStoredQuantity
import com.actionaura.retail.db.Inventory_movements
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.financial.Quantity

/** M5.1/M5.5 -- real, SQLDelight-backed `InventoryRepository`. */
class SqlDelightInventoryRepository(private val db: RetailDatabase) : InventoryRepository {

    override suspend fun getStockOnHand(companyId: Long, productId: Long, branchId: Long): Quantity {
        val raw = db.inventoryQueries.selectStockOnHand(companyId, productId, branchId).executeAsOneOrNull()
        return raw?.let { parseStoredQuantity(it) } ?: Quantity.ZERO
    }

    override suspend fun ensureOpeningStock(companyId: Long, productId: Long, branchId: Long, opening: Quantity) {
        db.inventoryQueries.upsertOpeningStock(companyId, productId, branchId, opening.toString())
    }

    override suspend fun adjustStock(
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
    ): DomainResult<Quantity> = db.transactionWithResult {
        if (idempotencyKey != null) {
            val existing = db.inventoryQueries.selectMovementByIdempotencyKey(companyId, idempotencyKey).executeAsOneOrNull()
            if (existing != null) {
                val samePayload = existing.product_id == productId && existing.branch_id == branchId &&
                    existing.movement_type == reason.name && parseStoredQuantity(existing.quantity) == amount
                return@transactionWithResult if (samePayload) {
                    DomainResult.Success(parseStoredQuantity(existing.quantity_after))
                } else {
                    DomainResult.Failure(RepositoryError.IdempotencyConflict("inventory_movement", idempotencyKey))
                }
            }
        }

        db.inventoryQueries.upsertOpeningStock(companyId, productId, branchId, "0")
        val before = parseStoredQuantity(
            db.inventoryQueries.selectStockOnHand(companyId, productId, branchId).executeAsOneOrNull() ?: "0",
        )
        val after = when (direction) {
            StockMovementDirection.INCREASE -> before + amount
            StockMovementDirection.DECREASE -> {
                if (amount > before) {
                    return@transactionWithResult DomainResult.Failure(
                        RepositoryError.InsufficientStock(productId.toString(), before.toString(), amount.toString()),
                    )
                }
                before - amount
            }
        }
        db.inventoryQueries.decrementStock(after.toString(), companyId, productId, branchId)
        db.inventoryQueries.insertMovement(
            companyId, productId, branchId, reason.name, amount.toString(), before.toString(), after.toString(),
            reference, relatedSaleId, relatedReturnId, idempotencyKey, notes, createdBy, nowEpochMillis,
        )
        DomainResult.Success(after)
    }

    override suspend fun reconcileStock(
        companyId: Long,
        productId: Long,
        branchId: Long,
        countedQuantity: Quantity,
        notes: String?,
        createdBy: String,
        idempotencyKey: String?,
        nowEpochMillis: Long,
    ): DomainResult<Quantity> = db.transactionWithResult {
        if (idempotencyKey != null) {
            val existing = db.inventoryQueries.selectMovementByIdempotencyKey(companyId, idempotencyKey).executeAsOneOrNull()
            if (existing != null) {
                val samePayload = existing.product_id == productId && existing.branch_id == branchId &&
                    existing.movement_type == StockMovementReason.STOCK_TAKE_RECONCILE.name &&
                    parseStoredQuantity(existing.quantity_after) == countedQuantity
                return@transactionWithResult if (samePayload) {
                    DomainResult.Success(countedQuantity)
                } else {
                    DomainResult.Failure(RepositoryError.IdempotencyConflict("inventory_movement", idempotencyKey))
                }
            }
        }

        db.inventoryQueries.upsertOpeningStock(companyId, productId, branchId, "0")
        val before = parseStoredQuantity(
            db.inventoryQueries.selectStockOnHand(companyId, productId, branchId).executeAsOneOrNull() ?: "0",
        )
        val delta = if (countedQuantity >= before) countedQuantity - before else before - countedQuantity
        db.inventoryQueries.decrementStock(countedQuantity.toString(), companyId, productId, branchId)
        db.inventoryQueries.insertMovement(
            companyId, productId, branchId, StockMovementReason.STOCK_TAKE_RECONCILE.name,
            delta.toString(), before.toString(), countedQuantity.toString(),
            null, null, null, idempotencyKey, notes, createdBy, nowEpochMillis,
        )
        DomainResult.Success(countedQuantity)
    }

    override suspend fun listMovementHistory(companyId: Long, productId: Long, limit: Long): List<InventoryMovement> =
        db.inventoryQueries.selectMovementsForProduct(companyId, productId, limit).executeAsList().map { it.toDomain() }
}

private fun Inventory_movements.toDomain() = InventoryMovement(
    id = id, companyId = company_id, productId = product_id, branchId = branch_id, movementType = movement_type,
    quantity = parseStoredQuantity(quantity), quantityBefore = parseStoredQuantity(quantity_before), quantityAfter = parseStoredQuantity(quantity_after),
    reference = reference, relatedSaleId = related_sale_id, relatedReturnId = related_return_id, idempotencyKey = idempotency_key,
    notes = notes, createdBy = created_by, createdAtEpochMillis = created_at,
)
