package com.actionaura.retail.sync

import com.google.gson.JsonObject
import com.google.gson.JsonParser

/**
 * Fully-automatic LAN hub discovery and self-join.
 *
 * This is the Android/Kotlin side of the flow `commercial_runtime/sync/
 * site_relay/beacon.py` (discovery) and the Owner-facing `/api/sync/v1/
 * identity` + `/api/sync/v1/join` routes (membership) implement on the hub
 * side. Read `beacon.py`'s module docstring before touching this file -- the
 * wire format below, and the "a beacon is a pointer, not a credential"
 * argument, are taken directly from it, not restated independently.
 *
 * THE PROBLEM THIS REPLACES: a shop owner pasting a hub's IP/port, or
 * scanning a QR code, into every new till before it can sync. The owner has
 * ruled that path out for the common case -- LAN sync must be fully
 * automatic, and the shared Owner-issued LICENCE (not an operator-entered
 * secret) is what decides which shop a device belongs to. The Settings paste
 * field in `RetailExtraScreens.kt` remains as a fallback for whatever this
 * automatic path cannot cover; this file IS the automatic path.
 *
 * THE FLOW, end to end:
 *   1. DISCOVER -- listen for the hub's signed UDP beacon (`installation_id`,
 *      `url`, `spki_pin`, plus `timestamp`/`signature`, which this module
 *      does not act on -- see [parseBeacon]'s doc comment for why not).
 *   2. FETCH IDENTITY -- open a TLS connection to the advertised URL,
 *      pinned to the advertised SPKI *provisionally*, and read
 *      `GET /api/sync/v1/identity`.
 *   3. VERIFY MEMBERSHIP -- trust the hub if and only if the identity
 *      response's Owner-signed assertion names THIS device's own
 *      `license_public_id` (see [shouldJoin]).
 *   4. JOIN -- `POST /api/sync/v1/join` with this device's own installation
 *      id, public key and assertion; the hub performs the mirror-image
 *      check on its own side.
 *   5. On success, persist the pairing (see [HubDiscovery.discoverAndJoin]'s
 *      `persistHub` parameter).
 *
 * THIS FILE IS INTENTIONALLY SELF-CONTAINED. It never touches
 * `SyncCoordinator.kt`, `SyncRelayClient.kt`, `SpkiPinning.kt`, `HubPrefs.kt`,
 * or any real Android `Context`/socket/TLS handshake -- every external
 * effect (receiving a UDP datagram, opening a pinned TLS connection,
 * persisting a successful pairing) is expressed as an injected function, so
 * this class can be exercised end-to-end by a plain-JVM unit test. A later
 * task is responsible for wiring a real [Transport] (a real `DatagramSocket`
 * plus [SpkiPinning]-pinned HTTP calls) and a real `persistHub` callback
 * (backed by [HubPrefs.set]) into this class from wherever the app's actual
 * `Context` and device identity live.
 */

/**
 * One hub this device has heard a beacon from, before membership has been
 * checked against this device's own licence. Mirrors the three beacon
 * fields this module actually acts on -- see [parseBeacon].
 */
data class DiscoveredHub(
    val installationId: String,
    val baseUrl: String,
    val spkiPin: String,
)

/**
 * Everything [HubDiscovery] needs from the outside world, abstracted behind
 * plain functions so a test never touches a real socket or a real TLS
 * handshake -- the same "inject the transport, keep the logic pure" shape
 * [SyncRelayClient] already uses for its own `httpClient`/`sslSocketFactory`
 * constructor parameters.
 *
 * *** PINNING IS THE IMPLEMENTOR'S RESPONSIBILITY, NOT THIS INTERFACE'S ***
 * [getIdentity] and [postJoin] are each handed the [DiscoveredHub.spkiPin]
 * the beacon advertised, deliberately as a parameter rather than something
 * baked into a pre-built HTTP client: a real implementation of this
 * interface MUST build its TLS connection with `SpkiPinning.pinnedPair(
 * spkiPin)`, hostname verification OFF -- exactly as [SpkiPinning]'s class
 * doc and `commercial_runtime/sync/site_relay/pinned_transport.py` both
 * argue. The hub's certificate is self-signed and deliberately carries no
 * LAN IP in its SAN (its address is DHCP-assigned and unstable), so ordinary
 * hostname verification would reject a correctly-pinned hub outright; the
 * pin -- one exact key, learned from the beacon and confirmed by a
 * successful handshake -- is a STRICTER identity check than hostname
 * matching, not a weaker one. This module never performs that handshake
 * itself; it only defines the seam a real implementation must fill in, in a
 * later task.
 */
