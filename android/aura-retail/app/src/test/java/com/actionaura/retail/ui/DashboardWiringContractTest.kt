package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Rendering-honesty guard for the cashier dashboard.
 *
 * Measured on the Mi Note 10, 2026-09-06: a cashier's dashboard read
 * "TODAY'S SALES JD 0.000 / TRANSACTIONS 0" minutes after ringing a real
 * JD 18.000 sale on that same phone. The server's answer to
 * GET /api/sub/retail/dashboard/stats for a cashier is a deliberate 403 --
 * gated on retail.reports, which ROLE_CASHIER does not hold -- and the old
 * code caught that failure and rendered the model's all-zero defaults over
 * it, so a refusal was painted as a fact.
 *
 * Same shape and justification as EmployeesWiringContractTest and
 * EmployeeSalesWiringContractTest: there is no Compose test runner, no
 * Chaquopy and no device in this environment, so the source text is the
 * only seam available for "does the screen actually stop believing the
 * zero defaults are real once it knows the account cannot see them."
 * `codeOnly` (KotlinSourceText.kt, same package) strips comments first, so
 * these assertions are about CODE, not about the paragraph above quoting
 * the very bug they guard against.
 */
class DashboardWiringContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val dashboardScreen get() =
        source("src/main/java/com/actionaura/retail/ui/screens/DashboardScreen.kt")

    @Test
    fun the_screen_gates_rendering_on_the_reports_capability() {
        // Rendering advice only -- the server independently re-checks and
        // answers 403 regardless of what this client believes. This is the
        // check that the client stops treating the zero defaults as real
        // the moment it knows the signed-in account cannot see figures.
        assertThat(codeOnly(dashboardScreen)).contains("hasCapability(CAP_REPORTS)")
    }

    @Test
    fun the_zero_default_rendering_is_gone() {
        // The exact bug: the model's all-zero defaults rendered as the
        // day's real totals whenever the fetch was refused or failed. If
        // this expression is back, a 403 is being painted as a fact again.
        assertThat(codeOnly(dashboardScreen)).doesNotContain("?: RetailStats()")
    }

    @Test
    fun a_gated_account_sees_the_ready_to_sell_card_not_a_blank_or_a_guess() {
        // Same sentence, same key, as the desktop shell's own "Ready to
        // sell" card -- the two clients must never state this differently.
        val code = codeOnly(dashboardScreen)
        assertThat(code).contains("""tr("Ready to sell")""")
        assertThat(code).contains(
            """tr("Sales totals and reports are limited to managers and the store owner. Open the till to start ringing sales.")""",
        )
    }

    @Test
    fun a_failed_fetch_that_never_loaded_anything_says_so_instead_of_showing_zero() {
        // The other path to the same zero-default bug: the account CAN see
        // figures, but the call itself failed before anything ever loaded.
        // That is a different fact from "this account holds no figures" and
        // must be shown as one, never as a silent fallback to zero.
        assertThat(codeOnly(dashboardScreen)).contains("""tr("Figures unavailable")""")
    }
}
