package com.actionaura.retail.sync

import com.google.gson.JsonObject
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

/** The embedded Python backend's `/_internal/bootstrap/...` seam. */
interface BootstrapStager {
    fun schemaVersion(): Int
    /** Verify and park a decrypted copy for the next start. @throws BootstrapRefused with Python's reason code. */
    fun stage(zip: ByteArray, relayUrl: String)
}

class BootstrapRefused(val reasonCode: String, message: String) : Exception(message)

/** Small persisted record: the request id and the SINGLE-USE private key, so a restart resumes the same request. */
interface BootstrapStateStore {
    fun load(): BootstrapState?
    fun save(state: BootstrapState)
    fun discard()
}

data class BootstrapState(val requestId: String, val privateB64: String, val relayUrl: String, val staged: Boolean = false)

class FileBootstrapStateStore(private val file: File) : BootstrapStateStore {
    override fun load(): BootstrapState? = try {
        val o = com.google.gson.JsonParser.parseString(file.readText()).asJsonObject
        BootstrapState(
            o.get("request_id").asString, o.get("private_b64").asString, o.get("relay_url").asString,
            o.get("staged")?.asBoolean ?: false)
    } catch (exc: Exception) {
        null
    }

    override fun save(state: BootstrapState) {
        val o = JsonObject()
        o.addProperty("request_id", state.requestId)
        o.addProperty("private_b64", state.privateB64)
        o.addProperty("relay_url", state.relayUrl)
        o.addProperty("staged", state.staged)
        file.parentFile?.mkdirs()
        val tmp = File(file.path + ".tmp")
        tmp.writeText(o.toString())
        if (!tmp.renameTo(file)) { file.delete(); tmp.renameTo(file) }
    }

    override fun discard() { file.delete() }
}

enum class RequesterOutcome { REQUESTED, WAITING, STAGED, RETRY }

/**
 * Android's requester for a copy of the shop (SYNC-BOOT Phase 3), the Kotlin twin of
 * `transport_owner.OwnerRequester`: make a single-use key, ask Owner's mailbox, WAIT (a producer is
 * another computer in the shop), download every chunk checking each hash, decrypt, hand the zip to
 * Python to verify and stage, then ack so Owner deletes its bytes. Install happens at the next start.
 * Not a producer: a phone never builds a copy.
 *
 * [step] does at most one unit of progress and never blocks on a peer; the coordinator calls it once per
 * (throttled) tick while the retail stream is stopped. The ack follows the STAGING, not the install: the
 * staged file on this device is the durable copy from then on.
 */
class BootstrapRequester(
    private val transport: MailboxTransport,
    private val stager: BootstrapStager,
    private val store: BootstrapStateStore,
    private val relayUrl: String,
    private val appVersion: String? = null,
    /** Injectable only so a test can use a key pair whose ciphertext it already holds. */
    private val newKeys: () -> RequesterKeys = BootstrapCrypto::newRequesterKeys,
) {
    fun step(): RequesterOutcome {
        var state = store.load()?.takeIf { it.relayUrl == relayUrl }
        val view: JsonObject
        if (state == null) {
            val keys = newKeys()
            view = transport.request(keys.publicB64, stager.schemaVersion(), appVersion)
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
        val manifest = view.getAsJsonObject("manifest") ?: throw BootstrapCryptoError("Owner's answer has no manifest")
        val params = manifest.getAsJsonObject("encryption") ?: throw BootstrapCryptoError("Owner's manifest has no encryption parameters")
        val hashes = manifest.getAsJsonArray("chunk_sha256") ?: throw BootstrapCryptoError("Owner's manifest has no chunk hashes")
        val count = manifest.get("chunk_count")?.asInt ?: 0
        if (count < 1 || hashes.size() != count) throw BootstrapCryptoError("Owner's manifest names no usable chunk list")
        val decryptor = BootstrapCrypto.ChunkDecryptor(state.privateB64, params, state.requestId)
        if (decryptor.total != count) throw BootstrapCryptoError("the chunk count and the encryption parameters disagree")
        val out = java.io.ByteArrayOutputStream()
        for (i in 0 until count) {
            val answer = transport.downloadChunk(state.requestId, i)
            val blob = Base64.getDecoder().decode(answer.get("data")?.asString ?: "")
            val digest = sha256Hex(blob)
            // Owner's own hash and the one recorded when the producer finished: a chunk altered on the
            // mailbox disk or in transit stops here, naming the chunk (GCM would also catch it).
            if (digest != answer.get("sha256")?.asString || digest != hashes[i].asString) {
                throw BootstrapCryptoError("chunk $i does not match its recorded hash")
            }
            out.write(decryptor.decryptChunk(i, blob))
        }
        val zip = out.toByteArray()
        val declared = params.get("plaintext_size")?.asInt
        if (declared != null && declared != zip.size) throw BootstrapCryptoError("the decrypted size does not match the declared size")
        stager.stage(zip, relayUrl)                       // throws BootstrapRefused: nothing staged, state kept
        store.save(state.copy(staged = true, privateB64 = ""))
        try {
            transport.ack(state.requestId)
        } catch (exc: Exception) {
            // Staged is what matters; an unacked blob is swept by Owner after 72 hours.
        }
        store.discard()
        return RequesterOutcome.STAGED
    }

    private fun sha256Hex(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
}
