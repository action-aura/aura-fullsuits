package com.actionaura.retail.sync

import com.actionaura.retail.licensing.DeviceIdentity
import com.actionaura.retail.licensing.KeyWrapper
import com.google.common.truth.Truth.assertThat
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
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.spec.GCMParameterSpec

/**
 * `SyncCoordinator.runOnce()`'s dual-stream orchestration -- the wiring that
 * lets a cashier created on desktop actually log in on Android (the
 * registry stream) without breaking retail sync that already worked.
 *
 * [SyncCoordinator] is a Kotlin `object` wired to real Android/Chaquopy
 * singletons ([com.actionaura.retail.server.ServerBootstrap],
 * `BuildConfig`) that a plain JVM test cannot construct, so these tests
 * drive the `internal fun runOnce(localBaseUrl, internalSecret,
 * relayBaseUrl, identity)` overload directly -- the exact same seam
 * [SyncRelayClientTest] already uses for `requireTransportIsSafe`. Every
 * OTHER piece exercised here is real, not faked: real OkHttp for the local
 * GET/POST calls, and a real [SyncRelayClient] (raw-socket signed pull,
 * OkHttp signed push) talking to [RoutedFakeServer] over a real loopback
 * socket.
 *
 * [RoutedFakeServer] plays BOTH roles a real device juggles -- this
 * device's own embedded local backend (both `/api/sync/_internal/...` and
 * `/api/registry-sync/_internal/...`) AND Owner's relay
 * (`/api/sync/v1/push|pull`) -- by routing purely on request PATH. In
 * production those two roles never share a URL, so nothing stops one test
 * double from answering both, and doing so is what lets a single recorded
 * request timeline show everything one `runOnce()` tick actually did.
 *
 * It is hand-rolled (raw sockets), not MockWebServer, because
 * [SyncRelayClient.pull] sends a real HTTP GET carrying a body (see that
 * class's own doc comment for why OkHttp cannot construct one), and
 * MockWebServer's OWN request parser unconditionally rejects a GET carrying
 * a body -- already discovered and documented in SyncRelayClientTest
 * (`FakeSyncServer` there exists for that exact reason). This is that same
 * workaround, generalized to answer on multiple paths instead of one FIFO
 * queue, because retail's and registry's requests must each get their OWN
 * correct answer regardless of arrival order.
 */
class SyncCoordinatorTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    private lateinit var identity: DeviceIdentity
    private lateinit var server: RoutedFakeServer

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
        identity = DeviceIdentity(tempFolder.newFolder(), TestKeyWrapper())
        identity.generateNewKey()
        server = RoutedFakeServer()
        server.start()
        configureHappyPath()
        // android.util.Log throws "not mocked" in this module's plain-JVM
        // unit tests (no Robolectric, no unitTests.returnDefaultValues) --
        // see SyncCoordinator.logError's own doc comment. Swapped for a
        // silent no-op here (not asserted on) purely so a genuinely FAILING
        // tick -- which every isolation test below deliberately causes --
        // does not crash on the very Log.e call that reports the failure.
        SyncCoordinator.logError = { _, _ -> }
    }

    @After
    fun tearDown() {
        server.shutdown()
        // Restore the real logger -- SyncCoordinator is a singleton `object`
        // and this field would otherwise leak a test double into whatever
        // runs next in the same JVM.
        SyncCoordinator.logError = { message, exc ->
            if (exc != null) android.util.Log.e("SyncCoordinator", message, exc)
            else android.util.Log.e("SyncCoordinator", message)
        }
    }

    private fun baseUrl() = "http://127.0.0.1:${server.port}"

    /** Runs the real [SyncCoordinator.runOnce] overload against [server],
     *  standing in as BOTH the local backend and Owner's relay (see class
     *  doc). */
    private fun runOnce() {
        SyncCoordinator.runOnce(
            localBaseUrl = baseUrl(),
            internalSecret = "test-internal-secret",
            relayBaseUrl = baseUrl(),
            identity = identity,
        )
    }

    /** Every route on BOTH streams answers with a small, real success
     *  payload -- one queued event each -- so a happy-path run exercises the
     *  FULL chain (outbox -> relay push -> ack, cursor -> relay pull ->
     *  apply) for both streams, not just the empty-outbox short-circuit.
     *  Individual tests override specific paths on top of this to inject a
     *  targeted failure. */
    private fun configureHappyPath() {
        for (stream in SyncStream.ALL) {
            server.on(
                stream.prefix + "/_internal/outbox",
                body = """{"installation_id":"inst-1","events":[{"id":"evt-${stream.label}","entity_type":"category","event_type":"create","seq":1}]}""",
            )
            server.on(stream.prefix + "/_internal/outbox/ack", body = """{"result":"SUCCESS"}""")
            server.on(stream.prefix + "/_internal/cursor", body = """{"installation_id":"inst-1","since":0}""")
            server.on(stream.prefix + "/_internal/pull-apply", body = """{"result":"SUCCESS"}""")
        }
        server.on("/api/sync/v1/push", body = """{"result":"SUCCESS"}""")
        server.on("/api/sync/v1/pull", body = """{"result":"SUCCESS","events":[],"has_more":false}""")
    }

    // ── (a) both prefixes are actually requested ────────────────────────

    @Test
    fun `runOnce actually requests both the retail and the registry local prefixes`() {
        runOnce()

        val pathsHit = server.requests.map { it.path }.toSet()
        // The regression this guards: SyncCoordinator used to hardcode only
        // the retail prefix, so nothing ever called the registry routes at
        // all. Asserting "some request happened" would have passed on that
        // OLD code too (it does hit the retail routes) -- so this checks
        // the SPECIFIC registry paths are present, not just a non-empty set.
        assertThat(pathsHit).contains(SyncStream.REGISTRY.prefix + "/_internal/outbox")
        assertThat(pathsHit).contains(SyncStream.REGISTRY.prefix + "/_internal/outbox/ack")
        assertThat(pathsHit).contains(SyncStream.REGISTRY.prefix + "/_internal/cursor")
        assertThat(pathsHit).contains(SyncStream.REGISTRY.prefix + "/_internal/pull-apply")
        // ...and the pre-existing retail ones are untouched by this change.
        assertThat(pathsHit).contains(SyncStream.RETAIL.prefix + "/_internal/outbox")
        assertThat(pathsHit).contains(SyncStream.RETAIL.prefix + "/_internal/outbox/ack")
        assertThat(pathsHit).contains(SyncStream.RETAIL.prefix + "/_internal/cursor")
        assertThat(pathsHit).contains(SyncStream.RETAIL.prefix + "/_internal/pull-apply")
    }

    // ── (b) isolation -- the important one ───────────────────────────────

    @Test
    fun `retail push failing does not stop registry push or registry pull`() {
        // Fails at the LOCAL outbox read -- before anything ever reaches the
        // relay -- so this is unambiguously a retail-only failure, not a
        // shared-relay failure that would incidentally also break registry.
        server.on(SyncStream.RETAIL.prefix + "/_internal/outbox", status = 500, body = "boom")

        runOnce()

        val pathsHit = server.requests.map { it.path }.toSet()
        assertThat(pathsHit).contains(SyncStream.REGISTRY.prefix + "/_internal/outbox")
        assertThat(pathsHit).contains(SyncStream.REGISTRY.prefix + "/_internal/outbox/ack")
        assertThat(pathsHit).contains(SyncStream.REGISTRY.prefix + "/_internal/cursor")
        assertThat(pathsHit).contains(SyncStream.REGISTRY.prefix + "/_internal/pull-apply")
    }

    @Test
    fun `registry push failing does not stop retail push or retail pull (vice versa)`() {
        server.on(SyncStream.REGISTRY.prefix + "/_internal/outbox", status = 500, body = "boom")

        runOnce()

        val pathsHit = server.requests.map { it.path }.toSet()
        assertThat(pathsHit).contains(SyncStream.RETAIL.prefix + "/_internal/outbox")
        assertThat(pathsHit).contains(SyncStream.RETAIL.prefix + "/_internal/outbox/ack")
        assertThat(pathsHit).contains(SyncStream.RETAIL.prefix + "/_internal/cursor")
        assertThat(pathsHit).contains(SyncStream.RETAIL.prefix + "/_internal/pull-apply")
    }

    @Test
    fun `registry pull failing does not stop retail pull, and does not stop registry's own push`() {
        // Fails registry's CURSOR read -- the very first call pullOnce makes
        // -- so registry's relay pull and pull-apply are never reached
        // either. Also proves push and pull are independent WITHIN one
        // stream: registry's push (a completely separate try/catch) still
        // succeeds even though registry's pull is broken this same tick.
        server.on(SyncStream.REGISTRY.prefix + "/_internal/cursor", status = 500, body = "boom")

        runOnce()

        val pathsHit = server.requests.map { it.path }.toSet()
        assertThat(pathsHit).contains(SyncStream.RETAIL.prefix + "/_internal/cursor")
        assertThat(pathsHit).contains(SyncStream.RETAIL.prefix + "/_internal/pull-apply")
        assertThat(pathsHit).contains(SyncStream.RETAIL.prefix + "/_internal/outbox")
        assertThat(pathsHit).contains(SyncStream.RETAIL.prefix + "/_internal/outbox/ack")
        assertThat(pathsHit).contains(SyncStream.REGISTRY.prefix + "/_internal/outbox")
        assertThat(pathsHit).contains(SyncStream.REGISTRY.prefix + "/_internal/outbox/ack")
    }

    // ── (c) pendingCount is the SUM across streams ───────────────────────

    @Test
    fun `pendingCount is the sum of both streams' outbox sizes`() {
        server.on(
            SyncStream.RETAIL.prefix + "/_internal/outbox",
            body = """{"installation_id":"inst-1","events":[{"id":"a"},{"id":"b"},{"id":"c"}]}""",
        )
        server.on(
            SyncStream.REGISTRY.prefix + "/_internal/outbox",
            body = """{"installation_id":"inst-1","events":[{"id":"x"},{"id":"y"}]}""",
        )

        runOnce()

        assertThat(SyncCoordinator.health().pendingCount).isEqualTo(5)
    }

    // ── (d) a failure reason names its stream ────────────────────────────

    @Test
    fun `a push failure reason is prefixed with the failing stream's label`() {
        server.on(SyncStream.REGISTRY.prefix + "/_internal/outbox", status = 403, body = "nope")

        runOnce()

        val reason = SyncCoordinator.health().push.lastFailureReason
        assertThat(reason).isNotNull()
        assertThat(reason).startsWith("registry:")
    }

    @Test
    fun `a pull failure reason is prefixed with the failing stream's label`() {
        server.on(SyncStream.RETAIL.prefix + "/_internal/cursor", status = 403, body = "nope")

        runOnce()

        val reason = SyncCoordinator.health().pull.lastFailureReason
        assertThat(reason).isNotNull()
        assertThat(reason).startsWith("retail:")
    }

    // ── (e) existing single-stream behaviours still hold ─────────────────

    @Test
    fun `a failed relay push does not ack -- for either stream`() {
        server.on("/api/sync/v1/push", status = 400, body = """{"result":"REJECTED","reason_code":"INVALID_SIGNATURE"}""")

        runOnce()

        val ackPaths = server.requests.filter { it.path.endsWith("/outbox/ack") }
        assertThat(ackPaths).isEmpty()
    }

    @Test
    fun `push is chunked at PUSH_CHUNK_SIZE, per stream`() {
        // 250 events at a 200-event chunk cap -> two chunks -> two acks.
        val manyEvents = (1..250).joinToString(",") { """{"id":"evt-$it","entity_type":"category","seq":$it}""" }
        server.on(
            SyncStream.RETAIL.prefix + "/_internal/outbox",
            body = """{"installation_id":"inst-1","events":[$manyEvents]}""",
        )
        server.on(SyncStream.REGISTRY.prefix + "/_internal/outbox", body = """{"installation_id":"inst-1","events":[]}""")

        runOnce()

        val retailAcks = server.requests.count { it.path == SyncStream.RETAIL.prefix + "/_internal/outbox/ack" }
        assertThat(retailAcks).isEqualTo(2)
        // Registry's empty outbox short-circuits before ever touching the
        // relay or ack -- chunking on one stream must not spill into the
        // other's request count.
        val registryAcks = server.requests.count { it.path == SyncStream.REGISTRY.prefix + "/_internal/outbox/ack" }
        assertThat(registryAcks).isEqualTo(0)
    }
}

