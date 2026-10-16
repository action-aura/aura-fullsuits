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
 * Emulator run 2026-10-16: a late-joining phone's registry cursor stayed 0 forever, so it never received
 * a user and could not log in. Owner answered the registry pull with 409 RESYNC_REQUIRED carrying
 * fast_forward_to; Kotlin dropped the number and parked the stream. The rule now lives in Python
 * (the local registry fast-forward route, shared with the desktop) and Kotlin calls it, then resumes the
 * pull from the new cursor in the same tick.
 *
 * MUTATION PROOFS (run for real): K1 - drop fast_forward_to in raiseIfRejected turns the "moves the
 * cursor" and "resumes from" tests red; K2 - do not call the local route turns the same two red;
 * K3 - drop the registry-only guard in pullOnce turns the retail test red.
 */
class SyncCoordinatorRegistryFastForwardTest {

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

    /** The local registry cursor; the fake seam moves it, as the real Python does. */
    @Volatile private var registryCursor = 0L
    private val seamPath = SyncStream.REGISTRY.prefix + "/_internal/registry/fast-forward"
    private val retailSeamPath = SyncStream.RETAIL.prefix + "/_internal/registry/fast-forward"

    @Before
    fun setUp() {
        identity = DeviceIdentity(tempFolder.newFolder(), TestKeyWrapper())
        identity.generateNewKey()
        server = RoutedFakeServer()
        server.start()
        registryCursor = 0L
        for (stream in SyncStream.ALL) {
            server.on(stream.prefix + "/_internal/outbox", body = """{"installation_id":"inst-1","events":[]}""")
            server.on(stream.prefix + "/_internal/outbox/ack", body = """{"result":"SUCCESS"}""")
            server.on(stream.prefix + "/_internal/pull-apply", body = """{"result":"SUCCESS"}""")
        }
        server.on("/api/sync/v1/push", body = """{"result":"SUCCESS"}""")
        server.on(SyncStream.RETAIL.prefix + "/_internal/cursor", body = """{"installation_id":"inst-1","since":3}""")
        server.onDynamic(SyncStream.REGISTRY.prefix + "/_internal/cursor") {
            200 to """{"installation_id":"inst-1","since":$registryCursor}"""
        }
        server.onDynamic(seamPath) { req ->
            val target = Regex(""""fast_forward_to":(\d+)""").find(req.body)!!.groupValues[1].toLong()
            if (target >= registryCursor) registryCursor = target
            200 to """{"result":"SUCCESS","fast_forwarded":true,"since":$registryCursor}"""
        }
        // Owner: a registry pull from below 680 is refused and told where the log now ends; from 680 it
        // gets the main computer's re-announced events. The retail stream is refused with a target too,
        // which must be ignored (retail history is never skipped).
        server.onDynamic("/api/sync/v1/pull") { req ->
            val registry = req.body.contains("\"stream\":\"registry\"")
            when {
                registry && !req.body.contains("\"since\":680") ->
                    409 to """{"reason_code":"RESYNC_REQUIRED","pruned_through":600,"fast_forward_to":680}"""
                registry -> 200 to """{"events":[{"id":"u1"}],"cursor":712}"""
                else -> 409 to """{"reason_code":"RESYNC_REQUIRED","pruned_through":41,"fast_forward_to":99}"""
            }
        }
        SyncCoordinator.logError = { _, _ -> }
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
    private fun runOnce() = SyncCoordinator.runOnce(
        localBaseUrl = base(), internalSecret = "s", relayBaseUrl = base(), identity = identity,
    )
    private fun registryPulls() =
        server.requests.filter { it.path == "/api/sync/v1/pull" && it.body.contains("\"stream\":\"registry\"") }

    @Test
    fun `a registry RESYNC_REQUIRED with fast_forward_to moves the cursor through the local route`() {
        runOnce()

        val seamCalls = server.requests.filter { it.path == seamPath }
        assertThat(seamCalls).hasSize(1)
        assertThat(seamCalls[0].body).contains("\"fast_forward_to\":680")
        assertThat(registryCursor).isEqualTo(680L)
        assertThat(SyncCoordinator.health().resyncRequiredStreams).doesNotContain("registry")
    }

    @Test
    fun `the registry pull resumes from fast_forward_to and applies the re-announced events`() {
        runOnce()

        val pulls = registryPulls()
        assertThat(pulls).hasSize(2)
        assertThat(pulls[0].body).contains("\"since\":0")
        assertThat(pulls[1].body).contains("\"since\":680")
        val applied = server.requests.filter {
            it.path == SyncStream.REGISTRY.prefix + "/_internal/pull-apply" && it.body.contains("\"u1\"")
        }
        assertThat(applied).hasSize(1)
    }

    @Test
    fun `the retail stream is never fast-forwarded - it parks as before`() {
        runOnce()

        assertThat(SyncCoordinator.health().resyncRequiredStreams).contains("retail")
        assertThat(server.requests.filter { it.path == retailSeamPath }).isEmpty()
    }
}
