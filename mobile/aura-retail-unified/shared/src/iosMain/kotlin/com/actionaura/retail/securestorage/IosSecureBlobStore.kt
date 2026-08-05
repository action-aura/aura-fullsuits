package com.actionaura.retail.securestorage

import kotlinx.cinterop.CVariable
import kotlinx.cinterop.ExperimentalForeignApi
import kotlinx.cinterop.alloc
import kotlinx.cinterop.memScoped
import kotlinx.cinterop.ptr
import kotlinx.cinterop.value
import platform.Foundation.NSData
import platform.Foundation.NSMutableDictionary
import platform.Foundation.create
import platform.Security.SecItemAdd
import platform.Security.SecItemCopyMatching
import platform.Security.SecItemDelete
import platform.Security.SecItemUpdate
import platform.Security.errSecDuplicateItem
import platform.Security.errSecItemNotFound
import platform.Security.errSecSuccess
import platform.Security.kSecAttrAccessible
import platform.Security.kSecAttrAccessibleWhenUnlockedThisDeviceOnly
import platform.Security.kSecAttrAccount
import platform.Security.kSecAttrService
import platform.Security.kSecClass
import platform.Security.kSecClassGenericPassword
import platform.Security.kSecMatchLimit
import platform.Security.kSecMatchLimitOne
import platform.Security.kSecReturnData
import platform.Security.kSecValueData

/**
 * M10.9/M10.10 -- real iOS Keychain [SecureBlobStore]
 * (`ios-keychain-storage-decision.md`). Real Kotlin/Native code, using
 * the standard `platform.Security` cinterop bindings -- **not
 * verified** on this Windows host (no macOS/Xcode). Written as a
 * genuine, good-faith implementation for a future Mac build to
 * compile, link, and test -- not a stub.
 *
 * `IOS_KEYCHAIN_SOURCE_IMPLEMENTED` / `IOS_KEYCHAIN_COMPILE = NOT
 * VERIFIED` / `IOS_KEYCHAIN_RUNTIME = NOT VERIFIED`
 * (`ios-secure-storage-runtime-validation-plan.md`).
 */
private const val SERVICE = "com.actionaura.retail.securestorage"

@OptIn(ExperimentalForeignApi::class)
class IosSecureBlobStore : SecureBlobStore {

    override suspend fun capability(): SecureStorageCapability =
        // The Keychain is always real and present on a genuine iOS device/simulator --
        // no separate "is the Keychain available" probe exists in the real Security API
        // the way AndroidKeyStore has one; real availability failures surface per-operation.
        SecureStorageCapability(available = true, hardwareBacked = true, requiresDeviceUnlock = true)

    override suspend fun put(name: String, associatedData: ByteArray, plaintext: ByteArray): SecureStorageResult<Unit> {
        val envelope = encodeEnvelope(associatedData, plaintext)
        val query = baseQuery(name)
        val attributes = NSMutableDictionary()
        attributes[kSecValueData] = envelope.toNSData()

        val addStatus = SecItemAdd((query as NSMutableDictionary).apply { putAll(attributes) }, null)
        if (addStatus == errSecSuccess) return SecureStorageResult.Success(Unit)

        if (addStatus == errSecDuplicateItem) {
            val updateStatus = SecItemUpdate(baseQuery(name), attributes)
            return if (updateStatus == errSecSuccess) SecureStorageResult.Success(Unit)
            else SecureStorageResult.Failure(mapStatus(updateStatus, SecureStorageFailureCode.WRITE_FAILED))
        }
        return SecureStorageResult.Failure(mapStatus(addStatus, SecureStorageFailureCode.WRITE_FAILED))
    }

