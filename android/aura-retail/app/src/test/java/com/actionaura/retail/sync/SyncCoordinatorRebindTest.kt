package com.actionaura.retail.sync

import com.actionaura.retail.licensing.DeviceIdentity
import com.actionaura.retail.licensing.KeyWrapper
import com.actionaura.retail.ui.codeOnly
import com.google.common.truth.Truth.assertThat
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.InputStream
import java.net.ServerSocket
import java.net.Socket
import java.net.URLDecoder
import java.security.SecureRandom
import java.util.Timer
import java.util.concurrent.CopyOnWriteArrayList
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.spec.GCMParameterSpec
import javax.net.ssl.SSLContext

/**
 * The headline fix this task exists for: measured on real hardware
 * (2026-09-24), a phone that joined a shop's LAN hub kept signing sync
 * requests against its OLD relay (Owner's cloud) until the app was
 * physically restarted -- `site_sync_nonces` stayed at 0 on the hub for
 * this phone until then, and jumped to 37 only after a restart. The cause
 * was [SyncCoordinator.start] resolving `effectiveRelayBaseUrl`/`hubSpkiPin`
 * ONCE, at app-boot Context time, with nothing ever re-resolving them
 * later. [SyncCoordinator.onHubPairingChanged] is the fix: both the manual
 * pairing screen ([RetailExtraScreens]) and the automatic join
 * ([HubAutoJoinService]) now call it the instant [HubPrefs] changes, and
 * this file proves the coordinator's [RelayTarget] actually moves as a
 * result, with no restart, on the very next tick.
 *
 * Uses the same [RoutedFakeServer] harness [SyncCoordinatorTest] already
 * proved out (path-routed, plays both this device's own local backend AND
 * Owner's cloud relay by path) -- see that class's own doc comment for why
 * a hand-rolled raw-socket server is needed here instead of MockWebServer
 * ([SyncRelayClient.pull] sends a real GET carrying a body, which
 * MockWebServer's own parser rejects). [RoutedFakeServer] itself stays
 * `private` in that file's evidence of an already-passed guard; only its
 * VISIBILITY changed (`private` -> `internal`) so this sibling file in the
 * same package/module can reuse it, per this task's own file-ownership note.
 */
class SyncCoordinatorRebindTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    private lateinit var identity: DeviceIdentity
    private lateinit var server: RoutedFakeServer
    private val observedPairs = mutableListOf<Pair<String, String>>()

    /** Copied verbatim from [SyncCoordinatorTest]'s own private
     *  `TestKeyWrapper` (~15 lines, per this task's own instructions) rather
     *  than extracted into a shared file: that copy is shipped evidence for
     *  a guard that already passed, and is left exactly as verified. */
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
        // Stopped BEFORE anything else -- this class's tests drive start()/
        // onHubPairingChanged() directly against the shared SyncCoordinator
        // singleton `object`, so a previous test class's leftover
        // running=true state must never leak into this one's "starts from
        // stopped" assumptions.
        SyncCoordinator.stop()
        identity = DeviceIdentity(tempFolder.newFolder(), TestKeyWrapper())
        identity.generateNewKey()
        server = RoutedFakeServer()
        server.start()
        configureHappyPath()
        // Same reason SyncCoordinatorTest/SyncRelayDiscoveryTest swap this:
        // resolveRelayBaseUrl/resolveRelayTarget log via SyncCoordinator
        // .logError on a rejected candidate, which defaults to real
        // android.util.Log.e -- "not mocked" in this plain-JVM unit test
        // module.
        SyncCoordinator.logError = { _, _ -> }
        SyncCoordinator.relayClientObserver = { url, pin -> observedPairs.add(url to pin) }
    }

    @After
    fun tearDown() {
        server.shutdown()
        SyncCoordinator.stop()
        SyncCoordinator.logError = { message, exc ->
            if (exc != null) android.util.Log.e("SyncCoordinator", message, exc)
            else android.util.Log.e("SyncCoordinator", message)
        }
        SyncCoordinator.relayClientObserver = { _, _ -> }
    }

    private fun baseUrl() = "http://127.0.0.1:${server.port}"

    /** Every route on both streams answers with an EMPTY outbox -- unlike
     *  [SyncCoordinatorTest]'s happy path, this test cares about WHICH
     *  relay a tick talks to, not about moving real events, so an empty
     *  outbox keeps push() a no-op (see pushOnce's own "events.size() == 0
     *  -> return" short-circuit) and the only relayClient() calls this test
     *  needs to observe come from the pull side, on both streams, every
     *  tick. */
    private fun configureHappyPath() {
        for (stream in SyncStream.ALL) {
            server.on(stream.prefix + "/_internal/outbox", body = """{"installation_id":"inst-1","events":[]}""")
            server.on(stream.prefix + "/_internal/outbox/ack", body = """{"result":"SUCCESS"}""")
            server.on(stream.prefix + "/_internal/cursor", body = """{"installation_id":"inst-1","since":0}""")
            server.on(stream.prefix + "/_internal/pull-apply", body = """{"result":"SUCCESS"}""")
        }
        server.on("/api/sync/v1/push", body = """{"result":"SUCCESS"}""")
        server.on("/api/sync/v1/pull", body = """{"result":"SUCCESS","events":[],"has_more":false}""")
    }

    /** Drives one real tick, exactly the seam [SyncCoordinatorTest] already
     *  uses ([SyncCoordinator.runOnceAgainstCurrentTarget] instead of the
     *  private zero-arg [SyncCoordinator]`.runOnce()` the real timer calls),
     *  so this test's OWN [identity] -- signed via [TestKeyWrapper], not
     *  whatever `AndroidKeystoreWrapper`-backed identity `start()`'s
     *  internal `DeviceIdentity(identityDir)` would otherwise construct --
     *  is what actually signs every push/pull this test observes. The
     *  local half always talks to THIS test's own [server] regardless of
     *  which relay the tick's [SyncCoordinator]`.target` currently points
     *  at -- exactly like production, where "this device's own embedded
     *  backend" and "the relay" are never the same host. */
    private fun tick() {
        SyncCoordinator.runOnceAgainstCurrentTarget(baseUrl(), "test-internal-secret", identity)
    }

    /** A momentarily-bound, then immediately closed, loopback TCP port --
     *  guaranteed nothing answers on it. Used only by K2/K3 below, which
     *  never tick() -- they assert purely on [SyncCoordinator
     *  .currentRelayTarget]/[SyncCoordinator.health] state, so the hub URL
     *  only needs to be a syntactically valid, loopback (cleartext-permitted)
     *  `https://` address, never an actually-reachable one. K1, which DOES
     *  tick() against the hub, deliberately uses a second real
     *  [RoutedFakeServer] instead -- see that test's own comment on why a
     *  genuinely unreachable target would make the test slow (SyncRelayClient's
     *  real retry/backoff loop). */
    private fun closedPort(): Int {
        val socket = ServerSocket(0)
        val port = socket.localPort
        socket.close()
        return port
    }

    /** A real, well-formed SPKI pin -- same construction [SpkiPinningTest]
     *  uses (`okhttp3.tls.HeldCertificate`, already on this module's test
     *  classpath). This test never completes a TLS handshake against it
     *  (see [closedPort]'s own doc comment), so the pin's VALUE is never
     *  actually verified against anything -- it only needs to be non-blank
     *  and shaped like a real pin, so [SyncCoordinator.resolveRelayTarget]
     *  treats it as a genuine hub credential rather than exercising some
     *  other, unrelated blank-pin code path. */
    private fun wellFormedPin(): String =
        SpkiPinning.pinOf(HeldCertificate.Builder().commonName("rebind-test-hub").build().certificate)

    // -- K1 (headline): a mid-run re-point moves url+pin together, no restart --

    @Test
    fun `a mid-run re-point moves both url and pin together on the very next tick, with no restart`() {
        // A SECOND real fake server stands in for the paired hub -- NOT a
        // closed/unreachable port. SyncRelayClient retries a genuinely
        // unreachable relay up to 4 times with real Thread.sleep backoff
        // (1s, 2s, 4s, 8s -- SyncRelayClientConfig's defaults, which
        // SyncCoordinator.relayClient() never overrides), because a plain
        // ConnectException is IOException and therefore retryable there --
        // that would make this one test take upwards of 15 real seconds
        // for NO assertion value. Every existing failure test in this
        // package (SyncCoordinatorTest's isolation tests) makes the same
        // choice for the same reason: keep the fake relay reachable and
        // answer with a non-retryable outcome instead of leaving it
        // unreachable. A plain http:// hub is also legal here --
        // requireTransportIsSafe permits cleartext to 127.0.0.1 (loopback
        // exemption) -- so SyncRelayClient.pull()'s raw-socket path never
        // attempts a TLS handshake against it either (isHttps gates that
        // branch on the URL's own scheme).
        val hubServer = RoutedFakeServer()
        hubServer.start()
        for (stream in SyncStream.ALL) {
            hubServer.on(stream.prefix + "/_internal/outbox", body = """{"installation_id":"inst-1","events":[]}""")
        }
        hubServer.on("/api/sync/v1/push", body = """{"result":"SUCCESS"}""")
        hubServer.on("/api/sync/v1/pull", body = """{"result":"SUCCESS","events":[],"has_more":false}""")
        try {
            val cloudUrl = baseUrl()
            SyncCoordinator.start(tempFolder.newFolder(), "", cloudUrl)

            // -- tick 1: still on the cloud --
            tick()

            assertThat(observedPairs).isNotEmpty()
            for (pair in observedPairs) assertThat(pair).isEqualTo(cloudUrl to "")
            val n0 = server.requests.count { it.path.startsWith("/api/sync/v1/") }

            // -- re-point to a paired hub --
            val pin = wellFormedPin()
            val hubUrl = "http://127.0.0.1:${hubServer.port}"
            SyncCoordinator.onHubPairingChanged(tempFolder.newFolder(), hubUrl, pin)

            assertThat(SyncCoordinator.currentRelayTarget()).isEqualTo(RelayTarget(hubUrl, pin))

            // -- tick 2: against the hub, with NO restart of anything --
            observedPairs.clear()
            tick()

            assertThat(observedPairs).isNotEmpty()
            for (pair in observedPairs) assertThat(pair).isEqualTo(hubUrl to pin)

            // Nothing NEW went to the cloud on tick 2 -- the re-pointed
            // tick's relay traffic went to hubServer instead, never back
            // to this test's own [server] (which plays both this device's
            // own local backend AND the cloud relay).
            val n1 = server.requests.count { it.path.startsWith("/api/sync/v1/") }
            assertThat(n1).isEqualTo(n0)
            // ...and it DID reach the hub -- proving tick 2 was a genuine
            // attempt against the new target, not merely "observed but
            // never sent".
            assertThat(hubServer.requests.any { it.path == "/api/sync/v1/pull" }).isTrue()

            // The LOCAL cursor request (always against this test's own
            // [server], regardless of which relay the tick targets -- see
            // tick()'s own doc comment) carries the NEW relay in its
            // `relay_url` query param, for retail schema v31's
            // ensure_cursor_matches_relay guard -- see SyncCoordinatorTest's
            // own identical assertion for the pre-existing half of this
            // contract.
            val cursorRequests = server.requests.filter { it.path.endsWith("/_internal/cursor") }
            assertThat(cursorRequests.size).isAtLeast(SyncStream.ALL.size * 2)
            for (request in cursorRequests.takeLast(SyncStream.ALL.size)) {
                val rawQuery = request.rawTarget.substringAfter('?', missingDelimiterValue = "")
                val relayUrlParam = rawQuery.split('&')
                    .map { it.split('=', limit = 2) }
                    .firstOrNull { it.getOrNull(0) == "relay_url" }
                    ?.getOrNull(1)
                assertThat(relayUrlParam).isNotNull()
                assertThat(URLDecoder.decode(relayUrlParam, "UTF-8")).isEqualTo(hubUrl)
            }
        } finally {
            hubServer.shutdown()
        }
    }

    // -- K2: not-yet-running -> a hub pairing is this device's FIRST target --

    @Test
    fun `a hub pairing while never started begins the loop with no cloud relay at all, and unpairing stops it`() {
        val dir = tempFolder.newFolder()
        // A deliberately-UNSAFE persisted URL (cleartext to a non-loopback
        // host), not null. start() records ANY non-null value into
        // lastPersistedRelayBaseUrl unconditionally (see start()'s own doc
        // comment: "only ever overwritten by a non-null value"), even
        // though resolveRelayBaseUrl then rejects it and start() itself
        // stays inert. Passing null here instead would leave
        // lastPersistedRelayBaseUrl at whatever a DIFFERENT test class
        // elsewhere in this same JVM fork last left it -- SyncCoordinator
        // is a genuine process-wide singleton, and stop() was already
        // established (SyncRelayDiscoveryTest's own comment) to never
        // clear that kind of "last known" field. Passing a deterministic,
        // always-rejected value here pins the "nothing safe to fall back
        // to" precedence for the unpair assertion below regardless of
        // whatever ran before this test in the same process.
        SyncCoordinator.start(dir, "", "http://not-a-loopback-host.invalid")
        assertThat(SyncCoordinator.health().running).isFalse()

        val pin = wellFormedPin()
        val hubUrl = "https://127.0.0.1:${closedPort()}"
        SyncCoordinator.onHubPairingChanged(dir, hubUrl, pin)

        assertThat(SyncCoordinator.health().running).isTrue()
        assertThat(SyncCoordinator.currentRelayTarget()).isEqualTo(RelayTarget(hubUrl, pin))

        SyncCoordinator.onHubPairingChanged(dir, null, null)

        assertThat(SyncCoordinator.health().running).isFalse()
        assertThat(SyncCoordinator.currentRelayTarget()).isEqualTo(RelayTarget("", ""))
    }

    // -- K3: leaving a hub falls back to the cloud target start() resolved --

    @Test
    fun `unpairing from a hub falls back to the cloud target start resolved, and stays running`() {
        val dir = tempFolder.newFolder()
        val cloudUrl = baseUrl()
        SyncCoordinator.start(dir, "", cloudUrl)
        assertThat(SyncCoordinator.currentRelayTarget()).isEqualTo(RelayTarget(cloudUrl, ""))

        val pin = wellFormedPin()
        val hubUrl = "https://127.0.0.1:${closedPort()}"
        SyncCoordinator.onHubPairingChanged(dir, hubUrl, pin)
        assertThat(SyncCoordinator.currentRelayTarget()).isEqualTo(RelayTarget(hubUrl, pin))

        SyncCoordinator.onHubPairingChanged(dir, null, null)

        assertThat(SyncCoordinator.currentRelayTarget()).isEqualTo(RelayTarget(cloudUrl, ""))
        assertThat(SyncCoordinator.health().running).isTrue()
    }

    // -- K4 (contract): relayClient never reads the live field; the two --
    // -- retired fields are actually gone, not merely renamed --------------

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    @Test
    fun `relayClient never references the live target field -- the pin must come from the tick snapshot`() {
        val code = codeOnly(source("src/main/java/com/actionaura/retail/sync/SyncCoordinator.kt"))

        val body = code.substringAfter("private fun relayClient(").substringBefore("private fun pushOnce(")
        assertThat(body).doesNotContain("target")

        // Word-boundary, not substringnegation: a coincidental prefix match
        // (e.g. the pre-existing `hubSpkiPinValue` parameter on the
        // Context-taking start() overload) must not falsely trip this --
        // this checks the OLD FIELD NAMES are gone, not that no identifier
        // anywhere in the file happens to start with the same letters.
        assertThat(Regex("""\bhubSpkiPin\b""").containsMatchIn(code)).isFalse()
        assertThat(Regex("""\beffectiveRelayBaseUrl\b""").containsMatchIn(code)).isFalse()
    }

    // -- K5-K8: added by the adversarial verifier, 2026-09-24 ----------------
    //
    // WHY THESE EXIST: every test above proves the (url, pin) PAIR moves on a
    // re-point, but none of them proves the pin is ever USED. K1's hub is
    // plain http:// and [SyncCoordinator.relayClientObserver] records the pin
    // on relayClient's FIRST line -- before relayClient decides pinned vs
    // plain -- so the plan's own mutation MK2 (`val pin = ""` inside
    // relayClient, i.e. "the phone re-points to the hub but talks to it with
    // an UNPINNED client") survived all 119 tests in this package, measured.
    // On real hardware that mutation is a phone that re-points and then fails
    // every TLS handshake against the hub's self-signed certificate: the
    // exact "joined but nothing crosses" symptom this task exists to fix,
    // re-created one layer down. K5/K6 put a real TLS hub (self-signed, like
    // the real one) behind the re-point and assert on what the HUB received,
    // in both directions: a wrong pin is refused, the right pin syncs.

    @Test
    fun `K5 -- a re-point to a real TLS hub handshakes ONLY under the new pin, on BOTH transport seams`() {
        val hubCert = selfSignedHubCert()
        val hub = TlsRoutedHub(serverSslContext(hubCert))
        hub.start()
        // A non-empty outbox on both streams, so push() actually reaches the
        // relay: push is the OkHttp seam (httpClient = pinnedHttpClient) and
        // pull is the raw-socket seam (sslSocketFactory = factory) -- this
        // test must see BOTH arrive, not just one.
        configureOneEventOutbox()
        try {
            SyncCoordinator.start(tempFolder.newFolder(), "", baseUrl())
            val hubUrl = "https://127.0.0.1:${hub.port}"

            // Deny-half first: a well-formed pin of a DIFFERENT key. Nothing
            // may reach the hub -- the pinned trust manager must refuse the
            // hub's real key on both seams.
            SyncCoordinator.onHubPairingChanged(tempFolder.newFolder(), hubUrl, wellFormedPin())
            tick()
            assertThat(hub.paths).isEmpty()

            // Allow-half: the hub's real pin. Exactly one push and one pull
            // per stream must now complete a TLS handshake with the hub.
            val rightPin = SpkiPinning.pinOf(hubCert.certificate)
            SyncCoordinator.onHubPairingChanged(tempFolder.newFolder(), hubUrl, rightPin)
            assertThat(SyncCoordinator.currentRelayTarget()).isEqualTo(RelayTarget(hubUrl, rightPin))
            tick()
            assertThat(hub.paths.count { it == "/api/sync/v1/push" }).isEqualTo(SyncStream.ALL.size)
            assertThat(hub.paths.count { it == "/api/sync/v1/pull" }).isEqualTo(SyncStream.ALL.size)
        } finally {
            hub.shutdown()
        }
    }

    // The "no regression for a phone that boots already paired" half: the
    // pin handed to start() at boot (AppRoot -> HubPrefs) must be the one the
    // very first tick handshakes with. Before this test, the only thing
    // guarding the boot path's pin was an equality assertion on
    // currentRelayTarget() inside the catch-up gate test -- incidental cover.

    @Test
    fun `K6 -- a phone that BOOTS already paired syncs to its hub over the pinned transport from the first tick`() {
        val hubCert = selfSignedHubCert()
        val hub = TlsRoutedHub(serverSslContext(hubCert))
        hub.start()
        configureOneEventOutbox()
        try {
            val hubUrl = "https://127.0.0.1:${hub.port}"
            val pin = SpkiPinning.pinOf(hubCert.certificate)
            SyncCoordinator.start(tempFolder.newFolder(), "", baseUrl(), hubUrl, pin)
            assertThat(SyncCoordinator.currentRelayTarget()).isEqualTo(RelayTarget(hubUrl, pin))

            tick()

            assertThat(hub.paths.count { it == "/api/sync/v1/push" }).isEqualTo(SyncStream.ALL.size)
            assertThat(hub.paths.count { it == "/api/sync/v1/pull" }).isEqualTo(SyncStream.ALL.size)
            // ...and not one relay call leaked to the cloud (this test's
            // [server], which is also the local backend).
            assertThat(server.requests.count { it.path.startsWith("/api/sync/v1/") }).isEqualTo(0)
        } finally {
            hub.shutdown()
        }
    }

    // THE TIMER-CHAIN RACE this change made reachable. Before it, stop() had
    // no production caller, so `running` never went true -> false -> true
    // inside one process. onHubPairingChanged now does exactly that on a
    // LAN-only phone (unpair with no cloud fallback stops the loop; the next
    // pairing -- manual Connect, or the auto-join tick -- starts it again),
    // and a tick can be in flight across both: against an unreachable hub one
    // tick can run for a minute (SyncRelayClient retries each of four
    // operations with 1+2+4+8s backoff), which is precisely when a user is
    // tapping Disconnect/Connect. When that stale tick finished, its
    // `finally` used to call scheduleNext() unconditionally, saw running ==
    // true (set by the re-pair) and forked a SECOND self-rescheduling chain
    // beside the one the re-pair started: two ticks overlapping forever,
    // double pushes/pulls, double pull-apply (equal-row_version conflict
    // rows), and `timer` naming only one of them. The tick's own `finally`
    // is modelled by onTickFinished(<that chain's Timer>).

    @Test
    fun `K7 -- a tick still in flight across an unpair and re-pair can never fork a second timer chain`() {
        val dir = tempFolder.newFolder()
        // Same deterministic "nothing safe to fall back to" precedence as K2,
        // so the unpair below genuinely stops the loop.
        SyncCoordinator.start(dir, "", "http://not-a-loopback-host.invalid")
        val hubUrl = "https://127.0.0.1:${closedPort()}"
        val pin = wellFormedPin()
        val timers = mutableListOf<Timer>()
        try {
            SyncCoordinator.onHubPairingChanged(dir, hubUrl, pin)
            val chainA = checkNotNull(liveTimer())
            timers += chainA
            SyncCoordinator.onHubPairingChanged(dir, null, null)
            assertThat(SyncCoordinator.health().running).isFalse()
            SyncCoordinator.onHubPairingChanged(dir, hubUrl, pin)
            val chainB = checkNotNull(liveTimer())
            timers += chainB
            assertThat(chainB).isNotSameInstanceAs(chainA)

            // Chain A's tick was in flight through all of that and finishes
            // now. Deny-half: it must not continue -- B stays the only chain.
            SyncCoordinator.onTickFinished(chainA)
            assertThat(liveTimer()).isSameInstanceAs(chainB)

            // Allow-half: the CURRENT chain's tick finishing must still
            // schedule its successor, or the loop would die after one tick.
            SyncCoordinator.onTickFinished(chainB)
            val successor = checkNotNull(liveTimer())
            timers += successor
            assertThat(successor).isNotSameInstanceAs(chainB)
            assertThat(SyncCoordinator.health().running).isTrue()
        } finally {
            timers.forEach { it.cancel() }
        }

        // The model above is only as good as the wiring it models: the real
        // TimerTask must end through onTickFinished with ITS OWN Timer, never
        // a bare scheduleNext() (which is the pre-fix race, re-created).
        val code = codeOnly(source("src/main/java/com/actionaura/retail/sync/SyncCoordinator.kt"))
        val scheduleBody = code.substringAfter("private fun scheduleNext(").substringBefore("internal fun onTickFinished(")
        assertThat(scheduleBody).contains("onTickFinished(t)")
        assertThat(scheduleBody).doesNotContain("scheduleNext()")
    }

    /** [SyncCoordinator]'s private `timer` field -- read reflectively (it is
     *  this module's own class, not a JDK internal) so K7 can assert WHICH
     *  chain is live without widening production visibility for a test. */
    private fun liveTimer(): Timer? {
        val field = SyncCoordinator::class.java.getDeclaredField("timer")
        field.isAccessible = true
        val owner = if (java.lang.reflect.Modifier.isStatic(field.modifiers)) null else SyncCoordinator
        return field.get(owner) as Timer?
    }

    // A hub URL that FAILS requireTransportIsSafe loses precedence to the
    // cloud -- and must not lend the cloud its pin. Pinning Owner's cloud
    // relay to a LAN hub's key would fail every cloud handshake forever: a
    // phone with a bad hub record would lose its cloud fallback too. The
    // "only pin when the hub url actually won" rule moved from start() into
    // resolveRelayTarget in this change; mutating it away survived every
    // test in this package, measured.

    @Test
    fun `K8 -- a hub url that loses precedence never lends its pin to the relay that won`() {
        val pin = wellFormedPin()
        val cloud = "https://owner.example.com"

        // Deny-half: cleartext to a non-loopback LAN host is refused, so the
        // cloud wins -- with NO pin.
        assertThat(SyncCoordinator.resolveRelayTarget("", cloud, "http://192.168.1.164:5443", pin))
            .isEqualTo(RelayTarget(cloud, ""))

        // Allow-half: the same hub over https wins, and carries its pin.
        assertThat(SyncCoordinator.resolveRelayTarget("", cloud, "https://192.168.1.164:5443", pin))
            .isEqualTo(RelayTarget("https://192.168.1.164:5443", pin))
    }

    private fun selfSignedHubCert(): HeldCertificate =
        HeldCertificate.Builder()
            .commonName("rebind-tls-hub")
            .addSubjectAlternativeName("127.0.0.1")
            .build()

    private fun serverSslContext(cert: HeldCertificate): SSLContext {
        val handshake = HandshakeCertificates.Builder().heldCertificate(cert).build()
        return SSLContext.getInstance("TLS").apply {
            init(arrayOf(handshake.keyManager), arrayOf(handshake.trustManager), SecureRandom())
        }
    }

    private fun configureOneEventOutbox() {
        for (stream in SyncStream.ALL) {
            server.on(
                stream.prefix + "/_internal/outbox",
                body = """{"installation_id":"inst-1","events":[{"id":"evt-${stream.label}","entity_type":"category","event_type":"create","seq":1}]}""",
            )
        }
    }
}

