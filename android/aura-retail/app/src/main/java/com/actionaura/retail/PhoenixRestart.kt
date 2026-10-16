package com.actionaura.retail

import android.content.Intent

/**
 * The pure rules of the "phoenix" restart, kept free of Android calls so they can be unit-tested.
 *
 * WHY (emulator run 2026-10-16): the first restart scheduled an AlarmManager PendingIntent and then killed the
 * process. Android 10+ refused to start the activity from the alarm ("Background activity launch blocked",
 * calling uid in a NONEXISTENT process), so the user landed on the home screen and the app never came back.
 * An app may start an activity while it has a visible one, so the relaunch is now done in two hops while the
 * app is still in front: the main process starts [RestartActivity] (its own process, so it survives the
 * kill), then exits; [RestartActivity] waits for the old process to be gone and starts the real launch
 * intent.
 */
object PhoenixRestart {
    const val EXTRA_LAUNCH_INTENT = "com.actionaura.retail.restart.LAUNCH_INTENT"
    const val EXTRA_MAIN_PID = "com.actionaura.retail.restart.MAIN_PID"

    /** How long [RestartActivity] waits for the old process to disappear before launching anyway. */
    const val WAIT_FOR_EXIT_MILLIS = 5_000L
    const val POLL_MILLIS = 50L

    /** Flags for the intent that starts [RestartActivity]. NEW_TASK is mandatory: it is started from the
     *  application context, and without the flag that start throws. */
    fun phoenixFlags(): Int = Intent.FLAG_ACTIVITY_NEW_TASK

    /** Flags for the real launch intent: a fresh task, so the new process starts clean. */
    fun mainLaunchFlags(): Int = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK

    /** The relaunch may only be STARTED while the app is in the foreground; from the background Android
     *  blocks it and the process would die for nothing. (RestartGate already waits for this; this is the
     *  last check before the point of no return.) */
    fun mayStart(isForeground: Boolean): Boolean = isForeground

    /** Has the old main process exited? [runningPids] are the pids Android still lists for this app. */
    fun oldProcessGone(mainPid: Int, runningPids: Collection<Int>): Boolean = mainPid !in runningPids

    /** Should [RestartActivity] stop waiting and launch now (old process gone, or out of patience)? */
    fun launchNow(gone: Boolean, waitedMillis: Long): Boolean = gone || waitedMillis >= WAIT_FOR_EXIT_MILLIS
}
