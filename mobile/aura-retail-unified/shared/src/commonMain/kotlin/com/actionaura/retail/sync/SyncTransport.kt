package com.actionaura.retail.sync

import com.actionaura.retail.licensing.lease.LeaseCanonicalJson
import com.actionaura.retail.licensing.transport.secureRandomHex
import io.ktor.client.HttpClient
import io.ktor.client.plugins.HttpRequestTimeoutException
import io.ktor.client.plugins.HttpTimeout
import io.ktor.client.request.get
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.client.statement.HttpResponse
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.HttpStatusCode
import io.ktor.http.contentType
import kotlinx.coroutines.CancellationException
import kotlinx.datetime.Clock
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlin.io.encoding.Base64
import kotlin.io.encoding.ExperimentalEncodingApi

/**
 * Task 9 (multi-device-sync-foundation) -- the real, net-new Ktor-backed
 * client for Owner's multi-device sync relay (`owner/app/sync/routes.py`,
 * Task 2). A direct sibling of
 * [com.actionaura.retail.licensing.transport.HttpExternalLicensingTransport]
 * (Task 8) -- same `HttpClient`-derivation-with-its-own-[HttpTimeout]
 * pattern, same [LeaseCanonicalJson]-based signing via [DeviceSigner], same
 * manual JSON construction/decoding (no `ContentNegotiation` plugin
 * installed, matching that class's own established choice), same
 * TLS/network/timeout exception classification -- deliberately not a
 * reinvention.
 *
 * Wire contract (corrected against the real, unmodified
 * `owner/app/sync/routes.py`, not the plan's stale draft):
 * - `POST {baseUrl}/api/sync/v1/push` -- body
 *   `{installation_id, timestamp, nonce, events, signature}`, response
 *   `{"stored": N, "received": N}` (200) or `{"reason_code": "..."}`
 *   (400/500).
 * - `GET {baseUrl}/api/sync/v1/pull` -- a GET request carrying a signed
 *   JSON body `{installation_id, timestamp, nonce, since, signature}`
 *   (`since` lives INSIDE the signed body, never a `?since=` query
 *   parameter -- routes.py's own module docstring explains why a free
 *   query param would let a captured pull request be replayed with a
 *   different `since`). Response `{"events": [...], "cursor": N}` (200).
 *
 * **[baseHttpClient]'s engine MUST support sending a body on a GET
 * request -- Ktor's `OkHttp` engine (Task 8's own choice for
 * `HttpExternalLicensingTransport`, POST-only) does NOT and must never be
 * used here.** Real, disassembly- and live-verified finding, not a
 * theoretical concern: `io.ktor.client.engine.okhttp.OkHttpEngineKt.convertToOkHttpRequest`
 * unconditionally passes `null` as the request body whenever
 * `okhttp3.internal.http.HttpMethod.permitsRequestBody(method)` is false
 * -- and OkHttp's own `permitsRequestBody("GET")` is `false` (OkHttp's
 * `Request.Builder` hard-rejects a GET-with-body: "method GET must not
 * have a request body"). The very first live run of this task's own
 * `SyncTransportLiveTest` against a real Owner instance, using
 * `HttpClient(OkHttp)`, reproduced this exactly: [pull] silently sent an
 * empty body and Owner rejected it with `INVALID_REQUEST` every time --
 * not a crash, a silent, always-wrong empty body. Fixed by using Ktor's
 * `CIO` engine (`io.ktor.client.engine.cio.CIO`, added to
 * `shared/build.gradle.kts`'s `androidMain`/`androidUnitTest` specifically
 * for this class) instead, which writes raw HTTP text directly and
 * carries no such restriction. Whoever wires this class into production
 * DI (Task 10) MUST construct its `HttpClient` with `CIO`, never reuse
 * the `OkHttp`-backed client `HttpExternalLicensingTransport` uses.
 *
 * Every push/pull call generates a FRESH `nonce`/`timestamp` inline in
 * [signedBody] -- never cached on `this`, never reused across calls,
 * matching Owner's own replay protection (`replay.consume_nonce`,
 * `nonce_scope="sync_push"`/`"sync_pull"` -- a nonce from one operation
 * can never be replayed against the other either).
 */
