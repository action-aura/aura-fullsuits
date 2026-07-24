package com.actionaura.clinic.server

import android.content.Context
import com.actionaura.clinic.AssetInstaller
import com.actionaura.clinic.BuildConfig
import com.actionaura.clinic.net.ApiClient
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.security.SecureRandom
import java.util.Base64

/**
 * Starts the embedded Flask backend (via Chaquopy) the same way the WebView build
 * did — only now the native Compose UI talks to it over http://127.0.0.1:<port>.
 * Reuses AssetInstaller (extracts bundled assets) and the Python `main` module.
 */
object ServerBootstrap {

    @Volatile private var port: Int = 0

    // Phase 7 Part H/U: a fresh, random, process-lifetime-only secret proving
    // a /_internal/sync-* request came from THIS app's own Kotlin process
    // (see commercial_runtime/licensing_contracts/routes.py's
    // _register_internal_sync_routes docstring for the full threat model --
    // localhost binding already keeps other devices out, this secret is
    // narrower: keeping other apps on the SAME device out). Generated once
    // here, never persisted, never logged.
    private val internalSharedSecret: String by lazy {
        val bytes = ByteArray(32)
        SecureRandom().nextBytes(bytes)
        Base64.getEncoder().encodeToString(bytes)
    }

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
        // OWNER_LICENSING_BASE_URL defaults to "" (see build.gradle) -- an
        // unconfigured build leaves licensing NOT_CONFIGURED, same fail-safe
        // default as Windows (config.py), never a hidden fallback URL.
        val p = main.callAttr(
            "start_server", filesDir, 5000, BuildConfig.OWNER_LICENSING_BASE_URL, internalSharedSecret
        ).toInt()
        main.callAttr("wait_until_ready", p, 45)
        port = p
        p
    }

    fun baseUrl(): String = "http://127.0.0.1:$port/"

    /** The same secret just handed to Python -- callers (LicensingCoordinator)
     * attach it as X-Aura-Internal-Secret on every /_internal/sync-* call. */
    fun internalSecret(): String = internalSharedSecret
}
