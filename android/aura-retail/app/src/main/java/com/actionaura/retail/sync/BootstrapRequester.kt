package com.actionaura.retail.sync

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import java.io.File
import java.security.MessageDigest
import java.util.Base64

/** Owner's mailbox as the requester sees it. Production: [SyncRelayClient] (signed); tests: a fake. */
interface MailboxTransport {
    fun request(encPubkey: String, schemaVersion: Int, appVersion: String?): JsonObject
    /** @throws MailboxNotFound when Owner no longer knows the request. */
    fun status(requestId: String): JsonObject
    fun downloadChunk(requestId: String, index: Int): JsonObject
    fun ack(requestId: String)
}

class MailboxNotFound : Exception("Owner no longer knows this bootstrap request")

/** What the embedded Python backend says about bootstrapping, BEFORE any bytes are fetched
 *  (`/_internal/bootstrap/info`, installer.status on the Python side). */
data class BootstrapInfo(
    val schemaVersion: Int,
    /** False while an open cash drawer would make the backend refuse a copy (at staging and at the swap). */
    val canStage: Boolean = true,
    val blockReason: String? = null,
    /** A copy is already staged: do not ask for a second one (and do not download this one again). */
    val pendingStaged: Boolean = false,
    val installFailures: Int = 0,
    /** Too many installs in a row failed: stop asking, tell a person. */
    val needsSupport: Boolean = false,
)

/** The embedded Python backend's `/_internal/bootstrap/...` seam. */
interface BootstrapStager {
    fun info(): BootstrapInfo
    /** Verify and park the decrypted copy at [path] for the next start. The backend DELETES the file whatever
     *  the outcome. @throws BootstrapRefused with Python's reason code; nothing is staged then. */
    fun stageFile(path: String, relayUrl: String)
}

class BootstrapRefused(val reasonCode: String, message: String) : Exception(message)

/** Where the decrypted copy is written, one chunk at a time: the requester NEVER holds the whole copy in
 *  memory (a shop is tens of MB and a phone may have little to spare). */
interface CopySink {
    fun write(plain: ByteArray)
    /** All chunks written: flush and return the file's path. */
    fun finish(): String
    /** Something failed: remove the partial file. */
    fun abort()
}

fun interface CopySinkFactory {
    fun open(): CopySink
}

/** [CopySink] over one file, written through a small buffer. */
class FileCopySink(private val file: File) : CopySink {
    private val out = run {
        file.parentFile?.mkdirs()
        file.delete()
        file.outputStream().buffered(64 * 1024)
    }

    override fun write(plain: ByteArray) = out.write(plain)

    override fun finish(): String {
        out.flush()
        out.close()
        return file.absolutePath
    }

    override fun abort() {
        try { out.close() } catch (exc: Exception) { /* already closed */ }
        file.delete()
    }
}

/** Small persisted record: the request id and the SINGLE-USE private key (so a restart resumes the same
 *  request), plus the refusal back-off that must survive a restart too. */
interface BootstrapStateStore {
    fun load(): BootstrapState?
    fun save(state: BootstrapState)
    fun discard()
}

data class BootstrapState(
    val requestId: String,
    val privateB64: String,
    val relayUrl: String,
    val staged: Boolean = false,
    /** Copies this device downloaded that were then refused (tampered, another shop, open drawer ...). */
    val refusals: Int = 0,
    /** Do not download again before this wall-clock instant (exponential back-off after a refusal). */
    val notBeforeMillis: Long = 0L,
)

class FileBootstrapStateStore(private val file: File) : BootstrapStateStore {
    override fun load(): BootstrapState? = try {
        val o = JsonParser.parseString(file.readText()).asJsonObject
        BootstrapState(
            o.get("request_id").asString, o.get("private_b64").asString, o.get("relay_url").asString,
            o.get("staged")?.asBoolean ?: false, o.get("refusals")?.asInt ?: 0, o.get("not_before")?.asLong ?: 0L)
    } catch (exc: Exception) {
        null
    }