class SyncTransport(
    baseHttpClient: HttpClient,
    private val configuration: SyncRelayConfiguration,
    private val deviceSigner: DeviceSigner,
    private val installationId: String,
) {

    /**
     * A fresh derived client with THIS transport's own real timeout
     * policy installed -- never assumes the caller-supplied
     * [baseHttpClient] already carries [HttpTimeout], matching
     * `HttpExternalLicensingTransport`'s own established rationale.
     */
    private val httpClient: HttpClient = baseHttpClient.config {
        install(HttpTimeout) {
            requestTimeoutMillis = configuration.requestTimeoutMillis
            connectTimeoutMillis = configuration.connectTimeoutMillis
            socketTimeoutMillis = configuration.responseTimeoutMillis
        }
    }

    private val wireJson = Json { ignoreUnknownKeys = true; isLenient = false }

    suspend fun push(events: List<SyncEventEnvelope>): SyncTransportOutcome<PushResult> {
        val eventsArray = buildJsonArray {
            events.forEach { event ->
                add(
                    buildJsonObject {
                        put("id", event.id)
                        put("entity_type", event.entityType)
                        put("entity_id", event.entityId)
                        put("event_type", event.eventType)
                        put("payload", event.payload)
                        put("created_at", event.createdAt)
                    },
                )
            }
        }
        // Signing happens OUTSIDE the network try/catch below -- a
        // DeviceSigner failure (e.g. Task 7's real secure-store read
        // failure) propagates straight out of push(), never silently
        // reinterpreted as a network/TLS failure. Matches
        // HttpExternalLicensingTransport's own established control flow.
        val fullBody = signedBody(buildJsonObject { put("events", eventsArray) })

        return try {
            val response = httpClient.post(pushUrl()) {
                contentType(ContentType.Application.Json)
                setBody(fullBody.toString())
            }
            interpretPushResponse(response)
        } catch (e: CancellationException) {
            throw e
        } catch (e: HttpRequestTimeoutException) {
            SyncTransportOutcome.Timeout
        } catch (e: Exception) {
            classifyTransportException(e)
        }
    }

    suspend fun pull(since: Long): SyncTransportOutcome<PullResult> {
        val fullBody = signedBody(buildJsonObject { put("since", since) })

        return try {
            // A real GET request carrying a JSON body -- unusual but
            // exactly what `owner/app/sync/routes.py::pull()` expects
            // (`request.get_json()` does not restrict itself by HTTP
            // method). Ktor's HttpRequestBuilder does not forbid a body
            // on GET; the real relay round-trip in this task's own live
            // verification is what actually proves the underlying engine
            // (OkHttp on Android) sends it.
            val response = httpClient.get(pullUrl()) {
                contentType(ContentType.Application.Json)
                setBody(fullBody.toString())
            }
            interpretPullResponse(response)
        } catch (e: CancellationException) {
            throw e
        } catch (e: HttpRequestTimeoutException) {
            SyncTransportOutcome.Timeout
        } catch (e: Exception) {
            classifyTransportException(e)
        }
    }

    /**
     * Builds the full signed request body: `installation_id`/`timestamp`/
     * `nonce` (always fresh) plus [extraFields] (`events` for push,
     * `since` for pull), canonicalizes everything except `signature` via
     * [LeaseCanonicalJson] (the same verified byte-for-byte Kotlin port of
     * Owner's `canonicalize_bytes()` Task 8 already proved correct for
     * flat licensing payloads -- this task's own cross-language test
     * additionally proves it for THIS shape: an integer `since` field and
     * an `events` array of nested objects, see
     * `SyncCanonicalizationCrossCheckTest`), signs the canonical bytes via
     * [deviceSigner], and returns the complete signed body.
     */
    private suspend fun signedBody(extraFields: JsonObject): JsonObject {
        val signable = buildJsonObject {
            put("installation_id", installationId)
            put("timestamp", Clock.System.now().toString())
            put("nonce", secureRandomHex(24))
            extraFields.forEach { (key, value) -> put(key, value) }
        }
        val canonicalBytes = LeaseCanonicalJson.canonicalizeBytes(signable)
        val signatureB64 = encodeBase64(deviceSigner.sign(canonicalBytes))
        return JsonObject(signable + ("signature" to JsonPrimitive(signatureB64)))
    }

    private fun pushUrl(): String = configuration.baseUrl.trimEnd('/') + "/api/sync/v1/push"
    private fun pullUrl(): String = configuration.baseUrl.trimEnd('/') + "/api/sync/v1/pull"

    private suspend fun interpretPushResponse(response: HttpResponse): SyncTransportOutcome<PushResult> {
        val rawBody = try {
            response.bodyAsText()
        } catch (e: Exception) {
            return SyncTransportOutcome.MalformedResponse("failed to read push response body: ${e::class.simpleName}")
        }

        val decoded = try {
            wireJson.decodeFromString(PushWireResponse.serializer(), rawBody)
        } catch (e: SerializationException) {
            return SyncTransportOutcome.MalformedResponse("failed to decode push response JSON: ${e::class.simpleName}")
        } catch (e: IllegalArgumentException) {
            return SyncTransportOutcome.MalformedResponse("failed to decode push response JSON: ${e::class.simpleName}")
        }

        // `reason_code` is present on every real rejection body
        // (`_error()`, routes.py) and absent from every real success body
        // -- checked first so a 400/500 is never mistaken for success.
        if (decoded.reasonCode != null) {
            return SyncTransportOutcome.Rejected(decoded.reasonCode, response.status.value)
        }
        return if (response.status == HttpStatusCode.OK && decoded.stored != null && decoded.received != null) {
            SyncTransportOutcome.Success(PushResult(stored = decoded.stored, received = decoded.received))
        } else {
            SyncTransportOutcome.MalformedResponse(
                "unrecognized push response shape: httpStatus=${response.status.value}, stored=${decoded.stored}, received=${decoded.received}",
            )
        }
    }

    private suspend fun interpretPullResponse(response: HttpResponse): SyncTransportOutcome<PullResult> {
        val rawBody = try {
            response.bodyAsText()
        } catch (e: Exception) {
            return SyncTransportOutcome.MalformedResponse("failed to read pull response body: ${e::class.simpleName}")
        }

        val decoded = try {
            wireJson.decodeFromString(PullWireResponse.serializer(), rawBody)
        } catch (e: SerializationException) {
            return SyncTransportOutcome.MalformedResponse("failed to decode pull response JSON: ${e::class.simpleName}")
        } catch (e: IllegalArgumentException) {
            return SyncTransportOutcome.MalformedResponse("failed to decode pull response JSON: ${e::class.simpleName}")
        }

        if (decoded.reasonCode != null) {
            return SyncTransportOutcome.Rejected(decoded.reasonCode, response.status.value)
        }
        return if (response.status == HttpStatusCode.OK && decoded.events != null && decoded.cursor != null) {
            SyncTransportOutcome.Success(
                PullResult(
                    events = decoded.events.map {
                        PulledSyncEvent(
                            id = it.id,
                            entityType = it.entityType,
                            entityId = it.entityId,
                            eventType = it.eventType,
                            payload = it.payload,
                            createdAt = it.createdAt,
                            seq = it.seq,
                        )
                    },
                    cursor = decoded.cursor,
                ),
            )
        } else {
            SyncTransportOutcome.MalformedResponse("unrecognized pull response shape: httpStatus=${response.status.value}")
        }
    }

    /** Same string-matching heuristic `HttpExternalLicensingTransport.classifyTransportException` uses -- kept `commonMain`-safe/cross-platform, not based on typed per-platform exception classes. Not exhaustive (a known, already self-flagged limitation of the sibling class this mirrors). */
    private fun classifyTransportException(e: Exception): SyncTransportOutcome<Nothing> {
        val name = e::class.simpleName.orEmpty()
        val message = e.message.orEmpty()
        val looksLikeTls = listOf("SSL", "TLS", "Certificate", "Handshake").any { it in name || it in message }
        return if (looksLikeTls) {
            SyncTransportOutcome.TlsFailure
        } else {
            SyncTransportOutcome.NetworkFailure(message.ifBlank { name }.ifBlank { "unknown network failure" })
        }
    }
}

