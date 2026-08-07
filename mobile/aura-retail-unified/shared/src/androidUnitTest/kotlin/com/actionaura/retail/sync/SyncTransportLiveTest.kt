package com.actionaura.retail.sync

import com.actionaura.retail.licensing.ActivationResult
import com.actionaura.retail.licensing.DeviceMetadata
import com.actionaura.retail.licensing.InstallationIdentity
import com.actionaura.retail.licensing.InstallationIdentityStatus
import com.actionaura.retail.licensing.InstallationIdentityVersion
import com.actionaura.retail.licensing.LicensingPlatform
import com.actionaura.retail.licensing.LicensingProductCode
import com.actionaura.retail.licensing.LocalInstallationSeed
import com.actionaura.retail.licensing.transport.DirectLicenseKeyActivationCommand
import com.actionaura.retail.licensing.transport.ExternalApiConfiguration
import com.actionaura.retail.licensing.transport.HttpExternalLicensingTransport
import com.actionaura.retail.licensing.transport.LicensingEnvironment
import com.actionaura.retail.licensing.transport.TransportOutcome
import com.actionaura.retail.licensing.transport.secureRandomHex
import com.actionaura.retail.securestorage.InMemorySecureBlobStore
import io.ktor.client.HttpClient
import io.ktor.client.engine.cio.CIO
import io.ktor.client.engine.okhttp.OkHttp
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assume.assumeTrue
import kotlin.test.Test
import kotlin.test.assertIs
import kotlin.test.assertTrue

/**
 * Task 9 (multi-device-sync-foundation) -- real, executable end-to-end
 * proof that [SyncTransport] actually pushes to and pulls from a REAL,
 * locally running Owner instance's real, unmodified
 * `owner/app/sync/routes.py`. Deliberately NOT a mocked/MockEngine test
 * (`SyncTransportMockTest` already covers that shape) -- a genuine network
 * round-trip, same discipline `HttpExternalLicensingTransportActivationLiveTest`
 * (Task 8) established.
 *
 * Two real, independently activated installations against the SAME
 * license ("device A" / "device B") are required, not one: Owner's own
 * `pull()` route excludes events authored by the pulling device itself
 * (`SyncEvent.device_id != installation.id`, routes.py) -- a single
 * device pushing and then pulling its own event would never see it,
 * proving nothing about the real cross-device sync path this whole
 * plan exists for.
 *
 * Uses [CIO] for [SyncTransport] specifically, NOT [OkHttp] -- a real
 * finding from this test's own first live run (see the KDoc on
 * `shared/build.gradle.kts`'s `ktor-client-cio` dependency line for the
 * full disassembly-verified explanation): Ktor's OkHttp engine
 * unconditionally drops the request body on any GET call
 * (`okhttp3.internal.http.HttpMethod.permitsRequestBody("GET") == false`),
 * so `pull()` would always send an empty body and Owner would always
 * reject it with `INVALID_REQUEST`. [HttpExternalLicensingTransport]'s own
 * activation calls stay on [OkHttp] here (POST-only, unaffected).
 *
 * Gated behind `RUN_OWNER_LIVE_SYNC_TEST=1` + `OWNER_LIVE_TEST_LICENSE_KEY`
 * (same license-key env var `HttpExternalLicensingTransportActivationLiveTest`
 * already uses) so this never runs as part of the ordinary test suite.
 */
class SyncTransportLiveTest {

