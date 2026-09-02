package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * ONE overflow surface, and Log out reachable inside it.
 *
 * Same shape and justification as BranchPinWiringContractTest and
 * EmployeeSalesWiringContractTest: no Compose test runner and no device in
 * this environment, but "can a user actually reach this control" is exactly
 * the regression that stays invisible until somebody goes looking.
 *
 * WHY THIS EXISTS
 *
 * The app shipped with TWO competing overflow surfaces: a bottom "More" list
 * AND a ModalNavigationDrawer opened by a hamburger or an edge-swipe. The
 * drawer held exactly two entries -- Settings, which MoreScreen already lists
 * under Finance, and Log out. So a gesture, a button and a full-height panel
 * existed to deliver ONE destination not reachable anywhere else.
 *
 * The owner, after using it on a real phone: "its inconvenient to swipe left
 * and there is 2 things and it looks bad." That is a literal description --
 * swipe left, two items.
 *
 * The drawer is deleted and Log out lives in MoreScreen. These tests stop it
 * growing back, and stop Log out being stranded if someone edits that list.
 */
class OverflowNavigationContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val appRoot get() = source("src/main/java/com/actionaura/retail/ui/AppRoot.kt")
    private val extraScreens get() =
        source("src/main/java/com/actionaura/retail/ui/screens/RetailExtraScreens.kt")
    private val strings get() =
        source("src/main/java/com/actionaura/retail/ui/i18n/Strings.kt")

    /** MoreScreen's own body, bounded so an assertion about it cannot be
     *  satisfied by some other composable further down this large file. */
    private val moreScreenBody: String get() =
        codeOnly(extraScreens)
            .substringAfter("fun MoreScreen(")
            .substringBefore("private fun MoreItem(")

    // ── There is exactly ONE overflow surface ────────────────────────────────

    @Test
    fun there_is_no_navigation_drawer() {
        val code = codeOnly(appRoot)
        for (symbol in listOf("ModalNavigationDrawer", "ModalDrawerSheet",
                              "NavigationDrawerItem", "rememberDrawerState")) {
            assertThat(code).doesNotContain(symbol)
        }
    }

    @Test
    fun the_top_bar_has_no_menu_button_left_behind() {
        // A hamburger that opens nothing is worse than no hamburger. If the
        // drawer ever returns, this fails alongside the test above rather than
        // leaving a dead control on every top-level screen.
        assertThat(codeOnly(appRoot)).doesNotContain("Icons.Default.Menu")
    }

    // ── Log out survived the move ────────────────────────────────────────────

    @Test
    fun log_out_is_reachable_from_the_more_screen() {
        val body = moreScreenBody
        assertThat(body).contains("""tr("Log out")""")
        assertThat(body).contains("onLogout()")
    }

    @Test
    fun logging_out_still_ends_the_server_session_and_clears_local_state() {
        // The drawer's Log out did all three. Losing any one of them would
        // leave a device that LOOKS logged out while the session it holds is
        // still valid -- which is a security regression, not a UI one.
        val code = codeOnly(appRoot)
        assertThat(code).contains("ApiClient.get().logout()")
        assertThat(code).contains("RetailSession.reset()")
        assertThat(code).contains("onLogout()")
    }

    @Test
    fun no_bottom_tab_destination_is_repeated_in_the_more_list() {
        // The deleted drawer's real sin was duplication: it offered Settings,
        // which MoreScreen already had. The same trap is open in the other
        // direction now that the bar has five slots -- a destination promoted
        // to a tab must not also sit in the overflow list, or the app is once
        // again teaching two routes to the same place.
        val tabRoutes = Regex("""Dest\("([a-z_]+)"""")
            .findAll(codeOnly(appRoot)).map { it.groupValues[1] }.toList()
        assertThat(tabRoutes).isNotEmpty()

        val body = moreScreenBody
        for (route in tabRoutes) {
            assertThat(body).doesNotContain("""onNavigate("$route")""")
        }
    }

    @Test
    fun settings_is_not_duplicated_now_that_the_drawer_is_gone() {
        // MoreScreen already offered Settings under Finance; the drawer's copy
        // was the duplicate. Exactly one entry should navigate there.
        val occurrences = Regex("""onNavigate\("retail_settings"\)""")
            .findAll(codeOnly(extraScreens)).count()
        assertThat(occurrences).isEqualTo(1)
    }

    // ── Arabic ───────────────────────────────────────────────────────────────

    @Test
    fun the_session_section_strings_are_translated() {
        val catalog = literalRuns(codeOnly(strings)).toSet()
        assertThat(catalog).containsAtLeast(
            "Session",
            "End this session on this device",
            "Log out",
        )
    }

    @Test
    fun every_translated_string_the_more_screen_uses_is_in_the_catalogue() {
        val catalog = literalRuns(codeOnly(strings)).toSet()
        val used = trKeys(moreScreenBody).toSet()
        assertThat(used).isNotEmpty()
        assertThat(used.filterNot { it in catalog }.sorted()).isEmpty()
    }
}
