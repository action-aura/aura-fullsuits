package com.actionaura.retail.licensing

import android.content.Context
import android.util.Log
import com.actionaura.retail.BuildConfig
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Periodic licence check-in for Android -- the missing half of the licensing
 * lifecycle on this platform.
 *
 * Before this existed, `LicensingCoordinator.checkIn()` was reachable from
 * exactly one place: the manual "Check Now" button on LicensingScreen. That
 * meant nothing on Android ever contacted Owner after activation. Two things
 * followed, and both are user-visible:
 *
 *  1. A licence Owner SUSPENDS or REVOKES mid-use never lands. Every mutation
 *     route's `require_license_capability()` reads the LOCALLY persisted
 *     state (GET /api/licensing/status never contacts Owner -- it is a pure
 *     local read), so the device keeps behaving as ACTIVE until someone
 *     happens to press a button.
 *  2. Worse in practice: the offline grace/warning progression never
 *     advances. `evaluate()` only runs when something calls into the
 *     licensing layer, so WARNING ("check-in needed soon") and GRACE_PERIOD
 *     -- the states that exist precisely to warn a user BEFORE they lose
 *     features -- were dead code on Android. Weeks later a single manual
 *     Check Now would re-evaluate all of that elapsed time at once and drop
 *     the user straight to RESTRICTED, with no warning phase at all.
 *
 * This is the direct counterpart of the desktop shell's own fix, and
 * deliberately uses the same cadence: `app-shell.js`'s
 * `LICENSE_CHECKIN_POLL_MS = 5 * 60 * 1000` and `_pollLicenseCheckIn()`.
 * Five minutes, not sync's ten seconds, because unlike sync's cheap local
 * health read this is a real network call that does crypto verification and
 * DB writes on Owner's side too.
 *
 * It calls [LicensingCoordinator.checkIn] and NOT [LicensingCoordinator.status]
 * on purpose. `status()` is a local read that can never observe a revocation,
 * and -- just as importantly -- `checkIn()` is what falls back to
 * `/_internal/reevaluate` when Owner is unreachable, which is the ONLY thing
 * on Android that re-runs the offline policy against elapsed trusted time.
 * Polling `status()` would tick forever and advance nothing.
 *
 * Inert (never starts) when `BuildConfig.OWNER_LICENSING_BASE_URL` is blank,
 * the same fail-safe-empty rule [com.actionaura.retail.sync.SyncCoordinator]
 * and AppRoot's activation gate already use: an unconfigured build means
 * licensing is not enforced at all, so there is nothing to check in with.
 */
object LicenseCheckInCoordinator {

    private const val TAG = "LicenseCheckIn"

    /**
     * Must stay equal to `SubsystemApp.LICENSE_CHECKIN_POLL_MS` in
     * `products/retail/frontend/app-shell.js`. LicenseCheckInContractTest
     * reads that file and fails if the two ever drift, so "Android quietly
     * checks in at a different rate than Windows" cannot happen silently.
     */
    const val INTERVAL_MS: Long = 5L * 60 * 1000

    @Volatile private var job: Job? = null
    private val lock = Object()

    /**
     * Starts the loop. Safe to call more than once (a repeat call while
     * already running is a no-op) and safe to call before the device has ever
     * activated -- [LicensingCoordinator.checkIn] short-circuits locally, with
     * no Owner call at all, while there is no installation yet.
     *
     * Must be called after [com.actionaura.retail.server.ServerBootstrap.start]
     * has completed: every tick talks to the embedded Flask server.
     */
    fun start(appContext: Context) {
        if (BuildConfig.OWNER_LICENSING_BASE_URL.isBlank()) return
        synchronized(lock) {
            if (job?.isActive == true) return
            val coordinator = LicensingCoordinator(appContext.applicationContext)
            val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
            job = scope.launch {
                // No immediate first call -- AppRoot's boot gate already read
                // a fresh status a moment ago. This loop exists only to catch
                // a change Owner makes, or grace time that elapses, WHILE the
                // app is already running. Same reasoning as
                // _startLicenseCheckInPoll()'s own comment on desktop.
                while (isActive) {
                    delay(INTERVAL_MS)
                    runOnce(coordinator)
                }
            }
        }
    }

    fun stop() {
        synchronized(lock) {
            job?.cancel()
            job = null
        }
    }

    fun isRunning(): Boolean = job?.isActive == true

    /**
     * One tick. Never throws: a background poll must not be able to take the
     * app down, and a failed attempt is not itself an error state -- checkIn()
     * has already re-evaluated the offline policy locally by the time it
     * returns a `last_attempt_reached_owner=false` result.
     *
     * Deliberately no UI update. Every mutation route independently re-reads
     * this same persisted state via `require_license_capability()` on its own
     * next request; this loop's whole job is making sure that persisted state
     * does not go stale for a full session, not rendering a banner itself.
     */
    private suspend fun runOnce(coordinator: LicensingCoordinator) {
        try {
            val result = coordinator.checkIn()
            if (result["last_attempt_reached_owner"] != true) {
                // Logged, not silent: a device that has not reached Owner for
                // days is exactly the case that ends in a surprise RESTRICTED,
                // and "no log, no error, just silent non-progress" is the
                // failure mode SyncCoordinator's own LocalSyncApiError comment
                // was written about.
                Log.w(TAG, "Licence check-in did not reach Owner; local state re-evaluated offline " +
                           "(current_state=${result["current_state"]}).")
            }
        } catch (exc: Exception) {
            Log.e(TAG, "Licence check-in tick failed outright; the next tick will retry.", exc)
        }
    }
}
