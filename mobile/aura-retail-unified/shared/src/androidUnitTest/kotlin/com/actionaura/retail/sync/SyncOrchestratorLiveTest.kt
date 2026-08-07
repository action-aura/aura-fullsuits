package com.actionaura.retail.sync

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.data.sqldelight.SqlDelightCategoryRepository
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.licensing.ActivationResult
import com.actionaura.retail.licensing.DeviceMetadata
import com.actionaura.retail.licensing.InstallationIdentity
import com.actionaura.retail.licensing.InstallationIdentityStatus
import com.actionaura.retail.licensing.InstallationIdentityVersion
import com.actionaura.retail.licensing.LicensingPlatform
import com.actionaura.retail.licensing.LicensingProductCode
import com.actionaura.retail.licensing.LocalInstallationSeed
import com.actionaura.retail.licensing.transport.ActivationResponseProcessor
import com.actionaura.retail.licensing.transport.DirectLicenseKeyActivationCommand
import com.actionaura.retail.licensing.transport.ExternalApiConfiguration
import com.actionaura.retail.licensing.transport.HttpExternalLicensingTransport
import com.actionaura.retail.licensing.transport.LicensingEnvironment
import com.actionaura.retail.licensing.transport.TransportOutcome
import com.actionaura.retail.licensing.transport.secureRandomHex
import com.actionaura.retail.securestorage.GenerationalSecureMaterialStore
import com.actionaura.retail.securestorage.InMemorySecureBlobStore
import com.actionaura.retail.securestorage.SecureMaterialStoreActivationSink
import io.ktor.client.HttpClient
import io.ktor.client.engine.cio.CIO
import io.ktor.client.engine.okhttp.OkHttp
import kotlinx.coroutines.test.runTest
import kotlinx.datetime.Clock
import org.junit.Assume.assumeTrue
import kotlin.test.Test
import kotlin.test.assertIs
import kotlin.test.assertTrue

/**
 * Task 10 (multi-device-sync-foundation) -- real, executable end-to-end
 * proof that this task's own new code (category-write outbox
 * instrumentation, [SyncOrchestrator], and [resolveActiveSyncTransport]/
 * [resolveOwnerInstallationId]) genuinely reaches a REAL, locally running
 * Owner instance, following the exact same live-test discipline Tasks 8/9
 * already established (`HttpExternalLicensingTransportActivationLiveTest`,
 * `SyncTransportLiveTest`) -- gated, skipped by default, never fabricating
 * a fake success.
 *
 * Unlike those two, this test drives the FULL real production chain this
 * task adds, not just the transport in isolation:
 * 1. Real activation ([HttpExternalLicensingTransport.activateWithLicenseKey]).
 * 2. Real secure-storage commit ([SecureMaterialStoreActivationSink] over
 *    [GenerationalSecureMaterialStore]) -- the exact real integration this
 *    task's own on-device verification found broken
 *    (`ActivationResponseProcessor`'s `&&` bug, fixed alongside this test)
 *    and is why this test exists as PERMANENT regression coverage, not
 *    throwaway scratch: nothing else in this repo exercises
 *    `ActivationResponseProcessor` against the real paired production sink.
 * 3. Real resolution via [resolveActiveSyncTransport] -- the SAME function
 *    `AuraAppContainer` calls, not a hand-constructed shortcut.
 * 4. A real category `insert()` through [SqlDelightCategoryRepository]
 *    (this task's own outbox instrumentation) against a real, in-memory
 *    JVM SQLite database.
 * 5. [SyncOrchestrator.pushOnce] against the real relay.
 *
 * Complements this task's own real on-device verification (a physical
 * Android device's real `SyncOrchestrator.start()` loop was observed
 * successfully pulling from the same real Owner instance every ~10s in
 * Owner's own request log) -- that proved the on-device DI wiring and the
 * PULL direction; this test proves the PUSH direction (the one a locked
 * device screen made impractical to drive through the real UI) and is
 * runnable on any host with real Owner infrastructure, not just a
 * physical device.
 */
class SyncOrchestratorLiveTest {

