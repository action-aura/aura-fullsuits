package com.actionaura.retail.ui

/**
 * Text utilities for the source-reading contract tests in this module
 * (EmployeesWiringContractTest, EmployeeSalesWiringContractTest,
 * LicensingWiringContractTest's siblings).
 *
 * These tests exist because there is no Compose test runner, no Chaquopy and
 * no device in this environment, and "is this screen actually in the nav
 * graph" / "does this string actually have an Arabic entry" are precisely the
 * regressions that stay invisible until a customer hits them. Scanning the
 * Kotlin source is the only seam available for that class of question.
 *
 * Extracted here rather than copied a third time. Deliberately NOT retrofitted
 * into EmployeesWiringContractTest, which keeps its own private copies: that
 * file is shipped evidence for a guard that already passed, and a refactor
 * that rewrites the parser underneath it would quietly change what that
 * evidence means. New tests use this; the old one is left exactly as it was
 * verified.
 */

/**
 * Strip `//` and block comments so an assertion about CODE is not satisfied --
 * or defeated -- by prose. Screens in this codebase deliberately carry long
 * "why" comments that quote the very strings and bugs the tests guard against,
 * so scanning raw source confuses the explanation with the offence in both
 * directions.
 *
 * ── A note on how this docstring is written ──────────────────────────────────
 * It never spells the two block-comment delimiters literally, and says OPENER
 * and TERMINATOR instead. Kotlin block comments NEST: an opener written inside
 * this comment starts a SECOND level, and the first terminator then only takes
 * the file back down to the first -- so the comment ends somewhere other than
 * where it looks like it ends and the lines after that point become top-level
 * garbage. The previous revision of this very file spelled them out while
 * explaining the hazard, terminated at what read as its own line 45, and cost
 * the whole module its compile: 261 errors, zero Android tests run. The main
 * sources learned the same lesson one file over (RetailSession.kt's
 * CAP_REPORTS, TerminalIdentity.kt's header).
 *
 * ── One left-to-right pass, not two regex sweeps ─────────────────────────────
 * This used to strip BLOCK comments first and LINE comments second, which gave
 * an OPENER that only ever appeared INSIDE a `//` comment the power to open a
 * phantom block running to the next TERMINATOR anywhere in the file. Measured
 * on AppRoot.kt, whose line 90 is a `//` comment naming the route prefix
 * `/_internal/sync/` followed by a glob star -- a slash and a star, adjacent,
 * which is an OPENER -- the phantom ran from there to the KDoc terminator on
 * line 198 and silently deleted 5,949 characters, 108 lines, of live code,
 * including the `tr("Starting…")` call site on line 188. Every "does this
 * screen have an Arabic entry for each key" assertion that runs on this output
 * was therefore reporting green about text it had never seen. Models.kt:576
 * carries the same shape and is harmless only because no TERMINATOR happens to
 * follow it.
 *
 * Reversing the two sweeps fixes that case and creates its mirror image: line-
 * stripping first eats the TERMINATOR closing a one-line block comment that
 * itself contains a `//`, after which the block runs on and deletes code again.
 * Zero instances of that shape exist in this module today, which is exactly the
 * sort of luck the AppRoot.kt case already spent.
 *
 * So the rule is now the one the Kotlin compiler itself uses: whichever
 * delimiter appears FIRST opens a comment, and the other is inert until that
 * comment closes.
 *
 * ── And block comments NEST, because Kotlin's do ─────────────────────────────
 * This function used to stop a block comment at its FIRST terminator and
 * documented that as safe, on the grounds that retaining text is the harder
 * direction to be wrong in. That reasoning is sound for the text it retains and
 * says nothing about the DECLARATIONS it retains, which is where the damage
 * was. Given a KDoc containing a nested opener, the compiler ends the comment
 * one terminator LATER than this did -- so a class, its `@Test` methods, and
 * every assertion in them can be comment to the compiler and live code here.
 *
 * That is not hypothetical. TerminalIdentityContractTest.kt opened two nested
 * comments in prose (`/api/devices` followed by a glob star, twice) and closed
 * one, so the entire file -- eleven assertions -- was a single unterminated
 * comment. The non-nesting reading of it reported `class
 * TerminalIdentityContractTest` and `@Test` as live source: a structural guard
 * that stays green while the thing it guards is not compiled at all. Across all
 * 70 `.kt` files in this module the two readings differ on exactly the 3 files
 * that wave touched, which is the whole argument for matching the compiler
 * rather than approximating it.
 *
 * Still deliberately naive about STRING LITERALS -- a `//` inside a URL does
 * truncate the line. That naivety keeps the character the old docstring
 * claimed for the whole function and could not actually deliver: a false strip
 * here can only ever make an assertion HARDER to satisfy, because it removes a
 * BOUNDED region (to end of line, or to the matching TERMINATOR) from both the
 * code and the prose being compared. What it can no longer do is open an
 * unbounded one, or hand back a declaration the compiler never saw.
 *
 * Whitespace is collapsed too, otherwise a stripped comment leaves behind its
 * own height in blank lines and a "within N characters" window ends up
 * measuring indentation instead of code.
 */