    override fun save(state: BootstrapState) {
        val o = JsonObject()
        o.addProperty("request_id", state.requestId)
        o.addProperty("private_b64", state.privateB64)
        o.addProperty("relay_url", state.relayUrl)
        o.addProperty("staged", state.staged)
        o.addProperty("refusals", state.refusals)
        o.addProperty("not_before", state.notBeforeMillis)
        file.parentFile?.mkdirs()
        val tmp = File(file.path + ".tmp")
        tmp.writeText(o.toString())
        if (!tmp.renameTo(file)) { file.delete(); tmp.renameTo(file) }
    }

    override fun discard() { file.delete() }
}

enum class RequesterOutcome {
    /** Asked Owner for a copy this step. */
    REQUESTED,
    /** Asked earlier; no producer has finished yet. */
    WAITING,
    /** A copy is staged (now, or already was): the app must restart to install it. */
    STAGED,
    /** The request is gone (expired / forgotten); the next step starts over. */
    RETRY,
    /** The backend cannot take a copy right now (open cash drawer): nothing requested, nothing downloaded. */
    BLOCKED,
    /** A copy was refused recently; waiting out the back-off before downloading again. */
    BACKING_OFF,
    /** Too many installs failed: no more requests until a person clears it. */
    NEEDS_SUPPORT,
}

/** One unit of requester progress. The coordinator depends on this, not on the concrete class, so its
 *  restart wiring can be tested without a mailbox. */
fun interface BootstrapStepper {
    fun step(): RequesterOutcome
}

/**
 * Android's requester for a copy of the shop (SYNC-BOOT Phase 3), the Kotlin twin of
 * `transport_owner.OwnerRequester`: make a single-use key, ask Owner's mailbox, WAIT (a producer is
 * another computer in the shop), download every chunk checking each hash, decrypt, hand the file to Python
 * to verify and stage, then ack so Owner deletes its bytes. Install happens at the next start. Not a
 * producer: a phone never builds a copy.
 *
 * Guards, each tested:
 *  - STREAMING: chunk -> sha256 -> decrypt -> [CopySink] -> next chunk. Peak memory is one chunk, never the copy.
 *  - ASK THE BACKEND FIRST: an open cash drawer refuses a copy at staging, so nothing is requested or
 *    downloaded while one is open; an already-staged copy is never re-requested (this also closes the crash
 *    window between staging and recording it).
 *  - REFUSAL BACK-OFF: a downloaded copy that is refused (tampered, another shop, ...) is not fetched again
 *    at once: 1, 2, 4 ... 30 minutes, persisted.
 *  - INSTALL CAP: when the backend reports too many failed installs, no more requests (NEEDS_SUPPORT).
 * The ack follows the STAGING, not the install: the staged file on this device is the durable copy.
 */
