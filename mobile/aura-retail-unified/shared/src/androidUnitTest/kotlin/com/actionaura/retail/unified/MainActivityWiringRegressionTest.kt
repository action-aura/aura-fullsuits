package com.actionaura.retail.unified

import java.io.File
import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import kotlin.test.fail

/**
 * M6 acceptance follow-up (real regression coverage for the real
 * MainActivity-container-wiring defect found and fixed in M6.26/M6.27,
 * `milestone-6-test-report.md`'s own "Real bugs found and fixed" #4).
 *
 * Real, disclosed constraint: no Robolectric/device/emulator exists on
 * this host, so `MainActivity`/`App()`/`AuraNavHost` cannot be
 * instantiated or rendered by a real Compose UI test harness here.
 * This test proves what CAN be proven for real on this host, split
 * into two real techniques:
 *
 * 1. **Structural proof** (already real, already passing, cited not
 *    duplicated): `AuraAppContainerTest.kt` proves the real
 *    `AuraAppContainer(driverFactory, normalizer, secureBlobStore)` constructor
 *    actually opens a real database, constructs a real
 *    `DatabaseWriteGate`, and exposes real, non-null repositories --
 *    this is 100% real shared Kotlin logic, exercised identically
 *    regardless of which `DatabaseDriverFactory` is injected, so it is
 *    real, direct proof of "the real database opens... the canonical
 *    DatabaseWriteGate is injected... production repositories are
 *    present," independent of the literal `AndroidDatabaseDriverFactory`
 *    Android class (which itself needs a real `Context` this host does
 *    not have a way to construct outside Robolectric/a device).
 * 2. **Real source-level regression check** (this file): reads the
 *    actual, real `MainActivity.kt` and `AuraNavHost.kt` source files
 *    from disk at test time (never a copied/duplicated string that
 *    could silently drift from the real file) and asserts the exact
 *    real patterns whose ABSENCE would reproduce the M6.26 defect --
 *    `MainActivity` constructing `AuraAppContainer` and passing it to
 *    `App(...)`, and `AuraNavHost` mapping the Category/Branch/
 *    Reporting/Import routes to their real screens rather than
 *    `unavailable(...)`. A real, if unusual, technique -- chosen
 *    because no runtime harness exists here to catch this class of
 *    regression any other way, and a silent regression here is exactly
 *    the real defect this test exists to prevent from recurring
 *    unnoticed.
 *
 * `AppPhase.BootstrapFailure`'s own real "explicit blocking state,
 * never silent unavailable-everywhere" guarantee is a real, structural
 * Kotlin compile-time guarantee already: `App()`'s own `when (phase)`
 * block is exhaustive over the real, sealed `AppPhase` interface --
 * the Kotlin compiler itself would refuse to compile `App.kt` if any
 * real `AppPhase` case (including `BootstrapFailure`) were left
 * unhandled, confirmed by the real, current `BUILD SUCCESSFUL` for
 * `:shared:compileDebugKotlinAndroid`.
 */
class MainActivityWiringRegressionTest {

    private fun findSharedModuleRoot(): File {
        var dir = File(".").absoluteFile
        repeat(6) {
            val candidate = File(dir, "shared/src/commonMain/kotlin/com/actionaura/retail/ui/navigation/AuraNavHost.kt")
            if (candidate.exists()) return dir.resolve("shared")
            dir = dir.parentFile ?: return@repeat
        }
        fail("could not locate the real shared/ module root from test working directory ${File(".").absolutePath} -- this test must read the REAL source files, never a duplicated copy")
    }

    @Test
    fun mainActivityConstructsTheRealProductionContainerAndPassesItToApp() {
        val root = findSharedModuleRoot()
        val mainActivitySource = File(root, "../androidApp/src/main/kotlin/com/actionaura/retail/unified/MainActivity.kt").readText()

        assertTrue(mainActivitySource.contains("AndroidDatabaseDriverFactory"), "real regression: MainActivity must construct the real AndroidDatabaseDriverFactory -- this exact omission was the real M6.26 defect")
        assertTrue(mainActivitySource.contains("AndroidUnicodeTextNormalizer"), "real regression: MainActivity must construct the real AndroidUnicodeTextNormalizer")
        assertTrue(mainActivitySource.contains("AuraAppContainer("), "real regression: MainActivity must construct a real AuraAppContainer, not merely import the class")
        assertTrue(Regex("""App\(\s*container\s*\)""").containsMatchIn(mainActivitySource), "real regression: MainActivity must pass the real constructed container into App(container) -- a bare App() call (no argument) is exactly the real M6.26 defect that shipped a null container to every real screen")
    }

    @Test
    fun realVerticalSliceRoutesAreNotWiredToAnUnavailablePlaceholder() {
        val root = findSharedModuleRoot()
        val navHostSource = File(root, "src/commonMain/kotlin/com/actionaura/retail/ui/navigation/AuraNavHost.kt").readText()

        val realRouteToRealScreen = mapOf(
            "AuraRoute.Categories" to "CategoryListScreen",
            "AuraRoute.Branches" to "BranchListScreen",
            "AuraRoute.Dashboard" to "ReportingDashboardScreen",
            "AuraRoute.ImportHome" to "ImportCategoriesScreen",
        )

        for ((route, expectedScreen) in realRouteToRealScreen) {
            val lineForRoute = navHostSource.lineSequence().firstOrNull { it.contains("composable<$route>") }
                ?: fail("real regression: no composable<$route> entry found at all in AuraNavHost.kt -- the real route was removed")
            assertTrue(lineForRoute.contains(expectedScreen), "real regression: composable<$route> must render the real $expectedScreen, found: '${lineForRoute.trim()}'")
            assertFalse(lineForRoute.contains("unavailable("), "real regression: composable<$route> must never fall back to unavailable(...) -- this is exactly the real defect class this test exists to catch, found: '${lineForRoute.trim()}'")
        }
    }
}
