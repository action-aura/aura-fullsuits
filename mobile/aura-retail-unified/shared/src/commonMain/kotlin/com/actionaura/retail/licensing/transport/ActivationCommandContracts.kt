package com.actionaura.retail.licensing.transport

import com.actionaura.retail.licensing.DeviceMetadata
import com.actionaura.retail.licensing.InstallationIdentity
import com.actionaura.retail.licensing.LicensingPlatform
import com.actionaura.retail.licensing.LicensingProductCode

/**
 * M9.10 -- immutable activation command
 * (`activation-command-contract.md`). Explicit typed fields only --
 * no `Map<String, Any>` -- and no client-selected commercial value
 * (device limit/active count/remaining slots/License expiry/
 * entitlement) anywhere on this type, matching the same discipline
 * `ResolvedDevicePolicy` already enforces structurally (M8.15's own
 * `onlyOneCanonicalDeviceLimitFieldExistsOnResolvedDevicePolicy`
 * proof).
 */
data class ActivationCommand(
    val customerSessionId: ExternalCustomerSessionId,
    val licenseClaimReference: String,
    val productCode: LicensingProductCode,
    val platform: LicensingPlatform,
    val installationIdentity: InstallationIdentity,
    val deviceMetadata: DeviceMetadata,
    val idempotencyKey: String,
    val clientContractVersion: String = "v1",
) {
    override fun toString(): String = "ActivationCommand(customerSessionId=$customerSessionId, licenseClaimReference=<redacted>, productCode=$productCode, platform=$platform, installationIdentity=$installationIdentity, deviceMetadata=$deviceMetadata, idempotencyKey=$idempotencyKey, clientContractVersion=$clientContractVersion)"
}
