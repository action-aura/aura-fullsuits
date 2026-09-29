package com.actionaura.retail.ui

import com.actionaura.retail.net.SessionResponse
import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Registry v9: an Accountant (books codes, NO `retail.sell`) must not land on,
 * or be one tap from, the till.
 *
 * Before this, MainShell hard-coded `startDestination = "dashboard"` and a tab
 * strip with POS in it. An accountant opened the app on a till that would 403
 * every ring, which reads to a shopkeeper as "the app is broken", not "you
 * have a reading job".
 *
 * There is no Compose test runner and no device here, so two kinds of check:
 *  - BEHAVIOUR on the pure routing seam (`isBooksOnly`, `homeRouteFor`,
 *    `RetailSession.isBooksOnly`) -- these run for real.
 *  - SOURCE-TEXT wiring on AppRoot.kt (comments stripped by [codeOnly]) for
 *    the parts only a running Compose tree could exercise. That is weaker: it
 *    proves the wiring is written, not that a phone renders it. Reported as
 *    contract-tested, not proved on a device.
 */
class AccountantHomeContractTest {

    private val moduleRoot = File(".")
    private val gson = Gson()

    private fun source(relative: String): String {
        val f = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", f.exists())
        return f.readText()
    }

    private val appRoot get() = codeOnly(source("src/main/java/com/actionaura/retail/ui/AppRoot.kt"))

    private val accountantBody = """
        {"authenticated": true,
         "capabilities": ["retail.books.view", "retail.books.record", "retail.reports", "retail.cash.approve"],
         "user": {"id": "u-9", "role": "manager", "role_preset": "accountant"}}
    """.trimIndent()

    private val cashierBody = """
        {"authenticated": true,
         "capabilities": ["retail.sell", "retail.refund"],
         "user": {"id": "u-3", "role": "cashier"}}
    """.trimIndent()

    // ── The routing decision, for real ───────────────────────────────────────

    @Test
    fun a_books_only_user_is_sent_to_the_books_home_and_everyone_else_to_the_dashboard() {
        assertThat(homeRouteFor(true)).isEqualTo(ROUTE_BOOKS_HOME)
        assertThat(homeRouteFor(false)).isEqualTo(ROUTE_DASHBOARD)
        assertThat(ROUTE_BOOKS_HOME).isNotEqualTo(ROUTE_DASHBOARD)
        // Neither may be the till: that is the whole point.
        assertThat(homeRouteFor(true)).isNotEqualTo("pos")
    }

    @Test
    fun an_accountant_session_lands_on_books_and_a_cashier_session_does_not() {
        try {
            RetailSession.adopt(gson.fromJson(accountantBody, SessionResponse::class.java))
            assertThat(homeRouteFor(RetailSession.isBooksOnly)).isEqualTo(ROUTE_BOOKS_HOME)
            RetailSession.reset()

            RetailSession.adopt(gson.fromJson(cashierBody, SessionResponse::class.java))
            assertThat(homeRouteFor(RetailSession.isBooksOnly)).isEqualTo(ROUTE_DASHBOARD)
        } finally {
            RetailSession.reset()
        }
    }

    @Test
    fun a_user_with_books_codes_AND_sell_stays_on_the_normal_shell() {
        // A manager preset holds sell and may also be granted books by hand.
        // Taking their till away because they gained a reading right would be
        // the reverse bug.
        val both = """
            {"authenticated": true,
             "capabilities": ["retail.sell", "retail.books.view", "retail.reports"],
             "user": {"id": "u-4", "role": "manager"}}
        """.trimIndent()
        try {
            RetailSession.adopt(gson.fromJson(both, SessionResponse::class.java))
            assertThat(RetailSession.isBooksOnly).isFalse()
        } finally {
            RetailSession.reset()
        }
    }

    @Test
    fun unknown_capabilities_keep_the_normal_shell_rather_than_stranding_anyone() {
        // Before the session is adopted (or against an older server that sends
        // no list) capabilities are null. holdsCapability fails OPEN there; the
        // routing must fail the same way, or a cold start would show the books
        // shell to a cashier for a frame -- or forever.
        assertThat(isBooksOnly(null)).isFalse()
        RetailSession.reset()
        assertThat(RetailSession.isBooksOnly).isFalse()
        assertThat(homeRouteFor(RetailSession.isBooksOnly)).isEqualTo(ROUTE_DASHBOARD)
    }

