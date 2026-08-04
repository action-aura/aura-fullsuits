package com.actionaura.retail.usecases

import com.actionaura.retail.financial.Cart

/**
 * M5.5 mandatory follow-up (Cart Branch identity) -- the real mechanism
 * that makes "UI parameters cannot replace the Cart's Branch identity" an
 * enforced guarantee rather than an aspiration: this is the ONLY sanctioned
 * way to build a `FinalizeSaleCommand` from a `Cart`, and `branchId` is
 * always read from `cart.branchId` -- there is no separate `branchId`
 * parameter here for a caller to substitute a different value. A future
 * `SaleRepository` (M5.5.9/M5.5.10's own documented scope boundary --
 * still not built this milestone) revalidates that branch's active state
 * fresh at finalization time regardless (the same real primitive
 * `AdjustInventoryUseCase`/`BranchRepository.getById(...).isActive` already
 * proves, `sale-return-inventory-integration.md`); this function's own
 * contribution is specifically that the branch identifier reaching that
 * check can never silently diverge from the cart's own.
 */
fun Cart.toFinalizeSaleCommand(
    customerId: String?,
    paymentMethod: String,
    amountPaidRaw: String?,
    idempotencyKey: String?,
): FinalizeSaleCommand = FinalizeSaleCommand(
    lines = lines.map { line -> SaleLineRequest(line.productId, line.quantity.toString(), line.discountPct.toString().toDouble()) },
    branchId = branchId.toString(),
    customerId = customerId,
    paymentMethod = paymentMethod,
    amountPaidRaw = amountPaidRaw,
    idempotencyKey = idempotencyKey,
)
