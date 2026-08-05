package com.actionaura.retail.licensing

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * M7.17 -- real release/version contract (`mobile-release-version-contract.md`).
 * Modeled from the real `ProductVersion` fields exposed by
 * `GET /product-version-check` -- disclosed as NOT a live, authenticated
 * route today (`app/api/routes.py`, prototype-only, disabled by default).
 * A mobile client must not depend on this being reachable until a later
 * milestone closes that `OWNER_SERVER`/`RELEASE_PIPELINE` gap.
 */
@Serializable
data class ReleaseCheckRequest(
    @SerialName("product_code") val productCode: LicensingProductCode,
    val platform: LicensingPlatform,
    @SerialName("release_channel") val releaseChannel: String? = null,
)

@Serializable
data class ReleaseCheckResult(
    val version: String,
    @SerialName("is_current_stable") val isCurrentStable: Boolean,
    @SerialName("is_deprecated") val isDeprecated: Boolean,
    @SerialName("release_channel") val releaseChannel: String,
    @SerialName("artifact_checksum") val artifactChecksumSha256: String,
)
