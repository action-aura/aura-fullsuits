package com.actionaura.retail.sync

import com.actionaura.retail.ui.codeOnly
import com.google.common.truth.Truth.assertThat
import com.google.gson.JsonParser
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Plain-JVM coverage for [HubAutoJoinService] -- no device, no Chaquopy, no
 * real `Context`. [HubAutoJoinService.start]/[HubAutoJoinService.stop] take a
 * real `android.content.Context` and cannot be exercised here (the same
 * "android.* is a throwing stub under plain JUnit" limitation every other
 * test in this module works around), so this file targets the things that
 * ARE plain-JVM testable:
 *
 *   1. THE DECISION LOGIC -- [HubAutoJoinService.shouldAttemptJoin] is a pure
 *      `(Boolean, Boolean) -> Boolean` function with no `Context` in its
 *      signature at all, extracted from [HubAutoJoinService.runOnce] for
 *      exactly this reason. Every branch that decides whether a tick even
 *      attempts a join is covered here directly.
 *   2. THE TRANSPORT'S SECURITY SHAPE -- source-text contract checks against
 *      `HubTransport.kt`, the same idiom `HubTransportContractTest` and
 *      `HubPairingWiringContractTest` already use for "does the code have
 *      shape X" questions with no runtime harness available for a real TLS
 *      handshake or a real `DatagramSocket` in this environment.
 *   3. THE LICENCE-IDENTITY GAP CLOSED IN THIS TASK --
 *      [HubAutoJoinService.parseLicenseIdentity] (pure `Map` -> `Pair?`) and
 *      [HubAutoJoinService.joinUsingLicenseIdentity] (the same parse wired
 *      into one [HubDiscovery.discoverAndJoin] call against an injected
 *      [Transport]) are both extracted, exactly like [shouldAttemptJoin],
 *      because [HubAutoJoinService.attemptJoin] itself needs a real
 *      `Context` (`LicensingCoordinator`, `DeviceIdentity`, `HubPrefs`) and
 *      cannot be exercised here. Every case
 *      `GET /api/licensing/_internal/license-identity` can answer with --
 *      200 with both fields, 400 `NO_LOCAL_LICENSE`, 403, and a malformed/
 *      empty body -- is covered against both functions below.
 */
class HubAutoJoinServiceTest {

    // ── shouldAttemptJoin: the pure decision ──────────────────────────────

    @Test
    fun `not activated -- no attempt, regardless of join state`() {
        assertThat(HubAutoJoinService.shouldAttemptJoin(activated = false, alreadyJoined = false)).isFalse()
    }

    @Test
    fun `already joined -- no attempt, even though activated`() {
        // MUTATION PROOF (report both directions verbatim): with
        // shouldAttemptJoin's real body (`activated && !alreadyJoined`) this
        // is RED->GREEN as intended. Changing the body to ignore
        // alreadyJoined (e.g. `= activated`) turns this GREEN test RED,
        // because shouldAttemptJoin(true, true) would then return true
        // instead of false -- see this task's report for the actual before/
        // after run.
        assertThat(HubAutoJoinService.shouldAttemptJoin(activated = true, alreadyJoined = true)).isFalse()
    }

    @Test
    fun `activated and not yet joined -- attempts`() {
        assertThat(HubAutoJoinService.shouldAttemptJoin(activated = true, alreadyJoined = false)).isTrue()
    }

