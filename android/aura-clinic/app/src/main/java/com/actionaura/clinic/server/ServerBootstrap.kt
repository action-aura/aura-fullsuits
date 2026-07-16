package com.actionaura.clinic.server

import android.content.Context
import com.actionaura.clinic.AssetInstaller
import com.actionaura.clinic.net.ApiClient
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Starts the embedded Flask backend (via Chaquopy) the same way the WebView build
 * did — only now the native Compose UI talks to it over http://127.0.0.1:<port>.
 * Reuses AssetInstaller (extracts bundled assets) and the Python `main` module.
 */
object ServerBootstrap {

    @Volatile private var port: Int = 0

    /** Boots the server if needed and returns the port it's serving on. Idempotent. */
    suspend fun start(context: Context): Int = withContext(Dispatchers.IO) {
        ApiClient.init(context)   // enable persistent login cookie (survives restarts)
        if (port != 0) return@withContext port

        val versionCode = try {
            context.packageManager.getPackageInfo(context.packageName, 0).versionCode
        } catch (e: Exception) { 1 }
        AssetInstaller.installIfNeeded(context, versionCode)

        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(context))
        }
        val py = Python.getInstance()
        val main = py.getModule("main")
        val filesDir = context.filesDir.absolutePath
        val p = main.callAttr("start_server", filesDir).toInt()
        main.callAttr("wait_until_ready", p, 45)
        port = p
        p
    }

    fun baseUrl(): String = "http://127.0.0.1:$port/"
}