@OptIn(ExperimentalEncodingApi::class)
private fun encodeBase64(bytes: ByteArray): String = Base64.Default.encode(bytes)

/** One outbound sync event -- the client-side shape [SyncTransport.push] accepts. Field names are Kotlin-idiomatic camelCase; wire-format snake_case conversion happens inside [SyncTransport.push] itself, matching `owner/app/sync/routes.py::_build_event`'s exact required field set (`id`, `entity_type`, `entity_id`, `event_type`, `payload`, `created_at`). */
data class SyncEventEnvelope(
    val id: String,
    val entityType: String,
    val entityId: String,
    val eventType: String,
    val payload: JsonObject,
    val createdAt: String,
)

/** Matches `owner/app/sync/routes.py::push()`'s real `{"stored": N, "received": N}` success body. */
data class PushResult(val stored: Int, val received: Int)

/** One inbound sync event, as returned by `owner/app/sync/routes.py::pull()` -- includes `seq`, the server-assigned monotonic sequence number this device's [PullResult.cursor] advances past. */
data class PulledSyncEvent(
    val id: String,
    val entityType: String,
    val entityId: String,
    val eventType: String,
    val payload: JsonObject,
    val createdAt: String,
    val seq: Long,
)

/** Matches `owner/app/sync/routes.py::pull()`'s real `{"events": [...], "cursor": N}` success body. */
data class PullResult(val events: List<PulledSyncEvent>, val cursor: Long)

@Serializable
private data class PushWireResponse(
    val stored: Int? = null,
    val received: Int? = null,
    @SerialName("reason_code") val reasonCode: String? = null,
)

@Serializable
private data class PullWireResponse(
    val events: List<PullWireEvent>? = null,
    val cursor: Long? = null,
    @SerialName("reason_code") val reasonCode: String? = null,
)

@Serializable
private data class PullWireEvent(
    val id: String,
    @SerialName("entity_type") val entityType: String,
    @SerialName("entity_id") val entityId: String,
    @SerialName("event_type") val eventType: String,
    val payload: JsonObject,
    @SerialName("created_at") val createdAt: String,
    val seq: Long,
)
