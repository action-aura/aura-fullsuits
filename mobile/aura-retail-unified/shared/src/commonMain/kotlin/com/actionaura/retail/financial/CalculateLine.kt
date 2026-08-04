package com.actionaura.retail.financial

/**
 * M3.2 -- pure, deterministic port of core/retail/pricing.py's
 * calculate_line()/calculate_invoice(). No Android/iOS/SQL/filesystem/
 * clock/UI/network/localization dependency -- these are plain functions
 * over the Money/Quantity/PercentageRate types from M3.1.
 *
 * Exact formulas (financial-invariant-catalog.md invariant #1):
 *   gross = unitPrice * quantity
 *   discountAmount = gross * (discountPct / 100)
 *   AFTER_DISCOUNT (default): taxableAmount = gross - discountAmount; tax = taxableAmount * (taxRate/100); total = taxableAmount + tax
 *   BEFORE_DISCOUNT:          taxableAmount = gross;                  tax = gross * (taxRate/100);          total = gross - discountAmount + tax
 *
 * Every result field is independently rounded to 2dp via Money's own
 * construction rounding (matches Python's per-field `_money()` call on
 * every dict entry, not one blended rounding at the end).
 */
data class LineCalculation(
    val gross: Money,
    val discountAmount: Money,
    val taxableAmount: Money,
    val tax: Money,
    val total: Money,
)

fun calculateLine(
    unitPrice: Money,
    quantity: Quantity,
    discountPct: PercentageRate = PercentageRate.ZERO_RATE,
    taxRate: PercentageRate = PercentageRate.ZERO_RATE,
    mode: TaxMode = TaxMode.DEFAULT,
): LineCalculation {
    val gross = quantity * unitPrice
    val discountAmount = gross.timesFraction(discountPct.asFraction())

    return if (mode == TaxMode.BEFORE_DISCOUNT) {
        val taxableAmount = gross
        val tax = gross.timesFraction(taxRate.asFraction())
        val total = (gross - discountAmount) + tax
        LineCalculation(gross, discountAmount, taxableAmount, tax, total)
    } else {
        val taxableAmount = gross - discountAmount
        val tax = taxableAmount.timesFraction(taxRate.asFraction())
        val total = taxableAmount + tax
        LineCalculation(gross, discountAmount, taxableAmount, tax, total)
    }
}

/**
 * Cart/invoice-level variant -- mirrors calculate_invoice() exactly.
 * `discountAmount` here is already a currency amount (not a percentage),
 * clamped to [0, subtotal] -- a distinct discount model from
 * calculateLine's per-line percentage (financial-invariant-catalog.md
 * invariant #5). Not currently invoked by any real Python route, but a
 * real, tested, existing shape in the Python authority -- reserved for
 * the shared ApplyOrderDiscount command (M3.3).
 */
data class InvoiceCalculation(
    val discountAmount: Money,
    val taxableAmount: Money,
    val tax: Money,
    val total: Money,
)

fun calculateInvoice(
    subtotal: Money,
    discountAmount: Money,
    taxRatePct: PercentageRate,
    mode: TaxMode = TaxMode.DEFAULT,
): InvoiceCalculation {
    val clampedDiscount = when {
        discountAmount.isNegative() -> Money.ZERO
        discountAmount > subtotal -> subtotal
        else -> discountAmount
    }

    return if (mode == TaxMode.BEFORE_DISCOUNT) {
        val taxableAmount = subtotal
        val tax = subtotal.timesFraction(taxRatePct.asFraction())
        val total = (subtotal - clampedDiscount) + tax
        InvoiceCalculation(clampedDiscount, taxableAmount, tax, total)
    } else {
        val taxableAmount = subtotal - clampedDiscount
        val tax = taxableAmount.timesFraction(taxRatePct.asFraction())
        val total = taxableAmount + tax
        InvoiceCalculation(clampedDiscount, taxableAmount, tax, total)
    }
}

/** Mirrors create_sale()'s `change = max(0, paid - total)` (invariant #15) -- never negative. */
fun calculateChange(amountPaid: Money, total: Money): Money =
    (amountPaid - total).coerceAtLeastZero()