/** Minimal raw-socket HTTP test double, PATH-routed rather than a strict
 *  arrival-order queue -- see [SyncCoordinatorTest]'s class doc for why
 *  MockWebServer cannot serve this test's traffic (a real GET-with-body from
 *  [SyncRelayClient.pull]) and why routing by path (not FIFO order) matters
 *  here specifically: two independent streams' requests must each get their
 *  own correct answer regardless of which one the coordinator happens to
 *  issue first. Single-threaded accept loop, one request per connection
 *  (`Connection: close`), which is all any caller here ever sends -- same
 *  shape as `SyncRelayClientTest`'s private `FakeSyncServer`. */
private class RoutedFakeServer {
    private val serverSocket = ServerSocket(0)
    val port: Int get() = serverSocket.localPort

    data class RecordedRequest(val method: String, val path: String, val body: String)
    private data class CannedResponse(val status: Int, val body: String)

    val requests = CopyOnWriteArrayList<RecordedRequest>()
    private val responses = ConcurrentHashMap<String, CannedResponse>()
    private var acceptThread: Thread? = null
    @Volatile private var stopped = false

    /** Configures the response every future request to [path] receives
     *  ("sticky" -- not consumed/popped -- since a single tick can hit the
     *  same path more than once, e.g. multiple ack calls for one chunked
     *  push, and each of those calls needs the identical canned answer). */
    fun on(path: String, status: Int = 200, body: String = """{"result":"SUCCESS"}""") {
        responses[path] = CannedResponse(status, body)
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
                    // best-effort; an aborted/malformed request is not this fake's concern
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
        requests.add(RecordedRequest(method, path, String(bodyBytes, Charsets.UTF_8)))

        val canned = responses[path] ?: CannedResponse(404, """{"error":"RoutedFakeServer: no response configured for $path"}""")
        val bodyOut = canned.body.toByteArray(Charsets.UTF_8)
        val head = buildString {
            append("HTTP/1.1 ").append(canned.status).append(" X\r\n")
            append("Content-Type: application/json\r\n")
            append("Content-Length: ").append(bodyOut.size).append("\r\n")
            append("Connection: close\r\n")
            append("\r\n")
        }
        val out = socket.getOutputStream()
        out.write(head.toByteArray(Charsets.US_ASCII))
        out.write(bodyOut)
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
