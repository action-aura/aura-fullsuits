package com.actionaura.retail.licensing.lease

import com.actionaura.retail.licensing.InstallationStatus
import com.actionaura.retail.licensing.LicenseStatus
import com.actionaura.retail.licensing.LicensingPlatform
import com.actionaura.retail.licensing.LicensingProductCode
import com.actionaura.retail.licensing.SubscriptionStatus
import kotlinx.datetime.Instant
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/** M11.10 -- exact context a verified lease must be bound to. Supplied only by the production application container -- never user-selectable (M11.10's own explicit rule). */
data class LeaseVerificationContext(
    val expectedProductCode: LicensingProductCode,
    val expectedPlatform: LicensingPlatform,
    val expectedInstallationPublicId: String,
    val nowWallClockEpochMillis: Long,
    val trustedTimeAnchor: TrustedTimeAnchor?,
    val lastSuccessfulCheckInEpochMillis: Long,
    val lastCheckInAttemptOk: Boolean,
    val localSafetyCeilingSeconds: Long? = null,
)

/** M11.9 -- real, minimal verified-claims view the rest of the app may read AFTER signature verification succeeds. Deliberately narrow -- not the full raw JSON tree. */
data class VerifiedLeaseClaims(
    val productCode: LicensingProductCode,
    val platform: LicensingPlatform,
    val installationPublicId: String,
    val licenseStatus: LicenseStatus,
    val installationStatus: InstallationStatus,
    val subscriptionStatus: SubscriptionStatus,
    val contractVersion: String,
    val notBeforeEpochMillis: Long,
    val expiresAtEpochMillis: Long,
    val signingKeyId: String,
) {
    override fun toString(): String = "VerifiedLeaseClaims(productCode=$productCode, platform=$platform, licenseStatus=$licenseStatus, contractVersion=$contractVersion)"
}

sealed interface LeaseVerificationResult {
    data class Verified(val claims: VerifiedLeaseClaims, val offlineEvidence: OfflinePolicyEvidence, val anchor: TrustedTimeAnchor) : LeaseVerificationResult
    data class Rejected(val failure: LeaseVerificationFailure) : LeaseVerificationResult
}

/** Real minimum supported claim contract version -- M11 rejects an unrecognized/future contract version rather than guessing compatibility. */
const val SUPPORTED_LEASE_CONTRACT_VERSION = "1.0"

interface SignedLeaseVerifier {
    suspend fun verify(rawLease: ProtectedSignedLease, expectedContext: LeaseVerificationContext): LeaseVerificationResult
}

/**
 * M11.7 -- the one real commonMain verification boundary. Real,
 * exact step order (matches `assertion_verifier.py::verify_assertion`,
 * `canonical-signed-lease-authority-audit.md`): bound input → decode →
 * resolve key → verify signature → THEN parse/trust claims → validate
 * context → validate time. No claim is read/acted on before signature
 * verification succeeds -- a valid signature with invalid claims is
 * still rejected; a bad signature never reaches claim parsing at all.
 */
