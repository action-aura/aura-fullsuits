package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * Pins [FirstRunDecision.decide] against the real payload shapes
 * status_presenter.py can actually return, per its own doc comment. This is
 * the one function AppRoot's boot path and its post-activation callback both
 * go through, so a mistake here is a mistake both callers make identically.
 */
class FirstRunDecisionTest {

    private val NEEDS = setOf("NOT_CONFIGURED", "ACTIVATION_REQUIRED", "ACTIVATING")

    @Test
    fun no_setup_needed_means_none_regardless_of_status() {
        val status = mapOf("current_state" to "ACTIVE_ONLINE", "installation_id" to "inst-1")
        assertThat(FirstRunDecision.decide(false, status, NEEDS)).isEqualTo(FirstRun.NONE)
    }

    @Test
    fun needs_setup_with_a_null_status_is_setup() {
        // A failed status call, or licensing not configured at all -- AppRoot
        // passes null for both.
        assertThat(FirstRunDecision.decide(true, null, NEEDS)).isEqualTo(FirstRun.SETUP)
    }

    @Test
    fun needs_setup_with_not_configured_is_setup() {
        val status = mapOf(
            "current_state" to "NOT_CONFIGURED",
            "detail" to "Owner licensing URL is not configured.",
        )
        assertThat(FirstRunDecision.decide(true, status, NEEDS)).isEqualTo(FirstRun.SETUP)
    }

    @Test
    fun needs_setup_with_activation_required_is_setup() {
        val status = mapOf("current_state" to "ACTIVATION_REQUIRED")
        assertThat(FirstRunDecision.decide(true, status, NEEDS)).isEqualTo(FirstRun.SETUP)
    }

    @Test
    fun needs_setup_with_activating_and_an_installation_id_is_still_setup() {
        // Pending approval keeps the ordinary flow even though an
        // installation id already exists -- the id alone is not enough,
        // it must also be outside the pre-activation states.
        val status = mapOf("current_state" to "ACTIVATING", "installation_id" to "inst-1")
        assertThat(FirstRunDecision.decide(true, status, NEEDS)).isEqualTo(FirstRun.SETUP)
    }

    @Test
    fun needs_setup_with_active_online_and_an_installation_id_is_join_choice() {
        val status = mapOf("current_state" to "ACTIVE_ONLINE", "installation_id" to "inst-1")
        assertThat(FirstRunDecision.decide(true, status, NEEDS)).isEqualTo(FirstRun.JOIN_CHOICE)
    }

    @Test
    fun needs_setup_with_active_offline_and_an_installation_id_is_join_choice() {
        val status = mapOf("current_state" to "ACTIVE_OFFLINE", "installation_id" to "inst-1")
        assertThat(FirstRunDecision.decide(true, status, NEEDS)).isEqualTo(FirstRun.JOIN_CHOICE)
    }

    @Test
    fun needs_setup_with_active_online_but_no_installation_id_is_setup() {
        // The id is the signal, not the state name.
        val status = mapOf("current_state" to "ACTIVE_ONLINE")
        assertThat(FirstRunDecision.decide(true, status, NEEDS)).isEqualTo(FirstRun.SETUP)
    }

    @Test
    fun an_unlisted_state_with_no_installation_id_never_admits_a_device() {
        // Pins that an unlisted/made-up state alone -- with no installation
        // id -- can never resolve to JOIN_CHOICE.
        val status = mapOf("current_state" to "ACTIVE")
        assertThat(FirstRunDecision.decide(true, status, NEEDS)).isEqualTo(FirstRun.SETUP)
    }
}
