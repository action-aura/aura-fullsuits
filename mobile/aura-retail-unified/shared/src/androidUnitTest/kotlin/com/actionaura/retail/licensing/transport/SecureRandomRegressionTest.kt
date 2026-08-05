package com.actionaura.retail.licensing.transport

import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import kotlin.test.fail

/**
 * M10.25 -- real, permanent regression coverage for the M9-disclosed
 * weak-RNG defect (`csprng-native-bridge-review.md`). Reads the real,
 * actual `ActivationViewModel.kt` source from disk at test time --
 * never a duplicated/copied string that could silently drift from the
 * real file -- mirroring the same real, source-level technique
 * `MainActivityWiringRegressionTest` (M6) established for a different
 * defect class. Real, deliberate placement in `androidUnitTest`, not
 * `commonTest` -- `java.io.File` is JVM-only and would break a real
 * iOS build if this lived in `commonTest`, the same reasoning that
 * placed `MainActivityWiringRegressionTest` here in M6.
 */
class SecureRandomRegressionTest {

    private fun readSourceFile(relativePath: String): String {
        var dir = java.io.File(".").absoluteFile
        repeat(8) {
            val candidate = java.io.File(dir, relativePath)
            if (candidate.exists()) return candidate.readText()
            dir = dir.parentFile ?: return@repeat
        }
        fail("could not locate $relativePath from test working directory ${java.io.File(".").absolutePath} -- this test must read the REAL source file, never a duplicated copy")
    }

    /** Real, live-code-only invocation pattern -- deliberately does not match the historical explanation prose ("previously `kotlin.random.Random`-based...") both real doc comments legitimately carry, only a real call site (`Random.nextInt(`/`Random.nextBytes(`/`Random.Default` used as a value). */
    private val liveKotlinRandomInvocation = Regex("""\bRandom\.(nextInt\(|nextBytes\(|nextLong\(|Default\b)""")

    /** Strips real `/** ... */` KDoc blocks and `//` line comments before searching -- both real, legitimate doc comments in this codebase discuss the historical kotlin.random.Random defect by name; only LIVE code must be checked. */
    private fun stripComments(source: String): String {
        val noBlockComments = source.replace(Regex("""/\*.*?\*/""", RegexOption.DOT_MATCHES_ALL), "")
        return noBlockComments.lineSequence().joinToString("\n") { line -> line.substringBefore("//") }
    }

    @Test
    fun activationViewModelNeverUsesKotlinRandomForSecurityIdentifiers() {
        val source = stripComments(readSourceFile("shared/src/commonMain/kotlin/com/actionaura/retail/ui/activation/ActivationViewModel.kt"))
        assertFalse(
            liveKotlinRandomInvocation.containsMatchIn(source),
            "real regression: ActivationViewModel.kt must never generate a security-relevant identifier (idempotency key or otherwise) using kotlin.random.Random -- this exact omission was the real M9 weak-cryptographic-primitive defect a security review caught",
        )
        assertTrue(
            source.contains("secureRandomHex") || source.contains("secureRandomBytes"),
            "real regression: ActivationViewModel.kt must generate its idempotency key via the real platform CSPRNG bridge (secureRandomHex/secureRandomBytes), not silently regress to a different mechanism",
        )
    }

    @Test
    fun secureRandomBytesSourceFileNeverFallsBackToKotlinRandom() {
        val raw = readSourceFile("shared/src/commonMain/kotlin/com/actionaura/retail/licensing/transport/SecureRandomBytes.kt")
        assertTrue(raw.contains("expect fun secureRandomBytes"), "real regression: the commonMain expect declaration must still require a real platform actual -- no default/fallback body may ever be added here")
        assertFalse(liveKotlinRandomInvocation.containsMatchIn(stripComments(raw)), "real regression: the commonMain CSPRNG contract file must never itself invoke kotlin.random.Random as a live fallback")
    }

    @Test
    fun androidSecureRandomActualUsesRealJavaSecuritySecureRandom() {
        val raw = readSourceFile("shared/src/androidMain/kotlin/com/actionaura/retail/licensing/transport/SecureRandomBytes.android.kt")
        assertTrue(raw.contains("java.security.SecureRandom"), "real regression: the Android actual must use the real platform CSPRNG, java.security.SecureRandom")
        assertFalse(liveKotlinRandomInvocation.containsMatchIn(stripComments(raw)), "real regression: the Android actual must never fall back to kotlin.random.Random")
    }
}