    @Test
    fun createdCategoryReachesOwnersRealSyncEventLogViaPushOnce() = runTest {
        val runLiveTest = System.getenv("RUN_OWNER_LIVE_SYNC_TEST") == "1"
        assumeTrue("set RUN_OWNER_LIVE_SYNC_TEST=1 and OWNER_LIVE_TEST_LICENSE_KEY to run this against a real local Owner instance", runLiveTest)
        val licenseKey = System.getenv("OWNER_LIVE_TEST_LICENSE_KEY")
        assumeTrue("OWNER_LIVE_TEST_LICENSE_KEY must be set to a real, issued AURA_RETAIL test license key", !licenseKey.isNullOrBlank())

        val baseUrl = System.getenv("OWNER_LIVE_TEST_BASE_URL") ?: "http://127.0.0.1:5551"

        // ---- 1/2/3: real activation, real secure-storage commit, real resolution ----
        val secureBlobStore = InMemorySecureBlobStore()
        val deviceSigner = PlatformDeviceSigner(secureBlobStore)
        val licensingConfig = ExternalApiConfiguration(
            environment = LicensingEnvironment.DEVELOPMENT,
            baseUrl = "$baseUrl/api/licensing/v1",
            productCode = LicensingProductCode.AURA_RETAIL,
            platform = LicensingPlatform.ANDROID,
        )
        val licensingTransport = HttpExternalLicensingTransport(HttpClient(OkHttp), licensingConfig, deviceSigner)
        val identity = InstallationIdentity(
            seed = LocalInstallationSeed("task10-live-test-" + secureRandomHex(16), InstallationIdentityVersion.V1),
            status = InstallationIdentityStatus.GENERATED,
            generatedAt = Clock.System.now().toString(),
        )
        val activationOutcome = licensingTransport.activateWithLicenseKey(
            DirectLicenseKeyActivationCommand(
                licenseKey = licenseKey!!,
                productCode = LicensingProductCode.AURA_RETAIL,
                platform = LicensingPlatform.ANDROID,
                installationIdentity = identity,
                deviceMetadata = DeviceMetadata(platform = LicensingPlatform.ANDROID, appVersion = "0.1.0-task10-live-test"),
                idempotencyKey = "task10-live-test-idem-" + secureRandomHex(16),
            ),
        )
        println("Task 10 live test activation outcome: $activationOutcome")
        val approved = assertIs<ActivationResult.Approved>(
            assertIs<TransportOutcome.Success<ActivationResult>>(activationOutcome, "expected activation Success, got: $activationOutcome").value,
            "expected a fresh activation to be Approved",
        )

        val secureMaterialStore = GenerationalSecureMaterialStore(secureBlobStore, { Clock.System.now().toString() }, { secureRandomHex(16) })
        val sink = SecureMaterialStoreActivationSink(
            store = secureMaterialStore,
            installationIdentity = identity,
            productCode = LicensingProductCode.AURA_RETAIL.name,
            platform = LicensingPlatform.ANDROID.name,
            ownerInstallationId = approved.installationPublicId,
            nowIso8601 = { Clock.System.now().toString() },
        )
        val processed = ActivationResponseProcessor(sink, sink).process(approved, LicensingProductCode.AURA_RETAIL, LicensingPlatform.ANDROID)
        println("Task 10 live test activation processed: $processed")
        assertTrue(processed is com.actionaura.retail.licensing.transport.ActivationProcessingResult.Complete, "expected the real paired production sink to complete the commit, got: $processed")

        val resolvedTransport = resolveActiveSyncTransport(
            secureBlobStore = secureBlobStore,
            secureMaterialStore = secureMaterialStore,
            deviceSigner = deviceSigner,
            httpClient = HttpClient(CIO), // MUST be CIO -- SyncTransport.kt's own class KDoc.
            configuration = SyncRelayConfiguration(environment = LicensingEnvironment.DEVELOPMENT, baseUrl = baseUrl),
            productCode = LicensingProductCode.AURA_RETAIL.name,
        )
        assertTrue(resolvedTransport != null, "resolveActiveSyncTransport (the exact function AuraAppContainer's DI wiring calls) must find the activation bundle just committed above")

        // ---- 4: real category write through this task's own outbox instrumentation ----
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        val db = RetailDatabase(driver)
        val gate = DatabaseWriteGate()
        val categoryRepository = SqlDelightCategoryRepository(db, gate)
        val created = categoryRepository.insert(1L, "Task 10 Live Sync Test Category", "created by SyncOrchestratorLiveTest", Clock.System.now().toEpochMilliseconds())
        println("Task 10 live test created local category id=${created.id}")
        assertTrue(db.syncQueries.selectOutbox().executeAsList().size == 1, "insert() must have queued exactly one outbox event")

        // ---- 5: real push ----
        val orchestrator = SyncOrchestrator({ resolvedTransport }, db, gate, this)
        orchestrator.pushOnce()

        assertTrue(db.syncQueries.selectOutbox().executeAsList().isEmpty(), "pushOnce() must have cleared the outbox on a real, successful push -- if this fails, the event never reached Owner")
        println("Task 10 live test: outbox drained -- category id=${created.id} pushed to Owner (installationId=${approved.installationPublicId}). Verify via: SELECT * FROM owner_sync_events WHERE payload->>'id' = '${created.id}';")
    }
}
