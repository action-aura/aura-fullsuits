package com.actionaura.retail.net

import com.actionaura.retail.ui.pos.CartLine
import com.actionaura.retail.ui.pos.DiscountAccess
import com.actionaura.retail.ui.pos.ModifierPick
import com.actionaura.retail.ui.pos.buildHoldRequest
import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import com.google.gson.JsonParser
import org.junit.Test

/**
 * ANDROID-3 items 3 and 5: a held sale resumes WITH its extras, its own prices and its own percentages.
 * The `resume` body is what the server returns for a snapshot the DESKTOP wrote (`_saveHold` stores
 * `items: this._cart` - the whole cart line - and the route keeps it verbatim): COPIED, and the harder
 * direction, because a phone that can only read its own holds cannot pick up a sale parked at the counter.
 */
class HeldExtrasResumeTest {

    private fun parse(json: String) = parseResumedCart(JsonParser.parseString(json).asJsonObject)

    private val desktopHold = """
        {"id":7,"hold_number":"HOLD-0007-ab12","label":"Table 4",
         "items":[
           {"product_id":"p-latte","name":"Latte","quantity":1,"unit_price":3.0,"tax_rate":16,"line_total":3.0,"max_stock":40,
            "category_id":"c1","parent_product_id":null,
            "modifier_option_ids":["o-shot","o-oat"],
            "modifiers":[{"group_name":"Milk","name":"Oat","price_delta":0.5},{"group_name":"Extras","name":"Extra shot","price_delta":0.75}]},
           {"product_id":"p-latte","name":"Latte","quantity":2,"unit_price":2.5,"tax_rate":16,"line_total":5.0,"max_stock":40},
           {"product_id":"p-cake","name":"Cake","quantity":1,"unit_price":5.75,"tax_rate":16,"line_total":5.75,
            "unit_price_override":5.0,"price_reason":"Damaged","disc_pct":10}],
         "discount_pct":0,"payment_method":"cash","customer_id":null}
    """.trimIndent()

    @Test
    fun a_desktop_sale_with_extras_resumes_with_its_extras() {
        val c = parse(desktopHold)
        // two lines of one product with different choices stay two lines (they used to merge into one plain product)
        assertThat(c.lines.map { it.productId }).containsExactly("p-latte", "p-latte", "p-cake").inOrder()
        val withExtras = c.lines[0]
        assertThat(withExtras.modifierOptionIds).containsExactly("o-shot", "o-oat").inOrder()
        assertThat(withExtras.modifiers.map { it.name }).containsExactly("Oat", "Extra shot").inOrder()
        assertThat(withExtras.modifiers.sumOf { it.price_delta }).isWithin(1e-9).of(1.25)
        assertThat(c.lines[1].modifierOptionIds).isEmpty()
        assertThat(c.lines[1].quantity).isEqualTo(2.0)
    }

    @Test
    fun a_desktop_line_keeps_its_own_price_reason_and_percentage() {
        val cake = parse(desktopHold).lines[2]
        assertThat(cake.unitPriceOverride).isEqualTo(5.0)
        assertThat(cake.priceReason).isEqualTo("Damaged")
        assertThat(cake.ownPct).isEqualTo(10.0)
    }

    @Test
    fun the_same_product_with_the_same_extras_in_another_order_merges() {
        val c = parse("""{"items":[
            {"product_id":"a","quantity":1,"modifier_option_ids":["x","y"]},
            {"product_id":"a","quantity":2,"modifier_option_ids":["y","x"]}]}""")
        assertThat(c.lines).hasSize(1)
        assertThat(c.lines[0].quantity).isEqualTo(3.0)
    }

    @Test
    fun a_price_reason_without_a_price_is_dropped_and_a_negative_price_is_ignored() {
        val c = parse("""{"items":[{"product_id":"a","quantity":1,"price_reason":"stale"},
                                   {"product_id":"b","quantity":1,"unit_price_override":-3,"price_reason":"x"}]}""")
        assertThat(c.lines[0].priceReason).isNull()
        assertThat(c.lines[1].unitPriceOverride).isNull()
        assertThat(c.lines[1].priceReason).isNull()
    }

    @Test
    fun junk_in_the_extras_fields_does_not_crash_the_resume() {
        val c = parse("""{"items":[{"product_id":"a","quantity":1,"modifier_option_ids":"oops","modifiers":[1,"x",{"nope":1}]}]}""")
        assertThat(c.lines.single().modifierOptionIds).isEmpty()
        assertThat(c.lines.single().modifiers).isEmpty()
    }

    @Test
    fun a_phone_hold_with_extras_and_prices_resumes_identically_after_the_servers_verbatim_round_trip() {
        val lines = listOf(
            CartLine("p-latte", 2.0, listOf("o-oat"), listOf(ModifierPick("Milk", "Oat", 0.5)), ownPct = 5.0),
            CartLine("p-cake", 1.0, unitPriceOverride = 5.0, priceReason = "Damaged"),
        )
        val hold = buildHoldRequest(
            lines, mapOf("p-latte" to Product(id = "p-latte", name = "Latte", sell_price = 2.5),
                "p-cake" to Product(id = "p-cake", name = "Cake", sell_price = 5.75)),
            salePct = 10.0, access = DiscountAccess.Full, customerId = null, label = "", singleMethod = "cash",
            displayTotal = 0.0, dp = 3)
        val snapshot = Gson().toJsonTree(
            mapOf("items" to hold.items, "discount_pct" to hold.discount_pct, "payment_method" to hold.payment_method)
        ).asJsonObject
        val back = parseResumedCart(snapshot)
        assertThat(back.lines).isEqualTo(lines)
        assertThat(back.salePct).isEqualTo(10.0)
    }

    @Test
    fun resuming_does_not_freeze_the_sale_percentage_onto_lines_that_only_inherited_it() {
        // 2a's rule, kept: a phone-written line whose effective % equals the sale % is NOT its own.
        val c = parse("""{"items":[{"product_id":"a","quantity":1,"discount_pct":10},
                                   {"product_id":"b","quantity":1,"discount_pct":5,"disc_pct":5}],"discount_pct":10}""")
        assertThat(c.lines[0].ownPct).isNull()
        assertThat(c.lines[1].ownPct).isEqualTo(5.0)
    }
}
