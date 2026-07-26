package com.actionaura.clinic.ui.i18n

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Wave 1B (MOB-007) heuristic static audit: flags capitalized English string
 * literals in Compose screen files that are NOT wrapped in tr(...), so a
 * future PR that adds a new hardcoded label doesn't silently regress
 * localization coverage. This is a heuristic, not a full Kotlin parser --
 * it deliberately allow-lists patterns that are not user-facing text
 * (Modifier/Alignment/style DSL, imports, comments, map keys, data values
 * sent to the backend). See docs/release/wave1b/clinic-arabic-localization-report.md
 * for the full manual audit this test complements.
 */
class HardcodedStringAuditTest {

    private fun uiDir(): File {
        var dir = File(System.getProperty("user.dir"))
        repeat(6) {
            val candidate = File(dir, "app/src/main/java/com/actionaura/clinic/ui")
            if (candidate.exists()) return candidate
            dir = dir.parentFile ?: dir
        }
        val direct = File("src/main/java/com/actionaura/clinic/ui")
        if (direct.exists()) return direct
        throw IllegalStateException("Could not locate ui/ from ${System.getProperty("user.dir")}")
    }

    // Wave 1B follow-up: this originally only scanned ui/screens/, which is
    // exactly why it missed a real gap in AppRoot.kt (nav tab/title/drawer
    // labels, found live on a real device -- "Patients" and "Billing" titles
    // stayed English because their Strings.kt entries were simply missing,
    // in a file this audit never looked at). Now scans ui/screens/*.kt plus
    // AppRoot.kt itself, which is where nav chrome text actually lives.
    private fun targetFiles(): List<File> {
        val root = uiDir()
        val screens = File(root, "screens").listFiles { f -> f.extension == "kt" }?.toList() ?: emptyList()
        val appRoot = File(root, "AppRoot.kt").let { if (it.exists()) listOf(it) else emptyList() }
        return screens + appRoot
    }

    // Matches a quoted, capitalized, multi-word-ish literal: a reasonable
    // proxy for "this looks like user-facing prose", not a single technical
    // token (route names, hex colors, format specifiers already excluded by
    // requiring a space or 4+ letters).
    private val literalRegex = Regex(""""[A-Z][a-zA-Z]{2,}[a-zA-Z ,.?!]*"""")

    private val safeContext = listOf(
        "import ", "package ", "//", "* ", "style = ", "color = ", "modifier = ",
        "FontWeight", "Alignment", "Arrangement", " to \"", "fontWeight",
        "textAlign", "shape = ", "keyboardType", "contentDescription = null",
        "TimeZone.getTimeZone", // technical zone id ("UTC"), not user-facing text
    )

    // DashboardScreen's greeting is translated via tr(greeting) at its display
    // site, not at the literal itself -- necessary so the greeting re-translates
    // live on a language switch instead of being baked in once. The heuristic
    // above can't see through that indirection, so these three are allow-listed
    // rather than reported as false negatives; StringsCoverageTest still proves
    // both are present in Strings.kt.
    private val translatedViaIndirection = setOf(
        "\"Good morning\"", "\"Good afternoon\"", "\"Good evening\"",
        // AppRoot.kt's Dest(route, label, icon) label and the route->title
        // `when` block are raw English *lookup keys* by design -- each is
        // passed through tr() once, at its single render/consumption site
        // (the bottom-nav Text and the top-bar Text(tr(title), ...)), not at
        // its definition. Wrapping them here too would just be a harmless
        // no-op, but StringsCoverageTest already proves each key has a real
        // Arabic entry, so allow-listing the definitions avoids noise.
        "\"Dashboard\"", "\"Patients\"", "\"Appointments\"", "\"Billing\"",
        "\"Settings\"", "\"Doctors\"", "\"Lab Expenses\"", "\"Prescriptions\"", "\"Patient\"",
        // Brand names, never translated (matches "Action Aura" elsewhere).
        "\"Action Aura\"", "\"Aura AI\"",
        // Phase 7: LicensingScreen.kt's bare LicenseState/Owner-protocol code
        // comparisons (state == "SUCCESS", state in setOf("RESTRICTED", ...))
        // -- these match commercial_runtime/licensing_contracts/state_machine.py's
        // enum values and Owner's response "result" field verbatim; they are
        // protocol identifiers being compared, not prose. The one label actually
        // shown to the user for each state is separately translated inline in
        // stateLabel() (tr("Restricted") etc.), same as this list's other entries.
        "\"SUCCESS\"", "\"RESTRICTED\"", "\"WARNING\"", "\"SUSPENDED\"", "\"REVOKED\"", "\"EXPIRED\"",
        // Phase 8 Part O: Owner's activation response "result" field can
        // also be "PENDING" (manual-approval/risk-review gate) -- same
        // protocol-identifier comparison as the values above, not prose.
        // The one label shown to the user is separately translated inline
        // via tr("This activation is awaiting manual approval...").
        "\"PENDING\"",
    )

    @Test
    fun screen_files_have_no_new_unwrapped_english_literals() {
        val offenders = mutableListOf<String>()
        val files = targetFiles()
        assertTrue("Expected to find Clinic screen files", files.isNotEmpty())

        for (file in files) {
            file.readLines().forEachIndexed { idx, rawLine ->
                val line = rawLine.trim()
                if (safeContext.any { line.contains(it) }) return@forEachIndexed
                if (line.contains("tr(")) return@forEachIndexed
                val matches = literalRegex.findAll(line)
                for (m in matches) {
                    if (m.value in translatedViaIndirection) continue
                    offenders.add("${file.name}:${idx + 1}: ${m.value}")
                }
            }
        }

        assertTrue(
            "Found ${offenders.size} likely-unwrapped English literal(s) in Clinic screens " +
                "(wrap in tr(\"...\") and add an Arabic entry to Strings.kt):\n" +
                offenders.joinToString("\n"),
            offenders.isEmpty(),
        )
    }
}
