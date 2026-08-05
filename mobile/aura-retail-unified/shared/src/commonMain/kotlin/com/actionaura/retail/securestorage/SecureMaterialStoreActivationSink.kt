package com.actionaura.retail.securestorage

import com.actionaura.retail.licensing.InstallationIdentity
import com.actionaura.retail.licensing.SignedAssertionEnvelope
import com.actionaura.retail.licensing.transport.ExternalCustomerAccessCredential
import com.actionaura.retail.licensing.transport.ExternalCustomerRefreshCredential
import com.actionaura.retail.licensing.transport.InstallationCredentialMaterial
import com.actionaura.retail.licensing.transport.InstallationCredentialSink
import com.actionaura.retail.licensing.transport.SecureMaterialCommitResult
import com.actionaura.retail.licensing.transport.SignedLeaseSink
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * M10.21 -- the real production bridge between M9's two-call secure-
 * material handoff boundary (`InstallationCredentialSink`/
 * `SignedLeaseSink`, `ActivationResponseProcessor`) and M10's real
 * atomic activation-bundle commit (`GenerationalSecureMaterialStore`).
 *
 * `ActivationResponseProcessor` calls the credential sink and the
 * lease sink as two separate `commit(...)` calls -- this adapter
 * gathers both pieces (plus the real Installation identity and
 * optional Customer session material supplied at construction) and
 * performs exactly one real, atomic `commitActivationBundle` call once
 * both required pieces have arrived. Neither individual `commit` call
 * durably persists anything by itself -- only the combined,
 * real atomic commit does, matching M10.6's own "no Installation
 * credential exists without its matching lease metadata" guarantee.
 */
class SecureMaterialStoreActivationSink(
    private val store: SecureMaterialStore,
    private val installationIdentity: InstallationIdentity,
    private val productCode: String,
    private val platform: String,
    private val ownerInstallationId: String,
    private val customerAccountId: String? = null,
    private val customerAccessCredential: ExternalCustomerAccessCredential? = null,
    private val customerRefreshCredential: ExternalCustomerRefreshCredential? = null,
    private val nowIso8601: () -> String,
) : InstallationCredentialSink, SignedLeaseSink {

    private val mutex = Mutex()
    private var pendingCredential: InstallationCredentialMaterial? = null
    private var pendingLease: SignedAssertionEnvelope? = null

    override suspend fun commit(installationId: String, credential: InstallationCredentialMaterial): SecureMaterialCommitResult = mutex.withLock {
        pendingCredential = credential
        tryCommitLocked()
    }

    override suspend fun commit(installationId: String, lease: SignedAssertionEnvelope): SecureMaterialCommitResult = mutex.withLock {
        pendingLease = lease
        tryCommitLocked()
    }

    private suspend fun tryCommitLocked(): SecureMaterialCommitResult {
        val credential = pendingCredential ?: return SecureMaterialCommitResult.Failed("awaiting matching lease/credential component")
        val lease = pendingLease ?: return SecureMaterialCommitResult.Failed("awaiting matching lease/credential component")

        val metadata = SecureActivationBundleMetadata(
            productCode = productCode, platform = platform, ownerInstallationId = ownerInstallationId,
            customerAccountId = customerAccountId, contractVersion = leaseContractVersion(lease),
            credentialRevision = 0, leaseRevision = 0,
            createdAtIso8601 = nowIso8601(), lastCommittedAtIso8601 = nowIso8601(),
        )
        val bundle = SecureActivationBundle(
            metadata = metadata,
            customerAccessCredential = customerAccessCredential,
            customerRefreshCredential = customerRefreshCredential,
            installationIdentity = installationIdentity,
            installationCredential = credential,
            rawSignedLease = lease,
        )

        return when (val result = store.commitActivationBundle(bundle)) {
            is SecureStorageResult.Success -> {
                pendingCredential = null
                pendingLease = null
                SecureMaterialCommitResult.Committed
            }
            is SecureStorageResult.Failure -> SecureMaterialCommitResult.Failed(result.failure.code.name)
        }
    }

    private fun leaseContractVersion(lease: SignedAssertionEnvelope): String = lease.payload.contractVersion
}
