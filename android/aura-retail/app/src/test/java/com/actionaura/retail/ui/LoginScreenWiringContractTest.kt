package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Wiring guard for LoginScreen.kt's three real-device findings (retail-
 * hardware-viewports QA pass) -- there is no Compose test runner, no
 * Chaquopy and no device in this environment, so scanning the Kotlin source
 * is the seam available, same convention as ProductsWiringContractTest and
 * this file's other siblings.
 *
 * The BEHAVIOUR of the error-mapping decision itself (401 -> credentials
 * message, IOException -> connectivity message, etc.) is a pure function and
 * is proven directly, with real inputs and outputs, by
 * net/ApiErrorsTest.kt -- this file only pins that the SCREEN actually calls
 * it, and that the other two on-device findings stay fixed.
 */
class LoginScreenWiringContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val loginScreenSrc get() =
        codeOnly(source("src/main/java/com/actionaura/retail/ui/screens/LoginScreen.kt"))

    // ── Finding 1: a wrong password must not read as a network failure ─────

    @Test
    fun the_login_catch_routes_through_loginErrorMessage_not_a_blanket_string() {
        // The exact call the on-device bug's fix depends on. If this reverts
        // to a bare `error = tr("Couldn't reach the server...")`, a 401 on a
        // mistyped password reads as "server unreachable" again -- proven by
        // net/ApiErrorsTest.kt's own 401 case, which is what a revert here
        // would silently stop protecting.
        assertThat(loginScreenSrc).contains("error = loginErrorMessage(e)")
    }

    @Test
    fun the_login_catch_no_longer_hardcodes_the_couldnt_reach_the_server_literal() {
        // Belt-and-braces on the assertion above: even a loginErrorMessage(e)
        // call sitting ALONGSIDE the old hardcoded fallback would still be a
        // regression (dead code proving nothing). The literal must be gone
        // from this file entirely, not just no longer assigned to `error`.
        assertThat(loginScreenSrc).doesNotContain("Couldn't reach the server. Try again.")
    }

    // ── Finding 2: an empty password field must not show fake saved-password dots ──

    @Test
    fun the_password_field_has_no_hardcoded_bullet_placeholder() {
        // A fixed "••••••••" placeholder made an
        // EMPTY field look like it already held a saved password, and the
        // "show password" eye then toggled its icon over nothing to reveal.
        // Spelled with the escape (not the literal glyph) so this assertion
        // survives being read/edited through a tool that normalizes Unicode.
        val bullets = "•".repeat(8)
        assertThat(loginScreenSrc).doesNotContain(bullets)
    }

    // ── Finding 3: the Sign In button must not lose its keyboard clearance
    //    when the error banner appears ─────────────────────────────────────

    @Test
    fun an_appearing_error_re_scrolls_the_column_so_the_button_stays_clear_of_the_keyboard() {
        // Compose's own focus-follow only brings the FOCUSED field into view
        // above the IME -- never the button below it -- so the banner's
        // extra height (inserted between the field and the button) has to be
        // compensated for explicitly rather than left to auto-follow.
        assertThat(loginScreenSrc).contains("LaunchedEffect(error)")
        assertThat(loginScreenSrc).contains("scrollState.animateScrollTo(scrollState.maxValue)")

        // Ordering matters, not just presence: the effect has to watch the
        // SAME scrollState the Column actually scrolls with, or this proves
        // nothing about the real viewport.
        val scrollStateDeclaredAt = loginScreenSrc.indexOf("val scrollState = rememberScrollState()")
        val columnUsesItAt = loginScreenSrc.indexOf(".verticalScroll(scrollState)")
        assertThat(scrollStateDeclaredAt).isGreaterThan(-1)
        assertThat(columnUsesItAt).isGreaterThan(-1)
        assertThat(scrollStateDeclaredAt).isLessThan(columnUsesItAt)
    }
}
