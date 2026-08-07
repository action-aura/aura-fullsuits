package com.actionaura.retail.sync

import com.actionaura.retail.licensing.transport.LicensingEnvironment
import com.actionaura.retail.securestorage.InMemorySecureBlobStore
import com.actionaura.retail.securestorage.SecureStorageFixtures
import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull

/**
 * Task 10 (multi-device-sync-foundation) fix pass -- code review found
 * [resolveActiveSyncTransport] never called [SyncRelayConfiguration.validate],
 * so [AuraAppContainer]'s own poll-loop gate (which DOES call `.validate()`
 * before `syncOrchestrator.start()`) could be silently bypassed by
 * `nudge()` -- fired on every category write, independent of whether the
 * poll loop ever started. Regression coverage for the fix: an invalid
 * configuration (here, cleartext HTTP against a `PRODUCTION` environment,
 * one of `SyncRelayConfiguration.validate()`'s own real, enumerated
 * rejections) must resolve to "no transport" exactly like a `null`
 * configuration does -- never attempt a network call.
 */
class SyncActivationResolverTest {

    private val fakeSigner = object : DeviceSigner {
        override suspend fun publicKeyBytes(): ByteArray = ByteArray(32)
        override suspend fun sign(message: ByteArray): ByteArray = ByteArray(64)
    }

    private fun refusingHttpClient(): HttpClient {
        // Fails the test loudly if the resolver ever actually tries to use
        // this client -- an invalid/missing configuration must short-circuit
        // before any real or attempted network call.
        val engine = MockEngine { throw AssertionError("resolveActiveSyncTransport must never reach the network for an invalid/missing configuration") }
        return HttpClient(engine)
    }

    @Test
    fun resolvesNullTransportWhenConfigurationIsNull() = runTest {
        val blobStore = InMemorySecureBlobStore()
        val store = SecureStorageFixtures.store(blobStore)
        val bundle = SecureStorageFixtures.bundle(SecureStorageFixtures.metadata(accountId = null))
        store.commitActivationBundle(bundle)

        val resolved = resolveActiveSyncTransport(
            secureBlobStore = blobStore, secureMaterialStore = store, deviceSigner = fakeSigner,
            httpClient = refusingHttpClient(), configuration = null, productCode = "AURA_RETAIL",
        )
        assertNull(resolved, "a null configuration must resolve to no transport, even with a real committed activation bundle present")
    }

    @Test
    fun resolvesNullTransportWhenConfigurationFailsItsOwnValidation() = runTest {
        val blobStore = InMemorySecureBlobStore()
        val store = SecureStorageFixtures.store(blobStore)
        val bundle = SecureStorageFixtures.bundle(SecureStorageFixtures.metadata(accountId = null))
        store.commitActivationBundle(bundle)

        // Real, enumerated SyncRelayConfiguration.validate() rejection:
        // cleartext HTTP is never permitted in a PRODUCTION environment.
        val invalidConfiguration = SyncRelayConfiguration(environment = LicensingEnvironment.PRODUCTION, baseUrl = "http://relay.example.com")
        assertEquals(
            true,
            invalidConfiguration.validate() is SyncRelayConfigurationValidationResult.Invalid,
            "sanity: this configuration must actually be invalid, or this test proves nothing",
        )

        val resolved = resolveActiveSyncTransport(
            secureBlobStore = blobStore, secureMaterialStore = store, deviceSigner = fakeSigner,
            httpClient = refusingHttpClient(), configuration = invalidConfiguration, productCode = "AURA_RETAIL",
        )
        assertNull(resolved, "an invalid configuration must resolve to no transport, exactly like a null configuration -- this is the real gap this test locks in: nudge() must never reach the network with a config this module's own security rules reject")
    }

    @Test
    fun resolvesNullTransportWhenNoActivationBundleExists() = runTest {
        val blobStore = InMemorySecureBlobStore()
        val store = SecureStorageFixtures.store(blobStore)
        val validConfiguration = SyncRelayConfiguration(environment = LicensingEnvironment.DEVELOPMENT, baseUrl = "http://127.0.0.1:5551")

        val resolved = resolveActiveSyncTransport(
            secureBlobStore = blobStore, secureMaterialStore = store, deviceSigner = fakeSigner,
            httpClient = refusingHttpClient(), configuration = validConfiguration, productCode = "AURA_RETAIL",
        )
        assertNull(resolved, "no committed activation bundle -- must resolve to no transport")
    }

    @Test
    fun resolvesARealTransportWhenBothAValidConfigurationAndARealCommittedBundleExist() = runTest {
        val blobStore = InMemorySecureBlobStore()
        val store = SecureStorageFixtures.store(blobStore)
        val bundle = SecureStorageFixtures.bundle(SecureStorageFixtures.metadata(installationId = "fixture-installation-real", accountId = null))
        store.commitActivationBundle(bundle)
        val validConfiguration = SyncRelayConfiguration(environment = LicensingEnvironment.DEVELOPMENT, baseUrl = "http://127.0.0.1:5551")
        val engine = MockEngine { respond("""{"stored":0,"received":0}""", HttpStatusCode.OK, headersOf(HttpHeaders.ContentType, "application/json")) }

        val resolved = resolveActiveSyncTransport(
            secureBlobStore = blobStore, secureMaterialStore = store, deviceSigner = fakeSigner,
            httpClient = HttpClient(engine), configuration = validConfiguration, productCode = "AURA_RETAIL",
        )
        assertNotNull(resolved, "a valid configuration plus a real committed activation bundle must resolve to a real, usable SyncTransport")
    }
}
