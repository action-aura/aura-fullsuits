package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Wiring guard for the POS cart bottom sheet's (RetailScreens.kt's
 * `ModalBottomSheet { showCart }` block) keyboard-clearance fix
 * (retail-hardware-viewports).
 *
 * The bug this pins: that Column carries customer picker, an optional
 * outstanding-credit line, the whole loyalty-redemption block, the cart
 * lines, subtotal, payment-method chips, an optional down-payment field,
 * then Hold sale and Charge -- and had `imePadding()` but no
 * `verticalScroll()`. `ModalBottomSheet` does not scroll its own content;
 * with loyalty eligible, a credit sale selected (down-payment field
 * visible) and the keyboard open, the combined height can exceed the
 * post-IME viewport, leaving Charge -- the money button, on the highest-
 * frequency screen in the app -- unreachable. Same shape as LoginScreen.kt's
 * identical historical bug (LoginScreenWiringContractTest), just with no
 * scroll fallback at all instead of one that stopped reaching the button.
 *
 * No Compose test runner in this environment (see
 * ProductsWiringContractTest's identical rationale), so this scans the
 * Kotlin source rather than rendering the sheet.
 */
class PosCartSheetWiringContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val retailScreensSrc get() =
        codeOnly(source("src/main/java/com/actionaura/retail/ui/screens/RetailScreens.kt"))

    @Test
    fun the_cart_sheets_column_scrolls_and_still_pads_for_the_keyboard() {
        // Anchored on the imePadding() call this Column already had, so the
        // assertion is about THIS specific Column rather than any
        // verticalScroll()/imePadding() pair anywhere in a 1000+ line file.
        val imePaddingAt = retailScreensSrc.indexOf(
            "Column( Modifier .padding(20.dp) .padding(bottom = 24.dp) .verticalScroll(rememberScrollState()) .imePadding(),",
        )
        // codeOnly() collapses all whitespace to single spaces, so the
        // needle above has to match that shape -- reproduced exactly rather
        // than approximated, so a real reformatting still gets caught by a
        // clear failure instead of a false pass from a loose contains().
        assertThat(imePaddingAt).isGreaterThan(-1)
    }

    @Test
    fun the_nested_cart_line_list_stays_height_bounded() {
        // Nesting a scrolling Column around an unbounded LazyColumn crashes
        // at runtime ("vertically scrollable component was measured with an
        // infinite height"); this pins that the cart's own LazyColumn keeps
        // its height cap, which is what makes nesting it inside the fix
        // above safe.
        assertThat(retailScreensSrc).contains("LazyColumn(Modifier.heightIn(max = 340.dp)")
    }
}