class GenerationalSignedLeaseVerifier(
    private val keyRing: LeaseVerificationKeyRing,
    private val monotonicClock: MonotonicClock = systemMonotonicClock(),
) : SignedLeaseVerifier {

    override suspend fun verify(rawLease: ProtectedSignedLease, expectedContext: LeaseVerificationContext): LeaseVerificationResult {
        if (rawLease.algorithm != "ed25519") {
            return reject(LeaseFailureCode.UNSUPPORTED_ALGORITHM)
        }

        val decoded = LeaseDecoder.decode(rawLease)
        val payload = when (decoded) {
            is LeaseDecodeResult.Failure -> return LeaseVerificationResult.Rejected(decoded.failure)
            is LeaseDecodeResult.Success -> decoded.payload
        }

        // Real key resolution BEFORE any cryptography is attempted -- matches
        // the canonical Python authority's own real ordering exactly.
        if (!keyRing.isTrusted(rawLease.signingKeyId)) {
            return reject(LeaseFailureCode.UNKNOWN_SIGNING_KEY)
        }
        val trustedKey = keyRing.resolve(rawLease.signingKeyId) ?: return reject(LeaseFailureCode.UNKNOWN_SIGNING_KEY)

        val publicKeyRaw = decodeBase64OrNull(trustedKey.publicKeyB64) ?: return reject(LeaseFailureCode.MALFORMED_PUBLIC_KEY)
        val signatureRaw = decodeBase64OrNull(rawLease.signature) ?: return reject(LeaseFailureCode.MALFORMED_SIGNATURE)
        if (publicKeyRaw.size != LeaseDecodingLimits.ED25519_PUBLIC_KEY_BYTES) return reject(LeaseFailureCode.MALFORMED_PUBLIC_KEY)
        if (signatureRaw.size != LeaseDecodingLimits.ED25519_SIGNATURE_BYTES) return reject(LeaseFailureCode.MALFORMED_SIGNATURE)

        val messageBytes = rawLease.payloadCanonicalJson.encodeToByteArray()
        val signatureValid = verifyEd25519Signature(publicKeyRaw, signatureRaw, messageBytes)
        if (!signatureValid) return reject(LeaseFailureCode.SIGNATURE_INVALID)

        // Only now -- signature cryptographically verified -- do we trust any claim.
        val claims = try {
            parseClaims(payload, rawLease.signingKeyId)
        } catch (e: Exception) {
            return reject(LeaseFailureCode.MALFORMED_ENVELOPE, e::class.simpleName)
        } ?: return reject(LeaseFailureCode.MALFORMED_ENVELOPE, "unparseable claims")

        if (claims.contractVersion != SUPPORTED_LEASE_CONTRACT_VERSION) {
            return reject(LeaseFailureCode.UNSUPPORTED_CLAIM_VERSION, claims.contractVersion)
        }

        // M11.10 -- exact context binding, no fallback/wildcard.
        if (claims.productCode != expectedContext.expectedProductCode) return reject(LeaseFailureCode.PRODUCT_MISMATCH)
        if (claims.platform != expectedContext.expectedPlatform) return reject(LeaseFailureCode.PLATFORM_MISMATCH)
        if (claims.installationPublicId != expectedContext.expectedInstallationPublicId) return reject(LeaseFailureCode.INSTALLATION_MISMATCH)

        val tolerance = 60_000L // real, exact port of assertion_verifier.py's CLOCK_SKEW_TOLERANCE_SECONDS = 60
        val nowForWindowCheck = expectedContext.trustedTimeAnchor?.let { TrustedTimeAuthority.trustedNowMillis(it, monotonicClock) }
            ?: expectedContext.nowWallClockEpochMillis
        if (nowForWindowCheck < claims.notBeforeEpochMillis - tolerance) return reject(LeaseFailureCode.NOT_YET_VALID)
        if (nowForWindowCheck > claims.expiresAtEpochMillis + tolerance) return reject(LeaseFailureCode.EXPIRED)

        val offlinePolicy = payload["offline_policy"]?.jsonObject
            ?: return reject(LeaseFailureCode.MALFORMED_ENVELOPE, "missing offline_policy")
        val evidence = try {
            parseOfflinePolicyEvidence(payload, offlinePolicy, claims)
        } catch (e: Exception) {
            return reject(LeaseFailureCode.MALFORMED_ENVELOPE, "malformed offline_policy: ${e::class.simpleName}")
        }

        val anchor = expectedContext.trustedTimeAnchor
            ?: TrustedTimeAuthority.newAnchor(expectedContext.nowWallClockEpochMillis, monotonicClock)

        return LeaseVerificationResult.Verified(claims, evidence, anchor)
    }

    private fun parseClaims(payload: JsonObject, signingKeyId: String): VerifiedLeaseClaims? {
        val productRaw = payload["product_code"]?.jsonPrimitive?.content ?: return null
        val platformRaw = payload["platform"]?.jsonPrimitive?.content ?: return null
        val product = LicensingProductCode.entries.firstOrNull { it.name == productRaw } ?: return null
        val platform = LicensingPlatform.entries.firstOrNull { it.name == platformRaw } ?: return null
        val installationId = payload["installation_public_id"]?.jsonPrimitive?.content ?: return null
        val licenseStatus = LicenseStatus.entries.firstOrNull { it.name == payload["license_status"]?.jsonPrimitive?.content } ?: return null
        val installationStatus = InstallationStatus.entries.firstOrNull { it.name == payload["installation_status"]?.jsonPrimitive?.content } ?: return null
        val subscriptionStatus = SubscriptionStatus.entries.firstOrNull { it.name == payload["subscription_status"]?.jsonPrimitive?.content } ?: return null
        val contractVersion = payload["contract_version"]?.jsonPrimitive?.content ?: return null
        val notBefore = payload["not_before"]?.jsonPrimitive?.content?.let { parseIso8601Millis(it) } ?: return null
        val expiresAt = payload["expires_at"]?.jsonPrimitive?.content?.let { parseIso8601Millis(it) } ?: return null
        return VerifiedLeaseClaims(
            productCode = product, platform = platform, installationPublicId = installationId,
            licenseStatus = licenseStatus, installationStatus = installationStatus, subscriptionStatus = subscriptionStatus,
            contractVersion = contractVersion, notBeforeEpochMillis = notBefore, expiresAtEpochMillis = expiresAt,
            signingKeyId = signingKeyId,
        )
    }

    private fun parseOfflinePolicyEvidence(payload: JsonObject, policy: JsonObject, claims: VerifiedLeaseClaims): OfflinePolicyEvidence {
        fun long(key: String): Long = policy[key]!!.jsonPrimitive.content.toLong()
        val commercialGraceEnd = payload["commercial_grace_end"]?.jsonPrimitive?.content?.let { parseIso8601Millis(it) }
        val emergencyExtensionUntil = policy["emergency_extension_until"]?.jsonPrimitive?.content?.let { parseIso8601Millis(it) }
        return OfflinePolicyEvidence(
            notBeforeEpochMillis = claims.notBeforeEpochMillis,
            expiresAtEpochMillis = claims.expiresAtEpochMillis,
            licenseStatus = claims.licenseStatus,
            installationStatus = claims.installationStatus,
            subscriptionStatus = claims.subscriptionStatus,
            checkInIntervalSeconds = long("check_in_interval_seconds"),
            retryIntervalSeconds = long("retry_interval_seconds"),
            offlineGraceSeconds = long("offline_grace_seconds"),
            warningStartSeconds = long("warning_start_seconds"),
            hardExpiryBehavior = policy["hard_expiry_behavior"]!!.jsonPrimitive.content,
            clockRollbackToleranceSeconds = long("clock_rollback_tolerance_seconds"),
            emergencyExtensionAllowed = policy["emergency_extension_allowed"]?.jsonPrimitive?.content?.toBoolean() ?: false,
            emergencyExtensionUntilEpochMillis = emergencyExtensionUntil,
            commercialGraceEndEpochMillis = commercialGraceEnd,
        )
    }

    private fun reject(code: LeaseFailureCode, reason: String? = null): LeaseVerificationResult.Rejected =
        LeaseVerificationResult.Rejected(LeaseVerificationFailure(code, reason))
}

private fun parseIso8601Millis(iso: String): Long = Instant.parse(iso).toEpochMilliseconds()

@OptIn(kotlin.io.encoding.ExperimentalEncodingApi::class)
internal fun decodeBase64OrNull(value: String): ByteArray? = try {
    kotlin.io.encoding.Base64.decode(value)
} catch (e: IllegalArgumentException) {
    null
}
