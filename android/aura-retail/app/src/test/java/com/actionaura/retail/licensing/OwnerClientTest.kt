package com.actionaura.retail.licensing

import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.spec.GCMParameterSpec

/** Real local HTTP server (MockWebServer), real OkHttpClient, real
 * DeviceIdentity signing -- only Owner's actual network location is faked,
 * everything else in this test exercises real code paths. */
class OwnerClientTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    private lateinit var server: MockWebServer
    private lateinit var identity: DeviceIdentity

    private class TestKeyWrapper : KeyWrapper {
        private val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        private val random = SecureRandom()
        override fun wrap(plaintext: ByteArray): ByteArray {
            val iv = ByteArray(12).also { random.nextBytes(it) }
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, key, GCMParameterSpec(128, iv))
            return iv + cipher.doFinal(plaintext)
        }
        override fun unwrap(stored: ByteArray): ByteArray {
            val iv = stored.copyOfRange(0, 12)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, iv))
            return cipher.doFinal(stored.copyOfRange(12, stored.size))
        }
    }

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        identity = DeviceIdentity(tempFolder.newFolder(), TestKeyWrapper())
        identity.generateNewKey()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun client(maxRetries: Int = 4): OwnerClient {
        val config = OwnerClientConfig(
            baseUrl = server.url("/api/licensing/v1").toString(),
            maxRetries = maxRetries,
            retryBaseBackoffMillis = 1,
            retryMaxBackoffMillis = 5,
        )
        return OwnerClient(config, sleepFn = { /* no real sleeping in tests */ })
    }

    private fun parseJson(raw: String): Map<String, Any?> {
        val type = object : TypeToken<Map<String, Any?>>() {}.type
        return Gson().fromJson(raw, type)
    }

    @Test
    fun `activate sends signed request and parses response`() {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"result":"SUCCESS","reason_code":"ACTIVATION_APPROVED","installation_id":"owner-inst-1"}"""
            )
        )

        val result = client().activate(
            productCode = "AURA_RETAIL",
            platform = "ANDROID",
            appVersion = "1.0.0-rc.2",
            releaseChannel = "rc",
            installationId = "client-id-1",
            devicePublicKeyB64 = identity.getPublicKeyB64(),
            licenseKey = "AURA-RETAIL-XXXX-YYYY",
            idempotencyKey = "idem-1",
            identity = identity,
        )

        assertThat(parseJson(result)["reason_code"]).isEqualTo("ACTIVATION_APPROVED")
        // The response is returned VERBATIM, not parsed-then-reserialized --
        // this is the whole point of the fix (Phase 7V-A): Python's
        // independent signature re-verification needs the exact bytes Owner
        // sent, and a Gson Map<String, Any?> round-trip silently turns
        // integers into doubles, which would invalidate a genuinely-valid
        // signature.
        assertThat(result).isEqualTo(
            """{"result":"SUCCESS","reason_code":"ACTIVATION_APPROVED","installation_id":"owner-inst-1"}"""
        )
        val sentRequest = server.takeRequest()
        assertThat(sentRequest.path).isEqualTo("/api/licensing/v1/activations")
        assertThat(sentRequest.body.readUtf8()).contains("\"license_key\":\"AURA-RETAIL-XXXX-YYYY\"")
        assertThat(sentRequest.body.readUtf8()).doesNotContain("signature\":\"\"") // a real signature was attached
    }

    @Test
    fun `checkin body has no license key field`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"result":"SUCCESS"}"""))
        client().checkIn(installationId = "inst-1", identity = identity)
        val sentBody = server.takeRequest().body.readUtf8()
        assertThat(sentBody).doesNotContain("license_key")
    }

    @Test
    fun `retries on 503 then succeeds`() {
        server.enqueue(MockResponse().setResponseCode(503))
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"result":"SUCCESS"}"""))

        val result = client().checkIn(installationId = "inst-1", identity = identity)

        assertThat(parseJson(result)["result"]).isEqualTo("SUCCESS")
        assertThat(server.requestCount).isEqualTo(2)
    }

    @Test
    fun `exhausts retries and throws NetworkError`() {
        repeat(10) { server.enqueue(MockResponse().setResponseCode(503)) }
        try {
            client(maxRetries = 3).checkIn(installationId = "inst-1", identity = identity)
            throw AssertionError("expected NetworkError")
        } catch (exc: NetworkError) {
            // expected
        }
        assertThat(server.requestCount).isEqualTo(4) // 1 initial + 3 retries
    }

    @Test
    fun `4xx business rejection is not retried`() {
        server.enqueue(
            MockResponse().setResponseCode(400).setBody("""{"result":"REJECTED","reason_code":"INVALID_SIGNATURE"}""")
        )
        val result = client().checkIn(installationId = "inst-1", identity = identity)
        assertThat(parseJson(result)["reason_code"]).isEqualTo("INVALID_SIGNATURE")
        assertThat(server.requestCount).isEqualTo(1)
    }

    @Test
    fun `malformed json response throws MalformedResponseError`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("not json"))
        try {
            client().checkIn(installationId = "inst-1", identity = identity)
            throw AssertionError("expected MalformedResponseError")
        } catch (exc: MalformedResponseError) {
            // expected
        }
    }

    @Test
    fun `fetchSigningKeys is a plain get`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"schema_version":1,"keys":[]}"""))
        val result = client().fetchSigningKeys()
        assertThat(result["schema_version"]).isEqualTo(1.0) // Gson decodes JSON numbers as Double by default
        val sentRequest = server.takeRequest()
        assertThat(sentRequest.method).isEqualTo("GET")
    }

    @Test
    fun `signature differs across two activations from the same key due to fresh nonce`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"result":"SUCCESS"}"""))
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"result":"SUCCESS"}"""))
        val c = client()
        c.activate("AURA_RETAIL", "ANDROID", "1.0.0-rc.2", "rc", "id-1", identity.getPublicKeyB64(), "KEY-A", "idem-1", identity)
        c.activate("AURA_RETAIL", "ANDROID", "1.0.0-rc.2", "rc", "id-1", identity.getPublicKeyB64(), "KEY-A", "idem-2", identity)
        val body1 = server.takeRequest().body.readUtf8()
        val body2 = server.takeRequest().body.readUtf8()
        assertThat(body1).isNotEqualTo(body2) // different nonce/timestamp/request_id -> different signature
    }
}
