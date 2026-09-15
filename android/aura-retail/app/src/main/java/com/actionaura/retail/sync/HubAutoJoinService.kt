package com.actionaura.retail.sync

import android.content.Context
import android.os.Build
import android.util.Log
import com.actionaura.retail.licensing.DeviceIdentity
import com.actionaura.retail.licensing.LicensingCoordinator
import com.actionaura.retail.ui.screens.NEEDS_ACTIVATION_STATES
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.io.File

/**
 * Background driver for [HubDiscovery] -- the piece `HubDiscovery.kt`'s own
 * header names as a later task's job: "wiring a real [HubDiscovery.Transport]
 * ... and a real `persistHub` callback ... into this class from wherever the
 * app's actual `Context` and device identity live." This object is that
 * wiring, run on a recurring timer, mirroring
 * `commercial_runtime/sync/site_relay/autojoin.py`'s desktop equivalent (same
 * "an unactivated device does nothing at all, ever" non-negotiable) but
 * Kotlin-driven here because only Kotlin holds this device's Ed25519 signing
 * key -- Python on Android never does (see [DeviceIdentity]'s own class doc)
 * -- the identical reason [SyncCoordinator]'s push/pull loop is Kotlin-driven
 * on this platform and Python-driven on Windows.
 */
object HubAutoJoinService {

    private const val TAG = "HubAutoJoin"

    /**
     * 60 seconds, not faster. Joining a hub is a once-in-a-device's-life
     * event: after the first success, [HubPrefs.isConfigured] short-circuits
     * every later tick before it ever opens a socket (see [runOnce]'s first
     * line), so polling harder than this buys nothing but a wifi radio woken
     * up more often. Independent of `LicenseCheckInCoordinator.INTERVAL_MS`
     * (5 minutes -- a real network round trip to Owner) and
     * [SyncCoordinator]'s own push/pull cadence: a discovery tick is at most
     * one local UDP listen plus two LAN HTTP calls, never a cloud round trip,
     * so a shorter interval than either of those is still cheap.
     */
    const val INTERVAL_MS: Long = 60_000L

    @Volatile private var job: Job? = null
    private val lock = Object()

    /**
     * Last thing [note] logged, so a tick that reaches the SAME conclusion as
     * the previous one stays silent.
     *
     * Every early return in [runOnce] used to be silent, and that cost real
     * time: a phone that was never joining looked EXACTLY like a phone that
     * had joined and had nothing to do -- both produced an empty log -- so
     * there was no way to tell "the loop never started", "it started and the
     * device looked unactivated", and "it ran and heard no beacon" apart
     * without rebuilding with printf debugging. A support engineer on a shop
     * floor has that problem permanently.
     *
     * Deduped rather than logged every tick because the steady states here
     * are PERMANENT: once a device is paired, `alreadyJoined` is true on
     * every tick for the rest of the install's life, and logging that once a
     * minute forever would bury the one line anyone actually needs. A change
     * of conclusion is the event worth recording; a repeat is not.
     */
    @Volatile private var lastNote: String? = null

    private fun note(message: String) {
        if (message == lastNote) return
        lastNote = message
        Log.i(TAG, message)
    }

    /**
     * THE PURE DECISION, extracted so it is testable with no `Context` at all
     * (see `HubAutoJoinServiceTest`). An attempt is made iff this device is
     * activated AND is not already joined to a hub -- mirrors
     * `autojoin.py::discover_and_join`'s own two upfront short-circuits
     * (`NOT_ACTIVATED`, `ALREADY_JOINED`) collapsed into one boolean, since
     * unlike the Python version both facts are known here BEFORE
     * [HubDiscovery] is ever constructed, not read from inside it.
     */
    internal fun shouldAttemptJoin(activated: Boolean, alreadyJoined: Boolean): Boolean =
        activated && !alreadyJoined

    /**
     * Starts the recurring tick. Safe to call more than once -- a repeat call
     * while already running is a no-op, mirroring
     * `LicenseCheckInCoordinator.start()`'s identical guard.
     */
    fun start(appContext: Context) {
        synchronized(lock) {
            if (job?.isActive == true) return
            Log.i(TAG, "Hub auto-join loop starting.")
            val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
            job = scope.launch {
                while (isActive) {
                    runOnce(appContext.applicationContext)
                    delay(INTERVAL_MS)
                }
            }
        }
    }

    /** Cancels the loop cleanly. Safe to call when not running. */
    fun stop() {
        synchronized(lock) {
            job?.cancel()
            job = null
        }
    }

