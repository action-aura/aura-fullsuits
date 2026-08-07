package com.actionaura.retail.sync

import android.content.Context
import android.util.Log
import com.actionaura.retail.BuildConfig
import com.actionaura.retail.licensing.DeviceIdentity
import com.actionaura.retail.server.ServerBootstrap
import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.util.Timer
import java.util.TimerTask
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/**
 * Drives Android's push/pull sync loop (multi-device-sync-foundation, Task 9
 * wiring). Kotlin-side counterpart of `commercial_runtime/sync/sync_service.py`'s
 * `SyncService` -- same self-rescheduling-timer shape (never overlaps ticks;
 * a tick only reschedules the next one after it fully finishes), but this
 * process holds the real signing key (via [DeviceIdentity], AndroidKeystore-
 * backed) and Python's embedded backend does not, so the roles are split
 * differently than on Windows:
 *
 *  - THIS class makes the real signed HTTP calls to Owner (via
 *    [SyncRelayClient], mirroring [com.actionaura.retail.licensing.OwnerClient]).
 *  - The embedded Python backend's `/api/sync/_internal/...` routes
 *    (`commercial_runtime/sync/internal_routes.py`, registered only on
 *    Android) are the narrow local seam this class uses to read the outbox
 *    it's about to sign, acknowledge what it successfully pushed, read the
 *    cursor, and apply what it successfully pulled -- authenticated the
 *    same way [ServerBootstrap]'s other internal calls are, via
 *    `X-Aura-Internal-Secret`.
 *
 * Inert (never starts a timer) when `BuildConfig.OWNER_SYNC_BASE_URL` is
 * unconfigured -- same fail-safe-empty philosophy as
 * `OWNER_LICENSING_BASE_URL` (see that field's own build.gradle comment):
 * an unconfigured build means sync never runs, never a hidden default Owner
 * instance.
 */
object SyncCoordinator {

    private const val TAG = "SyncCoordinator"
    private const val INTERVAL_SECONDS = 10L

    @Volatile private var identity: DeviceIdentity? = null
    @Volatile private var running = false
    private var timer: Timer? = null
    private val lock = Object()

