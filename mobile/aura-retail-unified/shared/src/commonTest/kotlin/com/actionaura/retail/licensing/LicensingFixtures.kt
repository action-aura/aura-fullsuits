package com.actionaura.retail.licensing

import kotlinx.serialization.json.JsonPrimitive

/**
 * M7.18 -- versioned, sanitized fixture suite. NO real license key, NO real
 * customer/staff PII, NO real device public key from a genuine device, NO
 * real Owner private-key material of any kind (none of these fixtures were
 * ever produced by, or verified against, a real Owner signing key -- every
 * `signature`/`devicePublicKey` string below is an inert, obviously-fake
 * placeholder, chosen specifically so it can never be mistaken for real
 * key material even if this file were read out of context).
 *
 * Scenario coverage required by `offline-license-lease-contract-audit.md`:
 * valid, grace, expired, suspended, revoked, wrong-product, wrong-platform,
 * unknown-key, malformed-signature, future-version, min-version-failure.
 * Each fixture is versioned (`FIXTURE_SET_VERSION`) so a future contract
 * change can be tracked against a known baseline.
 */
object LicensingFixtures {
    const val FIXTURE_SET_VERSION = "m7-fixtures-v1"

    private const val FAKE_DEVICE_PUBLIC_KEY = "FAKE_TEST_DEVICE_PUBLIC_KEY_NOT_REAL_BASE64=="
    private const val FAKE_SIGNATURE = "FAKE_TEST_SIGNATURE_NOT_REAL_BASE64=="
    private const val FAKE_SIGNING_KEY_ID = "fake-test-signing-key-000000000000"

    fun activationRequest(
        productCode: LicensingProductCode = LicensingProductCode.AURA_RETAIL,
        platform: LicensingPlatform = LicensingPlatform.ANDROID,
        contractVersion: String = "v1",
        signature: String = FAKE_SIGNATURE,
    ) = ActivationRequest(
        contractVersion = contractVersion,
        requestId = "fixture-request-0001",
        correlationId = "fixture-correlation-0001",
        timestamp = "2026-08-05T00:00:00Z",
        nonce = "fixture-nonce-0000000000000001",
        productCode = productCode,
        platform = platform,
        appVersion = "1.0.0-fixture",
        releaseChannel = "STABLE",
        installationId = "fixture-local-installation-id",
        devicePublicKey = FAKE_DEVICE_PUBLIC_KEY,
        devicePublicKeyAlgorithm = "ed25519",
        licenseKey = "FIXTURE-LICENSE-KEY-NOT-REAL-0000-0000",
        idempotencyKey = "fixture-idempotency-key-0001",
        signature = signature,
    )

    private fun offlinePolicy() = OfflinePolicy(
        checkInIntervalSeconds = 86_400,
        retryIntervalSeconds = 3_600,
        offlineGraceSeconds = 259_200,
        warningStartSeconds = 172_800,
        hardExpiryBehavior = "BLOCK",
        clockRollbackToleranceSeconds = 300,
        assertionRefreshThresholdSeconds = 43_200,
        emergencyExtensionAllowed = true,
        emergencyExtensionUntil = null,
    )

    private fun assertionPayload(
        licenseStatus: LicenseStatus,
        installationStatus: InstallationStatus = InstallationStatus.ACTIVE,
        subscriptionStatus: SubscriptionStatus = SubscriptionStatus.ACTIVE,
        productCode: LicensingProductCode = LicensingProductCode.AURA_RETAIL,
        platform: LicensingPlatform = LicensingPlatform.ANDROID,
        contractVersion: String = "v1",
        expiresAt: String = "2026-09-05T00:00:00Z",
        commercialGraceEnd: String? = null,
    ) = AssertionPayload(
        assertionId = "fixture-assertion-0001",
        issuer = "aura-owner",
        productCode = productCode,
        licensePublicId = "fixture-license-public-id",
        installationPublicId = "fixture-installation-public-id",
        platform = platform,
        appVersionPolicy = "WINDOWS,ANDROID",
        releaseChannel = "STABLE",
        issuedAt = "2026-08-05T00:00:00Z",
        notBefore = "2026-08-05T00:00:00Z",
        expiresAt = expiresAt,
        licenseStatus = licenseStatus,
        installationStatus = installationStatus,
        subscriptionStatus = subscriptionStatus,
        allowedDeviceCount = 3,
        deviceKeyFingerprint = "fixture-device-key-fingerprint",
        entitlements = mapOf("max_devices" to JsonPrimitive(3)),
        offlinePolicy = offlinePolicy(),
        contractVersion = contractVersion,
        commercialGraceEnd = commercialGraceEnd,
    )

