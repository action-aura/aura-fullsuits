package com.actionaura.retail.net

import com.actionaura.retail.ui.pos.CartDiscount
import com.actionaura.retail.ui.pos.CartDiscountKind
import com.actionaura.retail.ui.pos.CartLine
import com.actionaura.retail.ui.pos.DiscountAccess
import com.actionaura.retail.ui.pos.ModifierPick
import com.actionaura.retail.ui.pos.buildCreateSaleRequest
import com.actionaura.retail.ui.pos.buildHoldRequest
import com.actionaura.retail.ui.pos.buildSaleItems
import com.actionaura.retail.ui.pos.saleDiscountReason
import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * ANDROID-3: what the phone sends for extras, a price of its own, a sale-level discount and a manager's
 * approval, against what the DESKTOP's `_checkout` sends (frontend/subsystem-retail.js ~10411-10466).
 * The desktop payload below is COPIED and is the contract: the Flask route reads exactly these names
 * (create_sale: `modifier_option_ids`, `unit_price_override`, `cart_discount_amount`, `cart_target_total`,
 * `discount_reason`, `approval`), so a renamed or invented key is a silently ignored one.
 */
class PosExtrasWireContractTest {

    private val gson = Gson()
    private fun json(o: Any): JsonObject = gson.toJsonTree(o).asJsonObject

    // The desktop, after "Round total down to 10.000" + a manager PIN, on a cart with one extra and one own price.
    private val desktopSale: JsonObject = JsonParser.parseString(
        """
        {"idempotency_key":"pos_1","customer_id":null,"subtotal":10.75,"discount_amount":0.65,"tax_amount":1.6,
         "total":10.0,"amount_paid":10.0,"payment_method":"cash","points_redeemed":0,
         "items":[{"product_id":"p-latte","name":"Latte","quantity":2,"unit_price":2.5,"tax_rate":16,"line_total":5,
                   "modifier_option_ids":["o-oat","o-shot"],"modifiers":[{"group_name":"Milk","name":"Oat","price_delta":0.5}],
                   "discount_pct":0},
                  {"product_id":"p-cake","name":"Cake","quantity":1,"unit_price":5.75,"tax_rate":16,"line_total":5.75,
                   "unit_price_override":5.0,"discount_pct":0}],
         "cart_target_total":10.0,"discount_reason":"Round down",
         "approval":{"approver":"u-123","pin":"1234"}}
        """.trimIndent()
    ).asJsonObject

    private val lines = listOf(
        CartLine("p-latte", 2.0, listOf("o-oat", "o-shot"), listOf(ModifierPick("Milk", "Oat", 0.5))),
        CartLine("p-cake", 1.0, unitPriceOverride = 5.0, priceReason = "Damaged"),
    )

    private fun sale(disc: CartDiscount? = null, approval: ApprovalReq? = null, ls: List<CartLine> = lines) = buildCreateSaleRequest(
        items = buildSaleItems(ls, 0.0, DiscountAccess.Full, cartDiscountOn = disc != null),
        customerId = null, idempotencyKey = "k", pointsRedeemed = 0, singleMethod = "cash", creditDownPayment = null,
        splitParts = emptyList(), dp = 3,
        cartDiscount = disc, discountReason = saleDiscountReason(disc, ls), approval = approval,
    )

    @Test
    fun an_extras_line_and_an_own_price_line_carry_the_desktops_keys() {
        val items = json(sale()).getAsJsonArray("items")
        val latte = items[0].asJsonObject
        assertThat(latte.getAsJsonArray("modifier_option_ids").map { it.asString }).containsExactly("o-oat", "o-shot").inOrder()
        assertThat(latte.has("unit_price_override")).isFalse()
        val cake = items[1].asJsonObject
        assertThat(cake.get("unit_price_override").asDouble).isEqualTo(5.0)
        assertThat(cake.has("modifier_option_ids")).isFalse()
        // every key the phone sends on a line is one the desktop sends too
        val desktopLine = desktopSale.getAsJsonArray("items")[0].asJsonObject.keySet() +
            desktopSale.getAsJsonArray("items")[1].asJsonObject.keySet()
        assertThat(desktopLine).containsAtLeastElementsIn(latte.keySet() + cake.keySet())
    }

    @Test
    fun a_plain_line_is_byte_for_byte_what_it_always_was() {
        val line = json(sale(ls = listOf(CartLine("p1", 1.0)))).getAsJsonArray("items")[0].asJsonObject
        assertThat(line.keySet()).containsExactly("product_id", "quantity", "discount_pct")
    }