    @Test
    fun pushesFromDeviceAAndPullsThemOnDeviceBAgainstARealLocallyRunningOwnerInstance() = runTest {
        val runLiveTest = System.getenv("RUN_OWNER_LIVE_SYNC_TEST") == "1"
        assumeTrue("set RUN_OWNER_LIVE_SYNC_TEST=1 and OWNER_LIVE_TEST_LICENSE_KEY to run this against a real local Owner instance", runLiveTest)
        val licenseKey = System.getenv("OWNER_LIVE_TEST_LICENSE_KEY")
        assumeTrue("OWNER_LIVE_TEST_LICENSE_KEY must be set to a real, issued AURA_RETAIL test license key", !licenseKey.isNullOrBlank())

        val baseUrl = System.getenv("OWNER_LIVE_TEST_BASE_URL") ?: "http://127.0.0.1:5551"
        val licensingConfiguration = ExternalApiConfiguration(
            environment = LicensingEnvironment.DEVELOPMENT,
            baseUrl = "$baseUrl/api/licensing/v1",
            productCode = LicensingProductCode.AURA_RETAIL,
            platform = LicensingPlatform.ANDROID,
        )
        val syncConfiguration = SyncRelayConfiguration(
            environment = LicensingEnvironment.DEVELOPMENT,
            baseUrl = baseUrl,
        )

        val deviceASigner = com.actionaura.retail.sync.PlatformDeviceSigner(InMemorySecureBlobStore())
        val deviceBSigner = com.actionaura.retail.sync.PlatformDeviceSigner(InMemorySecureBlobStore())

        val installationIdA = activateRealInstallation(licensingConfiguration, deviceASigner, licenseKey!!, "task9-live-deviceA-")
        val installationIdB = activateRealInstallation(licensingConfiguration, deviceBSigner, licenseKey, "task9-live-deviceB-")
        println("Task 9 live test: installationIdA=$installationIdA installationIdB=$installationIdB")

        val transportA = SyncTransport(HttpClient(CIO), syncConfiguration, deviceASigner, installationIdA)
        val transportB = SyncTransport(HttpClient(CIO), syncConfiguration, deviceBSigner, installationIdB)

        // ---- Push path: device A pushes one real create event ----
        // `id`/`entity_id` must be real UUIDs -- `owner/app/sync/routes.py::_build_event`
        // parses both via `uuid.UUID(str(raw[...]))` and rejects anything
        // else with INVALID_EVENT (confirmed live: an earlier run of this
        // test using plain prefixed-hex strings here failed with exactly
        // that reason code -- fixed by switching to real UUIDs).
        @OptIn(kotlin.uuid.ExperimentalUuidApi::class)
        val entityId = kotlin.uuid.Uuid.random().toString()
        @OptIn(kotlin.uuid.ExperimentalUuidApi::class)
        val eventId = kotlin.uuid.Uuid.random().toString()
        val pushOutcome = transportA.push(
            listOf(
                SyncEventEnvelope(
                    id = eventId,
                    entityType = "category",
                    entityId = entityId,
                    eventType = "create",
                    payload = buildJsonObject {
                        put("id", entityId)
                        put("name", "Task 9 Live Sync Test Category")
                        put("description", "created by SyncTransportLiveTest")
                    },
                    createdAt = kotlinx.datetime.Clock.System.now().toString(),
                ),
            ),
        )
        println("Task 9 live push outcome: $pushOutcome")
        val pushSuccess = assertIs<SyncTransportOutcome.Success<PushResult>>(pushOutcome, "expected push to be accepted by the real relay, got: $pushOutcome")
        assertTrue(pushSuccess.value.stored >= 1, "expected at least 1 event stored, got ${pushSuccess.value.stored}")
        assertTrue(pushSuccess.value.received == 1, "expected exactly 1 event received, got ${pushSuccess.value.received}")

        // ---- Pull path: device B pulls since=0 and must see device A's event ----
        val pullOutcome = transportB.pull(since = 0)
        println("Task 9 live pull outcome: $pullOutcome")
        val pullSuccess = assertIs<SyncTransportOutcome.Success<PullResult>>(pullOutcome, "expected pull to be accepted by the real relay, got: $pullOutcome")
        val pulledEvent = pullSuccess.value.events.find { it.entityId == entityId }
        assertTrue(pulledEvent != null, "expected device B's pull to include device A's pushed event (entityId=$entityId), got events=${pullSuccess.value.events.map { it.entityId }}")
        println("Task 9 live pull found matching event: id=${pulledEvent.id} seq=${pulledEvent.seq} payload=${pulledEvent.payload}")

        // ---- Replay proof: a second pull with since=cursor from device B returns no new events for the same entity ----
        val secondPullOutcome = transportB.pull(since = pullSuccess.value.cursor)
        val secondPullSuccess = assertIs<SyncTransportOutcome.Success<PullResult>>(secondPullOutcome)
        assertTrue(
            secondPullSuccess.value.events.none { it.entityId == entityId },
            "expected a pull since the already-observed cursor to not repeat the same event",
        )
    }

    private suspend fun activateRealInstallation(
        configuration: ExternalApiConfiguration,
        signer: DeviceSigner,
        licenseKey: String,
        seedPrefix: String,
    ): String {
        val transport = HttpExternalLicensingTransport(HttpClient(OkHttp), configuration, signer)
        val command = DirectLicenseKeyActivationCommand(
            licenseKey = licenseKey,
            productCode = LicensingProductCode.AURA_RETAIL,
            platform = LicensingPlatform.ANDROID,
            installationIdentity = InstallationIdentity(
                seed = LocalInstallationSeed(seedPrefix + secureRandomHex(16), InstallationIdentityVersion.V1),
                status = InstallationIdentityStatus.GENERATED,
                generatedAt = "2026-08-07T00:00:00Z",
            ),
            deviceMetadata = DeviceMetadata(platform = LicensingPlatform.ANDROID, appVersion = "0.1.0-unified-dev"),
            idempotencyKey = seedPrefix + "idem-" + secureRandomHex(16),
        )
        val outcome = transport.activateWithLicenseKey(command)
        val result = assertIs<TransportOutcome.Success<ActivationResult>>(outcome, "expected a well-formed activation Success, got: $outcome").value
        val approved = assertIs<ActivationResult.Approved>(result, "expected a fresh activation to be Approved, got: $result")
        return approved.installationPublicId
    }
}
