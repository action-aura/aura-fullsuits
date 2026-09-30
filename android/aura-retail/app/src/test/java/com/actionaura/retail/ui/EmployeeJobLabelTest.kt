package com.actionaura.retail.ui

import com.actionaura.retail.ui.screens.ASSIGNABLE_PRESETS
import com.actionaura.retail.ui.screens.DISPLAY_NAME_MAX
import com.actionaura.retail.ui.screens.PRESET_BASE_ROLE
import com.actionaura.retail.ui.screens.buildCreateEmployeeRequest
import com.actionaura.retail.ui.screens.cleanName
import com.actionaura.retail.ui.screens.employeeJobLabel
import com.actionaura.retail.ui.screens.matchedPreset
import com.actionaura.retail.ui.screens.nameLength
import com.actionaura.retail.ui.screens.nameProblem
import com.actionaura.retail.ui.screens.shouldConfirmPresetChange
import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * BEHAVIOUR of the employee-screen decision functions the A2 fix round changed.
 *
 * These call the real functions and assert what comes back. They exist because
 * the first version of these guards were source-text checks, and the verifier
 * showed a source-text check cannot tell "the label is right" from "the label
 * function is called": inverting the one `if` inside [employeeJobLabel] left
 * every one of them green.
 */
class EmployeeJobLabelTest {

    // ── the job label (fix 1) ────────────────────────────────────────────────

    @Test
    fun a_preset_the_server_says_no_longer_matches_reads_custom_not_the_preset_name() {
        // The lie being fixed: an Accountant whose books row was edited by hand
        // used to keep showing "Accountant".
        assertThat(employeeJobLabel("accountant", false, "manager")).isEqualTo("Custom")
        assertThat(employeeJobLabel("branch_manager", false, "manager")).isEqualTo("Custom")
        assertThat(employeeJobLabel("cashier", false, "cashier")).isEqualTo("Custom")
    }

    @Test
    fun a_matching_preset_reads_its_own_name() {
        assertThat(employeeJobLabel("accountant", true, "manager")).isEqualTo("Accountant")
        assertThat(employeeJobLabel("branch_manager", true, "manager")).isEqualTo("Branch manager")
        assertThat(employeeJobLabel("manager", true, "manager")).isEqualTo("Manager")
        assertThat(employeeJobLabel("cashier", true, "cashier")).isEqualTo("Cashier")
    }

    @Test
    fun an_older_server_that_never_sent_the_field_keeps_the_preset_name() {
        // null is "field absent", NOT "does not match". Only an explicit false
        // may say Custom -- this is the allow-half of the deny/allow pair above.
        assertThat(employeeJobLabel("accountant", null, "manager")).isEqualTo("Accountant")
    }

    @Test
    fun a_row_with_no_preset_falls_back_to_the_legacy_role_label() {
        assertThat(employeeJobLabel(null, null, "manager")).isEqualTo("Manager")
        assertThat(employeeJobLabel("  ", null, "cashier")).isEqualTo("Cashier")
        assertThat(employeeJobLabel(null, null, "admin")).isEqualTo("Owner")
        // A false flag with NO stored preset has nothing to be "custom" against.
        assertThat(employeeJobLabel(null, false, "manager")).isEqualTo("Manager")
    }

    @Test
    fun the_custom_row_shows_no_preset_sentence_and_the_sheet_and_row_both_use_the_one_function() {
        val f = File(File("."), "src/main/java/com/actionaura/retail/ui/screens/EmployeesScreen.kt")
        assumeTrue("EmployeesScreen.kt not reachable from this run context", f.exists())
        val src = codeOnly(f.readText())
        // Row and manage sheet: exactly the two call sites, both handing over
        // the server's preset_matches (a third caller passing a literal would be
        // a door back to the lie).
        val calls = Regex("""employeeJobLabel\(\s*(\w+)\.role_preset,\s*\1\.preset_matches""")
            .findAll(src).count()
        assertThat(calls).isEqualTo(2)
        assertThat(Regex("""employeeJobLabel\(""").findAll(src).count() - 1).isEqualTo(2) // minus the definition
    }

    // ── the picker (fix 2) ───────────────────────────────────────────────────

    @Test
    fun a_stale_preset_is_not_shown_as_chosen() {
        assertThat(matchedPreset("accountant", false)).isNull()
        assertThat(matchedPreset("accountant", true)).isEqualTo("accountant")
        assertThat(matchedPreset("accountant", null)).isEqualTo("accountant")
        assertThat(matchedPreset(null, null)).isNull()
        assertThat(matchedPreset("  ", true)).isNull()
        assertThat(matchedPreset(" Accountant ", true)).isEqualTo("accountant")
    }

    @Test
    fun tapping_the_chosen_preset_does_nothing_and_any_other_tap_asks_first() {
        assertThat(shouldConfirmPresetChange("cashier", "cashier")).isFalse()
        assertThat(shouldConfirmPresetChange("cashier", "manager")).isTrue()
        // Nothing chosen (legacy row, or a Custom account): every tap is a change,
        // including re-applying the stored preset.
        assertThat(shouldConfirmPresetChange(null, "accountant")).isTrue()
    }

    @Test
    fun the_picker_only_opens_the_confirm_through_shouldConfirmPresetChange() {
        val f = File(File("."), "src/main/java/com/actionaura/retail/ui/screens/EmployeesScreen.kt")
        assumeTrue("EmployeesScreen.kt not reachable from this run context", f.exists())
        val src = codeOnly(f.readText())
        assertThat(src).contains("onPick = { if (shouldConfirmPresetChange(currentPreset, it)) confirmPreset = it }")
        assertThat(src).contains("val currentPreset = matchedPreset(employee.role_preset, employee.preset_matches)")
    }