    // ── HubTransport: source-text security-shape checks ───────────────────

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return codeOnly(file.readText())
    }

    private val hubTransportSource: String get() =
        source("src/main/java/com/actionaura/retail/sync/HubTransport.kt")

    /**
     * WHY AN UNPINNED DISCOVERY TRANSPORT WOULD BE WORSE THAN USELESS: an
     * unpinned `getIdentity`/`postJoin` would complete a TLS handshake with
     * WHATEVER host answers on the beacon-advertised address and then hand
     * it this device's own installation id, device public key and
     * Owner-signed assertion envelope in the `/join` request body -- the
     * exact same "answered on the right IP" vs. "is actually the hub"
     * confusion `HubPairingWiringContractTest`'s identical test documents
     * for the manual pairing screen this feature replaces. A beacon is
     * unauthenticated by design (see `HubDiscovery.kt`'s own "A BEACON IS A
     * POINTER, NOT A CREDENTIAL" section) -- the pinned TLS connection is the
     * ONLY thing standing between "heard a UDP packet from anyone on the
     * shop wifi" and "sent this device's identity to them".
     *
     * MUTATION PROOF (report both directions verbatim): with
     * `HubTransport.pinnedClient` calling `SpkiPinning.pinnedPair(...)`, this
     * is GREEN. Deleting that call (wiring an unpinned `OkHttpClient()`
     * instead) turns this test RED.
     */
    @Test
    fun `HubTransport wires SpkiPinning into the pinned client`() {
        assertThat(hubTransportSource).contains("SpkiPinning.pinnedPair(")
        assertThat(hubTransportSource).contains("sslSocketFactory(factory, trustManager)")
    }

    /**
     * `listenForBeacon` is unauthenticated input from anyone on the shop
     * wifi (see `HubDiscovery.kt`'s own docstring) -- a JSON parser must
     * never run over an attacker-chosen payload of unbounded size. Checked
     * by source text because there is no live-socket harness in this module
     * to actually send an oversized datagram and observe the truncation.
     * Bounded to `listenForBeacon`'s own body so this cannot be satisfied by
     * the unrelated size constant declared elsewhere in the file.
     */
    @Test
    fun `listenForBeacon caps the datagram read before it is turned into a string`() {
        val body = hubTransportSource
            .substringAfter("fun listenForBeacon(")
            .substringBefore("fun getIdentity(")

        val bufferIndex = body.indexOf("ByteArray(BEACON_MAX_DATAGRAM_BYTES)")
        val receiveIndex = body.indexOf("socket.receive(packet)")
        val stringIndex = body.indexOf("String(packet.data")

        assertThat(bufferIndex).isGreaterThan(-1)
        assertThat(receiveIndex).isGreaterThan(-1)
        assertThat(stringIndex).isGreaterThan(-1)
        // The capped buffer is allocated, and the datagram is received into
        // it, BEFORE any byte of it is turned into a String.
        assertThat(bufferIndex).isLessThan(receiveIndex)
        assertThat(receiveIndex).isLessThan(stringIndex)
    }

    // ── parseLicenseIdentity: the pure parse of the internal route's body ──

    private fun assertionEnvelope(licensePublicId: String, assertionId: String): String = """
        {
          "payload": {
            "assertion_id": "$assertionId",
            "license_public_id": "$licensePublicId",
            "installation_public_id": "install-xyz"
          },
          "signing_key_id": "owner-key-1",
          "algorithm": "ed25519",
          "signature": "envelopeSigBase64=="
        }
    """.trimIndent()

    @Test
    fun `parseLicenseIdentity extracts both fields from a 200-shaped response, envelope byte-identical`() {
        val envelope = assertionEnvelope(licensePublicId = "lic-shop-alpha", assertionId = "my-assertion-1")
        val response = mapOf(
            "license_public_id" to "lic-shop-alpha",
            "assertion_envelope_json" to envelope,
        )

        val parsed = HubAutoJoinService.parseLicenseIdentity(response)

        assertThat(parsed).isNotNull()
        assertThat(parsed!!.first).isEqualTo("lic-shop-alpha")
        // BYTE-IDENTICAL: the exact same String content as the raw response
        // field, whitespace and all -- parseLicenseIdentity never re-parses
        // this into a JsonObject and re-serializes it, which could reorder
        // keys or change whitespace and silently break the hub's signature
        // check over these exact bytes.
        assertThat(parsed.second).isEqualTo(envelope)
    }

    @Test
    fun `parseLicenseIdentity returns null for a 400 NO_LOCAL_LICENSE-shaped response`() {
        val response = mapOf("reason_code" to "NO_LOCAL_LICENSE", "detail" to "No local licence assertion on file.")
        assertThat(HubAutoJoinService.parseLicenseIdentity(response)).isNull()
    }

    @Test
    fun `parseLicenseIdentity returns null for a 403-shaped response`() {
        val response = mapOf("reason_code" to "INVALID_REQUEST", "detail" to "Unauthorized.")
        assertThat(HubAutoJoinService.parseLicenseIdentity(response)).isNull()
    }

    @Test
    fun `parseLicenseIdentity returns null for a malformed or empty response`() {
        assertThat(HubAutoJoinService.parseLicenseIdentity(emptyMap())).isNull()
        // Only one of the two keys present.
        assertThat(HubAutoJoinService.parseLicenseIdentity(mapOf("license_public_id" to "lic-shop-alpha"))).isNull()
        // Both keys present but blank.
        assertThat(
            HubAutoJoinService.parseLicenseIdentity(
                mapOf("license_public_id" to "", "assertion_envelope_json" to "")
            )
        ).isNull()
        // Wrong element type for the key (Gson would never produce this from
        // a JSON string field, but a caller must not crash on it either).
        assertThat(
            HubAutoJoinService.parseLicenseIdentity(
                mapOf("license_public_id" to 123.0, "assertion_envelope_json" to "x")
            )
        ).isNull()
    }

    // ── joinUsingLicenseIdentity: the parse wired into discoverAndJoin,
    // asserted on the FAKE transport's calls, not just the return value ────

    private class FakeTransport(
        var beacon: String? = null,
        var identityJson: String? = null,
        var joinStatus: Int = 200,
    ) : Transport {
        var listenForBeaconCalled = false
            private set
        val getIdentityCalls = mutableListOf<Pair<String, String>>()
        val postJoinCalls = mutableListOf<Triple<String, String, String>>()

        override fun listenForBeacon(timeoutMillis: Long): String? {
            listenForBeaconCalled = true
            return beacon
        }

        override fun getIdentity(baseUrl: String, spkiPin: String): String {
            getIdentityCalls.add(baseUrl to spkiPin)
            return identityJson ?: error("FakeTransport.identityJson was not configured for this test")
        }

        override fun postJoin(baseUrl: String, spkiPin: String, body: String): Int {
            postJoinCalls.add(Triple(baseUrl, spkiPin, body))
            return joinStatus
        }
    }

    private fun beaconJson(): String = """
        {"installation_id":"hub-install-1","url":"https://192.168.1.50:8443","spki_pin":"hubSpkiPinBase64==","timestamp":"2026-09-14T00:00:00+00:00","signature":"beaconSigBase64=="}
    """.trimIndent()

    private fun hubIdentityJson(licensePublicId: String): String = """
        {
          "installation_id": "hub-install-1",
          "device_public_key": "hubDeviceKeyBase64==",
          "assertion": ${assertionEnvelope(licensePublicId = licensePublicId, assertionId = "hub-assertion-1")}
        }
    """.trimIndent()

    @Test
    fun `joinUsingLicenseIdentity carries the parsed values through to discoverAndJoin on a 200 response`() {
        val myEnvelope = assertionEnvelope(licensePublicId = "lic-shop-alpha", assertionId = "my-assertion-1")
        val transport = FakeTransport(
            beacon = beaconJson(),
            identityJson = hubIdentityJson(licensePublicId = "lic-shop-alpha"),
            joinStatus = 200,
        )
        val response = mapOf(
            "license_public_id" to "lic-shop-alpha",
            "assertion_envelope_json" to myEnvelope,
        )

        val result = HubAutoJoinService.joinUsingLicenseIdentity(
            discovery = HubDiscovery(transport),
            identityResponse = response,
            myInstallationId = "my-install-1",
            myDevicePublicKey = "myDeviceKeyBase64==",
            label = "Front Register",
            persistHub = { _, _, _, _ -> },
        )

        assertThat(result).isInstanceOf(HubDiscoveryResult.Joined::class.java)
        // Reached discoverAndJoin at all (a blank licence never gets past
        // its first line) AND the exact envelope this response carried is
        // what left this device's own /join request -- not a substitute or
        // re-serialized copy.
        assertThat(transport.postJoinCalls).hasSize(1)
        val sentBody = JsonParser.parseString(transport.postJoinCalls.single().third).asJsonObject
        assertThat(sentBody.get("installation_id").asString).isEqualTo("my-install-1")
        assertThat(sentBody.get("assertion").asJsonObject.get("payload").asJsonObject.get("assertion_id").asString)
            .isEqualTo("my-assertion-1")
    }

    @Test
    fun `joinUsingLicenseIdentity attempts no network call at all for a 400 NO_LOCAL_LICENSE response`() {
        val transport = FakeTransport(beacon = beaconJson())
        val response = mapOf("reason_code" to "NO_LOCAL_LICENSE", "detail" to "No local licence assertion on file.")

        val result = HubAutoJoinService.joinUsingLicenseIdentity(
            discovery = HubDiscovery(transport),
            identityResponse = response,
            myInstallationId = "my-install-1",
            myDevicePublicKey = "myDeviceKeyBase64==",
            label = "Front Register",
            persistHub = { _, _, _, _ -> },
        )

        assertThat(result).isEqualTo(HubDiscoveryResult.Failed("NO_LOCAL_LICENSE"))
        // No network call of any kind -- not even a beacon listen.
        assertThat(transport.listenForBeaconCalled).isFalse()
        assertThat(transport.getIdentityCalls).isEmpty()
        assertThat(transport.postJoinCalls).isEmpty()
    }

    @Test
    fun `joinUsingLicenseIdentity attempts no network call at all for a 403 response`() {
        val transport = FakeTransport(beacon = beaconJson())
        val response = mapOf("reason_code" to "INVALID_REQUEST", "detail" to "Unauthorized.")

        val result = HubAutoJoinService.joinUsingLicenseIdentity(
            discovery = HubDiscovery(transport),
            identityResponse = response,
            myInstallationId = "my-install-1",
            myDevicePublicKey = "myDeviceKeyBase64==",
            label = "Front Register",
            persistHub = { _, _, _, _ -> },
        )

        assertThat(result).isEqualTo(HubDiscoveryResult.Failed("NO_LOCAL_LICENSE"))
        assertThat(transport.listenForBeaconCalled).isFalse()
        assertThat(transport.getIdentityCalls).isEmpty()
        assertThat(transport.postJoinCalls).isEmpty()
    }

    @Test
    fun `joinUsingLicenseIdentity attempts no network call at all for a malformed or empty response, and never throws`() {
        val transport = FakeTransport(beacon = beaconJson())

        val result = HubAutoJoinService.joinUsingLicenseIdentity(
            discovery = HubDiscovery(transport),
            identityResponse = emptyMap(),
            myInstallationId = "my-install-1",
            myDevicePublicKey = "myDeviceKeyBase64==",
            label = "Front Register",
            persistHub = { _, _, _, _ -> },
        )

        assertThat(result).isEqualTo(HubDiscoveryResult.Failed("NO_LOCAL_LICENSE"))
        assertThat(transport.listenForBeaconCalled).isFalse()
        assertThat(transport.getIdentityCalls).isEmpty()
        assertThat(transport.postJoinCalls).isEmpty()
    }

    /*
     * ── MUTATION PROOF (report both directions verbatim) ──────────────────
     *
     * Mutated joinUsingLicenseIdentity to fabricate a non-blank licence on a
     * failed/malformed read instead of falling through to the blank pair --
     * `identity?.first ?: ""` -> `identity?.first ?: "FALLBACK-LICENSE-ID"`
     * (and the same for the assertion envelope). RED: `joinUsingLicenseIdentity
     * attempts no network call at all for a malformed or empty response, and
     * never throws` failed on `assertThat(transport.listenForBeaconCalled)
     * .isFalse()` -- with a fabricated non-blank licence, discoverAndJoin no
     * longer short-circuited on its blank check and went straight to
     * transport.listenForBeacon(), i.e. a failed identity read would have
     * had this device announce itself on the shop wifi with a made-up
     * identity. Reverted immediately after observing the failure; GREEN
     * again with the code as written above (see this task's report for the
     * actual command output of both runs).
     */
}
