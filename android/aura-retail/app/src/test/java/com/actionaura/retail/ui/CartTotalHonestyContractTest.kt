package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * The cart may not call a pre-tax preview "Total".
 *
 * WHY THIS EXISTS
 *
 * PosScreen keeps a local `total` that its own checkout button describes, in
 * writing, as a "pre-tax, pre-discount PREVIEW only" which is "never
 * persisted/displayed as the sale's actual total". The second half of that
 * sentence was false. The cart summary rendered exactly that variable under
 * the label "Total", while the server computed and charged the taxed figure.
 *
 * So on any taxed product the cashier read one number aloud to the customer
 * and the till took another. Nothing failed, nothing logged, and the code
 * comment asserted the opposite of what the screen did -- which is why
 * reading the file was never going to catch it.
 *
 * THE RULE THIS PINS, and it is deliberately not a blanket ban on the word:
 * a "Total" label is CORRECT next to an authoritative server figure. The
 * shared receipt prints `Total: ${money(sale.total)}` from the createSale
 * response and should keep doing so. What is forbidden is pairing that label
 * with the LOCAL preview variable. The distinction is the whole point --
 * `docs/architecture/financial-authority-contracts.md` makes the server the
 * only authority on money, and this test enforces the display side of that
 * contract rather than restating it in another comment.
 *
 * Same shape and justification as OverflowNavigationContractTest: no Compose
 * test runner and no device in this environment, but "does the screen tell
 * the customer the truth" is exactly the regression that stays invisible.
 */
class CartTotalHonestyContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val posScreens get() =
        source("src/main/java/com/actionaura/retail/ui/screens/RetailScreens.kt")
    private val strings get() =
        source("src/main/java/com/actionaura/retail/ui/i18n/Strings.kt")

    /** How much text before a `money(total)` render counts as "its label".
     *  Generous on purpose: a label separated from its figure by styling
     *  arguments is still that figure's label to the person reading the
     *  screen, and a tight window would let the defect return by adding a
     *  modifier. */
    private val labelWindow = 400

    @Test
    fun the_local_preview_is_never_labelled_total() {
        val code = codeOnly(posScreens)

        // Every place the LOCAL preview variable is rendered. `money(total)`
        // and not `money(sale.total)`: the regex requires the argument to be
        // exactly `total`, so the authoritative server figure is untouched by
        // this test.
        val renders = Regex("""money\(total\)""").findAll(code).toList()
        assertThat(renders).isNotEmpty()   // the screen must still show it at all

        for (render in renders) {
            val start = maxOf(0, render.range.first - labelWindow)
            val window = code.substring(start, render.range.first)
            assertThat(window).doesNotContain("""tr("Total")""")
        }
    }

    @Test
    fun the_cart_calls_it_a_subtotal_and_says_what_is_missing() {
        val code = codeOnly(posScreens)
        assertThat(code).contains("""tr("Subtotal")""")
        assertThat(code).contains("""tr("Tax and discounts are applied at checkout")""")
    }

    /** tr() falls back to the English key when a translation is missing, so a
     *  forgotten entry is INVISIBLE rather than obviously broken -- an Arabic
     *  till would simply show an English sentence in the middle of its cart.
     *  That silence is why this is asserted rather than trusted. */
    @Test
    fun the_honesty_line_is_translated() {
        assertThat(codeOnly(strings))
            .contains(""""Tax and discounts are applied at checkout" to """")
    }

    /** The authoritative figure keeps its own label -- proving the test above
     *  bans the PAIRING and not the word, and failing if someone "fixes" this
     *  contract by stripping Total off the receipt too. */
    @Test
    fun the_servers_own_total_is_still_called_total() {
        assertThat(codeOnly(posScreens)).contains("""Total: ${'$'}{money(sale.total)}""")
    }
}
