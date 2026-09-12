package com.actionaura.retail.ui

/** What a device that still has no admin account should see next. */
internal enum class FirstRun { NONE, SETUP, JOIN_CHOICE }

/**
 * The one place that tells a brand-new install apart from a device that has
 * already joined a licensed shop -- pure, so it can be unit-tested with the
 * real payload shapes and so AppRoot's two callers (cold boot and the
 * post-activation callback) cannot drift apart.
 *
 * The honest signal is a DATA fact, not a state list: status_presenter.py
 * exposes `installation_id` only once Owner has issued one, so a build with
 * no licensing configured, a fresh install, or a failed status call can never
 * satisfy it. The pre-activation states are then removed so a device still
 * pending Owner approval (ACTIVATING carries an installation_id too) keeps
 * the ordinary flow. Mirrors `_isJoinedDevice()` in the desktop shell.
 */
internal object FirstRunDecision {
    fun decide(
        needsSetup: Boolean,
        licensingStatus: Map<String, Any?>?,
        needsActivationStates: Set<String>,
    ): FirstRun {
        if (!needsSetup) return FirstRun.NONE
        val installationId = licensingStatus?.get("installation_id") as? String
        val state = licensingStatus?.get("current_state") as? String
        val joined = !installationId.isNullOrBlank() && state !in needsActivationStates
        return if (joined) FirstRun.JOIN_CHOICE else FirstRun.SETUP
    }
}
