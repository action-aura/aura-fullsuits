package com.actionaura.retail.net

import com.actionaura.retail.ui.pos.DiscountAccess
import com.actionaura.retail.ui.pos.buildHoldRequest
import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import org.junit.Test

/**
 * ANDROID-2a item 1: the hold request against what the DESKTOP's `_saveHold`
 * (frontend/subsystem-retail.js) sends to the same /held-sales route. The
 * desktop example is COPIED here and is the contract: a phone-only key, or a
 * missing one, is read differently (or not at all) by the Flask route.
 */
class HoldWireContractTest {

    private val gson = Gson()
    private fun json(o: Any): JsonObject = gson.toJsonTree(o).asJsonObject

    private val desktopHold: JsonObject = JsonParser.parseString(
        """
        {"items":[{"product_id":"p1","name":"Tea","quantity":2,"discount_pct":0}],
         "customer_id":"c-1","label":"Table 4","discount_pct":10,"payment_method":"cash",
         "subtotal":5,"total":4.5}
        """.trimIndent()
    ).asJsonObject

    @Test
    fun a_hold_request_has_exactly_the_desktops_top_level_keys() {
        val p = Product(id = "p1", name = "Tea", sell_price = 2.5)
        val body = json(buildHoldRequest(
            cart = linkedMapOf("p1" to 2.0), products = mapOf("p1" to p), lineDiscounts = emptyMap(),
            salePct = 10.0, access = DiscountAccess.Full, customerId = "c-1", label = "Table 4",
            singleMethod = "cash", displayTotal = 4.5, dp = 3,
        ))
        assertThat(body.keySet()).isEqualTo(desktopHold.keySet())
        assertThat(body.get("label").asString).isEqualTo("Table 4")
        assertThat(body.get("discount_pct").asDouble).isEqualTo(10.0)
        assertThat(body.get("subtotal").asDouble).isEqualTo(5.0)
        val line = body.getAsJsonArray("items")[0].asJsonObject
        assertThat(line.get("product_id").asString).isEqualTo("p1")
        assertThat(line.get("quantity").asDouble).isEqualTo(2.0)
        // effective percentage, so a desktop resume keeps every line's discount
        assertThat(line.get("discount_pct").asDouble).isEqualTo(10.0)
        assertThat(line.get("line_total").asDouble).isEqualTo(4.5)
        assertThat(line.keySet()).containsAtLeastElementsIn(desktopHold.getAsJsonArray("items")[0].asJsonObject.keySet())
    }

    @Test
    fun a_hold_note_is_optional_and_capped_at_what_the_server_stores() {
        val long = json(buildHoldRequest(
            linkedMapOf("p1" to 1.0), emptyMap(), emptyMap(), 0.0, DiscountAccess.Full,
            null, "x".repeat(500), "cash", 0.0, 3))
        assertThat(long.get("label").asString.length).isEqualTo(200)
        val blank = json(buildHoldRequest(
            linkedMapOf("p1" to 1.0), emptyMap(), emptyMap(), 0.0, DiscountAccess.Full,
            null, "   ", "cash", 0.0, 3))
        assertThat(blank.get("label").asString).isEmpty()
        assertThat(blank.has("customer_id")).isFalse()   // walk-in: server does `or None`
    }

    @Test
    fun without_discount_access_a_held_cart_carries_no_discount() {
        val body = json(buildHoldRequest(
            linkedMapOf("p1" to 1.0), mapOf("p1" to Product(id = "p1", sell_price = 2.0)),
            mapOf("p1" to 50.0), 20.0, DiscountAccess.None, null, "", "cash", 0.0, 3))
        assertThat(body.get("discount_pct").asDouble).isEqualTo(0.0)
        assertThat(body.getAsJsonArray("items")[0].asJsonObject.get("discount_pct").asDouble).isEqualTo(0.0)
    }

    @Test
    fun a_product_missing_from_the_catalogue_is_still_held() {
        val body = json(buildHoldRequest(
            linkedMapOf("gone" to 3.0), emptyMap(), emptyMap(), 0.0, DiscountAccess.Full,
            null, "", "cash", 0.0, 3))
        val line = body.getAsJsonArray("items")[0].asJsonObject
        assertThat(line.get("product_id").asString).isEqualTo("gone")
        assertThat(line.get("quantity").asDouble).isEqualTo(3.0)
    }
}
