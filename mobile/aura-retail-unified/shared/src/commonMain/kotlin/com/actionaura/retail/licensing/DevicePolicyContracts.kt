package com.actionaura.retail.licensing

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * M8.1/M8.2 -- one resolved, immutable device policy
 * (`resolved-device-policy-contract.md`). The client consumes exactly
 * this one value; it never sees or reconciles Owner's four raw,
 * unreconciled device-limit columns
 * (`licensing-gap-ownership-matrix.md` gap #1).
 */
@Serializable
enum class DeviceLimitMode { TOTAL_ACTIVE_INSTALLATIONS }

@Serializable
data class ResolvedDevicePolicy(
    @SerialName("policy_version") val policyVersion: Int,
    @SerialName("license_public_id") val licensePublicId: String,
    @SerialName("product_code") val productCode: LicensingProductCode,
    @SerialName("allowed_platforms") val allowedPlatforms: List<String>,
    @SerialName("device_limit_mode") val deviceLimitMode: DeviceLimitMode = DeviceLimitMode.TOTAL_ACTIVE_INSTALLATIONS,
    @SerialName("total_active_installation_limit") val totalActiveInstallationLimit: Int,
    @SerialName("current_active_installation_count") val currentActiveInstallationCount: Int,
    @SerialName("remaining_installation_slots") val remainingInstallationSlots: Int,
    @SerialName("voluntary_deactivation_allowed") val voluntaryDeactivationAllowed: Boolean,
    @SerialName("replacement_allowed") val replacementAllowed: Boolean,
    @SerialName("same_installation_retry_consumes_slot") val sameInstallationRetryConsumesSlot: Boolean = false,
    @SerialName("release_channel") val releaseChannel: String? = null,
    @SerialName("policy_effective_at") val policyEffectiveAt: String,
) {
    companion object {
        val SUPPORTED_POLICY_VERSIONS = setOf(1)
    }

    /**
     * Real internal-consistency check, `resolved-device-policy-contract.md`.
     * Never computes or substitutes a device cap -- only validates the
     * server-provided one.
     */
    fun validate(): DevicePolicyValidationResult {
        val problems = mutableListOf<String>()
        if (policyVersion !in SUPPORTED_POLICY_VERSIONS) problems += "unsupported policyVersion=$policyVersion"
        if (totalActiveInstallationLimit < 0) problems += "negative totalActiveInstallationLimit"
        if (currentActiveInstallationCount < 0) problems += "negative currentActiveInstallationCount"
        if (remainingInstallationSlots < 0) problems += "negative remainingInstallationSlots"
        if (remainingInstallationSlots != totalActiveInstallationLimit - currentActiveInstallationCount) {
            problems += "remainingInstallationSlots does not agree with totalActiveInstallationLimit - currentActiveInstallationCount"
        }
        val decodedPlatforms = allowedPlatforms.map { PlatformDecodeResult.parse(it) }
        val unsupported = decodedPlatforms.filterIsInstance<PlatformDecodeResult.UnsupportedPlatform>()
        if (unsupported.isNotEmpty()) problems += "unsupported platform value(s) in allowedPlatforms: ${unsupported.map { it.raw }}"

        return if (problems.isEmpty()) {
            DevicePolicyValidationResult.Valid(decodedPlatforms.filterIsInstance<PlatformDecodeResult.Known>().map { it.platform })
        } else {
            DevicePolicyValidationResult.Malformed(problems)
        }
    }
}

sealed interface DevicePolicyValidationResult {
    data class Valid(val platforms: List<LicensingPlatform>) : DevicePolicyValidationResult
    data class Malformed(val problems: List<String>) : DevicePolicyValidationResult
}

/**
 * M8.3 -- real, closed interpretation of a server multi-device result
 * (`multi-device-scenario-contract.md`). Never locally computed from
 * first principles -- always derived from a real server reason code
 * or a real server-provided `ResolvedDevicePolicy` field.
 */
enum class DeviceSlotOutcome {
    DEVICE_SLOT_AVAILABLE,
    DEVICE_LIMIT_REACHED,
    SAME_INSTALLATION_RETRY,
    PLATFORM_NOT_ALLOWED,
    REPLACEMENT_ALLOWED,
    REPLACEMENT_REQUIRES_DEACTIVATION,
    INSTALLATION_REVOKED,
    SERVER_POLICY_UNAVAILABLE,
}

/**
 * M8.8 -- real, current, evidence-based iOS readiness snapshot
 * (`ios-platform-readiness-state.md`). `current()` reflects only
 * executable evidence gathered in M7/M8 -- never narrative confidence.
 */
enum class IosReadinessFlag {
    CONTRACT_SUPPORTED,
    OWNER_PLATFORM_UNSEEDED,
    PRODUCT_PLATFORM_MAPPING_MISSING,
    RELEASE_MAPPING_MISSING,
    SERVER_ACCEPTANCE_NOT_VERIFIED,
    CLIENT_BUILD_NOT_VERIFIED,
    CLIENT_RUNTIME_NOT_VERIFIED,
    READY_FOR_ACTIVATION,
}

data class IosPlatformReadinessState(val flags: Set<IosReadinessFlag>) {
    val readyForActivation: Boolean get() = flags == setOf(IosReadinessFlag.READY_FOR_ACTIVATION)

    companion object {
        /** The real, current, evidence-based snapshot -- see ios-platform-readiness-state.md. */
        fun current(): IosPlatformReadinessState = IosPlatformReadinessState(
            setOf(
                IosReadinessFlag.CONTRACT_SUPPORTED,
                IosReadinessFlag.OWNER_PLATFORM_UNSEEDED,
                IosReadinessFlag.PRODUCT_PLATFORM_MAPPING_MISSING,
                IosReadinessFlag.RELEASE_MAPPING_MISSING,
                IosReadinessFlag.SERVER_ACCEPTANCE_NOT_VERIFIED,
                IosReadinessFlag.CLIENT_BUILD_NOT_VERIFIED,
                IosReadinessFlag.CLIENT_RUNTIME_NOT_VERIFIED,
            ),
        )
    }
}
