package com.actionaura.retail.sync

import com.google.common.truth.Truth.assertThat
import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import org.junit.Assert.assertThrows
import org.junit.Test
import java.io.File
import java.security.MessageDigest
import java.util.Base64

/**
 * SYNC-BOOT Phase 3, Android requester: the state machine around Owner's mailbox, with a fake mailbox and a
 * fake Python seam, and REAL ciphertext (the vector Python's `crypto.py` produced; see BootstrapCryptoTest).
 *
 * What is pinned, each with the opposite direction beside it:
 *  - the happy path asks once, waits while no producer has finished, then downloads, decrypts, stages,
 *    acks (Owner may delete the bytes) and forgets the key;
 *  - a chunk whose hash does not match, or that does not authenticate, stages NOTHING and acks NOTHING
 *    (the bytes must stay so a retry is possible);
 *  - a copy the backend refuses (another shop) stages nothing and acks nothing, and the same request is kept;
 *  - a restart resumes the SAME request instead of making a second one.
 */
class BootstrapRequesterTest {
    private val rid = "11111111-2222-3333-4444-555555555555"
    private val keys = RequesterKeys(
        privateB64 = "MJG9gT9zOzvWCpJvFplxkZdcQG4S+pqts0iKGu4QlW4=",
        publicB64 = "rzub34ICwe3f/jhl82prTJ3mMPIsK2Xv2hDCVCgxGEo=")
    private val chunks: List<ByteArray> = BootstrapCryptoVector.chunks()
    private val encryption: JsonObject = JsonParser.parseString(
        """{"format":"aura-bootstrap-enc/1","ephemeral_pub":"CzuD7VEzVllLY8k4gwmr2USMBtuKKckoeyu7C33m9Xk=",
           "salt":"y7pmtd0YDoVkxRjR65NwcSAlsiaRksqrkFLf2cUlG1M=","chunk_count":3,"plaintext_size":2500}"""
    ).asJsonObject

    private fun sha(b: ByteArray) = MessageDigest.getInstance("SHA-256").digest(b).joinToString("") { "%02x".format(it) }

    private class MemoryStore : BootstrapStateStore {
        var state: BootstrapState? = null
        override fun load() = state
        override fun save(state: BootstrapState) { this.state = state }
        override fun discard() { state = null }
    }

    private inner class FakeMailbox(var status: String = "OPEN") : MailboxTransport {
        var requests = 0
        var acks = 0
        var forgotten = false
        var corrupt: ((Int, ByteArray) -> ByteArray)? = null
        var recordedHashes: List<String> = chunks.map(::sha)
        override fun request(encPubkey: String, schemaVersion: Int, appVersion: String?): JsonObject {
            requests++
            assertThat(encPubkey).isEqualTo(keys.publicB64)       // the producer is told the key it must encrypt to
            return view("OPEN")
        }
        override fun status(requestId: String): JsonObject {
            if (forgotten) throw MailboxNotFound()
            return view(status)
        }
        override fun downloadChunk(requestId: String, index: Int): JsonObject {
            val raw = corrupt?.invoke(index, chunks[index]) ?: chunks[index]
            return JsonObject().also {
                it.addProperty("index", index)
                it.addProperty("data", Base64.getEncoder().encodeToString(raw))
                it.addProperty("sha256", sha(raw))
            }
        }
        override fun ack(requestId: String) { acks++ }
        private fun view(s: String): JsonObject = JsonObject().also { v ->
            v.addProperty("request_id", rid)
            v.addProperty("status", s)
            val m = JsonObject()
            if (s == "READY") {
                m.add("encryption", encryption.deepCopy())
                m.addProperty("chunk_count", 3)
                m.add("chunk_sha256", JsonArray().also { a -> recordedHashes.forEach(a::add) })
            }
            v.add("manifest", m)
        }
    }

    private class FakeStager(var refuse: String? = null) : BootstrapStager {
        var staged: ByteArray? = null
        var relay: String? = null
        override fun schemaVersion() = 56
        override fun stage(zip: ByteArray, relayUrl: String) {
            refuse?.let { throw BootstrapRefused(it, "refused") }
            staged = zip
            relay = relayUrl
        }
    }

    private val expectedPlain = ByteArray(2500) { i -> ((i * 7 + 3) % 251).toByte() }
    private fun requester(box: FakeMailbox, stager: FakeStager, store: MemoryStore) =
        BootstrapRequester(box, stager, store, "https://owner.example", "1.0", newKeys = { keys })

