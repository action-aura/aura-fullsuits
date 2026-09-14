package com.actionaura.retail.sync

import com.google.common.truth.Truth.assertThat
import com.google.gson.JsonParser
import org.junit.Test

/**
 * Plain JVM tests for [HubDiscovery] and its free functions
 * ([parseBeacon]/[licenseIdOf]/[shouldJoin]) -- a fake [Transport] below,
 * matching [SyncRelayDiscoveryTest]'s idiom of exercising the pure,
 * Context-free core of this module directly rather than reaching for
 * Robolectric or Mockito (this module has neither). No sockets, no TLS, no
 * `android.content.Context` anywhere in this file.
 *
 * The most important test in this file is [`shouldJoin discoverAndJoin
 * never offers this device to a hub from a different shop`] -- it is the
 * shop-boundary property this whole feature exists to enforce, and it is
 * asserted on the FAKE's recorded calls, not merely on the returned result,
 * so a bug that joins first and checks the licence second cannot slip past
 * it. See the mutation proofs at the bottom of this file.
 */
class HubDiscoveryTest {

    // ── Fixtures -- realistic wire-shaped JSON matching beacon.py and the
    // Owner-signed assertion envelope `assertion_verifier.py` reads. ──────

    private fun beaconJson(
        installationId: String = "hub-install-1",
        url: String = "https://192.168.1.50:8443",
        spkiPin: String = "hubSpkiPinBase64==",
        timestamp: String = "2026-09-14T00:00:00+00:00",
        signature: String = "beaconSigBase64==",
    ): String = """
        {"installation_id":"$installationId","url":"$url","spki_pin":"$spkiPin","timestamp":"$timestamp","signature":"$signature"}
    """.trimIndent()

