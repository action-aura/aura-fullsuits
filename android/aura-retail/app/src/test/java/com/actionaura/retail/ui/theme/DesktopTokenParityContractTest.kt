package com.actionaura.retail.ui.theme

import androidx.compose.ui.graphics.Color
import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import kotlin.math.roundToInt

/**
 * The phone is supposed to be the desktop's own dark palette, not a second
 * palette that merely resembles it -- see Color.kt's header for the owner's
 * requirement this file exists to hold: "the desktop retail and the phone
 * should look close like basically they supposed to be known for eachother."
 *
 * That claim is only ever as true as the last person who copied a hex value
 * by hand. It has already drifted once silently: BorderHairline shipped as
 * #222B37 against the desktop's #232B37 -- a one-digit typo introduced the
 * same day this file's constants were written, invisible on screen, and
 * invisible to every other guard in this module because nothing else reads
 * main.css.
 *
 * So this test does not carry its own copy of the desktop's hex values --
 * that would just be a SECOND place for the same typo to happen and a
 * different-but-equally-wrong number to agree with. It reads
 * products/retail/frontend/css/main.css itself, at test time, and compares
 * PARSED values against the real `val` constants in Color.kt (same package,
 * referenced directly -- not re-typed). Either side moving without the other
 * fails this test and names exactly which token and both hex values.
 *
 * Android has one fixed dark theme (Theme.kt: deliberately not DayNight, not
 * dynamicColor -- a till must render identically on every device). The
 * desktop's `html[data-theme="dark"]` block is therefore the only correct
 * comparison target; the light `:root` block is a different theme and is not
 * compared here.
 */
class DesktopTokenParityContractTest {

    // Same shape as OverflowNavigationContractTest's moduleRoot/source(): a
    // root File() the scan in CompiledTestSuiteRollCallTest can resolve, and a
    // helper that takes the path as a String and hands it straight to
    // File(root, relative) -- recognised by SHAPE, not by name, so this stays
    // visible to that module's "every guard that can skip itself opens a path
    // this scan can see" completeness check.
    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    // From this module's root (android/aura-retail/app) up to the repo root,
    // then down into the desktop token file. Verified against the actual
    // working directory Gradle uses for :app:testDebugUnitTest (see
    // ReadinessContractTest / CompiledTestSuiteRollCallTest for the same
    // assumption stated elsewhere in this module).
    private val mainCss: String get() = source("../../../products/retail/frontend/css/main.css")

    private val darkTokenPattern = Regex("""--([a-z0-9-]+):\s*#([0-9a-fA-F]{6})\s*;""")

    /**
     * The `html[data-theme="dark"] { ... }` block's token declarations,
     * lower-cased name to upper-case 6-digit hex (no `#`). Non-colour
     * declarations in that block (rgba() shadows, the sub-accent-rgb triplet,
     * cubic-bezier easing) simply do not match [darkTokenPattern] and are
     * skipped -- this only ever extracts `--name: #rrggbb;` pairs.
     */
    private fun desktopDarkTokens(): Map<String, String> {
        val css = mainCss

        val marker = "html[data-theme=\"dark\"] {"
        val markerIndex = css.indexOf(marker)
        assumeTrue(
            "html[data-theme=\"dark\"] block not found in main.css -- " +
                "the desktop dark palette may have been renamed or removed",
            markerIndex >= 0,
        )

        val braceStart = markerIndex + marker.length - 1
        val braceEnd = css.indexOf('}', braceStart)
        assumeTrue(
            "html[data-theme=\"dark\"] block has no closing brace in main.css",
            braceEnd > braceStart,
        )
        val block = css.substring(braceStart + 1, braceEnd)

        val tokens = darkTokenPattern.findAll(block)
            .associate { it.groupValues[1] to it.groupValues[2].uppercase() }
        // A parser that silently found nothing would make every comparison
        // below vacuously pass. Twenty-plus colour declarations exist in this
        // block today (verified by reading main.css directly); if the CSS
        // authoring style ever changes shape, this is the assertion that
        // catches the parser going blind rather than the token comparisons
        // quietly stopping to mean anything.
        assertThat(tokens.size).isAtLeast(15)
        return tokens
    }

