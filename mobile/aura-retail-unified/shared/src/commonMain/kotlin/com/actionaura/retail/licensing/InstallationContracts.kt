package com.actionaura.retail.licensing

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * M7.17 -- real, non-invasive installation identity
 * (`installation-identity-privacy-contract.md`). No field resembling a
 * persistent hardware identifier (no IMEI/serial/MAC/advertising ID) --
 * `installationId` is a locally-generated, throwaway value per that
 * document's own real finding.
 */
@Serializable
data class InstallationDescriptor(
    @SerialName("installation_id") val installationId: String,
    @SerialName("device_public_key") val devicePublicKey: String,
    @SerialName("device_public_key_algorithm") val devicePublicKeyAlgorithm: String = "ed25519",
    @SerialName("device_label") val deviceLabel: String? = null,
    @SerialName("os_version") val osVersion: String? = null,
    @SerialName("app_version") val appVersion: String,
    val platform: LicensingPlatform,
    @SerialName("release_channel") val releaseChannel: String? = null,
    val status: InstallationStatus,
)
