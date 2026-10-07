package com.actionaura.retail.ui

import com.actionaura.retail.net.IncomeStatement
import com.actionaura.retail.net.IncomeStatementResponse
import com.actionaura.retail.net.OperatingExpenses
import com.actionaura.retail.ui.screens.StatementActivity
import com.actionaura.retail.ui.screens.costBasisWarning
import com.actionaura.retail.ui.screens.grossMarginPercentLabel
import com.actionaura.retail.ui.screens.incomeStatementActivity
import com.actionaura.retail.ui.screens.monthToDateRange
import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.time.LocalDate

/**
 * Reachability + honesty guards for the Profit & Loss screen
 * (retail-hardware-viewports, core/retail/financial_statements.py::
 * income_statement).
 *
 * Same shape and justification as TrialBalanceWiringContractTest: no Compose
 * test runner, no Chaquopy and no device here, so "is this screen actually
 * in the nav graph" and "does the activity/cost-basis verdict ever lie" are
 * asked by reading source and by calling the pure decision functions
 * directly.
 *
 * The functions this file exists to pin -- [incomeStatementActivity],
 * [costBasisWarning] and [monthToDateRange] -- are exactly the things this
 * screen must never get wrong: telling a shopkeeper a period broke even when
 * nothing was rung up at all, or that its cost of goods figure is trustworthy
 * when the server has flagged it as understated.
 */
class ProfitAndLossWiringContractTest {

    private val moduleRoot = File(".")
    // Gradle unit tests run with the module directory (android/aura-retail/app)
    // as the working directory -- see ReadinessContractTest -- so three levels
    // up is the aura-fullsuits root.
    private val suiteRoot = File("../../..")
    private val gson = Gson()

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val appRoot get() = source("src/main/java/com/actionaura/retail/ui/AppRoot.kt")
    private val extraScreens get() = source("src/main/java/com/actionaura/retail/ui/screens/RetailExtraScreens.kt")
    private val screen get() = source("src/main/java/com/actionaura/retail/ui/screens/ProfitAndLossScreen.kt")
    private val strings get() = source("src/main/java/com/actionaura/retail/ui/i18n/Strings.kt")
    private val apiSrc get() = source("src/main/java/com/actionaura/retail/net/AuraApi.kt")

    private val catalog get() = literalRuns(codeOnly(strings)).toSet()

    // ── Reachability: BOTH halves ────────────────────────────────────────────

    @Test
    fun the_profit_and_loss_route_is_registered_in_the_nav_graph() {
        val graph = appRoot.substringAfter("private fun retailGraph(")
        assertThat(graph).contains("""b.composable("profit_and_loss")""")
        assertThat(graph).contains("ProfitAndLossScreen(")
    }

    @Test
    fun something_actually_navigates_to_the_profit_and_loss_route() {
        // A registered route with no caller is still dead code -- see
        // EmployeeSalesWiringContractTest's identical test for the
        // precedent this repeats. The entry lives in ReportsScreen
        // (MoreItem, gated alongside "Trial Balance"), itself reached from
        // MoreScreen -- a path a user can actually walk: More -> Reports ->
        // Profit and Loss.
        assertThat(codeOnly(extraScreens)).contains("""onNavigate("profit_and_loss")""")
        assertThat(codeOnly(appRoot)).contains("ReportsScreen(snackbar, onNavigate =")
    }