    @Test
    fun a_target_total_sale_has_the_desktops_top_level_shape_and_zero_on_every_line() {
        val d = CartDiscount(CartDiscountKind.TARGET, 10_000, "Round down", "sig")
        val body = json(sale(d, ApprovalReq("u-123", "1234")))
        assertThat(body.get("cart_target_total").asDouble).isEqualTo(10.0)
        assertThat(body.get("discount_reason").asString).isEqualTo("Round down")
        assertThat(body.has("cart_discount_amount")).isFalse()        // the server refuses both at once
        assertThat(body.getAsJsonObject("approval")).isEqualTo(desktopSale.getAsJsonObject("approval"))
        body.getAsJsonArray("items").forEach { assertThat(it.asJsonObject.get("discount_pct").asDouble).isEqualTo(0.0) }
        assertThat(desktopSale.keySet()).containsAtLeastElementsIn(body.keySet())
    }

    @Test
    fun an_amount_sale_sends_the_amount_not_a_target() {
        val body = json(sale(CartDiscount(CartDiscountKind.AMOUNT, 650, "Damaged", "sig")))
        assertThat(body.get("cart_discount_amount").asDouble).isEqualTo(0.65)
        assertThat(body.has("cart_target_total")).isFalse()
        assertThat(body.has("approval")).isFalse()                    // nobody was asked
    }

    @Test
    fun an_ordinary_sale_has_none_of_the_new_keys() {
        val body = json(sale(ls = listOf(CartLine("p1", 1.0))))
        for (k in listOf("cart_discount_amount", "cart_target_total", "discount_reason", "approval")) {
            assertThat(body.has(k)).isFalse()
        }
    }

    @Test
    fun a_cart_discount_zeroes_a_stale_line_percentage_on_the_wire() {
        // Deny half: a line still carrying 5% in state while an amount discount is on must not ride along.
        val ls = listOf(CartLine("p1", 1.0, ownPct = 5.0))
        val items = buildSaleItems(ls, 10.0, DiscountAccess.Full, cartDiscountOn = true)
        assertThat(items.single().discount_pct).isEqualTo(0.0)
        // and without the cart discount the same line still goes at its own percentage (allow half)
        assertThat(buildSaleItems(ls, 10.0, DiscountAccess.Full).single().discount_pct).isEqualTo(5.0)
    }

    @Test
    fun the_pin_never_reaches_a_log_line_by_accident() {
        assertThat(ApprovalReq("u-123", "1234").toString()).doesNotContain("1234")
    }

    @Test
    fun the_reason_of_a_price_of_its_own_rides_on_the_sale() {
        assertThat(json(sale()).get("discount_reason").asString).isEqualTo("Damaged")
    }

    // ── the held snapshot: the server keeps `items` verbatim, so the extras survive parking ──────────
    @Test
    fun a_held_line_carries_extras_prices_and_its_own_percentage_in_the_desktops_names() {
        val ls = listOf(
            CartLine("p-latte", 2.0, listOf("o-oat"), listOf(ModifierPick("Milk", "Oat", 0.5)), ownPct = 5.0),
            CartLine("p-cake", 1.0, unitPriceOverride = 5.0, priceReason = "Damaged"),
        )
        val body = json(buildHoldRequest(
            ls, mapOf("p-latte" to Product(id = "p-latte", name = "Latte", sell_price = 2.5), "p-cake" to Product(id = "p-cake", name = "Cake", sell_price = 5.75)),
            0.0, DiscountAccess.Full, null, "", "cash", 0.0, 3))
        val latte = body.getAsJsonArray("items")[0].asJsonObject
        assertThat(latte.get("disc_pct").asDouble).isEqualTo(5.0)
        assertThat(latte.getAsJsonArray("modifier_option_ids")[0].asString).isEqualTo("o-oat")
        assertThat(latte.getAsJsonArray("modifiers")[0].asJsonObject.keySet()).containsExactly("group_name", "name", "price_delta")
        assertThat(latte.get("unit_price").asDouble).isEqualTo(3.0)             // 2.5 + the 0.5 extra
        val cake = body.getAsJsonArray("items")[1].asJsonObject
        assertThat(cake.get("unit_price_override").asDouble).isEqualTo(5.0)
        assertThat(cake.get("price_reason").asString).isEqualTo("Damaged")
        assertThat(cake.has("disc_pct")).isFalse()
    }

    // ── the server still speaks these names (a rename there would leave the phone sending ghosts) ───
    @Test
    fun the_server_reads_the_names_the_phone_sends_and_serves_the_routes_it_calls() {
        val f = File("../../../products/retail/backend/api/retail_api.py")
        assumeTrue("retail_api.py not reachable from this run context", f.exists())
        val py = f.readText()
        for (needle in listOf(
            "data.get('cart_discount_amount')", "data.get('cart_target_total')", "data.get('approval')",
            "i.get('unit_price_override')", "'/products/<string:pid>/modifier-groups'", "'/products/<string:pid>/variants'",
            "'/discount/approvers'", "'code': 'DISCOUNT_REASON_REQUIRED'", "'APPROVAL_REJECTED'", "'PIN_LOCKED'",
        )) assertThat(py).contains(needle)
        assertThat(py).contains("'items': items,")      // hold_sale stores the cart lines as sent
        assertThat(py).contains("modifier_option_ids")
    }
}
