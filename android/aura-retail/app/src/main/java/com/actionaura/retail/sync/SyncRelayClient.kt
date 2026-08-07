package com.actionaura.retail.sync

import com.actionaura.retail.licensing.DeviceIdentity
import com.actionaura.retail.licensing.canonicalizeBytes
import com.google.gson.Gson
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.BufferedReader
import java.io.IOException
import java.io.InputStreamReader
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException
import java.net.URI
import java.nio.charset.StandardCharsets
import java.security.SecureRandom
import java.time.Instant
import java.util.Base64
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLException
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory
import kotlin.random.Random

/**
 * Kotlin implementation of the Task 5 (`commercial_runtime/sync/relay_client.py`
 * `SyncRelayClient`) wire contract on Android, mirroring [com.actionaura.retail
 * .licensing.OwnerClient]'s shape exactly (same retry/backoff/timeout policy,
 * same canonicalization approach) for the same reason OwnerClient exists at
 * all: Android's embedded Python backend never holds the device's private
 * key (`AndroidBridgeDeviceIdentityProvider.sign()` always raises -- see
 * that class's docstring), so this is the ONLY place on Android that ever
 * makes a signed HTTP call to Owner's `/api/sync/v1/push|pull`, exactly
 * like OwnerClient is "the ONLY place on Android that ever makes a signed
 * HTTP call to Owner" for licensing.
 *
 * Wire contract (read directly from `owner/app/sync/routes.py`, matching
 * `SyncRelayClient` on desktop byte-for-byte): every push/pull body carries
 * `installation_id`, a fresh `timestamp`, a fresh unique `nonce`, and a
 * `signature` computed over canonicalize_bytes(body minus "signature").
 * `since` lives INSIDE pull's signed body, never a `?since=` query
 * parameter (see routes.py's module docstring on why a free query param
 * would be a replay hole). On any auth/validation rejection the relay
 * returns HTTP 400 with `{"reason_code": "..."}`; a 500 is a genuine
 * server-side fault.
 *
 * Pull is a real HTTP GET that ALSO carries a signed JSON body (Flask's
 * `request.get_json()` does not care about method). OkHttp's own
 * `Request.Builder` structurally forbids a body on GET/HEAD
 * (`HttpMethod.permitsRequestBody` hard-codes this; confirmed against
 * OkHttp 4.12.0's own source) -- there is no supported way to build that
 * request through OkHttp. `pull()` therefore uses plain
 * `java.net.HttpURLConnection` (which has no such restriction --
 * `setRequestMethod("GET")` + `doOutput = true` is the standard, documented
 * way to send a body on a non-standard GET, the same technique commonly
 * used for Elasticsearch's `_mget`/`_msearch` APIs) for this one call only;
 * push (a real POST) uses OkHttp like everything else in this app.
 */

private const val NONCE_LENGTH_BYTES = 24
private val RETRYABLE_STATUS_CODES = setOf(429, 500, 502, 503, 504)

data class SyncRelayClientConfig(
    val baseUrl: String,
    val timeoutSeconds: Long = 10,
    val maxRetries: Int = 4,
    val retryBaseBackoffMillis: Long = 1000,
    val retryMaxBackoffMillis: Long = 30000,
)

open class SyncRelayClientError(val reasonCode: String, message: String) : Exception(message)
class SyncNetworkError(reasonCode: String, message: String) : SyncRelayClientError(reasonCode, message)
class SyncMalformedResponseError(reasonCode: String, message: String) : SyncRelayClientError(reasonCode, message)
class SyncRelayRejected(reasonCode: String, message: String) : SyncRelayClientError(reasonCode, message)

/** Converts a parsed [JsonElement] into exactly the value shapes
 * `com.actionaura.retail.licensing.canonicalize()` accepts (String, Boolean,
 * Int/Long, Double, Map<String, Any?>, List<Any?>, null) WITHOUT going
 * through Gson's generic `Map<String, Any?>` object deserialization --
 * that path collapses every JSON number to Double (see OwnerClient.kt's own
 * doc comment on this exact failure mode), which would silently turn an
 * integer field like a category's `company_id` into `1.0` the moment it's
 * re-canonicalized for signing. Whole-number JSON literals (no `.`/`e`/`E`
 * in their source text) become Long here; everything else becomes Double --
 * preserving the exact int/float distinction the original JSON text had. */
