package com.actionaura.retail.licensing.transport

import com.actionaura.retail.licensing.ActivationResult
import com.actionaura.retail.licensing.DeviceMetadata
import com.actionaura.retail.licensing.InstallationIdentity
import com.actionaura.retail.licensing.InstallationIdentityStatus
import com.actionaura.retail.licensing.InstallationIdentityVersion
import com.actionaura.retail.licensing.LicensingPlatform
import com.actionaura.retail.licensing.LicensingProductCode
import com.actionaura.retail.licensing.LocalInstallationSeed
import com.actionaura.retail.licensing.ServerReasonCode
import com.actionaura.retail.sync.DeviceSigner
import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.client.engine.mock.respondError
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import kotlinx.coroutines.delay
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertTrue

/**
 * Task 8 fix (multi-device-sync-foundation, code-review finding #2) -- real,
 * non-live regression coverage for [HttpExternalLicensingTransport]'s
 * response-branch/error-classification logic
 * (SUCCESS/PENDING/FAILURE/rate-limited/malformed/unsupported-contract-version,
 * plus timeout/TLS/network exception classification). The live test
 * (`HttpExternalLicensingTransportActivationLiveTest`) proves the real wire
 * format against a real Owner instance but is skipped by default in every
 * ordinary run (no live Owner instance in CI) -- these tests run every time,
 * using Ktor's own real [MockEngine] (no fake HTTP stack of this codebase's
 * own invention) to control exactly what the "server" returns.
 */
class HttpExternalLicensingTransportMockTest {

    private fun transportWith(engine: MockEngine, signer: DeviceSigner = FakeDeviceSigner()): HttpExternalLicensingTransport {
        val configuration = ExternalApiConfiguration(
            environment = LicensingEnvironment.DEVELOPMENT,
            baseUrl = "http://127.0.0.1:5551/api/licensing/v1",
            productCode = LicensingProductCode.AURA_RETAIL,
            platform = LicensingPlatform.ANDROID,
        )
        return HttpExternalLicensingTransport(HttpClient(engine), configuration, signer)
    }

    private fun fakeCommand(idempotencyKey: String = "mock-idem-key") = DirectLicenseKeyActivationCommand(
        licenseKey = "FAKE-TEST-LICENSE-KEY-NOT-REAL",
        productCode = LicensingProductCode.AURA_RETAIL,
        platform = LicensingPlatform.ANDROID,
        installationIdentity = InstallationIdentity(
            seed = LocalInstallationSeed("mock-installation-seed", InstallationIdentityVersion.V1),
            status = InstallationIdentityStatus.GENERATED,
            generatedAt = "2026-08-07T00:00:00Z",
        ),
        deviceMetadata = DeviceMetadata(platform = LicensingPlatform.ANDROID, appVersion = "1.0.0-mock"),
        idempotencyKey = idempotencyKey,
    )

    // ---- SUCCESS ----

    @Test
    fun successApprovedResponseDecodesToActivationResultApproved() = runTest {
        val engine = MockEngine { respondJson(HttpStatusCode.OK, successBody(reasonCode = "ACTIVATION_APPROVED", installationId = "installation-approved-1")) }
        val outcome = transportWith(engine).activateWithLicenseKey(fakeCommand())

        val success = assertIs<TransportOutcome.Success<ActivationResult>>(outcome)
        val approved = assertIs<ActivationResult.Approved>(success.value)
        assertEquals("installation-approved-1", approved.installationPublicId)
        assertEquals("assertion-mock-1", approved.assertion.payload.assertionId)
    }

    /** Real forward-compatible dead code today (`activation.py` only ever emits `ACTIVATION_APPROVED` from this endpoint) -- still tested since the branch exists and must decode correctly if the server ever starts emitting it. */
    @Test
    fun successAlreadyActiveReasonCodeDecodesToActivationResultAlreadyActive() = runTest {
        val engine = MockEngine { respondJson(HttpStatusCode.OK, successBody(reasonCode = "ACTIVATION_ALREADY_ACTIVE", installationId = "installation-already-active-1")) }
        val outcome = transportWith(engine).activateWithLicenseKey(fakeCommand())

        val success = assertIs<TransportOutcome.Success<ActivationResult>>(outcome)
        val alreadyActive = assertIs<ActivationResult.AlreadyActive>(success.value)
        assertEquals("installation-already-active-1", alreadyActive.installationPublicId)
    }

