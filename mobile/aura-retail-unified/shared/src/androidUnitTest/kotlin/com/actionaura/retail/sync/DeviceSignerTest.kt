package com.actionaura.retail.sync

import com.actionaura.retail.licensing.lease.verifyEd25519Signature
import com.actionaura.retail.securestorage.InMemorySecureBlobStore
import com.actionaura.retail.securestorage.SecureBlobStore
import com.google.crypto.tink.subtle.Ed25519Verify
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * Task 7 -- real, executed proof of the two correctness properties that
 * matter most for device signing:
 *
 * 1. The persisted keypair survives a fresh, independently-constructed
 *    `PlatformDeviceSigner` backed by the SAME underlying `SecureBlobStore`
 *    -- the real precedent this codebase already established for
 *    simulating "does state survive a fresh process/container" in
 *    `AuraAppContainerTest.secureMaterialStoreIsRealFunctionalAndIndependentPerContainer`
 *    (two independent object graphs sharing one real `SecureBlobStore`
 *    instance is this module's own established JVM-testable proxy for "two
 *    app launches" -- a regenerated key would silently and permanently
 *    break Owner-side verification, since Owner registers whatever public
 *    key the device first presented at activation).
 *
 * 2. `sign()`'s output actually verifies against the existing, real,
 *    production `Ed25519Verify` primitive `SignedLeaseSignatureVerifier`
 *    already uses -- proving the new signer and the existing verifier
 *    agree on the same curve/encoding, not just that both compile.
 */
class DeviceSignerTest {

    @Test
    fun publicKeyIsStableAcrossIndependentlyConstructedSignersBackedBySameStore() = runTest {
        val sharedBlobStore: SecureBlobStore = InMemorySecureBlobStore()

        val firstLaunch = PlatformDeviceSigner(sharedBlobStore)
        val keyFromFirstLaunch = firstLaunch.publicKeyBytes()

        // A brand-new PlatformDeviceSigner instance -- exactly what a fresh
        // process launch constructs via a fresh AuraAppContainer -- backed
        // by the SAME real secure storage the first instance persisted into.
        val secondLaunch = PlatformDeviceSigner(sharedBlobStore)
        val keyFromSecondLaunch = secondLaunch.publicKeyBytes()

        assertContentEquals(
            keyFromFirstLaunch,
            keyFromSecondLaunch,
            "real regression: a regenerated key would silently and permanently break Owner-side verification -- the public key MUST be identical across independently-constructed signers backed by the same store",
        )

        // Repeat calls on either instance (in-memory cache hit vs.
        // freshly-loaded-from-store first call) must also agree.
        assertContentEquals(keyFromFirstLaunch, firstLaunch.publicKeyBytes())
        assertContentEquals(keyFromSecondLaunch, secondLaunch.publicKeyBytes())

        // A THIRD independent instance, constructed after both of the above
        // already touched the store, still sees the same persisted key --
        // not just "the second call after the first" but truly stable state.
        val thirdLaunch = PlatformDeviceSigner(sharedBlobStore)
        assertContentEquals(keyFromFirstLaunch, thirdLaunch.publicKeyBytes())
    }

    @Test
    fun twoDevicesWithIndependentSecureStorageNeverEndUpWithTheSameGeneratedKey() = runTest {
        val signerA = PlatformDeviceSigner(InMemorySecureBlobStore())
        val signerB = PlatformDeviceSigner(InMemorySecureBlobStore())

        val keyA = signerA.publicKeyBytes()
        val keyB = signerB.publicKeyBytes()

        assertFalse(keyA.contentEquals(keyB), "two devices with independent secure storage must never end up with the same generated key")
    }

    @Test
    fun signatureProducedByDeviceSignerVerifiesWithTheRealExistingEd25519VerifyPrimitive() = runTest {
        val signer = PlatformDeviceSigner(InMemorySecureBlobStore())
        val publicKey = signer.publicKeyBytes()
        val message = "task-7-device-signing-round-trip-message".encodeToByteArray()

        val signature = signer.sign(message)

        assertEquals(32, publicKey.size, "Ed25519 public key must be raw 32 bytes")
        assertEquals(64, signature.size, "Ed25519 signature must be raw 64 bytes")

        // Real, independent verification via the SAME real Tink primitive
        // `SignedLeaseSignatureVerifier.android.kt` uses in production --
        // throws on a bad signature/key, so reaching the next line at all is
        // the real proof. This closes the loop: the new signer and the
        // existing verifier agree on the same curve/encoding, not just that
        // both compile.
        Ed25519Verify(publicKey).verify(signature, message)

        // And through this codebase's own production `verifyEd25519Signature`
        // wrapper (the exact function the real lease-verification pipeline calls).
        assertTrue(verifyEd25519Signature(publicKey, signature, message), "the codebase's own production verifyEd25519Signature() must accept a signature the new DeviceSigner produced")
    }