internal fun jsonToCanonical(element: JsonElement): Any? = when {
    element.isJsonNull -> null
    element.isJsonObject -> {
        val out = LinkedHashMap<String, Any?>()
        for ((k, v) in element.asJsonObject.entrySet()) out[k] = jsonToCanonical(v)
        out
    }
    element.isJsonArray -> element.asJsonArray.map { jsonToCanonical(it) }
    element.isJsonPrimitive -> {
        val p = element.asJsonPrimitive
        when {
            p.isBoolean -> p.asBoolean
            p.isString -> p.asString
            p.isNumber -> {
                val raw = p.asString
                if (raw.none { it == '.' || it == 'e' || it == 'E' }) {
                    raw.toLongOrNull() ?: p.asDouble
                } else {
                    p.asDouble
                }
            }
            else -> null
        }
    }
    else -> null
}

class SyncRelayClient(
    private val config: SyncRelayClientConfig,
    private val identity: DeviceIdentity,
    private val installationId: String,
    httpClient: OkHttpClient? = null,
    private val sleepFn: (Long) -> Unit = { Thread.sleep(it) },
) {
    private val http: OkHttpClient = httpClient ?: OkHttpClient.Builder()
        .connectTimeout(config.timeoutSeconds, TimeUnit.SECONDS)
        .readTimeout(config.timeoutSeconds, TimeUnit.SECONDS)
        .build()

    /** `events` are already-converted canonical values (see [jsonToCanonical])
     * -- callers must not pass a Gson-`Map<String, Any?>`-deserialized batch,
     * or numeric fidelity is lost before it ever reaches this class. */
    fun push(events: List<Any?>): JsonObject {
        val body = signedBody(linkedMapOf("events" to events))
        val result = requestJson("POST", "/api/sync/v1/push", body)
        raiseIfRejected(result)
        return result
    }

    fun pull(since: Long): JsonObject {
        val body = signedBody(linkedMapOf("since" to since))
        val result = requestJson("GET", "/api/sync/v1/pull", body)
        raiseIfRejected(result)
        return result
    }

    private fun signedBody(extra: LinkedHashMap<String, Any?>): LinkedHashMap<String, Any?> {
        val body = linkedMapOf<String, Any?>(
            "installation_id" to installationId,
            "timestamp" to Instant.now().toString(),
            "nonce" to newNonce(),
        )
        body.putAll(extra)
        val canonicalBytes = canonicalizeBytes(body)
        body["signature"] = identity.sign(canonicalBytes)
        return body
    }

    private fun raiseIfRejected(result: JsonObject) {
        val reasonCode = result.get("reason_code")?.takeUnless { it.isJsonNull }?.asString
        if (reasonCode != null) {
            throw SyncRelayRejected(reasonCode, "Sync relay rejected the request: $reasonCode")
        }
    }

    private fun requestJson(method: String, path: String, body: Map<String, Any?>): JsonObject {
        val bodyJson = canonicalStructureToJsonString(body)
        val url = config.baseUrl.trimEnd('/') + path
        var lastError: SyncRelayClientError? = null

        for (attempt in 0..config.maxRetries) {
            if (attempt > 0) {
                val delay = config.retryBaseBackoffMillis * (1L shl (attempt - 1))
                val jitter = (delay * 0.25 * Random.nextDouble()).toLong()
                sleepFn(minOf(delay + jitter, config.retryMaxBackoffMillis))
            }

            val outcome: RequestOutcome = try {
                val (status, retryAfterHeader, responseBody) = if (method == "GET") {
                    executeGetWithBody(url, bodyJson)
                } else {
                    executePost(url, bodyJson)
                }
                if (status in RETRYABLE_STATUS_CODES) {
                    val retryAfterSeconds = retryAfterHeader?.toDoubleOrNull()
                    if (retryAfterSeconds != null) {
                        sleepFn(minOf((retryAfterSeconds * 1000).toLong(), config.retryMaxBackoffMillis))
                    }
                    val reasonCode = if (status == 429) "RATE_LIMITED" else "SERVICE_TEMPORARILY_UNAVAILABLE"
                    RequestOutcome.Retry(SyncNetworkError(reasonCode, "HTTP $status"))
                } else {
                    RequestOutcome.Success(responseBody)
                }
            } catch (exc: SocketTimeoutException) {
                RequestOutcome.Retry(SyncNetworkError("REQUEST_TIMED_OUT", exc.message ?: "timeout"))
            } catch (exc: SSLException) {
                // Never retried -- a TLS failure is not transient in the
                // sense that matters here (see OwnerClient.requestRaw()).
                throw SyncNetworkError("TLS_VERIFICATION_FAILED", exc.message ?: "TLS error")
            } catch (exc: IOException) {
                RequestOutcome.Retry(SyncNetworkError("NETWORK_UNAVAILABLE", exc.message ?: "network error"))
            }

            when (outcome) {
                is RequestOutcome.Success -> {
                    return try {
                        JsonParser.parseString(outcome.body).asJsonObject
                    } catch (exc: Exception) {
                        throw SyncMalformedResponseError("MALFORMED_RESPONSE", "Response was not a valid JSON object: ${exc.message}")
                    }
                }
                is RequestOutcome.Retry -> lastError = outcome.error
            }
        }

        throw lastError ?: SyncNetworkError("NETWORK_UNAVAILABLE", "Request failed with no captured error.")
    }

    private data class HttpResult(val status: Int, val retryAfter: String?, val body: String)

    private fun executePost(url: String, bodyJson: String): HttpResult {
        val request = Request.Builder()
            .url(url)
            .post(bodyJson.toRequestBody("application/json".toMediaType()))
            .build()
        http.newCall(request).execute().use { response ->
            val bodyString = response.body?.string() ?: ""
            return HttpResult(response.code, response.header("Retry-After"), bodyString)
        }
    }

    /** Real HTTP GET carrying a JSON body -- see the class doc comment for
     * why OkHttp cannot express this. `java.net.HttpURLConnection` (Android's
     * built-in implementation, not the same codebase as desktop/server
     * JVMs') was tried first and rejected: it silently rewrites the request
     * method to POST the instant `getOutputStream()` is touched, REGARDLESS
     * of `requestMethod` having been set to "GET" first (confirmed via
     * physical device testing -- `conn.requestMethod` read back "POST"
     * immediately after writing the body, and Owner's Werkzeug correctly
     * rejected the resulting real POST with 405 Method Not Allowed against
     * the GET-only route). There is no supported way to force a body onto a
     * GET through Android's HttpURLConnection.
     *
     * This method instead speaks raw HTTP/1.1 over a plain `Socket` (wrapped
     * in an `SSLSocket` for https), giving full control over the request
     * line -- the only way to put a literal "GET" on the wire together with
     * a body. `Connection: close` is always sent so the body can simply be
     * read until EOF, without needing to parse chunked transfer-encoding. */
    private fun executeGetWithBody(url: String, bodyJson: String): HttpResult {
        val uri = URI(url)
        val isHttps = uri.scheme == "https"
        val host = uri.host
        val port = if (uri.port != -1) uri.port else if (isHttps) 443 else 80
        val pathAndQuery = uri.rawPath.ifEmpty { "/" } + (uri.rawQuery?.let { "?$it" } ?: "")
        val bodyBytes = bodyJson.toByteArray(StandardCharsets.UTF_8)
        val timeoutMillis = (config.timeoutSeconds * 1000).toInt()

        val plainSocket = Socket()
        plainSocket.connect(InetSocketAddress(host, port), timeoutMillis)
        plainSocket.soTimeout = timeoutMillis
        val socket: Socket = if (isHttps) {
            val sslFactory = SSLSocketFactory.getDefault() as SSLSocketFactory
            (sslFactory.createSocket(plainSocket, host, port, true) as SSLSocket).also {
                it.soTimeout = timeoutMillis
                it.startHandshake()
            }
        } else {
            plainSocket
        }

        try {
            val requestHead = buildString {
                append("GET ").append(pathAndQuery).append(" HTTP/1.1\r\n")
                append("Host: ").append(host).append(if (uri.port != -1) ":${uri.port}" else "").append("\r\n")
                append("Content-Type: application/json\r\n")
                append("Content-Length: ").append(bodyBytes.size).append("\r\n")
                append("Connection: close\r\n")
                append("\r\n")
            }
            val out = socket.getOutputStream()
            out.write(requestHead.toByteArray(StandardCharsets.US_ASCII))
            out.write(bodyBytes)
            out.flush()

            val input = BufferedReader(InputStreamReader(socket.getInputStream(), StandardCharsets.UTF_8))
            val statusLine = input.readLine() ?: throw IOException("Empty response from server")
            val status = statusLine.split(" ").getOrNull(1)?.toIntOrNull()
                ?: throw IOException("Malformed HTTP status line: $statusLine")

            var retryAfter: String? = null
            var headerLine = input.readLine()
            while (!headerLine.isNullOrEmpty()) {
                val idx = headerLine.indexOf(':')
                if (idx > 0 && headerLine.substring(0, idx).equals("Retry-After", ignoreCase = true)) {
                    retryAfter = headerLine.substring(idx + 1).trim()
                }
                headerLine = input.readLine()
            }

            val bodyBuilder = StringBuilder()
            val buffer = CharArray(4096)
            while (true) {
                val n = input.read(buffer)
                if (n == -1) break
                bodyBuilder.append(buffer, 0, n)
            }
            return HttpResult(status, retryAfter, bodyBuilder.toString())
        } finally {
            socket.close()
        }
    }

    private sealed class RequestOutcome {
        data class Success(val body: String) : RequestOutcome()
        data class Retry(val error: SyncNetworkError) : RequestOutcome()
    }

    private fun newNonce(): String {
        val bytes = ByteArray(NONCE_LENGTH_BYTES)
        SecureRandom().nextBytes(bytes)
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    }
}