    /** [color]'s sRGB channels as the 6 upper-case hex digits main.css spells. */
    private fun Color.toHex6(): String {
        fun channel(v: Float) = (v * 255f).roundToInt().coerceIn(0, 255)
        return "%02X%02X%02X".format(channel(red), channel(green), channel(blue))
    }

    /**
     * One CSS custom-property name paired with the Android `Color` constant
     * that is supposed to hold the same value.
     */
    private data class TokenPair(val cssName: String, val androidName: String, val androidColor: Color)

    private fun mismatches(pairs: List<TokenPair>): List<String> {
        val desktop = desktopDarkTokens()
        return pairs.mapNotNull { pair ->
            val desktopHex = desktop[pair.cssName]
            val androidHex = pair.androidColor.toHex6()
            when {
                desktopHex == null ->
                    "${pair.androidName} (--${pair.cssName}): " +
                        "not found in main.css's dark block -- android=#$androidHex"
                androidHex != desktopHex ->
                    "${pair.androidName} (--${pair.cssName}): " +
                        "desktop=#$desktopHex android=#$androidHex"
                else -> null
            }
        }
    }

    // ── SURFACES ─────────────────────────────────────────────────────────────

    @Test
    fun surface_tokens_match_the_desktop_dark_palette() {
        val pairs = listOf(
            TokenPair("surface-app", "SurfaceApp", SurfaceApp),
            TokenPair("surface-sunken", "SurfaceSunken", SurfaceSunken),
            TokenPair("surface-panel", "SurfacePanel", SurfacePanel),
            TokenPair("surface-raised", "SurfaceRaised", SurfaceRaised),
            TokenPair("surface-till", "SurfaceTill", SurfaceTill),
            TokenPair("surface-hover", "SurfaceHover", SurfaceHover),
            TokenPair("surface-active", "SurfaceActive", SurfaceActive),
        )
        assertThat(mismatches(pairs)).isEmpty()
    }

    // ── TEXT ─────────────────────────────────────────────────────────────────

    @Test
    fun text_tokens_match_the_desktop_dark_palette() {
        val pairs = listOf(
            TokenPair("text-primary", "TextPrimary", TextPrimary),
            TokenPair("text-secondary", "TextSecondary", TextSecondary),
            TokenPair("text-tertiary", "TextTertiary", TextTertiary),
        )
        assertThat(mismatches(pairs)).isEmpty()
    }

    // ── ACCENT ───────────────────────────────────────────────────────────────

    @Test
    fun accent_tokens_match_the_desktop_dark_palette() {
        val pairs = listOf(
            TokenPair("accent-action", "AccentAction", AccentAction),
            TokenPair("text-on-accent", "OnAccent", OnAccent),
            TokenPair("surface-accent-soft", "AccentSoft", AccentSoft),
        )
        assertThat(mismatches(pairs)).isEmpty()
    }

    // ── BORDERS ──────────────────────────────────────────────────────────────

    @Test
    fun border_tokens_match_the_desktop_dark_palette() {
        val pairs = listOf(
            TokenPair("border-default", "BorderDefault", BorderDefault),
            TokenPair("border-hairline", "BorderHairline", BorderHairline),
        )
        assertThat(mismatches(pairs)).isEmpty()
    }

    // ── SEMANTIC STATE ───────────────────────────────────────────────────────

    @Test
    fun semantic_state_tokens_match_the_desktop_dark_palette() {
        val pairs = listOf(
            TokenPair("state-success-text", "Success", Success),
            TokenPair("state-warning-text", "Warning", Warning),
            TokenPair("state-danger-text", "Danger", Danger),
            TokenPair("state-info-text", "Info", Info),
            TokenPair("state-success-surface", "SuccessContainer", SuccessContainer),
            TokenPair("state-danger-surface", "DangerContainer", DangerContainer),
        )
        assertThat(mismatches(pairs)).isEmpty()
    }
}
