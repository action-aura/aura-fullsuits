package com.actionaura.retail.ui

import com.actionaura.retail.net.BalanceCheck
import com.actionaura.retail.net.BalanceSheet
import com.actionaura.retail.net.BalanceSheetResponse
import com.actionaura.retail.net.EquitySection
import com.actionaura.retail.ui.screens.BalanceCheckState
import com.actionaura.retail.ui.screens.StatementActivity
import com.actionaura.retail.ui.screens.balanceCheckState
import com.actionaura.retail.ui.screens.balanceSheetActivity
import com.actionaura.retail.ui.screens.costBasisWarning
import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Reachability + honesty guards for the Balance Sheet screen
 * (retail-hardware-viewports, core/retail/financial_statements.py::
 * balance_sheet).
 *
 * Same shape and justification as TrialBalanceWiringContractTest /
 * ProfitAndLossWiringContractTest: no Compose test runner, no Chaquopy and
 * no device here.
 *
 * [balanceCheckState] is the one function this file exists to pin above all
 * others -- it is this screen's entire reason to exist (per financial_
 * statements.py's own docstring: "provably... is exactly the class of claim
 * ... that must be MEASURED"), and it must never report a false "Balanced"
 * over a ledger that does not, nor a false "Out of Balance" alarm the server
 * never raised.
 */
class BalanceSheetWiringContractTest {

    private val moduleRoot = File(".")
    private val suiteRoot = File("../../..")
    private val gson = Gson()

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val appRoot get() = source("src/main/java/com/actionaura/retail/ui/AppRoot.kt")
    private val extraScreens get() = source("src/main/java/com/actionaura/retail/ui/screens/RetailExtraScreens.kt")
    private val screen get() = source("src/main/java/com/actionaura/retail/ui/screens/BalanceSheetScreen.kt")
    private val strings get() = source("src/main/java/com/actionaura/retail/ui/i18n/Strings.kt")
    private val apiSrc get() = source("src/main/java/com/actionaura/retail/net/AuraApi.kt")

    private val catalog get() = literalRuns(codeOnly(strings)).toSet()

    // ── Reachability: BOTH halves ────────────────────────────────────────────

    @Test
    fun the_balance_sheet_route_is_registered_in_the_nav_graph() {
        val graph = appRoot.substringAfter("private fun retailGraph(")
        assertThat(graph).contains("""b.composable("balance_sheet")""")
        assertThat(graph).contains("BalanceSheetScreen(")
    }

    @Test
    fun something_actually_navigates_to_the_balance_sheet_route() {
        assertThat(codeOnly(extraScreens)).contains("""onNavigate("balance_sheet")""")
        assertThat(codeOnly(appRoot)).contains("ReportsScreen(snackbar, onNavigate =")
    }

