package com.actionaura.clinic

import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Phase 4I regression guard: as of this phase, Clinic's Kotlin source
 * contains zero android.util.Log calls, zero println/System.out calls, and
 * no HttpLoggingInterceptor (which would otherwise log request/response
 * bodies -- potentially patient names, phone numbers, diagnoses,
 * prescriptions -- to Logcat). This test fails loudly if any of those are
 * ever reintroduced, rather than relying on a one-time manual audit staying
 * true forever. See docs/android/phase4/clinic-android-privacy-boundary.md
 * for the full audit this guard is derived from.
 */
class PrivacyLoggingGuardTest {

    private fun kotlinSourceFiles(root: File): List<File> =
        if (!root.exists()) emptyList()
        else root.walkTopDown().filter { it.isFile && it.extension == "kt" }.toList()

    @Test
    fun no_android_log_calls_in_kotlin_source() {
        val root = File("src/main/java")
        assumeTrue("source root not found in this run context", root.exists())
        val files = kotlinSourceFiles(root)
        assumeTrue("no source files found", files.isNotEmpty())
        val offenders = files.filter { it.readText().contains(Regex("""\bLog\.(d|v|i|w|e)\(""")) }
        assertTrue("android.util.Log calls found in: ${offenders.map { it.path }}", offenders.isEmpty())
    }

    @Test
    fun no_println_or_system_out_in_kotlin_source() {
        val root = File("src/main/java")
        assumeTrue("source root not found in this run context", root.exists())
        val files = kotlinSourceFiles(root)
        assumeTrue("no source files found", files.isNotEmpty())
        val offenders = files.filter {
            val t = it.readText()
            t.contains("println(") || t.contains("System.out")
        }
        assertTrue("println/System.out found in: ${offenders.map { it.path }}", offenders.isEmpty())
    }

    @Test
    fun no_http_body_logging_interceptor_configured() {
        val apiClient = File("src/main/java/com/actionaura/clinic/net/ApiClient.kt")
        assumeTrue("ApiClient.kt not found in this run context", apiClient.exists())
        val text = apiClient.readText()
        assertTrue(
            "HttpLoggingInterceptor must not be wired into the OkHttpClient (would log request/response bodies, including patient data, to Logcat)",
            !text.contains("HttpLoggingInterceptor"),
        )
    }
}
