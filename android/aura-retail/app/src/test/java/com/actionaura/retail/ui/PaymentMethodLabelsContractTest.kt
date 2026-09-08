package com.actionaura.retail.ui

import com.actionaura.retail.net.PayMethod
import com.actionaura.retail.ui.screens.paymentMethodName
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
 * still spells them the same way, and the call sites that render a method's
 * name route it through `tr()` -- so a renamed seed, a dropped catalogue
 * entry, or a label that reverts to raw text all fail loudly here instead of
 * quietly shipping English on an Arabic till again.
 *
 * THIRD SITE, added after this file's own docstring got it wrong. The suite
 * said "the TWO call sites" and one of its comments excused the sales-report
 * breakdown as rendering "a count and a revenue figure, not the name". It
 * rendered the name -- raw, uncapitalised-except-the-first-letter -- and the
 * excuse is why nobody checked it for months while the suite stayed green.
 * That site is different in kind from the other two and needed more than a
 * `tr()` wrapper: the report is grouped by the stored wire CODE
 * ("bank transfer"), not by a name, so it has to be resolved back against the
 * configured method list before translation. [paymentMethodName] does that,
 * and the last five tests below exercise it directly rather than by reading
 * source.
 *
 * TWO THINGS THIS SUITE DOES NOT COVER, said out loud rather than left to be
 * rediscovered:
 *   - The desktop has the same gap. products/retail/frontend/subsystem-retail.js
 *     feeds raw `r.payment_method` into its chart labels, so an Arabic desktop
 *     till shows the same English codes. That file is across an ownership
 *     boundary and is not touched here.
 *   - Android sends the method NAME lowercased while retail_api.py's
 *     `_DEFAULT_METHODS` pairs each name with a short code ('Bank Transfer',
 *     'bank'). The two ends disagree about what a payment method's identity
 *     IS, and [paymentMethodName] papers over it case-insensitively rather
 *     than resolving it. Worth a proper look; it is not this fix.
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
        // breakdown, further UP the same file; substringAfterLast reaches the
        // Settings list this fix touches.
        //
        // CORRECTION: this comment used to excuse the report block as "a list
        // of totals BY method, nothing to translate there since it renders a
        // count and a revenue figure, not the name". That was factually wrong
        // about the line it was describing -- the block DID render the name,
        // raw, and the docstring's excuse is why nobody looked. The report
        // block now has its own assertion, immediately below.
        val src = codeOnly(retailExtraScreens)
        val section = src.substringAfterLast("SectionHeader(tr(\"Payment methods\"))")
        assertThat(section).isNotEmpty()
        assertThat(section.substringBefore("newMethod")).contains("tr(m.name")
    }

    @Test
    fun the_sales_report_breakdown_resolves_the_stored_code_before_translating() {
        // The THIRD render site, and the one whose absence this suite used to
        // state in writing. It is different in kind from the other two: the
        // report is grouped by the stored wire CODE, so wrapping it in tr()
        // alone would look like a fix and change nothing -- the catalogue is
        // keyed on "Bank Transfer", the row says "bank transfer".
        val src = codeOnly(retailExtraScreens)
        val block = src.substringAfter("SectionHeader(tr(\"Payment methods\"))")
            .substringBefore("private fun StatCard(")
        assertThat(block).isNotEmpty()
        assertThat(block).contains("tr(paymentMethodName(")
        // The raw capitalise-the-first-letter render this replaced. Named
        // exactly so the old shape cannot come back looking translated.
        assertThat(block).doesNotContain("pm.payment_method ?:")
    }

    @Test
    fun the_report_screen_actually_fetches_the_list_it_resolves_against() {
        // paymentMethodName falls back to the stored string when it has no
        // configured names to match, so a resolver wired to a list nobody ever
        // loads is indistinguishable from no fix at all -- and would be green
        // on the assertion above.
        val src = codeOnly(retailExtraScreens)
        val screen = src.substringAfter("fun ReportsScreen(").substringBefore("private fun StatCard(")
        assertThat(screen).contains("ApiClient.get().payMethods()")
        assertThat(screen).contains("configuredMethods")
    }

    // ── paymentMethodName: real behaviour, no source reading ────────────────
    //
    // The rest of this file scans source because a @Composable is not runnable
    // here. The resolver is not a composable, so these call it.

    @Test
    fun a_seeded_two_word_method_resolves_back_to_its_configured_name() {
        // The defect, exactly: retail_api.py stores what the client sent
        // (`it.name.lowercase()`), so a Bank Transfer sale is the literal
        // "bank transfer" and the report groups on that. Capitalising the
        // first letter produced "Bank transfer" -- wrong in English, and a
        // catalogue miss in Arabic.
        val configured = listOf(
            PayMethod(id = 1, name = "Cash"),
            PayMethod(id = 2, name = "Bank Transfer"),
            PayMethod(id = 3, name = "Mobile Wallet"),
        )
        assertThat(paymentMethodName("bank transfer", configured)).isEqualTo("Bank Transfer")
        assertThat(paymentMethodName("mobile wallet", configured)).isEqualTo("Mobile Wallet")
        assertThat(paymentMethodName("cash", configured)).isEqualTo("Cash")
    }

    @Test
    fun an_unrecognised_code_falls_back_to_the_stored_string() {
        // The allow-half. A shop's own custom method, a deleted one, or
        // "credit" (which the Charge step always appends and the server never
        // seeds as a method row) must still render something readable rather
        // than an em dash or a blank.
        val configured = listOf(PayMethod(id = 1, name = "Cash"))
        assertThat(paymentMethodName("credit", configured)).isEqualTo("Credit")
        assertThat(paymentMethodName("cliq", configured)).isEqualTo("Cliq")
        assertThat(paymentMethodName("cash", emptyList())).isEqualTo("Cash")
    }

    @Test
    fun a_missing_or_blank_method_renders_the_em_dash_it_always_did() {
        // Unchanged behaviour, pinned so the fix cannot quietly turn a null
        // into the string "null" on a report a shop reads for money.
        val configured = listOf(PayMethod(id = 1, name = "Cash"))
        assertThat(paymentMethodName(null, configured)).isEqualTo("—")
        assertThat(paymentMethodName("   ", configured)).isEqualTo("—")
    }

    @Test
    fun resolution_is_case_insensitive_in_both_directions() {
        // Case is the ONLY relationship the two ends share: the client
        // lowercases the name on its way out and nothing normalises it on the
        // way back. A shop that renames a method to "BANK TRANSFER" must not
        // lose its own history's rows.
        val configured = listOf(PayMethod(id = 1, name = "BANK TRANSFER"))
        assertThat(paymentMethodName("bank transfer", configured)).isEqualTo("BANK TRANSFER")
        assertThat(paymentMethodName("Bank Transfer", configured)).isEqualTo("BANK TRANSFER")
    }
}
