package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Wiring guard for the Products screen's "Add Product" control.
 *
 * The bug this pins: the phone showed "Add Product" -- both the extended FAB
 * and the empty-state CTA -- to every signed-in role, but the server refuses
 * product creation for a cashier (`POST /api/sub/retail/products` gates on
 * `CAP_STOCK_ADJUST`, and `ROLE_CASHIER`'s grant set is sell/refund/cash-close
 * only). Measured on the Mi Note 10 signed in as a cashier, 2026-09-06: the
 * button rendered and its own request came back 403. Same shape and same
 * justification as EmployeeSalesWiringContractTest's capability-gating pair --
 * there is no Compose test runner, no Chaquopy and no device in this
 * environment, so scanning the Kotlin source is the seam available for "is
 * this control actually gated" and "is the gate's string the server's".
 */
class ProductsWiringContractTest {

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

    private val sessionSrc get() = source("src/main/java/com/actionaura/retail/ui/RetailSession.kt")
    private val productsScreen get() = source("src/main/java/com/actionaura/retail/ui/screens/RetailScreens.kt")

    // ── The constant exists on the client ────────────────────────────────────

    @Test
    fun retail_session_declares_the_stock_adjust_capability_constant() {
        assertThat(codeOnly(sessionSrc)).contains("""const val CAP_STOCK_ADJUST = "retail.stock.adjust"""")
    }

    // ── The constant matches the server's ────────────────────────────────────

    @Test
    fun the_capability_code_is_the_one_the_server_actually_gates_on() {
        // Read out of the Python that DEFINES it rather than restated here, so
        // this fails the day the constant moves or is renamed. A capability
        // string that no longer matches the server's does not fail loudly --
        // it silently hides the button from everyone, or shows it to a
        // cashier who cannot use it.
        val accounts = File(suiteRoot, "commercial_runtime/identity/user_accounts.py")
        assumeTrue("user_accounts.py not reachable from this run context", accounts.exists())
        val match = Regex("""CAP_STOCK_ADJUST\s*=\s*'([^']+)'""").find(accounts.readText())
        assertThat(match).isNotNull()
        assertThat(codeOnly(sessionSrc))
            .contains("""const val CAP_STOCK_ADJUST = "${match!!.groupValues[1]}"""")
    }

    // ── The screen actually gates on it, before offering the control ────────

    @Test
    fun the_products_screen_checks_the_capability_before_offering_add_product() {
        val code = codeOnly(productsScreen)
        assertThat(code).contains("hasCapability(CAP_STOCK_ADJUST)")

        // The gate has to be DECLARED before the first "Add Product" control
        // is built, not merely present somewhere in the file -- a gate that
        // exists below the button it should have wrapped protects nothing.
        val gateAt = code.indexOf("hasCapability(CAP_STOCK_ADJUST)")
        val firstAddProductAt = code.indexOf("""tr("Add Product")""")
        assertThat(gateAt).isGreaterThan(-1)
        assertThat(firstAddProductAt).isGreaterThan(-1)
        assertThat(gateAt).isLessThan(firstAddProductAt)

        // The ordering check above is necessary but not sufficient: the
        // `canAddProduct` val is declared once near the top of the composable
        // regardless of whether any individual control actually consumes it,
        // so it alone cannot tell "the FAB is gated" from "the FAB was
        // unwrapped and nothing downstream noticed". Pin the FAB itself as
        // conditional on the same val the gate above computes.
        assertThat(code).contains("if (canAddProduct) { ExtendedFloatingActionButton")
    }
}
