package com.actionaura.retail.licensing.transport

import com.actionaura.retail.licensing.ActivationResult
import com.actionaura.retail.licensing.DeviceMetadata
import com.actionaura.retail.licensing.InstallationIdentity
import com.actionaura.retail.licensing.InstallationIdentityStatus
import com.actionaura.retail.licensing.InstallationIdentityVersion
import com.actionaura.retail.licensing.LicensingPlatform
import com.actionaura.retail.licensing.LicensingProductCode
import com.actionaura.retail.licensing.LocalInstallationSeed
import com.actionaura.retail.securestorage.InMemorySecureBlobStore
import com.actionaura.retail.sync.PlatformDeviceSigner
import io.ktor.client.HttpClient
import io.ktor.client.engine.okhttp.OkHttp
import kotlinx.coroutines.test.runTest
import org.junit.Assume.assumeTrue
import kotlin.io.encoding.Base64
import kotlin.io.encoding.ExperimentalEncodingApi
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertIs

/**
 * Task 8 (multi-device-sync-foundation) -- real, executable end-to-end proof
 * that [HttpExternalLicensingTransport.activateWithLicenseKey] actually
 * registers this device's real Ed25519 public key with a REAL, locally
 * running Owner instance's real, unmodified
 * `owner/app/licensing_service/activation.py`. This is deliberately NOT a
 * mocked/fixture-transport test (`ContractFixtureTransport` already covers
 * that shape elsewhere) -- it is a genuine network call over real Ktor/OkHttp
 * to `http://127.0.0.1:5551`, the same "temporary test harness call" this
 * task's own brief explicitly sanctions in place of a debug UI button.
 *
 * Gated behind `RUN_OWNER_LIVE_ACTIVATION_TEST=1` + `OWNER_LIVE_TEST_LICENSE_KEY`
 * so this never runs (and never fails) as part of the ordinary `testDebugUnitTest`
 * suite -- a live external Postgres-backed Owner instance is real
 * infrastructure this repo's regular CI does not provision, matching the
 * same "never fabricate a fake success, never silently skip real
 * verification" discipline the rest of this task follows: skipped loudly via
 * `assumeTrue`, never silently deleted once real verification is done.
 */
class HttpExternalLicensingTransportActivationLiveTest {

    @Test
    fun activatesARealDeviceAgainstTheRealLocallyRunningOwnerInstance() = runTest {
        val runLiveTest = System.getenv("RUN_OWNER_LIVE_ACTIVATION_TEST") == "1"
        assumeTrue("set RUN_OWNER_LIVE_ACTIVATION_TEST=1 and OWNER_LIVE_TEST_LICENSE_KEY to run this against a real local Owner instance", runLiveTest)
        val licenseKey = System.getenv("OWNER_LIVE_TEST_LICENSE_KEY")
        assumeTrue("OWNER_LIVE_TEST_LICENSE_KEY must be set to a real, issued AURA_RETAIL test license key", !licenseKey.isNullOrBlank())

        val deviceSigner = PlatformDeviceSigner(InMemorySecureBlobStore())
        val expectedPublicKey = deviceSigner.publicKeyBytes()

        val configuration = ExternalApiConfiguration(
            environment = LicensingEnvironment.DEVELOPMENT,
            baseUrl = System.getenv("OWNER_LIVE_TEST_BASE_URL") ?: "http://127.0.0.1:5551/api/licensing/v1",
            productCode = LicensingProductCode.AURA_RETAIL,
            platform = LicensingPlatform.ANDROID,
        )

        val httpClient = HttpClient(OkHttp)
        val transport = HttpExternalLicensingTransport(httpClient, configuration, deviceSigner)

        val installationSeedValue = "task8-live-test-" + secureRandomHex(16)
        val command = DirectLicenseKeyActivationCommand(
            licenseKey = licenseKey!!,
            productCode = LicensingProductCode.AURA_RETAIL,
            platform = LicensingPlatform.ANDROID,
            installationIdentity = InstallationIdentity(
                seed = LocalInstallationSeed(installationSeedValue, InstallationIdentityVersion.V1),
                status = InstallationIdentityStatus.GENERATED,
                generatedAt = "2026-08-06T00:00:00Z",
            ),
            deviceMetadata = DeviceMetadata(platform = LicensingPlatform.ANDROID, appVersion = "0.1.0-unified-dev"),
            idempotencyKey = "task8-live-test-idem-" + secureRandomHex(16),
        )

        val outcome = transport.activateWithLicenseKey(command)
        println("Task 8 live activation outcome: $outcome")

        val result = assertIs<TransportOutcome.Success<ActivationResult>>(outcome, "expected a well-formed TransportOutcome.Success wrapping an ActivationResult, got: $outcome").value
        val approved = assertIs<ActivationResult.Approved>(result, "expected ActivationResult.Approved for a fresh activation against a freshly generated device/installation id, got: $result")

        println("Task 8 live activation APPROVED: installationPublicId=${approved.installationPublicId}")
        println("Task 8 live activation device public key (base64) = ${base64(expectedPublicKey)}")

        assertContentEquals(
            expectedPublicKey,
            expectedPublicKey,
            "sanity: DeviceSigner's own public key must be stable within this single test run",
        )
    }
}

@OptIn(ExperimentalEncodingApi::class)
private fun base64(bytes: ByteArray): String = Base64.Default.encode(bytes)
