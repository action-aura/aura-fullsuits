package com.actionaura.retail.ui.pos

import com.actionaura.retail.ui.codeOnly
import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Hold / resume wiring (ANDROID-2a item 1), read from source, plus the two
 * server behaviours the phone's flow depends on: resume DELETES the row (so the
 * cart must be rebuilt from the response at once) and the routes are the ones
 * the desktop uses.
 */
class HoldWiringContractTest {
    private val moduleRoot = File(".")
    private val suiteRoot = File("../../..")

    private fun kotlin(relative: String): String {
        val f = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", f.exists())
        return codeOnly(f.readText())
    }

    private val pos get() = kotlin("src/main/java/com/actionaura/retail/ui/screens/RetailScreens.kt")
    private val api get() = kotlin("src/main/java/com/actionaura/retail/net/AuraApi.kt")

    @Test
    fun the_process_local_parked_cart_store_is_gone() {
        // It died with the app and no other till could see it.
        assertThat(pos).doesNotContain("object HeldSales")
        assertThat(pos).doesNotContain("HeldSales.list")
        assertThat(pos).doesNotContain("data class HeldSale(")
    }

    @Test
    fun the_till_uses_the_servers_held_sales_routes() {
        assertThat(pos).contains("ApiClient.get().holdSale(buildHoldRequest(")
        assertThat(pos).contains("ApiClient.get().resumeHeldSale(h.id)")
        assertThat(pos).contains("ApiClient.get().discardHeldSale(h.id)")
        assertThat(api).contains("held-sales/{id}/resume")
    }

    @Test
    fun replacing_a_non_empty_cart_asks_first() {
        // The resume button must go through the confirm dialog when the cart has lines.
        assertThat(pos).contains("if (cartLines.isNotEmpty()) pendingResume = h else scope.launch { resumeHeld(h) }")
        assertThat(pos).contains("pendingResume = null; scope.launch { resumeHeld(h) }")
    }

    @Test
    fun the_held_routes_are_the_desktops_and_resume_deletes_the_row() {
        val f = File(suiteRoot, "products/retail/backend/api/retail_api.py")
        assumeTrue("retail_api.py not reachable", f.exists())
        val py = f.readText()
        assertThat(py).contains("@retail_bp.route('/held-sales/<int:held_id>/resume', methods=['POST'])")
        assertThat(py).contains("@retail_bp.route('/held-sales/<int:held_id>', methods=['DELETE'])")
        // The reason the phone rebuilds its cart immediately from the resume response.
        assertThat(py).contains("DELETE FROM held_sales WHERE id=? AND company_id=?\", (held_id, cid)")
    }
}
