package com.actionaura.retail.licensing

import com.google.common.truth.Truth.assertThat
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Pure-JVM logic verification of DeviceIdentity -- plain JUnit + a
 * TemporaryFolder, no Android framework, no Robolectric. Real crypto
 * throughout: FakeKeyWrapper below is a genuine AES-256-GCM implementation
 * (java.security "AES" provider, not android.security.keystore) -- it is
 * NOT AndroidKeystore-backed and is not non-exportable/hardware-backed
 * (that's exactly what it stands in for, deliberately, so this file tests
 * DeviceIdentity's own logic in isolation from the platform keystore).
 * [AndroidKeystoreWrapper] itself -- the real production wrapper -- is only
 * exercised on a real device/emulator; that verification is not replaced by
 * this file (Part AA, still required).
 */
class DeviceIdentityTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    private class FakeKeyWrapper : KeyWrapper {
        private val secretKey: SecretKey = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        private val random = SecureRandom()

        override fun wrap(plaintext: ByteArray): ByteArray {
            val iv = ByteArray(12).also { random.nextBytes(it) }
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, secretKey, GCMParameterSpec(128, iv))
            return iv + cipher.doFinal(plaintext)
        }

        override fun unwrap(stored: ByteArray): ByteArray {
            if (stored.size <= 12) throw LocalStateCorruptError("Blob too short.")
            val iv = stored.copyOfRange(0, 12)
            val ciphertext = stored.copyOfRange(12, stored.size)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, secretKey, GCMParameterSpec(128, iv))
            return try {
                cipher.doFinal(ciphertext)
            } catch (exc: Exception) {
                throw LocalStateCorruptError("Decryption failed.", exc)
            }
        }
    }

    private fun newIdentity(wrapper: KeyWrapper = FakeKeyWrapper()) = DeviceIdentity(tempFolder.newFolder(), wrapper)

    @Test
    fun `no key initially`() {
        assertThat(newIdentity().hasKey()).isFalse()
    }

    @Test
    fun `generate then sign and verify round trip`() {
        val identity = newIdentity()
        val meta = identity.generateNewKey()
        assertThat(meta.status).isEqualTo("ACTIVE")
        assertThat(meta.algorithm).isEqualTo("ed25519")
        assertThat(identity.hasKey()).isTrue()

        val pubKeyRaw = b64Decode(identity.getPublicKeyB64())
        assertThat(pubKeyRaw.size).isEqualTo(32)
        assertThat(meta.publicKeyFingerprint).isEqualTo(fingerprintOf(pubKeyRaw))

        val message = "test-message".toByteArray()
        val sigRaw = b64Decode(identity.sign(message))
        assertThat(sigRaw.size).isEqualTo(64)
        assertThat(verifySignature(pubKeyRaw, message, sigRaw)).isTrue()
    }

    @Test
    fun `signature does not verify against altered message`() {
        val identity = newIdentity()
        identity.generateNewKey()
        val pubKeyRaw = b64Decode(identity.getPublicKeyB64())
        val sigRaw = b64Decode(identity.sign("original".toByteArray()))
        assertThat(verifySignature(pubKeyRaw, "altered".toByteArray(), sigRaw)).isFalse()
    }

    @Test
    fun `private key file is not plaintext ed25519`() {
        val folder = tempFolder.newFolder()
        val identity = DeviceIdentity(folder, FakeKeyWrapper())
        identity.generateNewKey()
        val keyFile = java.io.File(java.io.File(folder, "licensing"), "device_key.enc")
        val blob = keyFile.readBytes()
        // AES-GCM(IV || ciphertext+tag): 12 (IV) + 32 (plaintext) + 16 (tag) = 60 bytes here, never the raw 32.
        assertThat(blob.size).isNotEqualTo(32)
    }

    @Test(expected = DeviceIdentityError::class)
    fun `generateNewKey refuses to overwrite existing key`() {
        val identity = newIdentity()
        identity.generateNewKey()
        identity.generateNewKey()
    }

    @Test(expected = DeviceIdentityError::class)
    fun `missing key raises not silently generates`() {
        newIdentity().sign("anything".toByteArray())
    }

    @Test(expected = LocalStateCorruptError::class)
    fun `corrupted key file raises LocalStateCorrupt`() {
        val folder = tempFolder.newFolder()
        val identity = DeviceIdentity(folder, FakeKeyWrapper())
        identity.generateNewKey()
        val keyFile = java.io.File(java.io.File(folder, "licensing"), "device_key.enc")
        keyFile.writeBytes("not a real encrypted blob at all, wrong length".toByteArray())
        identity.sign("anything".toByteArray())
    }

    @Test(expected = LocalStateCorruptError::class)
    fun `corrupted metadata raises LocalStateCorrupt`() {
        val folder = tempFolder.newFolder()
        val identity = DeviceIdentity(folder, FakeKeyWrapper())
        identity.generateNewKey()
        val metaFile = java.io.File(java.io.File(folder, "licensing"), "device_key_meta.json")
        metaFile.writeText("{not valid json")
        identity.getMetadata()
    }

    @Test
    fun `mark reset pending then destroy`() {
        val identity = newIdentity()
        identity.generateNewKey()
        identity.markResetPending()
        assertThat(identity.getMetadata().status).isEqualTo("RESET_PENDING")
        identity.destroyKey()
        assertThat(identity.hasKey()).isFalse()
    }

    @Test
    fun `two different devices produce different fingerprints`() {
        val meta1 = newIdentity().generateNewKey()
        val meta2 = newIdentity().generateNewKey()
        assertThat(meta1.publicKeyFingerprint).isNotEqualTo(meta2.publicKeyFingerprint)
    }

    @Test
    fun `fingerprint matches independent sha256 computation`() {
        val identity = newIdentity()
        val meta = identity.generateNewKey()
        val pubKeyRaw = b64Decode(identity.getPublicKeyB64())
        val expected = MessageDigest.getInstance("SHA-256").digest(pubKeyRaw).joinToString("") { "%02x".format(it) }
        assertThat(meta.publicKeyFingerprint).isEqualTo(expected)
    }

    private fun b64Decode(s: String): ByteArray = java.util.Base64.getDecoder().decode(s)
}