interface Transport {
    /**
     * Blocks up to [timeoutMillis] for one UDP beacon datagram (see
     * `beacon.py::listen_for_beacon`), returning its raw payload, or `null`
     * if nothing arrived before the deadline. Does not filter or validate
     * the datagram in any way -- that is [parseBeacon]'s job, not the
     * transport's.
     */
    fun listenForBeacon(timeoutMillis: Long): String?

    /**
     * `GET /api/sync/v1/identity` on [baseUrl], over a connection
     * provisionally pinned to [spkiPin] (see the interface doc above).
     * Returns the raw JSON response body.
     */
    fun getIdentity(baseUrl: String, spkiPin: String): String

    /**
     * `POST /api/sync/v1/join` on [baseUrl] with [body] as the raw JSON
     * request payload, over a connection pinned to [spkiPin]. Returns the
     * HTTP status code.
     */
    fun postJoin(baseUrl: String, spkiPin: String, body: String): Int
}

/**
 * The UDP port `commercial_runtime/sync/site_relay/beacon.py::BEACON_PORT`
 * broadcasts and listens on -- must stay numerically identical to that
 * constant; the hub and every paired device agree on it with no
 * configuration of their own.
 */
const val BEACON_PORT = 45455

/**
 * How long one discovery attempt listens before giving up. `beacon.py::
 * BEACON_INTERVAL_SECONDS` (5s) is how often the hub re-broadcasts; waiting
 * roughly twice that gives a fair chance of catching a beacon even if the
 * very first one is missed (e.g. arrives mid-bind).
 */
private const val DEFAULT_BEACON_TIMEOUT_MILLIS = 10_000L

/**
 * The exact field set `beacon.py::REQUIRED_BEACON_FIELDS` requires, kept
 * textually identical so a beacon this module rejects as malformed is
 * rejected for the same reason the Python listener would flag it -- not a
 * divergent Kotlin-side notion of "well-formed". Only three of these five
 * end up in [DiscoveredHub] ([parseBeacon] still requires all five present).
 */
private val REQUIRED_BEACON_FIELDS = listOf("installation_id", "url", "spki_pin", "timestamp", "signature")

/**
 * `beacon.py::BEACON_MAX_DATAGRAM_BYTES` -- enforced first, before any JSON
 * parsing is attempted, for the identical reason that module's docstring
 * gives: a UDP listener that runs a JSON parser over an attacker-chosen
 * payload of unbounded size is a denial-of-service surface on a port every
 * device on the shop wifi can reach.
 */
private const val BEACON_MAX_DATAGRAM_BYTES = 1024

/**
 * Parses one raw beacon datagram into a [DiscoveredHub], or `null` if it is
 * malformed, incomplete, or oversized. Never throws.
 *
 * *** A BEACON IS A POINTER, NOT A CREDENTIAL -- THIS FUNCTION DOES NOT AND
 * MUST NOT VERIFY THE BEACON'S SIGNATURE ***
 * `beacon.py::parse_beacon` verifies the beacon's Ed25519 signature against
 * a hub public key the caller already learned at a PRIOR pairing. This
 * module has no such prior trust to check against: discovering a hub for the
 * first time, by licence alone, is exactly the case where this device does
 * NOT yet know the hub's key. Anyone on the wifi can send a UDP broadcast --
 * that is fine, because a forged or replayed beacon can only ever point this
 * device at the WRONG address; it grants no trust by itself. The pinned TLS
 * connection [Transport.getIdentity] opens against the beacon's advertised
 * [DiscoveredHub.spkiPin], and the licence check [shouldJoin] runs against
 * the Owner-signed assertion fetched over that connection, are what actually
 * decide trust. Treating "the beacon parsed cleanly" as "the beacon is
 * trustworthy" is precisely the mistake this comment exists to head off for
 * whoever reads this function next -- verifying a beacon's signature would
 * not have changed that; there is no key to verify it against yet.
 *
 * Still validates every field `beacon.py::REQUIRED_BEACON_FIELDS` demands --
 * not just the three [DiscoveredHub] carries -- and the identical size cap,
 * so a datagram this function accepts really is the wire format that module
 * documents, not a superficially similar one that only happens to have the
 * fields this module reads.
 */
