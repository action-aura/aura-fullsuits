package com.actionaura.retail

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.os.Process
import android.util.Log

/**
 * Restarts the whole app process (SYNC-BOOT Phase 3). A staged bootstrap copy of the shop is swapped in by the
 * embedded Python backend's `init_app()` BEFORE any database opens, and that runs once per process; the Flask
 * server lives in this process, so the only clean way to run it again is a new process. The launch intent is
 * scheduled first (AlarmManager survives this process dying), then this process is ended.
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

    /** @return false when the package has no launch intent (nothing was done). */
    private fun relaunch(app: Context): Boolean {
        val launch = app.packageManager.getLaunchIntentForPackage(app.packageName) ?: return false
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
        val pending = PendingIntent.getActivity(
            app, 0, launch, PendingIntent.FLAG_CANCEL_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        (app.getSystemService(Context.ALARM_SERVICE) as AlarmManager)
            .set(AlarmManager.RTC, System.currentTimeMillis() + 500L, pending)
        Process.killProcess(Process.myPid())
        return true
    }
}
