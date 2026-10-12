package com.actionaura.retail.net

import com.google.common.truth.Truth.assertThat
import com.google.gson.JsonParser
import org.junit.Test

/**
 * Reading a parked cart back. The `resume` body below is COPIED from what the
 * server's `resume_held_sale` returns for a snapshot the DESKTOP wrote
 * (`_saveHold` puts the whole cart line, with every desktop-only field, in
 * `items`), because that is the harder direction: a phone that can only read
 * its own holds cannot pick up a sale parked at the counter's other till.
 */
class HeldSalesResumeTest {

    private fun parse(json: String) = parseResumedCart(JsonParser.parseString(json).asJsonObject)

    @Test
    fun a_desktop_snapshot_becomes_the_phones_cart() {
        val cart = parse(
            """
            {"id":7,"hold_number":"HOLD-0007-ab12","label":"Table 4",
             "items":[{"product_id":"p1","name":"Tea","quantity":2,"sell_price":2.5,"discount_pct":0,
                       "modifiers":[],"line_total":5},
                      {"product_id":"p2","name":"Cake","quantity":1.5,"discount_pct":0}],
             "discount_pct":10,"payment_method":"card","customer_id":"c-1"}
            """.trimIndent()
        )
        assertThat(cart.quantities).containsExactly("p1", 2.0, "p2", 1.5).inOrder()
        assertThat(cart.salePct).isEqualTo(10.0)
        assertThat(cart.customerId).isEqualTo("c-1")
        assertThat(cart.paymentMethod).isEqualTo("card")
        assertThat(cart.holdNumber).isEqualTo("HOLD-0007-ab12")
    }

    @Test
    fun a_line_percentage_equal_to_the_sale_one_is_inherited_not_frozen_onto_the_line() {
        val cart = parse(
            """{"items":[{"product_id":"a","quantity":1,"discount_pct":10},
                         {"product_id":"b","quantity":1,"discount_pct":5}],
               "discount_pct":10}"""
        )
        // "a" follows the sale-wide 10%; only "b" carries its own 5%.
        assertThat(cart.lineDiscounts).containsExactly("b", 5.0)
    }

    @Test
    fun the_same_product_twice_is_merged_and_junk_lines_are_skipped() {
        val cart = parse(
            """{"items":[{"product_id":"a","quantity":1},{"product_id":"a","quantity":2},
                         {"quantity":3},{"product_id":"z","quantity":0},{"product_id":"y","quantity":-1},
                         "not-an-object"],"discount_pct":0}"""
        )
        assertThat(cart.quantities).containsExactly("a", 3.0)
    }

    @Test
    fun a_missing_or_empty_body_is_an_empty_cart_not_a_crash() {
        assertThat(parseResumedCart(null).quantities).isEmpty()
        assertThat(parse("{}").quantities).isEmpty()
        assertThat(parse("{}").paymentMethod).isEqualTo("cash")
        assertThat(parse("""{"items":"oops"}""").quantities).isEmpty()
    }

    @Test
    fun a_phone_hold_round_trips_through_its_own_resume_with_line_discounts_intact() {
        // hold: a=5% own, b inherits the sale 10%  ->  resume: same effective result.
        val hold = com.actionaura.retail.ui.pos.buildHoldRequest(
            cart = linkedMapOf("a" to 2.0, "b" to 1.0),
            products = mapOf("a" to Product(id = "a", name = "A", sell_price = 2.0), "b" to Product(id = "b", name = "B", sell_price = 3.0)),
            lineDiscounts = mapOf("a" to 5.0), salePct = 10.0,
            access = com.actionaura.retail.ui.pos.DiscountAccess.Full,
            customerId = null, label = "", singleMethod = "cash", displayTotal = 0.0, dp = 3,
        )
        val snapshot = com.google.gson.Gson().toJsonTree(
            mapOf("items" to hold.items, "discount_pct" to hold.discount_pct, "payment_method" to hold.payment_method)
        ).asJsonObject
        val back = parseResumedCart(snapshot)
        assertThat(back.quantities).containsExactly("a", 2.0, "b", 1.0).inOrder()
        assertThat(back.salePct).isEqualTo(10.0)
        assertThat(back.lineDiscounts).containsExactly("a", 5.0)
    }
}