fun parseBeacon(raw: String): DiscoveredHub? {
    // Size cap first, before a single byte reaches the JSON parser -- see
    // BEACON_MAX_DATAGRAM_BYTES's doc comment.
    if (raw.toByteArray(Charsets.UTF_8).size > BEACON_MAX_DATAGRAM_BYTES) return null

    return try {
        val element = JsonParser.parseString(raw)
        if (!element.isJsonObject) return null
        val obj = element.asJsonObject

        fun field(key: String): String? =
            obj.get(key)?.takeUnless { it.isJsonNull }?.asString?.takeUnless { it.isBlank() }

        val installationId = field("installation_id")
        val url = field("url")
        val spkiPin = field("spki_pin")
        val timestamp = field("timestamp")
        val signature = field("signature")
        if (installationId == null || url == null || spkiPin == null || timestamp == null || signature == null) {
            return null
        }

        DiscoveredHub(installationId = installationId, baseUrl = url, spkiPin = spkiPin)
    } catch (malformed: Exception) {
        // Any parser exception (invalid JSON, or a required field present
        // but of the wrong element type, e.g. an object where a string was
        // expected) means "not a valid beacon" -- never an uncaught
        // exception a caller's discovery loop would have to guard against
        // separately.
        null
    }
}

/**
 * Extracts `license_public_id` from an Owner-signed assertion envelope's
 * PAYLOAD -- `{"payload": {..., "license_public_id": "...", ...},
 * "signing_key_id": "...", "algorithm": "...", "signature": "..."}`, the
 * exact envelope shape `licensing_contracts/assertion_verifier.py::
 * verify_assertion` reads (`envelope["payload"]`, `envelope["signing_key_
 * id"]`, `envelope["algorithm"]`, `envelope["signature"]` -- confirmed
 * against that function's own unpacking). Returns `null` for anything
 * malformed rather than throwing.
 */
fun licenseIdOf(assertionEnvelopeJson: String): String? {
    return try {
        val envelope = JsonParser.parseString(assertionEnvelopeJson)
        if (!envelope.isJsonObject) return null
        val payloadElement = envelope.asJsonObject.get("payload")?.takeUnless { it.isJsonNull } ?: return null
        if (!payloadElement.isJsonObject) return null
        payloadElement.asJsonObject.get("license_public_id")
            ?.takeUnless { it.isJsonNull }
            ?.asString
            ?.takeUnless { it.isBlank() }
    } catch (malformed: Exception) {
        null
    }
}

/**
 * THE SHOP BOUNDARY. This function is the entire replacement for an
 * operator-typed pairing code: a hub is trusted if and only if the
 * `license_public_id` inside its Owner-signed assertion equals THIS
 * device's own [myLicenseId]. Any other value -- a different licence, a
 * missing one, or an unparseable identity response -- means "a different
 * shop, or a hub this device cannot make sense of", and both are refused
 * identically. There is no partial-trust outcome here: either the licences
 * match, or [HubDiscovery.discoverAndJoin] must never offer this device to
 * that hub.
 *
 * [hubIdentityJson] is the raw body `GET /api/sync/v1/identity` returned --
 * `{"installation_id": "...", "device_public_key": "...", "assertion":
 * {...envelope...}}` -- this function reads the `assertion` sub-object back
 * out of it and defers to [licenseIdOf] for the actual payload extraction
 * rather than duplicating that parsing here.
 */
fun shouldJoin(hubIdentityJson: String, myLicenseId: String): Boolean {
    if (myLicenseId.isBlank()) {
        // Defensive only -- HubDiscovery.discoverAndJoin already refuses to
        // run at all without a local licence (see its own doc comment). A
        // blank myLicenseId must never be treated as "matches everything"
        // if this function is ever called directly.
        return false
    }

    val hubLicenseId = try {
        val root = JsonParser.parseString(hubIdentityJson)
        if (!root.isJsonObject) return false
        val assertionElement = root.asJsonObject.get("assertion")?.takeUnless { it.isJsonNull } ?: return false
        licenseIdOf(assertionElement.toString())
    } catch (malformed: Exception) {
        null
    }

    return hubLicenseId != null && hubLicenseId == myLicenseId
}