    /**
     * One tick. NEVER THROWS OUT OF THIS FUNCTION -- this is unattended
     * background work: an exception escaping a `launch` coroutine's body
     * completes it exceptionally, which would silently end the `while
     * (isActive)` loop in [start] and this device would never join a hub
     * again for the rest of the process's life, with nothing to point at.
     * Every failure below -- a bad licence read, a socket error inside
     * [HubTransport], a malformed hub response -- is caught here, logged at
     * INFO (never at a level that would page anyone; a missed beacon is
     * routine, not an incident), and left for the next tick to retry.
     */
    private suspend fun runOnce(appContext: Context) {
        try {
            // Cheap check FIRST, before any socket -- see HubPrefs
            // .isConfigured's own doc comment: a device already paired to a
            // hub has nothing left to do here, ever, until HubPrefs.clear()
            // runs (a manual unpair, elsewhere) makes it worth checking
            // again.
            val alreadyJoined = HubPrefs.isConfigured(appContext)
            if (alreadyJoined) {
                note("Already paired to a hub; nothing to do.")
                return
            }

            // THE ACTIVATION CHECK IS THE LICENCE IDENTITY READ ITSELF, and
            // deliberately no longer `/api/licensing/status`. Measured on a
            // real phone: a device holding a perfectly good ACTIVE_OFFLINE
            // assertion reported "Not activated yet" on every single tick,
            // forever, and never joined its shop.
            //
            // The cause is that `/status` answers `NOT_CONFIGURED`
            // unconditionally whenever no Owner base URL is configured
            // (licensing_contracts/routes.py's `_not_configured_response`,
            // returned BEFORE the licence database is ever opened). That is
            // correct for what /status is for -- reporting the commercial
            // relationship -- but it is the wrong question here. "Do I hold
            // an Owner-signed assertion naming a shop" is a purely LOCAL
            // fact, true or false regardless of whether this build was cut
            // with an Owner URL, and asking a URL-gated route for it makes
            // an offline-first feature depend on cloud configuration it does
            // not need.
            //
            // `/_internal/license-identity` reads licensing.db directly and
            // is not URL-gated, so it answers the question actually being
            // asked. It is also the value attemptJoin needs anyway, so the
            // gate and the payload are now one read instead of two.
            val identityResponse = readLicenseIdentity(appContext)
            val activated = parseLicenseIdentity(identityResponse) != null
            if (!shouldAttemptJoin(activated, alreadyJoined)) {
                note("No activated licence on this device yet; no shop to join.")
                return
            }

            attemptJoin(appContext, identityResponse)
        } catch (exc: Exception) {
            Log.i(TAG, "Hub auto-join tick failed; the next tick will retry.", exc)
        }
    }

    /**
     * Reads this device's own licence identity over the local
     * `/_internal/license-identity` route, returning the raw response map
     * (or an empty map on ANY failure -- unreachable backend, 403, 400
     * NO_LOCAL_LICENSE, malformed body). [parseLicenseIdentity] is what
     * decides whether what came back is usable, so every failure funnels to
     * the same fail-closed answer without this function needing to know
     * which one happened.
     *
     * DELIBERATELY NOT `/api/licensing/status`, which this used to call.
     * See [runOnce]'s comment at the call site for the measurement that
     * forced the change: /status answers NOT_CONFIGURED whenever no Owner
     * URL is configured, before it ever opens the licence database, so a
     * device holding a genuine ACTIVE_OFFLINE assertion read as
     * unactivated forever.
     */
    private suspend fun readLicenseIdentity(appContext: Context): Map<String, Any?> = try {
        LicensingCoordinator(appContext).licenseIdentity()
    } catch (exc: Exception) {
        emptyMap()
    }

