package com.actionaura.retail

import android.app.Activity
import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.Process
import android.util.Log

/**
 * Second half of the phoenix restart (see [PhoenixRestart]). Declared in the manifest with
 * `android:process=":restart"`, so it lives in its OWN process and is not taken down when the main process
 * exits. It has no UI (translucent, no history, out of recents). It waits for the old main process to be
 * gone, starts the real launch intent while it is itself the visible activity (which Android allows), then
 * finishes and ends its own process so no stray process is left behind.
 */
class RestartActivity : Activity() {
    private val handler = Handler(Looper.getMainLooper())
    private var waited = 0L

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val launch = launchIntent()
        if (launch == null) {
            Log.e(TAG, "No launch intent was passed; nothing to restart.")
            finishAndEnd()
            return
        }
        val mainPid = intent.getIntExtra(PhoenixRestart.EXTRA_MAIN_PID, -1)
        poll(launch, mainPid)
    }

    private fun poll(launch: Intent, mainPid: Int) {
        val gone = PhoenixRestart.oldProcessGone(mainPid, runningPids())
        if (PhoenixRestart.launchNow(gone, waited)) {
            launch.addFlags(PhoenixRestart.mainLaunchFlags())
            try {
                startActivity(launch)
            } catch (exc: Exception) {
                Log.e(TAG, "Could not start the app again.", exc)
            }
            finishAndEnd()
            return
        }
        waited += PhoenixRestart.POLL_MILLIS
        handler.postDelayed({ poll(launch, mainPid) }, PhoenixRestart.POLL_MILLIS)
    }

    @Suppress("DEPRECATION")
    private fun launchIntent(): Intent? =
        if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra(PhoenixRestart.EXTRA_LAUNCH_INTENT, Intent::class.java)
        else intent.getParcelableExtra(PhoenixRestart.EXTRA_LAUNCH_INTENT)

    private fun runningPids(): List<Int> {
        val am = getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        return am.runningAppProcesses?.map { it.pid } ?: emptyList()
    }

    private fun finishAndEnd() {
        finish()
        Process.killProcess(Process.myPid())
    }

    private companion object {
        const val TAG = "RestartActivity"
    }
}
