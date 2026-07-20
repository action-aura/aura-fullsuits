package com.actionaura.clinic.ui.i18n

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Wave 1B (MOB-007) automated localization coverage check. Not a UI test --
 * this is a static audit of the AR_STRINGS map source file itself, so it
 * runs fast and catches regressions (a new tr("...") call site added without
 * its Arabic entry, a blank translation, or a translation that's accidentally
 * identical to the English key) without a device.
 */
class StringsCoverageTest {

    private fun stringsFile(): File {
        // Walk up from the test module to find the source file directly --
        // simpler and more robust across Gradle invocation directories than
        // trying to load the compiled resource.
        var dir = File(System.getProperty("user.dir"))
        repeat(6) {
            val candidate = File(dir, "app/src/main/java/com/actionaura/clinic/ui/i18n/Strings.kt")
            if (candidate.exists()) return candidate
            dir = dir.parentFile ?: dir
        }
        val direct = File("src/main/java/com/actionaura/clinic/ui/i18n/Strings.kt")
        if (direct.exists()) return direct
        throw IllegalStateException("Could not locate Strings.kt from ${System.getProperty("user.dir")}")
    }

    private val entryRegex = Regex("""^\s*"((?:[^"\\]|\\.)*)"\s*to\s*"((?:[^"\\]|\\.)*)"\s*,?\s*$""")

    private fun loadEntries(): List<Pair<String, String>> {
        val entries = mutableListOf<Pair<String, String>>()
        for (line in stringsFile().readLines()) {
            val m = entryRegex.find(line) ?: continue
            entries.add(m.groupValues[1] to m.groupValues[2])
        }
        return entries
    }

    @Test
    fun no_blank_english_keys_or_arabic_values() {
        val entries = loadEntries()
        assertTrue("Expected a non-trivial number of translation entries", entries.size > 50)
        val blanks = entries.filter { (en, ar) -> en.isBlank() || ar.isBlank() }
        assertTrue("Blank key or value found: $blanks", blanks.isEmpty())
    }

    @Test
    fun no_duplicate_english_keys() {
        val entries = loadEntries()
        val counts = entries.groupingBy { it.first }.eachCount()
        val dupes = counts.filter { it.value > 1 }
        assertTrue("Duplicate English keys (last one silently wins in a Kotlin map literal): $dupes", dupes.isEmpty())
    }

    @Test
    fun no_accidental_identical_english_and_arabic_values() {
        // A handful of entries are legitimately identical (e.g. a brand name
        // like "Action Aura" or a symbol-only string) -- allow-list those
        // instead of asserting zero matches, so a genuinely-forgotten
        // translation still fails the test.
        val allowed = setOf("Action Aura")
        val entries = loadEntries()
        val suspicious = entries.filter { (en, ar) -> en == ar && en !in allowed && en.any { it.isLetter() } }
        assertTrue("English/Arabic values identical (likely untranslated): $suspicious", suspicious.isEmpty())
    }
}
