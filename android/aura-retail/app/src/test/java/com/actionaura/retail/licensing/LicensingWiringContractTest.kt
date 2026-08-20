package com.actionaura.retail.licensing

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Source-content guards for the wiring that has no unit-testable seam --
 * "is this timer actually started", "is this exception actually surfaced".
 * Same shape (and same justification) as ReadinessContractTest's main.py
 * guard: no Chaquopy, no device, and no Compose test runner in this
 * environment, but these are precisely the regressions that are invisible
 * until a real customer hits them.
 */
class LicensingWiringContractTest {

    private val moduleRoot = File(".")
    private val suiteRoot = File("../../..")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    @Test
    fun android_checks_in_on_the_same_cadence_as_the_desktop_shell() {
        val shell = File(suiteRoot, "products/retail/frontend/app-shell.js")
        assumeTrue("app-shell.js not reachable from this run context", shell.exists())
        val match = Regex("LICENSE_CHECKIN_POLL_MS:\\s*([0-9 *]+),").find(shell.readText())
        assertThat(match).isNotNull()
        val desktopMillis = match!!.groupValues[1].split("*").map { it.trim().toLong() }.reduce(Long::times)
        assertThat(LicenseCheckInCoordinator.INTERVAL_MS).isEqualTo(desktopMillis)
    }

    @Test
    fun the_background_check_in_loop_is_actually_started_at_boot() {
        // Without this line, revocation and suspension never land mid-use and
        // the offline grace/warning progression never advances -- a single
        // manual Check Now weeks later drops the user straight to RESTRICTED
        // with no warning phase.
        assertThat(source("src/main/java/com/actionaura/retail/ui/AppRoot.kt"))
            .contains("LicenseCheckInCoordinator.start(")
    }

    @Test
    fun the_awaiting_approval_screen_really_polls() {
        // The screen told a PENDING user "We'll keep checking automatically --
        // no action needed right now" while nothing on Android polled anything.
        // Either the promise is kept or the sentence goes; this pins the
        // former.
        val screen = source("src/main/java/com/actionaura/retail/ui/screens/LicensingScreen.kt")
        assertThat(screen).contains("PendingActivation.POLL_INTERVAL_MS")
        assertThat(screen).contains("classifyActivationResult(")
    }

    @Test
    fun the_activation_screen_offers_a_way_out_of_the_pending_state() {
        // A customer who mistyped the key, or was issued one Owner will never
        // approve, must be able to get back to the form under their own power.
        assertThat(source("src/main/java/com/actionaura/retail/ui/screens/LicensingScreen.kt"))
            .contains("Use a different key")
    }

    @Test
    fun server_readiness_result_is_not_discarded() {
        // wait_until_ready() returns a Boolean and returns false on timeout.
        // Throwing it away meant a backend that never came up looked exactly
        // like one that did.
        val bootstrap = source("src/main/java/com/actionaura/retail/server/ServerBootstrap.kt")
        assertThat(bootstrap).contains("wait_until_ready")
        assertThat(Regex("""val\s+\w+\s*=\s*main\.callAttr\(\s*"wait_until_ready"""").containsMatchIn(bootstrap))
            .isTrue()
    }

    @Test
    fun a_backend_startup_failure_is_not_silently_turned_into_a_login_screen() {
        // AppRoot used to swallow ANY Chaquopy/bootstrap exception into
        // Phase.LOGIN, stranding the user on a login screen that can never
        // succeed, with zero diagnostics anywhere.
        val appRoot = source("src/main/java/com/actionaura/retail/ui/AppRoot.kt")
        assertThat(Regex("""catch\s*\(\s*\w+\s*:\s*Exception\s*\)\s*\{\s*Phase\.LOGIN\s*\}""")
            .containsMatchIn(appRoot)).isFalse()
        assertThat(appRoot).contains("Phase.ERROR")
    }
}