    /**
     * Runs one [HubDiscovery.discoverAndJoin] attempt with this device's own
     * identity. Every field is read fresh on every call (mirroring
     * [SyncCoordinator]'s own "read licence state fresh every tick" idiom),
     * so a licence that activates, or a hub that gets paired by some other
     * path, while this loop is already running is picked up on the very next
     * tick with no restart required.
     */
    private suspend fun attemptJoin(
        appContext: Context,
        identityResponse: Map<String, Any?>,
    ) {
        // THE INSTALLATION ID COMES OUT OF THE ASSERTION, not /status.
        //
        // This is the second half of the same bug the licence check above
        // already fixed, and missing it made that fix useless: /status
        // answers NOT_CONFIGURED -- a body with no `installation_id` key at
        // all -- whenever no Owner URL is configured, so this resolved to
        // "" and the phone posted a BLANK installation id to the hub.
        //
        // Measured end to end. The phone logged:
        //     Hub join attempt declined: JOIN_REJECTED_400
        // while replaying the very same join from another machine, changing
        // nothing but sending the assertion's real installation id,
        // returned:
        //     POST /join -> HTTP 200  {"joined":true}
        // Same assertion, same device key, same licence, same hub. The only
        // difference was this field.
        //
        // Reading it from the assertion payload is also the only source
        // that CANNOT disagree with the rest of the request: the hub feeds
        // the id we send into `verify_assertion` as
        // `expected_installation_id` and refuses any mismatch (join.py), so
        // taking it from anywhere other than the assertion we are sending
        // alongside it is inviting exactly this failure.
        val installationId = parseInstallationId(identityResponse) ?: ""

        // The SAME on-disk device key LicensingCoordinator and
        // SyncCoordinator already use (File(filesDir, "data")) -- NOT
        // DeviceIdentity's plain Context convenience constructor, which
        // resolves one directory higher and would mint a key Owner never
        // activated and this device's own sync never signs with. Same
        // hazard, same fix, documented in the same words in
        // RetailExtraScreens.kt's own pairing call and SyncCoordinator
        // .start()'s comment on `identityDir`.
        val identity = DeviceIdentity(File(appContext.filesDir, "data"))
        val devicePublicKey = try {
            identity.getPublicKeyB64()
        } catch (exc: Exception) {
            // No key on disk (never activated), or it is unreadable
            // (DeviceIdentity.LocalStateCorruptError). isActivated() above
            // should already have refused this call in the ordinary case,
            // but a key can go missing/corrupt independently of the
            // licensing state machine's own idea of "activated" -- treated
            // identically either way: nothing to join with this tick.
            return
        }

        // THE GAP CLOSED: `HubDiscovery.discoverAndJoin` needs THIS device's
        // own `license_public_id` (the shop boundary -- see that function's
        // own doc comment) and its own Owner-signed `assertion` envelope
        // (sent to the hub's `/join` verbatim). Both live ONLY inside
        // Python's `licensing.db`, as `LicenseStateRecord
        // .assertion_envelope_json` -- `license_public_id` is not even its
        // own column there; it exists only nested inside that envelope's
        // signed payload. Read here over the SAME local HTTP hop, the SAME
        // `X-Aura-Internal-Secret`, and the SAME `OkHttpClient` every other
        // `_internal/*` call in this app already uses (see
        // [LicensingCoordinator.licenseIdentity]) -- Kotlin deliberately
        // never opens `licensing.db` itself. Python is the only holder of
        // the independently-VERIFIED copy: the envelope on disk has already
        // survived Owner's signature check and the capability-guard state
        // machine, and a second, parallel read of the raw file from Kotlin
        // would carry no such guarantee -- it would just be a second,
        // competing notion of "trusted", which is exactly the kind of
        // divergence this codebase's licensing model goes out of its way to
        // avoid (see LicensingCoordinator's own class doc: Python does "the
        // only state mutation that counts").
        //
        // Fails closed on every path via [parseLicenseIdentity]: a thrown
        // exception (server unreachable), a 403 (wrong/missing secret), a
        // 400 NO_LOCAL_LICENSE (no activated licence, or a stored envelope
        // this route itself could not make sense of), or a malformed/empty
        // body all collapse to the SAME blank pair used before this gap was
        // closed -- `HubDiscovery.discoverAndJoin`'s own blank-license check
        // then reports `HubDiscoveryResult.Failed("NO_LOCAL_LICENSE")`
        // without this tick ever opening a beacon socket, exactly as it did
        // when these two values were hardcoded blanks. No fabricated
        // identity is ever substituted for a read that failed.
        val result = joinUsingLicenseIdentity(
            // The Context is what lets HubTransport hold a wifi
            // MulticastLock for the beacon listen. Without it the listen
            // is silently dead on Android -- see HubTransport
            // .acquireBeaconLock for the measurement.
            discovery = HubDiscovery(HubTransport(appContext)),
            identityResponse = identityResponse,
            myInstallationId = installationId,
            myDevicePublicKey = devicePublicKey,
            label = Build.MODEL,
            persistHub = { url, pin, hubKey, hubId ->
                HubPrefs.set(appContext, url, pin, hubKey, hubId)
            },
        )

        // SAY WHAT HAPPENED. This used to discard the result entirely, which
        // made the whole loop undiagnosable: a tick that ran and declined
        // looked EXACTLY like a tick that never ran at all -- both produce a
        // silent log. That cost two full rebuild-and-observe cycles on real
        // hardware before it was obvious the two cases could not be told
        // apart, and it would have cost a support engineer far more on a
        // shop floor, where the only symptom is "the tablet never picks up
        // the till's prices" with nothing anywhere to look at.
        //
        // Safe to log by construction, not by luck: every [HubDiscoveryResult]
        // reason is a short fixed code -- [HubDiscoveryResult.Failed]'s own
        // doc comment guarantees "never raw exception text, and never
        // assertion/key content". The hub's base URL is a LAN address that
        // the hub itself broadcasts unencrypted in its beacon, so it is not
        // a secret either. Nothing here can leak key material; if a future
        // result type carries anything richer, it must not be logged whole.
        when (result) {
            is HubDiscoveryResult.Joined ->
                note("Joined the shop hub at ${result.hub.baseUrl}.")
            is HubDiscoveryResult.NoHubFound ->
                // Routine, not an incident: no hub broadcasting yet, or out
                // of earshot on this sweep. Still logged, because "heard
                // nothing" and "heard something and refused it" are the two
                // things anyone diagnosing this needs to tell apart.
                note("No hub beacon heard on this sweep.")
            is HubDiscoveryResult.DifferentShop ->
                note("Found a hub at ${result.hub.baseUrl} but it " +
                    "belongs to a different licence; not joining.")
            is HubDiscoveryResult.Failed ->
                note("Hub join attempt declined: ${result.reason}")
        }
    }