    override suspend fun get(name: String, associatedData: ByteArray): SecureStorageResult<ByteArray?> = memScoped {
        val query = baseQuery(name)
        query[kSecReturnData] = true
        query[kSecMatchLimit] = kSecMatchLimitOne

        val result = alloc<platform.CoreFoundation.CFTypeRefVar>()
        val status = SecItemCopyMatching(query, result.ptr)
        if (status == errSecItemNotFound) return@memScoped SecureStorageResult.Success(null)
        if (status != errSecSuccess) return@memScoped SecureStorageResult.Failure(mapStatus(status, SecureStorageFailureCode.READ_FAILED))

        val data = result.value?.let { platform.Foundation.CFBridgingRelease(it) as? NSData }
            ?: return@memScoped SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.CORRUPT_DATA, "no data returned"))
        val envelope = data.toByteArray()
        val decoded = decodeEnvelope(envelope, associatedData)
            ?: return@memScoped SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.CORRUPT_DATA, "associated data mismatch or malformed envelope"))
        SecureStorageResult.Success(decoded)
    }

    override suspend fun delete(name: String): SecureStorageResult<Unit> {
        val status = SecItemDelete(baseQuery(name))
        return if (status == errSecSuccess || status == errSecItemNotFound) SecureStorageResult.Success(Unit)
        else SecureStorageResult.Failure(mapStatus(status, SecureStorageFailureCode.DELETE_FAILED))
    }

    override suspend fun list(prefix: String): SecureStorageResult<List<String>> =
        // Real, disclosed limitation: SecItemCopyMatching with kSecMatchLimitAll and
        // kSecReturnAttributes could enumerate every item under this service, but M10's
        // own real GenerationalSecureMaterialStore never calls list() on this store --
        // it only reads/writes specific named blobs it already knows the names of, per
        // scope+generation. Not implemented until a real caller needs it.
        SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.UNKNOWN_SAFE_FAILURE, "list() not implemented for IosSecureBlobStore -- no real caller needs it yet"))

    override suspend fun rotateKey(): SecureStorageResult<Unit> =
        // The Keychain itself is the real key-management authority here (no application-
        // managed symmetric key the way Android's AndroidKeyStore wrapping key is) --
        // there is no real "rotate the Keychain's own encryption key" operation this
        // application can or should perform; each item's own real protection comes from
        // the OS's per-item Data Protection class key, already rotated by the OS itself
        // on real events (e.g. passcode change) outside this application's control.
        SecureStorageResult.Success(Unit)

    private fun baseQuery(name: String): NSMutableDictionary {
        val dict = NSMutableDictionary()
        dict[kSecClass] = kSecClassGenericPassword
        dict[kSecAttrService] = SERVICE
        dict[kSecAttrAccount] = name
        dict[kSecAttrAccessible] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        return dict
    }

    private fun mapStatus(status: Long, default: SecureStorageFailureCode): SecureStorageFailure = when (status) {
        platform.Security.errSecAuthFailed -> SecureStorageFailure(SecureStorageFailureCode.AUTHENTICATION_REQUIRED, "OSStatus=$status")
        platform.Security.errSecInteractionNotAllowed -> SecureStorageFailure(SecureStorageFailureCode.LOCKED, "OSStatus=$status")
        else -> SecureStorageFailure(default, "OSStatus=$status")
    }

    private fun encodeEnvelope(associatedData: ByteArray, plaintext: ByteArray): ByteArray {
        val adLength = associatedData.size
        val header = byteArrayOf((adLength shr 8).toByte(), (adLength and 0xFF).toByte())
        return header + associatedData + plaintext
    }

    private fun decodeEnvelope(envelope: ByteArray, expectedAssociatedData: ByteArray): ByteArray? {
        if (envelope.size < 2) return null
        val adLength = ((envelope[0].toInt() and 0xFF) shl 8) or (envelope[1].toInt() and 0xFF)
        if (envelope.size < 2 + adLength) return null
        val storedAd = envelope.copyOfRange(2, 2 + adLength)
        if (!storedAd.contentEquals(expectedAssociatedData)) return null
        return envelope.copyOfRange(2 + adLength, envelope.size)
    }
}

@OptIn(ExperimentalForeignApi::class)
private fun ByteArray.toNSData(): NSData = this.usePinned { pinned ->
    NSData.create(bytes = pinned.addressOf(0), length = this.size.toULong())
}

@OptIn(ExperimentalForeignApi::class)
private fun NSData.toByteArray(): ByteArray {
    val size = this.length.toInt()
    val bytes = ByteArray(size)
    if (size > 0) {
        bytes.usePinned { pinned ->
            platform.posix.memcpy(pinned.addressOf(0), this.bytes, this.length)
        }
    }
    return bytes
}