    private fun assertionEnvelopeJson(
        licensePublicId: String = "lic-shop-alpha",
        assertionId: String = "assertion-1",
    ): String = """
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

    private fun identityJson(
        hubInstallationId: String = "hub-install-1",
        hubDevicePublicKey: String = "hubDeviceKeyBase64==",
        licensePublicId: String = "lic-shop-alpha",
    ): String = """
        {
          "installation_id": "$hubInstallationId",
          "device_public_key": "$hubDevicePublicKey",
          "assertion": ${assertionEnvelopeJson(licensePublicId = licensePublicId)}
        }
    """.trimIndent()

    // ── A fake Transport -- records every call so tests can assert on
    // WHETHER a step ran, not only on what discoverAndJoin returned. ──────

    private class FakeTransport(
        var beacon: String? = null,
        var identityJson: String? = null,
        var identityError: Exception? = null,
        var joinStatus: Int = 200,
        var joinError: Exception? = null,
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
            identityError?.let { throw it }
            return identityJson ?: error("FakeTransport.identityJson was not configured for this test")
        }

        override fun postJoin(baseUrl: String, spkiPin: String, body: String): Int {
            postJoinCalls.add(Triple(baseUrl, spkiPin, body))
            joinError?.let { throw it }
            return joinStatus
        }
    }

    // ── Test 1: parseBeacon ─────────────────────────────────────────────

    @Test
    fun `parseBeacon reads a well-formed beacon`() {
        val hub = parseBeacon(
            beaconJson(
                installationId = "hub-install-1",
                url = "https://192.168.1.50:8443",
                spkiPin = "hubSpkiPinBase64==",
            )
        )

        assertThat(hub).isEqualTo(
            DiscoveredHub(
                installationId = "hub-install-1",
                baseUrl = "https://192.168.1.50:8443",
                spkiPin = "hubSpkiPinBase64==",
            )
        )
    }

    @Test
    fun `parseBeacon returns null on malformed JSON`() {
        assertThat(parseBeacon("not json at all")).isNull()
        assertThat(parseBeacon("""{"installation_id": "h",""")).isNull()
        assertThat(parseBeacon("[]")).isNull()
        assertThat(parseBeacon("")).isNull()
    }

    @Test
    fun `parseBeacon returns null when a required field is missing`() {
        // Missing "spki_pin" -- REQUIRED_BEACON_FIELDS demands it even
        // though the other four are present and well-formed.
        val json = """{"installation_id":"h","url":"https://h:1","timestamp":"2026-09-14T00:00:00+00:00","signature":"sig"}"""
        assertThat(parseBeacon(json)).isNull()
    }

    @Test
    fun `parseBeacon returns null for an oversized payload`() {
        // Otherwise well-formed, but padded past BEACON_MAX_DATAGRAM_BYTES
        // (1024) -- rejected by size before JSON parsing would even matter.
        val paddedUrl = "https://192.168.1.50:8443/" + "x".repeat(2000)
        val json = beaconJson(url = paddedUrl)
        assertThat(json.toByteArray(Charsets.UTF_8).size).isGreaterThan(1024)

        assertThat(parseBeacon(json)).isNull()
    }

    // ── Test 2: licenseIdOf ─────────────────────────────────────────────

    @Test
    fun `licenseIdOf extracts license_public_id from a realistic assertion envelope`() {
        val envelope = assertionEnvelopeJson(licensePublicId = "lic-shop-alpha")

        assertThat(licenseIdOf(envelope)).isEqualTo("lic-shop-alpha")
    }

    @Test
    fun `licenseIdOf returns null when the envelope has no payload`() {
        assertThat(licenseIdOf("""{"signing_key_id":"k","algorithm":"ed25519","signature":"s"}""")).isNull()
    }

    // ── Test 3: shouldJoin -- the shop boundary itself ──────────────────

    @Test
    fun `shouldJoin is true for a matching licence`() {
        val identity = identityJson(licensePublicId = "lic-shop-alpha")

        assertThat(shouldJoin(identity, myLicenseId = "lic-shop-alpha")).isTrue()
    }

    @Test
    fun `shouldJoin is false for a different licence`() {
        val identity = identityJson(licensePublicId = "lic-shop-alpha")

        assertThat(shouldJoin(identity, myLicenseId = "lic-shop-beta")).isFalse()
    }

    // ── Test 4: discoverAndJoin -- no beacon ────────────────────────────

    @Test
    fun `discoverAndJoin returns NoHubFound and never calls postJoin when no beacon arrives`() {
        val transport = FakeTransport(beacon = null)
        val discovery = HubDiscovery(transport)
        var persisted = false

        val result = discovery.discoverAndJoin(
            myLicensePublicId = "lic-shop-alpha",
            myInstallationId = "my-install-1",
            myDevicePublicKey = "myDeviceKeyBase64==",
            myAssertionEnvelopeJson = assertionEnvelopeJson(licensePublicId = "lic-shop-alpha"),
            label = "Front Register",
        ) { _, _, _, _ -> persisted = true }

        assertThat(result).isEqualTo(HubDiscoveryResult.NoHubFound)
        assertThat(transport.postJoinCalls).isEmpty()
        assertThat(persisted).isFalse()
    }

    // ── Test 5: discoverAndJoin -- a hub from a DIFFERENT shop ──────────
    // The most important test in this file -- see the class doc comment.

    @Test
    fun `discoverAndJoin never offers this device to a hub from a different shop`() {
        val transport = FakeTransport(
            beacon = beaconJson(),
            identityJson = identityJson(licensePublicId = "lic-shop-OTHER"),
        )
        val discovery = HubDiscovery(transport)
        var persisted = false

        val result = discovery.discoverAndJoin(
            myLicensePublicId = "lic-shop-alpha",
            myInstallationId = "my-install-1",
            myDevicePublicKey = "myDeviceKeyBase64==",
            myAssertionEnvelopeJson = assertionEnvelopeJson(licensePublicId = "lic-shop-alpha"),
            label = "Front Register",
        ) { _, _, _, _ -> persisted = true }

        assertThat(result).isInstanceOf(HubDiscoveryResult.DifferentShop::class.java)
        // Asserted on the FAKE, not just the return value -- a device must
        // not even offer itself to another shop's hub.
        assertThat(transport.postJoinCalls).isEmpty()
        assertThat(persisted).isFalse()
    }

    // ── Test 6: discoverAndJoin -- happy path ───────────────────────────

    @Test
    fun `discoverAndJoin joins a matching hub and sends this device's own identity`() {
        val transport = FakeTransport(
            beacon = beaconJson(url = "https://192.168.1.50:8443", spkiPin = "hubSpkiPinBase64=="),
            identityJson = identityJson(
                hubInstallationId = "hub-install-1",
                hubDevicePublicKey = "hubDeviceKeyBase64==",
                licensePublicId = "lic-shop-alpha",
            ),
            joinStatus = 200,
        )
        val discovery = HubDiscovery(transport)
        val persistedCalls = mutableListOf<List<String>>()

        val myAssertion = assertionEnvelopeJson(licensePublicId = "lic-shop-alpha", assertionId = "my-assertion-1")
        val result = discovery.discoverAndJoin(
            myLicensePublicId = "lic-shop-alpha",
            myInstallationId = "my-install-1",
            myDevicePublicKey = "myDeviceKeyBase64==",
            myAssertionEnvelopeJson = myAssertion,
            label = "Front Register",
        ) { baseUrl, spkiPin, hubDevicePublicKey, hubInstallationId ->
            persistedCalls.add(listOf(baseUrl, spkiPin, hubDevicePublicKey, hubInstallationId))
        }

        assertThat(result).isEqualTo(
            HubDiscoveryResult.Joined(
                DiscoveredHub(
                    installationId = "hub-install-1",
                    baseUrl = "https://192.168.1.50:8443",
                    spkiPin = "hubSpkiPinBase64==",
                )
            )
        )

        // postJoin received THIS DEVICE's own installation id, key and
        // assertion -- not the hub's.
        assertThat(transport.postJoinCalls).hasSize(1)
        val (joinBaseUrl, joinSpkiPin, joinBody) = transport.postJoinCalls.single()
        assertThat(joinBaseUrl).isEqualTo("https://192.168.1.50:8443")
        assertThat(joinSpkiPin).isEqualTo("hubSpkiPinBase64==")
        val sentBody = JsonParser.parseString(joinBody).asJsonObject
        assertThat(sentBody.get("installation_id").asString).isEqualTo("my-install-1")
        assertThat(sentBody.get("device_public_key").asString).isEqualTo("myDeviceKeyBase64==")
        assertThat(sentBody.get("label").asString).isEqualTo("Front Register")
        assertThat(sentBody.get("assertion").asJsonObject.get("payload").asJsonObject.get("assertion_id").asString)
            .isEqualTo("my-assertion-1")

        // Persisted the HUB's own address/pin/identity via the injected
        // callback (the real call site wires this to HubPrefs.set).
        assertThat(persistedCalls).containsExactly(
            listOf("https://192.168.1.50:8443", "hubSpkiPinBase64==", "hubDeviceKeyBase64==", "hub-install-1")
        )
    }

    // ── Test 7: discoverAndJoin -- non-200 from /join ───────────────────

    @Test
    fun `discoverAndJoin returns Failed and never persists on a non-200 join response`() {
        val transport = FakeTransport(
            beacon = beaconJson(),
            identityJson = identityJson(licensePublicId = "lic-shop-alpha"),
            joinStatus = 403,
        )
        val discovery = HubDiscovery(transport)
        var persisted = false

        val result = discovery.discoverAndJoin(
            myLicensePublicId = "lic-shop-alpha",
            myInstallationId = "my-install-1",
            myDevicePublicKey = "myDeviceKeyBase64==",
            myAssertionEnvelopeJson = assertionEnvelopeJson(licensePublicId = "lic-shop-alpha"),
            label = "Front Register",
        ) { _, _, _, _ -> persisted = true }

        assertThat(result).isEqualTo(HubDiscoveryResult.Failed("JOIN_REJECTED_403"))
        // postJoin DID run here (that's how we learned about the 403) --
        // what must not happen is the persist step. Asserted via the fake
        // callback's ordering, never via real SharedPreferences.
        assertThat(transport.postJoinCalls).hasSize(1)
        assertThat(persisted).isFalse()
    }

    // ── Test 8: discoverAndJoin -- no local licence at all ──────────────

    @Test
    fun `discoverAndJoin does nothing and reports no local licence when unactivated`() {
        val transport = FakeTransport(beacon = beaconJson())
        val discovery = HubDiscovery(transport)
        var persisted = false

        val result = discovery.discoverAndJoin(
            myLicensePublicId = "",
            myInstallationId = "my-install-1",
            myDevicePublicKey = "myDeviceKeyBase64==",
            myAssertionEnvelopeJson = assertionEnvelopeJson(),
            label = "Front Register",
        ) { _, _, _, _ -> persisted = true }

        assertThat(result).isEqualTo(HubDiscoveryResult.Failed("NO_LOCAL_LICENSE"))
        // No network call of any kind -- not even a beacon listen.
        assertThat(transport.listenForBeaconCalled).isFalse()
        assertThat(transport.getIdentityCalls).isEmpty()
        assertThat(transport.postJoinCalls).isEmpty()
        assertThat(persisted).isFalse()
    }

    /*
     * ── MUTATION PROOFS (reported verbatim in the task report) ─────────
     *
     * 1. Made `shouldJoin` return `true` unconditionally (ignoring the
     *    licence comparison entirely). Result: this class's RED tests were
     *    `shouldJoin is false for a different licence` (direct) and
     *    `discoverAndJoin never offers this device to a hub from a
     *    different shop` (end-to-end) -- the latter failed on
     *    `assertThat(transport.postJoinCalls).isEmpty()`, because with the
     *    guard neutered, discoverAndJoin proceeded straight to postJoin for
     *    a hub belonging to a different shop.
     *
     * 2. Reordered `HubDiscovery.discoverAndJoin` to call
     *    `transport.postJoin(...)` BEFORE the `shouldJoin(...)` check
     *    (join first, verify membership after). Result:
     *    `discoverAndJoin never offers this device to a hub from a
     *    different shop` went RED on
     *    `assertThat(transport.postJoinCalls).isEmpty()` -- postJoinCalls
     *    held one entry instead of zero, i.e. this device had already
     *    offered itself to the other shop's hub before the licence was
     *    ever checked.
     *
     * Both mutations were reverted immediately after observing the failure;
     * the full suite is green again with the code as written above.
     */
}
