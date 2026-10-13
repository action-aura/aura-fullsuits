package com.actionaura.retail.ui.pos

import com.actionaura.retail.net.Product

/**
 * The phone's cart as a list of LINES, not a product-id -> quantity map.
 *
 * WHY THE MAP HAD TO GO (ANDROID-3). Everything a line can now carry is a
 * property of the LINE, not of the product: the modifier options picked for it
 * ("Latte, extra shot" and "Latte, oat milk" are two lines of one product), a
 * price of its own, a percentage of its own. A map keyed by product id can hold
 * one of each product, so a second modifier choice silently merged into the
 * first and a desktop cart with two such lines resumed on the phone as one.
 * The desktop's cart is a list whose merge key is the product id PLUS the sorted
 * option ids ([cartLineKey], the same string the desktop's `_cartLineKey`
 * builds); this is the phone's twin of it.
 *
 * NOTHING HERE PRICES A SALE. [lineUnitPrice] exists for the DISPLAY estimate
 * only (the same limit SaleEstimate.kt states): the server re-resolves every
 * modifier delta from `modifier_option_ids`, clamps, applies the override and
 * prices the line itself.
 *
 * Immutable: every operation returns a new list.
 */
data class ModifierPick(val group_name: String, val name: String, val price_delta: Double)

data class CartLine(
    val productId: String,
    val quantity: Double,
    val modifierOptionIds: List<String> = emptyList(),
    /** Presentational only (what was picked, with its price delta). The server's own snapshot is the record. */
    val modifiers: List<ModifierPick> = emptyList(),
    /** This line's own percentage (wins over the whole-sale one for this line; never stacked). */
    val ownPct: Double? = null,
    /** A price the cashier set for this line (needs `retail.discount`, or a manager's PIN). */
    val unitPriceOverride: Double? = null,
    /** Why the price was changed. The server refuses an override with no reason. */
    val priceReason: String? = null,
) {
    val key: String get() = cartLineKey(productId, modifierOptionIds)
}

/** Merge key of two lines: same product AND the same SET of options, order-independent. */
fun cartLineKey(productId: String, optionIds: List<String>): String =
    productId + "::" + optionIds.sorted().joinToString(",")

/** Result of an attempt to put more of a product in the cart. [accepted] false = the stock guard refused. */
data class CartChange(val lines: List<CartLine>, val accepted: Boolean)

/** Units of [productId] already in the cart, across every one of its lines. */
fun unitsInCart(lines: List<CartLine>, productId: String): Double =
    lines.filter { it.productId == productId }.sumOf { it.quantity }

/**
 * Add [qty] of a product (with an optional modifier choice). Refused -- nothing
 * changes -- when the cart would hold more of the PRODUCT (all its lines
 * together) than is on hand: two modifier lines of one product share one stock.
 */
fun addToCart(
    lines: List<CartLine>, productId: String, onHand: Double, qty: Double = 1.0,
    optionIds: List<String> = emptyList(), picks: List<ModifierPick> = emptyList(),
): CartChange {
    if (unitsInCart(lines, productId) + qty > onHand) return CartChange(lines, false)
    val key = cartLineKey(productId, optionIds)
    val at = lines.indexOfFirst { it.key == key }
    val out = if (at >= 0) {
        lines.mapIndexed { i, l -> if (i == at) l.copy(quantity = l.quantity + qty) else l }
    } else {
        lines + CartLine(productId, qty, optionIds.toList(), picks.toList())
    }
    return CartChange(out, true)
}

/** One more of an existing line (stock-guarded like [addToCart]). */
fun incrementLine(lines: List<CartLine>, key: String, onHand: Double): CartChange {
    val l = lines.firstOrNull { it.key == key } ?: return CartChange(lines, false)
    if (unitsInCart(lines, l.productId) + 1.0 > onHand) return CartChange(lines, false)
    return CartChange(lines.map { if (it.key == key) it.copy(quantity = it.quantity + 1.0) else it }, true)
}

/** One fewer of an existing line; at zero the line goes. */
fun decrementLine(lines: List<CartLine>, key: String): List<CartLine> =
    lines.mapNotNull { if (it.key != key) it else if (it.quantity - 1.0 <= 0.0) null else it.copy(quantity = it.quantity - 1.0) }

fun setOwnPct(lines: List<CartLine>, key: String, pct: Double?): List<CartLine> =
    lines.map { if (it.key == key) it.copy(ownPct = pct) else it }

/** Set (or clear, with a null [price]) a line's own price. A cleared price drops its reason too. */
fun setPriceOverride(lines: List<CartLine>, key: String, price: Double?, reason: String?): List<CartLine> =
    lines.map {
        if (it.key != key) it
        else if (price == null) it.copy(unitPriceOverride = null, priceReason = null)
        else it.copy(unitPriceOverride = price, priceReason = reason)
    }

/**
 * The price one unit of the line is charged at, for DISPLAY: `max(0, (override
 * or list price) + the picked options' deltas)` -- the order the server uses (an
 * override replaces the base; the extras still add on top; the result never goes
 * below zero).
 */
fun lineUnitPrice(basePrice: Double, line: CartLine): Double =
    maxOf(0.0, (line.unitPriceOverride ?: basePrice) + line.modifiers.sumOf { it.price_delta })

/** The product's list price plus its extras, with no override -- what the line would cost with no price of its own. */
fun lineListPrice(basePrice: Double, line: CartLine): Double =
    maxOf(0.0, basePrice + line.modifiers.sumOf { it.price_delta })

/**
 * The cart's shape as a string: product, options, quantity and price override of every line. A
 * discount worked out for one cart is only valid for that cart, so it remembers this and goes
 * the moment it changes (the desktop's `_cartDiscSig`).
 */
fun cartSignature(lines: List<CartLine>): String =
    lines.joinToString("|") { "${it.key}@${it.quantity}@${it.unitPriceOverride ?: ""}" }

/** Items count for the cart bar badge. */
fun cartItemCount(lines: List<CartLine>): Int = lines.sumOf { it.quantity }.toInt()

/** Display name of a line's product: the variant label is appended when the name does not already carry it. */
fun displayName(p: Product?): String {
    val name = p?.name ?: "—"
    val v = p?.variant_label?.takeIf { it.isNotBlank() } ?: return name
    return if (name.contains(v, ignoreCase = true)) name else "$name · $v"
}