    @Test
    fun the_screen_has_a_title_in_the_top_bar_map() {
        // Without an entry here the top bar falls through to "Action Aura",
        // so the user cannot tell which screen they are on.
        assertThat(appRoot).contains(""""profit_and_loss" -> "Profit and Loss"""")
    }

    @Test
    fun the_endpoint_is_declared_on_the_api_interface() {
        assertThat(codeOnly(apiSrc)).contains("""@GET("api/sub/retail/reports/profit-and-loss")""")
        assertThat(codeOnly(apiSrc)).contains("suspend fun profitAndLoss(")
        assertThat(codeOnly(apiSrc)).contains("""@Query("start_date") startDate: String""")
        assertThat(codeOnly(apiSrc)).contains("""@Query("end_date") endDate: String""")
    }

    // ── Capability gating: the same gate the desktop uses ────────────────────

    @Test
    fun the_reports_entry_is_capability_gated_and_the_screen_re_checks() {
        val code = codeOnly(extraScreens)
        // CHANGED (rehearsal fix B7, cfbf883f): server route moved from
        // retail.reports to retail.books.view; entry + screen follow. Cannot
        // catch the entry sitting under the old reports gate by itself (it
        // proves the gate text exists in the file); per-route card gates are
        // pinned in WriteGateContractTest/AccountantHomeContractTest.
        assertThat(code).contains("RetailSession.hasCapability(CAP_BOOKS_VIEW)")
        assertThat(code).contains("""MoreItem(tr("Profit and Loss")""")
        assertThat(codeOnly(screen)).contains("if (!RetailSession.hasCapability(CAP_BOOKS_VIEW))")
    }

    @Test
    fun the_backend_route_this_screen_calls_gates_on_the_same_capability() {
        // Read out of the Python that actually enforces it, so this fails
        // the day the route's gate moves or is renamed -- same reasoning
        // TrialBalanceWiringContractTest applies to CAP_REPORTS itself.
        val api = File(suiteRoot, "products/retail/backend/api/retail_api.py")
        assumeTrue("retail_api.py not reachable from this run context", api.exists())
        val text = api.readText()
        val routeStart = text.indexOf("def report_profit_and_loss(")
        assumeTrue("report_profit_and_loss not found in retail_api.py", routeStart >= 0)
        val decorators = text.substring(text.lastIndexOf("@retail_bp.route(", routeStart), routeStart)
        // CHANGED (B7): was CAP_REPORTS; still reads the real decorator.
        assertThat(decorators).contains("@mt_require_capability(CAP_BOOKS_VIEW)")
        assertThat(decorators).contains("""@retail_bp.route('/reports/profit-and-loss'""")
    }

    // ── The Gson explicit-null crash ─────────────────────────────────────────

    @Test
    fun a_null_body_is_refused_rather_than_rendered_as_an_empty_period() {
        // Same shape as TrialBalanceWiringContractTest's identical guard:
        // Gson writes an explicit JSON null straight into `data` regardless
        // of the Kotlin type. The load path has to throw into its own
        // catch, not default to a fresh IncomeStatement() -- which would
        // render a malformed 200 as a period with nothing posted, exactly
        // the "a failed load reads as an empty answer" lie this screen must
        // never tell.
        val parsed = gson.fromJson("""{"success":true,"data":null}""", IncomeStatementResponse::class.java)
        assertThat(parsed.success).isTrue()
        assertThat(parsed.data).isNull()

        val code = codeOnly(screen)
        assertThat(code).contains("?: throw")
        assertThat(code).contains("Malformed response")
    }

    @Test
    fun a_well_formed_body_parses_including_operating_expenses() {
        val body = """
            {"success": true, "data": {
              "currency": "JOD", "revenue": 1000.0, "gross_revenue": 1100.0,
              "sales_returns": 100.0, "cost_of_goods_sold": 600.0,
              "gross_profit": 400.0, "gross_margin": 0.4,
              "operating_expenses": {"total": 150.0, "accounts": [
                {"code": "5900", "name": "Rent", "amount": 150.0}
              ]},
              "net_profit": 250.0, "has_activity": true,
              "cost_basis_incomplete": false, "cost_basis_incomplete_sale_count": 0
            }}
        """.trimIndent()
        val parsed = gson.fromJson(body, IncomeStatementResponse::class.java)
        assertThat(parsed.data).isNotNull()
        assertThat(parsed.data!!.operating_expenses.accounts).hasSize(1)
        assertThat(parsed.data!!.operating_expenses.accounts[0].name).isEqualTo("Rent")
        assertThat(parsed.data!!.gross_margin).isEqualTo(0.4)
    }

    // ── THE ACTIVITY VERDICT -- never a lie in either direction ──────────────

    @Test
    fun a_period_with_no_activity_is_never_rendered_as_a_computed_zero() {
        // Both totals genuinely zero AND has_activity false -- a period
        // nothing was rung up in. Must read NO_ACTIVITY, never a "balanced"-
        // looking statement of zeros.
        val ps = IncomeStatement(revenue = 0.0, net_profit = 0.0, has_activity = false)
        assertThat(incomeStatementActivity(ps)).isEqualTo(StatementActivity.NO_ACTIVITY)
    }

    @Test
    fun a_period_with_real_activity_reads_as_has_activity_even_at_a_loss() {
        // A real, negative net_profit (a loss) must still read HAS_ACTIVITY
        // -- "no activity" and "a loss" are different claims, and confusing
        // them would hide a real loss behind an empty-state screen.
        val ps = IncomeStatement(revenue = 500.0, net_profit = -75.0, has_activity = true)
        assertThat(incomeStatementActivity(ps)).isEqualTo(StatementActivity.HAS_ACTIVITY)
    }

    @Test
    fun the_activity_verdict_is_read_from_the_servers_own_field_not_inferred_from_revenue() {
        // A period could have real ledger movement (e.g. an all-return
        // period) that nets to zero revenue but is NOT an empty period --
        // only the server's own has_activity answers that, never a
        // client-side "is revenue nonzero" guess.
        assertThat(codeOnly(screen)).contains("ps.has_activity")
    }

    // ── THE COST-BASIS WARNING -- never buried, never a false alarm ──────────

    @Test
    fun cost_basis_incomplete_true_shows_the_warning_with_the_real_count() {
        val ps = IncomeStatement(cost_basis_incomplete = true, cost_basis_incomplete_sale_count = 7)
        val warning = costBasisWarning(ps)
        assertThat(warning.visible).isTrue()
        assertThat(warning.affectedSaleCount).isEqualTo(7)
    }

    @Test
    fun cost_basis_incomplete_false_never_shows_the_warning() {
        val ps = IncomeStatement(cost_basis_incomplete = false, cost_basis_incomplete_sale_count = 0)
        assertThat(costBasisWarning(ps).visible).isFalse()
    }

    @Test
    fun the_cost_basis_flag_is_read_from_the_servers_own_field_not_recomputed() {
        assertThat(codeOnly(screen)).contains("ps.cost_basis_incomplete")
    }

    // ── Gross margin -- a RATIO, nullable, never a misleading 0% ─────────────

    @Test
    fun a_null_margin_reads_as_an_em_dash_never_zero_percent() {
        assertThat(grossMarginPercentLabel(null)).isEqualTo("—")
    }

    @Test
    fun a_real_margin_ratio_is_converted_to_a_percentage_not_shown_as_a_bare_fraction() {
        // 0.4 is 40%, not "0.4%" -- the ratio-vs-percentage confusion this
        // function exists to prevent, per IncomeStatement.gross_margin's own
        // doc comment.
        assertThat(grossMarginPercentLabel(0.4)).isEqualTo("40.0%")
        assertThat(grossMarginPercentLabel(0.4256)).isEqualTo("42.6%")
    }

    // ── Month-to-date range -- a calendar boundary, clamped only by today ────

    @Test
    fun the_current_month_is_clamped_to_today_not_the_calendar_month_end() {
        val monthStart = LocalDate.of(2026, 9, 1)
        val today = LocalDate.of(2026, 9, 23)
        val (start, end) = monthToDateRange(monthStart, today)
        assertThat(start).isEqualTo(monthStart)
        assertThat(end).isEqualTo(today)
    }

    @Test
    fun a_past_month_returns_its_own_full_calendar_range_not_clamped_by_today() {
        val monthStart = LocalDate.of(2026, 8, 1)
        val today = LocalDate.of(2026, 9, 23)
        val (start, end) = monthToDateRange(monthStart, today)
        assertThat(start).isEqualTo(monthStart)
        assertThat(end).isEqualTo(LocalDate.of(2026, 8, 31))
    }

    @Test
    fun a_short_february_is_not_overrun_into_march() {
        val monthStart = LocalDate.of(2026, 2, 1)
        val today = LocalDate.of(2026, 9, 23)
        val (_, end) = monthToDateRange(monthStart, today)
        assertThat(end).isEqualTo(LocalDate.of(2026, 2, 28))
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
    fun a_failed_load_is_not_rendered_as_a_freshly_computed_period() {
        val src = screen
        assertThat(src).contains("loadError")
        assertThat(src.indexOf("loadError != null"))
            .isLessThan(src.indexOf("ProfitAndLossBody("))
    }

    // ── Arabic coverage ──────────────────────────────────────────────────────

    @Test
    fun every_translated_string_on_the_screen_has_an_arabic_entry() {
        val used = trKeys(codeOnly(screen)).toSet()
        assertThat(used).isNotEmpty()
        assertThat(used.filterNot { it in catalog }.sorted()).isEmpty()
    }

    @Test
    fun the_screens_this_change_touches_have_no_untranslated_strings() {
        for (file in listOf(codeOnly(extraScreens), codeOnly(appRoot))) {
            val used = trKeys(file).toSet()
            assertThat(used).isNotEmpty()
            assertThat(used.filterNot { it in catalog }.sorted()).isEmpty()
        }
    }

    @Test
    fun the_entry_point_and_the_core_statement_strings_are_translated() {
        assertThat(catalog).containsAtLeast(
            "Profit and Loss",
            "Revenue, cost of goods and net profit for a period",
            "Gross Revenue",
            "Sales Returns",
            "Cost of Goods Sold",
            "Gross Profit",
            "Gross Margin",
            "Operating Expenses",
            "Net Profit",
            "Cost basis is incomplete for this period",
            "sale(s) affected",
            "Nothing was posted in this period",
        )
    }

    // ── Key-reuse near-miss check ─────────────────────────────────────────────

    @Test
    fun the_new_strings_never_collide_with_the_existing_credit_payment_method_key() {
        // "Credit" is already the on-credit PAYMENT METHOD label ("آجل") --
        // this screen never introduces a bare "Credit" key for an accounting
        // concept, which would either silently reuse the wrong translation
        // or require overwriting the map entry (map literals with a
        // duplicate key silently keep only the last one).
        assertThat(codeOnly(screen)).doesNotContain("""tr("Credit")""")
    }
}
