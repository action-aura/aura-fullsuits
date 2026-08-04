package com.actionaura.retail.usecases.inventory

import com.actionaura.retail.data.BranchRepository
import com.actionaura.retail.data.DomainResult
import com.actionaura.retail.data.InventoryRepository
import com.actionaura.retail.data.ProductRepository
import com.actionaura.retail.data.RepositoryError
import com.actionaura.retail.data.StockMovementDirection
import com.actionaura.retail.data.StockMovementReason
import com.actionaura.retail.data.model.InventoryMovement
import com.actionaura.retail.financial.Quantity

/**
 * M5.5.7/M5.5.12 -- the Inventory domain's use-case layer, one layer above
 * `InventoryRepository` (M5.1/M5.5). Validates product/branch existence
 * and active state before delegating to the repository's own atomic
 * mutation -- the repository itself only knows about ids, not what an
 * archived product/branch means (inventory-mutation-contract.md).
 */

class GetProductInventoryUseCase(private val inventoryRepository: InventoryRepository) {
    suspend fun execute(companyId: Long, productId: Long, branchId: Long): Quantity =
        inventoryRepository.getStockOnHand(companyId, productId, branchId)
}

/**
 * M5.5.7 -- every stock change goes through this one command; callers
 * choose a direction + magnitude, never an authoritative post-mutation
 * value (the governing spec's own explicit prohibition: "Do not allow
 * callers to supply arbitrary authoritative post-mutation stock").
 */
class AdjustInventoryUseCase(
    private val inventoryRepository: InventoryRepository,
    private val productRepository: ProductRepository,
    private val branchRepository: BranchRepository,
) {
    suspend fun execute(
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
    ): DomainResult<Quantity> {
        productRepository.getById(companyId, productId)
            ?: return DomainResult.Failure(RepositoryError.NotFound("product", productId.toString()))
        val branch = branchRepository.getById(companyId, branchId)
            ?: return DomainResult.Failure(RepositoryError.NotFound("branch", branchId.toString()))
        if (!branch.isActive) {
            return DomainResult.Failure(RepositoryError.ValidationFailed("inventory", "cannot mutate stock in an archived branch"))
        }
        return inventoryRepository.adjustStock(
            companyId, productId, branchId, direction, amount, reason,
            reference, relatedSaleId, relatedReturnId, idempotencyKey, notes, createdBy, nowEpochMillis,
        )
    }
}

/** M5.5.7 -- stock-take / physical-count reconciliation, real named requirement absent from the legacy authority (product-inventory-authority-audit.md #3/#6). */
class ReconcileInventoryUseCase(
    private val inventoryRepository: InventoryRepository,
    private val productRepository: ProductRepository,
    private val branchRepository: BranchRepository,
) {
    suspend fun execute(
        companyId: Long,
        productId: Long,
        branchId: Long,
        countedQuantity: Quantity,
        notes: String?,
        createdBy: String,
        idempotencyKey: String?,
        nowEpochMillis: Long,
    ): DomainResult<Quantity> {
        productRepository.getById(companyId, productId)
            ?: return DomainResult.Failure(RepositoryError.NotFound("product", productId.toString()))
        val branch = branchRepository.getById(companyId, branchId)
            ?: return DomainResult.Failure(RepositoryError.NotFound("branch", branchId.toString()))
        if (!branch.isActive) {
            return DomainResult.Failure(RepositoryError.ValidationFailed("inventory", "cannot reconcile stock in an archived branch"))
        }
        return inventoryRepository.reconcileStock(companyId, productId, branchId, countedQuantity, notes, createdBy, idempotencyKey, nowEpochMillis)
    }
}

class GetMovementHistoryUseCase(private val inventoryRepository: InventoryRepository) {
    suspend fun execute(companyId: Long, productId: Long, limit: Long = 100): List<InventoryMovement> =
        inventoryRepository.listMovementHistory(companyId, productId, limit)
}
