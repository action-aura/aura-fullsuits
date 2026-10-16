package com.actionaura.retail

import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.os.Process
import android.util.Log

/**
 * Restarts the whole app process (SYNC-BOOT Phase 3). A staged bootstrap copy of the shop is swapped in by the
 * embedded Python backend's `init_app()` BEFORE any database opens, and that runs once per process; the Flask
 * server lives in this process, so the only clean way to run it again is a new process. The relaunch is
 * handed to [RestartActivity] (own process, "phoenix" pattern, see [PhoenixRestart]) and then this process
 * is ended.
 *
 * WHEN: not at once. [RestartCoordinator] waits until the app is in the foreground and the till has been
 * idle for a minute (see [RestartGate]), and schedules at most one restart however often it is asked. If the
 * swap is then REFUSED at start (an open cash drawer, a newer schema) the installer boots on the old data and
 * says why; nothing is lost either way.
 */
object AppRestart {
    private const val TAG = "AppRestart"
    @Volatile private var coordinator: RestartCoordinator? = null

    @Synchronized
    fun restart(context: Context) {
        val app = context.applicationContext
        val c = coordinator ?: RestartCoordinator(
            gate = RestartGate(
                isForeground = { TillActivity.isForeground },
                cartLineCount = { TillActivity.cartLineCount },
                inFlight = { TillActivity.inFlightRequests() },
                clock = { System.currentTimeMillis() },
            ),
            post = { delay, block -> Handler(Looper.getMainLooper()).postDelayed({ block() }, delay) },
            performRestart = { relaunch(app) },
            log = { Log.e(TAG, it) },
        ).also { coordinator = it }
        c.request()
    }

    /** @return false when nothing was done (no launch intent, or not in the foreground): the staged copy
     *  stays staged and the coordinator tries again later. */
    private fun relaunch(app: Context): Boolean {
        val launch = app.packageManager.getLaunchIntentForPackage(app.packageName) ?: return false
        // Android 10+ blocks starting an activity from the background (and from an alarm of a dead
        // process), so the hand-over happens NOW, while this app is in front: start RestartActivity (its
        // own process), then let this process end. See PhoenixRestart for the 2026-10-16 evidence.
        if (!PhoenixRestart.mayStart(TillActivity.isForeground)) return false
        val phoenix = Intent(app, RestartActivity::class.java)
            .addFlags(PhoenixRestart.phoenixFlags())
            .putExtra(PhoenixRestart.EXTRA_LAUNCH_INTENT, launch)
            .putExtra(PhoenixRestart.EXTRA_MAIN_PID, Process.myPid())
        app.startActivity(phoenix)
        Process.killProcess(Process.myPid())
        return true
    }
}
