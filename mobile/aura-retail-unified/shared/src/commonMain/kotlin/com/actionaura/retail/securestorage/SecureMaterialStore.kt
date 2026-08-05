package com.actionaura.retail.securestorage

/**
 * M10.4/M10.6 -- the real, higher-level secure-storage authority
 * presentation/orchestration code depends on. One real implementation
 * exists in commonMain ([GenerationalSecureMaterialStore]), built on
 * top of the narrow platform [SecureBlobStore] primitive -- atomicity/
 * generation/recovery logic is written once, pure, and unit-testable
 * on the JVM, never duplicated per platform.
 */
interface SecureMaterialStore {
    suspend fun initialize(): SecureStorageResult<Unit>
    suspend fun capability(): SecureStorageCapability
    suspend fun commitActivationBundle(bundle: SecureActivationBundle): SecureStorageResult<SecureMaterialSnapshot>
    suspend fun loadActivationBundle(scope: SecureMaterialScope): SecureStorageResult<SecureActivationBundle?>
    suspend fun deleteScope(scope: SecureMaterialScope): SecureStorageResult<Unit>
    suspend fun rotateKey(): SecureStorageResult<Unit>
    suspend fun recoverInterruptedCommit(scope: SecureMaterialScope): SecureStorageResult<SecureStorageRecoveryOutcome>
    suspend fun health(scope: SecureMaterialScope): SecureStorageResult<SecureStorageHealth>
}
