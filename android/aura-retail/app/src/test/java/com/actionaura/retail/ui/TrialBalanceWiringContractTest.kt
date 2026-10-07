package com.actionaura.retail.ui

import com.actionaura.retail.net.SubledgerTie
import com.actionaura.retail.net.TrialBalance
import com.actionaura.retail.net.TrialBalanceResponse
import com.actionaura.retail.ui.screens.TieState
import com.actionaura.retail.ui.screens.TrialBalanceStatus
import com.actionaura.retail.ui.screens.accountTypeLabel
import com.actionaura.retail.ui.screens.apTieState
import com.actionaura.retail.ui.screens.arTieState
import com.actionaura.retail.ui.screens.trialBalanceStatus
import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Reachability + honesty guards for the Trial Balance screen (retail-
 * hardware-viewports, schema v38/v39 general ledger).
 *
 * Same shape and justification as EmployeeSalesWiringContractTest: no Compose
 * test runner, no Chaquopy and no device here, so "is this screen actually in
 * the nav graph" and "does the balanced/tie verdict ever lie" are asked by
 * reading source and by calling the pure decision functions directly.
 *
 * The two functions this file exists to pin -- [trialBalanceStatus] and
 * [arTieState]/[apTieState] -- are exactly the two things this screen must
 * never get wrong: telling a shopkeeper their books balance when they do not,
 * or that a subledger ties back when it does not (or when the server never
 * said either way).
 */
class TrialBalanceWiringContractTest {

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
    private val screen get() = source("src/main/java/com/actionaura/retail/ui/screens/TrialBalanceScreen.kt")
    private val strings get() = source("src/main/java/com/actionaura/retail/ui/i18n/Strings.kt")
    private val apiSrc get() = source("src/main/java/com/actionaura/retail/net/AuraApi.kt")

    private val catalog get() = literalRuns(codeOnly(strings)).toSet()

    // ── Reachability: BOTH halves ────────────────────────────────────────────

    @Test
    fun the_trial_balance_route_is_registered_in_the_nav_graph() {
        val graph = appRoot.substringAfter("private fun retailGraph(")
        assertThat(graph).contains("""b.composable("trial_balance")""")
        assertThat(graph).contains("TrialBalanceScreen(")
    }

    @Test
    fun something_actually_navigates_to_the_trial_balance_route() {
        // A registered route with no caller is still dead code -- see
        // EmployeeSalesWiringContractTest's identical test for the precedent
        // this repeats. The entry lives in ReportsScreen (MoreItem, gated
        // alongside "By Employee"), itself reached from MoreScreen -- a path
        // a user can actually walk: More -> Reports -> Trial Balance.
        assertThat(codeOnly(extraScreens)).contains("""onNavigate("trial_balance")""")
        assertThat(codeOnly(appRoot)).contains("ReportsScreen(snackbar, onNavigate =")
    }

