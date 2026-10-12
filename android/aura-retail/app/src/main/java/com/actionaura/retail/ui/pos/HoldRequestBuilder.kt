package com.actionaura.retail.ui.pos

import com.actionaura.retail.net.HeldLine
import com.actionaura.retail.net.HoldSaleRequest
import com.actionaura.retail.net.Product

/** The server stores at most this much of a hold note (`label[:200]`). */
const val HOLD_LABEL_MAX = 200

/**
 * The `POST /held-sales` body for the phone's cart, in the shape the desktop's
 * `_saveHold` sends (items, customer_id, label, discount_pct, payment_method,
 * subtotal, total).
 *
 * Each line carries its EFFECTIVE discount percentage (own, else the whole-sale
 * one) so a sale held on the phone and resumed on the desktop keeps every
 * line's discount, and the top-level `discount_pct` carries the whole-sale one
 * the desktop puts back in its discount box. `unit_price` / `line_total` /
 * `subtotal` / `total` are DISPLAY-ONLY (the hold route says so itself and the
 * server re-prices at the real checkout); they exist so the resume list can
 * show an amount per parked sale.
 *
 * A product that is no longer in the loaded catalogue is still held (id and
 * quantity are what matter), with a zero display price, rather than being
 * dropped: losing a line from a customer's parked basket is worse than showing
 * a blank price on it.
 */
fun buildHoldRequest(
    cart: Map<String, Double>,
    products: Map<String, Product>,
    lineDiscounts: Map<String, Double>,
    salePct: Double,
    access: DiscountAccess,
    customerId: String?,
    label: String,
    singleMethod: String,
    displayTotal: Double,
    dp: Int,
): HoldSaleRequest {
    var subtotalMinor = 0L
    val lines = cart.entries.map { (id, qty) ->
        val p = products[id]
        val price = p?.sell_price ?: 0.0
        val pct = if (access == DiscountAccess.None) 0.0 else effectiveLinePct(lineDiscounts[id], salePct)
        val grossMinor = toMinor(price * qty, dp)
        subtotalMinor += grossMinor
        val lineMinor = toMinor(price * qty * (1.0 - pct / 100.0), dp)
        HeldLine(
            product_id = id, name = p?.name, quantity = qty, discount_pct = pct,
            unit_price = price, line_total = fromMinor(lineMinor, dp),
        )
    }
    return HoldSaleRequest(
        items = lines,
        customer_id = customerId,
        label = label.trim().take(HOLD_LABEL_MAX),
        discount_pct = if (access == DiscountAccess.None) 0.0 else clampDiscountPct(salePct),
        payment_method = singleMethod,
        subtotal = fromMinor(subtotalMinor, dp),
        total = displayTotal,
    )
}
