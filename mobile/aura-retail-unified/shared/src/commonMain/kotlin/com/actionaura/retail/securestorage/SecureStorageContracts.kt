package com.actionaura.retail.securestorage

/**
 * M10.4 -- shared secure-storage authority
 * (`shared-secure-storage-contract.md`). Secure storage and business-
 * database writes are separate authorities (M10.30's own requirement)
 * -- this package never touches `DatabaseWriteGate` or any SQLDelight
 * type.
 */

enum class SecureMaterialType {
    CUSTOMER_ACCESS_CREDENTIAL, CUSTOMER_REFRESH_CREDENTIAL, CUSTOMER_SESSION_METADATA,
    INSTALLATION_IDENTITY_SEED, INSTALLATION_CREDENTIAL, SIGNED_LEASE, ACTIVATION_BUNDLE_METADATA,
}

/** Real scope binding -- prevents one Product/Installation/account's material from being read under another's key (associated-data binding, M10.8). */
data class SecureMaterialScope(
    val productCode: String,
    val installationId: String? = null,
    val accountId: String? = null,
)

data class SecureMaterialKey(val type: SecureMaterialType, val scope: SecureMaterialScope) {
    /** Real, stable, non-secret name derived from the key -- safe to use as a platform storage identifier and safe to log. */
    fun stableName(): String = "${type.name}:${scope.productCode}:${scope.installationId ?: "-"}:${scope.accountId ?: "-"}"
}

data class SecureMaterialVersion(val storageFormatVersion: Int, val materialTypeVersion: Int) {
    companion object {
        val CURRENT = SecureMaterialVersion(storageFormatVersion = 1, materialTypeVersion = 1)
    }
}

data class SecureMaterialRevision(val value: Int) {
    fun next(): SecureMaterialRevision = SecureMaterialRevision(value + 1)
    companion object { val INITIAL = SecureMaterialRevision(0) }
}

/** Real, closed failure vocabulary -- never carries a secret value. */
enum class SecureStorageFailureCode {
    NOT_AVAILABLE, LOCKED, AUTHENTICATION_REQUIRED, KEY_INVALIDATED, CORRUPT_DATA,
    PARTIAL_COMMIT, UNSUPPORTED_VERSION, WRITE_FAILED, READ_FAILED, DELETE_FAILED,
    ROTATION_FAILED, STORAGE_FULL, ACCESS_DENIED, CANCELLED, UNKNOWN_SAFE_FAILURE,
}

data class SecureStorageFailure(val code: SecureStorageFailureCode, val safeDiagnosticReason: String? = null) {
    override fun toString(): String = "SecureStorageFailure(code=$code, safeDiagnosticReason=$safeDiagnosticReason)"
}

sealed interface SecureStorageResult<out T> {
    data class Success<T>(val value: T) : SecureStorageResult<T>
    data class Failure(val failure: SecureStorageFailure) : SecureStorageResult<Nothing>
}

fun <T> SecureStorageResult<T>.getOrNull(): T? = (this as? SecureStorageResult.Success<T>)?.value

data class SecureMaterialMetadata(
    val version: SecureMaterialVersion,
    val revision: SecureMaterialRevision,
    val lastCommittedAtIso8601: String,
)

data class VersionedMaterial(val bytes: ByteArray, val metadata: SecureMaterialMetadata) {
    override fun toString(): String = "VersionedMaterial(bytes=<redacted:${bytes.size}b>, metadata=$metadata)"
}

data class SecureStorageCapability(
    val available: Boolean,
    val hardwareBacked: Boolean,
    val requiresDeviceUnlock: Boolean,
)

data class SecureMaterialSnapshot(val scope: SecureMaterialScope, val revision: SecureMaterialRevision, val committedAtIso8601: String)

sealed interface SecureStorageRecoveryOutcome {
    data object NoRecoveryNeeded : SecureStorageRecoveryOutcome
    data class RecoveredToLastGoodGeneration(val snapshot: SecureMaterialSnapshot) : SecureStorageRecoveryOutcome
    data object UnrecoverableReactivationRequired : SecureStorageRecoveryOutcome
}

data class SecureStorageHealth(
    val adapterAvailable: Boolean,
    val keyAvailable: Boolean,
    val currentStorageVersion: Int?,
    val activationBundleExists: Boolean,
    val migrationRequired: Boolean,
    val recoveryRequired: Boolean,
    val keyInvalidated: Boolean,
    val lastSuccessfulCommitAtIso8601: String?,
    val safeErrorCode: SecureStorageFailureCode?,
)
