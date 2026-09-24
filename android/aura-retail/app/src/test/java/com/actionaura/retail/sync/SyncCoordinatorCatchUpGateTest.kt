package com.actionaura.retail.sync

import com.actionaura.retail.licensing.DeviceIdentity
import com.actionaura.retail.licensing.KeyWrapper
import com.google.common.truth.Truth.assertThat
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.spec.GCMParameterSpec

/**
 * [SyncCoordinator.readyToJoinHub]'s catch-up gate -- see that function's
 * own doc comment for WHY it exists: closes part of gap 0.5/G3 from this
 * task's plan. The phone-side re-point (`SyncCoordinatorRebindTest`) makes
 * a hub join take effect within about a minute of `HubAutoJoinService`'s
 * own polling interval; without this gate, a device could join a hub
 * before it finished pulling its OWN pre-hub history down from Owner's
 * cloud -- and the cloud never hands a device back its own events (0.4 in
 * this task's plan), so anything still sitting in the cloud at the moment
 * of a premature join is gone for good from this device's point of view.
 *
 * Drives real ticks against a [RoutedFakeServer] (same harness
 * [SyncCoordinatorTest]/`SyncCoordinatorRebindTest` already use) so the
 * "did a real pull actually see a full page" accounting in
 * [SyncCoordinator.runOnce]'s tail is exercised end to end, never asserted
 * against a hand-built fixture that could silently drift from what a real
 * pull response shape actually is. Every relay target used here stays
 * REACHABLE (a real [RoutedFakeServer], answering immediately) rather than
 * a genuinely unreachable host -- see `SyncCoordinatorRebindTest`'s own K1
 * comment for why: [SyncRelayClient] retries a real connection failure up
 * to 4 times with real `Thread.sleep` backoff, which would make a test
 * needlessly slow for no assertion value.
 */
class SyncCoordinatorCatchUpGateTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    private lateinit var identity: DeviceIdentity
    private lateinit var server: RoutedFakeServer
    private var fakeNow = 1_000_000_000L

    /** Copied verbatim from [SyncCoordinatorTest]'s own private
     *  `TestKeyWrapper`, for the identical reason `SyncCoordinatorRebindTest`
     *  keeps its own nested copy: a small, self-contained double, not a
     *  cross-file private reach. */
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
        // Stopped first -- see SyncCoordinatorRebindTest's identical note:
        // this shared singleton `object` must not carry running=true (or a
        // stale target/catch-up state) in from whatever test ran before
        // this one in the same JVM.
        SyncCoordinator.stop()
        identity = DeviceIdentity(tempFolder.newFolder(), TestKeyWrapper())
        identity.generateNewKey()
        server = RoutedFakeServer()
        server.start()
        configureHappyPath()
        SyncCoordinator.logError = { _, _ -> }
        fakeNow = 1_000_000_000L
        SyncCoordinator.clock = { fakeNow }
    }

    @After
    fun tearDown() {
        server.shutdown()
        SyncCoordinator.stop()
        SyncCoordinator.logError = { message, exc ->
            if (exc != null) android.util.Log.e("SyncCoordinator", message, exc)
            else android.util.Log.e("SyncCoordinator", message)
        }
        SyncCoordinator.clock = { System.currentTimeMillis() }
    }

    private fun baseUrl() = "http://127.0.0.1:${server.port}"

    /** [pullEventCount] events come back from EVERY stream's pull -- Owner's
     *  relay is not duplicated per stream (see [SyncStream]'s own class
     *  doc: `/api/sync/v1/pull` ingests/returns regardless of which local
     *  stream is asking), and [RoutedFakeServer] routes purely by PATH, so
     *  there is no way to give one stream a full page and the other an
     *  empty one against this fake. Giving BOTH streams the same page size
     *  is still a faithful (if strictly stronger) exercise of
     *  [SyncCoordinator]'s own `allBelowPage = SyncStream.ALL.all { ... }`
     *  check, which requires EVERY stream below the limit to count as
     *  caught up. */
    private fun configureHappyPath(pullEventCount: Int = 0) {
        for (stream in SyncStream.ALL) {
            server.on(stream.prefix + "/_internal/outbox", body = """{"installation_id":"inst-1","events":[]}""")
            server.on(stream.prefix + "/_internal/outbox/ack", body = """{"result":"SUCCESS"}""")
            server.on(stream.prefix + "/_internal/cursor", body = """{"installation_id":"inst-1","since":0}""")
            server.on(stream.prefix + "/_internal/pull-apply", body = """{"result":"SUCCESS"}""")
        }
        server.on("/api/sync/v1/push", body = """{"result":"SUCCESS"}""")
        val events = (1..pullEventCount).joinToString(",") { i ->
            """{"id":"evt-$i","entity_type":"category","event_type":"create","seq":$i}"""
        }
        server.on("/api/sync/v1/pull", body = """{"result":"SUCCESS","events":[$events],"has_more":false}""")
    }

    private fun tick() {
        SyncCoordinator.runOnceAgainstCurrentTarget(baseUrl(), "test-internal-secret", identity)
    }

    // -- "nothing to catch up from" cases -- always ready ------------------

    @Test
    fun `not running -- ready, because there is nothing to catch up from`() {
        assertThat(SyncCoordinator.health().running).isFalse()

        assertThat(SyncCoordinator.readyToJoinHub(nowMillis = fakeNow)).isTrue()
    }

    @Test
    fun `already on a paired hub -- ready, because this device is not coming from the cloud at all`() {
        val pin = SpkiPinning.pinOf(HeldCertificate.Builder().commonName("catchup-gate-hub").build().certificate)
        // http:// is a legal hub candidate on loopback -- see
        // requireTransportIsSafe's CLEARTEXT_PERMITTED_HOSTS -- and this
        // test never ticks, so no network call is even attempted; only
        // resolveRelayTarget's precedence and readyToJoinHub's own state
        // machine are exercised here.
        SyncCoordinator.start(tempFolder.newFolder(), "", null, baseUrl(), pin)
        assertThat(SyncCoordinator.currentRelayTarget()).isEqualTo(RelayTarget(baseUrl(), pin))

        assertThat(SyncCoordinator.readyToJoinHub(nowMillis = fakeNow)).isTrue()
    }

    // -- on the cloud, with no catch-up observed yet -- not ready ----------

    @Test
    fun `running on the cloud before any tick -- not ready`() {
        SyncCoordinator.start(tempFolder.newFolder(), "", baseUrl())

        assertThat(SyncCoordinator.readyToJoinHub(nowMillis = fakeNow)).isFalse()
    }

    // -- a real tick that actually caught up -- ready ----------------------

    @Test
    fun `after a tick where every stream's pull returns a partial (here, empty) page -- ready`() {
        SyncCoordinator.start(tempFolder.newFolder(), "", baseUrl())

        tick()

        assertThat(SyncCoordinator.readyToJoinHub(nowMillis = fakeNow)).isTrue()
    }

    // -- a real tick that did NOT catch up -- still not ready --------------

    @Test
    fun `after a tick where a pull returns a full page -- still not ready, more is certainly behind it`() {
        // Mirrors CLOUD_PULL_PAGE_LIMIT's real value (500, private in
        // SyncCoordinator.kt) -- see this test class's own doc comment on
        // why the fake cannot isolate the full page to just one stream.
        configureHappyPath(pullEventCount = 500)
        SyncCoordinator.start(tempFolder.newFolder(), "", baseUrl())

        tick()

        assertThat(SyncCoordinator.readyToJoinHub(nowMillis = fakeNow)).isFalse()
    }

    // -- the grace period: never reached the cloud at all, still eventually joins --

    @Test
    fun `stuck on the cloud with no successful catch-up -- ready once the ten-minute grace period elapses`() {
        SyncCoordinator.start(tempFolder.newFolder(), "", baseUrl())
        assertThat(SyncCoordinator.readyToJoinHub(nowMillis = fakeNow)).isFalse()

        val justBeforeGrace = fakeNow + 10 * 60_000L - 1
        assertThat(SyncCoordinator.readyToJoinHub(nowMillis = justBeforeGrace)).isFalse()

        val atGrace = fakeNow + 10 * 60_000L
        assertThat(SyncCoordinator.readyToJoinHub(nowMillis = atGrace)).isTrue()
    }

    // -- verifier-added, 2026-09-24: a catch-up does not outlive its target --
    //
    // markTargetChanged() resets cloudCaughtUpAtMillis on every re-point
    // (its own doc comment: "a caught-up observation ... against the OLD
    // target says nothing about the new one"). Deleting that reset survived
    // every test in this package, measured -- no test ever took a caught-up
    // device round a hub and back. Without the reset, a phone that caught up
    // once, joined a hub, and was later unpaired would re-join the next hub
    // it hears with NO fresh catch-up, carrying a stale "caught up" forever.

    @Test
    fun `a catch-up observed before a hub round trip does not survive it -- back on the cloud the gate starts fresh`() {
        val dir = tempFolder.newFolder()
        SyncCoordinator.start(dir, "", baseUrl())
        tick()
        assertThat(SyncCoordinator.readyToJoinHub(nowMillis = fakeNow)).isTrue()

        val pin = SpkiPinning.pinOf(HeldCertificate.Builder().commonName("catchup-roundtrip-hub").build().certificate)
        SyncCoordinator.onHubPairingChanged(dir, "https://127.0.0.1:9", pin)
        SyncCoordinator.onHubPairingChanged(dir, null, null)
        assertThat(SyncCoordinator.currentRelayTarget()).isEqualTo(RelayTarget(baseUrl(), ""))

        assertThat(SyncCoordinator.readyToJoinHub(nowMillis = fakeNow)).isFalse()
    }
}
