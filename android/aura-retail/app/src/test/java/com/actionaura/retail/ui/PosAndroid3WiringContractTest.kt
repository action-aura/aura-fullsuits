package com.actionaura.retail.ui

import androidx.compose.material3.windowsizeclass.WindowWidthSizeClass
import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * ANDROID-3: the till's new wiring, read from source (no Compose runner here; the emulator run in the
 * ANDROID-3 note is the pixel proof), and the tablet decision as a pure function.
 */
class PosAndroid3WiringContractTest {

    private val root = File(".")
    private fun kotlin(relative: String): String {
        val f = File(root, relative)
        assumeTrue("$relative not reachable from this run context", f.exists())
        return codeOnly(f.readText())
    }

    private val pos get() = kotlin("src/main/java/com/actionaura/retail/ui/screens/RetailScreens.kt")
    private val app get() = kotlin("src/main/java/com/actionaura/retail/ui/AppRoot.kt")

    // ── tablet layout ────────────────────────────────────────────────────────
    @Test
    fun only_a_compact_window_keeps_the_phone_layout() {
        assertThat(usesWideLayout(WindowWidthSizeClass.Compact)).isFalse()
        assertThat(usesWideLayout(WindowWidthSizeClass.Medium)).isTrue()
        assertThat(usesWideLayout(WindowWidthSizeClass.Expanded)).isTrue()
    }

    @Test
    fun the_shell_swaps_the_bottom_bar_for_a_rail_when_wide_and_provides_the_flag() {
        assertThat(app).contains("calculateWindowSizeClass(it).widthSizeClass")
        assertThat(app).contains("if (isTopLevel && !wide) NavigationBar(")
        assertThat(app).contains("if (isTopLevel && wide) NavigationRail(")
        assertThat(app).contains("LocalWideLayout provides wide")
    }

    @Test
    fun the_till_shows_one_cart_in_a_pane_when_wide_and_in_a_sheet_when_not() {
        assertThat(pos).contains("val wide = LocalWideLayout.current")
        assertThat(pos).contains("if (wide) { VerticalDivider() Surface(Modifier.width(400.dp).fillMaxHeight()")
        assertThat(pos).contains("if (showCart && !wide) {")
        assertThat(pos).contains("visible = count > 0 && !wide,")
        // one body for both: the pane and the sheet call the same lambda, so they cannot drift
        assertThat(Regex("""cartContent\(\)""").findAll(pos).count()).isEqualTo(2)
    }

    // ── pickers: nothing reaches the cart that needed a picker ───────────────
    @Test
    fun a_product_with_variants_or_extras_goes_through_its_picker_first() {
        assertThat(pos).contains("if (p.has_variants == 1 || products.any { it.parent_product_id == p.id }) { openVariants(p); return Chosen.PICKER }")
        assertThat(pos).contains("if (p.has_modifiers == 1) { openModifiers(p); return Chosen.PICKER }")
        // every entry point - tile, HID scan, camera scan - chooses through the same function
        assertThat(Regex("""choose\(p\)""").findAll(pos).count()).isAtLeast(3)
        assertThat(pos).doesNotContain("addOne(")
    }

    @Test
    fun a_variant_is_sold_through_the_variant_it_was_picked_as_and_variants_have_no_tile_of_their_own() {
        assertThat(pos).contains("it.parent_product_id.isNullOrBlank() &&")
        assertThat(pos).contains("choose(v)")
    }

    @Test
    fun the_scanner_closes_when_a_picker_has_to_open() {
        assertThat(pos).contains("if (c == Chosen.PICKER) { showScanner = false; lastScanned = null }")
    }

    // ── manager approval ─────────────────────────────────────────────────────
    @Test
    fun the_approval_goes_into_the_sale_and_is_asked_for_by_prediction_or_by_the_servers_word() {
        assertThat(pos).contains("cartDiscount = activeDisc, discountReason = saleDiscountReason(activeDisc, cartLines), approval = approval")
        assertThat(pos).contains("else if (needsManager) { scope.launch { askManager() }")
        assertThat(pos).contains("if (f.needsManager && approval == null) askManager() else snackbar.showSnackbar(f.message)")
        assertThat(pos).contains("onApprove = { req -> approvers = null; scope.launch { submitSale(req) } }")
    }

    @Test
    fun the_pin_is_not_held_in_the_tills_state() {
        // approvers (who) is state; the PIN exists only inside the sheet and the one request
        assertThat(pos).doesNotContain("pinText")
        assertThat(pos).doesNotContain("var pin ")
        val sheet = kotlin("src/main/java/com/actionaura/retail/ui/screens/PosApprovalSheet.kt")
        assertThat(sheet).contains("\"●\".repeat(pin.length)")
        assertThat(sheet).doesNotContain("Text(pin")
    }

    @Test
    fun the_estimate_is_labelled_as_one_and_the_servers_total_is_reported_when_it_differs() {
        assertThat(pos).contains("tr(\"Estimated total\")")
        assertThat(pos).contains("tr(\"An estimate. The shop confirms the real total when you charge.\")")
        assertThat(pos).contains("tr(\"The total charged is %s\").format(money(sale.total))")
    }

    @Test
    fun a_stale_sale_discount_cannot_ride_into_a_sale() {
        assertThat(pos).contains("val activeDisc = cartDisc?.takeIf { it.sig == cartSig }")
        assertThat(pos).doesNotContain("cartDiscount = cartDisc")
    }
}
