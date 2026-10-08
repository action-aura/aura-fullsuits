package com.actionaura.retail.updates

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log
import java.io.File

/**
 * The last step: hand a verified APK to Android's installer. An interface so
 * the flow's "installer invoked zero times on every refusal, exactly once on
 * the good path" can be COUNTED in a plain JVM test.
 */
interface ApkInstaller {
    /** False until the user has allowed this app to install packages (Settings, per app, API 26+). */
    fun canRequestPackageInstalls(): Boolean

    /** Starts the install. Returns once the session is committed; the outcome arrives via [InstallResultReceiver]. */
    fun install(apk: File)
}

/**
 * PackageInstaller SESSION API on purpose -- not ACTION_VIEW with a content
 * URI. That route needs a FileProvider (an exported-ish door into our private
 * cache), grants a read permission to whichever app answers the intent, and
 * reports nothing back. A session writes the bytes straight into the system's
 * installer and tells us how it ended through a PendingIntent.
 */
class SessionApkInstaller(context: Context) : ApkInstaller {
    private val appContext = context.applicationContext

    override fun canRequestPackageInstalls(): Boolean = appContext.packageManager.canRequestPackageInstalls()

    override fun install(apk: File) {
        val installer = appContext.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
        // Pinned to this app's own package: even a verified-but-wrong archive
        // cannot install under another name through this session.
        params.setAppPackageName(appContext.packageName)
        params.setSize(apk.length())
        val sessionId = installer.createSession(params)
        installer.openSession(sessionId).use { session ->
            apk.inputStream().use { input ->
                session.openWrite("aura-update.apk", 0, apk.length()).use { out ->
                    input.copyTo(out)
                    session.fsync(out)
                }
            }
            session.commit(resultIntentSender(appContext, sessionId))
        }
    }

    private fun resultIntentSender(context: Context, sessionId: Int) =
        PendingIntent.getBroadcast(
            context,
            sessionId,
            // Explicit (component + package): only OUR receiver can be targeted.
            Intent(context, InstallResultReceiver::class.java)
                .setPackage(context.packageName)
                .setAction(InstallResultReceiver.ACTION),
            // The system adds the status extras to this intent, so on API 31+ it
            // must be MUTABLE; below 31 every PendingIntent already is.
            PendingIntent.FLAG_UPDATE_CURRENT or
                (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0),
        ).intentSender

    companion object {
        /** Intent that opens "Install unknown apps" for THIS app (the only app it can ever grant). */
        fun unknownAppSourcesIntent(context: Context): Intent =
            Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${context.packageName}"))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }
}

/**
 * Receives the installer's progress/result. Declared in the manifest with
 * exported=false: the system reaches it through the PendingIntent above, which
 * carries this app's own identity, so no other app can post a fake "success".
 */
class InstallResultReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION) return
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
        if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            // The system wants the user to confirm. It hands us the activity to
            // show; we only ever launch what the installer itself gave us.
            @Suppress("DEPRECATION")
            val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
            if (confirm != null) {
                confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                context.startActivity(confirm)
            } else {
                Log.e(TAG, "Installer asked for user action but sent no confirm screen.")
                UpdateCoordinator.onInstallStatus(PackageInstaller.STATUS_FAILURE)
            }
            return
        }
        if (status != PackageInstaller.STATUS_SUCCESS) {
            Log.w(TAG, "Install ended with status $status: ${intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)}")
        }
        UpdateCoordinator.onInstallStatus(status)
    }

    companion object {
        const val ACTION = "com.actionaura.retail.updates.INSTALL_RESULT"
        private const val TAG = "UpdateInstall"
    }
}
