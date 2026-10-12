package com.actionaura.retail.ui.pos

import com.actionaura.retail.ui.codeOnly
import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/** Split-tender wiring (ANDROID-2a item 2) read from source, plus the server rules it mirrors. */
class SplitWiringContractTest {
    private val moduleRoot = File(".")
    private val suiteRoot = File("../../..")

    private fun kotlin(relative: String): String {
        val f = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", f.exists())
        return codeOnly(f.readText())
    }

    private val pos get() = kotlin("src/main/java/com/actionaura/retail/ui/screens/RetailScreens.kt")

    @Test
    fun the_sale_request_is_built_by_the_shared_builder() {
        assertThat(pos).contains("ApiClient.get().createSale(buildCreateSaleRequest(")
        assertThat(pos).doesNotContain("createSale(CreateSaleRequest(")   // no inline construction left in the till
    }

    @Test
    fun charge_is_enabled_only_when_two_or_more_parts_cover_the_total() {
        assertThat(pos).contains("enabled = !charging && (!splitMode || (splitParts.size >= 2 && splitSummary.chargeable))")
    }

    @Test
    fun split_mode_does_not_apply_the_single_method_credit_rules() {
        assertThat(pos).contains("if (!splitMode && pm == \"credit\" && cust == null)")
        assertThat(pos).contains("if (pm == \"credit\" && !splitMode)")
    }

    @Test
    fun the_estimate_is_labelled_estimated_never_total() {
        val parts = kotlin("src/main/java/com/actionaura/retail/ui/screens/PosSplitParts.kt")
        assertThat(parts).contains("tr(\"Estimated amount due\")")
        assertThat(parts).doesNotContain("tr(\"Total\")")
    }

    @Test
    fun the_server_still_settles_the_parts_with_the_rules_the_phone_mirrors() {
        val f = File(suiteRoot, "products/retail/backend/core/retail/tenders.py")
        assumeTrue("tenders.py not reachable", f.exists())
        val py = f.readText()
        assertThat(py).contains("MAX_TENDERS = $MAX_TENDER_PARTS")
        for (code in listOf("TENDERS_SHORT", "TENDER_CREDIT_OVERPAID", "TENDER_OVERPAID_NON_CASH", "TENDER_CASH_NOT_NEEDED", "TENDER_DUPLICATE_METHOD")) {
            assertThat(py).contains("'$code'")
        }
        val apiFile = File(suiteRoot, "products/retail/backend/api/retail_api.py")
        assumeTrue("retail_api.py not reachable", apiFile.exists())
        assertThat(apiFile.readText()).contains("if 'tenders' in data:")
    }
}
