package com.actionaura.retail.financial

/**
 * M3.3 -- pure cart state and calculation, no persistence. Mirrors the
 * real Python authority's per-line resolution shape (retail_api.py's
 * create_sale() resolved_lines accumulation) without any of the
 * transactional/persistence concerns, which live in usecases/FinalizeSale.kt
 * (M3.4).
 */
data class CartLine(
    val productId: String,
    val unitPrice: Money,
    val quantity: Quantity,
    val discountPct: PercentageRate,
    val taxRate: PercentageRate,
    val calculation: LineCalculation,
)

data class Cart(
    val lines: List<CartLine>,
    val mode: TaxMode,
) {
    val subtotal: Money get() = lines.fold(Money.ZERO) { acc, l -> acc + l.calculation.gross }
    val discountTotal: Money get() = lines.fold(Money.ZERO) { acc, l -> acc + l.calculation.discountAmount }
    val taxTotal: Money get() = lines.fold(Money.ZERO) { acc, l -> acc + l.calculation.tax }
    val total: Money get() = lines.fold(Money.ZERO) { acc, l -> acc + l.calculation.total }
}

/**
 * CreateCartLine / UpdateCartQuantity / ApplyLineDiscount are pure
 * transformations over Cart -- CalculateCart is just reading the derived
 * totals above, so it needs no separate function; it's the Cart's own
 * properties, matching the fact that Python never has a separate
 * "calculate cart" step distinct from resolving each line (create_sale
 * resolves+sums in the same loop).
 */
fun createCartLine(
    productId: String,
    unitPrice: Money,
    quantity: Quantity,
    discountPct: PercentageRate,
    taxRate: PercentageRate,
    mode: TaxMode,
): CartLine {
    val calc = calculateLine(unitPrice, quantity, discountPct, taxRate, mode)
    return CartLine(productId, unitPrice, quantity, discountPct, taxRate, calc)
}

fun Cart.addLine(line: CartLine): Cart = copy(lines = lines + line)

fun Cart.updateLineQuantity(productId: String, newQuantity: Quantity): Cart = copy(
    lines = lines.map { line ->
        if (line.productId == productId) {
            createCartLine(line.productId, line.unitPrice, newQuantity, line.discountPct, line.taxRate, mode)
        } else line
    }
)

fun Cart.applyLineDiscount(productId: String, discountPct: PercentageRate): Cart = copy(
    lines = lines.map { line ->
        if (line.productId == productId) {
            createCartLine(line.productId, line.unitPrice, line.quantity, discountPct, line.taxRate, mode)
        } else line
    }
)

fun Cart.removeLine(productId: String): Cart = copy(lines = lines.filterNot { it.productId == productId })

/**
 * ApplyOrderDiscount -- the cart/invoice-level discount model
 * (calculate_invoice's currency-amount discount, invariant #5), applied
 * across the cart's already-summed subtotal rather than per line. Returns
 * the InvoiceCalculation; callers combine this with the cart's own
 * per-line tax handling per product decision (M3.6 -- not currently
 * exercised by any real Python route, ported as a real, tested, existing
 * shape).
 */
fun Cart.applyOrderDiscount(discountAmount: Money, taxRatePct: PercentageRate): InvoiceCalculation =
    calculateInvoice(subtotal, discountAmount, taxRatePct, mode)
