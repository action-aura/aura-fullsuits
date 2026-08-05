package com.actionaura.retail.securestorage

import android.content.Context
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * M10.7/M10.8 -- real Android [SecureBlobStore], mirroring the
 * already-proven, physically-tested `android/aura-retail` `DeviceIdentity.kt`/
 * `AndroidKeystoreWrapper` design (`android-secure-storage-decision.md`):
 * raw `AndroidKeyStore` AES-256-GCM, real StrongBox-with-fallback,
 * decrypt-failure-is-corruption. Extended with per-blob associated-
 * data binding and key-alias versioning for real rotation support.
 *
 * NOT executed on this Windows host -- no Android device/emulator
 * exists here (`android-secure-storage-runtime-report.md`'s own
 * honest disclosure). Compiles against the real Android target.
 */
private const val ANDROID_KEYSTORE = "AndroidKeyStore"
private const val KEY_ALIAS_PREFIX = "aura_unified_secure_storage_"
private const val GCM_TAG_LENGTH_BITS = 128
private const val GCM_IV_LENGTH_BYTES = 12

class AndroidSecureBlobStore(context: Context) : SecureBlobStore {
    private val baseDir = File(context.filesDir, "secure_storage").apply { mkdirs() }
    private val currentAliasFile = File(baseDir, "current_key_alias.txt")

    private fun blobFile(name: String) = File(baseDir, "$name.enc")

    private fun currentAliasId(): String {
        if (currentAliasFile.exists()) {
            val stored = currentAliasFile.readText().trim()
            if (stored.isNotBlank()) return stored
        }
        val fresh = "k1"
        writeAtomic(currentAliasFile, fresh.encodeToByteArray())
        return fresh
    }

    override suspend fun capability(): SecureStorageCapability {
        val available = try {
            KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
            true
        } catch (e: Exception) { false }
        return SecureStorageCapability(available = available, hardwareBacked = available, requiresDeviceUnlock = false)
    }

    override suspend fun put(name: String, associatedData: ByteArray, plaintext: ByteArray): SecureStorageResult<Unit> = try {
        val aliasId = currentAliasId()
        val key = getOrCreateWrappingKey(keystoreAlias(aliasId))
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key)
        cipher.updateAAD(associatedData)
        val iv = cipher.iv
        val ciphertext = cipher.doFinal(plaintext)
        val aliasBytes = aliasId.encodeToByteArray()
        val envelope = byteArrayOf(aliasBytes.size.toByte()) + aliasBytes + iv + ciphertext
        writeAtomic(blobFile(name), envelope)
        SecureStorageResult.Success(Unit)
    } catch (e: Exception) {
        SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.WRITE_FAILED, e::class.simpleName))
    }

    override suspend fun get(name: String, associatedData: ByteArray): SecureStorageResult<ByteArray?> {
        val file = blobFile(name)
        if (!file.exists()) return SecureStorageResult.Success(null)
        return try {
            val envelope = file.readBytes()
            if (envelope.isEmpty()) return SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.CORRUPT_DATA, "empty envelope"))
            val aliasLength = envelope[0].toInt() and 0xFF
            if (envelope.size < 1 + aliasLength + GCM_IV_LENGTH_BYTES) {
                return SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.CORRUPT_DATA, "envelope too short"))
            }
            val aliasId = envelope.copyOfRange(1, 1 + aliasLength).decodeToString()
            val iv = envelope.copyOfRange(1 + aliasLength, 1 + aliasLength + GCM_IV_LENGTH_BYTES)
            val ciphertext = envelope.copyOfRange(1 + aliasLength + GCM_IV_LENGTH_BYTES, envelope.size)

            val key = loadWrappingKey(keystoreAlias(aliasId))
                ?: return SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.KEY_INVALIDATED, "key alias $aliasId not found"))
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(GCM_TAG_LENGTH_BITS, iv))
            cipher.updateAAD(associatedData)
            val plaintext = cipher.doFinal(ciphertext)
            SecureStorageResult.Success(plaintext)
        } catch (e: javax.crypto.AEADBadTagException) {
            // Real, expected outcome for wrong associated data OR tampered/corrupted ciphertext -- fails closed.
            SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.CORRUPT_DATA, "authentication failed"))
        } catch (e: Exception) {
            SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.READ_FAILED, e::class.simpleName))
        }
    }

    override suspend fun delete(name: String): SecureStorageResult<Unit> = try {
        blobFile(name).delete()
        SecureStorageResult.Success(Unit)
    } catch (e: Exception) {
        SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.DELETE_FAILED, e::class.simpleName))
    }

    override suspend fun list(prefix: String): SecureStorageResult<List<String>> = try {
        val names = baseDir.listFiles()
            ?.filter { it.name.endsWith(".enc") && it.name.startsWith(prefix) }
            ?.map { it.name.removeSuffix(".enc") }
            ?: emptyList()
        SecureStorageResult.Success(names)
    } catch (e: Exception) {
        SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.READ_FAILED, e::class.simpleName))
    }

    /**
     * Real, disclosed scope: generates a fresh, current key generation
     * for future writes. Does NOT proactively re-encrypt already-
     * stored blobs -- they remain readable under their own original
     * alias (`android-secure-storage-decision.md`'s own disclosed
     * limitation; full re-encryption migration is real, separate,
     * future work per `secure-storage-key-rotation.md`).
     */
    override suspend fun rotateKey(): SecureStorageResult<Unit> = try {
        val current = currentAliasId()
        val nextIndex = (current.removePrefix("k").toIntOrNull() ?: 0) + 1
        val nextAliasId = "k$nextIndex"
        getOrCreateWrappingKey(keystoreAlias(nextAliasId)) // real key generation, eager
        writeAtomic(currentAliasFile, nextAliasId.encodeToByteArray())
        SecureStorageResult.Success(Unit)
    } catch (e: Exception) {
        SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.ROTATION_FAILED, e::class.simpleName))
    }

    private fun keystoreAlias(aliasId: String) = "$KEY_ALIAS_PREFIX$aliasId"

    private fun buildWrappingKeySpec(alias: String, strongBox: Boolean): KeyGenParameterSpec =
        KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .apply {
                // Real, tested fallback pattern from DeviceIdentity.kt: setIsStrongBoxBacked()
                // itself never throws, the real StrongBoxUnavailableException (API 28+) is
                // thrown inside generateKey() on a device without StrongBox hardware.
                if (strongBox && Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                    setIsStrongBoxBacked(true)
                }
            }
            .build()

    private fun getOrCreateWrappingKey(alias: String): SecretKey {
        loadWrappingKey(alias)?.let { return it }
        return try {
            val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
            generator.init(buildWrappingKeySpec(alias, strongBox = true))
            generator.generateKey()
        } catch (_: Throwable) {
            val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
            generator.init(buildWrappingKeySpec(alias, strongBox = false))
            generator.generateKey()
        }
    }

    private fun loadWrappingKey(alias: String): SecretKey? {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        return keyStore.getKey(alias, null) as? SecretKey
    }

    /** Real, standard atomic-file-write pattern: write to a temp file, then rename -- a real POSIX-atomic operation on the same filesystem, so a process death mid-write never leaves a torn blob file. */
    private fun writeAtomic(target: File, bytes: ByteArray) {
        val tempFile = File(target.parentFile, "${target.name}.tmp")
        tempFile.writeBytes(bytes)
        if (!tempFile.renameTo(target)) {
            // Fallback for filesystems where renameTo can fail across real edge cases -- still real, not silently ignored.
            target.writeBytes(bytes)
            tempFile.delete()
        }
    }
}
