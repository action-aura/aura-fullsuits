package com.actionaura.retail.licensing

import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * M7.22 -- real, executed test matrix for the M7.17 shared contract models
 * and M7.18 fixtures: domain-contract shape, serialization round-trip,
 * data-minimization field-set proof, and error-contract parsing.
 */
class LicensingContractTest {

    private val json = Json { encodeDefaults = true }

    // --- Data minimization (M7.16): the real, exact allowlist, no more, no less ---

    private val realActivationRequestAllowlist = setOf(
        "contract_version", "request_id", "correlation_id", "timestamp", "nonce",
        "product_code", "platform", "app_version", "release_channel", "installation_id",
        "device_public_key", "device_public_key_algorithm", "license_key", "idempotency_key", "signature",
    )

    @Test
    fun activationRequestSerializesToExactlyTheRealOwnerAllowlist() {
        val request = LicensingFixtures.activationRequest()
        val encoded = json.encodeToString(request)
        val keys = json.parseToJsonElement(encoded).jsonObject.keys
        assertEquals(realActivationRequestAllowlist, keys, "real regression: ActivationRequest must serialize to exactly owner/contracts/activation-request-v1.schema.json's own additionalProperties:false field set -- no more, no less")
    }

    @Test
    fun activationRequestRoundTripsThroughJson() {
        val request = LicensingFixtures.activationRequest()
        val decoded = json.decodeFromString<ActivationRequest>(json.encodeToString(request))
        assertEquals(request, decoded)
    }

    @Test
    fun activationRequestToStringNeverExposesLicenseKeyOrSignature() {
        val request = LicensingFixtures.activationRequest()
        val rendered = request.toString()
        assertFalse(rendered.contains(request.licenseKey), "real regression: toString() must never leak the plaintext license key -- owner-data-minimization-contract.md's own discipline mirrored client-side")
        assertFalse(rendered.contains(request.signature), "toString() must never leak the request signature")
    }

    @Test
    fun deactivationRequestToStringNeverExposesSignature() {
        val request = DeactivationRequest(
            requestId = "r1", correlationId = "c1", timestamp = "2026-08-05T00:00:00Z",
            nonce = "n1", installationId = "i1", idempotencyKey = "idem1", signature = "SECRET_SIGNATURE_VALUE",
        )
        assertFalse(request.toString().contains("SECRET_SIGNATURE_VALUE"))
    }

    @Test
    fun signedAssertionEnvelopeToStringNeverExposesSignature() {
        val envelope = LicensingFixtures.validAssertion()
        assertFalse(envelope.toString().contains(envelope.signature))
    }

    // --- Real state-machine shape (M7.3, M7.6) ---

    @Test
    fun licenseStatusHasExactlyTheSevenRealAuditedValues() {
        val real = setOf("DRAFT", "ISSUED", "ACTIVE", "SUSPENDED", "EXPIRED", "REVOKED", "REPLACED")
        assertEquals(real, LicenseStatus.entries.map { it.name }.toSet())
    }

    @Test
    fun installationStatusHasExactlyTheSixRealAuditedValues() {
        val real = setOf("REGISTERED", "PENDING_ACTIVATION", "ACTIVE", "SUSPENDED", "DEACTIVATED", "REPLACED")
        assertEquals(real, InstallationStatus.entries.map { it.name }.toSet())
    }

    @Test
    fun platformHasExactlyTheThreeClientContractValuesWindowsAndroidIos() {
        val real = setOf("WINDOWS", "ANDROID", "IOS")
        assertEquals(real, LicensingPlatform.entries.map { it.name }.toSet(), "platform-contract-reconciliation-m8.md: IOS is a real client-side forward-compatible contract case, added in M8 -- but see ios-platform-readiness-state.md, Owner does not accept it yet")
    }

    @Test
    fun platformDecodeParsesEveryKnownValue() {
        for (raw in listOf("WINDOWS", "ANDROID", "IOS")) {
            val result = PlatformDecodeResult.parse(raw)
            assertTrue(result is PlatformDecodeResult.Known, "expected $raw to parse as a known platform")
            assertEquals(raw, result.platform.name)
        }
    }

    @Test
    fun platformDecodeRejectsAllAnyMobileAndUnknownValuesWithoutFallback() {
        for (raw in listOf("ALL", "ANY", "MOBILE", "", "windows", "Android", "LINUX")) {
            val result = PlatformDecodeResult.parse(raw)
            assertTrue(result is PlatformDecodeResult.UnsupportedPlatform, "real regression: '$raw' must never silently decode into a known platform")
            assertEquals(raw, result.raw)
        }
    }

    // --- Error contract (M7.15): every real server code parses; unknown codes never silently coerced ---

    @Test
    fun everyRealServerReasonCodeParsesToItself() {
        for (raw in LicensingFixtures.allServerReasonCodeStrings()) {
            val error = LicensingError.fromServerCode(raw)
            assertTrue(error is LicensingError.FromServer, "expected $raw to parse as a known ServerReasonCode")
            assertEquals(raw, error.code.name)
        }
    }

