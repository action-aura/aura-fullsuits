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
 * SYNC-BOOT Phase 3, Android: the coordinator's bootstrap wiring against the same routed fake server as
 * SyncCoordinatorResyncTest. Each test has its opposite beside it (staged vs waiting vs failing vs healthy).
 */
class SyncCoordinatorBootstrapTest {

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
        SyncCoordinator.bootstrapRequesterOverride = null
        SyncCoordinator.onBootstrapStaged = {}
        SyncCoordinator.lastBootstrapStepMillis = 0L
        SyncCoordinator.bootstrapNeedsSupport = false
    }

    @After
    fun tearDown() {
        server.shutdown()
        SyncCoordinator.clock = { System.currentTimeMillis() }
        SyncCoordinator.bootstrapRequesterOverride = null
        SyncCoordinator.onBootstrapStaged = {}
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

    private var clockNow = 1_000_000L
    private var restarts = 0
    private var steps = 0

    private fun arm(outcome: () -> RequesterOutcome) {
        SyncCoordinator.clock = { clockNow }
        SyncCoordinator.setBootstrapStagedHandler { restarts++ }
        SyncCoordinator.bootstrapRequesterOverride = { BootstrapStepper { steps++; outcome() } }
    }

    @Test
    fun `a staged copy restarts the app exactly once`() {
        arm { RequesterOutcome.STAGED }
        resyncAnswer()
        runOnce()                        // tick 1: told RESYNC_REQUIRED, the stream parks
        assertThat(restarts).isEqualTo(0)
        clockNow += 60_000
        runOnce()                        // tick 2: parked -> the requester steps -> STAGED -> restart requested
        assertThat(steps).isEqualTo(1)
        assertThat(restarts).isEqualTo(1)
    }

    @Test
    fun `waiting for a producer does not restart anything`() {
        arm { RequesterOutcome.WAITING }
        resyncAnswer()
        runOnce()
        clockNow += 60_000
        runOnce()
        assertThat(steps).isEqualTo(1)
        assertThat(restarts).isEqualTo(0)
    }

    @Test
    fun `the requester steps are throttled, not one per ten second tick`() {
        arm { RequesterOutcome.WAITING }
        resyncAnswer()
        runOnce()
        clockNow += 60_000
        runOnce(); runOnce(); runOnce()  // three ticks inside the throttle window
        assertThat(steps).isEqualTo(1)
        clockNow += 60_000
        runOnce()
        assertThat(steps).isEqualTo(2)
    }

    @Test
    fun `a failing step never breaks the tick and never restarts`() {
        arm { throw IllegalStateException("owner unreachable") }
        resyncAnswer()
        runOnce()
        clockNow += 60_000
        runOnce()                        // must not throw
        assertThat(restarts).isEqualTo(0)
        assertThat(logged.any { it.contains("Bootstrap request step failed") }).isTrue()
    }

    @Test
    fun `needs-support shows in health, is cleared by the next outcome, and never restarts`() {
        var outcome = RequesterOutcome.NEEDS_SUPPORT
        arm { outcome }
        resyncAnswer()
        runOnce()
        clockNow += 60_000
        runOnce()
        assertThat(SyncCoordinator.health().bootstrapNeedsSupport).isTrue()
        assertThat(restarts).isEqualTo(0)
        // support cleared it on the device: the next step reports something else, the sentence goes away
        outcome = RequesterOutcome.WAITING
        clockNow += 60_000
        runOnce()
        assertThat(SyncCoordinator.health().bootstrapNeedsSupport).isFalse()
    }

    @Test
    fun `a successful retail pull clears a stale needs-support flag`() {
        SyncCoordinator.bootstrapNeedsSupport = true
        runOnce()                                    // healthy tick: the copy worked, nothing to ask for
        assertThat(SyncCoordinator.health().bootstrapNeedsSupport).isFalse()
    }

    @Test
    fun `no requester step happens while the stream is healthy`() {
        arm { RequesterOutcome.STAGED }
        runOnce()
        clockNow += 60_000
        runOnce()
        assertThat(steps).isEqualTo(0)
        assertThat(restarts).isEqualTo(0)
    }

    @Test
    fun `pull asks for the one-shot own events only when the backend armed it, and spends it only when Owner echoes`() {
        server.on("/api/sync/_internal/cursor", body = """{"installation_id":"inst-1","since":3,"include_own":true}""")
        server.on("/api/sync/v1/pull", body = """{"events":[],"cursor":3,"include_own":true}""")
        server.on("/api/sync/_internal/bootstrap/include-own-done", body = """{"result":"SUCCESS"}""")
        runOnce()
        val retailPull = relayPulls().first { it.body.contains("\"stream\":\"retail\"") }
        assertThat(retailPull.body).contains("\"include_own\":true")
        assertThat(relayPulls().first { it.body.contains("\"stream\":\"registry\"") }.body).doesNotContain("include_own")
        assertThat(server.requests.count { it.path.endsWith("/include-own-done") }).isEqualTo(1)
    }

    @Test
    fun `an Owner that does not echo include_own leaves the flag armed`() {
        server.on("/api/sync/_internal/cursor", body = """{"installation_id":"inst-1","since":3,"include_own":true}""")
        server.on("/api/sync/v1/pull", body = """{"events":[],"cursor":3}""")
        server.on("/api/sync/_internal/bootstrap/include-own-done", body = """{"result":"SUCCESS"}""")
        runOnce()
        assertThat(server.requests.count { it.path.endsWith("/include-own-done") }).isEqualTo(0)
    }
}