    private fun envelope(payload: AssertionPayload, signature: String = FAKE_SIGNATURE, signingKeyId: String = FAKE_SIGNING_KEY_ID) =
        SignedAssertionEnvelope(payload = payload, signingKeyId = signingKeyId, algorithm = "ed25519", assertionVersion = 1, signature = signature)

    /** Valid, currently-active assertion. */
    fun validAssertion(): SignedAssertionEnvelope = envelope(assertionPayload(licenseStatus = LicenseStatus.ACTIVE))

    /** Past `expiresAt`, but within the real `offline_grace_seconds` window -- policy evaluation, not this model, decides grace behavior. */
    fun graceAssertion(): SignedAssertionEnvelope =
        envelope(assertionPayload(licenseStatus = LicenseStatus.ACTIVE, expiresAt = "2026-08-04T00:00:00Z", commercialGraceEnd = "2026-08-10T00:00:00Z"))

    /** Real `EXPIRED` license status -- see `license-state-machine-contract.md`. */
    fun expiredAssertion(): SignedAssertionEnvelope =
        envelope(assertionPayload(licenseStatus = LicenseStatus.EXPIRED, expiresAt = "2026-07-01T00:00:00Z"))

    /** Real `SUSPENDED` license status. */
    fun suspendedAssertion(): SignedAssertionEnvelope = envelope(assertionPayload(licenseStatus = LicenseStatus.SUSPENDED))

    /** Real `REVOKED` license status -- must never be trusted again per `device_identity.py:112-121`. */
    fun revokedAssertion(): SignedAssertionEnvelope = envelope(assertionPayload(licenseStatus = LicenseStatus.REVOKED))

    /** Product mismatch -- real `PRODUCT_MISMATCH` server reason code, `ASSERTION_PRODUCT_MISMATCH` local reason code on verify. */
    fun wrongProductAssertion(): SignedAssertionEnvelope =
        envelope(assertionPayload(licenseStatus = LicenseStatus.ACTIVE, productCode = LicensingProductCode.AURA_CLINIC))

    /** Platform mismatch -- real `PLATFORM_NOT_ALLOWED` server reason code, `ASSERTION_PLATFORM_MISMATCH` local reason code on verify. */
    fun wrongPlatformAssertion(): SignedAssertionEnvelope =
        envelope(assertionPayload(licenseStatus = LicenseStatus.ACTIVE, platform = LicensingPlatform.WINDOWS))

    /** Signed by a key not present in the local `OwnerTrustStore` -- real `UNKNOWN_SIGNING_KEY` local reason code. */
    fun unknownKeyAssertion(): SignedAssertionEnvelope =
        envelope(assertionPayload(licenseStatus = LicenseStatus.ACTIVE), signingKeyId = "fixture-untrusted-signing-key-999")

    /** Structurally well-formed but semantically-fake signature -- real `ASSERTION_VERIFICATION_FAILED` local reason code. */
    fun malformedSignatureAssertion(): SignedAssertionEnvelope =
        envelope(assertionPayload(licenseStatus = LicenseStatus.ACTIVE), signature = "not-even-base64!!!")

    /** Real `UNSUPPORTED_CONTRACT_VERSION` server reason code -- a request/assertion using a contract version newer than this client understands. */
    fun futureContractVersionAssertion(): SignedAssertionEnvelope =
        envelope(assertionPayload(licenseStatus = LicenseStatus.ACTIVE, contractVersion = "v99"))

    /**
     * Real `VERSION_UNSUPPORTED` / `VERSION_NOT_ALLOWED` server reason codes.
     * `mobile-release-version-contract.md`'s own finding: Owner has no
     * minimum-app-version enforcement today, so this fixture exercises the
     * real *contract-version* rejection family instead of a fabricated
     * app-semantic-version gate.
     */
    fun minVersionFailureError(): LicensingError = LicensingError.fromServerCode("VERSION_UNSUPPORTED")

    /** One fixture per real `ServerReasonCode` -- used to prove the parser round-trips every real value. */
    fun allServerReasonCodeStrings(): List<String> = ServerReasonCode.entries.map { it.name }
}