class BootstrapRequester(
    private val transport: MailboxTransport,
    private val stager: BootstrapStager,
    private val store: BootstrapStateStore,
    private val relayUrl: String,
    private val appVersion: String? = null,
    /** Injectable only so a test can use a key pair whose ciphertext it already holds. */
    private val newKeys: () -> RequesterKeys = BootstrapCrypto::newRequesterKeys,
    private val sinkFactory: CopySinkFactory,
    private val clock: () -> Long = { System.currentTimeMillis() },
) : BootstrapStepper {

    override fun step(): RequesterOutcome {
        val info = stager.info()
        if (info.needsSupport) return RequesterOutcome.NEEDS_SUPPORT
        var state = store.load()?.takeIf { it.relayUrl == relayUrl }
        if (info.pendingStaged) {
            // A copy is already waiting for the next start -- including the case where the app died between
            // staging it and writing "staged" below. Asking for another would waste a producer's effort and
            // could stage over a good copy. Tell Owner it can drop its bytes (best effort), forget the key.
            if (state != null) {
                try { transport.ack(state.requestId) } catch (exc: Exception) { /* Owner sweeps it in 72 h */ }
                store.discard()
            }
            return RequesterOutcome.STAGED
        }
        if (!info.canStage) return RequesterOutcome.BLOCKED
        if (state != null && state.notBeforeMillis > clock()) return RequesterOutcome.BACKING_OFF

        val view: JsonObject
        if (state == null) {
            val keys = newKeys()
            view = transport.request(keys.publicB64, info.schemaVersion, appVersion)
            state = BootstrapState(view.get("request_id").asString, keys.privateB64, relayUrl)
            store.save(state)
            if (view.get("status")?.asString == "OPEN") return RequesterOutcome.REQUESTED
        } else {
            view = try {
                transport.status(state.requestId)
            } catch (exc: MailboxNotFound) {
                store.discard()
                return RequesterOutcome.RETRY
            }
        }
        return when (view.get("status")?.asString) {
            "OPEN", "CLAIMED" -> RequesterOutcome.WAITING
            "READY" -> fetchAndStage(state, view)
            "DELIVERED", "FULFILLED" -> {
                if (state.staged) { store.discard(); RequesterOutcome.STAGED }
                else { store.discard(); RequesterOutcome.RETRY }
            }
            else -> { store.discard(); RequesterOutcome.RETRY }          // EXPIRED / FAILED: start over
        }
    }

    private fun fetchAndStage(state: BootstrapState, view: JsonObject): RequesterOutcome {
        val sink = sinkFactory.open()
        val path: String
        try {
            path = downloadInto(sink, state, view)
            stager.stageFile(path, relayUrl)                  // throws BootstrapRefused: nothing staged, state kept
        } catch (exc: Exception) {
            sink.abort()
            if (exc is BootstrapCryptoError || exc is BootstrapRefused) recordRefusal(state)
            throw exc
        }
        store.save(state.copy(staged = true, privateB64 = "", refusals = 0, notBeforeMillis = 0L))
        try {
            transport.ack(state.requestId)
        } catch (exc: Exception) {
            // Staged is what matters; an unacked blob is swept by Owner after 72 hours.
        }
        store.discard()
        return RequesterOutcome.STAGED
    }

    /** Streams the copy into [sink] one chunk at a time and returns the finished file's path. */
    private fun downloadInto(sink: CopySink, state: BootstrapState, view: JsonObject): String {
        val manifest = view.getAsJsonObject("manifest") ?: throw BootstrapCryptoError("Owner's answer has no manifest")
        val params = manifest.getAsJsonObject("encryption") ?: throw BootstrapCryptoError("Owner's manifest has no encryption parameters")
        val hashes = manifest.getAsJsonArray("chunk_sha256") ?: throw BootstrapCryptoError("Owner's manifest has no chunk hashes")
        val count = manifest.get("chunk_count")?.asInt ?: 0
        if (count < 1 || hashes.size() != count) throw BootstrapCryptoError("Owner's manifest names no usable chunk list")
        val decryptor = BootstrapCrypto.ChunkDecryptor(state.privateB64, params, state.requestId)
        if (decryptor.total != count) throw BootstrapCryptoError("the chunk count and the encryption parameters disagree")
        var written = 0L
        for (i in 0 until count) {
            val answer = transport.downloadChunk(state.requestId, i)
            val blob = Base64.getDecoder().decode(answer.get("data")?.asString ?: "")
            val digest = sha256Hex(blob)
            // Owner's own hash and the one recorded when the producer finished: a chunk altered on the
            // mailbox disk or in transit stops here, naming the chunk (GCM would also catch it).
            if (digest != answer.get("sha256")?.asString || digest != hashes[i].asString) {
                throw BootstrapCryptoError("chunk $i does not match its recorded hash")
            }
            val plain = decryptor.decryptChunk(i, blob)
            sink.write(plain)
            written += plain.size
        }
        val declared = params.get("plaintext_size")?.asLong
        if (declared != null && declared != written) throw BootstrapCryptoError("the decrypted size does not match the declared size")
        return sink.finish()
    }

    /** 1, 2, 4 ... capped at 30 minutes; persisted so a restart cannot reset it. */
    private fun recordRefusal(state: BootstrapState) {
        val refusals = state.refusals + 1
        val minutes = minOf(MAX_BACKOFF_MINUTES, 1L shl minOf(refusals - 1, 20))
        store.save(state.copy(refusals = refusals, notBeforeMillis = clock() + minutes * 60_000L))
    }

    private fun sha256Hex(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    companion object {
        const val MAX_BACKOFF_MINUTES = 30L
    }
}
