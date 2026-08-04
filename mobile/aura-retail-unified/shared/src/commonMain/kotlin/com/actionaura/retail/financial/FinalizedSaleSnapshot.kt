package com.actionaura.retail.financial

/**
 * M3.3 -- the immutable financial snapshot a finalized sale must persist,
 * sufficient to recreate its receipt later even if the live product's
 * name/price/tax/discount settings change or the product is archived.
 *
 * Real finding (financial-invariant-catalog.md invariant #20): the actual
 * Python authority's `sale_items` table does NOT snapshot the product's
 * display name -- receipts/history rely on a live join to `products.name`.
 * This type deliberately includes `productNameAtSale`, closing that real
 * gap (CANONICAL_UNIFIED) -- every other field here (unitPrice,
 * discountPct, taxRate, line totals) mirrors the real, already-correct
 * `sale_items` schema exactly (LEGACY_PARITY).
 */
data class FinalizedSaleLineSnapshot(
    val productId: String,
    val productNameAtSale: String, // CANONICAL_UNIFIED -- real gap closed, see above
    val quantity: Quantity,
    val unitPriceAtSale: Money,
    val discountPctAtSale: PercentageRate,
    val taxRateAtSale: PercentageRate,
    val calculation: LineCalculation,
)

data class FinalizedSaleSnapshot(
    val saleId: String,
    val saleNumber: String,
    val branchId: String,
    val customerId: String?,
    val lines: List<FinalizedSaleLineSnapshot>,
    val subtotal: Money,
    val discountAmount: Money,
    val taxAmount: Money,
    val total: Money,
    val amountPaid: Money,
    val change: Money,
    val balanceDue: Money,
    val paymentMethod: String,
    val currency: CurrencyCode,
    val mode: TaxMode,
    val createdAtEpochMillis: Long,
    val idempotencyKey: String?,
)

/** M3.3 -- refund figures always derived from the ORIGINAL sale's snapshot, never live product data (invariant #16). */
data class FinalizedReturnLineSnapshot(
    val productId: String,
    val productNameAtSale: String,
    val quantity: Quantity,
    val unitPriceAtSale: Money, // copied from the original FinalizedSaleLineSnapshot, not re-resolved
    val refundAmount: Money,
)

data class FinalizedReturnSnapshot(
    val returnId: String,
    val returnNumber: String,
    val saleId: String,
    val branchId: String,
    val lines: List<FinalizedReturnLineSnapshot>,
    val refundTotal: Money,
    val refundMethod: String,
    val reason: String,
    val createdAtEpochMillis: Long,
    val idempotencyKey: String?,
)
