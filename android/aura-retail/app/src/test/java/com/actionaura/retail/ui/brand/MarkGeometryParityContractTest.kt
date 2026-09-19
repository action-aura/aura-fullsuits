package com.actionaura.retail.ui.brand

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.w3c.dom.Element
import org.xml.sax.InputSource
import java.io.File
import java.io.StringReader
import javax.xml.parsers.DocumentBuilderFactory

/**
 * The phone must draw the SAME mark as the desktop, not a mark that merely
 * resembles it.
 *
 * WHY THIS FILE EXISTS
 * `AuraMark.kt` is Compose drawing code and the desktop's mark is an SVG, so
 * the same geometry is written out twice by hand. Until 2026-09-08 nothing
 * compared them; that drift is the reason this file exists at all (see git
 * history for the original defect: BorderHairline shipped #222B37 against
 * the desktop's #232B37, a one-digit typo, invisible on screen, caught only
 * once something finally compared the two files byte-for-coordinate).
 *
 * REWRITTEN 2026-09-19 for the "pierced A" redesign (Action-Aura-Brand-
 * Guide.md), which replaced the old "ring + upward A + beacon diamond"
 * construction this file used to pin. The OLD version of this file hardcoded
 * the exact expected numbers as literal search markers (`svgPath("84 178")`
 * to find `kotlinPath("84 * u, 178 * u")`) -- comparing two copies of the
 * SAME hand-typed digits instead of reading either side's actual geometry.
 * That is precisely how it went stale: AuraMark.kt was redrawn and this file
 * kept asserting the RETIRED numbers, silently, because nothing here ever
 * read the new ones.
 *
 * This version instead:
 *   - parses aura-mark.svg as real XML ([DocumentBuilderFactory]), reading
 *     each element's own attributes. No expected coordinate value is typed
 *     into this file as a literal.
 *   - extracts AuraMark.kt's numbers via regex keyed to the STRUCTURE of its
 *     Compose drawing calls (`val leftFacet = Path().apply { ... }`,
 *     `Rect(left = (cx - rx) * u, ...)`, `drawCircle(... radius = R * u,
 *     center = Offset(X * u, Y * u))`) rather than to the coordinate values
 *     themselves -- the numbers are captured as regex GROUPS and compared,
 *     never hardcoded as the expected answer.
 * A real Kotlin/Compose AST parser is not available in this test module
 * (no compiler-plugin or PSI dependency here), so the Kotlin side is still
 * text-derived -- that is the practical limit of "parse instead of
 * hardcode" reachable from this module. Changing either file's actual
 * geometry changes what this test reads on BOTH sides, automatically, which
 * is what the old marker-string approach never did.
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
    private val svgText: String
        get() = source("../../../products/retail/frontend/brand/aura-mark.svg")
    private val kotlin: String
        get() = source("src/main/java/com/actionaura/retail/ui/brand/AuraMark.kt")

    /** Every number (integer or decimal) appearing in [s], in order. */
    private fun nums(s: String): List<Double> =
        Regex("""-?\d+(?:\.\d+)?""").findAll(s).map { it.value.toDouble() }.toList()

    /**
     * aura-mark.svg parsed as real XML rather than string-matched. The
     * file's own header comment (see aura-mark.svg itself) contains a
     * literal "--" inside an XML comment, which the XML spec forbids and a
     * strict parser rejects with a SAXParseException -- so comments are
     * stripped by regex before parsing, the same lenient handling a browser
     * gives malformed HTML comments. This does not affect what is compared:
     * every assertion below reads element ATTRIBUTES, never comment prose.
     *
     * Cached ([svgRoot] is a `val`, computed once per test instance -- JUnit
     * gives each `@Test` method its own instance) so identity comparisons
     * (`===`) in the paint-order test below compare nodes from the SAME
     * parsed tree, not two independent re-parses.
     */
    private val svgRoot: Element by lazy {
        val withoutComments = svgText.replace(Regex("""<!--[\s\S]*?-->"""), "")
        DocumentBuilderFactory.newInstance()
            .newDocumentBuilder()
            .parse(InputSource(StringReader(withoutComments)))
            .documentElement
    }

    private fun elementsByTag(tag: String): List<Element> {
        val list = svgRoot.getElementsByTagName(tag)
        return (0 until list.length).map { list.item(it) as Element }
    }

    // ── Identify each of the SVG's 8 drawing elements by what it IS (its
    // own attributes), not by its position -- a reorder in the file is
    // still matched correctly. ──────────────────────────────────────────
    private fun svgBackArc() = elementsByTag("path").first { it.getAttribute("fill") == "none" && it.hasAttribute("opacity") }
    private fun svgFrontArc() = elementsByTag("path").first { it.getAttribute("fill") == "none" && it.hasAttribute("stroke-linecap") }
    private fun svgLeftFacet() = elementsByTag("path").first { it.getAttribute("fill").contains("fl") }
    private fun svgRightFacet() = elementsByTag("path").first { it.getAttribute("fill").contains("fr") }
    private fun svgRidgeLine() = elementsByTag("line").first()
    private fun svgCounterRect() = elementsByTag("rect").first()
    private fun svgHalo() = elementsByTag("circle").first { it.hasAttribute("opacity") }
    private fun svgCore() = elementsByTag("circle").first { !it.hasAttribute("opacity") }

    @Test
    fun `both clients draw the mark inside the same square design box`() {
        val viewBox = nums(svgRoot.getAttribute("viewBox"))
        assertThat(viewBox).hasSize(4)
        val width = viewBox[2]
        val height = viewBox[3]
        assertThat(width).isEqualTo(height) // must be square for a single `u` scale factor to mean one thing

        val divisor = Regex("""size\.toPx\(\)\s*/\s*(\d+(?:\.\d+)?)f""").find(kotlin)
        assertThat(divisor).isNotNull()
        assertThat(divisor!!.groupValues[1].toDouble()).isEqualTo(width)
    }

    @Test
    fun `the ring ellipse has the same centre and radii on both clients`() {
        val arcNums = nums(svgBackArc().getAttribute("d")) // M startX startY A rx ry xrot largeArc sweep endX endY
        assertThat(arcNums).hasSize(9)
        val startX = arcNums[0]
        val startY = arcNums[1]
        val rx = arcNums[2]
        val ry = arcNums[3]
        val endX = arcNums[7]
        val endY = arcNums[8]
        assertThat(endY).isEqualTo(startY) // both ends sit on the ellipse's own horizontal centre line
        val centerX = (startX + endX) / 2
        val centerY = startY

        val rectMatch = Regex(
            """val ringOval = Rect\(\s*""" +
                """left = \((-?[\d.]+)f - (-?[\d.]+)f\) \* u,\s*""" +
                """top = \((-?[\d.]+)f - (-?[\d.]+)f\) \* u,\s*""" +
                """right = \((-?[\d.]+)f \+ (-?[\d.]+)f\) \* u,\s*""" +
                """bottom = \((-?[\d.]+)f \+ (-?[\d.]+)f\) \* u,?\s*\)""",
        ).find(kotlin)
        assertThat(rectMatch).isNotNull()
        val g = rectMatch!!.groupValues.drop(1).map { it.toDouble() }
        // left=(cx-rx), top=(cy-ry), right=(cx+rx), bottom=(cy+ry)
        assertThat(g[0]).isEqualTo(centerX) // left's centre term
        assertThat(g[1]).isEqualTo(rx) // left's radius term
        assertThat(g[2]).isEqualTo(centerY) // top's centre term
        assertThat(g[3]).isEqualTo(ry) // top's radius term
        assertThat(g[4]).isEqualTo(centerX) // right's centre term
        assertThat(g[5]).isEqualTo(rx) // right's radius term
        assertThat(g[6]).isEqualTo(centerY) // bottom's centre term
        assertThat(g[7]).isEqualTo(ry) // bottom's radius term
    }

    @Test
    fun `the ring is rotated by the same angle about the same pivot on both clients`() {
        val transformNums = nums(svgBackArc().getAttribute("transform")) // rotate(deg pivotX pivotY)
        assertThat(transformNums).hasSize(3)
        val degrees = transformNums[0]
        val pivotX = transformNums[1]
        val pivotY = transformNums[2]
        // Both arcs must share the EXACT same transform -- it is the same
        // ellipse pierced by the ring, not two independently rotated arcs.
        assertThat(nums(svgFrontArc().getAttribute("transform"))).isEqualTo(transformNums)

        val rotateCalls = Regex("""rotate\(degrees = (-?[\d.]+)f, pivot = ringPivot\)""").findAll(kotlin).toList()
        assertThat(rotateCalls).hasSize(2) // back arc and front arc each rotate once
        rotateCalls.forEach { assertThat(it.groupValues[1].toDouble()).isEqualTo(degrees) }

        val pivotMatch = Regex("""val ringPivot = Offset\((-?[\d.]+)f \* u, (-?[\d.]+)f \* u\)""").find(kotlin)
        assertThat(pivotMatch).isNotNull()
        assertThat(pivotMatch!!.groupValues[1].toDouble()).isEqualTo(pivotX)
        assertThat(pivotMatch.groupValues[2].toDouble()).isEqualTo(pivotY)
    }

    @Test
    fun `the left and right facets of the A share the same four points each on both clients`() {
        fun kotlinFacetPoints(valName: String): List<Double> {
            val block = Regex("""val $valName = Path\(\)\.apply \{([\s\S]*?)\}""").find(kotlin)
            assertThat(block).isNotNull()
            return Regex("""(?:moveTo|lineTo)\((-?[\d.]+)f \* u, (-?[\d.]+)f \* u\)""")
                .findAll(block!!.groupValues[1])
                .flatMap { listOf(it.groupValues[1].toDouble(), it.groupValues[2].toDouble()) }
                .toList()
        }
        assertThat(kotlinFacetPoints("leftFacet")).isEqualTo(nums(svgLeftFacet().getAttribute("d")))
        assertThat(kotlinFacetPoints("rightFacet")).isEqualTo(nums(svgRightFacet().getAttribute("d")))
    }

    @Test
    fun `the ridge line and the counter square track the SVG's line and rect on both clients`() {
        val lineMatch = Regex(
            """drawLine\(\s*color = [\s\S]*?,\s*""" +
                """start = Offset\((-?[\d.]+)f \* u, (-?[\d.]+)f \* u\),\s*""" +
                """end = Offset\((-?[\d.]+)f \* u, (-?[\d.]+)f \* u\),\s*""" +
                """strokeWidth = (-?[\d.]+)f \* u,""",
        ).find(kotlin)
        assertThat(lineMatch).isNotNull()
        val (x1, y1, x2, y2, width) = lineMatch!!.groupValues.drop(1).map { it.toDouble() }
        val line = svgRidgeLine()
        assertThat(x1).isEqualTo(nums(line.getAttribute("x1")).single())
        assertThat(y1).isEqualTo(nums(line.getAttribute("y1")).single())
        assertThat(x2).isEqualTo(nums(line.getAttribute("x2")).single())
        assertThat(y2).isEqualTo(nums(line.getAttribute("y2")).single())
        assertThat(width).isEqualTo(nums(line.getAttribute("stroke-width")).single())

        val rectMatch = Regex(
            """drawRoundRect\(\s*color = [\s\S]*?,\s*""" +
                """topLeft = Offset\((-?[\d.]+)f \* u, (-?[\d.]+)f \* u\),\s*""" +
                """size = Size\((-?[\d.]+)f \* u, (-?[\d.]+)f \* u\),\s*""" +
                """cornerRadius = CornerRadius\((-?[\d.]+)f \* u, (-?[\d.]+)f \* u\),""",
        ).find(kotlin)
        assertThat(rectMatch).isNotNull()
        val rectVals = rectMatch!!.groupValues.drop(1).map { it.toDouble() }
        val rx = rectVals[0]
        val ry = rectVals[1]
        val rw = rectVals[2]
        val rh = rectVals[3]
        val rrx = rectVals[4]
        val rry = rectVals[5]
        val rect = svgCounterRect()
        assertThat(rx).isEqualTo(nums(rect.getAttribute("x")).single())
        assertThat(ry).isEqualTo(nums(rect.getAttribute("y")).single())
        assertThat(rw).isEqualTo(nums(rect.getAttribute("width")).single())
        assertThat(rh).isEqualTo(nums(rect.getAttribute("height")).single())
        assertThat(rrx).isEqualTo(nums(rect.getAttribute("rx")).single())
        assertThat(rry).isEqualTo(nums(rect.getAttribute("rx")).single()) // the SVG has no separate `ry` -- `rx` doubles as both
    }

    @Test
    fun `the back arc is dimmed by the same opacity and the front arc shares its stroke width and cap, matching the SVG`() {
        val strokes = Regex("""Stroke\(width = (-?[\d.]+)f \* u(, cap = StrokeCap\.(\w+))?\)""").findAll(kotlin).toList()
        assertThat(strokes).hasSize(2) // exactly the two arcs use a Stroke style -- the facets are filled, not stroked
        val backWidth = strokes[0].groupValues[1].toDouble()
        val frontWidth = strokes[1].groupValues[1].toDouble()
        assertThat(backWidth).isEqualTo(nums(svgBackArc().getAttribute("stroke-width")).single())
        assertThat(frontWidth).isEqualTo(nums(svgFrontArc().getAttribute("stroke-width")).single())

        // Cap: compared case-insensitively so neither side's literal casing
        // ("Round" vs "round") is typed into this test as an expected value.
        assertThat(strokes[0].groupValues[3]).isEmpty() // back arc carries no cap, on either side
        assertThat(svgBackArc().hasAttribute("stroke-linecap")).isFalse()
        assertThat(strokes[1].groupValues[3].lowercase()).isEqualTo(svgFrontArc().getAttribute("stroke-linecap"))

        val alphaMatch = Regex("""style = Stroke\(width = [\d.]+f \* u\),\s*alpha = (-?[\d.]+)f,""").find(kotlin)
        assertThat(alphaMatch).isNotNull()
        assertThat(alphaMatch!!.groupValues[1].toDouble()).isEqualTo(nums(svgBackArc().getAttribute("opacity")).single())
    }

    @Test
    fun `the energy node's halo and core share the same centre and radii, in the same order, as the SVG`() {
        val circles = Regex(
            """drawCircle\(\s*color = [\s\S]*?,\s*""" +
                """radius = (-?[\d.]+)f \* u,\s*""" +
                """center = Offset\((-?[\d.]+)f \* u, (-?[\d.]+)f \* u\),""",
        ).findAll(kotlin).toList()
        assertThat(circles).hasSize(2)
        val haloRadius = circles[0].groupValues[1].toDouble()
        val haloX = circles[0].groupValues[2].toDouble()
        val haloY = circles[0].groupValues[3].toDouble()
        val coreRadius = circles[1].groupValues[1].toDouble()
        val coreX = circles[1].groupValues[2].toDouble()
        val coreY = circles[1].groupValues[3].toDouble()

        val halo = svgHalo()
        val core = svgCore()
        assertThat(haloRadius).isEqualTo(nums(halo.getAttribute("r")).single())
        assertThat(haloX).isEqualTo(nums(halo.getAttribute("cx")).single())
        assertThat(haloY).isEqualTo(nums(halo.getAttribute("cy")).single())
        assertThat(coreRadius).isEqualTo(nums(core.getAttribute("r")).single())
        assertThat(coreX).isEqualTo(nums(core.getAttribute("cx")).single())
        assertThat(coreY).isEqualTo(nums(core.getAttribute("cy")).single())
    }

    @Test
    fun `both clients paint the mark's eight elements in the same order`() {
        // Paint order is part of the design (the ring passes BEHIND the
        // letter's shoulders and THROUGH the counter, then the front arc
        // sweeps back OVER the legs) -- both files' own comments say so.
        // Both sequences below are DERIVED, not typed: the SVG side reads
        // real document order via DOM, the Kotlin side reads the source
        // position of each construct's own (non-numeric) marker -- so a
        // reorder on either side changes what is compared, not just whether
        // a mismatch is caught.
        val svgOrder = elementsByTag("*")
            .filter { it.tagName in setOf("path", "line", "rect", "circle") }
            .map { el ->
                when {
                    el === svgBackArc() -> "backArc"
                    el === svgFrontArc() -> "frontArc"
                    el === svgLeftFacet() -> "leftFacet"
                    el === svgRightFacet() -> "rightFacet"
                    el.tagName == "line" -> "ridgeLine"
                    el.tagName == "rect" -> "counterRect"
                    el === svgHalo() -> "halo"
                    el === svgCore() -> "core"
                    else -> "unknown:${el.tagName}"
                }
            }
        assertThat(svgOrder.none { it.startsWith("unknown:") }).isTrue() // every drawing element must be one of the 8 identified roles

        val firstDrawCircle = kotlin.indexOf("drawCircle(")
        val markers = mapOf(
            "backArc" to kotlin.indexOf("val backArc = Path()"),
            "leftFacet" to kotlin.indexOf("val leftFacet = Path()"),
            "rightFacet" to kotlin.indexOf("val rightFacet = Path()"),
            "ridgeLine" to kotlin.indexOf("drawLine("),
            "counterRect" to kotlin.indexOf("drawRoundRect("),
            "frontArc" to kotlin.indexOf("val frontArc = Path()"),
            "halo" to firstDrawCircle,
            "core" to kotlin.indexOf("drawCircle(", firstDrawCircle + 1),
        )
        markers.values.forEach { assertThat(it).isGreaterThan(-1) }
        val kotlinOrder = markers.entries.sortedBy { it.value }.map { it.key }
        assertThat(kotlinOrder).isEqualTo(svgOrder)
    }
}
