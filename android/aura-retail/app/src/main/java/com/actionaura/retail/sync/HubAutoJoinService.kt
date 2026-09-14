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
            if (alreadyJoined) return

            val activated = isActivated(appContext)
            if (!shouldAttemptJoin(activated, alreadyJoined)) return

            attemptJoin(appContext)
        } catch (exc: Exception) {
            Log.i(TAG, "Hub auto-join tick failed; the next tick will retry.", exc)
        }
    }

    /**
     * Reads this device's own licence state the SAME way `AppRoot`'s own
     * boot gate does -- `LicensingCoordinator(ctx).status()["current_state"]
     * as? String`, compared against the SAME `NEEDS_ACTIVATION_STATES` set
     * `LicensingScreen.kt` already defines (`"NOT_CONFIGURED"`,
     * `"ACTIVATION_REQUIRED"`, `"ACTIVATING"`) -- never a second, divergent
     * notion of "activated" invented here. A failed or unreadable status
     * call is treated as NOT activated: the same fail-closed posture
     * `HubDiscovery.discoverAndJoin`'s own blank-license check already
     * takes -- no licence confirmed, no shop to join.
     */
    private suspend fun isActivated(appContext: Context): Boolean {
        val status = try {
            LicensingCoordinator(appContext).status()
        } catch (exc: Exception) {
            null
        }
        val currentState = status?.get("current_state") as? String
        return currentState != null && currentState !in NEEDS_ACTIVATION_STATES
    }

    /**
     * Runs one [HubDiscovery.discoverAndJoin] attempt with this device's own
     * identity. Every field is read fresh on every call (mirroring
     * [SyncCoordinator]'s own "read licence state fresh every tick" idiom),
     * so a licence that activates, or a hub that gets paired by some other
     * path, while this loop is already running is picked up on the very next
     * tick with no restart required.
     */
    private suspend fun attemptJoin(appContext: Context) {
        val status = try {
            LicensingCoordinator(appContext).status()
        } catch (exc: Exception) {
            emptyMap<String, Any?>()
        }
        val installationId = status["installation_id"] as? String ?: ""

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
        val identityResponse = try {
            LicensingCoordinator(appContext).licenseIdentity()
        } catch (exc: Exception) {
            emptyMap<String, Any?>()
        }

        joinUsingLicenseIdentity(
            discovery = HubDiscovery(HubTransport()),
            identityResponse = identityResponse,
            myInstallationId = installationId,
            myDevicePublicKey = devicePublicKey,
            label = Build.MODEL,
            persistHub = { url, pin, hubKey, hubId ->
                HubPrefs.set(appContext, url, pin, hubKey, hubId)
            },
        )
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
