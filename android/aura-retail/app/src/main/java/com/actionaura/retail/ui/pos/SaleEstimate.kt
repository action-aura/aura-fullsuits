package com.actionaura.retail.ui.pos

import java.math.BigDecimal
import java.math.RoundingMode

/**
 * A DISPLAY-ONLY estimate of what the cart will come to at checkout, used by
 * the split-payment screen to show "Remaining" before the server has priced
 * the sale.
 *
 * WHY THE PHONE HAS ANY ARITHMETIC AT ALL HERE, given RetailScreens.kt's long
 * argument against a second pricing engine on the client. Split tender is the
 * one place a cashier must be told "how much is left" BEFORE the sale exists:
 * the parts are typed first and the server prices the sale when they are sent.
 * Without an estimate the screen could not say Remaining, enable Charge, or
 * compute change, and the cashier would learn the figure from a refusal.
 *
 * THE LIMITS, stated so nobody mistakes this for the till's total:
 *  - It follows the server's DEFAULT mode (tax after discount, each of gross /
 *    discount / tax rounded once, half-up -- core/retail/pricing.py::
 *    calculate_line). A shop on tax-before-discount, a promotion, a manager's
 *    quotation or a loyalty redemption the phone has not mirrored can make the
 *    server's number differ.
 *  - Nothing sent to the server is derived from it: the request carries
 *    product ids, quantities, discount percentages and the parts the cashier
 *    typed; the server computes the real total and settles the parts against
 *    THAT. If the two differ the server refuses a short payment with its own
 *    message (TENDERS_SHORT names the exact amount still missing) or returns
 *    the extra cash as change -- the estimate can never charge anyone.
 *  - The screen labels it "Estimated", never "Total"
 *    (CartTotalHonestyContractTest keeps that word for the server's figure).
 */
data class EstimateLine(
    val unitPrice: Double,
    val quantity: Double,
    val discountPct: Double,
    val taxRatePct: Double,
)

fun estimateDueMinor(lines: List<EstimateLine>, dp: Int): Long {
    val hundred = BigDecimal(100)
    var total = BigDecimal.ZERO
    for (l in lines) {
        val gross = (BigDecimal(l.unitPrice.toString()) * BigDecimal(l.quantity.toString()))
            .setScale(dp, RoundingMode.HALF_UP)
        val pct = BigDecimal(clampDiscountPct(l.discountPct).toString())
        val discount = (gross * pct / hundred).setScale(dp, RoundingMode.HALF_UP)
        val taxable = gross - discount
        val tax = (taxable * BigDecimal(l.taxRatePct.toString()) / hundred).setScale(dp, RoundingMode.HALF_UP)
        total += taxable + tax
    }
    return total.movePointRight(dp).setScale(0, RoundingMode.HALF_UP).toLong()
}
