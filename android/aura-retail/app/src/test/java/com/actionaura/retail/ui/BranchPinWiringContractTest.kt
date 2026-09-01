package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Reachability + non-duplication guards for the this-device branch pin (Wave
 * C1, the Android half of the dc22b04 defect -- see net/Models.kt's Branch/
 * DeviceBranch doc comments).
 *
 * Same shape and same justification as EmployeesWiringContractTest and
 * EmployeeSalesWiringContractTest: no Compose test runner, no Chaquopy and no
 * device in this environment, but "is this control actually on the screen a
 * user reaches" is precisely the regression that stays invisible until an
 * owner goes looking for a feature that was written and never wired.
 *
 * Not hypothetical here either. Commit 0c6c3ea wrote the branch-pin section
 * into SettingsScreen.kt -- the exact file both siblings above name as the
 * one screen in this module that was fully written and never registered on
 * any route. So the pin shipped and never rendered for a single user, for
 * the same reason the employees screen and the by-employee report almost
 * did. The fix moved the section into RetailSettingsScreen (the real
 * "retail_settings" destination) and deleted the orphan; these tests exist
 * so the same class of bug cannot happen a second time to the same feature,
 * and so a future edit cannot quietly duplicate it back into a dead file.
 */
class BranchPinWiringContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val appRoot get() = source("src/main/java/com/actionaura/retail/ui/AppRoot.kt")
    private val extraScreens get() = source("src/main/java/com/actionaura/retail/ui/screens/RetailExtraScreens.kt")
    private val orphanPath = "src/main/java/com/actionaura/retail/ui/screens/SettingsScreen.kt"

    /**
     * RetailSettingsScreen's own body (plus the BranchPickerDialog that
     * follows it in the same file), comments stripped, bounded to the next
     * unrelated screen so an assertion about THIS control cannot be satisfied
     * by code that belongs to PurchaseOrdersScreen or anything else in this
     * large file.
     */
    private val retailSettingsBody: String get() =
        codeOnly(extraScreens)
            .substringAfter("fun RetailSettingsScreen(")
            .substringBefore("fun PurchaseOrdersScreen(")

    // ── (1) Reachability: the control is on the screen users actually reach ──

    @Test
    fun the_retail_settings_route_is_registered_in_the_nav_graph() {
        val graph = appRoot.substringAfter("private fun retailGraph(")
        assertThat(graph).contains("""b.composable("retail_settings")""")
        assertThat(graph).contains("RetailSettingsScreen(")
    }

    @Test
    fun the_branch_pin_control_is_part_of_retail_settings_screen() {
        // Bounded to RetailSettingsScreen's own body (see retailSettingsBody)
        // with comments stripped, so this only goes green for the real
        // control wired to the real API calls -- not for a doc comment that
        // merely talks about one, and not for a copy sitting in some other
        // screen in this file.
        val body = retailSettingsBody
        assertThat(body).contains("""tr("This Device's Branch")""")
        assertThat(body).contains("ApiClient.get().deviceBranch()")
        assertThat(body).contains("ApiClient.get().setDeviceBranch(")
        assertThat(body).contains("showBranchPicker")
        assertThat(body).contains("BranchPickerDialog(")
    }

    // ── (2) NOT left behind in the unrouted orphan ────────────────────────────

    @Test
    fun the_branch_pin_is_not_duplicated_into_the_unrouted_settings_screen() {
        // The clean outcome is that the orphan is gone outright -- there is
        // nothing left in it to render twice, wrongly, for nobody. If it ever
        // comes back (a revert, a merge, a future edit copying from history),
        // this stops it carrying the branch pin a second time regardless.
        val file = File(moduleRoot, orphanPath)
        if (!file.exists()) return
        assertThat(codeOnly(file.readText())).doesNotContain("This Device's Branch")
    }

    // ── (3) The unpinned state is surfaced distinctly, not silently ──────────

    @Test
    fun the_no_branch_pinned_state_is_shown_and_coloured_amber_not_red() {
        val body = retailSettingsBody
        assertThat(body).contains("""tr("No branch pinned")""")
        assertThat(body).contains("pinnedBranchName == null")
        // Amber (Warning), the same idiom this app already uses for
        // correct-but-noticeable states -- never the error/red used for an
        // actual fault, because a single-branch shop is a fine, common state.
        assertThat(body).contains("if (unpinned) Warning else")
        assertThat(body).contains("Icons.Default.WarningAmber")
    }

    // ── (4) No CAP_EMPLOYEES => read-only view, not a broken control ─────────

    @Test
    fun a_user_without_cap_employees_gets_the_read_only_view() {
        val body = retailSettingsBody
        assertThat(body).contains("RetailSession.hasCapability(CAP_EMPLOYEES)")
        assertThat(body).contains(
            "Only the owner can change which branch this device is pinned to. Ask the owner to make the change on their account.",
        )
        // The picker only ever opens through the gate -- never unconditionally.
        assertThat(body).contains("if (canManageBranch) it.clickable")
    }

    @Test
    fun the_403_on_save_is_reported_as_a_role_problem_not_a_licensing_one() {
        // A cashier's client-side gate can be stale; the server's own 403 is
        // what actually decides, and it must not fall through to
        // apiErrorMessage()'s generic "blocked by your subscription/licence"
        // wording, which would misname a role problem as a billing one.
        val body = codeOnly(extraScreens)
        assertThat(body).contains("e is HttpException && e.code() == 403")
        assertThat(body).contains(
            "Only the owner can change this device's branch. Ask the owner to make the change on their account.",
        )
    }

    // ── Arabic coverage ────────────────────────────────────────────────────

    @Test
    fun the_moved_strings_still_have_arabic_entries() {
        val catalog = literalRuns(codeOnly(
            source("src/main/java/com/actionaura/retail/ui/i18n/Strings.kt"))).toSet()
        assertThat(catalog).containsAtLeast(
            "This Device's Branch",
            "No branch pinned",
            "Couldn't load this device's branch",
            "This device's branch",
            "Only the owner can change which branch this device is pinned to. Ask the owner to make the change on their account.",
            "Only the owner can change this device's branch. Ask the owner to make the change on their account.",
        )
    }

    @Test
    fun every_translated_string_the_control_uses_is_in_the_catalogue() {
        val catalog = literalRuns(codeOnly(
            source("src/main/java/com/actionaura/retail/ui/i18n/Strings.kt"))).toSet()
        val used = trKeys(retailSettingsBody).toSet()
        assertThat(used).isNotEmpty()
        assertThat(used.filterNot { it in catalog }.sorted()).isEmpty()
    }
}
