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
import java.net.URLEncoder
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

/**
 * The one thing [SyncCoordinator] actually pushes/pulls against on a given
 * tick: a relay base URL, plus the SPKI pin that authenticates it (blank for
 * Owner's cloud relay, non-blank for a paired LAN hub -- see [SpkiPinning]).
 *
 * Replaces the two fields this class used to carry separately
 * (`effectiveRelayBaseUrl` and `hubSpkiPin`), which could drift apart for a
 * moment on a relay switch -- e.g. a reader observing the URL after it was
 * updated but the pin before -- because nothing published the two together.
 * A `data class` behind a single `@Volatile` reference makes one publish
 * atomic: any reader (a tick that just started, [nudge]) sees either the OLD
 * (url, pin) pair or the NEW one, never a mix of the two. That guarantee is
 * by construction (one volatile read per tick, see
 * [runOnceAgainstCurrentTarget]), not by a concurrency test:
 * `SyncCoordinatorRebindTest` K1 re-points BETWEEN two ticks and asserts
 * every pair the second tick used is the new one, and K5 proves the pin
 * half is actually enforced against a real TLS hub. This mirrors why
 * [SyncHalfHealth] is itself an immutable data class behind a `@Volatile`
 * reference swap rather than several separately-mutated fields.
 */
internal data class RelayTarget(val baseUrl: String, val spkiPin: String)

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

    /** Owner's own pull page size (`store.PULL_LIMIT` / the cloud relay's
     *  `.limit(500)`) -- mirrored here so [readyToJoinHub]'s catch-up gate
     *  can tell "this pull returned a FULL page, there is certainly more
     *  behind it" from "this pull returned everything there was". A tick
     *  that saw a full page can never be "caught up" no matter how many
     *  events it moved, because Owner's own pagination guarantees a full
     *  page is never the end of the stream. */
    private const val CLOUD_PULL_PAGE_LIMIT = 500

    /** How long a device is willing to sit on the cloud relay, having never
     *  once observed a caught-up tick, before joining a LAN hub anyway --
     *  see [readyToJoinHub]'s own doc comment for why this exists at all
     *  (closing part of gap 0.5/G3: a fast re-point must not join a hub
     *  before this device's own pre-hub history has left the cloud). Ten
     *  minutes, not longer: a device that can never reach the cloud (no
     *  internet, Owner down) must still eventually be allowed to join its
     *  shop's LAN hub -- this is a grace period, not a hard requirement. */
    private const val CLOUD_CATCHUP_GRACE_MS = 10 * 60_000L

    @Volatile private var identity: DeviceIdentity? = null
    @Volatile private var running = false
    @Volatile private var pushHealth = SyncHalfHealth()
    @Volatile private var pullHealth = SyncHalfHealth()

    /** The relay this coordinator is (or was) running against, published as
     *  one atomic (url, pin) pair -- see [RelayTarget]'s own class doc for
     *  why this replaced two separately-mutated fields. Blank url until
     *  [start] first decides to run. Read by [nudge] and the zero-arg
     *  [runOnce] (via [runOnceAgainstCurrentTarget]) instead of either
     *  re-reading `BuildConfig.OWNER_SYNC_BASE_URL` directly or the stale
     *  Context-time snapshot [start] used to freeze forever, so a hub
     *  pairing (or unpairing) that happens WHILE this loop is already
     *  running takes effect on the very next tick -- see
     *  [onHubPairingChanged], the whole point of this task. */
    @Volatile private var target = RelayTarget("", "")

    /** [target]'s url, exposed `internal` purely so a plain-JVM test can
     *  assert WHICH url [start]'s precedence actually picked, not just
     *  whether the coordinator started at all -- "some valid url was
     *  chosen" would pass identically whether precedence picked the right
     *  one or the wrong one, since either candidate url in a precedence
     *  test is independently valid. */
    internal fun currentRelayBaseUrl(): String = target.baseUrl

    /** The full [RelayTarget] (url AND pin together), for a test that needs
     *  to prove both moved together on a re-point -- see
     *  `SyncCoordinatorRebindTest`. */
    internal fun currentRelayTarget(): RelayTarget = target

    /** [start]'s own `buildConfigValue`/`persistedRelayBaseUrl` arguments,
     *  remembered so [onHubPairingChanged] -- which is never handed either
     *  one, since [com.actionaura.retail.ui.screens.RetailExtraScreens]'s
     *  pairing screen and [HubAutoJoinService] know nothing about
     *  BuildConfig or licence-activation discovery -- can still recompute
     *  [resolveRelayTarget]'s FULL precedence, not just "hub vs nothing".
     *  Assigned at the very top of [start], before that function's own
     *  blank-result early return, so these hold the last values [start] was
     *  ever CALLED with even on a call that itself stayed inert (e.g. no
     *  hub and no persisted URL yet) -- [onHubPairingChanged] must still be
     *  able to fall back to whatever BuildConfig/persisted precedence
     *  [start] would have used, if a hub is later unpaired. [
     *  lastPersistedRelayBaseUrl] is only ever overwritten by a non-null
     *  value (mirrors [start]'s own "callers with no persisted value yet
     *  pass null rather than omit a genuine failure" contract) -- a later
     *  call passing null must not erase an earlier real discovery. */
    @Volatile private var lastBuildConfigValue: String = ""
    @Volatile private var lastPersistedRelayBaseUrl: String? = null

    /** Test seam for [readyToJoinHub]'s elapsed-time math -- mirrors
     *  [SyncRelayClient]'s own injectable `sleepFn` for the identical
     *  reason: a real side effect (`System.currentTimeMillis()`) swapped for
     *  a controllable one in `SyncCoordinatorCatchUpGateTest`. Production
     *  never assigns this. */
    internal var clock: () -> Long = { System.currentTimeMillis() }

    /** Set the instant a tick's pull pass against [target] came back fully
     *  caught up (see [runOnce]'s pull-pass tail) -- null until then, and
     *  reset to null by [markTargetChanged] on every re-point, because a
     *  caught-up observation against the OLD target says nothing about the
     *  new one. */
    @Volatile private var cloudCaughtUpAtMillis: Long? = null

    /** The instant [target] most recently became a CLOUD target (blank
     *  pin) -- null while [target] is a paired hub. Feeds [readyToJoinHub]'s
     *  grace-period fallback: "how long has this device been stuck on the
     *  cloud with no successful catch-up". Set in [start] (when the
     *  resolved target is cloud) and in [markTargetChanged]. */
    @Volatile private var cloudTargetSinceMillis: Long? = null

    /** Test seam for [relayClient] -- called as its first line with
     *  `(relayBaseUrl, spkiPin)`, so `SyncCoordinatorRebindTest` can record
     *  every (url, pin) pair a tick actually built a client against without
     *  needing a live TLS handshake to succeed. Production never assigns
     *  this. */
    internal var relayClientObserver: (String, String) -> Unit = { _, _ -> }

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
     * genuine failure.
     *
     * [hubRelayBaseUrl] is a paired LAN hub's URL (see [HubPrefs]) -- it
     * outranks even [persistedRelayBaseUrl] and `BuildConfig
     * .OWNER_SYNC_BASE_URL` (see [resolveRelayBaseUrl]'s own doc comment for
     * why). [com.actionaura.retail.ui.AppRoot] is expected to pass
     * `HubPrefs.getBaseUrl(ctx).takeIf { HubPrefs.isConfigured(ctx) }` here
     * -- non-null therefore means "this device IS paired", so the matching
     * SPKI pin is read from [appContext] right here, at the one moment this
     * singleton `object` ever holds a real `Context`, and stored as plain
     * instance state (inside [target], see [RelayTarget]) rather than the
     * `Context` itself -- mirroring exactly how [identityDir] below is
     * resolved from
     * `appContext.filesDir` once and only the resulting `File` is kept. */
    fun start(appContext: Context, persistedRelayBaseUrl: String? = null, hubRelayBaseUrl: String? = null) {
        val hubSpkiPinValue = hubRelayBaseUrl?.let { HubPrefs.getSpkiPin(appContext) }
        start(File(appContext.filesDir, "data"), BuildConfig.OWNER_SYNC_BASE_URL, persistedRelayBaseUrl, hubRelayBaseUrl, hubSpkiPinValue)
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
     *  directly. `internal`, not `private`, for that one reason.
     *
     *  [hubBaseUrl]/[hubSpkiPinValue] default to null so every existing
     *  3-arg call site (production and test) is unaffected. */
    internal fun start(
        identityDir: File,
        buildConfigValue: String,
        persistedRelayBaseUrl: String?,
        hubBaseUrl: String? = null,
        hubSpkiPinValue: String? = null,
    ) {
        // Remembered BEFORE the blank-result early return below, so
        // [onHubPairingChanged] can recompute this same precedence later
        // (e.g. on an unpair) even from a [start] call that itself stayed
        // inert -- see [lastBuildConfigValue]'s own doc comment.
        lastBuildConfigValue = buildConfigValue
        if (persistedRelayBaseUrl != null) lastPersistedRelayBaseUrl = persistedRelayBaseUrl
        val newTarget = resolveRelayTarget(buildConfigValue, persistedRelayBaseUrl, hubBaseUrl, hubSpkiPinValue)
        if (newTarget.baseUrl.isBlank()) return
        synchronized(lock) {
            if (running) return
            startLocked(identityDir, newTarget)
            // Reuses [markTargetChanged] rather than a bespoke line here:
            // a fresh start is "the target changed" too (from nothing to
            // [newTarget]) -- a device that boots straight onto the cloud
            // relay (the ordinary case) must still track how long it has
            // been there, so [readyToJoinHub]'s grace period has a real
            // starting point instead of null forever, and this also
            // guarantees [cloudCaughtUpAtMillis] never carries a stale
            // value across a stop()+start() cycle ([stop] itself has no
            // production caller, but onHubPairingChanged's unpair-with-no-
            // fallback branch is a production stop, and this module's own
            // tests, which share this singleton `object` across many `@Test`
            // methods in one JVM, cycle it constantly).
            markTargetChanged(newTarget)
        }
    }

    /** The actual "begin running against [newTarget]" side effects, shared
     *  by [start] (this device has never run before) and
     *  [onHubPairingChanged]'s not-yet-running branch (this device was
     *  never activated in time for [start] to have anything to run
     *  against, and a hub pairing is what gives it its first target) --
     *  see [HubAutoJoinRebindContractTest]/[HubPairingRebindContractTest]'s
     *  manual-pairing case for why that second caller matters: a LAN-only
     *  phone with no cloud relay at all must still start syncing the
     *  moment it pairs, with no restart. Caller holds [lock]. */
    private fun startLocked(identityDir: File, newTarget: RelayTarget) {
        // Same base dir LicensingCoordinator uses (File(filesDir, "data"))
        // -- this MUST resolve to the identical on-disk device key
        // LicensingCoordinator's DeviceIdentity already generated/uses
        // during activation. A second, differently-rooted DeviceIdentity
        // instance would hold a DIFFERENT key that Owner never activated,
        // and every signed push/pull would fail with INVALID_SIGNATURE
        // or DEVICE_KEY_REVOKED.
        identity = DeviceIdentity(identityDir)
        target = newTarget
        running = true
        scheduleNext()
    }

    /** Precedence + validation for the relay base URL this coordinator
     *  actually uses.
     *
     *  [hubValue] -- a paired LAN hub's URL (see [HubPrefs]) -- ALWAYS wins
     *  over everything else, when non-blank and it passes
     *  [requireTransportIsSafe] itself (the identical check every other
     *  candidate here is held to; a hub URL is `https://` by construction --
     *  see [parsePairingPayload] -- so this in practice never rejects a
     *  genuinely-paired hub, but a candidate this function trusted
     *  unconditionally is exactly the mistake [requireTransportIsSafe]
     *  exists to catch everywhere else). A device paired to a hub MUST talk
     *  to that hub and only that hub: Owner's cloud relay and each LAN hub
     *  run INDEPENDENT `sync_cursor` sequence spaces, so a device that
     *  switched between them mid-stream would hold one cursor against two
     *  different orderings, silently skipping or replaying events depending
     *  on which relay it asks next tick. (The Python side now guards this
     *  too -- retail schema v31's `SyncService.ensure_cursor_matches_relay`
     *  resets the cursor the moment the relay URL it's asked against changes
     *  (`internal_routes.py`'s `/_internal/cursor` route) -- but that is a
     *  server-side safety net for a client that already got this wrong, not
     *  a reason for the client to stop picking exactly one relay and
     *  staying on it.) A hub candidate that fails the safety check is never
     *  partially trusted either: it is dropped and precedence continues to
     *  [buildConfigValue]/[persistedValue] exactly as if no hub had been
     *  supplied.
     *
     *  Failing that, [buildConfigValue] (an operator's explicit build-time
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
    internal fun resolveRelayBaseUrl(buildConfigValue: String, persistedValue: String?, hubValue: String? = null): String {
        val hubCandidate = hubValue?.trim().orEmpty()
        if (hubCandidate.isNotBlank()) {
            try {
                requireTransportIsSafe(hubCandidate)
                return hubCandidate
            } catch (exc: SyncRelayClientError) {
                logError(
                    "Paired hub URL failed the same transport-safety check every other " +
                        "relay candidate is held to (${exc.reasonCode}); ignoring it and " +
                        "falling back to the next precedence level rather than trusting an " +
                        "unsafe hub URL.",
                    exc,
                )
            }
        }
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

    /** [resolveRelayBaseUrl] plus the matching pin, bundled as one
     *  [RelayTarget] -- the single seam [start] and [onHubPairingChanged]
     *  both go through, so the "only pin when the hub URL actually won
     *  precedence" rule ([start]'s old inline comment, now here) can only
     *  ever be expressed once. Mirrors [resolveRelayBaseUrl]'s own
     *  precedence exactly; this does not re-decide anything, it only pairs
     *  the winning url with its pin. */
    internal fun resolveRelayTarget(
        buildConfigValue: String,
        persistedValue: String?,
        hubBaseUrl: String?,
        hubPinValue: String?,
    ): RelayTarget {
        val url = resolveRelayBaseUrl(buildConfigValue, persistedValue, hubBaseUrl)
        // Only pin when the hub URL is the candidate that actually WON
        // precedence -- if it failed requireTransportIsSafe and
        // resolveRelayBaseUrl fell through to BuildConfig/persisted
        // instead, pinning against a hub key that isn't even the relay in
        // use would be meaningless at best and misleading at worst.
        val pin = if (hubBaseUrl != null && url == hubBaseUrl.trim()) {
            hubPinValue?.trim().orEmpty()
        } else {
            ""
        }
        return RelayTarget(url, pin)
    }

    fun stop() {
        synchronized(lock) {
            running = false
            timer?.cancel()
            timer = null
        }
    }

    /** Resets the catch-up bookkeeping the instant [target] actually
     *  changes -- called from both [start] (nothing to reset yet, but see
     *  [start]'s own cloud-since assignment) and [onHubPairingChanged]'s
     *  re-point branches. A caught-up observation, or an elapsed-time
     *  clock, against the OLD target says nothing about the new one: a
     *  device that just spent an hour catching up on the cloud and then
     *  joined a hub must not carry that hour's clock into some FUTURE
     *  fall-back-to-cloud decision, and a device that just left a hub for
     *  the cloud must start its grace period fresh, not from whenever it
     *  first booted. */
    private fun markTargetChanged(newTarget: RelayTarget) {
        cloudCaughtUpAtMillis = null
        cloudTargetSinceMillis = if (newTarget.spkiPin.isBlank()) clock() else null
    }

    /**
     * Re-points this coordinator's [target] the instant this device's hub
     * pairing changes -- pairs with [start]'s own precedence so a hub
     * pairing (or unpairing) that happens WHILE the loop is already running
     * takes effect on the very next tick, with **no app restart** (measured
     * gap, 2026-09-24: a real phone auto-joined a hub, HubPrefs held the
     * hub's URL and pin, yet the hub recorded 0 site_sync_nonces from it --
     * the phone kept syncing to its old Owner cloud URL -- until the app was
     * restarted, after which the hub recorded 37). Both
     * [RetailExtraScreens]'s manual pairing screen and
     * [HubAutoJoinService]'s automatic join call this the instant either one
     * learns of a new (or cleared) [HubPrefs] pairing.
     *
     * Deliberately does **not** cancel or reschedule [timer]: an in-flight
     * tick (or one about to fire) finishes against ITS OWN snapshot of
     * [target] -- taken once, atomically, by [runOnceAgainstCurrentTarget] --
     * which is harmless (the cloud/hub both dedup by event id, and the
     * local cursor re-binds to whichever relay the NEXT tick actually asks
     * against, via `ensure_cursor_matches_relay`). Cancelling and
     * rescheduling here would risk two ticks racing each other, or a tick
     * firing mid-cancel; leaving the timer alone means the worst case is
     * "the next tick already against the new target is at most one
     * existing backoff interval away" (300s at most) -- see [nextDelayMillis]
     * -- which this class's own doc names as an accepted latency, not a bug.
     */
    fun onHubPairingChanged(appContext: Context) {
        val hub = HubPrefs.getBaseUrl(appContext).takeIf { HubPrefs.isConfigured(appContext) }
        onHubPairingChanged(
            File(appContext.applicationContext.filesDir, "data"),
            hub,
            hub?.let { HubPrefs.getSpkiPin(appContext) },
        )
    }

    /** Context-free core of [onHubPairingChanged] -- see [start]'s own
     *  5-arg overload for why this module needs a Context-free seam at all
     *  (no Robolectric/Mockito here, so a plain-JVM test cannot construct a
     *  real `Context`). `internal`, not `private`, for that one reason. */
    internal fun onHubPairingChanged(identityDir: File, hubBaseUrl: String?, hubSpkiPinValue: String?) {
        val newTarget = resolveRelayTarget(lastBuildConfigValue, lastPersistedRelayBaseUrl, hubBaseUrl, hubSpkiPinValue)
        synchronized(lock) {
            if (!running) {
                // Never activated in time for start() to have anything to
                // run against (or start() genuinely had nothing) -- a hub
                // pairing arriving now is this device's FIRST target, e.g.
                // a LAN-only shop with no cloud relay at all. Nothing to
                // tear down; just begin.
                if (newTarget.baseUrl.isNotBlank()) {
                    startLocked(identityDir, newTarget)
                    markTargetChanged(newTarget)
                }
                return
            }
            if (newTarget == target) return
            if (newTarget.baseUrl.isBlank()) {
                // An unpair with no fallback relay at all (no BuildConfig,
                // nothing persisted) -- stop rather than run forever
                // against a blank url that would fail every tick.
                running = false
                timer?.cancel()
                timer = null
                target = newTarget
                return
            }
            target = newTarget
            markTargetChanged(newTarget)
        }
        logError(
            "Sync re-pointed to ${if (newTarget.spkiPin.isNotBlank()) "the paired hub" else "the cloud relay"} " +
                "(pinned=${newTarget.spkiPin.isNotBlank()}) with no restart.",
            null,
        )
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
            // ONE volatile read -- see [runOnceAgainstCurrentTarget]'s own
            // comment on why url and pin must never be read separately.
            val targetSnapshot = target
            val identitySnapshot = currentIdentity()
            val failure = eachStreamCatching("nudge push") { stream ->
                pushOnce(stream, localBaseUrl, internalSecret, targetSnapshot.baseUrl, identitySnapshot, targetSnapshot.spkiPin)
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
        // behavior for a build-time relay), while target.baseUrl alone
        // covers a persisted-discovery URL (or a paired hub) that [start]/
        // [onHubPairingChanged] resolved and is now running against, which
        // BuildConfig itself has no idea about.
        configured = BuildConfig.OWNER_SYNC_BASE_URL.isNotBlank() || target.baseUrl.isNotBlank(),
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

    /** Caller either holds [lock] (startLocked) or is [onTickFinished], which
     *  takes it -- so the running check, the new Timer and the [timer]
     *  publish are one step, never interleaved with [onHubPairingChanged] or
     *  [stop] on another thread. */
    private fun scheduleNext() {
        synchronized(lock) {
            if (!running) return
            val t = Timer("sync-coordinator", true)
            t.schedule(object : TimerTask() {
                override fun run() {
                    try {
                        runOnce()
                    } finally {
                        onTickFinished(t)
                    }
                }
            }, nextDelayMillis())
            timer = t
        }
    }

    /** The end of one tick of the chain driven by [finished]. Only the chain
     *  [timer] still names may schedule a successor.
     *
     *  WHY (verifier fix, 2026-09-24): this used to be an unconditional
     *  `scheduleNext()` in the task's `finally`, guarded only by [running].
     *  That was safe while `running` only ever went false -> true once per
     *  process ([stop] had no production caller). [onHubPairingChanged] made
     *  true -> false -> true reachable: a LAN-only phone that unpairs (no
     *  fallback relay, so the loop stops) and re-pairs (manual Connect or the
     *  auto-join tick, which starts a NEW chain) while a tick is still in
     *  flight -- and against an unreachable hub one tick can take a minute of
     *  SyncRelayClient retries, exactly when someone is re-pairing. The stale
     *  tick then saw running == true and forked a second self-rescheduling
     *  chain beside the new one: overlapping ticks for the rest of the
     *  process, breaking this class's "ticks never overlap" guarantee
     *  (double push/pull, double pull-apply). [stop] and the unpair branch
     *  null or replace [timer], so an orphaned chain can never match here.
     *  Pinned by `SyncCoordinatorRebindTest` K7, both halves: a stale chain
     *  stops, the live chain continues. `internal` only so K7 can drive the
     *  stale tick's ending deterministically. */
    internal fun onTickFinished(finished: Timer) {
        synchronized(lock) {
            if (timer === finished) scheduleNext()
        }
    }

    private fun runOnce() {
        runOnceAgainstCurrentTarget(
            localBaseUrl = ServerBootstrap.baseUrl(),
            internalSecret = ServerBootstrap.internalSecret(),
            identity = currentIdentity(),
        )
    }

    /** Reads [target] ONCE -- a single volatile read of the (url, pin) pair
     *  together -- and drives one tick against that exact snapshot. This is
     *  the seam that makes a mid-run [onHubPairingChanged] re-point safe:
     *  without it, a tick that read the url before a re-point and the pin
     *  after would sign a request to the OLD relay with the NEW hub's pin
     *  (or vice versa), which is exactly the kind of tear [RelayTarget]'s
     *  own class doc exists to rule out. `internal`, not `private`, so
     *  `SyncCoordinatorRebindTest` can drive a tick against whatever
     *  [target] currently holds without reaching into [scheduleNext]'s
     *  timer machinery. */
    internal fun runOnceAgainstCurrentTarget(localBaseUrl: String, internalSecret: String, identity: DeviceIdentity) {
        val t = target
        runOnce(localBaseUrl, internalSecret, t.baseUrl, identity, t.spkiPin)
    }

    /**
     * The actual dual-stream sync tick: pushes [SyncStream.ALL], then pulls
     * [SyncStream.ALL]. Parametrized (rather than reading [ServerBootstrap]/
     * [BuildConfig]/[currentIdentity] itself) purely so a plain-JVM test can
     * drive this exact function against a fake local+relay server --
     * [runOnceAgainstCurrentTarget] above is the only production caller and
     * always supplies the real values. `internal`, not `private`, for that
     * one reason, mirroring how [requireTransportIsSafe] in
     * SyncRelayClient.kt is exposed to its own test for the same reason.
     * [spkiPin] defaults to `""` so every existing 4-argument test call
     * site (this function's original signature, before the hub re-point
     * feature) is unaffected.
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
        spkiPin: String = "",
    ) {
        val pushFailure = eachStreamCatching("push") { stream ->
            pushOnce(stream, localBaseUrl, internalSecret, relayBaseUrl, identity, spkiPin)
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

        // Keyed by SyncStream.label rather than positional, so a stream that
        // THROWS partway through pullOnce (caught below by
        // eachStreamCatching) simply leaves no entry -- which reads as
        // null/"not caught up" in the [allBelowPage] check, exactly like a
        // stream that never got a chance to run at all. No separate
        // "did every stream even get invoked" bookkeeping is required.
        val pullCounts = mutableMapOf<String, Int?>()
        val pullFailure = eachStreamCatching("pull") { stream ->
            pullCounts[stream.label] = pullOnce(stream, localBaseUrl, internalSecret, relayBaseUrl, identity, spkiPin)
        }
        if (pullFailure == null) {
            recordSuccess(isPush = false)
        } else {
            val (streamLabel, exc) = pullFailure
            val n = recordFailure(isPush = false, streamLabel = streamLabel, exc = exc)
            logError("Sync pull FAILED this tick (first failing stream=$streamLabel, reason=" +
                     "${reasonOf(exc)}, consecutive pull failures=$n); retried on the next tick.", exc)
        }

        // Cloud "caught up" bookkeeping for [readyToJoinHub] -- see that
        // function's own doc comment for why a fast re-point must not join
        // a hub before this device's pre-hub history has actually left the
        // cloud. A stream that returned a FULL page (>= CLOUD_PULL_PAGE_LIMIT)
        // is guaranteed to have more behind it (Owner's own pull is
        // paginated at that exact limit), so "every stream returned a
        // partial page" is the only honest signal that nothing was left
        // behind this tick.
        val allBelowPage = SyncStream.ALL.all { stream ->
            val count = pullCounts[stream.label]
            count != null && count < CLOUD_PULL_PAGE_LIMIT
        }
        // relayBaseUrl == target.baseUrl guards against an IN-FLIGHT tick
        // (this one) finishing against a snapshot that a re-point has
        // since moved past -- see [onHubPairingChanged]'s own doc comment
        // on why the timer is never cancelled/rescheduled on a re-point.
        // Without this guard, a slow tick against the OLD cloud target
        // could mark "caught up" AFTER this device already re-pointed to a
        // hub, which would be a stale success wrongly credited to whatever
        // target happens to be live when this line runs.
        if (pullFailure == null && allBelowPage && spkiPin.isBlank() &&
            relayBaseUrl.isNotBlank() && relayBaseUrl == target.baseUrl
        ) {
            cloudCaughtUpAtMillis = clock()
        }
    }

    /**
     * Whether [HubAutoJoinService] may attempt a hub join right now --
     * closes part of gap 0.5/G3 (a hub's pre-hub/pre-upgrade history never
     * enters its own site log, so a phone that joins the hub before it
     * finished pulling ITS pre-hub history from the cloud would never see
     * it, because the cloud never hands the hub back its own events
     * either -- see the plan's own 0.5/0.4 findings). Always true when
     * there is nothing to catch up FROM:
     *  - the coordinator was never started, or has no relay at all (a
     *    fresh install with no BuildConfig/persisted URL yet) -- there is
     *    no cloud history to lose because there was never a cloud target;
     *  - [target] is ALREADY a paired hub (a second hub discovered while
     *    paired to a different one, or a re-check after joining) -- this
     *    device is not coming from the cloud at all;
     *  - a tick already observed a fully caught-up pull against the
     *    CURRENT cloud target (see [runOnce]'s tail).
     * Otherwise this is a grace period, not a hard gate: a device that can
     * never reach the cloud (no internet, Owner down) must still eventually
     * be allowed to join its shop's LAN hub, so after
     * [CLOUD_CATCHUP_GRACE_MS] since [target] became a cloud target, this
     * returns true regardless of whether a catch-up tick ever succeeded.
     */
    internal fun readyToJoinHub(nowMillis: Long = clock()): Boolean {
        val t = target
        if (!running || t.baseUrl.isBlank()) return true
        if (t.spkiPin.isNotBlank()) return true
        if (cloudCaughtUpAtMillis != null) return true
        val since = cloudTargetSinceMillis ?: return true
        return nowMillis - since >= CLOUD_CATCHUP_GRACE_MS
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

    /** Builds the [SyncRelayClient] for one push/pull attempt, against the
     *  exact ([relayBaseUrl], [spkiPin]) pair the caller snapshotted from
     *  [target] for THIS tick -- never read from the live [target] field
     *  directly, so a re-point mid-tick (see [onHubPairingChanged]) can
     *  never tear the url and pin the caller already committed to using
     *  together (`SyncCoordinatorRebindTest`'s K4 contract test pins this:
     *  the text between this function's signature and [pushOnce]'s must
     *  never reference the live field). When [spkiPin] is non-blank (this
     *  tick is running against a paired LAN hub), the SAME pinned
     *  [SpkiPinning] factory/trust-manager pair is threaded into BOTH of
     *  [SyncRelayClient]'s independent TLS seams -- `httpClient` (push(),
     *  OkHttp) AND `sslSocketFactory` (pull(), the raw Socket path that
     *  never goes near OkHttp at all). Pinning only one of the two would
     *  leave the other completely unpinned; see [SpkiPinning]'s class doc
     *  for the full reasoning and why that specific half-measure is the
     *  single most likely regression here -- [HubTransportContractTest]'s
     *  source-text test on this exact function exists to catch it.
     *  Otherwise (blank pin) this builds exactly the same client as before
     *  this feature existed. */
    private fun relayClient(installationId: String, relayBaseUrl: String, identity: DeviceIdentity, spkiPin: String): SyncRelayClient {
        relayClientObserver(relayBaseUrl, spkiPin)
        val config = SyncRelayClientConfig(baseUrl = relayBaseUrl)
        val pin = spkiPin
        if (pin.isBlank()) {
            return SyncRelayClient(config, identity, installationId)
        }
        val (factory, trustManager) = SpkiPinning.pinnedPair(pin)
        val pinnedHttpClient = OkHttpClient.Builder()
            .connectTimeout(config.timeoutSeconds, TimeUnit.SECONDS)
            .readTimeout(config.timeoutSeconds, TimeUnit.SECONDS)
            // OkHttp requires both the factory AND the trust manager
            // explicitly -- see SpkiPinning.pinnedPair's own doc comment.
            .sslSocketFactory(factory, trustManager)
            // Accepting every hostname here is NOT a weakening: the trust
            // manager backing `factory`/`trustManager` above has already
            // refused every peer except the one exact pinned key,
            // unconditionally, before this verifier ever runs -- the pin IS
            // the identity, not the name. Mirrors `verifyHostname = false`
            // below; see that SyncRelayClient constructor parameter's own
            // doc comment for the full reasoning, which mirrors
            // commercial_runtime/sync/site_relay/pinned_transport.py's
            // SpkiPinnedAdapter.
            .hostnameVerifier { _, _ -> true }
            .build()
        return SyncRelayClient(
            config,
            identity,
            installationId,
            httpClient = pinnedHttpClient,
            sslSocketFactory = factory,
            verifyHostname = false,
        )
    }

    private fun pushOnce(
        stream: SyncStream,
        localBaseUrl: String,
        internalSecret: String,
        relayBaseUrl: String,
        identity: DeviceIdentity,
        spkiPin: String = "",
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
        val client = relayClient(installationId, relayBaseUrl, identity, spkiPin)
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

    /** Returns the number of events THIS pull actually returned -- feeds
     *  [runOnce]'s "was this pull page full, or genuinely caught up" check
     *  (see [CLOUD_PULL_PAGE_LIMIT]) -- or `null` when this stream was
     *  skipped because no `installation_id` was available yet (never
     *  activated), which must never be confused with "returned zero
     *  events" by [readyToJoinHub]'s caller: a skipped stream proves
     *  nothing about whether the cloud has been drained. */
    private fun pullOnce(
        stream: SyncStream,
        localBaseUrl: String,
        internalSecret: String,
        relayBaseUrl: String,
        identity: DeviceIdentity,
        spkiPin: String = "",
    ): Int? {
        // relay_url (URL-encoded) lets the local backend's own
        // ensure_cursor_matches_relay (internal_routes.py's
        // /_internal/cursor route, retail schema v31) reset the cursor the
        // moment this device's effective relay actually changes -- e.g. a
        // device that stops talking to Owner's cloud relay and starts
        // talking to a paired LAN hub instead (or vice versa) must never
        // read a cursor that belonged to the OTHER relay's independent
        // sequence space (see resolveRelayBaseUrl's own doc comment for why
        // that matters). Optional on the Python side -- absent means "no
        // relay check, exactly the old behavior" -- so this is additive,
        // never a breaking parameter.
        val cursorPath = stream.prefix + "/_internal/cursor?relay_url=" +
            URLEncoder.encode(relayBaseUrl, "UTF-8")
        val cursor = getLocal(cursorPath, localBaseUrl, internalSecret)
        val installationId = cursor.stringOrNull("installation_id") ?: return null
        val since = cursor.get("since")?.asLong ?: 0L

        val result = relayClient(installationId, relayBaseUrl, identity, spkiPin).pull(since) // raises on failure
        // Forwarded verbatim (result.toString() re-serializes Gson's own
        // JsonElement tree, which preserves the original int/float text --
        // see jsonToCanonical()'s doc comment on why a generic Map<String,
        // Any?> round-trip would NOT) -- the local route applies it via the
        // exact same SyncService.apply_pull_result() pull_once() itself uses.
        postLocal(stream.prefix + "/_internal/pull-apply", result.toString(), localBaseUrl, internalSecret)
        return result.getAsJsonArray("events")?.size() ?: 0
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