    // ── create request per preset (fix 5, kills U) ───────────────────────────

    private val userAccountsPy: String get() {
        val f = File(File("../../.."), "commercial_runtime/identity/user_accounts.py")
        assumeTrue("user_accounts.py not reachable from this run context", f.exists())
        return f.readText()
    }

    /** PRESET_BASE_ROLE read out of the server source, names resolved to values. */
    private fun serverBaseRoles(): Map<String, String> {
        val py = userAccountsPy
        fun constants(prefix: String): Map<String, String> =
            Regex("""(?m)^($prefix[A-Z_]+)\s*=\s*'(\w+)'""").findAll(py)
                .associate { it.groupValues[1] to it.groupValues[2] }
        val presets = constants("PRESET_")
        val roles = constants("ROLE_")
        val block = py.substringAfter("PRESET_BASE_ROLE = {").substringBefore("}")
        val out = Regex("""(PRESET_[A-Z_]+):\s*(ROLE_[A-Z_]+)""").findAll(block)
            .associate { presets.getValue(it.groupValues[1]) to roles.getValue(it.groupValues[2]) }
        // The harness must actually have read the table, or every comparison
        // below is between two empty maps.
        assertThat(out).hasSize(4)
        return out
    }

    @Test
    fun the_phone_base_role_table_is_the_servers_table() {
        assertThat(PRESET_BASE_ROLE).isEqualTo(serverBaseRoles())
        assertThat(ASSIGNABLE_PRESETS).containsExactlyElementsIn(serverBaseRoles().keys)
    }

    @Test
    fun the_create_request_sends_each_presets_base_role() {
        // accountant -> manager, branch_manager -> manager, cashier -> cashier.
        // A hardcoded "cashier" here is refused by the server with
        // "Role does not match the chosen preset.".
        val server = serverBaseRoles()
        assertThat(server["accountant"]).isEqualTo("manager")
        assertThat(server["branch_manager"]).isEqualTo("manager")
        assertThat(server["cashier"]).isEqualTo("cashier")
        for ((preset, role) in server) {
            val req = buildCreateEmployeeRequest("A@B.com ", "Sara", preset, "branch-uid-1")
            assertThat(req.role).isEqualTo(role)
            assertThat(req.preset).isEqualTo(preset)
            assertThat(req.email).isEqualTo("a@b.com")
        }
    }

    @Test
    fun the_branch_uid_travels_only_with_the_branch_manager_preset() {
        for (preset in ASSIGNABLE_PRESETS) {
            val req = buildCreateEmployeeRequest("a@b.com", "", preset, "branch-uid-1")
            if (preset == "branch_manager") {
                assertThat(req.branch_scope_uid).isEqualTo("branch-uid-1")
            } else {
                assertThat(req.branch_scope_uid).isNull()
            }
        }
    }

    @Test
    fun a_blank_name_on_create_is_left_out_not_sent_empty() {
        assertThat(buildCreateEmployeeRequest("a@b.com", "   ", "cashier", null).name).isNull()
        assertThat(buildCreateEmployeeRequest("a@b.com", " Sara ", "cashier", null).name).isEqualTo("Sara")
    }

    // ── name judged the way the server judges it (fix 6) ─────────────────────

    @Test
    fun the_limit_is_the_servers_limit() {
        val m = Regex("""(?m)^DISPLAY_NAME_MAX\s*=\s*(\d+)""").find(userAccountsPy)
        assertThat(m).isNotNull()
        assertThat(DISPLAY_NAME_MAX).isEqualTo(m!!.groupValues[1].toInt())
    }

    @Test
    fun sixty_is_fine_and_sixty_one_is_not() {
        assertThat(nameProblem("a".repeat(DISPLAY_NAME_MAX))).isNull()
        assertThat(nameProblem("a".repeat(DISPLAY_NAME_MAX + 1))).isEqualTo("Name must be 60 characters or fewer.")
    }

    @Test
    fun an_invisible_pad_is_stripped_before_counting_like_the_server_does() {
        // U+200B is a format (Cf) character the server strips, so it cannot push
        // a 60-character name over the limit.
        assertThat(nameProblem("a".repeat(60) + "​")).isNull()
        assertThat(nameLength("a".repeat(60) + "​")).isEqualTo(60)
    }

    @Test
    fun the_five_kept_format_characters_do_count() {
        // ZWJ is on the server's keep list: it stays, so it is the 61st.
        assertThat(nameProblem("a".repeat(60) + "‍")).isEqualTo("Name must be 60 characters or fewer.")
    }

    @Test
    fun length_is_counted_in_code_points_not_utf16_units() {
        val grin = "😀" // one code point, two chars
        assertThat(nameProblem(grin.repeat(60))).isNull()
        assertThat(nameProblem(grin.repeat(61))).isEqualTo("Name must be 60 characters or fewer.")
    }

    @Test
    fun combining_marks_are_composed_before_counting() {
        val composed = "é" // NFC -> a single code point
        assertThat(nameProblem(composed.repeat(60))).isNull()
        assertThat(nameLength(composed.repeat(60))).isEqualTo(60)
    }

    @Test
    fun a_line_break_is_refused_not_joined() {
        for (raw in listOf("a\nb", "a\rb", "a b", "a b", "a\u0085b", "a\u000Bb", "a\u000Cb")) {
            assertThat(nameProblem(raw)).isEqualTo("Name must be a single line.")
        }
    }

    @Test
    fun a_blank_name_is_accepted_and_cleans_to_empty_which_is_what_clears_it() {
        for (raw in listOf("", "   ", "​", " ​ ")) {
            assertThat(nameProblem(raw)).isNull()
            assertThat(cleanName(raw)).isEmpty()
        }
    }
}
