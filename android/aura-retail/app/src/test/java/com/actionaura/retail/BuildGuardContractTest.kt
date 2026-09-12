package com.actionaura.retail

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * The release-build guard in `app/build.gradle`.
 *
 * This project's build-time config is empty-by-default on purpose -- a blank
 * value is never a hidden fallback URL or a committed secret. The cost of that
 * choice is that a blank value is also INVISIBLE: the APK installs, launches,
 * looks entirely correct, and quietly does less than it claims. That already
 * shipped once (every rebuilt APK carried a dead AI assistant), and the same
 * shape had a strictly worse instance sitting next to it:
 * `OWNER_LICENSING_BASE_URL` blank means AppRoot skips the activation gate and
 * LicenseCheckInCoordinator returns before starting its loop, so the build
 * enforces NO licensing whatsoever. That is not a degraded build; it is the
 * product given away.
 *
 * Gradle cannot be executed from a JVM unit test, so these are source-content
 * guards -- the same shape and the same justification as
 * LicensingWiringContractTest and ReadinessContractTest. The guard's actual
 * runtime behaviour is verified by running
 * `./gradlew :app:assembleRelease --dry-run` with and without the properties.
 */
class BuildGuardContractTest {

    private val moduleRoot = File(".")

    private fun buildGradle(): String {
        val file = File(moduleRoot, "build.gradle")
        assumeTrue("app/build.gradle not reachable from this run context", file.exists())
        return file.readText()
    }

    private fun mainKotlinSources(): List<File> {
        val src = File(moduleRoot, "src/main/java")
        assumeTrue("src/main/java not reachable from this run context", src.exists())
        return src.walkTopDown().filter { it.isFile && it.extension == "kt" }.toList()
    }

    /** The `gradleProperty:` keys listed in build.gradle's releaseRequiredConfig. */
    private fun releaseRequiredProperties(): List<String> =
        Regex("""gradleProperty\s*:\s*'([A-Za-z0-9_]+)'""")
            .findAll(buildGradle())
            .map { it.groupValues[1] }
            .toList()

    @Test
    fun a_release_build_with_no_licensing_url_is_refused() {
        // The headline defect. An APK built without this enforces no licensing
        // at all -- no activation gate, no check-in, no revocation, ever.
        assertThat(releaseRequiredProperties()).contains("ownerLicensingBaseUrl")
    }

    @Test
    fun every_build_config_kotlin_treats_as_a_kill_switch_is_release_blocking() {
        // The anti-drift test, and the reason this file exists rather than one
        // more hardcoded assertion. Any BuildConfig field that Kotlin tests
        // with isBlank()/isNotBlank() is being used as an on/off switch for a
        // feature, which means blank silently disables that feature. Adding
        // such a field WITHOUT adding it to releaseRequiredConfig re-creates
        // the exact defect this guard exists for, one feature further along.
        val gradle = buildGradle()
        val required = releaseRequiredProperties()

        val killSwitchFields = mainKotlinSources()
            .flatMap { file ->
                Regex("""BuildConfig\.([A-Z0-9_]+)\.is(?:Not)?Blank\(\)""")
                    .findAll(file.readText())
                    .map { it.groupValues[1] }
            }
            .toSortedSet()

        // Guards the guard: if the regex ever stops matching anything, this
        // test would vacuously pass while proving nothing.
        assertThat(killSwitchFields).isNotEmpty()

        val unguarded = killSwitchFields.filter { field ->
            // buildConfigField "String", "OWNER_LICENSING_BASE_URL", asJavaStringLiteral(ownerLicensingBaseUrl)
            val declaration = Regex(
                """buildConfigField\s+"String",\s*"$field",\s*asJavaStringLiteral\(\s*([A-Za-z0-9_]+)\s*\)"""
            ).find(gradle)
            // A field with no resolveSecret-backed declaration cannot be
            // release-required, and is reported as unguarded rather than
            // silently skipped.
            declaration == null || declaration.groupValues[1] !in required
        }

        assertThat(unguarded).isEmpty()
    }

    @Test
    fun the_guard_fails_release_shaped_builds_and_only_warns_debug_ones() {
        val gradle = buildGradle()
        // Staging is release-SHAPED (non-debuggable, installed on real
        // devices) even though it is not the release build type, so it must be
        // covered too -- that is the build an internal tester actually holds.
        assertThat(gradle).contains("""^(assemble|bundle|install|package)(Release|Staging)${'$'}""")
        assertThat(gradle).contains("throw new GradleException(")
        // A developer not working on the feature must not be BLOCKED by it,
        // but must not be able to miss it either.
        assertThat(gradle).contains("""^(assemble|bundle|install|package)Debug${'$'}""")
        assertThat(gradle).contains("logger.warn(")
    }

    @Test
    fun the_guard_names_no_secret_value_anywhere() {
        // The failure text quotes KEYS and env var NAMES only. A guard that
        // echoed a resolved token into build output -- which lands in CI logs
        // -- would be a worse defect than the one it is preventing.
        val gradle = buildGradle()
        for (banned in listOf("\${aiBearerToken}", "\${whatsappAccessToken}", "\${ownerLicensingBaseUrl}",
                              "\${entry.value}", "\${it.value}")) {
            assertThat(gradle).doesNotContain(banned)
        }
    }
}
