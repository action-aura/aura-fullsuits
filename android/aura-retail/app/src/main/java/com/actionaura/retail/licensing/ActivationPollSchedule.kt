package com.actionaura.retail.licensing

/**
 * When the awaiting-approval poll fires next, and when it stops firing at all.
 *
 * The poll started life as a flat `delay(30s)` forever, with no backoff and no
 * cap. Two things are wrong with that, and only one of them is about traffic:
 *
 *  1. Every tick is a full re-POST of `/activate` -- an Owner round trip with
 *     signature verification and DB writes on their side. A device parked on
 *     this screen with no connectivity re-attempts 2,880 times a day, forever,
 *     and Owner's own answer for the held-activation branch literally says
 *     `retry_guidance: "safe_to_retry_with_backoff"`
 *     (commercial_runtime/licensing_contracts/activation.py). The client was
 *     ignoring guidance the protocol goes out of its way to state.
 *  2. An unbounded silent retry is its own dishonesty. A screen that says "we
 *     keep checking automatically" while every check has failed for an hour is
 *     making the same promise this screen was built to stop making. The cap
 *     exists so the screen can eventually say something TRUE instead.
 *
 * Backoff applies only to ticks that never reached the service. When Owner
 * answers -- including when it answers "still pending" -- the cadence stays at
 * the base interval, because that 30s is a real cross-platform contract shared
 * with `products/retail/frontend/licensing.js`'s AWAITING_POLL_MS and pinned by
 * PendingActivationTest. Slowing down while Owner is answering fine would be
 * inventing a divergence, not fixing one.
 */
object ActivationPollSchedule {

    /** Cadence while the service is answering. Same value, same source of truth. */
    const val BASE_INTERVAL_MS: Long = PendingActivation.POLL_INTERVAL_MS

    /**
     * Ceiling for a backed-off interval. Fifteen minutes is long enough to stop
     * hammering an unreachable service and short enough that a device which
     * regains connectivity notices within one coffee break.
     */
    const val MAX_INTERVAL_MS: Long = 15L * 60 * 1000

    /**
     * Consecutive ticks that failed to reach the service before the automatic
     * poll gives up and hands control back to the user.
     *
     * With the schedule below that is 30s + 1m + 2m + 4m + 8m + 15m + 15m + 15m
     * ~= one hour of genuinely trying before the screen stops claiming to be
     * checking. Reaching the service at any point resets the count to zero.
     */
    const val MAX_CONSECUTIVE_UNREACHED: Int = 8

    /**
     * Delay before the next tick, given how many consecutive ticks have failed
     * to reach the service (0 = the last tick got an answer, or none has run).
     *
     * Doubling, capped. The exponent is clamped before shifting -- `1L shl 64`
     * is not 0 in Kotlin, it wraps around to 1, which would silently turn a
     * long outage into a tight retry loop. Same guard, for the same reason, as
     * SyncCoordinator.nextDelayMillis().
     */
    fun intervalMs(consecutiveUnreached: Int): Long {
        if (consecutiveUnreached <= 0) return BASE_INTERVAL_MS
        val exponent = minOf(consecutiveUnreached, 32)
        val doubled = BASE_INTERVAL_MS shl exponent
        // shl can overflow into a negative before the cap is ever compared.
        if (doubled <= 0) return MAX_INTERVAL_MS
        return minOf(doubled, MAX_INTERVAL_MS)
    }

    /** False once the poll has exhausted its attempts and must say so. */
    fun shouldKeepPolling(consecutiveUnreached: Int): Boolean =
        consecutiveUnreached < MAX_CONSECUTIVE_UNREACHED
}
