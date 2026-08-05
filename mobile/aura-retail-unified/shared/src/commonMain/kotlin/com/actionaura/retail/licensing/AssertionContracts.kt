package com.actionaura.retail.licensing

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * M7.17 -- real, versioned signed-assertion contract
 * (`installation-credential-contract.md`, `offline-license-lease-contract-audit.md`).
 * Field set matches `ALLOWED_PAYLOAD_FIELDS`,
 * `commercial_runtime/licensing_contracts/assertion_verifier.py:59-100`,
 * exactly -- no field added, none renamed. Immutable, no platform classes.
 */
@Serializable
data class OfflinePolicy(
    @SerialName("check_in_interval_seconds") val checkInIntervalSeconds: Long,
    @SerialName("retry_interval_seconds") val retryIntervalSeconds: Long,
    @SerialName("offline_grace_seconds") val offlineGraceSeconds: Long,
    @SerialName("warning_start_seconds") val warningStartSeconds: Long,
    @SerialName("hard_expiry_behavior") val hardExpiryBehavior: String,
    @SerialName("clock_rollback_tolerance_seconds") val clockRollbackToleranceSeconds: Long,
    @SerialName("assertion_refresh_threshold_seconds") val assertionRefreshThresholdSeconds: Long,
    @SerialName("emergency_extension_allowed") val emergencyExtensionAllowed: Boolean,
    @SerialName("emergency_extension_until") val emergencyExtensionUntil: String? = null,
)

@Serializable
data class AssertionPayload(
    @SerialName("assertion_id") val assertionId: String,
    val issuer: String,
    @SerialName("product_code") val productCode: LicensingProductCode,
    @SerialName("license_public_id") val licensePublicId: String,
    @SerialName("installation_public_id") val installationPublicId: String,
    val platform: LicensingPlatform,
    @SerialName("app_version_policy") val appVersionPolicy: String,
    @SerialName("release_channel") val releaseChannel: String? = null,
    @SerialName("issued_at") val issuedAt: String,
    @SerialName("not_before") val notBefore: String,
    @SerialName("expires_at") val expiresAt: String,
    @SerialName("license_status") val licenseStatus: LicenseStatus,
    @SerialName("installation_status") val installationStatus: InstallationStatus,
    @SerialName("subscription_status") val subscriptionStatus: SubscriptionStatus,
    @SerialName("allowed_device_count") val allowedDeviceCount: Int,
    @SerialName("device_key_fingerprint") val deviceKeyFingerprint: String,
    val entitlements: Map<String, String> = emptyMap(),
    @SerialName("offline_policy") val offlinePolicy: OfflinePolicy,
    @SerialName("contract_version") val contractVersion: String,
    @SerialName("commercial_policy_version") val commercialPolicyVersion: String? = null,
    @SerialName("renewal_status") val renewalStatus: String? = null,
    @SerialName("plan_code") val planCode: String? = null,
    @SerialName("term_start") val termStart: String? = null,
    @SerialName("term_end") val termEnd: String? = null,
    @SerialName("past_due_since") val pastDueSince: String? = null,
    @SerialName("commercial_grace_end") val commercialGraceEnd: String? = null,
    @SerialName("pilot_status") val pilotStatus: String? = null,
    @SerialName("emergency_extension_id") val emergencyExtensionId: String? = null,
) {
    /** No secret material lives on this type; still explicit so a future field addition is forced to reconsider this. */
    override fun toString(): String = "AssertionPayload(assertionId=$assertionId, licensePublicId=$licensePublicId, installationPublicId=$installationPublicId, licenseStatus=$licenseStatus)"
}

@Serializable
data class SignedAssertionEnvelope(
    val payload: AssertionPayload,
    @SerialName("signing_key_id") val signingKeyId: String,
    val algorithm: String,
    @SerialName("assertion_version") val assertionVersion: Int,
    val signature: String,
) {
    override fun toString(): String = "SignedAssertionEnvelope(signingKeyId=$signingKeyId, algorithm=$algorithm, assertionVersion=$assertionVersion, signature=<redacted>)"
}