/** Outcome of one [HubDiscovery.discoverAndJoin] attempt. */
sealed interface HubDiscoveryResult {
    /**
     * Successfully joined [hub] -- its address/pin/identity have already
     * been persisted via the caller-supplied `persistHub` callback by the
     * time this is returned.
     */
    data class Joined(val hub: DiscoveredHub) : HubDiscoveryResult

    /**
     * No beacon arrived before the listen timeout elapsed. Not an error --
     * a LAN scan with no hub broadcasting yet, or one temporarily out of
     * earshot, lands here.
     */
    data object NoHubFound : HubDiscoveryResult

    /**
     * A hub answered, but its Owner-signed assertion names a different
     * `license_public_id` than this device's own -- see [shouldJoin]. This
     * device was never offered to that hub: [Transport.postJoin] is
     * guaranteed not to have been called for this attempt.
     */
    data class DifferentShop(val hub: DiscoveredHub) : HubDiscoveryResult

    /**
     * Something else went wrong: network failure, a malformed response, or
     * the hub's own `/join` rejected this device. [reason] is a short,
     * stable code -- never raw exception text, and never assertion/key
     * content (see [HubDiscovery.discoverAndJoin]'s "never logs" rule).
     */
    data class Failed(val reason: String) : HubDiscoveryResult
}

/**
 * Runs the automatic discover -> fetch identity -> verify membership -> join
 * sequence described in this file's header, against an injected [Transport]
 * so no real socket or TLS handshake is ever touched by a test.
 */
class HubDiscovery(
    private val transport: Transport,
    private val listenPort: Int = BEACON_PORT,
) {
    init {
        // listenPort carries no direct effect on the injected Transport
        // (which already encapsulates its own socket) -- it exists for
        // parity with beacon.py::BEACON_PORT and so a real Transport
        // implementation's own construction can be checked against the same
        // value this class was configured with. Validated here anyway,
        // defensively, the same way a real DatagramSocket bind would refuse
        // an out-of-range port.
        require(listenPort in 1..65535) { "listenPort must be a valid UDP port, got $listenPort" }
    }

    /**
     * @param myLicensePublicId This device's OWN `license_public_id`, read
     *   from its own activated licence state. Blank means "not activated"
     *   -- see the no-licence rule below.
     * @param myInstallationId This device's own installation id, sent to
     *   the hub's `/join` verbatim.
     * @param myDevicePublicKey This device's own Ed25519 public key
     *   (base64), sent to the hub's `/join` verbatim.
     * @param myAssertionEnvelopeJson This device's own Owner-signed
     *   assertion envelope (the same shape [licenseIdOf] parses), sent to
     *   the hub's `/join` verbatim so the hub can run the mirror-image
     *   licence check on its own side.
     * @param label A human-readable label for this device, sent to `/join`
     *   for the hub's own device roster.
     * @param beaconTimeoutMillis How long to listen for a beacon before
     *   giving up and returning [HubDiscoveryResult.NoHubFound].
     * @param persistHub Called exactly once, only after a successful join,
     *   with the HUB's own `(baseUrl, spkiPin, devicePublicKey,
     *   installationId)`. A caller wires this to `HubPrefs.set(ctx,
     *   baseUrl, spkiPin, devicePublicKey, installationId)`. Injected
     *   rather than this class calling [HubPrefs.set] directly, because
     *   [HubPrefs] needs a real `android.content.Context` -- which this
     *   plain-JVM-tested class must never touch (see this file's header
     *   "self-contained" paragraph) -- exactly the same reasoning that
     *   makes [Transport] injected rather than a real socket.
     *
     * NEVER LOGS. Neither [myAssertionEnvelopeJson] nor [myDevicePublicKey]
     * (nor the hub's own copies of either, read back from the identity
     * response) are ever written to any log by this function -- every
     * [HubDiscoveryResult.Failed] reason below is a short fixed code, never
     * raw exception text or response content.
     */
    fun discoverAndJoin(
        myLicensePublicId: String,
        myInstallationId: String,
        myDevicePublicKey: String,
        myAssertionEnvelopeJson: String,
        label: String,
        beaconTimeoutMillis: Long = DEFAULT_BEACON_TIMEOUT_MILLIS,
        persistHub: (baseUrl: String, spkiPin: String, hubDevicePublicKey: String, hubInstallationId: String) -> Unit,
    ): HubDiscoveryResult {
        // An unactivated device has no shop to belong to -- do NOTHING, not
        // even listen for a beacon. Checked first and unconditionally: no
        // call on transport happens anywhere below this line when it fires.
        if (myLicensePublicId.isBlank()) {
            return HubDiscoveryResult.Failed("NO_LOCAL_LICENSE")
        }

        val rawBeacon = transport.listenForBeacon(beaconTimeoutMillis) ?: return HubDiscoveryResult.NoHubFound
        val hub = parseBeacon(rawBeacon) ?: return HubDiscoveryResult.NoHubFound

        val identityJson = try {
            transport.getIdentity(hub.baseUrl, hub.spkiPin)
        } catch (networkError: Exception) {
            return HubDiscoveryResult.Failed("IDENTITY_FETCH_FAILED")
        }

        // THE SHOP BOUNDARY -- checked BEFORE anything below ever offers
        // this device to the hub via postJoin. A hub from a different shop
        // must never even see a /join request; see shouldJoin's own doc
        // comment for why this replaces an operator-typed pairing code.
        if (!shouldJoin(identityJson, myLicensePublicId)) {
            return HubDiscoveryResult.DifferentShop(hub)
        }

        val joinBody = buildJoinRequestBody(
            installationId = myInstallationId,
            devicePublicKey = myDevicePublicKey,
            assertionEnvelopeJson = myAssertionEnvelopeJson,
            label = label,
        ) ?: return HubDiscoveryResult.Failed("MALFORMED_LOCAL_ASSERTION")

        val status = try {
            transport.postJoin(hub.baseUrl, hub.spkiPin, joinBody)
        } catch (networkError: Exception) {
            return HubDiscoveryResult.Failed("JOIN_REQUEST_FAILED")
        }

        if (status != 200) {
            return HubDiscoveryResult.Failed("JOIN_REJECTED_$status")
        }

        val hubIdentity = parseHubIdentityForPersist(identityJson)
            ?: return HubDiscoveryResult.Failed("MALFORMED_HUB_IDENTITY")
        persistHub(hub.baseUrl, hub.spkiPin, hubIdentity.devicePublicKey, hubIdentity.installationId)

        return HubDiscoveryResult.Joined(hub)
    }
}

