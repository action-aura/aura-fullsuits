package com.actionaura.retail.ui.theme

import androidx.compose.ui.graphics.Color
import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import kotlin.math.roundToInt

/**
 * The phone is supposed to be the desktop's own palettes, not a second set
 * that merely resembles them -- see Color.kt's header for the owner's
 * requirement this file exists to hold: "the desktop retail and the phone
 * should look close like basically they supposed to be known for eachother."
 * That requirement now spans all FIVE sanctioned themes (owner request,
 * 2026-09: "night mode back" + "themes for both mobile and desktop"), not
 * only the original dark one.
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
 * PARSED values against the real fields of the [AuraColors] instances in
 * Color.kt (same package, referenced directly -- not re-typed). Either side
 * moving without the other fails this test and names exactly which token and
 * both hex values.
 *
 * Each palette below is read off its [AuraColors] object's FIELDS directly
 * ([AuraPalette.DAY], [AuraPalette.NIGHT], ...), never through the top-level
 * getters (`SurfaceApp`, `TextPrimary`, ...) -- those resolve through
 * [AuraPalette.current], and this test must compare a NAMED palette against
 * its own desktop block regardless of which palette happens to be active.
 *
 * The original dark comparison (Calm == desktop `html[data-theme="dark"]`) is
 * kept exactly as it always ran. Day, Night, Dusk and Sand are the same
 * comparison against their own desktop block; each is its own @Test so one
 * theme's drift never hides another's, and each `assumeTrue`-skips (naming
 * the missing marker) if its desktop block is not there yet -- the desktop
 * side of this change can legitimately land after the phone side does.
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

    private val cssTokenPattern = Regex("""--([a-z0-9-]+):\s*#([0-9a-fA-F]{6})\s*;""")

    /**
     * The `--name: #rrggbb;` declarations inside one CSS block, lower-cased
     * name to upper-case 6-digit hex (no `#`). Non-colour declarations in
     * that block (rgba() shadows, the sub-accent-rgb triplet, cubic-bezier
     * easing) simply do not match [cssTokenPattern] and are skipped -- this
     * only ever extracts `--name: #rrggbb;` pairs.
     *
     * [marker] is the CSS selector line this block opens with -- e.g.
     * `:root {` or `html[data-theme="night"] {` -- and [missingMessage]
     * names it in the skip reason when the block is not found, so a run that
     * skips every theme test still says exactly which markers it looked for.
     */
    private fun desktopTokens(marker: String, missingMessage: String): Map<String, String> {
        val css = mainCss

        val markerIndex = css.indexOf(marker)
        assumeTrue(missingMessage, markerIndex >= 0)

        val braceStart = markerIndex + marker.length - 1
        val braceEnd = css.indexOf('}', braceStart)
        assumeTrue("$marker has no closing brace in main.css", braceEnd > braceStart)
        val block = css.substring(braceStart + 1, braceEnd)

        val tokens = cssTokenPattern.findAll(block)
            .associate { it.groupValues[1] to it.groupValues[2].uppercase() }
        // A parser that silently found nothing would make every comparison
        // below vacuously pass. Fifteen-plus colour declarations exist in
        // every theme block today (verified by reading main.css directly);
        // if the CSS authoring style ever changes shape, this is the
        // assertion that catches the parser going blind rather than the
        // token comparisons quietly stopping to mean anything.
        assertThat(tokens.size).isAtLeast(15)
        return tokens
    }

    private fun desktopDarkTokens(): Map<String, String> = desktopTokens(
        "html[data-theme=\"dark\"] {",
        "html[data-theme=\"dark\"] block not found in main.css -- " +
            "the desktop dark palette may have been renamed or removed",
    )

    private fun desktopDayTokens(): Map<String, String> = desktopTokens(
        ":root {",
        "[design-tokens:begin] :root block not found in main.css -- " +
            "the desktop light/Day palette may have been renamed or removed",
    )

    private fun desktopNightTokens(): Map<String, String> = desktopTokens(
        "html[data-theme=\"night\"] {",
        "[design-tokens-night:begin] html[data-theme=\"night\"] block not found in main.css yet -- " +
            "the desktop Night theme may not have landed yet",
    )

    private fun desktopDuskTokens(): Map<String, String> = desktopTokens(
        "html[data-theme=\"dusk\"] {",
        "[design-tokens-dusk:begin] html[data-theme=\"dusk\"] block not found in main.css yet -- " +
            "the desktop Dusk theme may not have landed yet",
    )

    private fun desktopSandTokens(): Map<String, String> = desktopTokens(
        "html[data-theme=\"sand\"] {",
        "[design-tokens-sand:begin] html[data-theme=\"sand\"] block not found in main.css yet -- " +
            "the desktop Sand theme may not have landed yet",
    )

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

    /** The full token-pair set for one [AuraColors] instance, named the same
     *  way as the desktop's CSS custom properties. */
    private fun pairsFor(p: AuraColors): List<TokenPair> = listOf(
        TokenPair("surface-app", "surfaceApp", p.surfaceApp),
        TokenPair("surface-sunken", "surfaceSunken", p.surfaceSunken),
        TokenPair("surface-panel", "surfacePanel", p.surfacePanel),
        TokenPair("surface-raised", "surfaceRaised", p.surfaceRaised),
        TokenPair("surface-till", "surfaceTill", p.surfaceTill),
        TokenPair("surface-hover", "surfaceHover", p.surfaceHover),
        TokenPair("surface-active", "surfaceActive", p.surfaceActive),
        TokenPair("text-primary", "textPrimary", p.textPrimary),
        TokenPair("text-secondary", "textSecondary", p.textSecondary),
        TokenPair("text-tertiary", "textTertiary", p.textTertiary),
        TokenPair("accent-action", "accentAction", p.accentAction),
        TokenPair("text-on-accent", "onAccent", p.onAccent),
        TokenPair("surface-accent-soft", "accentSoft", p.accentSoft),
        TokenPair("border-default", "borderDefault", p.borderDefault),
        TokenPair("border-hairline", "borderHairline", p.borderHairline),
        TokenPair("state-success-text", "success", p.success),
        TokenPair("state-warning-text", "warning", p.warning),
        TokenPair("state-danger-text", "danger", p.danger),
        TokenPair("state-info-text", "info", p.info),
        TokenPair("state-success-surface", "successContainer", p.successContainer),
        TokenPair("state-danger-surface", "dangerContainer", p.dangerContainer),
    )

    private fun mismatches(desktop: Map<String, String>, pairs: List<TokenPair>): List<String> {
        return pairs.mapNotNull { pair ->
            val desktopHex = desktop[pair.cssName]
            val androidHex = pair.androidColor.toHex6()
            when {
                desktopHex == null ->
                    "${pair.androidName} (--${pair.cssName}): " +
                        "not found in main.css's block -- android=#$androidHex"
                androidHex != desktopHex ->
                    "${pair.androidName} (--${pair.cssName}): " +
                        "desktop=#$desktopHex android=#$androidHex"
                else -> null
            }
        }
    }

    // ── CALM == desktop dark (the ORIGINAL parity test, unchanged) ────────────
    // Split into four grouped assertions exactly as before so a failure names
    // a small, readable subset rather than the whole 21-token set at once.

    @Test
    fun surface_tokens_match_the_desktop_dark_palette() {
        val desktop = desktopDarkTokens()
        val p = AuraPalette.CALM
        val pairs = listOf(
            TokenPair("surface-app", "SurfaceApp", p.surfaceApp),
            TokenPair("surface-sunken", "SurfaceSunken", p.surfaceSunken),
            TokenPair("surface-panel", "SurfacePanel", p.surfacePanel),
            TokenPair("surface-raised", "SurfaceRaised", p.surfaceRaised),
            TokenPair("surface-till", "SurfaceTill", p.surfaceTill),
            TokenPair("surface-hover", "SurfaceHover", p.surfaceHover),
            TokenPair("surface-active", "SurfaceActive", p.surfaceActive),
        )
        assertThat(mismatches(desktop, pairs)).isEmpty()
    }

    @Test
    fun text_tokens_match_the_desktop_dark_palette() {
        val desktop = desktopDarkTokens()
        val p = AuraPalette.CALM
        val pairs = listOf(
            TokenPair("text-primary", "TextPrimary", p.textPrimary),
            TokenPair("text-secondary", "TextSecondary", p.textSecondary),
            TokenPair("text-tertiary", "TextTertiary", p.textTertiary),
        )
        assertThat(mismatches(desktop, pairs)).isEmpty()
    }

    @Test
    fun accent_tokens_match_the_desktop_dark_palette() {
        val desktop = desktopDarkTokens()
        val p = AuraPalette.CALM
        val pairs = listOf(
            TokenPair("accent-action", "AccentAction", p.accentAction),
            TokenPair("text-on-accent", "OnAccent", p.onAccent),
            TokenPair("surface-accent-soft", "AccentSoft", p.accentSoft),
        )
        assertThat(mismatches(desktop, pairs)).isEmpty()
    }

    @Test
    fun border_tokens_match_the_desktop_dark_palette() {
        val desktop = desktopDarkTokens()
        val p = AuraPalette.CALM
        val pairs = listOf(
            TokenPair("border-default", "BorderDefault", p.borderDefault),
            TokenPair("border-hairline", "BorderHairline", p.borderHairline),
        )
        assertThat(mismatches(desktop, pairs)).isEmpty()
    }

    @Test
    fun semantic_state_tokens_match_the_desktop_dark_palette() {
        val desktop = desktopDarkTokens()
        val p = AuraPalette.CALM
        val pairs = listOf(
            TokenPair("state-success-text", "Success", p.success),
            TokenPair("state-warning-text", "Warning", p.warning),
            TokenPair("state-danger-text", "Danger", p.danger),
            TokenPair("state-info-text", "Info", p.info),
            TokenPair("state-success-surface", "SuccessContainer", p.successContainer),
            TokenPair("state-danger-surface", "DangerContainer", p.dangerContainer),
        )
        assertThat(mismatches(desktop, pairs)).isEmpty()
    }

    // ── The other four palettes, one @Test each ───────────────────────────────

    @Test
    fun day_palette_matches_the_desktop_root_light_block() {
        val desktop = desktopDayTokens()
        assertThat(mismatches(desktop, pairsFor(AuraPalette.DAY))).isEmpty()
    }

    @Test
    fun night_palette_matches_the_desktop_night_block() {
        val desktop = desktopNightTokens()
        assertThat(mismatches(desktop, pairsFor(AuraPalette.NIGHT))).isEmpty()
    }

    @Test
    fun dusk_palette_matches_the_desktop_dusk_block() {
        val desktop = desktopDuskTokens()
        assertThat(mismatches(desktop, pairsFor(AuraPalette.DUSK))).isEmpty()
    }

    @Test
    fun sand_palette_matches_the_desktop_sand_block() {
        val desktop = desktopSandTokens()
        assertThat(mismatches(desktop, pairsFor(AuraPalette.SAND))).isEmpty()
    }
}
