package com.actionaura.retail.licensing

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * The awaiting-approval poll's decision table.
 *
 * A PENDING activation can only be resolved by re-POSTing /activate (never by
 * check-in: routes.py short-circuits check-in with a canned
 * ACTIVATION_REQUIRED for exactly the `state_repository.load() is None` case
 * a PENDING device is in). So every tick of the poll gets back a raw
 * activate() result, and the ONLY thing standing between the user and a
 * wrong screen is how that result is classified.
 *
 * Three of the five outcomes look identical on the wire -- routes.py's
 * /_internal/sync-activation turns every failure into the same
 * `{reason_code}` body whether the code came from Owner or from our own
 * verify_assertion() -- which is why the classification is tested here rather
 * than trusted to read correctly inline in a @Composable.
 */
class ActivationPollTest {

    @Test
    fun a_verified_activation_is_approved() {
        val outcome = classifyActivationResult(
            mapOf("result" to "SUCCESS", "state" to "ACTIVE_ONLINE", "installation_id" to "inst-1")
        )
        assertThat(outcome).isEqualTo(ActivationOutcome.Approved)
    }

    @Test
    fun owner_still_holding_it_keeps_the_screen_waiting() {
        val outcome = classifyActivationResult(
            mapOf("result" to "PENDING", "reason_code" to "ACTIVATION_PENDING_REVIEW", "installation_id" to "inst-1")
        )
        assertThat(outcome).isEqualTo(ActivationOutcome.StillPending("inst-1"))
    }

    @Test
    fun a_network_failure_is_not_a_verdict() {
        // Dropping the pending marker on a DNS blip would strand the user
        // back on a key form for a submission Owner is still perfectly
        // willing to approve -- the same re-ask loop from the other side.
        for (code in listOf("NETWORK_UNAVAILABLE", "REQUEST_TIMED_OUT", "RATE_LIMITED",
                            "SERVICE_TEMPORARILY_UNAVAILABLE", "TLS_VERIFICATION_FAILED")) {
            val outcome = classifyActivationResult(mapOf("reason_code" to code, "_local_error" to true))
            assertThat(outcome).isEqualTo(ActivationOutcome.Transient(code))
        }
    }

    @Test
    fun owner_approved_but_unverifiable_locally_is_not_a_verdict_either() {
        // The live-droplet failure this whole bucket exists for: Owner rotated
        // its signing key, this install still ships a stale trust_anchor.json,
        // so Owner APPROVED, consumed a paid device slot, and the assertion
        // failed verify_assertion() here. Treating that as a rejection
        // destroys the marker and tells the customer to double-check a key
        // that was never the problem.
        val outcome = classifyActivationResult(mapOf("reason_code" to "UNKNOWN_SIGNING_KEY"))
        assertThat(outcome).isEqualTo(ActivationOutcome.LocalVerificationFailed("UNKNOWN_SIGNING_KEY"))

        val expired = classifyActivationResult(mapOf("reason_code" to "ASSERTION_EXPIRED"))
        assertThat(expired).isEqualTo(ActivationOutcome.LocalVerificationFailed("ASSERTION_EXPIRED"))
    }

    @Test
    fun a_real_owner_verdict_stops_the_poll() {
        val outcome = classifyActivationResult(mapOf("reason_code" to "INSTALLATION_DEACTIVATED"))
        assertThat(outcome).isEqualTo(ActivationOutcome.Declined("INSTALLATION_DEACTIVATED"))
    }

    @Test
    fun a_failure_with_no_reason_code_is_still_a_verdict_not_a_silent_success() {
        val outcome = classifyActivationResult(mapOf("detail" to "something went wrong"))
        assertThat(outcome).isEqualTo(ActivationOutcome.Declined("ACTIVATION_REJECTED"))
    }
}
