package com.actionaura.retail.updates

import com.actionaura.retail.licensing.KeyWrapper
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.spec.GCMParameterSpec

/**
 * A real AES-GCM [KeyWrapper] with a throwaway key, so the update tests sign
 * with a REAL Ed25519 device identity (the signature checks below verify real
 * bytes) without needing the Android keystore. Same wrapper the licensing
 * tests use; kept here so this package's tests stand on their own.
 */
internal class UpdatesTestKeyWrapper : KeyWrapper {
    private val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
    private val random = SecureRandom()

    override fun wrap(plaintext: ByteArray): ByteArray {
        val iv = ByteArray(12).also { random.nextBytes(it) }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key, GCMParameterSpec(128, iv))
        return iv + cipher.doFinal(plaintext)
    }

    override fun unwrap(stored: ByteArray): ByteArray {
        val iv = stored.copyOfRange(0, 12)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, iv))
        return cipher.doFinal(stored.copyOfRange(12, stored.size))
    }
}
