package com.actionaura.retail.licensing

import android.content.Context
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import com.google.crypto.tink.subtle.Ed25519Sign
import com.google.crypto.tink.subtle.Ed25519Verify
import java.io.File
import java.security.KeyStore
import java.security.MessageDigest
import java.time.Instant
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Kotlin reference implementation of the device Ed25519 keypair (Phase 7
 * Part F). See docs/licensing/phase7/android-device-key-storage-design.md
 * (ADR-7.1) for the full rationale -- summary: Ed25519 key generation/
 * signing uses Tink's raw "subtle" primitives (com.google.crypto.tink.subtle
 * .Ed25519Sign/Ed25519Verify), which are RFC 8032-compliant and produce raw
 * 32-byte public keys / raw 64-byte signatures -- bit-compatible with what
 * Owner's Python `cryptography` library signs and verifies, with no Tink
 * keyset/prefix framing involved. Cross-language interop verified directly
 * (Ed25519CrossVerifyTest): a signature this stack produces was independently
 * verified by Python's `cryptography` library, and vice versa. This
 * deliberately avoids AndroidKeyStore's own native Ed25519 support (API 33+,
 * inconsistent below/around that line; this app's minSdk is 26).
 *
 * The generated Ed25519 private key bytes are the plaintext input to an
 * AES-256-GCM key wrapped via [KeyWrapper] -- production code always uses
 * [AndroidKeystoreWrapper] (non-exportable, hardware-backed where
 * available). The wrapping step is behind an interface, not hardcoded,
 * specifically so DeviceIdentityTest can inject a fake wrapper and verify
 * this class's own logic (generate/sign/corruption-detection/reset) on the
 * JVM: Robolectric was tried first and found to not provide a working
 * "AndroidKeyStore" JCA provider out of the box (every KeyStore/KeyGenerator
 * call threw NoSuchAlgorithmException) -- a real, documented Robolectric
 * limitation, not a bug in this class. [AndroidKeystoreWrapper] itself is
 * only exercised on a real device/emulator (Part AA); that remains a real
 * requirement this test file does not replace.
 *
 * Mirrors the Python WindowsDpapiDeviceIdentityProvider's shape
 * (commercial_runtime/licensing_contracts/device_identity.py) so both
 * platforms implement the same Part C DeviceIdentityProvider concept, even
 * though this is a separate Kotlin implementation, not shared code (see
 * docs/licensing/phase7/product-integration-architecture.md).
 */

class DeviceIdentityError(message: String, cause: Throwable? = null) : Exception(message, cause)
class LocalStateCorruptError(message: String, cause: Throwable? = null) : Exception(message, cause)

data class DeviceKeyMetadata(
    val algorithm: String,
    val publicKeyFingerprint: String,
    val createdAt: String,
    val status: String, // "ACTIVE" | "RESET_PENDING" | "REVOKED_LOCALLY"
    val ownerInstallationId: String?,
)

fun fingerprintOf(rawPublicKey: ByteArray): String {
    // SHA-256 hex digest of the raw 32-byte Ed25519 public key -- identical
    // derivation to Owner's device_identity.py::fingerprint_of() and the
    // Windows provider's fingerprint_of(), so a fingerprint computed on any
    // platform matches byte-for-byte for the same key.
    val digest = MessageDigest.getInstance("SHA-256").digest(rawPublicKey)
    return digest.joinToString("") { "%02x".format(it) }
}

/** Wraps/unwraps arbitrary bytes with an authenticated cipher. The only
 * thing DeviceIdentity depends on -- it does not know or care whether the
 * underlying key is AndroidKeystore-backed or not. */
interface KeyWrapper {
    fun wrap(plaintext: ByteArray): ByteArray
    fun unwrap(stored: ByteArray): ByteArray
}

private const val ANDROID_KEYSTORE = "AndroidKeyStore"
private const val WRAPPING_KEY_ALIAS = "aura_licensing_device_key_wrap"
private const val GCM_TAG_LENGTH_BITS = 128
private const val GCM_IV_LENGTH_BYTES = 12

/** Production implementation -- AndroidKeystore-backed AES-256-GCM,
 * non-exportable, hardware-backed (StrongBox where available, standard TEE
 * otherwise). AES-GCM symmetric key wrapping has been reliable on
 * AndroidKeystore since API 23, unlike Ed25519 signing keys (API 33+). */
class AndroidKeystoreWrapper : KeyWrapper {

    override fun wrap(plaintext: ByteArray): ByteArray {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, getOrCreateWrappingKey())
        val iv = cipher.iv
        val ciphertext = cipher.doFinal(plaintext)
        // Store IV || ciphertext (ciphertext already includes the GCM auth
        // tag appended by the JCE provider) -- self-contained, no separate
        // integrity field needed; a tampered/corrupted file fails to
        // decrypt, which is exactly the LocalStateCorrupt signal we want.
        return iv + ciphertext
    }

    override fun unwrap(stored: ByteArray): ByteArray {
        if (stored.size <= GCM_IV_LENGTH_BYTES) {
            throw LocalStateCorruptError("Stored blob is too short to contain a valid IV + ciphertext.")
        }
        val iv = stored.copyOfRange(0, GCM_IV_LENGTH_BYTES)
        val ciphertext = stored.copyOfRange(GCM_IV_LENGTH_BYTES, stored.size)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, getOrCreateWrappingKey(), GCMParameterSpec(GCM_TAG_LENGTH_BITS, iv))
        return try {
            cipher.doFinal(ciphertext)
        } catch (exc: Exception) {
            throw LocalStateCorruptError("Blob could not be decrypted (tampered or corrupted).", exc)
        }
    }

    private fun getOrCreateWrappingKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getKey(WRAPPING_KEY_ALIAS, null) as? SecretKey)?.let { return it }

        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        val spec = KeyGenParameterSpec.Builder(
            WRAPPING_KEY_ALIAS,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
        )
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .apply {
                // setIsStrongBoxBacked requires API 28; this app's minSdk is
                // 26, so a version check (not just try/catch) is required --
                // lint's NewApi check does not treat try/catch as a valid
                // guard for a call that isn't resolvable on older API levels.
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                    try {
                        setIsStrongBoxBacked(true)
                    } catch (_: Throwable) {
                        // Declared API 28+ but not all real devices expose
                        // StrongBox hardware -- falls back to the standard
                        // TEE-backed path if unavailable.
                    }
                }
            }
            .build()
        generator.init(spec)
        return generator.generateKey()
    }
}