    @Test
    fun successResponseMissingSignedAssertionIsMalformed() = runTest {
        val body = """{"contract_version":"v1","correlation_id":"c1","result":"SUCCESS","reason_code":"ACTIVATION_APPROVED","decision":"APPROVED","installation_id":"i1"}"""
        val engine = MockEngine { respondJson(HttpStatusCode.OK, body) }
        val outcome = transportWith(engine).activateWithLicenseKey(fakeCommand())

        val malformed = assertIs<TransportOutcome.MalformedResponse>(outcome)
        assertTrue(malformed.reason.contains("signed_assertion"), "expected reason to mention the missing field, got: ${malformed.reason}")
    }

    @Test
    fun successResponseMissingInstallationIdIsMalformed() = runTest {
        val body = """{"contract_version":"v1","correlation_id":"c1","result":"SUCCESS","reason_code":"ACTIVATION_APPROVED","decision":"APPROVED","signed_assertion":${signedAssertionJson("i1")}}"""
        val engine = MockEngine { respondJson(HttpStatusCode.OK, body) }
        val outcome = transportWith(engine).activateWithLicenseKey(fakeCommand())

        val malformed = assertIs<TransportOutcome.MalformedResponse>(outcome)
        assertTrue(malformed.reason.contains("installation_id"), "expected reason to mention the missing field, got: ${malformed.reason}")
    }

    // ---- PENDING ----

    @Test
    fun pendingResponseDecodesToActivationResultPending() = runTest {
        val body = """{"contract_version":"v1","correlation_id":"corr-pending-1","result":"PENDING","reason_code":"ACTIVATION_PENDING_REVIEW","decision":"PENDING_REVIEW","installation_id":"installation-pending-1","retry_guidance":"safe_to_retry_with_backoff"}"""
        val engine = MockEngine { respondJson(HttpStatusCode.OK, body) }
        val outcome = transportWith(engine).activateWithLicenseKey(fakeCommand())

        val success = assertIs<TransportOutcome.Success<ActivationResult>>(outcome)
        val pending = assertIs<ActivationResult.Pending>(success.value)
        assertEquals("installation-pending-1", pending.installationPublicId)
        assertEquals("corr-pending-1", pending.correlationId)
    }

    @Test
    fun pendingResponseMissingInstallationIdIsMalformed() = runTest {
        val body = """{"contract_version":"v1","correlation_id":"c1","result":"PENDING","reason_code":"ACTIVATION_PENDING_REVIEW","decision":"PENDING_REVIEW"}"""
        val engine = MockEngine { respondJson(HttpStatusCode.OK, body) }
        val outcome = transportWith(engine).activateWithLicenseKey(fakeCommand())

        val malformed = assertIs<TransportOutcome.MalformedResponse>(outcome)
        assertTrue(malformed.reason.contains("installation_id"), "expected reason to mention the missing field, got: ${malformed.reason}")
    }

    // ---- FAILURE (real `_error_response` shape) ----

    @Test
    fun failureResponseDecodesToActivationResultRejectedWithKnownReasonCode() = runTest {
        // DEVICE_LIMIT_REACHED -- a real reason code this task's own live
        // verification actually observed from the real Owner instance
        // (task-8-report.md), not a guessed/invented one.
        val body = """{"contract_version":"v1","response_id":"r1","correlation_id":"corr-fail-1","server_timestamp":"2026-08-07T00:00:00Z","result":"FAILURE","reason_code":"DEVICE_LIMIT_REACHED","decision":"REJECTED","retry_guidance":"do_not_retry_without_correction"}"""
        val engine = MockEngine { respondJson(HttpStatusCode.BadRequest, body) }
        val outcome = transportWith(engine).activateWithLicenseKey(fakeCommand())

        val success = assertIs<TransportOutcome.Success<ActivationResult>>(outcome)
        val rejected = assertIs<ActivationResult.Rejected>(success.value)
        assertEquals("corr-fail-1", rejected.correlationId)
        val fromServer = assertIs<com.actionaura.retail.licensing.LicensingError.FromServer>(rejected.error)
        assertEquals(ServerReasonCode.DEVICE_LIMIT_REACHED, fromServer.code)
    }

