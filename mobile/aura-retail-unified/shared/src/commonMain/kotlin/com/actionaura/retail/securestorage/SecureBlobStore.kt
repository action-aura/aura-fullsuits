package com.actionaura.retail.securestorage

/**
 * M10.7/M10.9 -- the one narrow platform primitive real Android/iOS
 * `actual` code must implement. Deliberately minimal: encrypted named-
 * blob get/put/delete/list. All atomicity, generation management, and
 * recovery logic lives once, in pure commonMain code
 * (`GenerationalSecureMaterialStore`), never duplicated per platform.
 *
 * Real, binding requirement for every real implementation: `put` must
 * bind `associatedData` (Product/Platform/material-type/scope/schema-
 * version, encoded by the caller) to the ciphertext via authenticated
 * encryption (AES-GCM's own AAD parameter) -- replaying one encrypted
 * blob into another logical name/scope must fail authentication, never
 * silently decrypt (`android-secure-storage-decision.md`,
 * `ios-keychain-storage-decision.md`).
 */
interface SecureBlobStore {
    suspend fun capability(): SecureStorageCapability
    suspend fun put(name: String, associatedData: ByteArray, plaintext: ByteArray): SecureStorageResult<Unit>
    suspend fun get(name: String, associatedData: ByteArray): SecureStorageResult<ByteArray?>
    suspend fun delete(name: String): SecureStorageResult<Unit>
    suspend fun list(prefix: String): SecureStorageResult<List<String>>
    suspend fun rotateKey(): SecureStorageResult<Unit>
}

/** Real, deterministic, non-secret associated-data encoding -- binds a blob's ciphertext to exactly the logical identity it was written for. */
fun secureAssociatedData(key: SecureMaterialKey, version: SecureMaterialVersion): ByteArray =
    "${key.stableName()}|v${version.storageFormatVersion}.${version.materialTypeVersion}".encodeToByteArray()
