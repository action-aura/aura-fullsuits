package com.actionaura.retail.securestorage

import com.actionaura.retail.licensing.InstallationIdentity
import com.actionaura.retail.licensing.InstallationIdentityStatus
import com.actionaura.retail.licensing.InstallationIdentityVersion
import com.actionaura.retail.licensing.LicensingFixtures
import com.actionaura.retail.licensing.LocalInstallationSeed
import com.actionaura.retail.licensing.transport.ExternalCustomerAccessCredential
import com.actionaura.retail.licensing.transport.ExternalCustomerRefreshCredential
import com.actionaura.retail.licensing.transport.InstallationCredentialMaterial

/**
 * M10.28/M10.29 -- sanitized fixtures for the secure-storage test
 * matrix. No real credentials/PII/keys -- reuses the already-audited
 * M7.18 assertion fixture, same discipline as M9SanitizedFixtures.
 */
object SecureStorageFixtures {
    fun metadata(
        productCode: String = "AURA_RETAIL",
        installationId: String = "fixture-installation-0001",
        accountId: String? = "fixture-account-0001",
    ) = SecureActivationBundleMetadata(
        productCode = productCode, platform = "ANDROID", ownerInstallationId = installationId,
        customerAccountId = accountId, contractVersion = "v1", credentialRevision = 0, leaseRevision = 0,
        createdAtIso8601 = "2026-08-05T00:00:00Z", lastCommittedAtIso8601 = "2026-08-05T00:00:00Z",
    )

    fun bundle(metadata: SecureActivationBundleMetadata = metadata()) = SecureActivationBundle(
        metadata = metadata,
        customerAccessCredential = ExternalCustomerAccessCredential("FIXTURE_ACCESS_TOKEN"),
        customerRefreshCredential = ExternalCustomerRefreshCredential("FIXTURE_REFRESH_TOKEN"),
        installationIdentity = InstallationIdentity(
            seed = LocalInstallationSeed("FIXTURE_SEED_VALUE", InstallationIdentityVersion.V1),
            status = InstallationIdentityStatus.PERSISTED_SECURELY,
            generatedAt = "2026-08-05T00:00:00Z",
        ),
        installationCredential = InstallationCredentialMaterial("FIXTURE_INSTALLATION_CREDENTIAL"),
        rawSignedLease = LicensingFixtures.validAssertion(),
    )

    fun scope(metadata: SecureActivationBundleMetadata = metadata()) =
        SecureMaterialScope(metadata.productCode, metadata.ownerInstallationId, metadata.customerAccountId)

    fun store(blobStore: SecureBlobStore = InMemorySecureBlobStore(), genCounter: IntArray = intArrayOf(0)) =
        GenerationalSecureMaterialStore(blobStore, nowIso8601 = { "2026-08-05T00:00:00Z" }, newGenerationId = { "gen-${genCounter[0]++}" })
}
