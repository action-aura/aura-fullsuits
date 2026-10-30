package com.actionaura.retail.licensing

/**
 * What the Python licence status says about a 7-day trial (new customer flow, owner 2026-10-30).
 *
 * The decision "this till is locked" is NEVER made here: it is the TRIAL_ENDED state Python computes
 * from the signed lease and trusted time (policy_evaluator.py) and the capability guard enforces on
 * every sale. This only reads the display block `status["trial"]` that status_presenter.py adds, so
 * the phone can say "N days left" and show the lock screen without a second source of truth.
 *
 * Pure (no Context, no Compose) so a plain JVM test can pin it.
 */
data class TrialStatus(val isTrial: Boolean, val ended: Boolean, val daysLeft: Int?) {
    /** True only while a trial is running and has at least a day left -- the "Trial: N days left" bar. */
    val isRunning: Boolean get() = isTrial && !ended && daysLeft != null && daysLeft > 0

    companion object {
        /** Null when the status carries no trial block (older backend, or not activated yet). */
        fun from(status: Map<String, Any?>): TrialStatus? {
            val block = status["trial"] as? Map<*, *> ?: return null
            val days = (block["days_left"] as? Number)?.toInt()
            return TrialStatus(
                isTrial = block["is_trial"] == true,
                ended = block["ended"] == true,
                daysLeft = days,
            )
        }
    }
}
