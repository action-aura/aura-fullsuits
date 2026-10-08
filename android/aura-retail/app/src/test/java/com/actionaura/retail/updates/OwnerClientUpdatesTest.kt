package com.actionaura.retail.updates

import com.actionaura.retail.licensing.DeviceIdentity
import com.actionaura.retail.licensing.DownloadError
import com.actionaura.retail.licensing.NetworkError
import com.actionaura.retail.licensing.OwnerClient
import com.actionaura.retail.licensing.OwnerClientConfig
import com.actionaura.retail.licensing.canonicalizeBytes
import com.actionaura.retail.licensing.verifySignature
import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import okio.Buffer
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * The three update calls on the REAL [OwnerClient] against a real local HTTP
 * server, signing with a real device key. Only Owner's address is fake.
 */
class OwnerClientUpdatesTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    private lateinit var server: MockWebServer
    private lateinit var identity: DeviceIdentity

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        identity = DeviceIdentity(tempFolder.newFolder(), UpdatesTestKeyWrapper())
        identity.generateNewKey()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun client(maxRetries: Int = 4) = OwnerClient(
        OwnerClientConfig(
            baseUrl = server.url("/api/licensing/v1").toString(),
            maxRetries = maxRetries,
            retryBaseBackoffMillis = 1,
            retryMaxBackoffMillis = 5,
        ),
        sleepFn = { /* no real sleeping in tests */ },
    )

    private fun parseJson(raw: String): Map<String, Any?> =
        Gson().fromJson(raw, object : TypeToken<Map<String, Any?>>() {}.type)

    /** Verifies the device signature over the body exactly as it arrived on the wire. */
    private fun signatureCoversBody(wireBody: String): Boolean {
        val parsed = LinkedHashMap(parseJson(wireBody))
        val signature = java.util.Base64.getDecoder().decode(parsed.remove("signature") as String)
        val publicKey = java.util.Base64.getDecoder().decode(identity.getPublicKeyB64())
        return verifySignature(publicKey, canonicalizeBytes(parsed), signature)
    }

    // ── findLatestRelease ────────────────────────────────────────────────────

    @Test
    fun `findLatestRelease sends the contract fields signed and returns the raw reply`() {
        val ownerReply = """{"result":"SUCCESS","size_bytes":30}"""
        server.enqueue(MockResponse().setResponseCode(200).setBody(ownerReply))

        val (requestId, raw) = client().findLatestRelease("inst-1", "1.0.0-rc.8", "rc", identity)

        // Verbatim, never parsed-and-reserialized: `30` must stay `30`.
        assertThat(raw).isEqualTo(ownerReply)
        val sent = server.takeRequest()
        assertThat(sent.path).isEqualTo("/api/licensing/v1/releases/latest")
        val body = sent.body.readUtf8()
        val fields = parseJson(body)
        assertThat(fields["contract_version"]).isEqualTo("v1")
        assertThat(fields["request_id"]).isEqualTo(requestId)
        assertThat(fields["installation_id"]).isEqualTo("inst-1")
        assertThat(fields["app_version"]).isEqualTo("1.0.0-rc.8")
        assertThat(fields["release_channel"]).isEqualTo("rc")
        // Same shape as client.py's find_latest_release: no platform field.
        assertThat(fields).doesNotContainKey("platform")
        assertThat(fields).doesNotContainKey("license_key")
        assertThat(signatureCoversBody(body)).isTrue()
        // ...and the signature really covers the fields (tamper -> invalid).
        assertThat(signatureCoversBody(body.replace("inst-1", "inst-2"))).isFalse()
    }

    @Test
    fun `findLatestRelease keeps the same request id across a retry but re-nonces and re-signs`() {
        server.enqueue(MockResponse().setResponseCode(503))
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"result":"SUCCESS"}"""))

        val (requestId, _) = client().findLatestRelease("inst-1", "1.0.0-rc.8", "rc", identity)

        // Two requests actually went out (the retry really happened).
        assertThat(server.requestCount).isEqualTo(2)
        val first = server.takeRequest().body.readUtf8()
        val second = server.takeRequest().body.readUtf8()
        assertThat(parseJson(first)["request_id"]).isEqualTo(requestId)
        assertThat(parseJson(second)["request_id"]).isEqualTo(requestId)
        // Owner's replay protection would reject a repeated nonce, so the retry
        // must carry a fresh one -- and a signature that covers it.
        assertThat(parseJson(first)["nonce"]).isNotEqualTo(parseJson(second)["nonce"])
        assertThat(signatureCoversBody(first)).isTrue()
        assertThat(signatureCoversBody(second)).isTrue()
    }

    @Test
    fun `authorizeReleaseDownload sends the version id signed and returns the raw reply`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"download_token":"tok-1"}"""))

        val raw = client().authorizeReleaseDownload("inst-1", "pv-42", identity)

        assertThat(raw).isEqualTo("""{"download_token":"tok-1"}""")
        val sent = server.takeRequest()
        assertThat(sent.path).isEqualTo("/api/licensing/v1/releases/authorize-download")
        val body = sent.body.readUtf8()
        assertThat(parseJson(body)["product_version_id"]).isEqualTo("pv-42")
        assertThat(parseJson(body)["installation_id"]).isEqualTo("inst-1")
        assertThat(signatureCoversBody(body)).isTrue()
    }

    // ── downloadRelease ──────────────────────────────────────────────────────

    private fun dest() = tempFolder.root.resolve("updates/aura.apk")
    private fun part() = File(dest().path + ".part")

    @Test
    fun `downloadRelease streams the file into place and leaves no part file`() {
        val payload = ByteArray(200_000) { (it % 251).toByte() }
        server.enqueue(MockResponse().setResponseCode(200).setBody(Buffer().write(payload)))
        val seen = mutableListOf<Long>()

        client().downloadRelease("tok-1", dest(), payload.size.toLong()) { seen.add(it) }

        assertThat(dest().readBytes()).isEqualTo(payload)
        assertThat(part().exists()).isFalse()
        assertThat(seen.last()).isEqualTo(payload.size.toLong())
        assertThat(server.takeRequest().path).isEqualTo("/api/licensing/v1/releases/download/tok-1")
    }

    @Test
    fun `downloadRelease keeps a hostile token inside one path segment`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("abc"))

        client().downloadRelease("a/../b?x=1", dest(), 3)

        val path = server.takeRequest().path!!
        assertThat(path).startsWith("/api/licensing/v1/releases/download/")
        // Exactly one segment after /download/, and no query smuggled in.
        assertThat(path.removePrefix("/api/licensing/v1/releases/download/")).doesNotContain("/")
        assertThat(path).doesNotContain("?")
    }

    @Test
    fun `a stream that runs past the ceiling is aborted and the part file is deleted`() {
        // Chunked: there is no Content-Length to refuse up front, so only the
        // running byte count can catch this.
        val tooMuch = Buffer().write(ByteArray(5_000) { 7 })
        server.enqueue(MockResponse().setResponseCode(200).setChunkedBody(tooMuch, 512))

        try {
            client().downloadRelease("tok-1", dest(), 1_000)
            throw AssertionError("expected DownloadError")
        } catch (exc: DownloadError) {
            assertThat(exc.reasonCode).isEqualTo("DOWNLOAD_TOO_LARGE")
        }
        assertThat(part().exists()).isFalse()
        assertThat(dest().exists()).isFalse()
    }

    @Test
    fun `a Content-Length over the ceiling is refused before any byte is written`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody(Buffer().write(ByteArray(5_000) { 7 })))

        try {
            client().downloadRelease("tok-1", dest(), 1_000)
            throw AssertionError("expected DownloadError")
        } catch (exc: DownloadError) {
            assertThat(exc.reasonCode).isEqualTo("DOWNLOAD_TOO_LARGE")
            assertThat(exc.message).contains("announced")
        }
        assertThat(part().exists()).isFalse()
        assertThat(dest().exists()).isFalse()
    }

    @Test
    fun `a failed download is never retried because the token is single use`() {
        // A retryable status on every other call in this client (it would be
        // tried 5 times there). Here: exactly once.
        repeat(6) { server.enqueue(MockResponse().setResponseCode(503)) }

        try {
            client().downloadRelease("tok-1", dest(), 1_000)
            throw AssertionError("expected DownloadError")
        } catch (exc: DownloadError) {
            assertThat(exc.reasonCode).isEqualTo("DOWNLOAD_REFUSED")
        }
        assertThat(server.requestCount).isEqualTo(1)
        assertThat(part().exists()).isFalse()
    }

    @Test
    fun `a connection dropped mid-body is a network error, deletes the part and is not retried`() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setBody(Buffer().write(ByteArray(100_000) { 1 }))
                .setSocketPolicy(SocketPolicy.DISCONNECT_DURING_RESPONSE_BODY)
        )

        try {
            client().downloadRelease("tok-1", dest(), 100_000)
            throw AssertionError("expected NetworkError")
        } catch (exc: NetworkError) {
            assertThat(exc.reasonCode).isEqualTo("NETWORK_UNAVAILABLE")
        }
        assertThat(server.requestCount).isEqualTo(1)
        assertThat(part().exists()).isFalse()
        assertThat(dest().exists()).isFalse()
    }
}
