package com.actionaura.retail.usecases

import com.actionaura.retail.financial.PercentageRate

/**
 * M3.3 -- shared sale-finalization command. Mirrors the real Python
 * authority's server-authoritative contract exactly (invariant #6):
 * the client may only supply commercial intent -- productId + quantity
 * (+ optional discountPct) per line, payment tender, customer/branch
 * context, and an idempotency key. unitPrice/taxRate/totals are never
 * accepted here at all (there is no field for them) -- they are always
 * resolved from the product repository inside the transactional service
 * (M3.4), never trusted from the caller.
 */
data class SaleLineRequest(
    val productId: String,
    val quantityRaw: String, // parsed to Quantity inside the service, per invariant #7/#7a
    val discountPctRaw: Double = 0.0, // clamped via PercentageRate.clampToDiscountRange, per invariant #3
)

data class FinalizeSaleCommand(
    val lines: List<SaleLineRequest>,
    val branchId: String,
    val customerId: String?,
    val paymentMethod: String,
    val amountPaidRaw: String?, // null -> defaults to exactly `total`, per invariant #14
    val idempotencyKey: String?,
)

data class ReturnLineRequest(
    val productId: String,
    val quantityRaw: String,
)

data class FinalizeReturnCommand(
    val saleId: String,
    val lines: List<ReturnLineRequest>,
    val reason: String,
    val refundMethod: String,
    val idempotencyKey: String?,
)