    @Test
    fun unrecognizedServerCodeSurfacesAsUnknownNeverCoercedToAnExistingCase() {
        val error = LicensingError.fromServerCode("SOME_FUTURE_CODE_NOT_YET_DEFINED")
        assertTrue(error is LicensingError.FromServerUnknown, "real regression: an unrecognized server reason code must never be silently mapped to an existing case")
        assertEquals("SOME_FUTURE_CODE_NOT_YET_DEFINED", error.rawCode)
    }

    @Test
    fun rateLimitedAndServiceUnavailableAreTheOnlyServerCodesSafeToRetry() {
        val retryable = ServerReasonCode.entries.filter { it.defaultRetryGuidance() == RetryGuidance.SAFE_TO_RETRY_WITH_BACKOFF }
        assertEquals(setOf(ServerReasonCode.RATE_LIMITED, ServerReasonCode.SERVICE_TEMPORARILY_UNAVAILABLE), retryable.toSet())
    }

    @Test
    fun minVersionFailureFixtureMapsToRealVersionUnsupportedNotAnInventedCode() {
        val error = LicensingFixtures.minVersionFailureError()
        assertTrue(error is LicensingError.FromServer)
        assertEquals(ServerReasonCode.VERSION_UNSUPPORTED, error.code)
    }

    // --- Multi-device / lease fixture scenarios (M7.18) ---

    @Test
    fun validAssertionHasActiveLicenseStatus() {
        assertEquals(LicenseStatus.ACTIVE, LicensingFixtures.validAssertion().payload.licenseStatus)
    }

    @Test
    fun expiredSuspendedRevokedFixturesCarryTheirRealDistinctLicenseStatus() {
        assertEquals(LicenseStatus.EXPIRED, LicensingFixtures.expiredAssertion().payload.licenseStatus)
        assertEquals(LicenseStatus.SUSPENDED, LicensingFixtures.suspendedAssertion().payload.licenseStatus)
        assertEquals(LicenseStatus.REVOKED, LicensingFixtures.revokedAssertion().payload.licenseStatus)
    }

    @Test
    fun graceFixtureIsPastExpiryButCarriesACommercialGraceEnd() {
        val assertion = LicensingFixtures.graceAssertion()
        assertTrue(assertion.payload.expiresAt < assertion.payload.issuedAt, "grace fixture must model expiresAt already in the past relative to issuedAt")
        assertEquals("2026-08-10T00:00:00Z", assertion.payload.commercialGraceEnd)
    }

    @Test
    fun wrongProductFixtureDiffersFromRequestedProduct() {
        val request = LicensingFixtures.activationRequest(productCode = LicensingProductCode.AURA_RETAIL)
        val assertion = LicensingFixtures.wrongProductAssertion()
        assertFalse(request.productCode == assertion.payload.productCode)
    }

    @Test
    fun wrongPlatformFixtureDiffersFromRequestedPlatform() {
        val request = LicensingFixtures.activationRequest(platform = LicensingPlatform.ANDROID)
        val assertion = LicensingFixtures.wrongPlatformAssertion()
        assertFalse(request.platform == assertion.payload.platform)
    }

    @Test
    fun unknownKeyFixtureUsesADifferentSigningKeyIdThanTheValidFixture() {
        assertFalse(LicensingFixtures.validAssertion().signingKeyId == LicensingFixtures.unknownKeyAssertion().signingKeyId)
    }

    @Test
    fun malformedSignatureFixtureIsNotValidBase64Shape() {
        assertTrue(LicensingFixtures.malformedSignatureAssertion().signature.contains("!"))
    }

    @Test
    fun futureContractVersionFixtureUsesAVersionThisClientDoesNotRecognizeAsV1() {
        assertFalse(LicensingFixtures.futureContractVersionAssertion().payload.contractVersion == "v1")
    }

    @Test
    fun assertionPayloadRoundTripsThroughJson() {
        val payload = LicensingFixtures.validAssertion().payload
        val decoded = json.decodeFromString<AssertionPayload>(json.encodeToString(payload))
        assertEquals(payload, decoded)
    }

    @Test
    fun signedAssertionEnvelopeRoundTripsThroughJson() {
        val envelope = LicensingFixtures.validAssertion()
        val decoded = json.decodeFromString<SignedAssertionEnvelope>(json.encodeToString(envelope))
        assertEquals(envelope, decoded)
    }

    // --- Fixtures never contain real secret-shaped material ---

    @Test
    fun fixturesAreObviouslyFakeNeverResemblingRealKeyMaterial() {
        val request = LicensingFixtures.activationRequest()
        assertTrue(request.licenseKey.contains("FIXTURE"), "fixture license key must be self-evidently fake, never resembling a real issued key")
        assertTrue(request.devicePublicKey.contains("FAKE"))
        assertTrue(request.signature.contains("FAKE"))
    }
}
