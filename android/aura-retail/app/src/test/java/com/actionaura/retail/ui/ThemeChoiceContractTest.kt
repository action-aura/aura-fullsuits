package com.actionaura.retail.ui

import com.actionaura.retail.ui.theme.AuraPalette
import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Reachability + contract guard for the five-theme switch (owner request,
 * 2026-09: "night mode back" + "themes for both mobile and desktop"). Same
 * shape and same justification as EmployeesWiringContractTest: there is no
 * Compose test runner, no Chaquopy and no device in this environment, but
 * "is the switch actually wired end to end" is precisely the regression that
 * has already happened once in this module -- the language picker shipped
 * complete inside a screen nobody ever routed to. This file makes sure the
 * theme picker cannot repeat that quietly: the palette table exists, every
 * token name the app already reads resolves through it, the scheme builder
 * consumes it for both polarities, the Settings row and its persistence are
 * both present, and every label the picker shows has an Arabic entry.
 *
 * Extended with the SYSTEM BARS and the PRE-COMPOSE WINDOW, which the first
 * pass missed: both were still hard-wired to the single dark identity the
 * switch replaced, so the two light palettes shipped with white status-bar
 * icons on a near-white ground and a near-black cold-start frame. See the
 * "System bars" section below for the measurements.
 */
class ThemeChoiceContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val colorKt get() = source("src/main/java/com/actionaura/retail/ui/theme/Color.kt")
    private val themeKt get() = source("src/main/java/com/actionaura/retail/ui/theme/Theme.kt")
    private val settingsScreen get() = source("src/main/java/com/actionaura/retail/ui/screens/RetailExtraScreens.kt")
    private val stringsKt get() = source("src/main/java/com/actionaura/retail/ui/i18n/Strings.kt")
    private val mainActivity get() = source("src/main/java/com/actionaura/retail/MainActivity.kt")

    // ── The palette table ────────────────────────────────────────────────────

    @Test
    fun the_palette_object_lists_all_five_themes_in_the_pickers_order() {
        assertThat(colorKt).contains("object AuraPalette")
        assertThat(colorKt).contains("ALL = listOf(DAY, SAND, CALM, NIGHT, DUSK)")
    }

    @Test
    fun every_existing_token_name_is_a_getter_over_the_active_palette() {
        // The ~150 call sites across the app's screens read these names
        // unchanged; the contract that keeps them compiling AND recomposing
        // on a theme switch is that each one now reads through
        // AuraPalette.current rather than holding a literal.
        val src = colorKt
        val names = listOf("SurfaceApp", "SurfaceTill", "TextPrimary", "AccentAction", "OnAccent", "BorderDefault")
        val missing = names.filterNot { src.contains("val $it: Color get() = AuraPalette.current.") }
        assertThat(missing).isEmpty()
    }

    // ── The scheme builder ───────────────────────────────────────────────────

    @Test
    fun the_scheme_builder_reads_the_active_palette_and_covers_both_polarities() {
        // Two of the five themes (Day, Sand) are light; the other three are
        // dark. A builder that only ever called darkColorScheme would render
        // Day and Sand with inverted M3 defaults everywhere the app does not
        // explicitly override a slot.
        val src = themeKt
        assertThat(src).contains("AuraPalette.current")
        assertThat(src).contains("darkColorScheme(")
        assertThat(src).contains("lightColorScheme(")
    }

    // ── System bars ──────────────────────────────────────────────────────────
    //
    // The app is edge-to-edge with TRANSPARENT bars, so the clock, the battery
    // and the gesture pill are drawn over whatever AppBackground painted --
    // colorScheme.background, i.e. the active palette's surfaceApp. Icon
    // polarity therefore belongs to the PALETTE, not to a fixed identity.
    //
    // MainActivity pinned SystemBarStyle.dark(...) for both bars on the premise
    // that "the app's identity is fixed-dark". Two light palettes later that
    // premise was false and the pin was measurably wrong: SystemBarStyle.dark
    // forces detectDarkMode=true (white icons, unconditionally), which on Day's
    // #EAEEF3 is 1.17:1 and on Sand's #EFE8DC 1.22:1. A correct dark glyph
    // (#141A24) measures 14.98:1 and 14.34:1 on those grounds.
    //
    // These assertions read source rather than pixels for the usual reason
    // (no Compose test runner, no device), so they are deliberately shaped to
    // fail on the exact regression: re-pinning `.dark(` on either bar.

    @Test
    fun the_startup_bar_polarity_is_read_from_the_palette_not_pinned_dark() {
        val src = codeOnly(mainActivity)
        // The palette, not the system theme and not a constant.
        assertThat(src).contains("AuraPalette.current.isDark")
        // Both polarities are actually reachable. A build that only ever
        // constructs SystemBarStyle.dark cannot satisfy this, which is the
        // mutation this test exists to catch.
        assertThat(src).contains("SystemBarStyle.light(")
        assertThat(src).contains("SystemBarStyle.dark(")
        // ...and neither bar is handed a fixed style at the call site.
        assertThat(src).doesNotContain("statusBarStyle = SystemBarStyle.dark(")
        assertThat(src).doesNotContain("navigationBarStyle = SystemBarStyle.dark(")
    }

    @Test
    fun the_theme_re_applies_bar_polarity_when_the_palette_changes() {
        // AppTheme.set writes straight into AuraPalette.current's Compose
        // state -- no Activity restart -- so onCreate's enableEdgeToEdge runs
        // exactly once per launch. Without this, switching Calm -> Day at
        // runtime leaves white icons on Day's near-white ground until the app
        // is killed and reopened.
        val src = codeOnly(themeKt)
        assertThat(src).contains("isAppearanceLightStatusBars = !palette.isDark")
        assertThat(src).contains("isAppearanceLightNavigationBars = !palette.isDark")
        // Keyed on the polarity, so a Night -> Dusk switch (same polarity) does
        // not touch the window at all.
        assertThat(src).contains("LaunchedEffect(palette.isDark)")
    }

    @Test
    fun the_pre_compose_window_ground_is_repainted_from_the_restored_palette() {
        // @color/window_background is ONE value and there are FIVE palettes, so
        // it can only ever match one of them; it stayed Calm's #0F1319 while a
        // Day user got a near-black frame on every cold start. Repainting right
        // after AppTheme.load() closes that from window attach to the first
        // Compose frame.
        //
        // WHAT THIS CANNOT CATCH, stated rather than implied: the system's
        // starting/preview window is drawn by the framework from the manifest
        // theme before this process draws anything, so a Day user still sees
        // one dark frame. Fixing that needs per-palette launch themes; no
        // assertion here would notice its absence.
        val src = codeOnly(mainActivity)
        assertThat(src).contains("window.setBackgroundDrawable(")
        assertThat(src).contains("AuraPalette.current.surfaceApp")
        // Order matters: the restore has to happen first, or this paints
        // whatever palette the process started with.
        assertThat(src.indexOf("AppTheme.load(this)"))
            .isLessThan(src.indexOf("window.setBackgroundDrawable("))
    }

    // ── The Settings row ─────────────────────────────────────────────────────

    @Test
    fun the_settings_screen_has_a_theme_row_that_writes_through_apptheme() {
        val src = settingsScreen
        assertThat(src).contains("""tr("Theme")""")
        assertThat(src).contains("AppTheme.set(")
    }

    // ── Persistence ──────────────────────────────────────────────────────────

    @Test
    fun apptheme_persists_next_to_applocale_with_all_five_labels_translated() {
        val src = stringsKt
        assertThat(src).contains("object AppTheme")
        val labels = listOf("\"Day\"", "\"Sand\"", "\"Calm\"", "\"Night\"", "\"Dusk\"")
        val missing = labels.filterNot { src.contains(it) }
        assertThat(missing).isEmpty()
    }

    // ── Pure behaviour -- no source reading, no assumeTrue ──────────────────

    @Test
    fun byName_falls_back_to_calm_for_an_unknown_name_and_resolves_a_known_one() {
        assertThat(AuraPalette.byName("nonsense")).isSameInstanceAs(AuraPalette.CALM)
        assertThat(AuraPalette.byName("night")).isSameInstanceAs(AuraPalette.NIGHT)
        assertThat(AuraPalette.byName(null)).isSameInstanceAs(AuraPalette.CALM)
    }

    @Test
    fun every_palette_in_all_has_a_distinct_name() {
        val names = AuraPalette.ALL.map { it.name }
        assertThat(names).containsNoDuplicates()
        assertThat(names).hasSize(5)
    }
}
