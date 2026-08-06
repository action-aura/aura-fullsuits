package com.actionaura.retail.licensing.lease

import com.google.crypto.tink.subtle.Ed25519Sign
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonObject
import kotlin.io.encoding.Base64
import kotlin.io.encoding.ExperimentalEncodingApi

/**
 * M11.34 -- real, sanitized signed-lease fixture generator. Uses a
 * REAL Ed25519 keypair (Tink's `Ed25519Sign.KeyPair`, the same real
 * primitive `SignedLeaseSignatureVerifier.android.kt` verifies with)
 * and REAL signing over the REAL canonical JSON this fixture itself
 * constructs -- never a fake/mocked signature. Test-only: this file
 * lives in `androidUnitTest`, never reachable from production wiring
 * (`production-test-material-isolation.md`).
 */
@OptIn(ExperimentalEncodingApi::class)
object LeaseTestFixtures {

    data class Generated(val lease: ProtectedSignedLease, val trustedKey: TrustedLeaseKey, val keyPair: Ed25519Sign.KeyPair)

    fun freshKeyPair(keyId: String = "test-ed25519-key-1"): Pair<Ed25519Sign.KeyPair, TrustedLeaseKey> {
        val keyPair = Ed25519Sign.KeyPair.newKeyPair()
        val trustedKey = TrustedLeaseKey(
            keyId = keyId,
            publicKeyB64 = Base64.encode(keyPair.publicKey),
            algorithm = "ed25519",
            status = LeaseKeyStatus.ACTIVE,
            source = LeaseKeySource.BUNDLED_ANCHOR,
        )
        return keyPair to trustedKey
    }

    fun defaultPayload(
        productCode: String = "AURA_RETAIL",
        platform: String = "ANDROID",
        installationPublicId: String = "test-installation-0001",
        licenseStatus: String = "ACTIVE",
        installationStatus: String = "ACTIVE",
        subscriptionStatus: String = "ACTIVE",
        notBeforeIso: String = "2026-01-01T00:00:00Z",
        expiresAtIso: String = "2030-01-01T00:00:00Z",
        contractVersion: String = SUPPORTED_LEASE_CONTRACT_VERSION,
        offlineGraceSeconds: Long = 259_200,
        checkInIntervalSeconds: Long = 86_400,
        retryIntervalSeconds: Long = 3_600,
        warningStartSeconds: Long = 43_200,
        hardExpiryBehavior: String = "RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA",
        clockRollbackToleranceSeconds: Long = 300,
        emergencyExtensionAllowed: Boolean = false,
        emergencyExtensionUntilIso: String? = null,
        commercialGraceEndIso: String? = null,
    ): JsonObject = buildJsonObject {
        put("assertion_id", "fixture-assertion-0001")
        put("issuer", "owner-fixture")
        put("product_code", productCode)
        put("license_public_id", "fixture-license-0001")
        put("installation_public_id", installationPublicId)
        put("platform", platform)
        put("app_version_policy", "1.0.0")
        put("issued_at", "2026-01-01T00:00:00Z")
        put("not_before", notBeforeIso)
        put("expires_at", expiresAtIso)
        put("license_status", licenseStatus)
        put("installation_status", installationStatus)
        put("subscription_status", subscriptionStatus)
        put("allowed_device_count", 3)
        put("device_key_fingerprint", "fixture-device-fingerprint")
        putJsonObject("entitlements") { put("reports", true) }
        putJsonObject("offline_policy") {
            put("check_in_interval_seconds", checkInIntervalSeconds)
            put("retry_interval_seconds", retryIntervalSeconds)
            put("offline_grace_seconds", offlineGraceSeconds)
            put("warning_start_seconds", warningStartSeconds)
            put("hard_expiry_behavior", hardExpiryBehavior)
            put("clock_rollback_tolerance_seconds", clockRollbackToleranceSeconds)
            put("assertion_refresh_threshold_seconds", 43_200)
            put("emergency_extension_allowed", emergencyExtensionAllowed)
            if (emergencyExtensionUntilIso != null) put("emergency_extension_until", emergencyExtensionUntilIso)
        }
        put("contract_version", contractVersion)
        if (commercialGraceEndIso != null) put("commercial_grace_end", commercialGraceEndIso)
    }

    /** Real signing: canonicalize, then real Ed25519 sign via Tink. */
    fun sign(payload: JsonObject, keyPair: Ed25519Sign.KeyPair, keyId: String): ProtectedSignedLease {
        val canonicalJson = LeaseCanonicalJson.canonicalize(payload)
        val signature = Ed25519Sign(keyPair.privateKey).sign(canonicalJson.encodeToByteArray())
        return ProtectedSignedLease(
            payloadCanonicalJson = canonicalJson,
            signingKeyId = keyId,
            algorithm = "ed25519",
            signature = Base64.encode(signature),
        )
    }

    fun generateValid(payloadOverrides: JsonObject = defaultPayload()): Generated {
        val (keyPair, trustedKey) = freshKeyPair()
        val lease = sign(payloadOverrides, keyPair, trustedKey.keyId)
        return Generated(lease, trustedKey, keyPair)
    }

    fun defaultContext(installationPublicId: String = "test-installation-0001", nowEpochMillis: Long = parseIso("2026-06-01T00:00:00Z")): LeaseVerificationContext =
        LeaseVerificationContext(
            expectedProductCode = com.actionaura.retail.licensing.LicensingProductCode.AURA_RETAIL,
            expectedPlatform = com.actionaura.retail.licensing.LicensingPlatform.ANDROID,
            expectedInstallationPublicId = installationPublicId,
            nowWallClockEpochMillis = nowEpochMillis,
            trustedTimeAnchor = TrustedTimeAnchor(nowEpochMillis, 0L),
            lastSuccessfulCheckInEpochMillis = nowEpochMillis,
            lastCheckInAttemptOk = true,
        )

    fun parseIso(iso: String): Long = kotlinx.datetime.Instant.parse(iso).toEpochMilliseconds()
}
