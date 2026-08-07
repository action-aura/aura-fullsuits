package com.actionaura.retail.sync

import com.actionaura.retail.licensing.DeviceIdentity
import com.actionaura.retail.licensing.KeyWrapper
import com.google.common.truth.Truth.assertThat
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.ServerSocket
import java.net.Socket
import java.security.SecureRandom
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.CopyOnWriteArrayList
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.spec.GCMParameterSpec
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory

/** Real local HTTP(S) server(s), real OkHttpClient for `push()`, real
 * [DeviceIdentity] signing -- mirrors `OwnerClientTest`'s pattern: only
 * Owner's actual network location is faked, everything else exercised here
 * is real code.
 *
 * `push()`-focused tests use [MockWebServer] directly (a normal POST).
 *
 * `pull()`-focused tests use [FakeSyncServer] instead: MockWebServer
 * unconditionally rejects any GET request that carries a body
 * (`IllegalArgumentException: Request must not have a body`, confirmed
 * empirically while writing this test) -- but Owner's real Flask/Werkzeug
 * server accepts exactly that (confirmed via physical-device testing, see
 * this task's report), which is the whole reason `pull()` exists as a
 * hand-rolled raw-socket client in the first place. [FakeSyncServer] has no
 * such opinion, matching the real target server's actual permissiveness. */
class SyncRelayClientTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    private lateinit var server: MockWebServer
    private lateinit var identity: DeviceIdentity
    private var fakeServer: FakeSyncServer? = null

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
        fakeServer?.shutdown()
    }

    private fun client(
        baseUrl: String,
        maxRetries: Int = 4,
        sslSocketFactory: SSLSocketFactory? = null,
    ): SyncRelayClient {
        val config = SyncRelayClientConfig(
            baseUrl = baseUrl,
            maxRetries = maxRetries,
            retryBaseBackoffMillis = 1,
            retryMaxBackoffMillis = 5,
        )
        return SyncRelayClient(
            config = config,
            identity = identity,
            installationId = "inst-1",
            sslSocketFactory = sslSocketFactory,
            sleepFn = { /* no real sleeping in tests */ },
        )
    }

    private fun startFakeServer(sslContext: SSLContext? = null): FakeSyncServer {
        val fake = FakeSyncServer(sslContext)
        fake.start()
        fakeServer = fake
        return fake
    }

    // ── push() -- real POST, MockWebServer ──────────────────────────────────

    @Test
    fun `push sends signed request with events, nonce, timestamp and signature`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"result":"SUCCESS"}"""))

        client(server.url("/").toString()).push(
            listOf(linkedMapOf<String, Any?>("entity_type" to "category", "event_type" to "create", "seq" to 1L))
        )

        val request = server.takeRequest()
        assertThat(request.method).isEqualTo("POST")
        assertThat(request.path).isEqualTo("/api/sync/v1/push")
        val body = request.body.readUtf8()
        assertThat(body).contains("\"installation_id\":\"inst-1\"")
        assertThat(body).contains("\"nonce\":\"")
        assertThat(body).contains("\"timestamp\":\"")
        assertThat(body).doesNotContain("\"signature\":\"\"") // a real signature was attached
        assertThat(body).contains("\"entity_type\":\"category\"")
    }

    @Test
    fun `push 400 with reason_code throws SyncRelayRejected and is not retried`() {
        server.enqueue(
            MockResponse().setResponseCode(400).setBody("""{"result":"REJECTED","reason_code":"INVALID_SIGNATURE"}""")
        )
        try {
            client(server.url("/").toString()).push(emptyList())
            throw AssertionError("expected SyncRelayRejected")
        } catch (exc: SyncRelayRejected) {
            assertThat(exc.reasonCode).isEqualTo("INVALID_SIGNATURE")
        }
        assertThat(server.requestCount).isEqualTo(1)
    }

    // ── Transport safety (final-review Fix 2) ───────────────────────────────
    //
    // `res/xml/network_security_config.xml` blocks cleartext to non-loopback
    // hosts, but that platform policy is enforced by HttpURLConnection/OkHttp
    // -- NOT by the raw `java.net.Socket` `pull()` speaks. So before this
    // check existed, a build configured with `http://some-real-host` had
    // `push()` blocked by the OS while `pull()` happily sent the signed body
    // and read back the whole cross-device event stream in the clear.

    @Test
    fun `pull refuses to run over cleartext http to a non-loopback host`() {
        try {
            client("http://sync.actionaura.example:5551").pull(0L)
            throw AssertionError("expected SyncInsecureRelayUrlError")
        } catch (exc: SyncInsecureRelayUrlError) {
            assertThat(exc.reasonCode).isEqualTo("INSECURE_RELAY_URL")
            assertThat(exc.message).contains("sync.actionaura.example")
        }
    }

    @Test
    fun `push refuses to run over cleartext http to a non-loopback host too`() {
        try {
            client("http://sync.actionaura.example:5551").push(emptyList())
            throw AssertionError("expected SyncInsecureRelayUrlError")
        } catch (exc: SyncInsecureRelayUrlError) {
            assertThat(exc.reasonCode).isEqualTo("INSECURE_RELAY_URL")
        }
    }

    @Test
    fun `an unsafe relay url fails immediately and is never retried`() {
        var slept = 0
        val client = SyncRelayClient(
            config = SyncRelayClientConfig(baseUrl = "http://sync.actionaura.example", maxRetries = 4),
            identity = identity,
            installationId = "inst-1",
            sleepFn = { slept++ },
        )
        try {
            client.pull(0L)
            throw AssertionError("expected SyncInsecureRelayUrlError")
        } catch (exc: SyncInsecureRelayUrlError) {
            // expected
        }
        assertThat(slept).isEqualTo(0)
    }

    @Test
    fun `cleartext http to loopback is still permitted (the real dev relay recipe)`() {
        requireTransportIsSafe("http://127.0.0.1:5551/api/sync/v1/pull")
        requireTransportIsSafe("http://localhost:5551/api/sync/v1/pull")
    }

    @Test
    fun `https to any host is permitted`() {
        requireTransportIsSafe("https://sync.actionaura.example/api/sync/v1/pull")
        requireTransportIsSafe("https://sync.actionaura.example:8443/api/sync/v1/push")
    }

    @Test
    fun `a loopback lookalike in the userinfo cannot smuggle cleartext through`() {
        // URI("http://127.0.0.1@evil.example.com/") has host evil.example.com;
        // a naive `url.contains("127.0.0.1")` check would have let it through.
        try {
            requireTransportIsSafe("http://127.0.0.1@evil.example.com/api/sync/v1/pull")
            throw AssertionError("expected SyncInsecureRelayUrlError")
        } catch (exc: SyncInsecureRelayUrlError) {
            assertThat(exc.message).contains("credentials")
        }
    }

    @Test
    fun `an unsupported scheme is refused`() {
        try {
            requireTransportIsSafe("ftp://sync.actionaura.example/api/sync/v1/pull")
            throw AssertionError("expected SyncInsecureRelayUrlError")
        } catch (exc: SyncInsecureRelayUrlError) {
            assertThat(exc.message).contains("unsupported scheme")
        }
    }

    // ── pull() -- real GET-with-body, FakeSyncServer ────────────────────────

    @Test
    fun `pull sends since inside the signed body, never as a query parameter`() {
        val fake = startFakeServer()
        fake.enqueueJson(200, """{"result":"SUCCESS","events":[],"has_more":false}""")

        client("http://127.0.0.1:${fake.port}").pull(42L)

        assertThat(fake.requestCount).isEqualTo(1)
        val recorded = fake.requests[0]
        assertThat(recorded.method).isEqualTo("GET")
        assertThat(recorded.path).isEqualTo("/api/sync/v1/pull") // never "?since=42"
        assertThat(recorded.body).contains("\"since\":42")
        assertThat(recorded.body).contains("\"nonce\":\"")
    }

    @Test
    fun `pull 400 with reason_code throws SyncRelayRejected and is not retried`() {
        val fake = startFakeServer()
        fake.enqueueJson(400, """{"result":"REJECTED","reason_code":"NONCE_REUSED"}""")

        try {
            client("http://127.0.0.1:${fake.port}").pull(0L)
            throw AssertionError("expected SyncRelayRejected")
        } catch (exc: SyncRelayRejected) {
            assertThat(exc.reasonCode).isEqualTo("NONCE_REUSED")
        }
        assertThat(fake.requestCount).isEqualTo(1)
    }

    @Test
    fun `pull retries on 503 then succeeds`() {
        val fake = startFakeServer()
        fake.enqueueJson(503, "")
        fake.enqueueJson(200, """{"result":"SUCCESS","events":[],"has_more":false}""")

        val result = client("http://127.0.0.1:${fake.port}", maxRetries = 3).pull(0L)

        assertThat(result.get("result").asString).isEqualTo("SUCCESS")
        assertThat(fake.requestCount).isEqualTo(2)
    }

    @Test
    fun `pull correctly decodes a chunked transfer-encoding response`() {
        // Finding 3: a real reverse proxy/load balancer in front of Owner
        // may chunk the response regardless of what the request asked for
        // -- the old "read to EOF" implementation was never caught because
        // testing only used a bare `flask run` dev server, which never
        // chunks. A small chunk size forces multiple chunks, genuinely
        // exercising the decoder's multi-chunk loop, not a single trivial
        // chunk.
        val fake = startFakeServer()
        val json = """{"result":"SUCCESS","events":[{"seq":1,"entity_type":"category"}],"has_more":false}"""
        fake.enqueueChunked(200, json, chunkSize = 5)

        val result = client("http://127.0.0.1:${fake.port}").pull(0L)

        assertThat(result.get("result").asString).isEqualTo("SUCCESS")
        assertThat(result.getAsJsonArray("events").size()).isEqualTo(1)
        assertThat(result.getAsJsonArray("events")[0].asJsonObject.get("entity_type").asString)
            .isEqualTo("category")
    }

    // ── TLS hostname verification (Finding 1) ──────────────────────────────

    @Test
    fun `pull over https succeeds when the certificate hostname matches the server`() {
        val rootCa = HeldCertificate.Builder()
            .certificateAuthority(0)
            .commonName("test-root-ca")
            .build()
        val serverCert = HeldCertificate.Builder()
            .signedBy(rootCa)
            .addSubjectAlternativeName("127.0.0.1")
            .commonName("127.0.0.1")
            .build()
        val serverCertificates = HandshakeCertificates.Builder()
            .heldCertificate(serverCert, rootCa.certificate)
            .build()
        val clientTrust = HandshakeCertificates.Builder()
            .addTrustedCertificate(rootCa.certificate)
            .build()

        val serverSslContext = SSLContext.getInstance("TLS").apply {
            init(arrayOf(serverCertificates.keyManager), arrayOf(serverCertificates.trustManager), SecureRandom())
        }
        val fake = startFakeServer(serverSslContext)
        fake.enqueueJson(200, """{"result":"SUCCESS","events":[],"has_more":false}""")

        val result = client(
            "https://127.0.0.1:${fake.port}",
            sslSocketFactory = clientTrust.sslSocketFactory(),
        ).pull(0L)

        assertThat(result.get("result").asString).isEqualTo("SUCCESS")
        assertThat(fake.requestCount).isEqualTo(1)
    }

    @Test
    fun `pull over https FAILS when the certificate is for the wrong hostname (MITM simulation)`() {
        // This is the exact MITM scenario the finding describes: an
        // attacker who can redirect traffic to a server holding a
        // certificate that chains to a CA the client trusts, but was
        // issued for a DIFFERENT hostname than the one actually being
        // connected to. `clientTrust` here trusts `rootCa` (standing in
        // for "any CA in the real system trust store"), and the fake
        // server presents a leaf certificate signed by that same CA but
        // for "wronghost.example.com", not "127.0.0.1" (the host actually
        // being connected to).
        //
        // Without endpoint identification enabled, a bare SSLSocket
        // completes this handshake successfully -- only chain trust is
        // checked, not hostname. This test FAILS against that code
        // (pull() returns the "MITM" server's response instead of
        // rejecting it) and only PASSES once executeGetWithBody() sets
        // sslSocket.sslParameters.endpointIdentificationAlgorithm =
        // "HTTPS" before startHandshake(). Verified manually: temporarily
        // removing those two lines makes this test fail with "expected
        // pull() to reject..."; restoring them makes it pass again.
        val rootCa = HeldCertificate.Builder()
            .certificateAuthority(0)
            .commonName("test-root-ca")
            .build()
        val wrongHostCert = HeldCertificate.Builder()
            .signedBy(rootCa)
            .addSubjectAlternativeName("wronghost.example.com")
            .commonName("wronghost.example.com")
            .build()
        val serverCertificates = HandshakeCertificates.Builder()
            .heldCertificate(wrongHostCert, rootCa.certificate)
            .build()
        val clientTrust = HandshakeCertificates.Builder()
            .addTrustedCertificate(rootCa.certificate)
            .build()

        val serverSslContext = SSLContext.getInstance("TLS").apply {
            init(arrayOf(serverCertificates.keyManager), arrayOf(serverCertificates.trustManager), SecureRandom())
        }
        val fake = startFakeServer(serverSslContext)
        fake.enqueueJson(200, """{"result":"SUCCESS","events":[],"has_more":false}""")

        try {
            client(
                "https://127.0.0.1:${fake.port}",
                maxRetries = 0,
                sslSocketFactory = clientTrust.sslSocketFactory(),
            ).pull(0L)
            throw AssertionError(
                "expected pull() to reject a certificate issued for the wrong hostname -- " +
                    "hostname verification is not being enforced"
            )
        } catch (exc: SyncNetworkError) {
            assertThat(exc.reasonCode).isEqualTo("TLS_VERIFICATION_FAILED")
        }
        // The handshake itself must have failed -- no HTTP request was ever
        // actually exchanged with the "MITM" server.
        assertThat(fake.requestCount).isEqualTo(0)
    }
}

/** Minimal hand-rolled HTTP(S) server for testing [SyncRelayClient]'s
 * raw-socket `pull()` transport -- see the class doc on [SyncRelayClientTest]
 * for why [MockWebServer] cannot be used for these tests. Single-threaded
 * accept loop (one request per connection, `Connection: close` semantics),
 * which is all [SyncRelayClient] ever sends. */
private class FakeSyncServer(sslContext: SSLContext? = null) {
    private val serverSocket: ServerSocket =
        sslContext?.serverSocketFactory?.createServerSocket(0) ?: ServerSocket(0)
    val port: Int get() = serverSocket.localPort

    data class RecordedRawRequest(val method: String, val path: String, val body: String)

    val requests = CopyOnWriteArrayList<RecordedRawRequest>()
    val requestCount: Int get() = requests.size

    private val responseQueue = ConcurrentLinkedQueue<ByteArray>()
    private var acceptThread: Thread? = null
    @Volatile private var stopped = false

    /** Queues a plain (non-chunked) JSON response with a Content-Length header. */
    fun enqueueJson(status: Int, body: String) {
        val bodyBytes = body.toByteArray(Charsets.UTF_8)
        val head = buildString {
            append("HTTP/1.1 ").append(status).append(" ").append(reasonPhrase(status)).append("\r\n")
            append("Content-Type: application/json\r\n")
            append("Content-Length: ").append(bodyBytes.size).append("\r\n")
            append("Connection: close\r\n")
            append("\r\n")
        }
        responseQueue.add(head.toByteArray(Charsets.US_ASCII) + bodyBytes)
    }

    /** Queues a `Transfer-Encoding: chunked` response, splitting [body] into
     * chunks of at most [chunkSize] bytes -- [body] must be pure ASCII so
     * char-count chunking equals byte-count chunking. */
    fun enqueueChunked(status: Int, body: String, chunkSize: Int) {
        val head = buildString {
            append("HTTP/1.1 ").append(status).append(" ").append(reasonPhrase(status)).append("\r\n")
            append("Content-Type: application/json\r\n")
            append("Transfer-Encoding: chunked\r\n")
            append("Connection: close\r\n")
            append("\r\n")
            var i = 0
            while (i < body.length) {
                val end = minOf(i + chunkSize, body.length)
                val chunk = body.substring(i, end)
                append(chunk.length.toString(16)).append("\r\n")
                append(chunk).append("\r\n")
                i = end
            }
            append("0\r\n\r\n")
        }
        responseQueue.add(head.toByteArray(Charsets.US_ASCII))
    }

    private fun reasonPhrase(status: Int): String = when (status) {
        200 -> "OK"
        400 -> "Bad Request"
        503 -> "Service Unavailable"
        else -> "Unknown"
    }

    fun start() {
        acceptThread = Thread {
            while (!stopped) {
                val socket = try {
                    serverSocket.accept()
                } catch (_: Exception) {
                    break
                }
                try {
                    handleOneRequest(socket)
                } catch (_: Exception) {
                    // Handshake/parse failures on the "MITM" test path are
                    // expected -- the client is supposed to abort those.
                } finally {
                    try {
                        socket.close()
                    } catch (_: Exception) {
                    }
                }
            }
        }.apply { isDaemon = true; start() }
    }

    fun shutdown() {
        stopped = true
        try {
            serverSocket.close()
        } catch (_: Exception) {
        }
        acceptThread?.join(2000)
    }

    private fun handleOneRequest(socket: Socket) {
        val input = socket.getInputStream()
        val requestLine = readLine(input) ?: return
        val parts = requestLine.split(" ")
        val method = parts.getOrNull(0) ?: return
        val path = parts.getOrNull(1) ?: return

        var contentLength = 0
        while (true) {
            val line = readLine(input) ?: return
            if (line.isEmpty()) break
            val idx = line.indexOf(':')
            if (idx > 0 && line.substring(0, idx).equals("Content-Length", ignoreCase = true)) {
                contentLength = line.substring(idx + 1).trim().toIntOrNull() ?: 0
            }
        }

        val bodyBytes = ByteArray(contentLength)
        var read = 0
        while (read < contentLength) {
            val n = input.read(bodyBytes, read, contentLength - read)
            if (n == -1) break
            read += n
        }
        requests.add(RecordedRawRequest(method, path, String(bodyBytes, Charsets.UTF_8)))

        val response = responseQueue.poll()
            ?: "HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                .toByteArray(Charsets.US_ASCII)
        val out = socket.getOutputStream()
        out.write(response)
        out.flush()
    }

    private fun readLine(input: InputStream): String? {
        val buf = ByteArrayOutputStream()
        var prev = -1
        while (true) {
            val b = input.read()
            if (b == -1) return if (buf.size() == 0) null else String(buf.toByteArray(), Charsets.ISO_8859_1)
            if (b == '\n'.code) {
                val bytes = buf.toByteArray()
                return String(
                    if (prev == '\r'.code) bytes.copyOfRange(0, bytes.size - 1) else bytes,
                    Charsets.ISO_8859_1,
                )
            }
            buf.write(b)
            prev = b
        }
    }
}