    private val http: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
            .build()
    }

    /** Starts the periodic sync loop. Safe to call more than once (a repeat
     * call while already running is a no-op) and safe to call before the
     * device has ever activated a license -- each tick independently checks
     * for a live `installation_id` (via the local `/_internal/outbox` and
     * `/_internal/cursor` responses) and simply skips that half of the work
     * when none exists yet, exactly mirroring `_build_sync_client()`'s own
     * per-attempt `record.owner_installation_id` re-check on desktop. Must
     * be called after [ServerBootstrap.start] has completed (the embedded
     * Flask server, and therefore the `/_internal/...` routes, must already
     * be serving). */
    fun start(appContext: Context) {
        if (BuildConfig.OWNER_SYNC_BASE_URL.isBlank()) return
        synchronized(lock) {
            if (running) return
            // Same base dir LicensingCoordinator uses (File(filesDir, "data"))
            // -- this MUST resolve to the identical on-disk device key
            // LicensingCoordinator's DeviceIdentity already generated/uses
            // during activation. A second, differently-rooted DeviceIdentity
            // instance would hold a DIFFERENT key that Owner never activated,
            // and every signed push/pull would fail with INVALID_SIGNATURE
            // or DEVICE_KEY_REVOKED.
            identity = DeviceIdentity(File(appContext.filesDir, "data"))
            running = true
            scheduleNext()
        }
    }

    fun stop() {
        synchronized(lock) {
            running = false
            timer?.cancel()
            timer = null
        }
    }

    /** Best-effort, non-blocking immediate push attempt -- mirrors
     * `commercial_runtime/sync/sync_service.py`'s module-level `nudge()`.
     * Safe to call unconditionally (a no-op when the loop was never
     * started/is unconfigured); never adds latency to the caller, since the
     * actual push runs on its own short-lived daemon thread. */
    fun nudge() {
        if (!running) return
        thread(isDaemon = true, name = "sync-nudge") {
            try {
                pushOnce()
            } catch (exc: Exception) {
                Log.i(TAG, "Sync nudge push failed (offline or rejected); the next timer tick will retry.", exc)
            }
        }
    }

    private fun scheduleNext() {
        if (!running) return
        val t = Timer("sync-coordinator", true)
        t.schedule(object : TimerTask() {
            override fun run() {
                try {
                    runOnce()
                } finally {
                    scheduleNext()
                }
            }
        }, INTERVAL_SECONDS * 1000)
        timer = t
    }

    private fun runOnce() {
        // push and pull each get their OWN try/catch -- mirrors
        // SyncService.run_once()'s own reasoning exactly: a push failure
        // (which can be a real, persistent RelayRejected against one bad
        // outbox row, not just "offline") must never skip pull() forever --
        // this device should keep receiving other devices' updates
        // regardless of whether its own outbox can currently drain.
        try {
            pushOnce()
        } catch (exc: Exception) {
            Log.i(TAG, "Sync push attempt failed (offline or rejected); will retry on the next tick.", exc)
        }
        try {
            pullOnce()
        } catch (exc: Exception) {
            Log.i(TAG, "Sync pull attempt failed (offline or rejected); will retry on the next tick.", exc)
        }
    }

    private fun currentIdentity(): DeviceIdentity = identity
        ?: throw IllegalStateException("SyncCoordinator.start() was never called.")

    private fun relayClient(installationId: String): SyncRelayClient =
        SyncRelayClient(
            SyncRelayClientConfig(baseUrl = BuildConfig.OWNER_SYNC_BASE_URL),
            currentIdentity(),
            installationId,
        )

    private fun pushOnce() {
        val outbox = getLocal("/api/sync/_internal/outbox")
        val installationId = outbox.stringOrNull("installation_id") ?: return
        val events = outbox.getAsJsonArray("events") ?: JsonArray()
        if (events.size() == 0) return

        val canonicalEvents = events.map { jsonToCanonical(it) }
        relayClient(installationId).push(canonicalEvents) // raises on failure -- ack below never runs

        val ids = events.mapNotNull { it.asJsonObject.get("id")?.asString }
        val ackBody = JsonObject().apply {
            add("ids", JsonArray().apply { ids.forEach { add(it) } })
        }
        postLocal("/api/sync/_internal/outbox/ack", ackBody.toString())
    }

    private fun pullOnce() {
        val cursor = getLocal("/api/sync/_internal/cursor")
        val installationId = cursor.stringOrNull("installation_id") ?: return
        val since = cursor.get("since")?.asLong ?: 0L

        val result = relayClient(installationId).pull(since) // raises on failure
        // Forwarded verbatim (result.toString() re-serializes Gson's own
        // JsonElement tree, which preserves the original int/float text --
        // see jsonToCanonical()'s doc comment on why a generic Map<String,
        // Any?> round-trip would NOT) -- the local route applies it via the
        // exact same SyncService.apply_pull_result() pull_once() itself uses.
        postLocal("/api/sync/_internal/pull-apply", result.toString())
    }

    private fun JsonObject.stringOrNull(key: String): String? =
        get(key)?.takeUnless { it.isJsonNull }?.asString

    private fun localUrl(path: String): String = ServerBootstrap.baseUrl().trimEnd('/') + path

    private fun getLocal(path: String): JsonObject {
        val request = Request.Builder()
            .url(localUrl(path))
            .header("X-Aura-Internal-Secret", ServerBootstrap.internalSecret())
            .get()
            .build()
        return executeLocal(path, request)
    }

    private fun postLocal(path: String, jsonBody: String): JsonObject {
        val request = Request.Builder()
            .url(localUrl(path))
            .header("X-Aura-Internal-Secret", ServerBootstrap.internalSecret())
            .post(jsonBody.toRequestBody("application/json".toMediaType()))
            .build()
        return executeLocal(path, request)
    }

    /** Thrown when a call to the embedded Python backend's own
     * `/api/sync/_internal/...` routes fails. Propagates up to
     * [runOnce]/[nudge]'s per-half catch, so a failing local call aborts only
     * that half of the tick and is retried on the next one -- but is now
     * genuinely LOGGED rather than being mistaken for success.
     *
     * Final-review Fix 5 (2026-08-07): this used to parse the response body
     * regardless of status code, so a 400 from `/pull-apply` (e.g. Python
     * raising while applying a pulled event) or a 403 from `/outbox/ack`
     * (wrong internal secret) was treated as a successful call. On the ack
     * path that is actively destructive -- a rejected ack was followed by
     * "push succeeded", leaving the outbox rows to be pushed again forever;
     * on the pull-apply path it silently discarded the batch while the caller
     * believed the cursor had advanced. It is also precisely why the Fix 1
     * foreign-key wedge was invisible on this platform: no log, no error,
     * just silent non-progress. */
    class LocalSyncApiError(val statusCode: Int, message: String) : Exception(message)

    private fun executeLocal(path: String, request: Request): JsonObject {
        http.newCall(request).execute().use { response ->
            val bodyString = response.body?.string() ?: ""
            if (!response.isSuccessful) {
                // Truncated: a Flask error page can be many KB of HTML, and
                // this goes to logcat on every failing tick.
                val detail = bodyString.take(500)
                Log.e(TAG, "Local sync API call to $path failed with HTTP ${response.code}: $detail")
                throw LocalSyncApiError(response.code, "Local sync API $path returned HTTP ${response.code}")
            }
            return try {
                JsonParser.parseString(bodyString).asJsonObject
            } catch (exc: Exception) {
                Log.e(TAG, "Local sync API call to $path returned a non-JSON body: ${bodyString.take(500)}", exc)
                throw LocalSyncApiError(response.code, "Local sync API $path returned a malformed body")
            }
        }
    }
}
