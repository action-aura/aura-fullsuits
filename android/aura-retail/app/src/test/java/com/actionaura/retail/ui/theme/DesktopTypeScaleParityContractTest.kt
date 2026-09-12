package com.actionaura.retail.ui.theme

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * The phone's type scale must be the desktop's type scale.
 *
 * Type.kt opens by saying it maps "the desktop token layer's type scale
 * (11/12/14/15/17/22/30/40)" onto the M3 roles this app uses, and every text
 * style below it carries a comment naming the token it came from. Nothing
 * checked that claim. It is the same exposure DesktopTokenParityContractTest
 * was written for on the colour side, where a hand-copied hex had already
 * drifted by one digit and shipped -- BorderHairline as #222B37 against the
 * desktop's #232B37, invisible on screen and invisible to every other guard.
 *
 * Sizes drift the same way and are just as quiet: someone adds a style and
 * types 16.sp because it looked right, or nudges a heading to 21.sp, and the
 * two clients stop being the same product at a glance. Nothing fails, because
 * nothing was asking.
 *
 * So this reads the desktop's own token block out of main.css at test time --
 * it does NOT carry a second copy of those numbers, which would just be
 * another place for the same typo -- and requires every font size declared in
 * Type.kt to be a value that block actually defines.
 *
 * What it deliberately does NOT assert: that Type.kt uses ALL eight steps. A
 * role the phone has no use for is a legitimate design decision; a size that
 * is on no step at all is not.
 */
class DesktopTypeScaleParityContractTest {

    // Same moduleRoot/source shape the other guards in this package use, so
    // CompiledTestSuiteRollCallTest can resolve the paths these open and
    // notice if they all start skipping at once. Both paths below are single
    // string literals for that reason -- an interpolated one reads back as
    // literal text and always resolves to a file that is not there.
    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val scaleToken = Regex("""--text-size-([a-z-]+)\s*:\s*(\d+)px\s*;""")
    private val fontSize = Regex("""fontSize\s*=\s*(\d+)\.sp""")

    /**
     * Every value any --text-size-* token takes anywhere in main.css.
     *
     * A SET of values, not a name-to-value map, because the desktop scale is
     * responsive: --text-size-total is 40px at :root and 34px again inside
     * the max-width 640px block, where the POS grand total shrinks. Keying by
     * name kept only the last declaration and dropped 40 off the scale
     * entirely, which made this guard fail against correct code the first
     * time it ran. The question worth asking is "is this size a step the
     * desktop declares somewhere", not "is it the step for this role at this
     * breakpoint" -- Compose has no breakpoints to compare against anyway.
     */
    private fun desktopScale(): Set<Int> {
        val css = source("../../../products/retail/frontend/css/main.css")
        return scaleToken.findAll(css).map { it.groupValues[2].toInt() }.toSet()
    }

    /** Token ROLE names declared, used only by the anti-vacuity check. */
    private fun desktopRoles(): Set<String> {
        val css = source("../../../products/retail/frontend/css/main.css")
        return scaleToken.findAll(css).map { it.groupValues[1] }.toSet()
    }

    private fun androidSizes(): List<Int> {
        val kt = source("src/main/java/com/actionaura/retail/ui/theme/Type.kt")
        return fontSize.findAll(kt).map { it.groupValues[1].toInt() }.toList()
    }

    @Test
    fun theDesktopScaleIsActuallyReadable() {
        // Anti-vacuity. If the token names or the file move, every assertion
        // below would compare against an empty set and pass while saying
        // nothing -- the failure shape where a guard goes green precisely
        // because it stopped being able to see its subject.
        assertThat(desktopRoles()).containsAtLeast(
            "micro", "meta", "body", "body-lg", "subhead", "title", "display", "total")
        assertThat(desktopScale().size).isAtLeast(8)
    }

    @Test
    fun typeKtDeclaresSomeSizesToCompare() {
        // The same anti-vacuity guard on the other side: a Type.kt that
        // stopped matching the fontSize pattern would make the parity test
        // below trivially true.
        assertThat(androidSizes().size).isAtLeast(10)
    }

    @Test
    fun everyAndroidFontSizeIsAStepOnTheDesktopScale() {
        val steps = desktopScale()
        val offScale = androidSizes().filterNot { it in steps }.distinct().sorted()
        assertThat(offScale).isEmpty()
    }
}
