package com.actionaura.retail.ui

import com.actionaura.retail.net.Employee
import com.actionaura.retail.ui.i18n.normalizePin
import com.actionaura.retail.ui.screens.inviteTokenOf
import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Reachability + contract guards for the Phase 1 employee-management screen.
 *
 * Same shape and same justification as LicensingWiringContractTest: there is
 * no Compose test runner, no Chaquopy and no device in this environment, but
 * "is this screen actually in the nav graph" is precisely the regression that
 * stays invisible until a customer looks for a feature that was written and
 * never wired.
 *
 * That is not hypothetical here. SettingsScreen.kt in this very module is a
 * complete, working settings screen -- language picker and all -- that has
 * never been registered on any route, so it has never rendered for a single
 * user; the language switcher had to be re-implemented inside
 * RetailSettingsScreen before anyone could reach it. These tests exist so the
 * employees screen cannot repeat that.
 */
class EmployeesWiringContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    /**
     * Strip `//` and block comments so an assertion about CODE is not
     * satisfied -- or defeated -- by prose. This file's subject deliberately
     * carries long "why" comments that quote the very strings and bugs these
     * tests guard against, so scanning raw source would confuse the
     * explanation with the offence in both directions.
     *
     * Deliberately naive (it does not track string literals), which is safe
     * here: the only `//` inside a literal in this screen is a URL scheme in a
     * comment, and a false strip can only ever make an assertion harder to
     * satisfy, never easier.
     */
    private fun codeOnly(src: String): String = src
        .replace(Regex("""/\*.*?\*/""", RegexOption.DOT_MATCHES_ALL), " ")
        .lines().joinToString("\n") { it.substringBefore("//") }
        // Whitespace collapsed too, otherwise a stripped comment leaves behind
        // its own height in blank lines and a "within N characters" window
        // measures indentation instead of code.
        .replace(Regex("""\s+"""), " ")

    private val appRoot get() = source("src/main/java/com/actionaura/retail/ui/AppRoot.kt")
    private val moreScreen get() = source("src/main/java/com/actionaura/retail/ui/screens/RetailExtraScreens.kt")
    private val employeesScreen get() = source("src/main/java/com/actionaura/retail/ui/screens/EmployeesScreen.kt")

    // ── Reachability ─────────────────────────────────────────────────────────

    @Test
    fun the_employees_route_is_registered_in_the_nav_graph() {
        val graph = appRoot.substringAfter("private fun retailGraph(")
        assertThat(graph).contains("""b.composable("employees")""")
        assertThat(graph).contains("EmployeesScreen(")
    }

    @Test
    fun something_actually_navigates_to_the_employees_route() {
        // A registered route with no caller is still dead code -- exactly the
        // half of the SettingsScreen failure people forget. The entry lives in
        // MoreScreen, which is a bottom-tab destination, so this is a real
        // path a user can walk: More -> Team -> Employees.
        assertThat(moreScreen).contains("""onNavigate("employees")""")
    }

    @Test
    fun the_employees_entry_is_owner_gated_and_the_screen_re_checks() {
        // Hiding the entry is usability; the screen's own check is what makes
        // a stale session or a future deep link honest rather than a wall of
        // 403s. Both halves have to be present.
        val entry = moreScreen.substringAfter("""SectionHeader(tr("Team"))""", "")
        assertThat(entry).isNotEmpty()
        assertThat(moreScreen.substringBefore("""SectionHeader(tr("Team"))"""))
            .contains("RetailSession.isAdmin")
        assertThat(employeesScreen).contains("if (!RetailSession.isAdmin)")
    }

    @Test
    fun the_screen_has_a_title_in_the_top_bar_map() {
        // Without an entry here the top bar falls through to "Action Aura",
        // so the user cannot tell which screen they are on.
        assertThat(appRoot).contains("""employees" -> "Employees"""")
    }

    // ── Error handling ───────────────────────────────────────────────────────

    @Test
    fun failures_go_through_the_shared_mapping_not_a_blanket_message() {
        // net/ApiErrors.kt exists because screens reported licensing 403s as
        // connectivity failures. A new screen must not reintroduce that: an
        // admin-only 403, a licence 403 and a dead network are three different
        // things an owner needs told apart.
        //
        // Asserted against the source with COMMENTS STRIPPED. The screen's own
        // comments quote the old blanket sentence to explain what was wrong,
        // and a test that cannot tell an explanation from a regression would
        // punish documenting the bug -- and would also mis-measure the
        // distance between a catch and its handler the moment somebody writes
        // a paragraph between them.
        val src = codeOnly(employeesScreen)
        assertThat(src).doesNotContain("Couldn't reach the server")

        // Every failure path -- not merely one of them -- has to reach the
        // shared mapping. A single catch that swallowed into its own message
        // would reintroduce exactly the class of bug for one action while the
        // rest of the screen looked correct.
        val catches = Regex("""catch\s*\(\s*\w+\s*:\s*Exception\s*\)""").findAll(src).toList()
        assertThat(catches).isNotEmpty()
        for (c in catches) {
            val body = src.substring(c.range.last, minOf(src.length, c.range.last + 120))
            assertThat(body).contains("apiErrorMessage(")
        }
    }

    @Test
    fun a_failed_load_is_not_rendered_as_an_empty_staff_list() {
        // Collapsing "the call failed" into "you have no employees" is how a
        // screen tells an owner their staff vanished because a session
        // expired. The error branch must be distinct and must show the
        // mapped message.
        assertThat(employeesScreen).contains("loadError")
        val emptyBranch = employeesScreen.substringAfter("employees.isEmpty() -> EmptyState")
        val errorBranch = employeesScreen.substringAfter("loadError != null -> EmptyState")
        assertThat(errorBranch).isNotEmpty()
        assertThat(employeesScreen.indexOf("loadError != null ->"))
            .isLessThan(employeesScreen.indexOf("employees.isEmpty() ->"))
        assertThat(emptyBranch).isNotEmpty()
    }

    // ── The rules the screen has to state out loud ───────────────────────────

    @Test
    fun the_pin_rule_is_shown_wherever_a_pin_is_set() {
        // Design §3: a PIN is attribution, never authorization. The sentence
        // appears at both places a PIN is handed out (the manage sheet and the
        // PIN dialog) -- an owner who reads it only once, on a screen they may
        // never revisit, has not been told.
        val rule = "A PIN says who is acting at the till. It does not grant permission"
        assertThat(Regex(Regex.escape(rule)).findAll(employeesScreen).count()).isAtLeast(2)
    }

    @Test
    fun the_invite_states_single_use_and_the_seven_day_expiry() {
        // Both are properties of the `secure_links` row create_employee wrote
        // (utcnow + 7 days, is_used flipped on redemption). An owner who is
        // not told cannot explain to their employee why the code stopped
        // working.
        assertThat(employeesScreen).contains("It works once, and it expires 7 days from now")
    }

    @Test
    fun a_role_change_warns_that_it_resets_permissions_before_it_happens() {
        // The route deletes the eight capability rows and re-seeds them, so an
        // exception the owner granted earlier is discarded. Saying so after
        // the fact is not consent.
        assertThat(employeesScreen).contains("This resets their permissions to that role's defaults")
        assertThat(employeesScreen).contains("confirmRole")
    }

    @Test
    fun the_owner_row_offers_no_role_change_or_deactivate() {
        // One admin per install and no way to mint a second, so demoting or
        // disabling the owner is an irreversible lockout of the whole shop.
        assertThat(employeesScreen).contains("isOwnerRow(")
        assertThat(employeesScreen).contains("if (!isOwnerRow(e)) sheetFor = e")
    }

    @Test
    fun only_manager_and_cashier_are_offered() {
        // 'admin' would be a side door to a second owner account; the legacy
        // 'employee' spelling is accepted by the API but is not a choice.
        assertThat(employeesScreen).contains("""listOf("manager", "cashier")""")
    }

    // ── Behaviour with a real seam ───────────────────────────────────────────

    @Test
    fun the_invite_token_is_extracted_from_the_loopback_setup_link() {
        // On Android host_url is http://127.0.0.1:<ephemeral port>, so the
        // link as returned is useless to anyone else and does not even survive
        // a relaunch. The token is the part that is actually the invite.
        assertThat(inviteTokenOf("http://127.0.0.1:41235/#setup/a1b2c3d4"))
            .isEqualTo("a1b2c3d4")
        assertThat(inviteTokenOf("http://shop.local/#setup/deadbeef")).isEqualTo("deadbeef")
    }

    @Test
    fun an_unrecognised_setup_link_is_shown_rather_than_swallowed() {
        // A shape change server-side must not silently produce a blank code
        // box: showing something the owner can copy beats showing nothing.
        assertThat(inviteTokenOf("something-unexpected")).isEqualTo("something-unexpected")
        assertThat(inviteTokenOf(null)).isEmpty()
        assertThat(inviteTokenOf("   ")).isEmpty()
    }

    @Test
    fun arabic_indic_digits_are_a_valid_pin_and_are_folded_to_ascii() {
        // The whole reason normalizePin exists. "١٢٣٤" is
        // what an Arabic soft keypad emits for 1234; the server hashes the
        // FOLDED form, so a client that sent the raw glyphs would store a PIN
        // its owner can never reproduce on an ASCII keypad. Written as escapes
        // so this assertion cannot be broken by an editor reordering a
        // bidirectional run.
        assertThat(normalizePin("١٢٣٤")).isEqualTo("1234")
        assertThat(normalizePin("۱۲۳۴")).isEqualTo("1234")
        assertThat(normalizePin("1234")).isEqualTo("1234")
    }

    @Test
    fun a_pin_keeps_its_leading_zeros() {
        // parseNum() cannot be reused for a PIN: it yields a Double, and 0042
        // as a number is 42. This is the assertion that pins that apart.
        assertThat(normalizePin("0042")).isEqualTo("0042")
    }

    @Test
    fun a_malformed_pin_is_refused_rather_than_coerced() {
        assertThat(normalizePin("123")).isNull()
        assertThat(normalizePin("12345")).isNull()
        assertThat(normalizePin("12a4")).isNull()
        assertThat(normalizePin("")).isNull()
        assertThat(normalizePin(null)).isNull()
        // Superscript two is Unicode category No, not Nd -- refused here for
        // the same reason the server refuses it, rather than folding to 2.
        assertThat(normalizePin("12²4")).isNull()
    }

    // ── Contract with the server ─────────────────────────────────────────────

    @Test
    fun the_legacy_employee_role_is_displayed_as_cashier_not_as_employee() {
        // Any install predating registry v3 still stores 'employee'. The row
        // behaves as a cashier everywhere a capability decision is made, so a
        // label saying otherwise is a label that lies.
        val legacy = Employee(id = "x", role = "employee", effective_role = "cashier")
        assertThat(legacy.effective_role).isEqualTo("cashier")
        // ...and the screen reads effective_role first, falling back to role.
        assertThat(employeesScreen).contains("e.effective_role ?: e.role")
    }

    // ── Arabic coverage ──────────────────────────────────────────────────────

    /**
     * Every string literal in `src`, with runs joined: `"a " + "b"` yields one
     * entry `"a b"`. That form is everywhere in this codebase because the
     * sentences are longer than a line, and a checker that treated each
     * fragment as its own key would find nothing.
     */
    private fun literalRuns(src: String): List<String> {
        val runs = mutableListOf<String>()
        val current = StringBuilder()
        var i = 0
        while (i < src.length) {
            if (src[i] != '"') { i++; continue }
            i++
            while (i < src.length && src[i] != '"') {
                if (src[i] == '\\' && i + 1 < src.length) {
                    current.append(when (val n = src[i + 1]) {
                        'n' -> '\n'; 't' -> '\t'; else -> n
                    })
                    i += 2
                } else current.append(src[i++])
            }
            i++
            // A `+` between two literals continues the same logical string.
            var j = i
            while (j < src.length && src[j].isWhitespace()) j++
            if (j < src.length && src[j] == '+') {
                var k = j + 1
                while (k < src.length && src[k].isWhitespace()) k++
                if (k < src.length && src[k] == '"') { i = k; continue }
            }
            runs.add(current.toString())
            current.setLength(0)
        }
        return runs
    }

    /** The literal arguments of every `tr(...)` call. Calls whose argument is
     *  not a pure literal concatenation -- `tr(r.error ?: "...")`, `tr(it)` --
     *  are skipped: those carry a server sentence or a value from elsewhere,
     *  and are covered by the server-refusal test below instead. */
    private fun trKeys(src: String): List<String> {
        val out = mutableListOf<String>()
        var idx = src.indexOf("tr(")
        while (idx >= 0) {
            val prev = if (idx == 0) ' ' else src[idx - 1]
            if (!prev.isLetterOrDigit() && prev != '_' && prev != '.') {
                var depth = 0
                var i = idx + 2
                var inStr = false
                var end = -1
                while (i < src.length) {
                    val c = src[i]
                    if (inStr) {
                        if (c == '\\') i++ else if (c == '"') inStr = false
                    } else when (c) {
                        '"' -> inStr = true
                        '(' -> depth++
                        ')' -> { depth--; if (depth == 0) { end = i; } }
                    }
                    if (end >= 0) break
                    i++
                }
                if (end > idx + 3) {
                    val arg = src.substring(idx + 3, end)
                    val withoutLiterals = arg
                        .replace(Regex("\"(\\\\.|[^\"\\\\])*\""), "")
                        .replace("+", "").trim()
                    if (withoutLiterals.isEmpty()) {
                        literalRuns(arg).singleOrNull()?.let { out.add(it) }
                    }
                }
            }
            idx = src.indexOf("tr(", idx + 3)
        }
        return out
    }

    @Test
    fun every_translated_string_on_the_screen_has_an_arabic_entry() {
        // This product ships Arabic and is RTL. tr() falls back to the English
        // key when a translation is missing, which degrades gracefully and
        // therefore FAILS SILENTLY -- an owner in Cairo sees an English
        // sentence and nothing anywhere reports a problem. This is the only
        // thing that reports it.
        val catalog = literalRuns(codeOnly(
            source("src/main/java/com/actionaura/retail/ui/i18n/Strings.kt"))).toSet()
        val used = trKeys(codeOnly(employeesScreen)).toSet()
        assertThat(used).isNotEmpty()

        val missing = used.filterNot { it in catalog }.sorted()
        assertThat(missing).isEmpty()
    }

    @Test
    fun the_server_refusals_this_screen_can_surface_have_arabic_entries() {
        // apiErrorMessage() runs the server's own sentence through tr(), so
        // these reach the user as-is. They are spelled exactly as
        // onboarding_routes.py and user_accounts.PinPolicyError raise them --
        // a reworded copy here would stop matching and quietly fall back to
        // English on an Arabic till.
        val catalog = literalRuns(codeOnly(
            source("src/main/java/com/actionaura/retail/ui/i18n/Strings.kt"))).toSet()
        val serverRefusals = listOf(
            "Admin only",
            "Email required",
            "Email already registered.",
            "Role must be manager or cashier.",
            "PIN must be exactly 4 digits.",
            "User not found.",
            "The owner account's role cannot be changed.",
        )
        assertThat(catalog).containsAtLeastElementsIn(serverRefusals)
    }

    @Test
    fun the_navigation_label_and_section_title_are_translated() {
        val catalog = literalRuns(codeOnly(
            source("src/main/java/com/actionaura/retail/ui/i18n/Strings.kt"))).toSet()
        assertThat(catalog).containsAtLeast("Employees", "Team", "Accounts, roles & till PINs")
    }

    @Test
    fun the_pin_hash_is_never_a_field_on_the_client_model() {
        // A PBKDF2 digest of a four-digit secret is a ten-thousand-entry
        // dictionary away from being the PIN. The server sends a boolean; if
        // this model ever grows a hash field, the server started sending one.
        val fields = Employee::class.java.declaredFields.map { it.name }
        assertThat(fields).doesNotContain("pin_hash")
        assertThat(fields).contains("has_pin")
    }
}
