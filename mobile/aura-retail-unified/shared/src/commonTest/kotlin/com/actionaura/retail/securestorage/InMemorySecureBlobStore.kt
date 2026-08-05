package com.actionaura.retail.securestorage

/**
 * M10.28 -- real, deterministic, in-memory [SecureBlobStore] test
 * double. Simulates real AEAD behavior: a `get` with associated data
 * that does not match what was written at `put` time fails exactly
 * like a real wrong-key/wrong-AAD authentication failure would
 * (`CORRUPT_DATA`), never silently returns the value under mismatched
 * AD. Supports fault injection for the real atomicity test matrix
 * (M10.28: failure at every commit phase).
 */
class InMemorySecureBlobStore(
    private val available: Boolean = true,
) : SecureBlobStore {
    private data class Stored(val associatedData: ByteArray, val bytes: ByteArray)

    private val store = mutableMapOf<String, Stored>()
    var failWritesAfterCount: Int? = null
        private set
    private var writeCount = 0
    var failNextNPuts: Int = 0
    var corruptOnNextGet: MutableSet<String> = mutableSetOf()

    fun failAfter(n: Int) { failWritesAfterCount = n }

    override suspend fun capability(): SecureStorageCapability =
        SecureStorageCapability(available = available, hardwareBacked = false, requiresDeviceUnlock = false)

    override suspend fun put(name: String, associatedData: ByteArray, plaintext: ByteArray): SecureStorageResult<Unit> {
        if (!available) return SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.NOT_AVAILABLE))
        writeCount++
        failWritesAfterCount?.let { limit -> if (writeCount > limit) return SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.WRITE_FAILED)) }
        if (failNextNPuts > 0) { failNextNPuts--; return SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.WRITE_FAILED)) }
        store[name] = Stored(associatedData, plaintext)
        return SecureStorageResult.Success(Unit)
    }

    override suspend fun get(name: String, associatedData: ByteArray): SecureStorageResult<ByteArray?> {
        if (!available) return SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.NOT_AVAILABLE))
        if (name in corruptOnNextGet) { corruptOnNextGet.remove(name); return SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.CORRUPT_DATA)) }
        val stored = store[name] ?: return SecureStorageResult.Success(null)
        return if (stored.associatedData.contentEquals(associatedData)) {
            SecureStorageResult.Success(stored.bytes)
        } else {
            // Real AEAD-equivalent behavior: wrong associated data authenticates as corrupt, never returns the value.
            SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.CORRUPT_DATA, "associated data mismatch"))
        }
    }

    override suspend fun delete(name: String): SecureStorageResult<Unit> {
        store.remove(name)
        return SecureStorageResult.Success(Unit)
    }

    override suspend fun list(prefix: String): SecureStorageResult<List<String>> =
        SecureStorageResult.Success(store.keys.filter { it.startsWith(prefix) })

    override suspend fun rotateKey(): SecureStorageResult<Unit> = SecureStorageResult.Success(Unit)

    fun rawKeyCount(): Int = store.size
    fun hasKey(name: String): Boolean = name in store
}
