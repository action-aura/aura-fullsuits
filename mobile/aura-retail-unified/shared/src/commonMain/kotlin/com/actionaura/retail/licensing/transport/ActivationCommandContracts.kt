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
    // WARNING (Task 8, multi-device-sync-foundation): this is NOT a raw
    // license key. The one real production caller, `ActivationViewModel.
    // onSubmitLicense()`, assigns this `outcome.result.licensePublicId` --
    // a server-assigned public ID returned by a real `claimLicense()` call
    // (still `TransportOutcome.TransportNotConfigured` today, no real
    // Owner-side claim authority exists yet). If you are wiring a REAL
    // activation call against Owner's actual
    // `owner/app/licensing_service/activation.py`, which wants the raw
    // plaintext `license_key` directly, do NOT put it here -- use
    // [DirectLicenseKeyActivationCommand.licenseKey] instead
    // (`HttpExternalLicensingTransport.activateWithLicenseKey()`). Putting
    // a raw key in this field would silently break the moment a real
    // `claimLicense` ships, since `activateInstallation()` (this command's
    // own real consumer) would start forwarding a public ID where Owner
    // expects a secret, and every real activation would fail with
    // `LICENSE_NOT_FOUND`.
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

/**
 * Task 8 (multi-device-sync-foundation) -- the real command type for the
 * ONLY activation path this codebase can actually perform against Owner
 * today: a direct, anonymous (no customer session) activation using the
 * raw plaintext license key, exactly matching
 * `owner/app/licensing_service/activation.py`'s real wire contract (which
 * has no concept of a customer session at all -- it authenticates purely
 * via `license_key` + device signature) and desktop's own
 * `commercial_runtime/licensing_contracts/client.py::LicensingClient.activate()`.
 *
 * Deliberately a SEPARATE type from [ActivationCommand], not a reuse of
 * its `licenseClaimReference` field -- see the warning on that field for
 * why conflating the two would be a real, silent landmine: this type's
 * [licenseKey] is unambiguously the raw secret; [ActivationCommand]'s
 * field is unambiguously a server-assigned public ID from the (still
 * unreal) customer-session+claim flow. A caller can never accidentally
 * pass the wrong one to the wrong transport method because the compiler
 * enforces the type difference.
 */
data class DirectLicenseKeyActivationCommand(
    val licenseKey: String,
    val productCode: LicensingProductCode,
    val platform: LicensingPlatform,
    val installationIdentity: InstallationIdentity,
    val deviceMetadata: DeviceMetadata,
    val idempotencyKey: String,
    val clientContractVersion: String = "v1",
) {
    /** The raw license key must never appear in a log line via toString() -- same discipline as `ActivationRequest.licenseKey`. */
    override fun toString(): String = "DirectLicenseKeyActivationCommand(licenseKey=<redacted>, productCode=$productCode, platform=$platform, installationIdentity=$installationIdentity, deviceMetadata=$deviceMetadata, idempotencyKey=$idempotencyKey, clientContractVersion=$clientContractVersion)"
}
