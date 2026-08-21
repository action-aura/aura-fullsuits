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
 * Deliberately naive (it does not track string literals). That is safe here
 * because a false strip can only ever make an assertion HARDER to satisfy,
 * never easier -- the failure mode is a test that complains, not a test that
 * passes when it should not.
 *
 * Whitespace is collapsed too, otherwise a stripped comment leaves behind its
 * own height in blank lines and a "within N characters" window ends up
 * measuring indentation instead of code.
 */
internal fun codeOnly(src: String): String = src
    .replace(Regex("""/\*.*?\*/""", RegexOption.DOT_MATCHES_ALL), " ")
    .lines().joinToString("\n") { it.substringBefore("//") }
    .replace(Regex("""\s+"""), " ")

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
