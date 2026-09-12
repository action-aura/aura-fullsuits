package com.actionaura.retail.licensing

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * The awaiting-approval poll's backoff and attempt cap, and the one predicate
 * that decides whether a tick counts as having reached the licensing service.
 *
 * The poll shipped as a flat `delay(30s)` forever: no backoff, despite Owner's
 * own held-activation answer carrying `retry_guidance:
 * "safe_to_retry_with_backoff"`, and no cap, so a device with no connectivity
 * re-POSTed /activate 2,880 times a day while the screen went on promising
 * "we keep checking automatically". Both halves are decisions, so both live
 * outside the @Composable where they can be exercised.
 */
class ActivationPollScheduleTest {

    @Test
    fun a_reachable_service_keeps_the_shared_cross_platform_cadence() {
        // 30s is a real contract with products/retail/frontend/licensing.js's
        // AWAITING_POLL_MS. Backing off while Owner is answering fine would
        // invent a divergence rather than fix one.
        assertThat(ActivationPollSchedule.intervalMs(0)).isEqualTo(PendingActivation.POLL_INTERVAL_MS)
        assertThat(ActivationPollSchedule.BASE_INTERVAL_MS).isEqualTo(PendingActivation.POLL_INTERVAL_MS)
    }

    @Test
    fun consecutive_unreachable_ticks_back_off_by_doubling() {
        val base = ActivationPollSchedule.BASE_INTERVAL_MS
        assertThat(ActivationPollSchedule.intervalMs(1)).isEqualTo(base * 2)
        assertThat(ActivationPollSchedule.intervalMs(2)).isEqualTo(base * 4)
        assertThat(ActivationPollSchedule.intervalMs(3)).isEqualTo(base * 8)
    }

    @Test
    fun the_backed_off_interval_is_capped_and_never_wraps_negative() {
        // `1L shl 64` wraps to 1 in Kotlin rather than saturating, so an
        // unclamped shift would silently turn a long outage into a tight
        // retry loop -- the opposite of backing off.
        for (streak in 0..200) {
            val interval = ActivationPollSchedule.intervalMs(streak)
            assertThat(interval).isAtLeast(ActivationPollSchedule.BASE_INTERVAL_MS)
            assertThat(interval).isAtMost(ActivationPollSchedule.MAX_INTERVAL_MS)
        }
        assertThat(ActivationPollSchedule.intervalMs(Int.MAX_VALUE))
            .isEqualTo(ActivationPollSchedule.MAX_INTERVAL_MS)
    }

    @Test
    fun the_poll_gives_up_after_a_bounded_number_of_unreached_attempts() {
        assertThat(ActivationPollSchedule.shouldKeepPolling(0)).isTrue()
        assertThat(ActivationPollSchedule.shouldKeepPolling(ActivationPollSchedule.MAX_CONSECUTIVE_UNREACHED - 1))
            .isTrue()
        assertThat(ActivationPollSchedule.shouldKeepPolling(ActivationPollSchedule.MAX_CONSECUTIVE_UNREACHED))
            .isFalse()
    }

    @Test
    fun giving_up_takes_long_enough_to_be_a_real_attempt() {
        // A cap that fires in ninety seconds is not a cap, it is a broken
        // poll. The screen only stops claiming to check after a genuine,
        // sustained failure to reach anything.
        val totalMs = (0 until ActivationPollSchedule.MAX_CONSECUTIVE_UNREACHED)
            .sumOf { ActivationPollSchedule.intervalMs(it) }
        assertThat(totalMs).isAtLeast(30L * 60 * 1000)
    }

    @Test
    fun only_a_transient_outcome_counts_as_never_having_reached_the_service() {
        // The one that matters: LocalVerificationFailed DID reach Owner --
        // Owner said yes and this device could not verify it -- so it must
        // not be counted as an unreachable tick, and it must be allowed to
        // advance the on-screen "last checked" time.
        assertThat(ActivationOutcome.Transient("NETWORK_UNAVAILABLE").reachedOwner).isFalse()
        assertThat(ActivationOutcome.Approved.reachedOwner).isTrue()
        assertThat(ActivationOutcome.StillPending("inst-1").reachedOwner).isTrue()
        assertThat(ActivationOutcome.LocalVerificationFailed("UNKNOWN_SIGNING_KEY").reachedOwner).isTrue()
        assertThat(ActivationOutcome.Declined("ACTIVATION_REJECTED").reachedOwner).isTrue()
    }

    @Test
    fun every_transient_reason_code_is_treated_as_not_reaching_the_service() {
        // Anchored to the real transport-failure set rather than a sample, so
        // adding a code to LicensingMessages cannot quietly start advancing
        // "last checked" on a tick that never left the device.
        for (code in LicensingMessages.TRANSIENT_REASON_CODES) {
            val outcome = classifyActivationResult(mapOf("reason_code" to code))
            assertThat(outcome.reachedOwner).isFalse()
        }
    }
}
