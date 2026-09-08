package com.actionaura.retail.ui.i18n

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * The app printed its own version wrong in Arabic.
 *
 * On a Mi Note 10 with the language set to Arabic, the sign-in footer read
 * **`rc.5-1.0.0`**. The build is `1.0.0-rc.5`. Nothing had corrupted the
 * string: Unicode's bidirectional algorithm treats `.` and `-` as neutral, so
 * in a right-to-left paragraph it lays the runs out right to left and the
 * version reads backwards. The damage happens inside the text engine, after
 * every assertion any test could make about the value being passed in.
 *
 * That is why this file has two halves and neither is optional.
 * [ltrIsolate] is unit-tested directly, and the two places that render a
 * version are checked to actually call it -- a correct helper nobody calls is
 * the exact shape of this bug, since the code was already passing the right
 * string when it rendered wrong.
 *
 * The desktop hit the same bug in the same week: the brand lockup read
 * "AuraRetail" in Arabic until `direction: ltr; unicode-bidi: isolate` was put
 * on it. Two clients, one Unicode rule, found both times by looking at a
 * screen rather than by running a suite.
 */
class LtrIsolateContractTest {

    private val lri = '⁦'   // LEFT-TO-RIGHT ISOLATE
    private val pdi = '⁩'   // POP DIRECTIONAL ISOLATE

    // Same moduleRoot/source() shape as the other guards in this module, so
    // CompiledTestSuiteRollCallTest's scan recognises it.
    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    @Test
    fun `a version string comes back wrapped in an isolate`() {
        val wrapped = ltrIsolate("1.0.0-rc.5")
        assertThat(wrapped.first()).isEqualTo(lri)
        assertThat(wrapped.last()).isEqualTo(pdi)
        // The token itself must survive untouched -- this fixes how the text
        // engine ORDERS it, and must never rewrite what it says.
        assertThat(wrapped.trim(lri, pdi)).isEqualTo("1.0.0-rc.5")
    }

    @Test
    fun `wrapping twice does not nest`() {
        val once = ltrIsolate("1.0.0-rc.5")
        assertThat(ltrIsolate(once)).isEqualTo(once)
    }

    @Test
    fun `null and empty stay empty rather than becoming two invisible marks`() {
        // A bare pair of isolate characters is not an empty string: it renders
        // as nothing but is non-empty, so `isBlank()` checks and layout that
        // hides empty rows would both start behaving differently.
        assertThat(ltrIsolate(null)).isEmpty()
        assertThat(ltrIsolate("")).isEmpty()
    }

    @Test
    fun `both places that render a version actually call it`() {
        // The half that matters. ltrIsolate could be perfect and the bug would
        // still ship if a call site were missed -- which is precisely what
        // happened, in reverse, before this fix: the strings were right and the
        // screen was wrong.
        val sites = listOf(
            "src/main/java/com/actionaura/retail/ui/screens/LoginScreen.kt",
            "src/main/java/com/actionaura/retail/ui/screens/RetailExtraScreens.kt",
        )
        for (relative in sites) {
            val text = source(relative)
            val renders = Regex("""Text\(\s*(?:[a-zA-Z]+\s*=\s*)?ltrIsolate\(\s*com\.actionaura\.retail\.BuildConfig\.VERSION_NAME""")
            assertThat(renders.containsMatchIn(text)).isTrue()
            // And no BARE render survives anywhere in the file.
            val bare = Regex("""Text\(\s*com\.actionaura\.retail\.BuildConfig\.VERSION_NAME""")
            assertThat(bare.containsMatchIn(text)).isFalse()
        }
    }

    @Test
    fun `no other screen renders a bare version`() {
        // Scoped scan, so a THIRD version row added later fails here instead of
        // reaching an Arabic-speaking customer. Deliberately looks for the
        // render shape rather than for the constant: reading VERSION_NAME to
        // send it to the licensing server or to build an identity payload is
        // correct and must not be flagged.
        val screens = File(moduleRoot, "src/main/java/com/actionaura/retail/ui")
        assumeTrue("ui sources not reachable from this run context", screens.exists())
        val bare = Regex("""Text\(\s*com\.actionaura\.retail\.BuildConfig\.VERSION_NAME""")
        val offenders = screens.walkTopDown()
            .filter { it.isFile && it.extension == "kt" }
            .filter { bare.containsMatchIn(it.readText()) }
            .map { it.name }
            .toList()
        assertThat(offenders).isEmpty()
    }
}
