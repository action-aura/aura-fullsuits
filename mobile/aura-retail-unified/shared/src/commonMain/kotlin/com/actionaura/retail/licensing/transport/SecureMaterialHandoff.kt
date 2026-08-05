package com.actionaura.retail.licensing.transport

import com.actionaura.retail.licensing.SignedAssertionEnvelope

/**
 * M9.13 -- narrow interface for future M10 secure storage
 * (`secure-material-handoff-boundary.md`). No Android Keystore/
 * EncryptedSharedPreferences/iOS Keychain/Secure Enclave here -- M9
 * only defines the boundary; M10 implements a real sink.
 */
sealed interface SecureMaterialCommitResult {
    data object Committed : SecureMaterialCommitResult
    data class Failed(val reason: String) : SecureMaterialCommitResult
    data object Cancelled : SecureMaterialCommitResult
}

/** Opaque installation credential material -- never exposed to Compose/ViewModel state, never logged. */
class InstallationCredentialMaterial(private val raw: String) {
    fun expose(): String = raw
    override fun toString(): String = "InstallationCredentialMaterial(<redacted>)"
}

interface InstallationCredentialSink {
    suspend fun commit(installationId: String, credential: InstallationCredentialMaterial): SecureMaterialCommitResult
}

interface SignedLeaseSink {
    suspend fun commit(installationId: String, lease: SignedAssertionEnvelope): SecureMaterialCommitResult
}

interface CustomerSessionCredentialSink {
    suspend fun commit(accountId: ExternalCustomerAccountId, accessCredential: ExternalCustomerAccessCredential, refreshCredential: ExternalCustomerRefreshCredential?): SecureMaterialCommitResult
}

/**
 * Test-only, in-memory sink -- never reachable from production wiring
 * (`production-transport-availability-rule.md`'s own discipline
 * extended to secure-material sinks). Real production wiring in M9
 * has no sink implementation at all -- see
 * `NoSecureStorageAvailableSink` for the honest production default.
 */
class InMemorySecureMaterialSink : InstallationCredentialSink, SignedLeaseSink, CustomerSessionCredentialSink {
    private val credentials = mutableMapOf<String, InstallationCredentialMaterial>()
    private val leases = mutableMapOf<String, SignedAssertionEnvelope>()
    private val sessions = mutableMapOf<String, Pair<ExternalCustomerAccessCredential, ExternalCustomerRefreshCredential?>>()

    override suspend fun commit(installationId: String, credential: InstallationCredentialMaterial): SecureMaterialCommitResult {
        credentials[installationId] = credential
        return SecureMaterialCommitResult.Committed
    }

    override suspend fun commit(installationId: String, lease: SignedAssertionEnvelope): SecureMaterialCommitResult {
        leases[installationId] = lease
        return SecureMaterialCommitResult.Committed
    }

    override suspend fun commit(accountId: ExternalCustomerAccountId, accessCredential: ExternalCustomerAccessCredential, refreshCredential: ExternalCustomerRefreshCredential?): SecureMaterialCommitResult {
        sessions[accountId.value] = accessCredential to refreshCredential
        return SecureMaterialCommitResult.Committed
    }

    fun hasCredentialFor(installationId: String): Boolean = credentials.containsKey(installationId)
    fun hasLeaseFor(installationId: String): Boolean = leases.containsKey(installationId)
}

/**
 * Real, honest production default for M9 -- no platform secure
 * storage exists yet, so every commit deterministically fails with a
 * clear reason. Never silently "succeeds" without real persistence.
 */
class NoSecureStorageAvailableSink : InstallationCredentialSink, SignedLeaseSink, CustomerSessionCredentialSink {
    private val failure = SecureMaterialCommitResult.Failed("SECURE_STORAGE_NOT_IMPLEMENTED")
    override suspend fun commit(installationId: String, credential: InstallationCredentialMaterial): SecureMaterialCommitResult = failure
    override suspend fun commit(installationId: String, lease: SignedAssertionEnvelope): SecureMaterialCommitResult = failure
    override suspend fun commit(accountId: ExternalCustomerAccountId, accessCredential: ExternalCustomerAccessCredential, refreshCredential: ExternalCustomerRefreshCredential?): SecureMaterialCommitResult = failure
}
