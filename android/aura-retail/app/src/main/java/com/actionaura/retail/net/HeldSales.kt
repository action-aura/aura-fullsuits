package com.actionaura.retail.net

import com.google.gson.JsonArray
import com.actionaura.retail.ui.pos.CartLine
import com.actionaura.retail.ui.pos.ModifierPick
import com.google.gson.JsonObject

/**
 * Held (parked) sales -- the server's `/held-sales` routes, the SAME ones the
 * desktop till uses, so a sale parked on the phone can be resumed on the
 * desktop and the other way round. The server owns the state; the phone used to
 * keep parked carts in a process-wide list that died with the app and was
 * invisible to every other device.
 *
 * Contract (frontend/subsystem-retail.js `_saveHold` / `_resumeHeldSale`):
 *   POST   held-sales            {items, customer_id, label, discount_pct,
 *                                 payment_method, subtotal, total}
 *   GET    held-sales            rows {id, hold_number, label, item_count,
 *                                 subtotal, total, customer_id, customer_name,
 *                                 held_by, created_at}
 *   POST   held-sales/{id}/resume  -> {items, discount_pct, payment_method,
 *                                 customer_id, hold_number, label}  AND the
 *                                 held row is DELETED in the same transaction
 *   DELETE held-sales/{id}
 *
 * `subtotal` and `total` are DISPLAY-ONLY figures for the resume list (the
 * route's own comment says so); a held cart is re-priced by the server when it
 * is finally charged. `items` lines carry product_id + quantity (+ the
 * display fields name / unit_price / line_total and a per-line discount_pct).
 */
data class HeldLine(
    val product_id: String,
    val name: String? = null,
    val quantity: Double,
    val discount_pct: Double = 0.0,
    val unit_price: Double = 0.0,
    val line_total: Double = 0.0,
    // ANDROID-3: the rest of the desktop's cart line. The server stores `items` VERBATIM (hold_sale
    // keeps the list as sent, resume hands it back), so these survive the round trip and a desktop
    // resume reads them too. Null = key omitted, a plain line is unchanged.
    /** A percentage of this line's OWN (the desktop's `disc_pct`); `discount_pct` above stays the effective one. */
    val disc_pct: Double? = null,
    val modifier_option_ids: List<String>? = null,
    val modifiers: List<ModifierPick>? = null,
    val unit_price_override: Double? = null,
    val price_reason: String? = null,
)

data class HoldSaleRequest(
    val items: List<HeldLine>,
    val customer_id: String? = null,
    val label: String = "",
    val discount_pct: Double = 0.0,
    val payment_method: String = "cash",
    val subtotal: Double = 0.0,
    val total: Double = 0.0,
)

data class HeldSaleRow(
    val id: Int = 0,
    val hold_number: String? = null,
    val label: String? = null,
    val item_count: Int = 0,
    val subtotal: Double = 0.0,
    val total: Double = 0.0,
    val customer_id: String? = null,
    val customer_name: String? = null,
    val held_by: String? = null,
    val created_at: String? = null,
)

data class HeldSalesResponse(val status: String = "", val data: List<HeldSaleRow> = emptyList())
data class HoldSaleResult(val id: Int = 0, val hold_number: String? = null, val item_count: Int = 0)
data class HoldSaleResponse(val status: String = "", val message: String? = null, val data: HoldSaleResult? = null)
data class ResumeHeldResponse(val status: String = "", val message: String? = null, val data: JsonObject? = null)
data class SimpleStatusResponse(val status: String = "", val message: String? = null)

/** A parked cart read back, in the phone's own cart shape. */
data class ResumedCart(
    val lines: List<CartLine>,
    val salePct: Double,
    val customerId: String?,
    val paymentMethod: String,
    val holdNumber: String?,
)

/**
 * Read a `resume` response body into a cart.
 *
 * Tolerant on purpose: the snapshot may have been written by the desktop (whose
 * lines carry many more fields) or by an older build. A line without a product
 * id or a positive quantity is skipped.
 *
 * ANDROID-3: a line keeps everything the desktop's cart line carries that the
 * phone understands -- `modifier_option_ids` + `modifiers` (the extras), the
 * price of its own (`unit_price_override`, `price_reason`) and its own
 * percentage (`disc_pct`). Two lines of one product with DIFFERENT extras stay
 * two lines; the same product with the same extras is merged (that is the
 * desktop's own merge key). Before this a desktop sale with extras resumed on
 * the phone as plain products.
 *
 * Own percentage: the desktop's `disc_pct` when present; otherwise a phone-written
 * (or older) line's `discount_pct` when it differs from the whole-sale one --
 * a line that merely follows the whole-sale percentage is "inherited" and not
 * recorded as the line's own, so resuming then holding again does not freeze a
 * whole-sale discount onto each line.
 */
fun parseResumedCart(data: JsonObject?): ResumedCart {
    val salePct = data.number("discount_pct") ?: 0.0
    val out = ArrayList<CartLine>()
    val items: JsonArray = data?.get("items")?.takeIf { it.isJsonArray }?.asJsonArray ?: JsonArray()
    for (el in items) {
        val line = el.takeIf { it.isJsonObject }?.asJsonObject ?: continue
        val id = line.get("product_id")?.takeIf { it.isJsonPrimitive }?.asString?.takeIf { it.isNotBlank() } ?: continue
        val q = line.number("quantity") ?: continue
        if (q <= 0.0) continue
        val optionIds = line.get("modifier_option_ids")?.takeIf { it.isJsonArray }?.asJsonArray
            ?.mapNotNull { it.takeIf { e -> e.isJsonPrimitive }?.asString?.takeIf { s -> s.isNotBlank() } } ?: emptyList()
        val picks = line.get("modifiers")?.takeIf { it.isJsonArray }?.asJsonArray
            ?.mapNotNull { e ->
                val m = e.takeIf { it.isJsonObject }?.asJsonObject ?: return@mapNotNull null
                val name = m.get("name")?.takeIf { it.isJsonPrimitive }?.asString ?: return@mapNotNull null
                ModifierPick(m.get("group_name")?.takeIf { it.isJsonPrimitive }?.asString ?: "", name, m.number("price_delta") ?: 0.0)
            } ?: emptyList()
        val pct = line.number("disc_pct") ?: line.number("discount_pct")?.takeIf { kotlin.math.abs(it - salePct) > 1e-9 }
        val override = line.number("unit_price_override")?.takeIf { it >= 0.0 }
        val parsed = CartLine(
            productId = id, quantity = q, modifierOptionIds = optionIds, modifiers = picks,
            ownPct = pct?.takeIf { it > 0.0 }, unitPriceOverride = override,
            priceReason = if (override != null) line.get("price_reason")?.takeIf { it.isJsonPrimitive }?.asString else null,
        )
        val at = out.indexOfFirst { it.key == parsed.key }
        if (at >= 0) out[at] = out[at].copy(quantity = out[at].quantity + q) else out += parsed
    }
    return ResumedCart(
        lines = out,
        salePct = salePct,
        customerId = data?.get("customer_id")?.takeIf { it.isJsonPrimitive }?.asString?.takeIf { it.isNotBlank() },
        paymentMethod = data?.get("payment_method")?.takeIf { it.isJsonPrimitive }?.asString ?: "cash",
        holdNumber = data?.get("hold_number")?.takeIf { it.isJsonPrimitive }?.asString,
    )
}

private fun JsonObject?.number(key: String): Double? {
    val e = this?.get(key) ?: return null
    if (!e.isJsonPrimitive) return null
    val p = e.asJsonPrimitive
    return if (p.isNumber) p.asDouble else p.asString.toDoubleOrNull()
}
