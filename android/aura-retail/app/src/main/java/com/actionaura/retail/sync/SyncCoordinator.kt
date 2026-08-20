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
import kotlin.random.Random

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

/** One half (push or pull) of this device's sync health. Immutable so a
 *  @Volatile reference swap publishes a coherent snapshot with no lock on
 *  the read path -- the Kotlin equivalent of what sync_service.py needs a
 *  dedicated _health_lock for (there, dicts are mutated in place). */
data class SyncHalfHealth(
    val healthy: Boolean = true,
    val lastSuccessAtMillis: Long? = null,
    val lastFailureAtMillis: Long? = null,
    val lastFailureReason: String? = null,
    val consecutiveFailures: Int = 0,
)

data class SyncHealth(
    val configured: Boolean,
    val running: Boolean,
    val push: SyncHalfHealth,
    val pull: SyncHalfHealth,
) {
    val healthy: Boolean get() = push.healthy && pull.healthy
}

object SyncCoordinator {

    private const val TAG = "SyncCoordinator"
    private const val BACKOFF_MAX_SECONDS = 300L
    private const val INTERVAL_SECONDS = 10L

    /** Upper bound on how many events one signed push request may carry.
     * Owner's relay hard-rejects any batch above its `_MAX_PUSH_BATCH = 200`
     * (owner/app/sync/routes.py) with INVALID_BATCH, all-or-nothing -- so an
     * unchunked push of a backlog that grew past the cap offline could never
     * succeed, and the outbox never drained again (AUDIT 2026-08-19).
     * Mirrors sync_service.py's `_PUSH_CHUNK_SIZE`; must stay at or below
     * Owner's cap. */
    private const val PUSH_CHUNK_SIZE = 200

    @Volatile private var identity: DeviceIdentity? = null
    @Volatile private var running = false
    @Volatile private var pushHealth = SyncHalfHealth()
    @Volatile private var pullHealth = SyncHalfHealth()
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
                recordSuccess(isPush = true)
            } catch (exc: Exception) {
                val n = recordFailure(isPush = true, exc)
                Log.e(TAG, "Sync nudge push to the Owner relay FAILED (reason=${reasonOf(exc)}, " +
                           "consecutive push failures=$n); the outbox was left untouched and the " +
                           "next timer tick will retry.", exc)
            }
        }
    }

    private fun reasonOf(exc: Throwable): String = when (exc) {
        is SyncRelayClientError -> exc.reasonCode
        is LocalSyncApiError    -> "LOCAL_API_HTTP_${exc.statusCode}"
        else                    -> exc::class.simpleName ?: "UNKNOWN"
    }

    private fun recordSuccess(isPush: Boolean) = synchronized(lock) {
        val now = System.currentTimeMillis()
        if (isPush) pushHealth = pushHealth.copy(healthy = true, consecutiveFailures = 0, lastSuccessAtMillis = now)
        else        pullHealth = pullHealth.copy(healthy = true, consecutiveFailures = 0, lastSuccessAtMillis = now)
    }

    /** Returns the new consecutive-failure count so the caller's Log.e line can name it. */
    private fun recordFailure(isPush: Boolean, exc: Throwable): Int = synchronized(lock) {
        val now = System.currentTimeMillis()
        val prev = if (isPush) pushHealth else pullHealth
        val next = prev.copy(healthy = false, consecutiveFailures = prev.consecutiveFailures + 1,
                             lastFailureAtMillis = now, lastFailureReason = reasonOf(exc))
        if (isPush) pushHealth = next else pullHealth = next
        next.consecutiveFailures
    }

    /** Queryable sync health. No UI consumes this yet (deliberate -- native
     *  Android has no StateFlow anywhere in this module, and the UI-banner
     *  ask for this task was for the web frontend, not this app); this
     *  exists so a failure is inspectable rather than invisible, and is the
     *  seam any future UI would read. */
    fun health(): SyncHealth = SyncHealth(
        configured = BuildConfig.OWNER_SYNC_BASE_URL.isNotBlank(),
        running = running, push = pushHealth, pull = pullHealth,
    )

    private fun nextDelayMillis(): Long {
        val failures = maxOf(pushHealth.consecutiveFailures, pullHealth.consecutiveFailures)
        if (failures == 0) return INTERVAL_SECONDS * 1000
        val exponent = minOf(failures - 1, 16)          // 10 << 16 already far past the cap; guards overflow
        val capped = minOf(INTERVAL_SECONDS shl exponent, BACKOFF_MAX_SECONDS)
        val jitter = (capped * 0.2 * Random.nextDouble()).toLong()
        return minOf(capped + jitter, BACKOFF_MAX_SECONDS) * 1000
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
        }, nextDelayMillis())
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
            // Empty outbox / not-yet-activated returns early without touching
            // the relay -- still a success: push health means "nothing is
            // stuck in the outbox".
            recordSuccess(isPush = true)
        } catch (exc: Exception) {
            val n = recordFailure(isPush = true, exc)
            Log.e(TAG, "Sync push to the Owner relay FAILED (reason=${reasonOf(exc)}, " +
                       "consecutive push failures=$n); the outbox was left untouched and will " +
                       "be retried on the next tick.", exc)
        }
        try {
            pullOnce()
            recordSuccess(isPush = false)
        } catch (exc: Exception) {
            val n = recordFailure(isPush = false, exc)
            Log.e(TAG, "Sync pull from the Owner relay FAILED (reason=${reasonOf(exc)}, " +
                       "consecutive pull failures=$n); the local cursor was not advanced and " +
                       "the same range will be re-pulled on the next tick.", exc)
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

        // Chunked to the relay's batch cap (see PUSH_CHUNK_SIZE), each chunk
        // acked only after Owner genuinely stored it and BEFORE the next
        // chunk goes out -- mirrors desktop push_once()'s per-chunk
        // ack-and-commit exactly. A failure partway through propagates to
        // runOnce()/nudge()'s catch with every un-acked chunk still queued
        // locally, so the next tick resumes where this one stopped instead
        // of losing or skipping events. The client is built once per attempt
        // (not per chunk), matching desktop's per-attempt client_factory.
        val client = relayClient(installationId)
        for (chunk in events.chunked(PUSH_CHUNK_SIZE)) {
            val canonicalEvents = chunk.map { jsonToCanonical(it) }
            client.push(canonicalEvents) // raises on failure -- this chunk's ack below never runs

            val ids = chunk.mapNotNull { it.asJsonObject.get("id")?.asString }
            val ackBody = JsonObject().apply {
                add("ids", JsonArray().apply { ids.forEach { add(it) } })
            }
            postLocal("/api/sync/_internal/outbox/ack", ackBody.toString())
        }
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
