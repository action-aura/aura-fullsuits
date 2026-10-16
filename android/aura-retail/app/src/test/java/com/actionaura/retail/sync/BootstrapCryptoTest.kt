package com.actionaura.retail.sync

import com.google.common.truth.Truth.assertThat
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import org.junit.Assert.assertThrows
import org.junit.Test
import java.util.Base64

/**
 * SYNC-BOOT Phase 3: the Kotlin requester must open exactly what the Python producer
 * (`commercial_runtime/sync/bootstrap/crypto.py`) encrypts. VECTOR below was produced BY that Python module
 * (`encrypt_package`, CHUNK_SIZE=1024, a fresh requester key pair): if either language's HKDF info, nonce,
 * AAD or key derivation drifts, [opensWhatPythonEncrypted] goes red.
 */
class BootstrapCryptoTest {
    private val rid = "11111111-2222-3333-4444-555555555555"
    private val priv = "MJG9gT9zOzvWCpJvFplxkZdcQG4S+pqts0iKGu4QlW4="
    private val params: JsonObject = JsonParser.parseString(
        """{"format":"aura-bootstrap-enc/1","ephemeral_pub":"CzuD7VEzVllLY8k4gwmr2USMBtuKKckoeyu7C33m9Xk=",
           "salt":"y7pmtd0YDoVkxRjR65NwcSAlsiaRksqrkFLf2cUlG1M=","chunk_count":3,"plaintext_size":2500}"""
    ).asJsonObject
    private val chunks: List<ByteArray> = BootstrapCryptoTestVectors.CHUNKS.map { Base64.getDecoder().decode(it) }
    private val expectedPlain = ByteArray(2500) { i -> ((i * 7 + 3) % 251).toByte() }

    private fun openAll(requestId: String = rid, blobs: List<ByteArray> = chunks): ByteArray {
        val d = BootstrapCrypto.ChunkDecryptor(priv, params, requestId)
        val out = java.io.ByteArrayOutputStream()
        blobs.forEachIndexed { i, b -> out.write(d.decryptChunk(i, b)) }
        return out.toByteArray()
    }

    @Test fun opensWhatPythonEncrypted() {
        assertThat(openAll()).isEqualTo(expectedPlain)
    }

    @Test fun aChunkMovedToAnotherPositionIsRefused() {
        val d = BootstrapCrypto.ChunkDecryptor(priv, params, rid)
        assertThrows(BootstrapCryptoError::class.java) { d.decryptChunk(0, chunks[1]) }
    }

    @Test fun aTamperedChunkIsRefused() {
        val bad = chunks[2].copyOf().also { it[5] = (it[5].toInt() xor 1).toByte() }
        assertThrows(BootstrapCryptoError::class.java) { openAll(blobs = listOf(chunks[0], chunks[1], bad)) }
    }

    @Test fun aCopyForAnotherRequestIsRefused() {
        assertThrows(BootstrapCryptoError::class.java) { openAll(requestId = "99999999-2222-3333-4444-555555555555") }
    }

    @Test fun aDifferentRequesterKeyCannotOpenIt() {
        val other = BootstrapCrypto.newRequesterKeys().privateB64
        val d = BootstrapCrypto.ChunkDecryptor(other, params, rid)
        assertThrows(BootstrapCryptoError::class.java) { d.decryptChunk(0, chunks[0]) }
    }

    @Test fun aTruncatedCopyIsRefusedBecauseTheTotalIsAuthenticated() {
        // Claiming 2 chunks instead of 3 changes every chunk's AAD, so even chunk 0 stops authenticating.
        val shorter = params.deepCopy().also { it.addProperty("chunk_count", 2) }
        val d = BootstrapCrypto.ChunkDecryptor(priv, shorter, rid)
        assertThrows(BootstrapCryptoError::class.java) { d.decryptChunk(0, chunks[0]) }
    }

    @Test fun anUnknownFormatIsRefused() {
        val odd = params.deepCopy().also { it.addProperty("format", "something-else/9") }
        assertThrows(BootstrapCryptoError::class.java) { BootstrapCrypto.ChunkDecryptor(priv, odd, rid) }
    }

    @Test fun freshRequesterKeysAreDistinctAnd32Bytes() {
        val a = BootstrapCrypto.newRequesterKeys()
        val b = BootstrapCrypto.newRequesterKeys()
        assertThat(a.privateB64).isNotEqualTo(b.privateB64)
        assertThat(Base64.getDecoder().decode(a.publicB64)).hasLength(32)
        assertThat(Base64.getDecoder().decode(a.privateB64)).hasLength(32)
    }
}
