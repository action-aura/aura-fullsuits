package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * MODULE-WIDE Arabic coverage: every `tr("literal")` call site in
 * `src/main/java`, checked against ui/i18n/Strings.kt's catalogue in one
 * sweep.
 *
 * ── Why this exists rather than one more per-screen check ────────────────────
 * `tr()` falls back to its English key when the catalogue has no entry. That
 * is the right runtime behaviour -- a missing translation degrades to readable
 * English instead of a blank or a crash -- and it is also why the gap is
 * invisible: nothing fails, nothing logs, the screen simply speaks the wrong
 * language to the one customer who cannot read it.
 *
 * This module has now shipped that three times. "Categories" and its subtitle
 * went onto the More hub with no entry, so the first row of the Records
 * section was English on an Arabic till. "Licensing" did the same on two
 * surfaces. Then CategoriesScreen shipped with 11 of its 12 keys missing --
 * a whole screen in English, reached from a row that had just been fixed --
 * and the guard that could have caught it, OverflowNavigationContractTest's
 * `every_translated_string_the_more_screen_uses_is_in_the_catalogue`, is
 * bounded to MoreScreen's own body by design.
 *
 * Each of those was found by widening a per-screen scan by hand, one screen at
 * a time, after the fact. This asks the question once, for every file.
 *
 * ── What it can and cannot see ───────────────────────────────────────────────
 * [trKeys] deliberately skips calls whose argument is not a pure literal --
 * `tr(r.error ?: "...")`, `tr(label)`, `tr(paymentMethodName(...))`. Those
 * carry a server sentence or a value resolved at runtime and belong to a
 * different assertion; this one cannot say anything about them. It also says
 * nothing about the QUALITY of a translation -- only that an entry exists.
 *
 * ── The exemption list is the point of interest ──────────────────────────────
 * [EXEMPT] is per-FILE and every entry carries its reason in writing. That is
 * the deal this guard offers: a screen may ship untranslated, but only if
 * somebody wrote down that it did. Adding a file here is a decision with a
 * name on it; adding one silently is what this test refuses.
 */
class TranslationCatalogueCoverageTest {

    private val moduleRoot = File(".")

    /**
     * Files allowed to use keys the catalogue does not carry, with the reason.
     * Keep this SHORT: the cost of an entry is that a whole file stops being
     * checked, so a screen listed here can regress further without anyone
     * hearing about it.
     */
    private val EXEMPT: Map<String, String> = mapOf(
        // The file's own docstring states this gap explicitly ("KNOWN GAP,
        // stated rather than hidden"): the three lines that make a factual
        // claim about what the app just did ARE translated; the rest of the
        // licensing copy, and LicensingMessages' reason-code table with it,
        // still needs a real Arabic pass.
        "ui/screens/LicensingScreen.kt" to
            "KNOWN GAP recorded in the file's docstring -- licensing copy awaits a real Arabic pass",
        // A single key: the e-mail field's placeholder "name@shop.com", which
        // is an example address rather than a sentence. Translating it would
        // mean inventing an Arabic domain that does not exist.
        "ui/screens/LoginScreen.kt" to
            "one key, the example address placeholder name@shop.com -- nothing to translate",
    )

    private fun mainSources(): List<File> {
        val root = File(moduleRoot, "src/main/java")
        assumeTrue("src/main/java not reachable from this run context", root.exists())
        return root.walkTopDown().filter { it.isFile && it.extension == "kt" }.sorted().toList()
    }

    private fun catalogue(): Set<String> {
        val strings = File(moduleRoot, "src/main/java/com/actionaura/retail/ui/i18n/Strings.kt")
        assumeTrue("Strings.kt not reachable from this run context", strings.exists())
        return literalRuns(codeOnly(strings.readText())).toSet()
    }

    /** `com/actionaura/retail/ui/screens/Foo.kt` -> `ui/screens/Foo.kt`. */
    private fun shortName(f: File): String =
        f.path.replace('\\', '/').substringAfter("com/actionaura/retail/")

