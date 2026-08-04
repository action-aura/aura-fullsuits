package com.actionaura.retail.data.model

import com.actionaura.retail.financial.Quantity

/** M5.5.8 -- one row per stock mutation, append-only (inventory-movement-authority-decision.md). */
data class InventoryMovement(
    val id: Long,
    val companyId: Long,
    val productId: Long,
    val branchId: Long?,
    val movementType: String,
    val quantity: Quantity,
    val quantityBefore: Quantity,
    val quantityAfter: Quantity,
    val reference: String?,
    val relatedSaleId: Long?,
    val relatedReturnId: Long?,
    val idempotencyKey: String?,
    val notes: String?,
    val createdBy: String,
    val createdAtEpochMillis: Long,
)
