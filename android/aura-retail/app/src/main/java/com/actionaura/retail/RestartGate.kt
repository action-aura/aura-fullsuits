package com.actionaura.retail

import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/**
 * What the app knows about whether anybody is using the till right now, kept as plain process-wide state so
 * the restart logic can read it without touching Compose or the Activity. Written by the UI
 * ([MainActivity] for foreground, the POS screen for the cart) and by the local HTTP client (in-flight calls).
 */
object TillActivity {
    private val startedActivities = AtomicInteger(0)
    @Volatile var cartLineCount: Int = 0

    /** Number of backend calls the UI has in flight right now (set by ApiClient). */
    @Volatile var inFlightRequests: () -> Int = { 0 }

    val isForeground: Boolean get() = startedActivities.get() > 0

    fun activityStarted() { startedActivities.incrementAndGet() }
    fun activityStopped() { startedActivities.updateAndGet { maxOf(0, it - 1) } }
}

/**
 * "Is it safe to restart the app NOW?": only when the app is in the FOREGROUND (Android 10+ blocks a
 * background app from starting an activity, so a background restart would kill the process and never bring
 * the app back) and the till has been IDLE -- empty cart, no backend call in flight -- for [idleMillis]
 * without a break. Any activity resets the timer, so a restart can never cut a sale in half.
 */
class RestartGate(
    private val isForeground: () -> Boolean,
    private val cartLineCount: () -> Int,
    private val inFlight: () -> Int,
    private val clock: () -> Long,
    private val idleMillis: Long = 60_000L,
) {
    private var idleSince: Long? = null

    @Synchronized
    fun safeNow(): Boolean {
        val busy = !isForeground() || cartLineCount() > 0 || inFlight() > 0
        if (busy) {
            idleSince = null
            return false
        }
        val since = idleSince ?: clock().also { idleSince = it }
        return clock() - since >= idleMillis
    }
}

/**
 * Schedules AT MOST ONE restart and performs it only when [gate] says it is safe, re-checking every
 * [recheckMillis]. If [performRestart] cannot (no launch intent for this package) it says so in [log], keeps
 * everything as it was (the staged copy is untouched, nothing is discarded) and allows a later request to
 * try again.
 */
class RestartCoordinator(
    private val gate: RestartGate,
    private val post: (delayMillis: Long, block: () -> Unit) -> Unit,
    private val performRestart: () -> Boolean,
    private val log: (String) -> Unit,
    private val recheckMillis: Long = 5_000L,
) {
    private val scheduled = AtomicBoolean(false)

    fun request() {
        if (!scheduled.compareAndSet(false, true)) return          // a restart is already waiting for its moment
        post(0L, ::tick)
    }

    internal fun tick() {
        if (!gate.safeNow()) {
            post(recheckMillis, ::tick)
            return
        }
        if (!performRestart()) {
            log("Restart for the staged shop copy was not possible (no launch intent); the copy stays staged and a later request will try again.")
            scheduled.set(false)
        }
    }
}
