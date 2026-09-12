package com.actionaura.retail.ui.brand

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * The phone must draw the SAME mark as the desktop, not a mark that merely
 * resembles it.
 *
 * WHY THIS FILE EXISTS
 * `AuraMark.kt` is Compose drawing code and the desktop's mark is an SVG, so
 * the same geometry is written out twice by hand: the same 84/128/172 feet and
 * apex, the same 211/60 beacon point, the same radius-94 ring, the same 26 and
 * 20 stroke weights. Until 2026-09-08 nothing compared them. Two neighbouring
 * tests read as though something did, and neither does:
 * [com.actionaura.retail.ui.theme.DesktopTokenParityContractTest] compares the
 * five themes' PALETTE hex values, and BrandMarkWiringContractTest checks only
 * that the mark is wired onto the screens a user reaches -- both would stay
 * green while the phone drew a mark the desktop retired.
 *
 * That is not a theoretical exposure. The palette parity test exists because
 * exactly this drift already happened once with colours: BorderHairline
 * shipped as #222B37 against the desktop's #232B37, a one-digit typo,
 * invisible on screen, caught only when something finally compared the two
 * files. The mark is copied the same way by the same hands and carries more
 * numbers.
 *
 * It matters most precisely when the mark is being redesigned, which is when
 * this was written: change the SVG, ship, and the phone keeps drawing the old
 * one silently until somebody opens the app and looks.
 *
 * WHAT IT COMPARES
 * The numbers, not the rendering. Both sides work in the same 256-unit box, so
 * every quantity below appears literally in both files and a mismatch is
 * always a real divergence rather than a rounding artefact. The ring's ANGLES
 * are deliberately excluded: SVG expresses the 300-degree gap as a dash array
 * plus a rotation and Compose as a start/sweep pair, so the two are not
 * comparable as text, and asserting a hand-derived equivalence between them
 * would be comparing two values that move together -- ENGINEERING.md section 2's
 * quiet failure. The ring's POSITION and SIZE are compared, because those are
 * written identically on both sides.
 */
class MarkGeometryParityContractTest {

    // Same shape as DesktopTokenParityContractTest's moduleRoot/source(): a
    // root File() plus a helper taking the path as a String, recognised by
    // SHAPE rather than name by this module's CompiledTestSuiteRollCallTest.
    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    // From this module's root (android/aura-retail/app) up to the repo root.
    private val svg: String
        get() = source("../../../products/retail/frontend/brand/aura-mark.svg")
    private val kotlin: String
        get() = source("src/main/java/com/actionaura/retail/ui/brand/AuraMark.kt")

    /** Every number in a `d="..."` path whose command letters match [shape]. */
    private fun svgPath(marker: String): List<Int> {
        val d = Regex("""d="([^"]*$marker[^"]*)"""").find(svg)?.groupValues?.get(1)
        assertThat(d).isNotNull()
        return Regex("""-?\d+""").findAll(d!!).map { it.value.toInt() }.toList()
    }

    /** Numbers from a Compose `Path()` block whose body contains [marker]. */
    private fun kotlinPath(marker: String): List<Int> {
        val block = Regex("""Path\(\)\.apply\s*\{(.*?)\}""", RegexOption.DOT_MATCHES_ALL)
            .findAll(kotlin)
            .map { it.groupValues[1] }
            .firstOrNull { it.contains(marker) }
        assertThat(block).isNotNull()
        return Regex("""(-?\d+)\s*\*\s*u""").findAll(block!!)
            .map { it.groupValues[1].toInt() }.toList()
    }

    private fun svgStrokeWidthFor(pathMarker: String): Int {
        // The stroke-width attribute on the same <path> element as this `d`.
        val element = Regex("""<path[^>]*$pathMarker[^>]*>""").find(svg)?.value
        assertThat(element).isNotNull()
        return Regex("""stroke-width="(\d+)"""").find(element!!)!!.groupValues[1].toInt()
    }

    private fun kotlinStrokeWidthAfter(marker: String): Int {
        val after = kotlin.substringAfter(marker)
        return Regex("""Stroke\(width\s*=\s*(\d+)\s*\*\s*u""").find(after)!!
            .groupValues[1].toInt()
    }

    @Test
    fun `the A's peak has the same three points on both clients`() {
        assertThat(kotlinPath("84 * u, 178 * u"))
            .isEqualTo(svgPath("84 178"))          // 84,178 -> 128,70 -> 172,178
    }

    @Test
    fun `the A's crossbar has the same two points on both clients`() {
        assertThat(kotlinPath("108 * u, 142 * u"))
            .isEqualTo(svgPath("108 142"))         // 108,142 -> 148,142
    }

    @Test
    fun `the beacon diamond has the same four points on both clients`() {
        assertThat(kotlinPath("196 * u, 45 * u"))
            .isEqualTo(svgPath("196 45"))          // 196,45 -> 211,60 -> 196,75 -> 181,60
    }

    @Test
    fun `the A's stroke weights match the desktop`() {
        // The 2026-09-08 redesign raised these (peak 19 to 26, bar 15 to 20)
        // so the letter rather than the ring is read first. Both files carry
        // that reasoning in a comment; only this test carries the number.
        assertThat(kotlinStrokeWidthAfter("val stem = Path()"))
            .isEqualTo(svgStrokeWidthFor("84 178"))
        assertThat(kotlinStrokeWidthAfter("val bar = Path()"))
            .isEqualTo(svgStrokeWidthFor("108 142"))
    }

    @Test
    fun `the ring sits in the same place at the same size and weight`() {
        val circle = Regex("""<circle[^>]*>""").find(svg)!!.value
        fun attr(name: String) =
            Regex("""$name="(\d+)"""").find(circle)!!.groupValues[1].toInt()
        val cx = attr("cx")
        val r = attr("r")

        // Compose takes a bounding box, SVG a centre and radius: the same
        // circle, stated the two ways each API wants.
        val arc = kotlin.substringAfter("drawArc(")
        val topLeft = Regex("""topLeft = Offset\(\((\d+) - (\d+)\)""").find(arc)!!.groupValues
        assertThat(topLeft[1].toInt()).isEqualTo(cx)
        assertThat(topLeft[2].toInt()).isEqualTo(r)

        val size = Regex("""size = Size\((\d+) \* u""").find(arc)!!.groupValues[1].toInt()
        assertThat(size).isEqualTo(2 * r)

        val kotlinStroke = Regex("""style = Stroke\(width = (\d+) \* u""")
            .find(arc)!!.groupValues[1].toInt()
        val svgStroke = Regex("""stroke-width="(\d+)"""").find(circle)!!
            .groupValues[1].toInt()
        assertThat(kotlinStroke).isEqualTo(svgStroke)
    }

    @Test
    fun `both clients draw the mark in the same 256-unit box`() {
        // Everything above compares raw numbers, which only means anything if
        // the two coordinate systems are the same size. If either side ever
        // moves to a different viewBox, every assertion above silently becomes
        // a comparison of unrelated quantities -- so pin the premise itself.
        assertThat(svg).contains("viewBox=\"0 0 256 256\"")
        assertThat(kotlin).contains("256")
    }
}
