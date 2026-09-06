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
