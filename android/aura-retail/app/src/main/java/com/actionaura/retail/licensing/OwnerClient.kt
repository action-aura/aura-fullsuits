package com.actionaura.retail.licensing

import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.net.SocketTimeoutException
import java.security.SecureRandom
import java.time.Instant
import java.util.Base64
import java.util.UUID
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLException
import kotlin.random.Random

/**
 * Kotlin implementation of the Phase 6 activation/check-in/deactivation
 * protocol (Part I/U). This is the ONLY place on Android that ever makes a
 * signed HTTP call to Owner -- it holds (indirectly, via [DeviceIdentity])
 * the device key that Python on this platform deliberately does not have.
 * Mirrors commercial_runtime/licensing_contracts/client.py's shape and
 * behavior (same field names, same retry/backoff policy, same
 * license-key-cleared-after-use discipline) -- see
 * docs/licensing/phase7/product-integration-architecture.md for why this is
 * a separate Kotlin implementation rather than shared code.
 *
 * Every raw response this class returns is UNTRUSTED until handed to the
 * embedded Python backend's /_internal/sync-* routes for independent
 * re-verification -- this class itself never decides whether an activation
 * succeeded from a security standpoint, only what Owner said.
 */

private const val CONTRACT_VERSION = "v1"
private val RETRYABLE_STATUS_CODES = setOf(429, 500, 502, 503, 504)

data class OwnerClientConfig(
    val baseUrl: String,
    val timeoutSeconds: Long = 10,
    val maxRetries: Int = 4,
    val retryBaseBackoffMillis: Long = 1000,
    val retryMaxBackoffMillis: Long = 30000,
)

open class OwnerClientError(val reasonCode: String, message: String) : Exception(message)
class NetworkError(reasonCode: String, message: String) : OwnerClientError(reasonCode, message)
class MalformedResponseError(reasonCode: String, message: String) : OwnerClientError(reasonCode, message)