    /**
     * THE PURE PARSE, extracted the same way [shouldAttemptJoin] is so it is
     * testable with plain `Map` fixtures and no `Context`/network at all
     * (see `HubAutoJoinServiceTest`). Turns the raw response map from
     * [LicensingCoordinator.licenseIdentity] into a verified
     * `(licensePublicId, assertionEnvelopeJson)` pair, or `null` for
     * anything not usable: a `{"reason_code", ...}` failure body (400 or
     * 403 -- neither carries these two keys at all), a malformed/empty map,
     * or either field present but blank.
     *
     * Reads `assertion_envelope_json` as a plain `String` straight out of
     * the map -- never re-parsed into a `JsonObject` and re-serialized here
     * -- so the value this returns is byte-identical to what the route read
     * off disk. See [LicensingCoordinator.licenseIdentity]'s own doc
     * comment for why that matters: the hub verifies a signature over these
     * exact bytes.
     */
    /**
     * This device's own installation id, read out of the SAME assertion
     * envelope that will be sent to `/join` alongside it.
     *
     * Parsed with [org.json.JSONObject] rather than re-using a Gson model:
     * the envelope must reach the hub as the exact bytes it was stored as
     * (see [LicensingCoordinator.licenseIdentity]), so it is only ever
     * INSPECTED here, never decoded-and-re-encoded.
     *
     * Returns null for anything unusable -- absent envelope, unparseable
     * JSON, no payload, no id -- which the caller turns into the same blank
     * that [HubDiscovery.discoverAndJoin] already fails closed on.
     */
    internal fun parseInstallationId(response: Map<String, Any?>): String? {
        val envelopeJson = response["assertion_envelope_json"] as? String ?: return null
        return try {
            val payload = org.json.JSONObject(envelopeJson).optJSONObject("payload")
                ?: return null
            payload.optString("installation_public_id").takeIf { it.isNotBlank() }
        } catch (exc: Exception) {
            null
        }
    }

    internal fun parseLicenseIdentity(response: Map<String, Any?>): Pair<String, String>? {
        val licensePublicId = response["license_public_id"] as? String
        val assertionEnvelopeJson = response["assertion_envelope_json"] as? String
        if (licensePublicId.isNullOrBlank() || assertionEnvelopeJson.isNullOrBlank()) {
            return null
        }
        return licensePublicId to assertionEnvelopeJson
    }

    /**
     * Wires a raw [identityResponse] into one [HubDiscovery.discoverAndJoin]
     * call via [parseLicenseIdentity] -- extracted (same reasoning as
     * [shouldAttemptJoin] and [parseLicenseIdentity]) so this exact "read
     * identity, refuse to join if it doesn't check out" composition is
     * testable against an injected [Transport], with no `Context` and no
     * real socket, the same way [HubDiscovery] itself is already tested. An
     * unusable [identityResponse] (see [parseLicenseIdentity]) becomes the
     * SAME blank pair `discoverAndJoin` has always failed closed on, so a
     * bad read never opens a beacon socket, let alone attempts a join.
     */
    internal fun joinUsingLicenseIdentity(
        discovery: HubDiscovery,
        identityResponse: Map<String, Any?>,
        myInstallationId: String,
        myDevicePublicKey: String,
        label: String,
        persistHub: (baseUrl: String, spkiPin: String, hubDevicePublicKey: String, hubInstallationId: String) -> Unit,
    ): HubDiscoveryResult {
        val identity = parseLicenseIdentity(identityResponse)
        return discovery.discoverAndJoin(
            myLicensePublicId = identity?.first ?: "",
            myInstallationId = myInstallationId,
            myDevicePublicKey = myDevicePublicKey,
            myAssertionEnvelopeJson = identity?.second ?: "",
            label = label,
            persistHub = persistHub,
        )
    }
}
