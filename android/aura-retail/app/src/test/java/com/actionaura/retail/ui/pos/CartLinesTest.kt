package com.actionaura.retail.ui.pos

import com.actionaura.retail.net.Product
import com.google.common.truth.Truth.assertThat
import org.junit.Test

class CartLinesTest {

    private val milk = ModifierPick("Milk", "Oat", 0.5)
    private val shot = ModifierPick("Extras", "Extra shot", 0.75)

    @Test
    fun the_same_product_with_the_same_options_merges_in_any_order() {
        val a = addToCart(emptyList(), "p1", 10.0, optionIds = listOf("o2", "o1"), picks = listOf(shot, milk))
        val b = addToCart(a.lines, "p1", 10.0, optionIds = listOf("o1", "o2"), picks = listOf(milk, shot))
        assertThat(b.lines).hasSize(1)
        assertThat(b.lines[0].quantity).isEqualTo(2.0)
    }

    @Test
    fun a_different_option_choice_is_a_different_line() {
        val a = addToCart(emptyList(), "p1", 10.0, optionIds = listOf("o1"), picks = listOf(milk))
        val b = addToCart(a.lines, "p1", 10.0, optionIds = listOf("o2"), picks = listOf(shot))
        val c = addToCart(b.lines, "p1", 10.0)
        assertThat(c.lines.map { it.key }).containsExactly("p1::o1", "p1::o2", "p1::").inOrder()
    }

    @Test
    fun the_desktops_line_key_has_the_same_shape() {
        // subsystem-retail.js _cartLineKey: `${productId}::${sorted ids joined by ","}`
        assertThat(cartLineKey("abc", listOf("z", "a"))).isEqualTo("abc::a,z")
        assertThat(cartLineKey("abc", emptyList())).isEqualTo("abc::")
    }

    @Test
    fun stock_is_shared_by_all_lines_of_a_product_allow_and_deny() {
        val a = addToCart(emptyList(), "p1", 2.0, optionIds = listOf("o1"), picks = listOf(milk))
        val b = addToCart(a.lines, "p1", 2.0)                       // 2 of 2: allowed
        assertThat(b.accepted).isTrue()
        val c = addToCart(b.lines, "p1", 2.0, optionIds = listOf("o2"), picks = listOf(shot))   // a third unit: refused
        assertThat(c.accepted).isFalse()
        assertThat(c.lines).isEqualTo(b.lines)
        assertThat(incrementLine(b.lines, "p1::", 2.0).accepted).isFalse()
        assertThat(incrementLine(b.lines, "p1::", 3.0).accepted).isTrue()
    }

    @Test
    fun decrementing_the_last_unit_removes_the_line() {
        val a = addToCart(emptyList(), "p1", 5.0)
        assertThat(decrementLine(a.lines, "p1::")).isEmpty()
        val two = incrementLine(a.lines, "p1::", 5.0).lines
        assertThat(decrementLine(two, "p1::")[0].quantity).isEqualTo(1.0)
    }

    @Test
    fun an_override_replaces_the_base_and_the_extras_still_add_on_top_never_below_zero() {
        val l = CartLine("p1", 1.0, listOf("o1"), listOf(milk))
        assertThat(lineUnitPrice(3.5, l)).isWithin(1e-9).of(4.0)
        assertThat(lineUnitPrice(3.5, l.copy(unitPriceOverride = 3.0))).isWithin(1e-9).of(3.5)
        val cheap = CartLine("p1", 1.0, listOf("o1"), listOf(ModifierPick("g", "Less", -2.0)))
        assertThat(lineUnitPrice(1.0, cheap)).isEqualTo(0.0)
        // the list price (for the strike-through logic and hold display) ignores the override
        assertThat(lineListPrice(3.5, l.copy(unitPriceOverride = 3.0))).isWithin(1e-9).of(4.0)
    }

    @Test
    fun clearing_a_price_clears_its_reason() {
        val a = setPriceOverride(listOf(CartLine("p1", 1.0)), "p1::", 2.0, "Damaged")
        assertThat(a[0].priceReason).isEqualTo("Damaged")
        val b = setPriceOverride(a, "p1::", null, "ignored")
        assertThat(b[0].unitPriceOverride).isNull()
        assertThat(b[0].priceReason).isNull()
    }

    @Test
    fun the_signature_moves_when_quantity_options_or_price_move() {
        val base = listOf(CartLine("p1", 1.0))
        val sig = cartSignature(base)
        assertThat(cartSignature(base.map { it.copy(quantity = 2.0) })).isNotEqualTo(sig)
        assertThat(cartSignature(base.map { it.copy(unitPriceOverride = 1.0) })).isNotEqualTo(sig)
        assertThat(cartSignature(listOf(CartLine("p1", 1.0, listOf("o1"))))).isNotEqualTo(sig)
        // a percentage of its own is NOT part of it (a cart discount cannot coexist with one anyway)
        assertThat(cartSignature(base.map { it.copy(ownPct = 5.0) })).isEqualTo(sig)
    }

    @Test
    fun a_variant_label_is_shown_once() {
        assertThat(displayName(Product(name = "Shirt", variant_label = "Red / L"))).isEqualTo("Shirt · Red / L")
        assertThat(displayName(Product(name = "Shirt Red / L", variant_label = "Red / L"))).isEqualTo("Shirt Red / L")
        assertThat(displayName(Product(name = "Tea"))).isEqualTo("Tea")
    }
}