private data class HubIdentityForPersist(val installationId: String, val devicePublicKey: String)

/**
 * Pulls the HUB's own top-level `installation_id`/`device_public_key` out of
 * the `/identity` response -- distinct from the ones inside its nested
 * `assertion` payload -- the pair [HubPrefs.set] needs to remember this
 * pairing.
 */
private fun parseHubIdentityForPersist(identityJson: String): HubIdentityForPersist? {
    return try {
        val root = JsonParser.parseString(identityJson)
        if (!root.isJsonObject) return null
        val obj = root.asJsonObject
        val installationId = obj.get("installation_id")
            ?.takeUnless { it.isJsonNull }?.asString?.takeUnless { it.isBlank() } ?: return null
        val devicePublicKey = obj.get("device_public_key")
            ?.takeUnless { it.isJsonNull }?.asString?.takeUnless { it.isBlank() } ?: return null
        HubIdentityForPersist(installationId, devicePublicKey)
    } catch (malformed: Exception) {
        null
    }
}

/**
 * Builds the `{installation_id, device_public_key, assertion, label}` body
 * for `POST /api/sync/v1/join`, with [assertionEnvelopeJson] embedded as a
 * real JSON object (never re-stringified into an escaped string) so the
 * hub's own JSON parser sees exactly the envelope this device received from
 * Owner. Returns `null` if [assertionEnvelopeJson] is not valid JSON at all
 * -- this device must never offer a broken assertion to a hub.
 */
private fun buildJoinRequestBody(
    installationId: String,
    devicePublicKey: String,
    assertionEnvelopeJson: String,
    label: String,
): String? {
    val assertionElement = try {
        JsonParser.parseString(assertionEnvelopeJson)
    } catch (malformed: Exception) {
        return null
    }
    val body = JsonObject()
    body.addProperty("installation_id", installationId)
    body.addProperty("device_public_key", devicePublicKey)
    body.add("assertion", assertionElement)
    body.addProperty("label", label)
    return body.toString()
}
