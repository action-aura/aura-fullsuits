package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Measured on the Mi Note 10 in Arabic, 2026-09-06 evening: with the app
 * switched to Arabic, every label on the Charge step and in Settings
 * translates except the payment methods themselves -- "Cash / Card / Bank
 * Transfer / Mobile Wallet / Check" stay English, because the screens render
 * the row's `name` raw instead of running it through [tr].
 *
 * Those five names are the server's seed, not this client's invention:
 * `_DEFAULT_METHODS` in retail_api.py. This suite pins both ends of that
 * contract -- the Arabic catalogue carries all five keys, the server seed
 * still spells them the same way, and the two call sites that render a
 * method's name route it through `tr()` -- so a renamed seed, a dropped
 * catalogue entry, or a label that reverts to raw text all fail loudly here
 * instead of quietly shipping English on an Arabic till again.
 *
 * The value written on the sale must NOT change: the Charge-step tender
 * chips key on `it.lowercase()`, never on the translated label, and that
 * wire code is untouched by this fix. Only the visible label goes through
 * `tr()`, which falls back to the English name for any custom method a shop
 * adds, so nothing breaks for names this catalogue has no entry for.
 *
 * Same shape as EmployeesWiringContractTest: no Compose test runner, no
 * Chaquopy and no device in this environment, so scanning the Kotlin (and,
 * for the seed, Python) source is the seam available for "is this actually
 * translated" and "does the seed still say what this test assumes."
 */
class PaymentMethodLabelsContractTest {

    private val moduleRoot = File(".")
    // Gradle unit tests run with the module directory (android/aura-retail/app)
    // as the working directory -- see ReadinessContractTest -- so three levels
    // up is the aura-fullsuits root.
    private val suiteRoot = File("../../..")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val stringsKt get() =
        source("src/main/java/com/actionaura/retail/ui/i18n/Strings.kt")
    private val retailScreens get() =
        source("src/main/java/com/actionaura/retail/ui/screens/RetailScreens.kt")
    private val retailExtraScreens get() =
        source("src/main/java/com/actionaura/retail/ui/screens/RetailExtraScreens.kt")

    // ── The catalogue carries all five seeded names ─────────────────────────

    @Test
    fun the_arabic_catalogue_has_an_entry_for_every_seeded_payment_method_name() {
        val catalog = codeOnly(stringsKt)
        val seeded = listOf("Cash", "Card", "Bank Transfer", "Mobile Wallet", "Check")
        val missing = seeded.filterNot { catalog.contains("\"$it\" to") }
        assertThat(missing).isEmpty()
    }

    // ── The server seed still spells the names this catalogue assumes ──────

    @Test
    fun the_server_seed_still_spells_every_name_this_catalogue_assumes() {
        // Reached the same way EmployeeSalesWiringContractTest reaches sibling
        // product files: three levels up from the module directory, skipped
        // (not failed) if this run has no repository around it -- the file
        // lives across an ownership boundary (products/retail/) another
        // branch may be editing.
        val api = File(suiteRoot, "products/retail/backend/api/retail_api.py")
        assumeTrue("retail_api.py not reachable from this run context", api.exists())
        val src = api.readText()
        val seedBlock = src.substringAfter("_DEFAULT_METHODS = [", "")
        assertThat(seedBlock).isNotEmpty()
        val seedLiteral = seedBlock.substringBefore("]")

        val seeded = listOf("Cash", "Card", "Bank Transfer", "Mobile Wallet", "Check")
        val missing = seeded.filterNot { seedLiteral.contains("'$it'") || seedLiteral.contains("\"$it\"") }
        // A renamed seed must fail loudly here rather than quietly leaving a
        // stale Arabic entry nothing ever matches again.
        assertThat(missing).isEmpty()
    }

    // ── The two render sites actually call tr() ─────────────────────────────

    @Test
    fun the_charge_step_tender_chips_translate_the_label_but_not_the_wire_value() {
        val src = codeOnly(retailScreens)
        val block = src.substringAfter("val payOptions = (").substringBefore("if (paymentMethod ==")
        assertThat(block).isNotEmpty()

        // The label shown to the cashier is translated...
        assertThat(block).contains("tr(label)")
        // ...but the value sent to the server is still the plain lowercase
        // English code -- this is the assertion that would go RED if a future
        // change translated the wire value along with the label.
        assertThat(block).contains(".lowercase()")
    }

    @Test
    fun the_settings_payment_methods_list_translates_the_name() {
        // "Payment methods" is also a section header on the sales-report
        // breakdown (a list of totals BY method, nothing to translate there
        // since it renders a count and a revenue figure, not the name) --
        // substringAfterLast reaches the Settings list this fix touches,
        // further down the same file.
        val src = codeOnly(retailExtraScreens)
        val section = src.substringAfterLast("SectionHeader(tr(\"Payment methods\"))")
        assertThat(section).isNotEmpty()
        assertThat(section.substringBefore("newMethod")).contains("tr(m.name")
    }
}
