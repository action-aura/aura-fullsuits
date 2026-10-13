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
 * SYNC-BOOT Phase 1, Android side of the same contract the Python
 * `SyncService` implements (commercial_runtime/sync/tests/
 * test_sync_stream_resync.py): the pull body names its stream, and Owner's
 * 409 RESYNC_REQUIRED becomes a visible state that STOPS that stream's pull
 * (so it can never present a silently partial history) while pushing goes on.
 *
 * Drives the real [SyncCoordinator.runOnce] against [RoutedFakeServer] (which
 * answers both the local `_internal` routes and Owner's relay by path), exactly
 * like [SyncCoordinatorTest].
 *
 * MUTATION PROOFS (run for real): K1 - `pull(since, stream.label)` -> `pull(since)`
 * turns the stream-label test red; K2 - delete the `pending != null` skip in
 * pullOnce turns the "second tick does not contact the relay" test red; K3 -
 * stop recording the state in the catch turns the health test red; K4 -
 * `consecutiveFailures = 1` -> `+ 1` in recordResyncPull turns the backoff test red.
 */
class SyncCoordinatorResyncTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    private lateinit var identity: DeviceIdentity
    private lateinit var server: RoutedFakeServer
    private val logged = mutableListOf<String>()

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
            server.on(stream.prefix + "/_internal/outbox", body = """{"installation_id":"inst-1","events":[{"id":"e-${stream.label}","entity_type":"category","event_type":"create","seq":1}]}""")
            server.on(stream.prefix + "/_internal/outbox/ack", body = """{"result":"SUCCESS"}""")
            server.on(stream.prefix + "/_internal/cursor", body = """{"installation_id":"inst-1","since":3}""")
            server.on(stream.prefix + "/_internal/pull-apply", body = """{"result":"SUCCESS"}""")
        }
        server.on("/api/sync/v1/push", body = """{"result":"SUCCESS"}""")
        server.on("/api/sync/v1/pull", body = """{"events":[],"cursor":3}""")
        SyncCoordinator.logError = { message, _ -> logged.add(message) }
        SyncCoordinator.clearResyncRequired()
    }

    @After
    fun tearDown() {
        server.shutdown()
        SyncCoordinator.clearResyncRequired()
        SyncCoordinator.logError = { message, exc ->
            if (exc != null) android.util.Log.e("SyncCoordinator", message, exc)
            else android.util.Log.e("SyncCoordinator", message)
        }
    }

    private fun base() = "http://127.0.0.1:${server.port}"

    private fun runOnce(relay: String = base()) = SyncCoordinator.runOnce(
        localBaseUrl = base(), internalSecret = "s", relayBaseUrl = relay, identity = identity,
    )

    private fun relayPulls() = server.requests.filter { it.path == "/api/sync/v1/pull" }

    private fun resyncAnswer() =
        server.on("/api/sync/v1/pull", status = 409, body = """{"reason_code":"RESYNC_REQUIRED","pruned_through":41}""")

    @Test
    fun `each stream names itself inside the signed pull body`() {
        runOnce()

        val bodies = relayPulls().map { it.body }
        assertThat(bodies).hasSize(2)
        assertThat(bodies.count { it.contains("\"stream\":\"retail\"") }).isEqualTo(1)
        assertThat(bodies.count { it.contains("\"stream\":\"registry\"") }).isEqualTo(1)
    }

    @Test
    fun `RESYNC_REQUIRED parks the stream - visible in health, pushing continues`() {
        resyncAnswer()

        runOnce()

        val health = SyncCoordinator.health()
        assertThat(health.resyncRequiredStreams).containsExactly("retail", "registry")
        assertThat(health.pull.healthy).isFalse()
        assertThat(health.pull.lastFailureReason).endsWith(":RESYNC_REQUIRED")
        // pushing is untouched: both streams pushed to the relay and acked.
        assertThat(server.requests.count { it.path == "/api/sync/v1/push" }).isEqualTo(2)
        assertThat(server.requests.count { it.path.endsWith("/outbox/ack") }).isEqualTo(2)
        assertThat(health.push.healthy).isTrue()
        // the partial history was never applied
        assertThat(server.requests.count { it.path.endsWith("/pull-apply") }).isEqualTo(0)
    }

    @Test
    fun `a parked stream does not contact the relay again, logs once, and does not grow the backoff`() {
        resyncAnswer()

        runOnce()
        val pullsAfterFirst = relayPulls().size
        runOnce()
        runOnce()

        assertThat(pullsAfterFirst).isEqualTo(2)               // one per stream
        assertThat(relayPulls()).hasSize(2)                    // ticks 2 and 3 skipped the relay
        assertThat(logged.count { it.contains("REQUIRES A RESYNC") }).isEqualTo(1)  // tick 1 only (first failing stream)
        assertThat(SyncCoordinator.health().pull.consecutiveFailures).isEqualTo(1)
        // and nothing else was logged as a generic failure on the later ticks
        assertThat(logged.count { it.contains("Sync pull (") }).isEqualTo(2)
    }

    @Test
    fun `pointing at a different relay leaves the resync state and pulls again`() {
        resyncAnswer()
        runOnce()
        assertThat(SyncCoordinator.health().resyncRequiredStreams).isNotEmpty()

        server.on("/api/sync/v1/pull", body = """{"events":[],"cursor":3}""")
        runOnce(relay = "http://localhost:${server.port}")   // a different relay URL

        assertThat(SyncCoordinator.health().resyncRequiredStreams).isEmpty()
        assertThat(SyncCoordinator.health().pull.healthy).isTrue()
    }

    @Test
    fun `an ordinary pull rejection is still a plain failure, not a resync`() {
        server.on("/api/sync/v1/pull", status = 400, body = """{"reason_code":"INVALID_SIGNATURE"}""")

        runOnce()

        val health = SyncCoordinator.health()
        assertThat(health.resyncRequiredStreams).isEmpty()
        assertThat(health.pull.lastFailureReason).endsWith(":INVALID_SIGNATURE")
    }
}
