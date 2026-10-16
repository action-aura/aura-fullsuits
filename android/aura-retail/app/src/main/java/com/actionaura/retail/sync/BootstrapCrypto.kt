package com.actionaura.retail.sync

import com.google.crypto.tink.subtle.Hkdf
import com.google.crypto.tink.subtle.X25519
import com.google.gson.JsonObject
import java.nio.ByteBuffer
import java.util.Base64
import javax.crypto.AEADBadTagException
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/** A bootstrap copy could not be opened or did not authenticate. Never "best effort". */
class BootstrapCryptoError(message: String) : Exception(message)

/** The requester's single-use X25519 pair, base64 of the 32 raw bytes (what Python's `crypto.py` uses). */
data class RequesterKeys(val privateB64: String, val publicB64: String)

/**
 * Kotlin half of `commercial_runtime/sync/bootstrap/crypto.py` (SYNC-BOOT Phase 3): the REQUESTER side of
 * the end-to-end encryption of a shop copy while it sits in Owner's mailbox. Owner relays ciphertext it
 * cannot read; only this device's private key (made here, never sent anywhere) opens it. The producer
 * (a desktop computer) encrypts; Android is never a producer in Phase 3, so there is no encrypt here.
 *
 * Byte-compatibility with the Python side is the whole point and is pinned by a vector produced by the
 * Python module (BootstrapCryptoTest): X25519 -> HKDF-SHA256(salt, info = "aura-bootstrap-enc/1 key|<request id>")
 * -> AES-256-GCM per chunk, nonce = 4 zero bytes + big-endian chunk index, AAD =
 * "aura-bootstrap-enc/1|<request id>|" + big-endian (index, total) as two uint32. The AAD is what stops a
 * chunk being moved, lifted from another request, or the copy being truncated.
 */
object BootstrapCrypto {
    const val FORMAT = "aura-bootstrap-enc/1"
    private val HKDF_INFO = "aura-bootstrap-enc/1 key".toByteArray(Charsets.US_ASCII)
    private const val GCM_TAG_BITS = 128

    fun newRequesterKeys(): RequesterKeys {
        val priv = X25519.generatePrivateKey()
        val pub = X25519.publicFromPrivate(priv)
        val enc = Base64.getEncoder()
        return RequesterKeys(enc.encodeToString(priv), enc.encodeToString(pub))
    }

    private fun unb64(text: String, what: String): ByteArray = try {
        Base64.getDecoder().decode(text)
    } catch (exc: IllegalArgumentException) {
        throw BootstrapCryptoError("$what is not valid base64")
    }

    private fun aad(requestId: String, index: Int, total: Int): ByteArray {
        val head = "$FORMAT|$requestId|".toByteArray(Charsets.US_ASCII)
        return ByteBuffer.allocate(head.size + 8).put(head).putInt(index).putInt(total).array()
    }

    private fun nonce(index: Int): ByteArray = ByteBuffer.allocate(12).putInt(0).putLong(index.toLong()).array()

    /** Opens chunks of ONE request, one at a time. [params] is the manifest's public `encryption` object. */
    class ChunkDecryptor(requesterPrivateB64: String, params: JsonObject, private val requestId: String) {
        val total: Int
        private val key: SecretKeySpec

        init {
            if (params.get("format")?.asString != FORMAT) {
                throw BootstrapCryptoError("the copy uses an encryption format this build does not know")
            }
            val priv = unb64(requesterPrivateB64, "the requester private key")
            val eph = unb64(params.get("ephemeral_pub")?.asString ?: "", "the producer key")
            val salt = unb64(params.get("salt")?.asString ?: "", "the salt")
            if (priv.size != 32 || eph.size != 32) throw BootstrapCryptoError("an X25519 key is not 32 bytes")
            total = params.get("chunk_count")?.asInt ?: 0
            if (total < 1) throw BootstrapCryptoError("the copy names no valid chunk count")
            val shared = X25519.computeSharedSecret(priv, eph)
            val info = HKDF_INFO + "|".toByteArray(Charsets.US_ASCII) + requestId.toByteArray(Charsets.US_ASCII)
            key = SecretKeySpec(Hkdf.computeHkdf("HmacSha256", shared, salt, info, 32), "AES")
        }

        fun decryptChunk(index: Int, blob: ByteArray): ByteArray {
            if (index !in 0 until total) throw BootstrapCryptoError("chunk $index is outside 0..${total - 1}")
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(GCM_TAG_BITS, nonce(index)))
            cipher.updateAAD(aad(requestId, index, total))
            return try {
                cipher.doFinal(blob)
            } catch (exc: AEADBadTagException) {
                throw BootstrapCryptoError("chunk $index failed authentication (wrong key, tampered, or from another copy)")
            }
        }
    }
}
