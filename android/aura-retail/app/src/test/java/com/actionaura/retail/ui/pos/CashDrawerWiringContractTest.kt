package com.actionaura.retail.ui.pos

import com.actionaura.retail.ui.CAPS_CASH_DRAWER
import com.actionaura.retail.ui.codeOnly
import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Cash-drawer wiring (ANDROID-2a item 4), read from source, plus the server
 * facts the phone's copies depend on (account codes, route gates).
 */
class CashDrawerWiringContractTest {
    private val moduleRoot = File(".")
    private val suiteRoot = File("../../..")

    private fun kotlin(relative: String): String {
        val f = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", f.exists())
        return codeOnly(f.readText())
    }

    private fun python(relative: String): String {
        val f = File(suiteRoot, relative)
        assumeTrue("$relative not reachable from this run context", f.exists())
        return f.readText()
    }

    private val appRoot get() = kotlin("src/main/java/com/actionaura/retail/ui/AppRoot.kt")
    private val more get() = kotlin("src/main/java/com/actionaura/retail/ui/screens/RetailExtraScreens.kt")
    private val screen get() = kotlin("src/main/java/com/actionaura/retail/ui/screens/CashSessionScreen.kt")

    @Test
    fun the_screen_is_in_the_nav_graph_with_a_title() {
        assertThat(appRoot).contains("b.composable(\"cash_drawer\") { CashSessionScreen(snackbar) }")
        assertThat(appRoot).contains("\"cash_drawer\" -> \"Cash Drawer\"")
    }

    @Test
    fun more_offers_it_only_to_people_who_hold_the_drawer_capability() {
        val at = more.indexOf("onNavigate(\"cash_drawer\")")
        assertThat(at).isGreaterThan(-1)
        val gate = more.lastIndexOf("hasAnyCapability(com.actionaura.retail.ui.CAPS_CASH_DRAWER)", at)
        assertThat(gate).isGreaterThan(-1)
        assertThat(at - gate).isLessThan(400)
    }

    @Test
    fun the_blind_count_never_fetches_or_keeps_the_expected_figure_for_a_non_approver() {
        // End Shift only asks for the x-report when the server said this person may approve ...
        assertThat(screen).contains("endExpected = null if (mayApprove) {")
        // ... and the X report is redacted before it is stored.
        assertThat(screen).contains("redactXReport(it, mayApprove)")
        // ... and the confirmation shows the variance only to an approver.
        assertThat(screen).contains("if (mayApprove && rep?.variance != null)")
    }

    @Test
    fun may_approve_fails_closed() {
        // A list that failed to load must leave mayApprove false.
        assertThat(screen).contains("mayApprove = list?.may_approve == true")
        assertThat(screen).contains("var mayApprove by remember { mutableStateOf(false) }")
    }

    @Test
    fun the_drawer_gate_equals_the_decorator_of_every_route_the_screen_calls() {
        val py = python("products/retail/backend/api/retail_api.py")
        val user = python("commercial_runtime/identity/user_accounts.py")
        val code = Regex("""(?m)^CAP_CASH_CLOSE = '([^']+)'""").find(user)!!.groupValues[1]
        assertThat(CAPS_CASH_DRAWER).containsExactly(code)
        for (fn in listOf("open_cash_session", "current_cash_session", "create_cash_movement", "cash_session_x_report", "close_cash_session")) {
            val defAt = Regex("""(?m)^def $fn\(""").find(py)!!.range.first
            val head = py.substring(0, defAt)
            val stack = head.substring(head.lastIndexOf("@retail_bp.route(")).lines()
                .filterNot { it.trimStart().startsWith("#") }.joinToString("\n")
            assertThat(stack).contains("@mt_require_capability(CAP_CASH_CLOSE)")
        }
    }

    @Test
    fun the_account_lists_equal_the_servers() {
        // general_ledger.py: ACCOUNT_X = 'nnnn' constants, EXPENSE_ACCOUNT_CODES = (names...),
        // PAID_OUT = EXPENSE + (ACCOUNT_OWNER_DRAWINGS,), PAID_IN = (names...). Resolved by name.
        val py = python("products/retail/backend/core/retail/general_ledger.py")
        val consts = Regex("""(?m)^(ACCOUNT_\w+) = '(\d{4})'""").findAll(py).associate { it.groupValues[1] to it.groupValues[2] }
        fun names(tuple: String): List<String> {
            val body = Regex("""(?s)$tuple = \((.*?)\)""").find(py)!!.groupValues[1]
                .lines().filterNot { it.trimStart().startsWith("#") }.joinToString(" ")
            return Regex("""ACCOUNT_\w+""").findAll(body).map { it.value }.toList()
        }
        val expense = names("EXPENSE_ACCOUNT_CODES").map { consts.getValue(it) }
        val serverOut = (expense + consts.getValue("ACCOUNT_OWNER_DRAWINGS")).toSet()
        val serverIn = names("PAID_IN_ACCOUNT_CODES").map { consts.getValue(it) }.toSet()
        assertThat(serverOut).hasSize(8)
        assertThat(serverIn).hasSize(2)
        assertThat(PAID_OUT_PURPOSES.map { it.code }.toSet()).isEqualTo(serverOut)
        assertThat(PAID_IN_PURPOSES.map { it.code }.toSet()).isEqualTo(serverIn)
    }
}
