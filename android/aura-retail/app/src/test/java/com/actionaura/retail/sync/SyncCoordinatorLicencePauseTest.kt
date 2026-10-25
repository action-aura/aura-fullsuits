package com.actionaura.retail.sync

import com.actionaura.retail.licensing.DeviceIdentity
import com.actionaura.retail.licensing.KeyWrapper
import com.google.common.truth.Truth.assertThat
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
 * Keep-selling rule on Android: when the subscription is UNPAID the shop keeps selling but multi-device
 * sync pauses. Kotlin does all the network sync, only Python can read the licence, so every tick first
 * asks `/api/sync/_internal/pause-status`. These tests drive the real `runOnce` / `nudgeAgainstCurrentTarget`
 * against [RoutedFakeServer] (which plays both the local backend and Owner's relay) and assert on the
 * RECORDED REQUEST LIST -- that the gate ran (pause-status was asked) and that nothing else was touched.
 *
 * The fail-open cases matter as much as the pause: a 404 (older backend / the fakes every other suite
 * uses), a 500, a malformed body or a missing key must all mean "not paused, sync runs".
 */
class SyncCoordinatorLicencePauseTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    private lateinit var identity: DeviceIdentity
    private lateinit var server: RoutedFakeServer
    private val infoLines = mutableListOf<String>()
    private val errorLines = mutableListOf<String>()

    private val pauseStatus = "/api/sync/_internal/pause-status"

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
        for (stream in SyncStream.ALL) {
            server.on(stream.prefix + "/_internal/outbox",
                body = """{"installation_id":"inst-1","events":[{"id":"evt-${stream.label}","entity_type":"category","event_type":"create","seq":1}]}""")
            server.on(stream.prefix + "/_internal/outbox/ack", body = """{"result":"SUCCESS"}""")
            server.on(stream.prefix + "/_internal/cursor", body = """{"installation_id":"inst-1","since":0}""")
            server.on(stream.prefix + "/_internal/pull-apply", body = """{"result":"SUCCESS"}""")
        }
        server.on("/api/sync/v1/push", body = """{"result":"SUCCESS"}""")
        server.on("/api/sync/v1/pull", body = """{"result":"SUCCESS","events":[],"has_more":false}""")
        SyncCoordinator.logError = { message, _ -> errorLines.add(message) }
        SyncCoordinator.logInfo = { message -> infoLines.add(message) }
        // The singleton survives across tests in one JVM: reset what this file touches by hand.
        SyncCoordinator.pausedByLicence = false
        SyncCoordinator.clearResyncRequired()
    }

    @After
    fun tearDown() {
        server.shutdown()
        SyncCoordinator.pausedByLicence = false
        SyncCoordinator.clearResyncRequired()
        SyncCoordinator.logInfo = { message -> android.util.Log.i("SyncCoordinator", message) }
        SyncCoordinator.logError = { message, exc ->
            if (exc != null) android.util.Log.e("SyncCoordinator", message, exc)
            else android.util.Log.e("SyncCoordinator", message)
        }
    }

    private fun base() = "http://127.0.0.1:${server.port}"

    private fun runOnce() = SyncCoordinator.runOnce(
        localBaseUrl = base(), internalSecret = "test-internal-secret",
        relayBaseUrl = base(), identity = identity,
    )

    private fun nudge() = SyncCoordinator.nudgeAgainstCurrentTarget(
        localBaseUrl = base(), internalSecret = "test-internal-secret", identity = identity,
    )

    private fun paths() = server.requests.map { it.path }

    private fun answerPause(paused: Boolean) =
        server.on(pauseStatus, body = """{"paused_by_licence":$paused}""")

    private fun assertNoSyncTraffic() {
        // The gate ran (it asked) ...
        assertThat(paths()).contains(pauseStatus)
        // ... and then NOTHING else: no outbox read, no relay call, no ack, no pull-apply, no cursor.
        assertThat(paths().filter { it != pauseStatus }).isEmpty()
    }

    // -- paused ------------------------------------------------------------------

    @Test
    fun `paused tick makes no request except pause-status and records no failure`() {
        answerPause(true)
        // The coordinator is a singleton and other tests in this JVM leave push/pull health behind, so
        // "no failure recorded" is asserted as "health is exactly what it was", not as "counters are zero".
        val before = SyncCoordinator.health()

        runOnce()

        assertNoSyncTraffic()
        val health = SyncCoordinator.health()
        assertThat(health.pausedByLicence).isTrue()
        // A deliberate pause is neither a success nor a failure: health bookkeeping untouched.
        assertThat(health.push).isEqualTo(before.push)
        assertThat(health.pull).isEqualTo(before.pull)
        assertThat(errorLines).isEmpty()
    }

    @Test
    fun `nudge while paused pushes nothing`() {
        answerPause(true)

        nudge()

        assertNoSyncTraffic()
        assertThat(SyncCoordinator.health().pausedByLicence).isTrue()
    }

    @Test
    fun `the pause probe is a GET carrying X-Aura-Internal-Secret with the internal secret`() {
        answerPause(true)
        runOnce()
        // G2 (verifier): a probe with a wrong/missing secret header gets a 403 from the real route, which
        // FAILS OPEN -- a lapsed phone would keep syncing with every other test green. So the header itself
        // is asserted, name and value (RoutedFakeServer lower-cases header names).
        val probe = server.requests.single { it.path == pauseStatus }
        assertThat(probe.method).isEqualTo("GET")
        assertThat(probe.headers["x-aura-internal-secret"]).isEqualTo("test-internal-secret")
    }

    // -- G4: resume must not wait out old backoff -------------------------------------

    @Test
    fun `renewal resets the failure backoff so the first resumed tick is scheduled at the base delay`() {
        server.on("/api/sync/_internal/outbox", status = 500, body = "boom")
        runOnce(); runOnce(); runOnce()
        val base = 10_000L // INTERVAL_SECONDS * 1000
        assertThat(SyncCoordinator.nextDelayMillis()).isGreaterThan(base) // failures really are backing off

        answerPause(true)
        SyncCoordinator.licencePausesSync(base(), "test-internal-secret")
        answerPause(false)
        SyncCoordinator.licencePausesSync(base(), "test-internal-secret") // paused -> not paused

        assertThat(SyncCoordinator.nextDelayMillis()).isEqualTo(base)
    }

    // -- renewal -------------------------------------------------------------------

    @Test
    fun `after renewal the next tick pushes the queued outbox and pulls`() {
        answerPause(true)
        runOnce()
        assertNoSyncTraffic()
        server.requests.clear()

        answerPause(false) // the licence check-in landed
        runOnce()

        assertThat(SyncCoordinator.health().pausedByLicence).isFalse()
        val hit = paths()
        for (stream in SyncStream.ALL) {
            assertThat(hit).contains(stream.prefix + "/_internal/outbox")
            assertThat(hit).contains(stream.prefix + "/_internal/outbox/ack")
            assertThat(hit).contains(stream.prefix + "/_internal/cursor")
            assertThat(hit).contains(stream.prefix + "/_internal/pull-apply")
        }
        assertThat(hit).contains("/api/sync/v1/push")
        assertThat(hit).contains("/api/sync/v1/pull")
    }

    @Test
    fun `entering and leaving the pause each log exactly one line, not one per tick`() {
        answerPause(true)
        runOnce(); runOnce(); runOnce()
        assertThat(infoLines).hasSize(1)
        assertThat(infoLines[0]).contains("paused")

        answerPause(false)
        runOnce(); runOnce()
        assertThat(infoLines).hasSize(2)
        assertThat(infoLines[1]).contains("resumed")
    }

    // -- fail open -------------------------------------------------------------------

    private fun assertSyncRanDespitePauseProbe() {
        assertThat(paths()).contains(pauseStatus) // the gate asked ...
        assertThat(paths()).contains("/api/sync/_internal/outbox") // ... and sync still ran
        assertThat(paths()).contains("/api/sync/v1/push")
        assertThat(SyncCoordinator.health().pausedByLicence).isFalse()
    }

    @Test
    fun `a 404 from pause-status means not paused and sync runs`() {
        // RoutedFakeServer answers 404 for any path nobody configured: this is every other suite's world.
        runOnce()
        assertSyncRanDespitePauseProbe()
    }

    @Test
    fun `a 500 from pause-status means not paused and sync runs`() {
        server.on(pauseStatus, status = 500, body = "boom")
        runOnce()
        assertSyncRanDespitePauseProbe()
    }

    @Test
    fun `a malformed body from pause-status means not paused and sync runs`() {
        server.on(pauseStatus, body = "<html>not json</html>")
        runOnce()
        assertSyncRanDespitePauseProbe()
    }

    @Test
    fun `a body missing the key means not paused and sync runs`() {
        server.on(pauseStatus, body = """{"something_else":true}""")
        runOnce()
        assertSyncRanDespitePauseProbe()
    }

    @Test
    fun `a non-boolean value means not paused and sync runs`() {
        server.on(pauseStatus, body = """{"paused_by_licence":"true"}""")
        runOnce()
        assertSyncRanDespitePauseProbe()
    }

    @Test
    fun `nudge fails open too`() {
        server.on(pauseStatus, status = 500, body = "boom")
        nudge()
        // nudge pushes to the coordinator's own `target` (blank in this harness, so no relay call is
        // observable here); the local outbox read is the proof that the push pass ran.
        assertThat(paths()).contains("/api/sync/_internal/outbox")
    }
}
