package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Every colour in RetailScreens.kt, LicensingScreen.kt and Components.kt must
 * come from ui/theme/Color.kt's token layer, not a raw hex literal.
 *
 * WHY THIS EXISTS
 *
 * All three files painted colours as `Color(0x......)` literals, bypassing
 * the token layer entirely. This was not a tidiness problem: the app is
 * DARK-ONLY (Theme.kt is deliberately not DayNight), and measured against the
 * app's own dark surfaces every one of LicensingScreen's `stateLabel` literals
 * failed WCAG AA's 4.5:1 floor for text --
 *
 *     #666666 (grey)   3.24:1
 *     #1A7A3D (green)  3.45:1
 *     #8A6100 (amber)  3.36:1
 *     #A3231F (red)    2.50:1
 *
 * -- and `MessageBanner` was a light-theme banner (pale pink / pale blue
 * fill, dark text) hard-coded into a dark-only app, on the licensing screen
 * every customer sees when activating the product. Fixed by routing through
 * TextTertiary/Success/Warning/Danger/SuccessContainer/DangerContainer (see
 * LicensingScreen.kt's `stateLabel` and `MessageBanner`, and RetailScreens.kt's
 * `StockBadge`, for the full before/after numbers at each call site).
 *
 * No Compose test runner and no device in this environment, so this is a
 * source-reading contract test, same shape and justification as
 * OverflowNavigationContractTest: "does this screen still read its colours
 * from the token layer" is exactly the regression that stays invisible until
 * a customer sees a low-contrast screen, because the app renders correctly
 * (it compiles, it draws SOMETHING) right up until a human looks at it.
 *
 * TWO KINDS OF LITERAL
 *
 *  - DECORATIVE identity-palette literals: the eight-hue avatar/category tile
 *    sets (RetailScreens.kt's `catPalette`, Components.kt's `avatarPalette`).
 *    These are deliberately a spread of distinct hues so two categories look
 *    different, and are not collapsed onto a semantic token for that reason.
 *    They are allowed, but only the SPECIFIC values now in the palettes --
 *    [ALLOWED_DECORATIVE_LITERALS] below -- each one contrast-measured (see
 *    the comments at catPalette/avatarPalette's own declarations for the
 *    worst-case numbers): four of the original eight hues read as text on
 *    their own 18%-tinted tile came in under 4.5:1 and were lightened
 *    (indigo, pink, purple, red); the other four were already compliant and
 *    are unchanged. A literal that is a colour but NOT one of these eight
 *    exact values is not on the list and fails this test -- so replacing the
 *    palette, or adding a ninth hue, is a decision this test forces someone
 *    to come update deliberately rather than one that slides through mute.
 *  - Everything else is SEMANTIC (text, background, border, status colour)
 *    and must come from Color.kt. Any `Color(0x......)` in the three files
 *    below that is not on the allowlist fails this test, naming the file and
 *    the literal.
 */
class ColorTokenContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val retailScreens get() =
        source("src/main/java/com/actionaura/retail/ui/screens/RetailScreens.kt")
    private val licensingScreen get() =
        source("src/main/java/com/actionaura/retail/ui/screens/LicensingScreen.kt")
    private val components get() =
        source("src/main/java/com/actionaura/retail/ui/components/Components.kt")

    /** Every eight-hue decorative identity palette, keyed by owning file. */
    private val ALLOWED_DECORATIVE_LITERALS: Map<String, Set<String>> = mapOf(
        "RetailScreens.kt" to setOf(
            "Color(0xFF9597F5)", // indigo, lightened -- 4.53:1 worst-case (was 3.11:1)
            "Color(0xFF14B8A6)", // teal, unchanged -- 4.78:1 worst-case
            "Color(0xFFF59E0B)", // amber, unchanged -- 5.38:1 worst-case
            "Color(0xFFF073B1)", // pink, lightened -- 4.50:1 worst-case (was 3.73:1)
            "Color(0xFF10B981)", // emerald, unchanged -- 4.74:1 worst-case
            "Color(0xFF38BDF8)", // sky, unchanged -- 5.31:1 worst-case
            "Color(0xFFC085F9)", // purple, lightened -- 4.52:1 worst-case (was 3.33:1)
            "Color(0xFFF37777)", // red, lightened -- 4.52:1 worst-case (was 3.60:1)
        ),
        "Components.kt" to setOf(
            "Color(0xFF14B8A6)", // teal, unchanged -- 4.78:1 worst-case
            "Color(0xFF9597F5)", // indigo, lightened -- 4.53:1 worst-case (was 3.11:1)
            "Color(0xFFF073B1)", // pink, lightened -- 4.50:1 worst-case (was 3.90:1)
            "Color(0xFFF59E0B)", // amber, unchanged -- 5.38:1 worst-case
            "Color(0xFF10B981)", // emerald, unchanged -- 4.74:1 worst-case
            "Color(0xFF38BDF8)", // sky, unchanged -- 5.31:1 worst-case
            "Color(0xFFC085F9)", // purple, lightened -- 4.52:1 worst-case (was 3.33:1)
            "Color(0xFFF37777)", // red, lightened -- 4.52:1 worst-case (was 3.60:1)
        ),
        // LicensingScreen.kt: none. stateLabel and MessageBanner were the only
        // two call sites with literals in this file and both are now tokens.
        "LicensingScreen.kt" to emptySet(),
    )

    private val colorLiteral = Regex("""Color\(0x[0-9A-Fa-f]{6,8}\)""")

    private fun literalsIn(src: String): List<String> = colorLiteral.findAll(codeOnly(src)).map { it.value }.toList()

    // ── The guard ────────────────────────────────────────────────────────────

    @Test
    fun no_colour_literal_outside_the_decorative_allowlist_bypasses_the_token_layer() {
        val files = mapOf(
            "RetailScreens.kt" to retailScreens,
            "LicensingScreen.kt" to licensingScreen,
            "Components.kt" to components,
        )

        val violations = files.flatMap { (name, src) ->
            val allowed = ALLOWED_DECORATIVE_LITERALS.getValue(name)
            literalsIn(src).filterNot { it in allowed }.map { "$name: $it" }
        }

        // The whole finding in one assertion: a semantic colour reintroduced
        // as a raw hex literal -- in a `stateLabel` branch, a new banner, a
        // border tint, anything that is not one of the eight allowlisted
        // decorative hues -- is named here, file and literal both.
        assertThat(violations).isEmpty()
    }

    // ── Guards the guard ─────────────────────────────────────────────────────

    @Test
    fun the_allowlist_names_only_literals_that_are_actually_still_in_the_source() {
        // A stale allowlist entry (the literal it names was since edited or
        // removed) would silently narrow what the test above can catch --
        // the same shape of blind spot the pathSites() completeness tests in
        // CompiledTestSuiteRollCallTest exist to catch for a different scan.
        val files = mapOf(
            "RetailScreens.kt" to retailScreens,
            "LicensingScreen.kt" to licensingScreen,
            "Components.kt" to components,
        )

        val stale = ALLOWED_DECORATIVE_LITERALS.flatMap { (name, allowed) ->
            val present = literalsIn(files.getValue(name)).toSet()
            allowed.filterNot { it in present }.map { "$name: $it" }
        }

        assertThat(stale).isEmpty()
    }

    @Test
    fun the_scan_is_not_silently_finding_nothing() {
        // A regex that stopped matching (renamed the shared helper, changed
        // the literal's spelling, codeOnly() started eating more than
        // comments) would leave the assertion above inspecting an empty list
        // and reporting green over a scan that saw nothing. Sixteen decorative
        // literals exist today (eight in catPalette, eight in avatarPalette);
        // pinned as "at least" rather than "equal to" so a NEW compliant
        // decorative literal does not itself break this self-check.
        val total = literalsIn(retailScreens).size + literalsIn(licensingScreen).size + literalsIn(components).size
        assertThat(total).isAtLeast(16)
    }
}