    @Test
    fun signatureIsRejectedAfterTamperingWithTheMessage() = runTest {
        val signer = PlatformDeviceSigner(InMemorySecureBlobStore())
        val publicKey = signer.publicKeyBytes()
        val originalMessage = "original-message".encodeToByteArray()
        val signature = signer.sign(originalMessage)

        val tamperedMessage = "tampered-message".encodeToByteArray()
        assertFalse(
            verifyEd25519Signature(publicKey, signature, tamperedMessage),
            "a signature over one message must never verify against a different message",
        )
    }

    @Test
    fun signatureIsRejectedUnderAWrongDevicesPublicKey() = runTest {
        val signerA = PlatformDeviceSigner(InMemorySecureBlobStore())
        val signerB = PlatformDeviceSigner(InMemorySecureBlobStore())
        val message = "shared-message-signed-by-a".encodeToByteArray()

        val signatureFromA = signerA.sign(message)
        val publicKeyOfB = signerB.publicKeyBytes()

        assertFalse(
            verifyEd25519Signature(publicKeyOfB, signatureFromA, message),
            "device A's signature must never verify under device B's public key",
        )
    }

    @Test
    fun signingIsDeterministicForTheSamePersistedKeyAndMessage() = runTest {
        // Ed25519 signing is a pure deterministic function of (private key,
        // message) per RFC 8032 -- signing the same message twice through
        // the SAME persisted key must produce byte-identical signatures.
        // A non-deterministic result here would indicate the signer is
        // silently using a different key each call.
        val store = InMemorySecureBlobStore()
        val firstLaunch = PlatformDeviceSigner(store)
        val secondLaunch = PlatformDeviceSigner(store)
        val message = "determinism-check".encodeToByteArray()

        val signatureFromFirstLaunch = firstLaunch.sign(message)
        val signatureFromSecondLaunch = secondLaunch.sign(message)

        assertContentEquals(signatureFromFirstLaunch, signatureFromSecondLaunch)
    }

    /**
     * Regression for the review-caught critical hazard: `loadOrCreateKeyPair()`
     * used to treat ANY `secureBlobStore.get()` failure (Keystore key
     * invalidation, corrupt/tampered ciphertext, generic IO error -- real,
     * non-hypothetical `SecureStorageFailureCode` variants, actually
     * returned by `AndroidSecureBlobStore` in production) the same as
     * "no key was ever stored," silently generating and persisting a
     * replacement keypair over the one existing `BLOB_NAME` slot. This is
     * the exact "regenerated key silently and permanently breaks
     * Owner-side verification" hazard this whole task exists to prevent --
     * `InMemorySecureBlobStore.corruptOnNextGet` (this codebase's own
     * established fault-injection primitive, already used by
     * `SecureMaterialStoreTest.corruptedComponentFailsClosedNeverReturnsPartialBundle`)
     * forces exactly that read-failure path, one-shot.
     */
    @Test
    fun readFailureIsSurfacedLoudlyAndNeverTreatedAsFirstRunNeverOverwritesThePersistedKey() = runTest {
        val store = InMemorySecureBlobStore()

        // Genuine first run: establishes and persists the real keypair.
        val originalPublicKey = PlatformDeviceSigner(store).publicKeyBytes()

        // Force the NEXT get() on this blob to fail closed as corrupt --
        // simulating a transient Keystore/ciphertext read failure. Must
        // match PlatformDeviceSigner's own internal BLOB_NAME.
        store.corruptOnNextGet.add("device_signing:ed25519_keypair_v1")

        // A brand-new instance (no in-memory cache) actually exercises the
        // read path and must hit the injected failure.
        val instanceDuringFailure = PlatformDeviceSigner(store)
        val thrown = assertFailsWith<IllegalStateException>(
            "a read failure must be surfaced loudly, never silently swallowed into \"must be a first run\"",
        ) {
            instanceDuringFailure.publicKeyBytes()
        }
        assertTrue(
            thrown.message.orEmpty().contains("refusing", ignoreCase = true),
            "the thrown exception must explain that it is refusing to treat a read failure as first-run, got: ${thrown.message}",
        )

        // Critical assertion: the failed attempt must NOT have generated or
        // persisted a replacement keypair. `corruptOnNextGet` is one-shot,
        // so this next read succeeds against real storage again -- and it
        // must still be the ORIGINAL key, proving no silent overwrite
        // occurred during the failure above.
        val instanceAfterFailure = PlatformDeviceSigner(store)
        assertContentEquals(
            originalPublicKey,
            instanceAfterFailure.publicKeyBytes(),
            "real regression: a transient read failure must never silently mint and persist a replacement device identity over the one Owner already registered",
        )
    }
}
