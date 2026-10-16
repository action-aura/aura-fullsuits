package com.actionaura.retail.sync

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.net.URLEncoder

/**
 * The production [MailboxTransport]: Owner's bootstrap-mailbox routes
 * (`owner/app/sync/bootstrap_routes.py`) through the SAME signed client as push and pull, so every call
 * carries the device's Ed25519 signature, a fresh nonce and timestamp. Android is a requester only: the
 * claim / chunk-upload / complete / fulfil routes are never called from here.
 */
class OwnerMailboxTransport(private val client: SyncRelayClient) : MailboxTransport {
    private val api = "/api/sync/v1/bootstrap"

    override fun request(encPubkey: String, schemaVersion: Int, appVersion: String?): JsonObject {
        val extra = linkedMapOf<String, Any?>(
            "stream" to "retail", "reason" to "resync", "enc_pubkey" to encPubkey, "schema_version" to schemaVersion)
        if (appVersion != null) extra["app_version"] = appVersion
        return client.postSigned("$api/request", extra)
    }

    override fun status(requestId: String): JsonObject = notFoundAware {
        client.postSigned("$api/status", linkedMapOf("request_id" to requestId))
    }

    override fun downloadChunk(requestId: String, index: Int): JsonObject = notFoundAware {
        client.postSigned("$api/download", linkedMapOf("request_id" to requestId, "index" to index))
    }

    override fun ack(requestId: String) {
        client.postSigned("$api/ack", linkedMapOf("request_id" to requestId))
    }

    private fun <T> notFoundAware(block: () -> T): T = try {
        block()
    } catch (exc: SyncRelayRejected) {
        if (exc.reasonCode == "BOOTSTRAP_NOT_FOUND") throw MailboxNotFound()
        throw exc
    }
}

/** The production [BootstrapStager]: the embedded Python backend's `/_internal/bootstrap/...` routes. */
class LocalBootstrapStager(
    private val localBaseUrl: String,
    private val internalSecret: String,
    private val http: OkHttpClient,
    private val streamPrefix: String,
) : BootstrapStager {
    private fun url(path: String) = localBaseUrl.trimEnd('/') + streamPrefix + path

    override fun schemaVersion(): Int {
        val request = Request.Builder().url(url("/_internal/bootstrap/info"))
            .header("X-Aura-Internal-Secret", internalSecret).get().build()
        http.newCall(request).execute().use { response ->
            val body = response.body?.string() ?: ""
            if (!response.isSuccessful) throw BootstrapRefused("LOCAL_ERROR", "bootstrap info returned HTTP ${response.code}")
            return JsonParser.parseString(body).asJsonObject.get("schema_version").asInt
        }
    }

    override fun stage(zip: ByteArray, relayUrl: String) {
        val request = Request.Builder()
            .url(url("/_internal/bootstrap/stage?relay_url=" + URLEncoder.encode(relayUrl, "UTF-8")))
            .header("X-Aura-Internal-Secret", internalSecret)
            .post(zip.toRequestBody("application/octet-stream".toMediaType()))
            .build()
        // Verifying and parking a whole shop (hash, write, integrity check) outlasts the 15 s the sync
        // loop gives an ordinary local call.
        val slow = http.newBuilder().readTimeout(5, java.util.concurrent.TimeUnit.MINUTES).build()
        slow.newCall(request).execute().use { response ->
            val body = response.body?.string() ?: ""
            if (response.isSuccessful) return
            val reason = try {
                JsonParser.parseString(body).asJsonObject.get("reason_code")?.asString
            } catch (exc: Exception) {
                null
            }
            throw BootstrapRefused(reason ?: "LOCAL_ERROR", "the backend refused the copy (HTTP ${response.code})")
        }
    }
}
