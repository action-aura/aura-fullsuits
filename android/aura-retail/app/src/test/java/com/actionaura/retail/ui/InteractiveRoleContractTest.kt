package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Every hand-built `Modifier.clickable` / `.selectable` / `.toggleable` call
 * site in `src/main` carries an explicit `role = ...` argument.
 *
 * WHY THIS EXISTS
 *
 * A Material `Button`/`Card(onClick=...)`/`RadioButton`/`Switch` supplies its
 * own accessibility role for free. The moment a screen reaches past those --
 * a hand-built card that needs a gradient fill (`TillCard` in Components.kt),
 * a settings row that is a bare `Row.clickable` (Language, Theme, This
 * device's branch, Licensing -- all in RetailExtraScreens.kt), a
 * `Row.selectable` radio option (the language/theme dialogs and the branch
 * picker, four sites), or a labelled `Row.toggleable` switch (the printer
 * auto-print row) -- nothing supplies that role automatically. Without it,
 * TalkBack announces the control as an unlabelled plain view: still tappable,
 * but indistinguishable from static text to a screen-reader user, who gets no
 * indication it is a button, a radio option, or a switch at all.
 *
 * Found in the accessibility audit that added this test (2026-09-19): TillCard
 * had exactly this gap, silently propagated into every one of its ~15
 * call sites app-wide (dashboard quick actions, product tiles, customer/
 * supplier/PO rows, ...), plus five bare `.clickable`/`.selectable` sites and
 * one orphaned `Switch` discovered alongside it. See that commit for the
 * full list and the fixes.
 *
 * The bare trailing-lambda form `.clickable { ... }` (no `(...)` argument
 * list at all, so no `role` can be attached) is refused outright, not just
 * required to carry a role -- every legitimate use in this module already
 * carries at least `role = ...` in parentheses before the trailing lambda, so
 * a NEW bare one is exactly the regression this guards against.
 *
 * No Compose test runner and no device in this environment, so this is a
 * source-reading contract test, same shape and justification as
 * ColorTokenContractTest / OverflowNavigationContractTest -- and it reuses
 * their [codeOnly] comment-stripper for the same reason: this module's
 * screens carry long "why" comments that themselves quote `.clickable(` and
 * `role =`, so scanning raw source would confuse the explanation with the
 * thing being checked.
 */
class InteractiveRoleContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    // Spelled as a literal at the call, not built from a constant: this is
    // what gives CompiledTestSuiteRollCallTest's completeness scan a path it
    // can see for this file's own assumeTrue() guards -- see ColorTokenContractTest's
    // colorKt for the identical reasoning, discovered there first.
    private val componentsKt: String get() = source("src/main/java/com/actionaura/retail/ui/components/Components.kt")

    /** Every Kotlin source under src/main, path relative to the module root. */
    private fun mainSources(): List<String> {
        val root = moduleRoot.resolve("src/main/java")
        assumeTrue("src/main/java not reachable from this run context", root.isDirectory)
        return root.walkTopDown()
            .filter { it.isFile && it.extension == "kt" }
            .map { it.relativeTo(moduleRoot).path.replace('\\', '/') }
            .sorted()
            .toList()
    }

    /**
     * The balanced-parenthesis argument text of the `(...)` immediately
     * following `.<fnName>` at [dotIndex] (the index of the leading '.'), or
     * null when `.<fnName>` is followed by something other than `(` -- the
     * bare trailing-lambda shape `.clickable { ... }` with no argument list
     * to carry a role in at all.
     */
    private fun parenArgsAfter(src: String, dotIndex: Int, fnName: String): String? {
        var i = dotIndex + 1 + fnName.length
        while (i < src.length && src[i].isWhitespace()) i++
        if (i >= src.length || src[i] != '(') return null
        val argsStart = i + 1
        var depth = 1
        i++
        while (i < src.length && depth > 0) {
            when (src[i]) {
                '(' -> depth++
                ')' -> depth--
            }
            i++
        }
        return src.substring(argsStart, i - 1)
    }

    /**
     * Drop `import ...` lines before scanning.
     *
     * `import androidx.compose.foundation.clickable` (and the `.selectable` /
     * `.toggleable` siblings) end in exactly the substring this test searches
     * for -- ".clickable" -- with nothing resembling an argument list after
     * it, which is indistinguishable from the bare-lambda call shape this
     * test exists to catch. Without this, every file that imports the
     * extension function reports a phantom violation on its own import line,
     * which is a false positive this test cannot tell apart from a real one.
     */
    private fun stripImports(src: String): String =
        src.lineSequence().filterNot { it.trim().startsWith("import ") }.joinToString("\n")

    /** Every `.<fnName>` call site in [src] (already comment-stripped), paired
     *  with its parenthesised argument text -- null for the bare-lambda form. */
    private fun callSites(src: String, fnName: String): List<String?> {
        val results = mutableListOf<String?>()
        val needle = ".$fnName"
        var idx = src.indexOf(needle)
        while (idx >= 0) {
            val after = idx + needle.length
            // Word boundary: ".clickableFoo(" is a different call and must
            // not be mistaken for ".clickable(".
            if (after >= src.length || !(src[after].isLetterOrDigit() || src[after] == '_')) {
                results.add(parenArgsAfter(src, idx, fnName))
            }
            idx = src.indexOf(needle, after)
        }
        return results
    }

    private val roleArg = Regex("""\brole\s*=""")

    private fun violationsFor(fnName: String): List<String> {
        val violations = mutableListOf<String>()
        for (path in mainSources()) {
            val code = codeOnly(stripImports(source(path)))
            for (args in callSites(code, fnName)) {
                when {
                    args == null -> violations.add("$path: bare .$fnName { } with no argument list, so no role")
                    !roleArg.containsMatchIn(args) -> violations.add("$path: .$fnName(...) with no role= argument")
                }
            }
        }
        return violations
    }

    // ── The guards ───────────────────────────────────────────────────────────

    @Test
    fun every_clickable_call_carries_a_role() {
        assertThat(violationsFor("clickable")).isEmpty()
    }

    @Test
    fun every_selectable_call_carries_a_role() {
        assertThat(violationsFor("selectable")).isEmpty()
    }

    @Test
    fun every_toggleable_call_carries_a_role() {
        assertThat(violationsFor("toggleable")).isEmpty()
    }

    @Test
    fun till_card_click_path_specifically_carries_role_button() {
        // The single highest-impact site: Components.kt's TillCard is a
        // hand-built card with no Material Button/Card underneath it to
        // supply a role for free, reused at ~15 call sites app-wide
        // (dashboard quick actions, product tiles, customer/supplier/PO
        // rows, ...). Pinned by name, not just swept up in the module-wide
        // guards above, because this is the one call site the whole app's
        // TalkBack-button coverage actually funnels through.
        val code = codeOnly(stripImports(componentsKt))
        val clickCalls = callSites(code, "clickable")
        assertThat(clickCalls).isNotEmpty()
        assertThat(clickCalls.all { it != null && roleArg.containsMatchIn(it) }).isTrue()
    }

    // ── Guards the guard ─────────────────────────────────────────────────────

    @Test
    fun the_scan_is_not_silently_finding_nothing() {
        // A word-boundary check that stopped matching, a walk that found no
        // files, or a codeOnly() that started eating code would leave every
        // assertion above inspecting an empty list and reporting green over a
        // scan that saw nothing. The 2026-09-19 audit's own call sites are the
        // floor: TillCard's onClick path, the four bare Row.clickable settings
        // rows, the four Row.selectable radio rows (two dialogs + two rows in
        // the branch picker) and the one Row.toggleable Switch row.
        var clickableCalls = 0
        var selectableCalls = 0
        var toggleableCalls = 0
        for (path in mainSources()) {
            val code = codeOnly(stripImports(source(path)))
            clickableCalls += callSites(code, "clickable").size
            selectableCalls += callSites(code, "selectable").size
            toggleableCalls += callSites(code, "toggleable").size
        }
        assertThat(clickableCalls).isAtLeast(6)
        assertThat(selectableCalls).isAtLeast(4)
        assertThat(toggleableCalls).isAtLeast(1)
        val files = mainSources()
        assertThat(files).contains("src/main/java/com/actionaura/retail/ui/components/Components.kt")
        assertThat(files).contains("src/main/java/com/actionaura/retail/ui/screens/RetailExtraScreens.kt")
    }
}