    // ── AppRoot wiring (source text) ─────────────────────────────────────────

    @Test
    fun the_start_destination_comes_from_the_routing_decision_not_a_literal() {
        assertThat(appRoot).contains("startDestination = homeRouteFor(booksOnly)")
        assertThat(appRoot).doesNotContain("""startDestination = "dashboard"""")
        assertThat(appRoot).contains("val booksOnly = RetailSession.isBooksOnly")
    }

    @Test
    fun the_books_only_tab_strip_has_no_pos_and_no_till_entry_points() {
        val strip = appRoot.substringAfter("private val booksOnlyTabs = listOf(")
            .substringBefore(") private data class")
        assertThat(strip).contains("Dest(ROUTE_BOOKS_HOME,")
        assertThat(strip).doesNotContain("\"pos\"")
        assertThat(strip).doesNotContain("ROUTE_DASHBOARD")
        // the shell picks it by the same flag
        assertThat(appRoot).contains("val tabs = if (booksOnly) booksOnlyTabs else retailTabs")
    }

    @Test
    fun the_books_home_is_registered_in_the_nav_graph_and_the_dashboard_still_is() {
        val graph = appRoot.substringAfter("private fun retailGraph(")
        assertThat(graph).contains("b.composable(ROUTE_BOOKS_HOME)")
        assertThat(graph).contains("BooksHomeScreen(")
        assertThat(graph).contains("b.composable(ROUTE_DASHBOARD)")
    }

    @Test
    fun the_books_home_only_lists_read_only_screens_each_gated_on_a_capability() {
        val list = appRoot.substringAfter("private val booksEntries = listOf(")
            .substringBefore(") @Composable")
        // Front door for viewers only: none of these routes may be one that
        // records, approves or closes.
        val routes = Regex("""BooksEntry\("(\w+)"""").findAll(list).map { it.groupValues[1] }.toList()
        assertThat(routes).containsExactly(
            "trial_balance", "profit_and_loss", "balance_sheet",
            "reports", "receivables", "payables", "cash_summary", "aging")
        // every entry carries a `needs` from the capability constants
        assertThat(Regex("""BooksEntry\(""").findAll(list).count()).isEqualTo(routes.size)
        assertThat(list).doesNotContain("CAP_SELL")
        // and the screen filters by it
        assertThat(appRoot).contains("booksEntries.filter { RetailSession.hasCapability(it.needs) }")
    }

    @Test
    fun every_books_home_route_is_a_real_registered_screen() {
        // A card that navigates to an unregistered route crashes the nav
        // controller on tap -- on the accountant's ONLY screen.
        val graph = appRoot.substringAfter("private fun retailGraph(")
        for (r in listOf("trial_balance", "profit_and_loss", "balance_sheet",
            "reports", "receivables", "payables", "cash_summary", "aging")) {
            assertThat(graph).contains("b.composable(\"$r\")")
        }
    }

    @Test
    fun the_books_home_strings_have_arabic_entries() {
        val strings = source("src/main/java/com/actionaura/retail/ui/i18n/Strings.kt")
        val catalog = Regex(""""((?:\\.|[^"\\])*)"\s+to""").findAll(strings)
            .map { it.groupValues[1] }.toSet()
        assertThat(catalog).containsAtLeast(
            "Books",
            "You can read the books and reports here. Selling at the till is not part of this account.",
            "Every account, debits against credits.",
            "Income minus cost and expenses for a period.",
            "What the business owns and owes.",
            "Sales and stock reports.",
            "What customers owe.",
            "What the shop owes suppliers.",
            "Daily cash in and out.",
            "How overdue each balance is.",
            "Trial Balance", "Profit and Loss", "Balance Sheet",
            "Reports", "Receivables", "Payables", "Cash Summary", "Aging",
        )
    }

    @Test
    fun the_existing_session_lifecycle_is_untouched() {
        // The routing reads RetailSession, so adopt-at-login and reset-at-logout
        // must still be wired exactly once each or the shell would be chosen
        // from a previous user's capabilities.
        assertThat(appRoot).contains("RetailSession.adopt(session)")
        assertThat(appRoot).contains("RetailSession.reset()")
    }
}