    @Test
    fun the_screen_has_a_title_in_the_top_bar_map() {
        assertThat(appRoot).contains(""""balance_sheet" -> "Balance Sheet"""")
    }

    @Test
    fun the_endpoint_is_declared_on_the_api_interface() {
        assertThat(codeOnly(apiSrc)).contains("""@GET("api/sub/retail/reports/balance-sheet")""")
        assertThat(codeOnly(apiSrc)).contains("""suspend fun balanceSheet(@Query("as_of_date") asOfDate: String)""")
    }

    // ── Capability gating: the same gate the desktop uses ────────────────────

    @Test
    fun the_reports_entry_is_capability_gated_and_the_screen_re_checks() {
        val code = codeOnly(extraScreens)
        // CHANGED (rehearsal fix B7, cfbf883f): the server moved this route from
        // retail.reports to retail.books.view (managers excluded on purpose), so
        // the entry and the screen gate on CAP_BOOKS_VIEW. What this can no
        // longer catch: the entry sitting under the reports gate -- but note it
        // only proves the gate text is present in the file, not that THIS entry
        // is inside it; WriteGateContractTest/AccountantHomeContractTest pin the
        // books-home card per route.
        assertThat(code).contains("RetailSession.hasCapability(CAP_BOOKS_VIEW)")
        assertThat(code).contains("""MoreItem(tr("Balance Sheet")""")
        assertThat(codeOnly(screen)).contains("if (!RetailSession.hasCapability(CAP_BOOKS_VIEW))")
    }

    @Test
    fun the_backend_route_this_screen_calls_gates_on_the_same_capability() {
        val api = File(suiteRoot, "products/retail/backend/api/retail_api.py")
        assumeTrue("retail_api.py not reachable from this run context", api.exists())
        val text = api.readText()
        val routeStart = text.indexOf("def report_balance_sheet(")
        assumeTrue("report_balance_sheet not found in retail_api.py", routeStart >= 0)
        val decorators = text.substring(text.lastIndexOf("@retail_bp.route(", routeStart), routeStart)
        // CHANGED (B7): was CAP_REPORTS. Reads the real decorator, so it still
        // fails the day the server's gate moves again.
        assertThat(decorators).contains("@mt_require_capability(CAP_BOOKS_VIEW)")
        assertThat(decorators).contains("""@retail_bp.route('/reports/balance-sheet'""")
    }

    // ── The Gson explicit-null crash ─────────────────────────────────────────

    @Test
    fun a_null_body_is_refused_rather_than_rendered_as_an_empty_sheet() {
        val parsed = gson.fromJson("""{"success":true,"data":null}""", BalanceSheetResponse::class.java)
        assertThat(parsed.success).isTrue()
        assertThat(parsed.data).isNull()

        val code = codeOnly(screen)
        assertThat(code).contains("?: throw")
        assertThat(code).contains("Malformed response")
    }

    @Test
    fun a_well_formed_body_parses_including_the_balance_check() {
        val body = """
            {"success": true, "data": {
              "currency": "JOD", "as_of_date": "2026-09-23",
              "assets": {"total": 5000.0, "accounts": [
                {"code": "1000", "name": "Cash and Bank", "amount": 5000.0}
              ]},
              "liabilities": {"total": 1000.0, "accounts": [
                {"code": "2100", "name": "Accounts Payable", "amount": 1000.0}
              ]},
              "equity": {"owners_equity": 3000.0, "retained_earnings": 1000.0, "total": 4000.0,
                "accounts": [{"code": "3000", "name": "Owner's Equity", "amount": 3000.0}]},
              "total_liabilities_and_equity": 5000.0,
              "balance_check": {"assets_total": 5000.0, "liabilities_and_equity_total": 5000.0,
                "is_balanced": true, "difference": 0.0},
              "has_activity": true, "cost_basis_incomplete": false,
              "cost_basis_incomplete_sale_count": 0
            }}
        """.trimIndent()
        val parsed = gson.fromJson(body, BalanceSheetResponse::class.java)
        assertThat(parsed.data).isNotNull()
        assertThat(parsed.data!!.assets.accounts).hasSize(1)
        assertThat(parsed.data!!.equity.owners_equity).isEqualTo(3000.0)
        assertThat(parsed.data!!.balance_check).isNotNull()
        assertThat(parsed.data!!.balance_check!!.is_balanced).isTrue()
    }

    // ── THE BALANCE VERDICT -- never a lie in either direction ───────────────

    @Test
    fun a_ledger_that_does_not_balance_is_never_reported_as_balanced() {
        val check = BalanceCheck(assets_total = 5200.0, liabilities_and_equity_total = 5000.0,
            is_balanced = false, difference = 200.0)
        assertThat(balanceCheckState(check)).isEqualTo(BalanceCheckState.DOES_NOT_BALANCE)
    }

    @Test
    fun a_genuinely_balanced_ledger_reads_as_balanced() {
        val check = BalanceCheck(assets_total = 5000.0, liabilities_and_equity_total = 5000.0,
            is_balanced = true, difference = 0.0)
        assertThat(balanceCheckState(check)).isEqualTo(BalanceCheckState.BALANCED)
    }

    @Test
    fun a_missing_balance_check_is_unknown_never_defaulted_to_either_verdict() {
        // The container itself absent/malformed -- must read "couldn't
        // verify", never a false "Balanced" (which a bare `false` default on
        // is_balanced would invert into a false alarm instead) nor a false
        // "Out of Balance".
        assertThat(balanceCheckState(null)).isEqualTo(BalanceCheckState.UNKNOWN)
    }

    @Test
    fun the_balance_verdict_is_read_from_the_servers_own_field_not_recomputed() {
        // balance_sheet already applies the currency's own half-minor-unit
        // epsilon server-side. This client must consume `is_balanced` as
        // sent, never re-derive it from assets_total/liabilities_and_equity_
        // total with its own tolerance -- a second definition of "balanced"
        // is exactly the kind of drift CLAUDE.md's money-epsilon correction
        // warns about.
        val code = codeOnly(screen)
        assertThat(code).contains("check?.is_balanced")
        assertThat(code).doesNotContain("assets_total -")
        assertThat(code).doesNotContain("assets_total ==")
    }

    // ── THE ACTIVITY VERDICT -- never a lie in either direction ──────────────

    @Test
    fun a_sheet_with_no_activity_is_never_rendered_as_a_computed_zero() {
        val bs = BalanceSheet(has_activity = false)
        assertThat(balanceSheetActivity(bs)).isEqualTo(StatementActivity.NO_ACTIVITY)
    }

    @Test
    fun a_sheet_with_real_activity_reads_as_has_activity() {
        val bs = BalanceSheet(has_activity = true,
            equity = EquitySection(owners_equity = 3000.0, retained_earnings = 500.0, total = 3500.0))
        assertThat(balanceSheetActivity(bs)).isEqualTo(StatementActivity.HAS_ACTIVITY)
    }

    @Test
    fun the_activity_verdict_is_read_from_the_servers_own_field() {
        assertThat(codeOnly(screen)).contains("bs.has_activity")
    }

    // ── THE COST-BASIS WARNING (balance-sheet twin) ───────────────────────────

    @Test
    fun cost_basis_incomplete_true_shows_the_inventory_warning() {
        val bs = BalanceSheet(cost_basis_incomplete = true, cost_basis_incomplete_sale_count = 3)
        val warning = costBasisWarning(bs)
        assertThat(warning.visible).isTrue()
        assertThat(warning.affectedSaleCount).isEqualTo(3)
    }

    @Test
    fun cost_basis_incomplete_false_never_shows_the_warning() {
        val bs = BalanceSheet(cost_basis_incomplete = false)
        assertThat(costBasisWarning(bs).visible).isFalse()
    }

    @Test
    fun the_balance_sheet_warning_uses_its_own_inventory_wording_not_the_pl_wording() {
        // financial_statements.py deliberately gives the two statements
        // DIFFERENT explanations of the identical root cause (COGS/gross
        // profit there, Inventory/retained earnings here) -- this screen
        // must pass its OWN body text into the shared warning card, not the
        // Profit & Loss screen's.
        assertThat(codeOnly(screen)).contains("Inventory and retained earnings")
        assertThat(codeOnly(screen)).doesNotContain("gross profit and net profit shown below")
    }

    // ── Error handling ───────────────────────────────────────────────────────

    @Test
    fun failures_go_through_the_shared_mapping_not_a_blanket_message() {
        val src = codeOnly(screen)
        assertThat(src).doesNotContain("Couldn't reach the server")
        val catches = Regex("""catch\s*\(\s*\w+\s*:\s*Exception\s*\)""").findAll(src).toList()
        assertThat(catches).isNotEmpty()
        for (c in catches) {
            val body = src.substring(c.range.last, minOf(src.length, c.range.last + 160))
            assertThat(body).contains("apiErrorMessage(")
        }
    }

    @Test
    fun a_failed_load_is_not_rendered_as_a_freshly_computed_sheet() {
        val src = screen
        assertThat(src).contains("loadError")
        assertThat(src.indexOf("loadError != null"))
            .isLessThan(src.indexOf("BalanceSheetBody("))
    }

    // ── The as-of-date control is a SNAPSHOT stepper, never a range ──────────

    @Test
    fun the_control_is_a_single_date_not_a_two_ended_range() {
        // The one structural difference from Profit & Loss this task is
        // explicit about: no `end_date`/second date field anywhere here.
        val code = codeOnly(screen)
        assertThat(code).contains("asOfDate")
        assertThat(code).doesNotContain("end_date")
        assertThat(code).doesNotContain("endDate")
    }

    // ── Arabic coverage ──────────────────────────────────────────────────────

    @Test
    fun every_translated_string_on_the_screen_has_an_arabic_entry() {
        val used = trKeys(codeOnly(screen)).toSet()
        assertThat(used).isNotEmpty()
        assertThat(used.filterNot { it in catalog }.sorted()).isEmpty()
    }

    @Test
    fun the_entry_point_and_the_core_statement_strings_are_translated() {
        assertThat(catalog).containsAtLeast(
            "Balance Sheet",
            "Assets, liabilities and equity as of a date",
            "Assets",
            "Liabilities",
            "Equity",
            "Retained Earnings",
            "Total Equity",
            "Total Assets",
            "Total Liabilities and Equity",
            "Difference",
            "Balanced",
            "Out of Balance",
            "Couldn't verify",
        )
    }

    // ── Key-reuse near-miss check ─────────────────────────────────────────────

    @Test
    fun the_new_strings_never_collide_with_the_existing_credit_payment_method_key() {
        assertThat(codeOnly(screen)).doesNotContain("""tr("Credit")""")
    }
}