/** Serializes a canonical-compatible structure (String/Boolean/Int/Long/
 * Double/Map/List/null -- the same shapes [canonicalizeBytes] accepts) to a
 * JSON string for the actual HTTP request body. Deliberately NOT
 * `Gson().toJson(map)` on the raw map -- Gson has no way to tell an
 * intentional `Long` apart from a `Double` it should format with a decimal
 * point when walking a raw `Map<String, Any?>` via reflection in every
 * case relevant here, so this walks the exact same structure
 * `canonicalize()` (Canonical.kt) already validated, byte-compatible with
 * how that function would render it (though this is the WIRE body, not the
 * signed bytes -- the signature itself always comes from
 * [canonicalizeBytes] directly, never from this function). */
private val wireGson = Gson()

internal fun canonicalStructureToJsonString(value: Any?): String {
    val sb = StringBuilder()
    fun write(v: Any?) {
        when (v) {
            null -> sb.append("null")
            is String -> sb.append(wireGson.toJson(v))
            is Boolean -> sb.append(v.toString())
            is Int -> sb.append(v.toString())
            is Long -> sb.append(v.toString())
            is Double -> sb.append(v.toString())
            is Map<*, *> -> {
                sb.append('{')
                var first = true
                for ((k, vv) in v) {
                    if (!first) sb.append(',')
                    first = false
                    sb.append(wireGson.toJson(k.toString()))
                    sb.append(':')
                    write(vv)
                }
                sb.append('}')
            }
            is List<*> -> {
                sb.append('[')
                var first = true
                for (item in v) {
                    if (!first) sb.append(',')
                    first = false
                    write(item)
                }
                sb.append(']')
            }
            else -> sb.append(wireGson.toJson(v))
        }
    }
    write(value)
    return sb.toString()
}
