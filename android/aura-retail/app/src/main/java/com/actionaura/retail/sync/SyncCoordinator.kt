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
 *  - The embedded Python backend's local `_internal/...` routes
 *    (`commercial_runtime/sync/internal_routes.py`, registered only on
 *    Android) are the narrow local seam this class uses to read the outbox
 *    it's about to sign, acknowledge what it successfully pushed, read the
 *    cursor, and apply what it successfully pulled -- authenticated the
 *    same way [ServerBootstrap]'s other internal calls are, via
 *    `X-Aura-Internal-Secret`.
 *
 * TWO independent streams drive this: retail data (`/api/sync/_internal/...`)
 * and the `user`/`user_permission` registry (`/api/registry-sync/_internal/...`)
 * -- see [SyncStream]. Both expose the identical four routes (`outbox`,
 * `outbox/ack`, `cursor`, `pull-apply`); [runOnce] iterates both every tick,
 * each stream's push and pull isolated behind its own try/catch, because a
 * cashier account created on desktop must still be able to reach this
 * device even on a tick where the retail outbox is jammed, and vice versa.
 *
 * Inert (never starts a timer) when neither an explicit build-time relay
 * nor a persisted, activation-discovered one is available -- same
 * fail-safe-empty philosophy as `OWNER_LICENSING_BASE_URL` (see that
 * field's own build.gradle comment): an unconfigured build means sync
 * never runs, never a hidden default Owner instance.
 *
 * Relay URL precedence (launch-readiness, phone-half, 2026-09-03; mirrors
 * desktop's `_resolve_effective_sync_relay_base_url()` in
 * products/retail/backend/config.py): `BuildConfig.OWNER_SYNC_BASE_URL`
 * (an operator's explicit build-time value) ALWAYS wins when non-blank --
 * see [resolveRelayBaseUrl]. Otherwise [start]'s `persistedRelayBaseUrl`
 * parameter is used, provided it passes [requireTransportIsSafe] -- the
 * identical transport-safety check a build-time value is held to at
 * request time (see `SyncRelayClient.requestJson()`).
 *
 * Desktop learns the persisted value by importing `LicenseStateRepository`
 * and reading `licensing.db` directly (`config.py`'s
 * `_discover_persisted_sync_relay_url()`); Android's Kotlin layer never
 * opens that database itself, only the embedded Python backend's own HTTP
 * routes (see [LicensingCoordinator]'s class doc). `present_status()`
 * (status_presenter.py) now includes `sync_relay_base_url` in the JSON
 * `GET /api/licensing/status` returns -- present only when this device has
 * actually learned one at activation, omitted entirely otherwise, the same
 * pattern its pre-existing `installation_id` field uses -- specifically so
 * this platform can learn it without touching licensing.db itself.
 * [com.actionaura.retail.ui.AppRoot] reads that field and passes it as
 * [start]'s `persistedRelayBaseUrl`, both on app boot and (since [start] is
 * documented idempotent -- a repeat call while already running is a no-op)
 * again right after a successful activation, so a device that activates
 * mid-session starts syncing without needing a restart.
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

/** [pendingCount] is the SUM of every [SyncStream]'s outbox size, not just
 *  retail's -- deliberately NOT split per-stream here (no new fields): the
 *  screens that already read this shape (`SyncStatusScreen`,
 *  `SyncStatusPresentationTest`) must not need to change to learn about the
 *  registry stream existing at all. */
data class SyncHealth(
    val configured: Boolean,
    val running: Boolean,
    val push: SyncHalfHealth,
    val pull: SyncHalfHealth,
    val pendingCount: Int = 0,
) {
    val healthy: Boolean get() = push.healthy && pull.healthy
}

object SyncCoordinator {

    private const val TAG = "SyncCoordinator"
    private const val BACKOFF_MAX_SECONDS = 300L
    private const val INTERVAL_SECONDS = 10L

    /** Indirection over `android.util.Log.e` -- injectable ONLY so
     *  SyncCoordinatorTest can drive a genuinely FAILING tick without
     *  tripping `android.util.Log`'s "not mocked" RuntimeException (this
     *  module has neither Robolectric nor `unitTests.returnDefaultValues`,
     *  and this file is the first code whose behavioral test needed to
     *  reach a real failure path that logs). Mirrors [SyncRelayClient]'s own
     *  injectable `sleepFn` for the identical reason -- a real side effect
     *  in production, swapped for a silent recorder in tests -- the only
     *  difference being this is a singleton `object` with no constructor to
     *  inject through, so the seam is a mutable `internal var` instead of a
     *  constructor parameter. Production callers must never assign this. */
    internal var logError: (String, Throwable?) -> Unit = { message, exc ->
        if (exc != null) Log.e(TAG, message, exc) else Log.e(TAG, message)
    }

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
    /** The relay base URL [start] actually resolved and is (or was) running
     *  against -- see [resolveRelayBaseUrl]. Blank until [start] first
     *  decides to run; read by [nudge] and the zero-arg [runOnce] instead
     *  of either re-reading `BuildConfig.OWNER_SYNC_BASE_URL` directly, so
     *  a persisted-discovery URL (once a route exists to supply one) is
     *  used consistently for every tick, not just the first. */
    @Volatile private var effectiveRelayBaseUrl: String = ""

    /** [effectiveRelayBaseUrl], exposed `internal` purely so a plain-JVM
     *  test can assert WHICH url [start]'s precedence actually picked, not
     *  just whether the coordinator started at all -- "some valid url was
     *  chosen" would pass identically whether precedence picked the right
     *  one or the wrong one, since either candidate url in a precedence
     *  test is independently valid. */
    internal fun currentRelayBaseUrl(): String = effectiveRelayBaseUrl
    /** Per-stream outbox size, keyed by [SyncStream.label] -- summed (never
     *  overwritten wholesale) so one stream's freshly-read count can never
     *  clobber the other's; see [recordPending] and [SyncHealth]'s doc. */
    @Volatile private var pendingByStream: Map<String, Int> = emptyMap()
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
     * be serving).
     *
     * [persistedRelayBaseUrl] is the sync_relay_base_url Owner handed this
     * device at activation, if the caller has one -- see this class's own
     * doc comment for the precedence rule and for where
     * [com.actionaura.retail.ui.AppRoot] actually reads it from. Passing
     * null reproduces the exact pre-[persistedRelayBaseUrl] behavior: inert
     * unless `BuildConfig.OWNER_SYNC_BASE_URL` is set -- callers that have
     * no persisted value yet (never activated, licensing unconfigured, a
     * failed local lookup) are expected to pass null rather than omit a
     * genuine failure. */
    fun start(appContext: Context, persistedRelayBaseUrl: String? = null) {
        start(File(appContext.filesDir, "data"), BuildConfig.OWNER_SYNC_BASE_URL, persistedRelayBaseUrl)
    }

    /** Context-free core of [start] -- [buildConfigValue] and
     *  [identityDir] are taken as plain parameters (rather than this
     *  function reading `BuildConfig.OWNER_SYNC_BASE_URL` / `appContext
     *  .filesDir` itself) purely so a plain-JVM test can drive every
     *  precedence/validation branch, including a non-blank BuildConfig
     *  value, without a real Android `Context` -- this module has neither
     *  Robolectric nor Mockito, so `Context` cannot otherwise be
     *  constructed or faked here. Mirrors why the 4-arg [runOnce] overload
     *  takes `relayBaseUrl` as a parameter instead of reading BuildConfig
     *  directly. `internal`, not `private`, for that one reason. */
    internal fun start(identityDir: File, buildConfigValue: String, persistedRelayBaseUrl: String?) {
        val resolved = resolveRelayBaseUrl(buildConfigValue, persistedRelayBaseUrl)
        if (resolved.isBlank()) return
        synchronized(lock) {
            if (running) return
            // Same base dir LicensingCoordinator uses (File(filesDir, "data"))
            // -- this MUST resolve to the identical on-disk device key
            // LicensingCoordinator's DeviceIdentity already generated/uses
            // during activation. A second, differently-rooted DeviceIdentity
            // instance would hold a DIFFERENT key that Owner never activated,
            // and every signed push/pull would fail with INVALID_SIGNATURE
            // or DEVICE_KEY_REVOKED.
            identity = DeviceIdentity(identityDir)
            effectiveRelayBaseUrl = resolved
            running = true
            scheduleNext()
        }
    }

    /** Precedence + validation for the relay base URL this coordinator
     *  actually uses. [buildConfigValue] (an operator's explicit build-time
     *  value) ALWAYS wins when non-blank -- a shop built against a specific
     *  relay must never be silently redirected by whatever Owner told this
     *  device at activation, so [persistedValue] is never even inspected in
     *  that case. Otherwise [persistedValue] is used ONLY if it passes
     *  [requireTransportIsSafe] -- the identical check a build-time value is
     *  held to (lazily, at request time) by `SyncRelayClient.requestJson()`.
     *  A discovered value that fails this check is never partially trusted:
     *  this returns blank exactly as if nothing had been discovered, so
     *  [start] stays inert instead of starting a timer that would fail
     *  every tick against a URL already known to be unsafe before the first
     *  attempt. `internal`, not `private`, so a plain-JVM test can drive
     *  every branch directly -- mirrors why `requireTransportIsSafe` itself
     *  is `internal` in SyncRelayClient.kt. */
    internal fun resolveRelayBaseUrl(buildConfigValue: String, persistedValue: String?): String {
        if (buildConfigValue.isNotBlank()) return buildConfigValue
        val candidate = persistedValue?.trim().orEmpty()
        if (candidate.isBlank()) return ""
        return try {
            requireTransportIsSafe(candidate)
            candidate
        } catch (exc: SyncRelayClientError) {
            logError(
                "Discovered sync relay URL failed the same transport-safety check a " +
                    "build-time URL is held to (${exc.reasonCode}); ignoring it -- the " +
                    "coordinator stays inert rather than starting a timer that would fail " +
                    "every tick against a URL already known to be unsafe.",
                exc,
            )
            ""
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
     * actual push runs on its own short-lived daemon thread. Nudges BOTH
     * streams, with the same per-stream isolation [runOnce] uses -- one
     * stream's rejected push must not suppress the other's. */
    fun nudge() {
        if (!running) return
        thread(isDaemon = true, name = "sync-nudge") {
            val localBaseUrl = ServerBootstrap.baseUrl()
            val internalSecret = ServerBootstrap.internalSecret()
            val relayBaseUrl = effectiveRelayBaseUrl
            val identitySnapshot = currentIdentity()
            val failure = eachStreamCatching("nudge push") { stream ->
                pushOnce(stream, localBaseUrl, internalSecret, relayBaseUrl, identitySnapshot)
            }
            if (failure == null) {
                recordSuccess(isPush = true)
            } else {
                val (streamLabel, exc) = failure
                val n = recordFailure(isPush = true, streamLabel = streamLabel, exc = exc)
                logError("Sync nudge push to the Owner relay FAILED (stream=$streamLabel, " +
                         "reason=${reasonOf(exc)}, consecutive push failures=$n); that stream's " +
                         "outbox was left untouched and the next timer tick will retry.", exc)
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

    /** Returns the new consecutive-failure count so the caller's Log.e line
     *  can name it. [streamLabel] is baked directly into [SyncHalfHealth
     *  .lastFailureReason] (e.g. "registry:HTTP_403") -- the ONLY way this
     *  single shared field can still say WHICH of the two streams actually
     *  failed, since [SyncHealth] deliberately carries no per-stream
     *  fields. When both streams fail on the same tick, this records
     *  whichever one [eachStreamCatching] saw first (stream declaration
     *  order) -- the other's failure is still independently logged via
     *  Log.e, just not reflected in this one summarized field. */
    private fun recordFailure(isPush: Boolean, streamLabel: String, exc: Throwable): Int = synchronized(lock) {
        val now = System.currentTimeMillis()
        val prev = if (isPush) pushHealth else pullHealth
        val next = prev.copy(healthy = false, consecutiveFailures = prev.consecutiveFailures + 1,
                             lastFailureAtMillis = now, lastFailureReason = "$streamLabel:${reasonOf(exc)}")
        if (isPush) pushHealth = next else pullHealth = next
        next.consecutiveFailures
    }

    /** Records the outbox size just read for [stream] -- called BEFORE the
     *  chunked push loop that might throw, so a stream's pending count stays
     *  fresh even on a tick where its own push later fails (mirrors the
     *  original single-stream field's "at most one tick stale" guarantee).
     *  A plain volatile map swap, no lock, matching how the single-stream
     *  `pendingCount` field this replaces was never synchronized either. */
    private fun recordPending(stream: SyncStream, count: Int) {
        pendingByStream = pendingByStream + (stream.label to count)
    }

    /** Queryable sync health. No UI consumes this yet (deliberate -- native
     *  Android has no StateFlow anywhere in this module, and the UI-banner
     *  ask for this task was for the web frontend, not this app); this
     *  exists so a failure is inspectable rather than invisible, and is the
     *  seam any future UI would read. */
    fun health(): SyncHealth = SyncHealth(
        // The `||` matters: BuildConfig alone keeps reporting "configured"
        // even before [start] has ever run (unchanged pre-existing
        // behavior for a build-time relay), while effectiveRelayBaseUrl
        // alone covers a persisted-discovery URL that [start] resolved and
        // is now running against, which BuildConfig itself has no idea
        // about.
        configured = BuildConfig.OWNER_SYNC_BASE_URL.isNotBlank() || effectiveRelayBaseUrl.isNotBlank(),
        running = running, push = pushHealth, pull = pullHealth,
        pendingCount = pendingByStream.values.sum(),
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
        runOnce(
            localBaseUrl = ServerBootstrap.baseUrl(),
            internalSecret = ServerBootstrap.internalSecret(),
            relayBaseUrl = effectiveRelayBaseUrl,
            identity = currentIdentity(),
        )
    }

    /**
     * The actual dual-stream sync tick: pushes [SyncStream.ALL], then pulls
     * [SyncStream.ALL]. Parametrized (rather than reading [ServerBootstrap]/
     * [BuildConfig]/[currentIdentity] itself) purely so a plain-JVM test can
     * drive this exact function against a fake local+relay server -- the
     * zero-arg [runOnce] above is the only production caller and always
     * supplies the real values. `internal`, not `private`, for that one
     * reason, mirroring how [requireTransportIsSafe] in SyncRelayClient.kt
     * is exposed to its own test for the same reason.
     *
     * Push and pull each get their own pass over [SyncStream.ALL] (via
     * [eachStreamCatching]) -- FOUR independent try/catches total (retail
     * push, registry push, retail pull, registry pull). This extends this
     * function's original single-stream reasoning -- a push failure (which
     * can be a real, persistent RelayRejected against one bad outbox row,
     * not just "offline") must never skip pull() forever, because this
     * device should keep receiving other devices' updates regardless of
     * whether its own outbox can currently drain -- to the second stream: a
     * broken retail outbox must never stop a new cashier's account
     * (registry stream) from arriving, and a broken registry pull must
     * never stop retail sales from pushing out. Neither ordering nor
     * grouping is accidental: streams are batched by OPERATION (push both,
     * then pull both) rather than by stream (push+pull retail, then
     * push+pull registry) so each of the four operations keeps its own
     * dedicated catch instead of two operations ever sharing one.
     */
    internal fun runOnce(
        localBaseUrl: String,
        internalSecret: String,
        relayBaseUrl: String,
        identity: DeviceIdentity,
    ) {
        val pushFailure = eachStreamCatching("push") { stream ->
            pushOnce(stream, localBaseUrl, internalSecret, relayBaseUrl, identity)
        }
        if (pushFailure == null) {
            // Empty outbox / not-yet-activated returns early without touching
            // the relay for either stream -- still a success: push health
            // means "nothing is stuck in either stream's outbox".
            recordSuccess(isPush = true)
        } else {
            val (streamLabel, exc) = pushFailure
            val n = recordFailure(isPush = true, streamLabel = streamLabel, exc = exc)
            logError("Sync push FAILED this tick (first failing stream=$streamLabel, reason=" +
                     "${reasonOf(exc)}, consecutive push failures=$n); retried on the next tick.", exc)
        }

        val pullFailure = eachStreamCatching("pull") { stream ->
            pullOnce(stream, localBaseUrl, internalSecret, relayBaseUrl, identity)
        }
        if (pullFailure == null) {
            recordSuccess(isPush = false)
        } else {
            val (streamLabel, exc) = pullFailure
            val n = recordFailure(isPush = false, streamLabel = streamLabel, exc = exc)
            logError("Sync pull FAILED this tick (first failing stream=$streamLabel, reason=" +
                     "${reasonOf(exc)}, consecutive pull failures=$n); retried on the next tick.", exc)
        }
    }

    /** Runs [block] once per entry of [SyncStream.ALL], in order, catching
     *  each stream's failure INDEPENDENTLY -- one stream's exception can
     *  never prevent the next stream's [block] from running. Returns the
     *  (streamLabel, exception) of the FIRST stream that failed, in stream
     *  order, or null if every stream succeeded. THE LOAD-BEARING PART: this
     *  is the one try/catch shape shared by [runOnce]'s push pass, [runOnce]'s
     *  pull pass, and [nudge]'s push, so all four/two-way isolation
     *  guarantees come from a single implementation instead of four
     *  hand-copied try/catch blocks that could quietly drift apart. */
    private fun eachStreamCatching(opLabel: String, block: (SyncStream) -> Unit): Pair<String, Exception>? {
        var firstFailure: Pair<String, Exception>? = null
        for (stream in SyncStream.ALL) {
            try {
                block(stream)
            } catch (exc: Exception) {
                logError("Sync $opLabel (${stream.label}) FAILED (reason=${reasonOf(exc)}); this " +
                         "stream's own outbox/cursor was left untouched and will be retried.", exc)
                if (firstFailure == null) firstFailure = stream.label to exc
            }
        }
        return firstFailure
    }

    private fun currentIdentity(): DeviceIdentity = identity
        ?: throw IllegalStateException("SyncCoordinator.start() was never called.")

    private fun relayClient(installationId: String, relayBaseUrl: String, identity: DeviceIdentity): SyncRelayClient =
        SyncRelayClient(
            SyncRelayClientConfig(baseUrl = relayBaseUrl),
            identity,
            installationId,
        )

    private fun pushOnce(
        stream: SyncStream,
        localBaseUrl: String,
        internalSecret: String,
        relayBaseUrl: String,
        identity: DeviceIdentity,
    ) {
        val outbox = getLocal(stream.prefix + "/_internal/outbox", localBaseUrl, internalSecret)
        val installationId = outbox.stringOrNull("installation_id") ?: return
        val events = outbox.getAsJsonArray("events") ?: JsonArray()
        // The true complete backlog for THIS stream -- SyncService.read_outbox
        // has no LIMIT -- and it is at most one tick (10s) stale, because it
        // is re-read from a real query at the top of every push attempt.
        // Recorded per-stream (see recordPending) so SyncHealth.pendingCount
        // can be the SUM across both streams without either stream's count
        // clobbering the other's.
        recordPending(stream, events.size())
        if (events.size() == 0) return

        // Chunked to the relay's batch cap (see PUSH_CHUNK_SIZE), each chunk
        // acked only after Owner genuinely stored it and BEFORE the next
        // chunk goes out -- mirrors desktop push_once()'s per-chunk
        // ack-and-commit exactly. A failure partway through propagates to
        // eachStreamCatching()'s catch with every un-acked chunk still
        // queued locally, so the next tick resumes where this one stopped
        // instead of losing or skipping events. The client is built once
        // per attempt (not per chunk), matching desktop's per-attempt
        // client_factory.
        val client = relayClient(installationId, relayBaseUrl, identity)
        for (chunk in events.chunked(PUSH_CHUNK_SIZE)) {
            val canonicalEvents = chunk.map { jsonToCanonical(it) }
            client.push(canonicalEvents) // raises on failure -- this chunk's ack below never runs

            val ids = chunk.mapNotNull { it.asJsonObject.get("id")?.asString }
            val ackBody = JsonObject().apply {
                add("ids", JsonArray().apply { ids.forEach { add(it) } })
            }
            postLocal(stream.prefix + "/_internal/outbox/ack", ackBody.toString(), localBaseUrl, internalSecret)
        }
    }

    private fun pullOnce(
        stream: SyncStream,
        localBaseUrl: String,
        internalSecret: String,
        relayBaseUrl: String,
        identity: DeviceIdentity,
    ) {
        val cursor = getLocal(stream.prefix + "/_internal/cursor", localBaseUrl, internalSecret)
        val installationId = cursor.stringOrNull("installation_id") ?: return
        val since = cursor.get("since")?.asLong ?: 0L

        val result = relayClient(installationId, relayBaseUrl, identity).pull(since) // raises on failure
        // Forwarded verbatim (result.toString() re-serializes Gson's own
        // JsonElement tree, which preserves the original int/float text --
        // see jsonToCanonical()'s doc comment on why a generic Map<String,
        // Any?> round-trip would NOT) -- the local route applies it via the
        // exact same SyncService.apply_pull_result() pull_once() itself uses.
        postLocal(stream.prefix + "/_internal/pull-apply", result.toString(), localBaseUrl, internalSecret)
    }

    private fun JsonObject.stringOrNull(key: String): String? =
        get(key)?.takeUnless { it.isJsonNull }?.asString

    private fun localUrl(path: String, baseUrl: String): String = baseUrl.trimEnd('/') + path

    private fun getLocal(path: String, baseUrl: String, internalSecret: String): JsonObject {
        val request = Request.Builder()
            .url(localUrl(path, baseUrl))
            .header("X-Aura-Internal-Secret", internalSecret)
            .get()
            .build()
        return executeLocal(path, request)
    }

    private fun postLocal(path: String, jsonBody: String, baseUrl: String, internalSecret: String): JsonObject {
        val request = Request.Builder()
            .url(localUrl(path, baseUrl))
            .header("X-Aura-Internal-Secret", internalSecret)
            .post(jsonBody.toRequestBody("application/json".toMediaType()))
            .build()
        return executeLocal(path, request)
    }

    /** Thrown when a call to the embedded Python backend's own local
     * `_internal/...` routes (either stream's prefix) fails. Propagates up
     * to [eachStreamCatching]'s per-stream catch, so a failing local call
     * aborts only that ONE stream's ONE operation this tick and is retried
     * on the next one -- but is now genuinely LOGGED rather than being
     * mistaken for success.
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
                logError("Local sync API call to $path failed with HTTP ${response.code}: $detail", null)
                throw LocalSyncApiError(response.code, "Local sync API $path returned HTTP ${response.code}")
            }
            return try {
                JsonParser.parseString(bodyString).asJsonObject
            } catch (exc: Exception) {
                logError("Local sync API call to $path returned a non-JSON body: ${bodyString.take(500)}", exc)
                throw LocalSyncApiError(response.code, "Local sync API $path returned a malformed body")
            }
        }
    }
}