class OwnerClient(
    private val config: OwnerClientConfig,
    httpClient: OkHttpClient? = null,
    private val sleepFn: (Long) -> Unit = { Thread.sleep(it) },
) {
    private val gson = Gson()
    private val http: OkHttpClient = httpClient ?: OkHttpClient.Builder()
        .connectTimeout(config.timeoutSeconds, TimeUnit.SECONDS)
        .readTimeout(config.timeoutSeconds, TimeUnit.SECONDS)
        .build()

    fun activate(
        productCode: String,
        platform: String,
        appVersion: String,
        releaseChannel: String?,
        installationId: String,
        devicePublicKeyB64: String,
        licenseKey: String,
        idempotencyKey: String,
        identity: DeviceIdentity,
    ): Map<String, Any?> {
        val body = linkedMapOf<String, Any?>(
            "contract_version" to CONTRACT_VERSION,
            "request_id" to UUID.randomUUID().toString(),
            "correlation_id" to UUID.randomUUID().toString(),
            "timestamp" to Instant.now().toString(),
            "nonce" to newNonce(),
            "product_code" to productCode,
            "platform" to platform,
            "app_version" to appVersion,
            "release_channel" to releaseChannel,
            "installation_id" to installationId,
            "device_public_key" to devicePublicKeyB64,
            "device_public_key_algorithm" to "ed25519",
            "license_key" to licenseKey,
            "idempotency_key" to idempotencyKey,
        )
        try {
            return postSigned("/activations", body, identity)
        } finally {
            // Discard the local reference to the full license key as soon
            // as this call returns (Part G) -- defense in depth on top of
            // the caller's own responsibility to clear its copy.
            body["license_key"] = null
        }
    }

    fun checkIn(installationId: String, identity: DeviceIdentity): Map<String, Any?> {
        val body = linkedMapOf<String, Any?>(
            "contract_version" to CONTRACT_VERSION,
            "request_id" to UUID.randomUUID().toString(),
            "correlation_id" to UUID.randomUUID().toString(),
            "timestamp" to Instant.now().toString(),
            "nonce" to newNonce(),
            "installation_id" to installationId,
        )
        return postSigned("/check-ins", body, identity)
    }

    fun deactivate(installationId: String, idempotencyKey: String, identity: DeviceIdentity): Map<String, Any?> {
        val body = linkedMapOf<String, Any?>(
            "contract_version" to CONTRACT_VERSION,
            "request_id" to UUID.randomUUID().toString(),
            "correlation_id" to UUID.randomUUID().toString(),
            "timestamp" to Instant.now().toString(),
            "nonce" to newNonce(),
            "installation_id" to installationId,
            "idempotency_key" to idempotencyKey,
        )
        return postSigned("/deactivations", body, identity)
    }

    fun fetchSigningKeys(): Map<String, Any?> = request("GET", "/signing-keys", null)

    fun fetchServiceInfo(): Map<String, Any?> = request("GET", "/service-info", null)

    private fun postSigned(path: String, body: LinkedHashMap<String, Any?>, identity: DeviceIdentity): Map<String, Any?> {
        val canonicalBytes = canonicalizeBytes(body)
        body["signature"] = identity.sign(canonicalBytes)
        return request("POST", path, body)
    }

    private fun request(method: String, path: String, body: Map<String, Any?>?): Map<String, Any?> {
        val url = config.baseUrl.trimEnd('/') + path
        var lastError: OwnerClientError? = null

        for (attempt in 0..config.maxRetries) {
            if (attempt > 0) {
                val delay = config.retryBaseBackoffMillis * (1L shl (attempt - 1))
                val jitter = (delay * 0.25 * Random.nextDouble()).toLong()
                sleepFn(minOf(delay + jitter, config.retryMaxBackoffMillis))
            }

            val requestBuilder = Request.Builder().url(url)
            when {
                body != null -> requestBuilder.post(gson.toJson(body).toRequestBody("application/json".toMediaType()))
                method == "POST" -> requestBuilder.post("".toRequestBody(null))
                else -> requestBuilder.get()
            }

            val outcome: RequestOutcome = try {
                http.newCall(requestBuilder.build()).execute().use { response ->
                    if (response.code in RETRYABLE_STATUS_CODES) {
                        val retryAfterSeconds = response.header("Retry-After")?.toDoubleOrNull()
                        if (retryAfterSeconds != null) {
                            sleepFn(minOf((retryAfterSeconds * 1000).toLong(), config.retryMaxBackoffMillis))
                        }
                        val reasonCode = if (response.code == 429) "RATE_LIMITED" else "SERVICE_TEMPORARILY_UNAVAILABLE"
                        RequestOutcome.Retry(NetworkError(reasonCode, "HTTP ${response.code}"))
                    } else {
                        val bodyString = response.body?.string() ?: ""
                        try {
                            val type = object : TypeToken<Map<String, Any?>>() {}.type
                            RequestOutcome.Success(gson.fromJson(bodyString, type))
                        } catch (exc: Exception) {
                            throw MalformedResponseError("MALFORMED_RESPONSE", "Response was not valid JSON: ${exc.message}")
                        }
                    }
                }
            } catch (exc: SocketTimeoutException) {
                RequestOutcome.Retry(NetworkError("REQUEST_TIMED_OUT", exc.message ?: "timeout"))
            } catch (exc: SSLException) {
                // Never retried -- a TLS failure is not transient in the
                // sense that matters here, and retrying could mask a real
                // MITM attempt as "just try again."
                throw NetworkError("TLS_VERIFICATION_FAILED", exc.message ?: "TLS error")
            } catch (exc: IOException) {
                RequestOutcome.Retry(NetworkError("NETWORK_UNAVAILABLE", exc.message ?: "network error"))
            }

            when (outcome) {
                is RequestOutcome.Success -> return outcome.value
                is RequestOutcome.Retry -> lastError = outcome.error
            }
        }

        throw lastError ?: NetworkError("NETWORK_UNAVAILABLE", "Request failed with no captured error.")
    }

    private sealed class RequestOutcome {
        data class Success(val value: Map<String, Any?>) : RequestOutcome()
        data class Retry(val error: NetworkError) : RequestOutcome()
    }

    private fun newNonce(): String {
        val bytes = ByteArray(24)
        SecureRandom().nextBytes(bytes)
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    }
}