internal fun codeOnly(src: String): String {
    val out = StringBuilder(src.length)
    var i = 0
    while (i < src.length) {
        when {
            src.startsWith("//", i) -> {
                // To end of line. The newline itself is left for the collapse
                // below so line-adjacent tokens do not fuse together.
                val nl = src.indexOf('\n', i)
                i = if (nl < 0) src.length else nl
                out.append(' ')
            }
            src.startsWith("/*", i) -> {
                // Counts levels, because Kotlin's block comments nest. An
                // unterminated one runs to end of file, which is also what the
                // compiler does (it then reports "Unclosed comment").
                //
                // `//` is deliberately NOT special inside here: to the Kotlin
                // lexer a line comment opener sitting inside a block comment is
                // just two more characters of prose.
                var depth = 1
                i += 2
                while (i < src.length && depth > 0) {
                    when {
                        src.startsWith("/*", i) -> { depth++; i += 2 }
                        src.startsWith("*/", i) -> { depth--; i += 2 }
                        else -> i++
                    }
                }
                out.append(' ')
            }
            else -> out.append(src[i++])
        }
    }
    return out.toString().replace(Regex("""\s+"""), " ")
}

/**
 * Every string literal in [src], with runs joined: `"a " + "b"` yields one
 * entry `"a b"`. That form is everywhere in this codebase because the
 * sentences are longer than a line, and a checker that treated each fragment
 * as its own key would find nothing.
 */
internal fun literalRuns(src: String): List<String> {
    val runs = mutableListOf<String>()
    val current = StringBuilder()
    var i = 0
    while (i < src.length) {
        if (src[i] != '"') { i++; continue }
        i++
        while (i < src.length && src[i] != '"') {
            if (src[i] == '\\' && i + 1 < src.length) {
                current.append(when (val n = src[i + 1]) {
                    'n' -> '\n'; 't' -> '\t'; else -> n
                })
                i += 2
            } else current.append(src[i++])
        }
        i++
        // A `+` between two literals continues the same logical string.
        var j = i
        while (j < src.length && src[j].isWhitespace()) j++
        if (j < src.length && src[j] == '+') {
            var k = j + 1
            while (k < src.length && src[k].isWhitespace()) k++
            if (k < src.length && src[k] == '"') { i = k; continue }
        }
        runs.add(current.toString())
        current.setLength(0)
    }
    return runs
}

/**
 * The literal arguments of every `tr(...)` call in [src].
 *
 * Calls whose argument is not a pure literal concatenation -- `tr(r.error ?:
 * "...")`, `tr(it)` -- are skipped: those carry a server sentence or a value
 * from elsewhere, and belong to a different assertion (the server-refusal
 * catalogue check) rather than to per-screen key coverage.
 */
internal fun trKeys(src: String): List<String> {
    val out = mutableListOf<String>()
    var idx = src.indexOf("tr(")
    while (idx >= 0) {
        val prev = if (idx == 0) ' ' else src[idx - 1]
        // `prev` filter: skips `attr(`, `.tr(`, `mgr(` and friends -- only a
        // bare `tr(` call is a translation key.
        if (!prev.isLetterOrDigit() && prev != '_' && prev != '.') {
            var depth = 0
            var i = idx + 2
            var inStr = false
            var end = -1
            while (i < src.length) {
                val c = src[i]
                if (inStr) {
                    if (c == '\\') i++ else if (c == '"') inStr = false
                } else when (c) {
                    '"' -> inStr = true
                    '(' -> depth++
                    ')' -> { depth--; if (depth == 0) { end = i } }
                }
                if (end >= 0) break
                i++
            }
            if (end > idx + 3) {
                val arg = src.substring(idx + 3, end)
                val withoutLiterals = arg
                    .replace(Regex("\"(\\\\.|[^\"\\\\])*\""), "")
                    .replace("+", "").trim()
                if (withoutLiterals.isEmpty()) {
                    literalRuns(arg).singleOrNull()?.let { out.add(it) }
                }
            }
        }
        idx = src.indexOf("tr(", idx + 3)
    }
    return out
}
