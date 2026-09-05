package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Every colour in the app comes from ui/theme/Color.kt. No `Color(0x......)`
 * literal anywhere else in `src/main` -- no allowlist, no exemptions.
 *
 * WHY THIS EXISTS
 *
 * RetailScreens.kt, LicensingScreen.kt and Components.kt used to paint
 * colours as raw hex literals, bypassing the token layer. This was not a
 * tidiness problem: the app is DARK-ONLY (Theme.kt is deliberately not
 * DayNight), and measured against the app's own dark surfaces every one of
 * LicensingScreen's `stateLabel` literals failed WCAG AA's 4.5:1 floor --
 *
 *     #666666 (grey)   3.24:1
 *     #1A7A3D (green)  3.45:1
 *     #8A6100 (amber)  3.36:1
 *     #A3231F (red)    2.50:1
 *
 * -- and `MessageBanner` was a light-theme banner hard-coded into a dark-only
 * app, on the screen every customer sees when activating. Those were routed
 * through the semantic tokens.
 *
 * The last sixteen literals outside Color.kt were the two decorative
 * identity palettes (avatar initials, category chips). Until 2026-09-06 this
 * test ALLOWLISTED them by file, which left the app with two places to paint
 * from and a guard that had to be told what to ignore. They now live in
 * Color.kt as `AvatarPalette` and `CategoryPalette`, so the contract can be
 * the simple one: outside Color.kt, zero literals. The palettes themselves
 * are pinned below by exact value AND order, because an avatar's colour is
 * `palette[hash % size]` -- reordering would silently recolour every
 * customer's initials and every category chip, and each value was
 * contrast-measured one by one (see Color.kt's comments for the numbers).
 *
 * No Compose test runner and no device in this environment, so this is a
 * source-reading contract test, same shape and justification as
 * OverflowNavigationContractTest.
 */
class ColorTokenContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val mainRoot = "src/main/java"
    private val tokenFile = "src/main/java/com/actionaura/retail/ui/theme/Color.kt"

    private val colorLiteral = Regex("""Color\(0x[0-9A-Fa-f]{6,8}\)""")

    private fun literalsIn(src: String): List<String> = colorLiteral.findAll(codeOnly(src)).map { it.value }.toList()

    /** Every Kotlin source under src/main, path relative to the module root. */
    private fun mainSources(): List<String> {
        val root = File(moduleRoot, mainRoot)
        assumeTrue("$mainRoot not reachable from this run context", root.isDirectory)
        return root.walkTopDown()
            .filter { it.isFile && it.extension == "kt" }
            .map { it.relativeTo(moduleRoot).path.replace('\\', '/') }
            .sorted()
            .toList()
    }

    private val expectedAvatarPalette = listOf(
        "Color(0xFF14B8A6)", // teal, unchanged -- 4.78:1 worst-case
        "Color(0xFF9597F5)", // indigo, lightened -- 4.53:1 worst-case (was 3.11:1)
        "Color(0xFFF073B1)", // pink, lightened -- 4.50:1 worst-case (was 3.90:1)
        "Color(0xFFF59E0B)", // amber, unchanged -- 5.38:1 worst-case
        "Color(0xFF10B981)", // emerald, unchanged -- 4.74:1 worst-case
        "Color(0xFF38BDF8)", // sky, unchanged -- 5.31:1 worst-case
        "Color(0xFFC085F9)", // purple, lightened -- 4.52:1 worst-case (was 3.33:1)
        "Color(0xFFF37777)", // red, lightened -- 4.52:1 worst-case (was 3.60:1)
    )
    private val expectedCategoryPalette = listOf(
        "Color(0xFF9597F5)", // indigo, lightened -- 4.53:1 worst-case (was 3.11:1)
        "Color(0xFF14B8A6)", // teal, unchanged -- 4.78:1 worst-case
        "Color(0xFFF59E0B)", // amber, unchanged -- 5.38:1 worst-case
        "Color(0xFFF073B1)", // pink, lightened -- 4.50:1 worst-case (was 3.73:1)
        "Color(0xFF10B981)", // emerald, unchanged -- 4.74:1 worst-case
        "Color(0xFF38BDF8)", // sky, unchanged -- 5.31:1 worst-case
        "Color(0xFFC085F9)", // purple, lightened -- 4.52:1 worst-case (was 3.33:1)
        "Color(0xFFF37777)", // red, lightened -- 4.52:1 worst-case (was 3.60:1)
    )

    /** The literals inside `val <name>: List<Color> = listOf( ... )`, in source order. */
    private fun paletteLiterals(colorKt: String, name: String): List<String> {
        val start = colorKt.indexOf("val $name")
        assertThat(start).isAtLeast(0)
        val open = colorKt.indexOf("listOf(", start)
        val close = colorKt.indexOf("\n)", open)
        assertThat(open).isAtLeast(0)
        assertThat(close).isGreaterThan(open)
        return literalsIn(colorKt.substring(open, close))
    }

    // ── The guard ────────────────────────────────────────────────────────────

    @Test
    fun no_colour_literal_outside_the_token_file() {
        val violations = mainSources()
            .filterNot { it == tokenFile }
            .flatMap { path -> literalsIn(source(path)).map { "$path: $it" } }

        // The whole finding in one assertion: a colour reintroduced as a raw
        // hex literal anywhere in the app -- a `stateLabel` branch, a new
        // banner, a border tint, a ninth avatar hue typed in place -- is
        // named here, file and literal both. There is no allowlist to widen;
        // the fix is always to name the colour in Color.kt.
        assertThat(violations).isEmpty()
    }

    @Test
    fun the_avatar_palette_keeps_its_measured_values_and_order() {
        assertThat(paletteLiterals(source(tokenFile), "AvatarPalette"))
            .containsExactlyElementsIn(expectedAvatarPalette).inOrder()
    }

    @Test
    fun the_category_palette_keeps_its_measured_values_and_order() {
        assertThat(paletteLiterals(source(tokenFile), "CategoryPalette"))
            .containsExactlyElementsIn(expectedCategoryPalette).inOrder()
    }

    // ── Guards the guard ─────────────────────────────────────────────────────

    @Test
    fun the_scan_is_not_silently_finding_nothing() {
        // A regex that stopped matching, a walk that found no files, or a
        // codeOnly() that started eating code would leave the assertion above
        // inspecting an empty list and reporting green over a scan that saw
        // nothing. Color.kt alone carries the 21 semantic tokens plus the two
        // eight-hue palettes; and the walk must actually reach the screens.
        assertThat(literalsIn(source(tokenFile)).size).isAtLeast(37)
        val files = mainSources()
        assertThat(files.size).isAtLeast(10)
        assertThat(files).contains("src/main/java/com/actionaura/retail/ui/screens/RetailScreens.kt")
        assertThat(files).contains("src/main/java/com/actionaura/retail/ui/components/Components.kt")
    }
}