    @Test
    fun failureResponseWithUnrecognizedReasonCodeStillDecodesAsRejectedUnknown() = runTest {
        val body = """{"contract_version":"v1","correlation_id":"corr-fail-2","result":"FAILURE","reason_code":"SOME_FUTURE_REASON_CODE_NOT_YET_KNOWN","decision":"REJECTED"}"""
        val engine = MockEngine { respondJson(HttpStatusCode.BadRequest, body) }
        val outcome = transportWith(engine).activateWithLicenseKey(fakeCommand())

        val success = assertIs<TransportOutcome.Success<ActivationResult>>(outcome)
        val rejected = assertIs<ActivationResult.Rejected>(success.value)
        val unknown = assertIs<com.actionaura.retail.licensing.LicensingError.FromServerUnknown>(rejected.error)
        assertEquals("SOME_FUTURE_REASON_CODE_NOT_YET_KNOWN", unknown.rawCode)
    }

    // ---- Rate limiting ----

    @Test
    fun http429WithRetryAfterHeaderMapsToRateLimitedWithParsedSeconds() = runTest {
        val body = """{"contract_version":"v1","result":"FAILURE","reason_code":"RATE_LIMITED","decision":"REJECTED"}"""
        val engine = MockEngine { respond(body, HttpStatusCode.TooManyRequests, headersOf(HttpHeaders.RetryAfter, "42")) }
        val outcome = transportWith(engine).activateWithLicenseKey(fakeCommand())

        val rateLimited = assertIs<TransportOutcome.RateLimited>(outcome)
        assertEquals(42L, rateLimited.retryAfterSeconds)
    }

    @Test
    fun http429WithoutRetryAfterHeaderStillMapsToRateLimited() = runTest {
        val body = """{"contract_version":"v1","result":"FAILURE","reason_code":"RATE_LIMITED","decision":"REJECTED"}"""
        val engine = MockEngine { respond(body, HttpStatusCode.TooManyRequests) }
        val outcome = transportWith(engine).activateWithLicenseKey(fakeCommand())

        val rateLimited = assertIs<TransportOutcome.RateLimited>(outcome)
        assertEquals(null, rateLimited.retryAfterSeconds)
    }

    // ---- Malformed / unrecognized shapes ----

    @Test
    fun nonJsonResponseBodyIsMalformedResponse() = runTest {
        val engine = MockEngine { respond("this is not json", HttpStatusCode.OK) }
        val outcome = transportWith(engine).activateWithLicenseKey(fakeCommand())
        assertIs<TransportOutcome.MalformedResponse>(outcome)
    }

    @Test
    fun http200WithUnrecognizedResultStringIsMalformedResponse() = runTest {
        val body = """{"contract_version":"v1","result":"SOMETHING_UNEXPECTED"}"""
        val engine = MockEngine { respondJson(HttpStatusCode.OK, body) }
        val outcome = transportWith(engine).activateWithLicenseKey(fakeCommand())
        assertIs<TransportOutcome.MalformedResponse>(outcome)
    }

    @Test
    fun failureResultWithBlankReasonCodeIsMalformedResponse() = runTest {
        val body = """{"contract_version":"v1","result":"FAILURE","reason_code":""}"""
        val engine = MockEngine { respondJson(HttpStatusCode.BadRequest, body) }
        val outcome = transportWith(engine).activateWithLicenseKey(fakeCommand())
        assertIs<TransportOutcome.MalformedResponse>(outcome)
    }

    // ---- Contract version mismatch ----

    @Test
    fun mismatchedContractVersionMapsToUnsupportedContractVersion() = runTest {
        val body = """{"contract_version":"v2","result":"SUCCESS","reason_code":"ACTIVATION_APPROVED","decision":"APPROVED","installation_id":"i1","signed_assertion":${signedAssertionJson("i1")}}"""
        val engine = MockEngine { respondJson(HttpStatusCode.OK, body) }
        val outcome = transportWith(engine).activateWithLicenseKey(fakeCommand())

        val unsupported = assertIs<TransportOutcome.UnsupportedContractVersion>(outcome)
        assertEquals("v2", unsupported.serverVersion)
    }

    // ---- Exception classification ----

