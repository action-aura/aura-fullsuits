package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * THE LAUNCHER ICON MUST ACTUALLY BE THE FILE IT SAYS IT COPIES.
 *
 * `res/drawable/ic_launcher_foreground.xml` opens with a header claiming its
 * geometry and colour are "inlined verbatim from
 * products/retail/frontend/brand/aura-mark-on-dark.svg" -- an adaptive icon
 * cannot host a Composable, so the launcher keeps its own hand-maintained
 * copy of the mark. A hand-maintained copy of a file that is edited
 * elsewhere drifts, silently, and nothing was watching.
 *
 * It had already drifted when this test was written (2026-09-20). The
 * launcher carried a uniformly BRIGHTENED derivation of the v1 master:
 *
 *     element        launcher              master v2
 *     back arc       #223B5E -> #DB9A57    #22304A -> #C97B3D
 *     left facet     #F6CFA0 -> #DB9A57    #F0B87A -> #C97B3D
 *     right facet    #B87A45 -> #1E3A5A    #A06030 -> #22304A
 *     front arc      #DB9A57 -> #F6CFA0    #C97B3D -> #F0B87A
 *     ridge          #FDF0E0               #FBE3C4
 *     counter glyph  #DB9A57               #F0B87A
 *     node halo      #F6CFA0               #F0B87A
 *     node core      #FDF6EC               #FBE3C4
 *
 * Two of those matter beyond tidiness. `#223B5E` and `#1E3A5A` are two
 * DIFFERENT improvised "lifted navies", neither of which is the value
 * Action-Aura-Brand-Guide.md v2 settled on (`#22304A`) and which
 * `AuraBrand.ShadowFacetOnDark` now uses for the in-app mark -- so the app's
 * own icon and the mark it draws on its own login screen disagreed about the
 * brand's shadow facet. And the counter glyph moved from the MID accent to
 * the LIGHT one in v2, which the launcher never picked up.
 *
 * WHY A COLOUR MULTISET AND NOT A DIFF
 * The two files are different formats -- SVG `<linearGradient><stop
 * stop-color>` versus Android `<gradient><item android:color>` with an
 * #AARRGGBB alpha byte -- so they can never be compared textually. What CAN
 * be compared is the palette each one actually paints with, including how
 * many times each colour is used. That catches a stop being changed, a
 * colour being swapped between two elements, and an element being dropped,
 * while staying blind to the formatting differences that are legitimate.
 *
 * WHAT THIS DOES NOT CLAIM
 * It does not check that each colour is on the RIGHT element -- swapping two
 * colours between two elements preserves the multiset. Geometry parity is
 * separately pinned in BrandMarkWiringContractTest (the two arc pathData
 * strings). Nor does it rasterise anything: it compares source to source.
 * A full render comparison would need an instrumented test harness, which
 * this module does not have.
 *
 * Comments are stripped from BOTH files first. Both carry long headers that
 * quote retired colour literals while explaining why they were retired --
 * including the table above, in this very file's sibling -- so a raw scan
 * would compare documentation instead of paint.
 */
class LauncherIconParityContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val launcherXml: String
        get() = source("src/main/res/drawable/ic_launcher_foreground.xml")

    private val darkMasterSvg: String
        get() = source("../../../products/retail/frontend/brand/aura-mark-on-dark.svg")

    /** Blank comment bodies; both formats use the XML/HTML comment syntax. */
    private fun stripComments(text: String): String =
        text.replace(Regex("<!--[\\s\\S]*?-->"), " ")

    /**
     * Every colour literal, normalised to #RRGGBB -- Android prefixes an
     * alpha byte the SVG does not carry, and the alphas are expressed
     * separately (strokeAlpha/fillAlpha vs opacity) and already agree.
     */
    private fun colours(text: String): Map<String, Int> =
        Regex("#([0-9A-Fa-f]{6,8})")
            .findAll(stripComments(text))
            .map { it.groupValues[1].uppercase() }
            .map { if (it.length == 8) it.substring(2) else it }
            .map { "#$it" }
            .groupingBy { it }
            .eachCount()

    @Test
    fun the_launcher_paints_exactly_the_dark_masters_palette() {
        val master = colours(darkMasterSvg)
        val launcher = colours(launcherXml)

        // Anti-vacuity. Two empty maps are equal, so a bad path or a read
        // that silently returned nothing would "pass" while comparing
        // nothing -- the failure shape ENGINEERING.md names outright.
        assertThat(master.values.sum()).isAtLeast(10)
        assertThat(launcher.values.sum()).isAtLeast(10)

        assertThat(launcher).isEqualTo(master)
    }

    @Test
    fun neither_improvised_lifted_navy_survives_anywhere_in_the_launcher() {
        // The multiset check above already fails if these are present, but
        // it fails with a whole-palette diff. These two literals are the
        // specific regression most likely to come back -- they are what a
        // future "the mark is invisible on dark" fix reaches for if it is
        // made locally instead of by taking the guide's value -- so they get
        // a check that names them.
        val code = stripComments(launcherXml).uppercase()
        assertThat(code).doesNotContain("223B5E")
        assertThat(code).doesNotContain("1E3A5A")
    }
}