    @Test fun asksOnceWaitsThenStagesAcksAndForgetsTheKey() {
        val box = FakeMailbox(); val stager = FakeStager(); val store = MemoryStore()
        val r = requester(box, stager, store)
        assertThat(r.step()).isEqualTo(RequesterOutcome.REQUESTED)
        assertThat(r.step()).isEqualTo(RequesterOutcome.WAITING)           // OPEN: no producer yet
        box.status = "CLAIMED"
        assertThat(r.step()).isEqualTo(RequesterOutcome.WAITING)
        assertThat(box.requests).isEqualTo(1)                              // never a second request while waiting
        box.status = "READY"
        assertThat(r.step()).isEqualTo(RequesterOutcome.STAGED)
        assertThat(stager.staged).isEqualTo(expectedPlain)
        assertThat(stager.relay).isEqualTo("https://owner.example")
        assertThat(box.acks).isEqualTo(1)
        assertThat(store.state).isNull()                                   // the single-use key is gone
    }

    @Test fun aRestartResumesTheSameRequest() {
        val box = FakeMailbox(); val store = MemoryStore()
        assertThat(requester(box, FakeStager(), store).step()).isEqualTo(RequesterOutcome.REQUESTED)
        // "restart": a brand-new requester over the same persisted state
        assertThat(requester(box, FakeStager(), store).step()).isEqualTo(RequesterOutcome.WAITING)
        assertThat(box.requests).isEqualTo(1)
    }

    @Test fun aTamperedChunkStagesNothingAndAcksNothing() {
        val box = FakeMailbox("READY").also { it.corrupt = { i, b -> if (i == 1) b.copyOf().also { c -> c[3] = (c[3].toInt() xor 1).toByte() } else b } }
        val stager = FakeStager(); val store = MemoryStore()
        val r = requester(box, stager, store)
        r.step()                                                           // REQUESTED (creates the state)
        assertThrows(BootstrapCryptoError::class.java) { r.step() }
        assertThat(stager.staged).isNull()
        assertThat(box.acks).isEqualTo(0)
        assertThat(store.state).isNotNull()                                // the request is kept for a retry
    }

    @Test fun aChunkThatDoesNotMatchTheRecordedHashIsRefused() {
        val box = FakeMailbox("READY")
        box.recordedHashes = chunks.map(::sha).toMutableList().also { it[2] = "0".repeat(64) }
        val stager = FakeStager(); val r = requester(box, stager, MemoryStore())
        r.step()
        assertThrows(BootstrapCryptoError::class.java) { r.step() }
        assertThat(stager.staged).isNull()
        assertThat(box.acks).isEqualTo(0)
    }

    @Test fun aCopyTheBackendRefusesIsNeverAckedAndTheRequestIsKept() {
        val box = FakeMailbox("READY"); val stager = FakeStager(refuse = "COMPANY_MISMATCH"); val store = MemoryStore()
        val r = requester(box, stager, store)
        r.step()
        val refused = assertThrows(BootstrapRefused::class.java) { r.step() }
        assertThat(refused.reasonCode).isEqualTo("COMPANY_MISMATCH")
        assertThat(box.acks).isEqualTo(0)
        assertThat(store.state).isNotNull()
    }

    @Test fun anExpiredOrForgottenRequestStartsOver() {
        val box = FakeMailbox(); val store = MemoryStore()
        val r = requester(box, FakeStager(), store)
        r.step()
        box.forgotten = true
        assertThat(r.step()).isEqualTo(RequesterOutcome.RETRY)
        assertThat(store.state).isNull()
        box.forgotten = false; box.status = "OPEN"
        assertThat(r.step()).isEqualTo(RequesterOutcome.REQUESTED)
        assertThat(box.requests).isEqualTo(2)
    }

    @Test fun fileStateStoreRoundTripsAndDiscards() {
        val f = File.createTempFile("bootstrap-state", ".json")
        f.delete()
        val s = FileBootstrapStateStore(f)
        assertThat(s.load()).isNull()
        val st = BootstrapState("rid-1", "priv", "https://x", staged = true)
        s.save(st)
        assertThat(s.load()).isEqualTo(st)
        s.discard()
        assertThat(s.load()).isNull()
    }
}

/** The ciphertext Python's `crypto.encrypt_package` produced for [BootstrapRequesterTest] (same as BootstrapCryptoTest). */
internal object BootstrapCryptoVector {
    fun chunks(): List<ByteArray> = BootstrapCryptoTestVectors.CHUNKS.map { Base64.getDecoder().decode(it) }
}