/** A TLS-terminating stand-in for a paired LAN hub (verifier-added,
 *  2026-09-24): self-signed like the real hub, answering only the two relay
 *  paths. [paths] records a request ONLY after a TLS handshake succeeded and
 *  a request line was read -- a refused handshake throws on the first read
 *  and records nothing, which is exactly the signal K5/K6 assert on. Same
 *  one-request-per-connection shape as [RoutedFakeServer]. */
private class TlsRoutedHub(sslContext: SSLContext) {
    private val serverSocket: ServerSocket = sslContext.serverSocketFactory.createServerSocket(0)
    val port: Int get() = serverSocket.localPort
    val paths = CopyOnWriteArrayList<String>()
    private var acceptThread: Thread? = null
    @Volatile private var stopped = false
    private val routes = mapOf(
        "/api/sync/v1/push" to """{"result":"SUCCESS"}""",
        "/api/sync/v1/pull" to """{"result":"SUCCESS","events":[],"has_more":false}""",
    )

    fun start() {
        acceptThread = Thread {
            while (!stopped) {
                val socket = try {
                    serverSocket.accept()
                } catch (_: Exception) {
                    break
                }
                try {
                    socket.soTimeout = 5000
                    handleOneRequest(socket)
                } catch (_: Exception) {
                    // a refused/aborted handshake lands here: nothing recorded
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
        val path = requestLine.split(" ").getOrNull(1)?.substringBefore('?') ?: return
        var contentLength = 0
        while (true) {
            val line = readLine(input) ?: return
            if (line.isEmpty()) break
            val idx = line.indexOf(':')
            if (idx > 0 && line.substring(0, idx).equals("Content-Length", ignoreCase = true)) {
                contentLength = line.substring(idx + 1).trim().toIntOrNull() ?: 0
            }
        }
        val body = ByteArray(contentLength)
        var read = 0
        while (read < contentLength) {
            val n = input.read(body, read, contentLength - read)
            if (n == -1) break
            read += n
        }
        paths.add(path)
        val status = if (routes.containsKey(path)) 200 else 404
        val bodyOut = (routes[path] ?: """{"error":"no route"}""").toByteArray(Charsets.UTF_8)
        val head = "HTTP/1.1 $status X\r\nContent-Type: application/json\r\n" +
            "Content-Length: ${bodyOut.size}\r\nConnection: close\r\n\r\n"
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
