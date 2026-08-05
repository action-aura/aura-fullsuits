package com.actionaura.retail.securestorage

import com.actionaura.retail.licensing.InstallationIdentity
import com.actionaura.retail.licensing.SignedAssertionEnvelope
import com.actionaura.retail.licensing.transport.ExternalCustomerAccessCredential
import com.actionaura.retail.licensing.transport.ExternalCustomerRefreshCredential
import com.actionaura.retail.licensing.transport.InstallationCredentialMaterial
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * M10.5 -- one immutable secure activation bundle
 * (`secure-activation-bundle-contract.md`). Separates secret payload
 * from non-secret metadata; aligns exactly with the real M7-M9
 * contracts (`AssertionPayload`, `ExternalCustomerSession`,
 * `InstallationIdentity`) -- no new business field is invented here.
 */
@Serializable
data class SecureActivationBundleMetadata(
    @SerialName("product_code") val productCode: String,
    @SerialName("platform") val platform: String,
    @SerialName("owner_installation_id") val ownerInstallationId: String,
    @SerialName("customer_account_id") val customerAccountId: String? = null,
    @SerialName("contract_version") val contractVersion: String,
    @SerialName("credential_revision") val credentialRevision: Int,
    @SerialName("lease_revision") val leaseRevision: Int,
    @SerialName("created_at") val createdAtIso8601: String,
    @SerialName("last_committed_at") val lastCommittedAtIso8601: String,
    @SerialName("safe_server_time_observation") val safeServerTimeObservationIso8601: String? = null,
    @SerialName("resolved_policy_version") val resolvedPolicyVersion: Int? = null,
    @SerialName("resolved_policy_snapshot_trusted") val resolvedPolicySnapshotTrusted: Boolean = false,
    @SerialName("release_channel") val releaseChannel: String? = null,
)

/**
 * The real secret payload -- never serialized into the same plaintext
 * blob as [SecureActivationBundleMetadata] without encryption; each
 * component is committed as its own separately-encrypted blob
 * (`GenerationalSecureMaterialStore`), this class exists only as the
 * in-memory, pre-commit / post-load shape.
 */
data class SecureActivationBundle(
    val metadata: SecureActivationBundleMetadata,
    val customerAccessCredential: ExternalCustomerAccessCredential?,
    val customerRefreshCredential: ExternalCustomerRefreshCredential?,
    val installationIdentity: InstallationIdentity,
    val installationCredential: InstallationCredentialMaterial,
    val rawSignedLease: SignedAssertionEnvelope,
) {
    override fun toString(): String =
        "SecureActivationBundle(metadata=$metadata, installationIdentity=$installationIdentity, customerAccessCredential=<redacted>, customerRefreshCredential=<redacted>, installationCredential=<redacted>, rawSignedLease=<redacted>)"
}