    @Test
    fun the_screen_has_a_title_in_the_top_bar_map() {
        // Without an entry here the top bar falls through to "Action Aura",
        // so the user cannot tell which screen they are on.
        assertThat(appRoot).contains(""""trial_balance" -> "Trial Balance"""")
    }

    @Test
    fun the_endpoint_is_declared_on_the_api_interface() {
        assertThat(codeOnly(apiSrc)).contains("""@GET("api/sub/retail/reports/trial-balance")""")
        assertThat(codeOnly(apiSrc)).contains("suspend fun trialBalance(): TrialBalanceResponse")
    }

    // ── Capability gating: the same gate the desktop uses ────────────────────

    @Test
    fun the_reports_entry_is_capability_gated_and_the_screen_re_checks() {
        // Hiding the entry is usability; the screen's own check is what makes
        // a stale session or a future deep link honest rather than a wall of
        // 403s. Both halves have to be present -- same pairing
        // EmployeeSalesWiringContractTest pins for its sibling entry, and
        // both entries share the SAME `if` block in ReportsScreen.
        val code = codeOnly(extraScreens)
        // CHANGED (rehearsal fix B7, cfbf883f): server route moved from
        // retail.reports to retail.books.view; entry + screen follow. Cannot
        // catch the entry sitting under the old reports gate by itself (it
        // proves the gate text exists in the file); per-route card gates are
        // pinned in WriteGateContractTest/AccountantHomeContractTest.
        assertThat(code).contains("RetailSession.hasCapability(CAP_BOOKS_VIEW)")
        assertThat(code).contains("""MoreItem(tr("Trial Balance")""")
        assertThat(codeOnly(screen)).contains("if (!RetailSession.hasCapability(CAP_BOOKS_VIEW))")
    }

    @Test
    fun the_backend_route_this_screen_calls_gates_on_the_same_capability() {
        // Read out of the Python that actually enforces it, so this fails the
        // day the route's gate moves or is renamed -- same reasoning
        // EmployeeSalesWiringContractTest applies to CAP_REPORTS itself.
        val api = File(suiteRoot, "products/retail/backend/api/retail_api.py")
        assumeTrue("retail_api.py not reachable from this run context", api.exists())
        val text = api.readText()
        val routeStart = text.indexOf("def report_trial_balance(")
        assumeTrue("report_trial_balance not found in retail_api.py", routeStart >= 0)
        // The decorator stack sits immediately ABOVE the def line -- searched
        // backward from it rather than forward, so this cannot accidentally
        // match a later route's decorators instead.
        val decorators = text.substring(text.lastIndexOf("@retail_bp.route(", routeStart), routeStart)
        // CHANGED (B7): was CAP_REPORTS; still reads the real decorator.
        assertThat(decorators).contains("@mt_require_capability(CAP_BOOKS_VIEW)")
        assertThat(decorators).contains("""@retail_bp.route('/reports/trial-balance'""")
    }

    // ── The Gson explicit-null crash ─────────────────────────────────────────

    @Test
    fun a_null_body_is_refused_rather_than_rendered_as_an_empty_ledger() {
        // Same shape as EmployeeSalesWiringContractTest's identical guard:
        // Gson writes an explicit JSON null straight into `data` regardless
        // of the Kotlin type. The load path has to throw into its own catch,
        // not default to a fresh TrialBalance() -- which would render a
        // malformed 200 as a freshly-seeded, all-zero ledger, exactly the
        // "zeros implying balanced books" lie this screen must never tell.
        val parsed = gson.fromJson("""{"success":true,"data":null}""", TrialBalanceResponse::class.java)
        assertThat(parsed.success).isTrue()
        assertThat(parsed.data).isNull()

        val code = codeOnly(screen)
        assertThat(code).contains("?: throw")
        assertThat(code).contains("Malformed response")
    }

    @Test
    fun a_well_formed_body_parses_including_the_subledger() {
        val body = """
            {"success": true, "data": {
              "currency": "JOD",
              "accounts": [{"code":"1100","name":"Accounts Receivable","account_type":"asset",
                            "normal_balance":"debit","debit":500.0,"credit":0.0,"balance":500.0}],
              "total_debit": 500.0, "total_credit": 500.0, "is_balanced": true,
              "subledger": {"ar_gl":500.0,"ar_subledger":500.0,"ar_ties":true,
                            "ap_gl":0.0,"ap_subledger":0.0,"ap_ties":true}
            }}
        """.trimIndent()
        val parsed = gson.fromJson(body, TrialBalanceResponse::class.java)
        assertThat(parsed.data).isNotNull()
        assertThat(parsed.data!!.accounts).hasSize(1)
        assertThat(parsed.data!!.accounts[0].code).isEqualTo("1100")
        assertThat(parsed.data!!.subledger).isNotNull()
        assertThat(parsed.data!!.subledger!!.ar_ties).isTrue()
    }

    // ── THE BALANCED VERDICT -- never a lie in either direction ──────────────

    @Test
    fun an_unbalanced_ledger_is_never_reported_as_balanced() {
        val tb = TrialBalance(total_debit = 620.0, total_credit = 590.0, is_balanced = false)
        assertThat(trialBalanceStatus(tb)).isEqualTo(TrialBalanceStatus.OUT_OF_BALANCE)
    }

    @Test
    fun a_genuinely_balanced_and_posted_ledger_reads_as_balanced() {
        val tb = TrialBalance(total_debit = 500.0, total_credit = 500.0, is_balanced = true)
        assertThat(trialBalanceStatus(tb)).isEqualTo(TrialBalanceStatus.BALANCED)
    }

    @Test
    fun a_ledger_with_nothing_posted_is_never_shown_as_balanced_even_though_zero_equals_zero() {
        // Both totals exactly zero -- a brand-new shop's seeded, zeroed chart
        // of accounts. is_balanced is trivially TRUE (0.0 == 0.0), but
        // reporting that as "Balanced" would claim the books were audited
        // and agree, when the true fact is that nothing has been rung up at
        // all. This is the exact case the task's own wording warns about:
        // "never as zeros implying balanced books."
        val tb = TrialBalance(total_debit = 0.0, total_credit = 0.0, is_balanced = true)
        assertThat(trialBalanceStatus(tb)).isEqualTo(TrialBalanceStatus.NOTHING_POSTED)
    }

    @Test
    fun the_balanced_verdict_is_read_from_the_servers_own_field_not_recomputed() {
        // gl.trial_balance already applies the currency's own half-minor-unit
        // epsilon server-side (general_ledger.py's `_gl_epsilon`). This client
        // must consume `is_balanced` as sent, never re-derive it from
        // total_debit/total_credit with its own tolerance -- a second
        // definition of "balanced" is exactly the kind of drift CLAUDE.md's
        // money-epsilon correction warns about.
        assertThat(codeOnly(screen)).contains("tb.is_balanced")
        assertThat(codeOnly(screen)).doesNotContain("total_debit - tb.total_credit")
        assertThat(codeOnly(screen)).doesNotContain("total_debit - total_credit")
    }

    // ── THE TIE VERDICT -- never a lie in either direction ───────────────────

    @Test
    fun a_receivables_subledger_that_does_not_tie_is_never_reported_as_tying() {
        val sl = SubledgerTie(ar_gl = 500.0, ar_subledger = 300.0, ar_ties = false)
        assertThat(arTieState(sl)).isEqualTo(TieState.DOES_NOT_TIE)
    }

    @Test
    fun a_payables_subledger_that_does_not_tie_is_never_reported_as_tying() {
        val sl = SubledgerTie(ap_gl = 700.0, ap_subledger = 650.0, ap_ties = false)
        assertThat(apTieState(sl)).isEqualTo(TieState.DOES_NOT_TIE)
    }

    @Test
    fun subledgers_that_genuinely_tie_read_as_tying() {
        val sl = SubledgerTie(ar_gl = 500.0, ar_subledger = 500.0, ar_ties = true,
            ap_gl = 700.0, ap_subledger = 700.0, ap_ties = true)
        assertThat(arTieState(sl)).isEqualTo(TieState.TIES)
        assertThat(apTieState(sl)).isEqualTo(TieState.TIES)
    }

    @Test
    fun a_missing_subledger_is_unknown_never_defaulted_to_either_verdict() {
        // The desktop defaults a missing `subledger` to `{}` and reads
        // `sl.ar_ties` off it, so a malformed/absent subledger there renders
        // the same red "Does Not Tie" alarm as a real mismatch -- an alarm
        // the server never raised, over data it never sent. This client
        // keeps the two apart: null answers UNKNOWN, never DOES_NOT_TIE and
        // never TIES.
        assertThat(arTieState(null)).isEqualTo(TieState.UNKNOWN)
        assertThat(apTieState(null)).isEqualTo(TieState.UNKNOWN)
    }

    @Test
    fun the_tie_verdicts_are_read_from_the_servers_own_fields_not_recomputed() {
        // gl.subledger_tie already applies the currency's own epsilon
        // server-side. Recomputing `ar_gl == ar_subledger` here with a
        // client-side tolerance (or none at all) is a second, independent
        // definition of "ties" that can disagree with the server's.
        val code = codeOnly(screen)
        assertThat(code).contains("subledger?.ar_ties")
        assertThat(code).contains("subledger?.ap_ties")
    }

    // ── account_type labelling ────────────────────────────────────────────────

    @Test
    fun every_default_chart_account_type_resolves_to_a_real_label() {
        // DEFAULT_CHART (general_ledger.py) seeds exactly these five types.
        // None may fall through to "Other" -- that fallback exists for a
        // FUTURE unseeded type, not for one this route already emits today.
        for (type in listOf("asset", "liability", "equity", "revenue", "expense")) {
            assertThat(accountTypeLabel(type)).isNotEqualTo("Other")
        }
    }

    @Test
    fun revenue_is_deliberately_not_left_on_the_desktops_dead_income_key() {
        // The desktop's own _TRIAL_BALANCE_TYPE_LABEL map keys 'income', not
        // 'revenue' -- see accountTypeLabel()'s doc comment. This asserts the
        // client-visible OUTCOME of that correction: a revenue account still
        // gets the real translated word ("Income"/"إيراد"), not the desktop's
        // fallback.
        assertThat(accountTypeLabel("revenue")).isEqualTo("Income")
        assertThat(catalog).contains("Income")
    }

    @Test
    fun an_unrecognised_account_type_is_a_fixed_safe_label_never_the_raw_value() {
        // Matches the desktop's own defensive shape (_glAccountTypeLabel):
        // an unrecognised type is display noise, not a reason to trust an
        // arbitrary server string into the UI untranslated.
        assertThat(accountTypeLabel("something-new")).isEqualTo("Other")
        assertThat(accountTypeLabel(null)).isEqualTo("Other")
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
    fun a_failed_load_is_not_rendered_as_a_freshly_seeded_ledger() {
        // Collapsing "the call failed" into "nothing posted yet" is the same
        // shape of lie as collapsing it into "balanced" -- both tell an owner
        // their books are fine when the real fact is that the session
        // expired or the network dropped. The error branch must be distinct
        // and must come before the body is ever rendered.
        val src = screen
        assertThat(src).contains("loadError")
        assertThat(src.indexOf("loadError != null ->"))
            .isLessThan(src.indexOf("TrialBalanceBody("))
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
        // RetailExtraScreens.kt carries the MoreItem entry point, AppRoot.kt
        // the nav title -- both are this feature's problem the same way
        // EmployeeSalesWiringContractTest scopes its identical check.
        for (file in listOf(codeOnly(extraScreens), codeOnly(appRoot))) {
            val used = trKeys(file).toSet()
            assertThat(used).isNotEmpty()
            assertThat(used.filterNot { it in catalog }.sorted()).isEmpty()
        }
    }

    @Test
    fun the_entry_point_and_the_core_report_strings_are_translated() {
        assertThat(catalog).containsAtLeast(
            "Trial Balance",
            "Chart of accounts and the AR/AP tie-back",
            "Balanced",
            "Out of Balance",
            "Nothing posted yet",
            "Ties",
            "Does Not Tie",
            "Couldn't verify",
        )
    }
}