    @Test
    fun requestTimeoutIsClassifiedAsTimeout() = runTest {
        val engine = MockEngine {
            delay(200)
            respondJson(HttpStatusCode.OK, successBody(reasonCode = "ACTIVATION_APPROVED", installationId = "i1"))
        }
        val configuration = ExternalApiConfiguration(
            environment = LicensingEnvironment.DEVELOPMENT,
            baseUrl = "http://127.0.0.1:5551/api/licensing/v1",
            productCode = LicensingProductCode.AURA_RETAIL,
            platform = LicensingPlatform.ANDROID,
            requestTimeoutMillis = 10,
            connectTimeoutMillis = 10,
            responseTimeoutMillis = 10,
        )
        val transport = HttpExternalLicensingTransport(HttpClient(engine), configuration, FakeDeviceSigner())
        val outcome = transport.activateWithLicenseKey(fakeCommand())

        assertEquals(TransportOutcome.Timeout, outcome)
    }

    @Test
    fun aThrownExceptionMentioningTlsIsClassifiedAsTlsFailure() = runTest {
        val engine = MockEngine { throw FakeTransportException("SSL handshake failed: unable to find valid certification path") }
        val outcome = transportWith(engine).activateWithLicenseKey(fakeCommand())
        assertEquals(TransportOutcome.TlsFailure, outcome)
    }

    @Test
    fun aGenericThrownExceptionIsClassifiedAsNetworkFailure() = runTest {
        val engine = MockEngine { throw FakeTransportException("connection refused") }
        val outcome = transportWith(engine).activateWithLicenseKey(fakeCommand())

        val networkFailure = assertIs<TransportOutcome.NetworkFailure>(outcome)
        assertTrue(networkFailure.reason.contains("connection refused"))
    }
}

private class FakeTransportException(message: String) : Exception(message)

/** Deterministic, obviously-fake signing capability -- MockEngine bypasses real server-side signature verification entirely, so no real Ed25519 material is needed here (unlike the live test, which uses a real `PlatformDeviceSigner`). */
private class FakeDeviceSigner : DeviceSigner {
    override suspend fun publicKeyBytes(): ByteArray = ByteArray(32) { it.toByte() }
    override suspend fun sign(message: ByteArray): ByteArray = ByteArray(64) { 0x42 }
}

private fun io.ktor.client.engine.mock.MockRequestHandleScope.respondJson(status: HttpStatusCode, body: String) =
    respond(body, status, headersOf(HttpHeaders.ContentType, "application/json"))

private fun successBody(reasonCode: String, installationId: String): String =
    """{"contract_version":"v1","response_id":"resp-1","correlation_id":"corr-1","server_timestamp":"2026-08-07T00:00:00Z","result":"SUCCESS","reason_code":"$reasonCode","decision":"APPROVED","installation_id":"$installationId","signed_assertion":${signedAssertionJson(installationId)},"signing_key_id":"mock-signing-key-1","assertion_version":1}"""

private fun signedAssertionJson(installationId: String): String = """{
    "payload": {
        "assertion_id": "assertion-mock-1",
        "issuer": "aura-owner",
        "product_code": "AURA_RETAIL",
        "license_public_id": "license-mock-1",
        "installation_public_id": "$installationId",
        "platform": "ANDROID",
        "app_version_policy": "WINDOWS,ANDROID",
        "issued_at": "2026-08-07T00:00:00Z",
        "not_before": "2026-08-07T00:00:00Z",
        "expires_at": "2026-08-08T00:00:00Z",
        "license_status": "ISSUED",
        "installation_status": "ACTIVE",
        "subscription_status": "ACTIVE",
        "allowed_device_count": 3,
        "device_key_fingerprint": "mock-fingerprint",
        "entitlements": {"max_devices": 3, "backup_enabled": false, "allowed_platforms": []},
        "offline_policy": {
            "check_in_interval_seconds": 86400,
            "retry_interval_seconds": 3600,
            "offline_grace_seconds": 1209600,
            "warning_start_seconds": 864000,
            "hard_expiry_behavior": "WARN_ONLY",
            "clock_rollback_tolerance_seconds": 300,
            "assertion_refresh_threshold_seconds": 86400,
            "emergency_extension_allowed": false
        },
        "contract_version": "v1"
    },
    "signing_key_id": "mock-signing-key-1",
    "algorithm": "ed25519",
    "assertion_version": 1,
    "signature": "bW9jay1zaWduYXR1cmUtbm90LXJlYWw="
}"""