class DeviceIdentity(
    baseDir: File,
    private val keyWrapper: KeyWrapper = AndroidKeystoreWrapper(),
) {

    /** Convenience constructor for real production callers -- avoids every
     * call site needing to know to pass context.filesDir specifically.
     * Decoupling the primary constructor from Context (rather than storing
     * it and reading .filesDir lazily) means tests need only a plain JUnit
     * TemporaryFolder, no Robolectric/Context simulation at all. */
    constructor(context: Context, keyWrapper: KeyWrapper = AndroidKeystoreWrapper()) : this(context.filesDir, keyWrapper)

    private val licensingDir: File = File(baseDir, "licensing").apply { mkdirs() }
    private val keyFile: File get() = File(licensingDir, "device_key.enc")
    private val metaFile: File get() = File(licensingDir, "device_key_meta.json")
    private val publicKeyFile: File get() = File(licensingDir, "device_public_key.txt")

    fun hasKey(): Boolean = keyFile.exists() && metaFile.exists()

    fun generateNewKey(): DeviceKeyMetadata {
        if (keyFile.exists()) {
            throw DeviceIdentityError("Device key already exists -- refusing to silently overwrite it.")
        }
        val keyPair = Ed25519Sign.KeyPair.newKeyPair()
        val wrapped = keyWrapper.wrap(keyPair.privateKey)
        keyFile.writeBytes(wrapped)
        // The public key is, by definition, safe to store in plaintext --
        // written here so getPublicKeyB64() never needs to touch (let alone
        // decrypt) the private key just to answer "what is our public key."
        publicKeyFile.writeText(java.util.Base64.getEncoder().encodeToString(keyPair.publicKey))

        val meta = DeviceKeyMetadata(
            algorithm = "ed25519",
            publicKeyFingerprint = fingerprintOf(keyPair.publicKey),
            createdAt = Instant.now().toString(),
            status = "ACTIVE",
            ownerInstallationId = null,
        )
        writeMeta(meta)
        return meta
    }

    fun getPublicKeyB64(): String = loadStoredPublicKeyB64()

    fun getMetadata(): DeviceKeyMetadata = readMeta()

    fun sign(canonicalBytes: ByteArray): String {
        val rawPrivate = loadRawPrivateKey()
        val signature = Ed25519Sign(rawPrivate).sign(canonicalBytes)
        return java.util.Base64.getEncoder().encodeToString(signature)
    }

    fun markResetPending() {
        val meta = readMeta()
        writeMeta(meta.copy(status = "RESET_PENDING"))
    }

    fun destroyKey() {
        // Retained (not destroyed) after plain deactivation -- only called
        // after Owner confirms a device-replacement request succeeded, same
        // policy as the Windows provider.
        keyFile.delete()
        metaFile.delete()
        publicKeyFile.delete()
    }

    private fun loadRawPrivateKey(): ByteArray {
        if (!keyFile.exists()) {
            throw DeviceIdentityError("No device key exists -- caller must route to ACTIVATION_REQUIRED.")
        }
        val raw = keyWrapper.unwrap(keyFile.readBytes())
        if (raw.size != 32) {
            throw LocalStateCorruptError("Decrypted device key has unexpected length ${raw.size} (expected 32).")
        }
        return raw
    }

    private fun loadStoredPublicKeyB64(): String {
        if (!publicKeyFile.exists()) {
            throw LocalStateCorruptError("Public key file is missing.")
        }
        return publicKeyFile.readText()
    }

    private fun writeMeta(meta: DeviceKeyMetadata) {
        metaFile.writeText(metaGson.toJson(meta))
    }

    private fun readMeta(): DeviceKeyMetadata {
        if (!metaFile.exists()) {
            throw LocalStateCorruptError("Device key metadata file is missing.")
        }
        return try {
            metaGson.fromJson(metaFile.readText(), DeviceKeyMetadata::class.java)
                ?: throw LocalStateCorruptError("Device key metadata file is empty.")
        } catch (exc: Exception) {
            if (exc is LocalStateCorruptError) throw exc
            throw LocalStateCorruptError("Device key metadata is unreadable: ${exc.message}", exc)
        }
    }
}

// org.json.JSONObject is an Android-platform-stubbed class (its methods
// throw "not mocked" under plain JUnit, same class of issue as
// android.util.Base64 -- see the class doc comment) -- Gson is a genuine
// pure-JVM library (already pulled in transitively via
// com.squareup.retrofit2:converter-gson, declared directly here too since
// this file imports it directly) with no such stub, works identically on a
// real device and in DeviceIdentityTest.
private val metaGson = com.google.gson.Gson()

fun verifySignature(rawPublicKey: ByteArray, canonicalBytes: ByteArray, signature: ByteArray): Boolean {
    return try {
        Ed25519Verify(rawPublicKey).verify(signature, canonicalBytes)
        true
    } catch (_: Exception) {
        false
    }
}
