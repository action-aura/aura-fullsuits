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
 *  - the happy path asks once, waits while no producer has finished, then streams the chunks into a file
 *    sink one at a time (peak = one chunk), hands the FILE to the backend, acks, forgets the key;
 *  - a chunk whose hash does not match, or that does not authenticate, stages NOTHING and acks NOTHING;
 *  - a refused copy backs off 1, 2, 4 ... minutes capped at 30, persisted, and is not fetched meanwhile;
 *  - an open cash drawer means nothing is requested or downloaded, however many ticks pass;
 *  - an already-staged copy (also the crash window before "staged" was recorded) is never re-requested;
 *  - too many failed installs means no more requests at all;
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
    private val expectedPlain = ByteArray(2500) { i -> ((i * 7 + 3) % 251).toByte() }
    private var now = 1_000_000L

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
        var downloads = 0
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
            downloads++
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

    private class FakeStager(var refuse: String? = null, var info: BootstrapInfo = BootstrapInfo(schemaVersion = 56)) : BootstrapStager {
        var stagedPath: String? = null
        var stagedBytes: ByteArray? = null
        var relay: String? = null
        var stageCalls = 0
        override fun info() = info
        override fun stageFile(path: String, relayUrl: String) {
            stageCalls++
            refuse?.let { throw BootstrapRefused(it, "refused") }
            stagedPath = path
            stagedBytes = File(path).readBytes()                  // the backend reads the FILE
            relay = relayUrl
        }
    }

    /** Records every write: the largest single buffer the requester ever holds is the largest write. */
    private class RecordingSink(val file: File) : CopySink {
        val writes = mutableListOf<Int>()
        var aborted = false
        private val out = file.outputStream()
        override fun write(plain: ByteArray) { writes += plain.size; out.write(plain) }
        override fun finish(): String { out.close(); return file.absolutePath }
        override fun abort() { aborted = true; try { out.close() } catch (e: Exception) {}; file.delete() }
    }

    private val sinks = mutableListOf<RecordingSink>()
    private fun sinkFactory() = CopySinkFactory {
        RecordingSink(File.createTempFile("copy", ".zip").also { it.deleteOnExit() }).also { sinks += it }
    }

    private fun requester(box: FakeMailbox, stager: FakeStager, store: MemoryStore) =
        BootstrapRequester(box, stager, store, "https://owner.example", "1.0", newKeys = { keys },
            sinkFactory = sinkFactory(), clock = { now })

    @Test fun asksOnceWaitsThenStreamsStagesAcksAndForgetsTheKey() {
        val box = FakeMailbox(); val stager = FakeStager(); val store = MemoryStore()
        val r = requester(box, stager, store)
        assertThat(r.step()).isEqualTo(RequesterOutcome.REQUESTED)
        assertThat(r.step()).isEqualTo(RequesterOutcome.WAITING)           // OPEN: no producer yet
        box.status = "CLAIMED"
        assertThat(r.step()).isEqualTo(RequesterOutcome.WAITING)
        assertThat(box.requests).isEqualTo(1)                              // never a second request while waiting
        box.status = "READY"
        assertThat(r.step()).isEqualTo(RequesterOutcome.STAGED)
        assertThat(stager.stagedBytes).isEqualTo(expectedPlain)
        assertThat(stager.relay).isEqualTo("https://owner.example")
        assertThat(box.acks).isEqualTo(1)
        assertThat(store.state).isNull()                                   // the single-use key is gone
    }

    @Test fun neverHoldsMoreThanOneChunkInMemory() {
        // The copy is streamed chunk by chunk into the sink: the biggest buffer the requester ever hands over is
        // one decrypted chunk (1024 bytes in this vector), and the whole copy (2500) is only ever on disk.
        // MUTATION: collect every chunk and write once at the end -> one 2500-byte write -> red.
        val box = FakeMailbox("READY"); val stager = FakeStager(); val r = requester(box, stager, MemoryStore())
        r.step(); r.step()
        assertThat(sinks).hasSize(1)
        assertThat(sinks[0].writes).containsExactly(1024, 1024, 452).inOrder()
        assertThat(sinks[0].writes.max()).isAtMost(1024)
        assertThat(stager.stagedPath).isEqualTo(sinks[0].file.absolutePath)      // the backend is handed a path, not bytes
    }

    @Test fun aRestartResumesTheSameRequest() {
        val box = FakeMailbox(); val store = MemoryStore()
        assertThat(requester(box, FakeStager(), store).step()).isEqualTo(RequesterOutcome.REQUESTED)
        // "restart": a brand-new requester over the same persisted state
        assertThat(requester(box, FakeStager(), store).step()).isEqualTo(RequesterOutcome.WAITING)
        assertThat(box.requests).isEqualTo(1)
    }

    @Test fun aTamperedChunkStagesNothingAcksNothingAndLeavesNoPartialFile() {
        val box = FakeMailbox("READY").also { it.corrupt = { i, b -> if (i == 1) b.copyOf().also { c -> c[3] = (c[3].toInt() xor 1).toByte() } else b } }
        val stager = FakeStager(); val store = MemoryStore()
        val r = requester(box, stager, store)
        r.step()                                                           // REQUESTED (creates the state)
        assertThrows(BootstrapCryptoError::class.java) { r.step() }
        assertThat(stager.stageCalls).isEqualTo(0)
        assertThat(box.acks).isEqualTo(0)
        assertThat(sinks[0].aborted).isTrue()
        assertThat(sinks[0].file.exists()).isFalse()
        assertThat(store.state).isNotNull()                                // the request is kept for a retry
    }

    @Test fun aChunkThatDoesNotMatchTheRecordedHashIsRefused() {
        val box = FakeMailbox("READY")
        box.recordedHashes = chunks.map(::sha).toMutableList().also { it[2] = "0".repeat(64) }
        val stager = FakeStager(); val r = requester(box, stager, MemoryStore())
        r.step()
        assertThrows(BootstrapCryptoError::class.java) { r.step() }
        assertThat(stager.stageCalls).isEqualTo(0)
        assertThat(box.acks).isEqualTo(0)
    }

    @Test fun aRefusedCopyBacksOffOneTwoFourMinutesAndIsNotFetchedMeanwhile() {
        // MUTATION: drop the notBefore check in step() -> the "meanwhile" downloads happen -> red.
        val box = FakeMailbox("READY"); val stager = FakeStager(refuse = "COMPANY_MISMATCH"); val store = MemoryStore()
        val r = requester(box, stager, store)
        r.step()
        for (minutes in listOf(1L, 2L, 4L)) {
            val before = box.downloads
            assertThat(assertThrows(BootstrapRefused::class.java) { r.step() }.reasonCode).isEqualTo("COMPANY_MISMATCH")
            assertThat(box.downloads).isEqualTo(before + 3)                // one full download per attempt
            assertThat(store.state!!.notBeforeMillis).isEqualTo(now + minutes * 60_000)
            now += minutes * 60_000 - 1
            assertThat(r.step()).isEqualTo(RequesterOutcome.BACKING_OFF)   // one millisecond early: nothing fetched
            assertThat(box.downloads).isEqualTo(before + 3)
            now += 1
        }
        assertThat(box.acks).isEqualTo(0)
    }

    @Test fun theBackOffIsCappedAtThirtyMinutesAndSurvivesARestart() {
        val box = FakeMailbox("READY"); val stager = FakeStager(refuse = "SCHEMA_NEWER"); val store = MemoryStore()
        store.state = BootstrapState(rid, keys.privateB64, "https://owner.example", refusals = 9)
        assertThrows(BootstrapRefused::class.java) { requester(box, stager, store).step() }
        assertThat(store.state!!.refusals).isEqualTo(10)
        assertThat(store.state!!.notBeforeMillis).isEqualTo(now + 30 * 60_000)         // 2^9 minutes capped to 30
        // a brand-new requester (app restart) over the same persisted state still waits
        assertThat(requester(box, stager, store).step()).isEqualTo(RequesterOutcome.BACKING_OFF)
    }

    @Test fun aGoodCopyAfterRefusalsClearsTheBackOff() {
        val box = FakeMailbox("READY"); val stager = FakeStager(); val store = MemoryStore()
        store.state = BootstrapState(rid, keys.privateB64, "https://owner.example", refusals = 3, notBeforeMillis = now - 1)
        assertThat(requester(box, stager, store).step()).isEqualTo(RequesterOutcome.STAGED)
        assertThat(store.state).isNull()
    }

    @Test fun anOpenCashDrawerMeansNothingIsRequestedOrDownloadedHoweverManyTicksPass() {
        // MUTATION: ignore info.canStage -> a request is made / chunks fetched -> red.
        val box = FakeMailbox("READY"); val stager = FakeStager(info = BootstrapInfo(56, canStage = false, blockReason = "OPEN_CASH_SESSION"))
        val store = MemoryStore()
        val r = requester(box, stager, store)
        repeat(25) { assertThat(r.step()).isEqualTo(RequesterOutcome.BLOCKED) }
        assertThat(box.requests).isEqualTo(0)
        assertThat(box.downloads).isEqualTo(0)
        assertThat(sinks).isEmpty()
        assertThat(store.state).isNull()
        // drawer closed: the very next step goes ahead
        stager.info = BootstrapInfo(56)
        assertThat(r.step()).isEqualTo(RequesterOutcome.REQUESTED)
    }

    @Test fun aCopyAlreadyStagedIsNeverRequestedAgain() {
        val box = FakeMailbox(); val store = MemoryStore()
        val r = requester(box, FakeStager(info = BootstrapInfo(56, pendingStaged = true)), store)
        assertThat(r.step()).isEqualTo(RequesterOutcome.STAGED)
        assertThat(box.requests).isEqualTo(0)
    }

    @Test fun theCrashWindowBetweenStagingAndRecordingItIsClosed() {
        // The app died after the backend staged the copy but before "staged" was saved: the state still names a
        // live READY request. The next tick must treat the copy as staged (ack Owner's bytes away, forget the
        // key) and must NOT download it again or open a second request.
        val box = FakeMailbox("READY"); val stager = FakeStager(); val store = MemoryStore()
        store.state = BootstrapState(rid, keys.privateB64, "https://owner.example")
        stager.info = BootstrapInfo(56, pendingStaged = true)
        assertThat(requester(box, stager, store).step()).isEqualTo(RequesterOutcome.STAGED)
        assertThat(box.downloads).isEqualTo(0)
        assertThat(box.requests).isEqualTo(0)
        assertThat(box.acks).isEqualTo(1)
        assertThat(store.state).isNull()
    }

    @Test fun tooManyFailedInstallsStopAllRequestsAndSayWhy() {
        val box = FakeMailbox(); val store = MemoryStore()
        val stager = FakeStager(info = BootstrapInfo(56, installFailures = 3, needsSupport = true))
        val r = requester(box, stager, store)
        repeat(5) { assertThat(r.step()).isEqualTo(RequesterOutcome.NEEDS_SUPPORT) }
        assertThat(box.requests).isEqualTo(0)
        // and the other direction: two failures so far is still allowed to ask
        stager.info = BootstrapInfo(56, installFailures = 2, needsSupport = false)
        assertThat(r.step()).isEqualTo(RequesterOutcome.REQUESTED)
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
        val st = BootstrapState("rid-1", "priv", "https://x", staged = true, refusals = 4, notBeforeMillis = 77L)
        s.save(st)
        assertThat(s.load()).isEqualTo(st)
        s.discard()
        assertThat(s.load()).isNull()
    }

    @Test fun fileCopySinkWritesThroughAndAbortRemovesThePartialFile() {
        val f = File.createTempFile("sink", ".zip")
        val ok = FileCopySink(f)
        ok.write(byteArrayOf(1, 2, 3)); ok.write(byteArrayOf(4))
        assertThat(File(ok.finish()).readBytes()).isEqualTo(byteArrayOf(1, 2, 3, 4))
        val bad = FileCopySink(f)
        bad.write(byteArrayOf(9))
        bad.abort()
        assertThat(f.exists()).isFalse()
    }
}

/** The ciphertext Python's `crypto.encrypt_package` produced for [BootstrapRequesterTest] (same as BootstrapCryptoTest). */
internal object BootstrapCryptoVector {
    fun chunks(): List<ByteArray> = BootstrapCryptoTestVectors.CHUNKS.map { Base64.getDecoder().decode(it) }
}
