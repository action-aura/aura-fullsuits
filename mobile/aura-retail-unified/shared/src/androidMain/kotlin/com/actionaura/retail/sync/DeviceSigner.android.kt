package com.actionaura.retail.sync

import com.actionaura.retail.securestorage.SecureBlobStore
import com.actionaura.retail.securestorage.SecureStorageResult
import com.google.crypto.tink.subtle.Ed25519Sign
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Task 7 -- real Android device-signing implementation, using Tink's raw
 * "subtle" `Ed25519Sign` primitive: the exact signing counterpart of
 * `Ed25519Verify`, which `SignedLeaseSignatureVerifier.android.kt` already
 * uses in production against the same `com.google.crypto.tink:tink-android:1.15.0`
 * dependency (`shared/build.gradle.kts`) -- no new crypto library added.
 * `Ed25519Sign(byte[] privateKey)` (constructor, public) and
 * `Ed25519Verify(byte[] publicKey)` (constructor, public) are the exact
 * mirrored real APIs this codebase already exercises end-to-end in
 * `LeaseTestFixtures.sign()`.
 *
 * `Ed25519Sign.KeyPair` (`Ed25519Sign.KeyPair.newKeyPair()`,
 * `.publicKey`/`.privateKey`, both raw 32-byte arrays -- confirmed against
 * the real class file in this project's own Gradle cache via `javap`,
 * `tink-android-1.15.0.jar!/com/google/crypto/tink/subtle/Ed25519Sign$KeyPair.class`,
 * since this codebase's own source never constructs one from raw bytes) has
 * only a *private* constructor -- `newKeyPair()`/`newKeyPairFromSeed(seed)`
 * are the only public factories. So persistence never round-trips a
 * `KeyPair` object: only the two raw 32-byte arrays it exposes
 * (`publicKey`/`privateKey`) are stored, concatenated as one blob; on
 * reload, signing needs nothing but the raw private-key bytes to build
 * `Ed25519Sign(privateKey)` directly (its public byte-array constructor),
 * and `publicKeyBytes()` returns the raw public-key bytes as-is -- neither
 * path ever needs a reconstructed `KeyPair`.
 *
 * Persistence deliberately goes through the narrow [SecureBlobStore]
 * primitive, NOT `GenerationalSecureMaterialStore`. Reading
 * `GenerationalSecureMaterialStore`'s real contract
 * (`commitActivationBundle`/`loadActivationBundle`, `AuraAppContainer.kt`)
 * shows it is not a generic keyed blob store: every real operation commits
 * or loads one whole product/installation/account-scoped "activation
 * bundle" (a fixed, closed set of `SecureMaterialType` components --
 * credentials/lease/installation identity) through its own
 * generation/pointer atomic-commit protocol. It has no operation for
 * storing one arbitrary named blob outside that bundle lifecycle, and a
 * device signing key is not customer-activation state in the first place --
 * it must survive activation-bundle rotation, deactivation, and
 * re-activation with a *different* account, none of which should ever
 * delete or regenerate it. Rather than bend that class's contract to a
 * meaning it was never designed for (or extend it for one caller), this
 * reuses the same real, platform-backed (Android Keystore-derived AEAD)
 * [SecureBlobStore] primitive `GenerationalSecureMaterialStore` itself is
 * built on -- already an `AuraAppContainer` constructor parameter, so no
 * existing contract is touched -- under one blob name in a distinct
 * namespace (`device_signing:*`) that can never collide with
 * `GenerationalSecureMaterialStore`'s own `"gen:*"`/`"pointer:*"` names.
 */
actual class PlatformDeviceSigner actual constructor(
    private val secureBlobStore: SecureBlobStore,
) : DeviceSigner {

    /** Raw Ed25519 key material only -- never a `KeyPair` (see class KDoc); [toString] never prints [privateKey]. */
    private class RawKeyPair(val privateKey: ByteArray, val publicKey: ByteArray) {
        override fun toString(): String = "RawKeyPair(privateKey=<redacted>, publicKey=<redacted>)"
    }

    private val mutex = Mutex()

    @Volatile
    private var cached: RawKeyPair? = null

    override suspend fun publicKeyBytes(): ByteArray = loadOrCreateKeyPair().publicKey.copyOf()

    override suspend fun sign(message: ByteArray): ByteArray {
        val keyPair = loadOrCreateKeyPair()
        return Ed25519Sign(keyPair.privateKey).sign(message)
    }

    /**
     * Real load-or-generate-once semantics: an existing persisted keypair is
     * always preferred over generating a new one -- generation only ever
     * happens the first time this device has never stored a keypair before.
     * Mutex-guarded so two concurrent first-use callers can never race and
     * generate two different keypairs (only one would win the persisted
     * write, but the loser must not hand a caller the *other*, non-persisted
     * keypair).
     */
    private suspend fun loadOrCreateKeyPair(): RawKeyPair = mutex.withLock {
        cached?.let { return@withLock it }

        val existing = secureBlobStore.get(BLOB_NAME, ASSOCIATED_DATA)
        if (existing is SecureStorageResult.Success && existing.value != null) {
            val loaded = decode(existing.value)
            cached = loaded
            return@withLock loaded
        }

        val generated = Ed25519Sign.KeyPair.newKeyPair()
        val raw = RawKeyPair(privateKey = generated.privateKey, publicKey = generated.publicKey)
        val writeResult = secureBlobStore.put(BLOB_NAME, ASSOCIATED_DATA, encode(raw))
        check(writeResult is SecureStorageResult.Success) {
            "failed to persist newly generated device signing keypair -- refusing to hand out an unpersisted key that Owner-side verification could never agree on again"
        }
        cached = raw
        raw
    }

    private fun encode(keyPair: RawKeyPair): ByteArray = keyPair.privateKey + keyPair.publicKey

    private fun decode(bytes: ByteArray): RawKeyPair {
        check(bytes.size == Ed25519Sign.SECRET_KEY_LEN + PUBLIC_KEY_LEN) {
            "corrupt device signing keypair blob: expected ${Ed25519Sign.SECRET_KEY_LEN + PUBLIC_KEY_LEN} bytes, got ${bytes.size}"
        }
        val privateKey = bytes.copyOfRange(0, Ed25519Sign.SECRET_KEY_LEN)
        val publicKey = bytes.copyOfRange(Ed25519Sign.SECRET_KEY_LEN, Ed25519Sign.SECRET_KEY_LEN + PUBLIC_KEY_LEN)
        return RawKeyPair(privateKey, publicKey)
    }

    private companion object {
        // Distinct namespace prefix -- never collides with GenerationalSecureMaterialStore's own "gen:"/"pointer:" blob names on the same underlying SecureBlobStore.
        const val BLOB_NAME = "device_signing:ed25519_keypair_v1"
        val ASSOCIATED_DATA = "device_signing|ed25519|v1".encodeToByteArray()

        // Ed25519Verify.PUBLIC_KEY_LEN (SignedLeaseSignatureVerifier.android.kt's own 32-byte check) -- Ed25519Sign has no equivalent public-key-length constant of its own.
        const val PUBLIC_KEY_LEN = 32
    }
}
