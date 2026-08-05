package com.actionaura.retail.licensing

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * M8.7 -- real, bounded device metadata allowlist
 * (`device-metadata-minimization.md`). Closed data class, no open Map
 * field -- nothing outside these 8 fields may ever be added without a
 * corresponding doc update.
 */
@Serializable
data class DeviceMetadata(
    @SerialName("device_label") val deviceLabel: String? = null,
    val platform: LicensingPlatform,
    @SerialName("os_version_major") val osVersionMajor: String? = null,
    @SerialName("app_version") val appVersion: String,
    @SerialName("app_build_number") val appBuildNumber: String? = null,
    @SerialName("device_model_family") val deviceModelFamily: String? = null,
    val locale: String? = null,
    val timezone: String? = null,
)

/**
 * M8.6 -- device replacement/transfer intent models
 * (`device-replacement-transfer-contract.md`). No HTTP execution.
 * Every request carries only the target Installation's identifier --
 * none grants the calling device authority to deactivate a different
 * Installation without real server authorization.
 */
data class DeactivateInstallationRequest(val installationId: String, val reason: String? = null)
data class RequestDeviceReplacementRequest(val oldInstallationId: String, val reason: String)
data class ReplaceLostDeviceRequest(val oldInstallationId: String, val reason: String)
data class ReplaceRevokedDeviceRequest(val oldInstallationId: String, val reason: String)
data class CancelReplacementRequest(val replacementRequestId: String)
data class ListInstallationsRequest(val licensePublicId: String)
data class RenameDeviceLabelRequest(val installationId: String, val newLabel: String)

enum class DeviceManagementOutcome {
    DEACTIVATED,
    REPLACEMENT_APPROVED,
    REPLACEMENT_DENIED,
    DEVICE_NOT_FOUND,
    DEVICE_ALREADY_INACTIVE,
    DEVICE_REVOKED,
    REAUTHENTICATION_REQUIRED,
    OWNER_SUPPORT_REQUIRED,
}
