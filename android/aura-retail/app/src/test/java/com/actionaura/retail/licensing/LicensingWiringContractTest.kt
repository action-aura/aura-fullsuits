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
        assertThat(screen).contains("ActivationPollSchedule.intervalMs(")
        assertThat(screen).contains("classifyActivationResult(")
    }

    @Test
    fun the_last_checked_time_is_set_after_classification_not_before() {
        // It used to be assigned unconditionally, immediately after the
        // activate() call and BEFORE the outcome was classified. So a tick
        // that never left the device still advanced the on-screen "Still
        // waiting for approval. Last checked at HH:MM" -- telling the user
        // their licence had just been verified when nothing had been verified
        // since they last had signal.
        val screen = source("src/main/java/com/actionaura/retail/ui/screens/LicensingScreen.kt")
        val assignment = Regex("""lastCheckedLabel\s*=\s*nowTimeLabel\(\)""").find(screen)
        assertThat(assignment).isNotNull()
        val classification = screen.indexOf("classifyActivationResult(")
        assertThat(classification).isGreaterThan(-1)
        assertThat(assignment!!.range.first).isGreaterThan(classification)
        // ...and it is guarded, not merely late.
        assertThat(screen).contains("if (outcome.reachedOwner) lastCheckedLabel = nowTimeLabel()")
    }

    @Test
    fun the_polls_concurrency_guard_both_reads_and_sets_the_busy_flag() {
        // The guard was one-directional: it READ `busy` (so a tick could not
        // start during a button press) but never SET it, so a press landing
        // mid-tick produced two overlapping /activate POSTs for the same held
        // key, racing to write the same marker and message state.
        val screen = source("src/main/java/com/actionaura/retail/ui/screens/LicensingScreen.kt")
        val pollBody = screen.substringAfter("LaunchedEffect(pendingKey, pollAttempt)")
        assertThat(pollBody).contains("if (busy) continue")
        assertThat(pollBody).contains("busy = true")
        // A `busy` left true by a cancelled tick would disable Check Now for
        // the rest of the session, so the reset must be in a finally.
        assertThat(pollBody).contains("finally")
    }

    @Test
    fun the_poll_backs_off_and_eventually_stops_claiming_to_check() {
        // No backoff and no cap meant a device with no connectivity re-POSTed
        // /activate every 30s forever, while the screen kept promising "we
        // keep checking automatically" -- the same false promise the awaiting
        // screen exists to remove.
        val screen = source("src/main/java/com/actionaura/retail/ui/screens/LicensingScreen.kt")
        assertThat(screen).contains("ActivationPollSchedule.shouldKeepPolling(")
        assertThat(screen).contains("pollExhausted = true")
        // And the screen has to actually SAY so when it gives up.
        assertThat(screen).contains("Your key is still held for approval")
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