    @Test
    fun every_translated_literal_in_the_module_has_an_arabic_entry() {
        val catalog = catalogue()
        // Guards the guard: an empty catalogue (a moved file, a parser that
        // stopped matching) would make every assertion below vacuous.
        assertThat(catalog).isNotEmpty()

        val gaps = sortedMapOf<String, List<String>>()
        for (file in mainSources()) {
            val short = shortName(file)
            if (short in EXEMPT) continue
            val missing = trKeys(codeOnly(file.readText()))
                .filterNot { it in catalog }
                .distinct()
                .sorted()
            if (missing.isNotEmpty()) gaps[short] = missing
        }

        // Reported as a map so a failure names the FILE and the exact keys,
        // rather than a bare count somebody then has to go and reproduce.
        assertThat(gaps).isEmpty()
    }

    @Test
    fun the_scan_actually_reaches_the_screens_it_claims_to() {
        // Without this, a broken path or a filter that matched nothing would
        // report perfect coverage of zero files. Names three screens that are
        // known to be translated, in three different packages.
        val scanned = mainSources().map { shortName(it) }
        assertThat(scanned).containsAtLeast(
            "ui/screens/CategoriesScreen.kt",
            "ui/screens/RetailExtraScreens.kt",
            "ui/AppRoot.kt",
        )

        val keys = mainSources()
            .filter { shortName(it) !in EXEMPT }
            .flatMap { trKeys(codeOnly(it.readText())) }
        // The module has hundreds of translated literals; a scan returning a
        // handful means the parser broke, not that the app lost its copy.
        assertThat(keys.size).isGreaterThan(200)
    }

    @Test
    fun no_string_handed_to_the_os_chooser_skips_tr() {
        // The blind spot of everything above: a scan of `tr("literal")` call
        // sites can only ever check strings that ALREADY go through tr(). A
        // literal handed straight to the framework is invisible to it.
        //
        // That is not hypothetical here. The share sheet's chooser title was
        // the bare literal "Share receipt" on a screen whose own button read
        // tr("Share Receipt") -- an Arabic cashier tapped a translated button
        // and got an English system dialog. One call site, so this is pinned
        // by shape rather than generalised further; the receipt BODY's labels
        // are deliberately still English and belong to DESIGN.md §9 item 3's
        // bilingual receipt template, not here.
        val offenders = mainSources().filter { file ->
            // Not a raw string: the pattern has to END in a double quote.
            Regex("createChooser\\([^)]*,\\s*\"").containsMatchIn(codeOnly(file.readText()))
        }.map { shortName(it) }
        assertThat(offenders).isEmpty()

        // Guards the guard: if the share sheet is ever deleted or moved, this
        // test must not keep passing over zero call sites.
        val chooserSites = mainSources().count { codeOnly(it.readText()).contains("createChooser(") }
        assertThat(chooserSites).isAtLeast(1)
    }

    @Test
    fun every_exempt_file_still_exists_and_still_needs_the_exemption() {
        // An exemption that outlives its file, or its reason, is a hole nobody
        // is watching. If a screen gets its Arabic pass, this fails and the
        // entry has to come out -- which is the moment the guard starts
        // covering it again.
        val catalog = catalogue()
        // Resolved out of the SAME enumeration the scan uses, deliberately,
        // rather than by building a path string: CompiledTestSuiteRollCallTest
        // vets every literal path a guard in this module opens, and an
        // interpolated one reads to it as a file that does not exist. Reusing
        // the enumeration also means an exemption cannot name a file the scan
        // would never have reached anyway.
        val byShortName = mainSources().associateBy { shortName(it) }
        for ((short, reason) in EXEMPT) {
            assertThat(reason).isNotEmpty()
            val file = byShortName[short]
            assertThat(file).isNotNull()
            val missing = trKeys(codeOnly(file!!.readText())).filterNot { it in catalog }
            assertThat(missing).isNotEmpty()
        }
    }
}
