package com.actionaura.retail.licensing

import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
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
class DownloadError(reasonCode: String, message: String) : OwnerClientError(reasonCode, message)

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
        registryDeviceId: String? = null,
    ): String {
        var lastBody: LinkedHashMap<String, Any?>? = null
        try {
            return requestRaw("POST", "/activations") {
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
                addRegistryDeviceId(body, registryDeviceId)
                lastBody = body
                sign(body, identity)
            }
        } finally {
            // Discard the local reference to the full license key as soon
            // as this call returns (Part G) -- defense in depth on top of
            // the caller's own responsibility to clear its copy.
            lastBody?.set("license_key", null)
        }
    }

    // ── Sign in with an Aura account (new customer flow, plan S6) ─────────────
    // Mirrors client.py's account_sign_in / change_password / accept_contract and
    // the account_ticket activation. Field names are the S5 contract; AccountFlow.kt
    // explains the split of work with Python.
    //
    // NEVER RETRIED (retry = false). A sign-in or password change that reached Owner
    // and was refused or rate-limited must not be repeated automatically: a retry
    // burns a lockout slot and, for the password change, re-sends a credential.
    // A 429 comes back as {"reason_code":"RATE_LIMITED","retry_after_seconds":N}.

    private fun accountBase(productCode: String, platform: String, appVersion: String, devicePublicKeyB64: String) =
        linkedMapOf<String, Any?>(
            "contract_version" to CONTRACT_VERSION,
            "request_id" to UUID.randomUUID().toString(),
            "correlation_id" to UUID.randomUUID().toString(),
            "timestamp" to Instant.now().toString(),
            "nonce" to newNonce(),
            "product_code" to productCode,
            "platform" to platform,
            "app_version" to appVersion,
            "device_public_key" to devicePublicKeyB64,
            "device_public_key_algorithm" to "ed25519",
        )

    fun accountSignIn(
        productCode: String, platform: String, appVersion: String, devicePublicKeyB64: String,
        email: String, password: String, identity: DeviceIdentity,
    ): String {
        var lastBody: LinkedHashMap<String, Any?>? = null
        try {
            return requestRaw("POST", "/account-sign-ins", retry = false) {
                val body = accountBase(productCode, platform, appVersion, devicePublicKeyB64)
                body["email"] = email
                body["password"] = password
                lastBody = body
                sign(body, identity)
            }
        } finally {
            lastBody?.set("password", null)
        }
    }

    fun changeAccountPassword(
        productCode: String, platform: String, appVersion: String, devicePublicKeyB64: String,
        ticket: String, newPassword: String, identity: DeviceIdentity,
    ): String {
        var lastBody: LinkedHashMap<String, Any?>? = null
        try {
            return requestRaw("POST", "/account-password-changes", retry = false) {
                val body = accountBase(productCode, platform, appVersion, devicePublicKeyB64)
                body["ticket"] = ticket
                body["new_password"] = newPassword
                lastBody = body
                sign(body, identity)
            }
        } finally {
            lastBody?.set("new_password", null)
            lastBody?.set("ticket", null)
        }
    }

    fun acceptAccountContract(
        productCode: String, platform: String, appVersion: String, devicePublicKeyB64: String,
        ticket: String, contractVersionId: String, textSha256: String, language: String, identity: DeviceIdentity,
    ): String {
        var lastBody: LinkedHashMap<String, Any?>? = null
        try {
            return requestRaw("POST", "/contract-acceptances", retry = false) {
                val body = accountBase(productCode, platform, appVersion, devicePublicKeyB64)
                body["ticket"] = ticket
                body["contract_version_id"] = contractVersionId
                body["text_sha256"] = textSha256
                body["language"] = language
                lastBody = body
                sign(body, identity)
            }
        } finally {
            lastBody?.set("ticket", null)
        }
    }

    /** Activation by `{account_ticket, license_public_id}` -- the response is forwarded to
     * Python's /_internal/sync-activation exactly like a key activation's. */
    fun activateWithAccount(
        productCode: String, platform: String, appVersion: String, releaseChannel: String?,
        installationId: String, devicePublicKeyB64: String, accountTicket: String,
        licensePublicId: String, idempotencyKey: String, identity: DeviceIdentity,
        registryDeviceId: String? = null,
    ): String {
        var lastBody: LinkedHashMap<String, Any?>? = null
        try {
            return requestRaw("POST", "/activations") {
                val body = accountBase(productCode, platform, appVersion, devicePublicKeyB64)
                body["release_channel"] = releaseChannel
                body["installation_id"] = installationId
                body["account_ticket"] = accountTicket
                body["license_public_id"] = licensePublicId
                body["idempotency_key"] = idempotencyKey
                addRegistryDeviceId(body, registryDeviceId)
                lastBody = body
                sign(body, identity)
            }
        } finally {
            lastBody?.set("account_ticket", null)
        }
    }

    fun checkIn(installationId: String, identity: DeviceIdentity, registryDeviceId: String? = null): String =
        requestRaw("POST", "/check-ins") {
            val body = linkedMapOf<String, Any?>(
                "contract_version" to CONTRACT_VERSION,
                "request_id" to UUID.randomUUID().toString(),
                "correlation_id" to UUID.randomUUID().toString(),
                "timestamp" to Instant.now().toString(),
                "nonce" to newNonce(),
                "installation_id" to installationId,
            )
            addRegistryDeviceId(body, registryDeviceId)
            sign(body, identity)
        }

    fun deactivate(installationId: String, idempotencyKey: String, identity: DeviceIdentity): String =
        requestRaw("POST", "/deactivations") {
            val body = linkedMapOf<String, Any?>(
                "contract_version" to CONTRACT_VERSION,
                "request_id" to UUID.randomUUID().toString(),
                "correlation_id" to UUID.randomUUID().toString(),
                "timestamp" to Instant.now().toString(),
                "nonce" to newNonce(),
                "installation_id" to installationId,
                "idempotency_key" to idempotencyKey,
            )
            sign(body, identity)
        }

    /**
     * Update channel: "what is the newest release I may install?". Device-signed
     * like a check-in; mirrors client.py's find_latest_release (same field names,
     * `release_channel` sent, `platform` deliberately NOT sent).
     *
     * Returns Pair(requestId, Owner's raw body). Owner echoes `request_id` inside
     * the signed release descriptor and the local updates route refuses a
     * descriptor whose echo differs, so a captured old answer cannot be replayed
     * as a new one -- which only works if the caller can hand that SAME id to
     * Python. The id is therefore minted ONCE, outside the body provider:
     * [requestRaw] rebuilds (and re-nonces, re-signs) the body on every retry, and
     * an id minted inside would change with it, so a retry that finally got
     * through would carry an id the caller never saw.
     */
    fun findLatestRelease(
        installationId: String,
        appVersion: String,
        releaseChannel: String?,
        identity: DeviceIdentity,
    ): Pair<String, String> {
        val requestId = UUID.randomUUID().toString()
        val raw = requestRaw("POST", "/releases/latest") {
            sign(
                linkedMapOf<String, Any?>(
                    "contract_version" to CONTRACT_VERSION,
                    "request_id" to requestId,
                    "correlation_id" to UUID.randomUUID().toString(),
                    "timestamp" to Instant.now().toString(),
                    "nonce" to newNonce(),
                    "installation_id" to installationId,
                    "app_version" to appVersion,
                    "release_channel" to releaseChannel,
                ),
                identity,
            )
        }
        return requestId to raw
    }

    /** Asks Owner for a short-lived, SINGLE-USE download token. Returns Owner's raw body. */
    fun authorizeReleaseDownload(installationId: String, productVersionId: String, identity: DeviceIdentity): String =
        requestRaw("POST", "/releases/authorize-download") {
            sign(
                linkedMapOf<String, Any?>(
                    "contract_version" to CONTRACT_VERSION,
                    "request_id" to UUID.randomUUID().toString(),
                    "correlation_id" to UUID.randomUUID().toString(),
                    "timestamp" to Instant.now().toString(),
                    "nonce" to newNonce(),
                    "installation_id" to installationId,
                    "product_version_id" to productVersionId,
                ),
                identity,
            )
        }

    /**
     * Streams the installer to [dest] (through `dest.part`, renamed only once
     * the whole stream arrived).
     *
     * NO RETRY, deliberately, unlike every other call here: the token is
     * single-use, so the moment Owner started serving it a second attempt can
     * only fail. A failure goes to the caller, who asks for a fresh token.
     *
     * [expectedSize] is a CEILING, not a hint (same rule as client.py's
     * download_release): more bytes than the signed descriptor promised means a
     * misbehaving or impersonated server, and it must not be allowed to fill the
     * phone. The part file is deleted on EVERY failure path, so a half-written
     * installer never lies around looking like a candidate. Whether the finished
     * file is the RIGHT size and hash is the caller's check, not this method's.
     */
    fun downloadRelease(token: String, dest: File, expectedSize: Long, progress: (Long) -> Unit = {}) {
        val part = File(dest.path + ".part")
        part.parentFile?.mkdirs()
        part.delete()
        dest.delete()
        val url = config.baseUrl.trimEnd('/').toHttpUrl().newBuilder()
            .addPathSegments("releases/download")
            .addPathSegment(token) // encoded as ONE segment: a token can never add path
            .build()
        // A longer read timeout than the JSON calls: the cap is per-read, not for
        // the whole transfer, and a slow mobile link stalls longer than 10s.
        val client = http.newBuilder()
            .readTimeout(maxOf(config.timeoutSeconds, 60L), TimeUnit.SECONDS)
            .build()
        try {
            client.newCall(Request.Builder().url(url).get().build()).execute().use { response ->
                if (response.code != 200) throw DownloadError("DOWNLOAD_REFUSED", "HTTP ${response.code}")
                val declared = response.header("Content-Length")?.toLongOrNull()
                if (declared != null && declared > expectedSize) {
                    throw DownloadError("DOWNLOAD_TOO_LARGE", "server announced $declared bytes, ceiling is $expectedSize")
                }
                val source = response.body?.byteStream() ?: throw DownloadError("DOWNLOAD_REFUSED", "empty body")
                var received = 0L
                part.outputStream().use { out ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) {
                        val n = source.read(buffer)
                        if (n < 0) break
                        received += n
                        if (received > expectedSize) {
                            throw DownloadError("DOWNLOAD_TOO_LARGE", "more than $expectedSize bytes arrived")
                        }
                        out.write(buffer, 0, n)
                        progress(received)
                    }
                }
            }
            if (!part.renameTo(dest)) throw DownloadError("DOWNLOAD_REFUSED", "could not move the finished download into place")
        } catch (exc: SSLException) {
            part.delete()
            throw NetworkError("TLS_VERIFICATION_FAILED", exc.message ?: "TLS error")
        } catch (exc: IOException) {
            part.delete()
            throw NetworkError("NETWORK_UNAVAILABLE", exc.message ?: "network error")
        } catch (exc: Throwable) {
            // DownloadError, a cancelled coroutine, anything: never leave the part behind.
            part.delete()
            throw exc
        }
    }

    fun fetchSigningKeys(): Map<String, Any?> = parseJson(requestRaw("GET", "/signing-keys"))

    fun fetchServiceInfo(): Map<String, Any?> = parseJson(requestRaw("GET", "/service-info"))

    private fun parseJson(bodyString: String): Map<String, Any?> {
        val type = object : TypeToken<Map<String, Any?>>() {}.type
        return gson.fromJson(bodyString, type)
    }

    /**
     * AUDIT-033 fix round (verifier V finding #1): tell Owner which shop-registry
     * computer id this device claims, so Owner can pin it and echo it back inside
     * the Owner-signed lease. Receivers of shop-authority events trust a signer
     * only when its lease attests the id it speaks as.
     *
     * Added to the body BEFORE [sign] (callers invoke this inside the body
     * provider, so every retry rebuilds and re-signs it): the device signature
     * must cover the id, or a man in the middle could swap it. Omitted entirely
     * when null or blank -- never sent as a null key -- because Owner only echoes
     * the field when the request carried it, and old clients reject unknown
     * fields (the Phase 8 Part W outage).
     */
    private fun addRegistryDeviceId(body: LinkedHashMap<String, Any?>, registryDeviceId: String?) {
        if (!registryDeviceId.isNullOrBlank()) body["registry_device_id"] = registryDeviceId
    }

    private fun sign(body: LinkedHashMap<String, Any?>, identity: DeviceIdentity): LinkedHashMap<String, Any?> {
        val canonicalBytes = canonicalizeBytes(body)
        body["signature"] = identity.sign(canonicalBytes)
        return body
    }

    /** Returns Owner's response body VERBATIM, never parsed-then-reserialized.
     * Critical for the signed calls (activate/checkIn/deactivate): their
     * response bodies are forwarded to the embedded Python backend's
     * /_internal/sync-* routes for independent signature re-verification
     * against the exact bytes Owner signed. Parsing into a Map<String, Any?>
     * via Gson and re-serializing (as this code used to do) silently
     * corrupts every JSON number -- Gson's generic Object deserialization
     * represents them all as Double, so an integer field like `30` comes
     * back out as `30.0` -- which changes the canonical byte sequence and
     * makes a genuinely-valid signature verify as invalid (confirmed via
     * physical Phase 7V-A testing: fingerprints/installation_id matched
     * exactly, only the signature itself failed). Forwarding the raw string
     * makes this a non-issue: the bytes Python re-verifies are byte-for-byte
     * what Owner sent. */
    private fun requestRaw(
        method: String, path: String, retry: Boolean = true,
        bodyProvider: (() -> Map<String, Any?>)? = null,
    ): String {
        val url = config.baseUrl.trimEnd('/') + path
        var lastError: OwnerClientError? = null

        for (attempt in 0..(if (retry) config.maxRetries else 0)) {
            if (attempt > 0) {
                val delay = config.retryBaseBackoffMillis * (1L shl (attempt - 1))
                val jitter = (delay * 0.25 * Random.nextDouble()).toLong()
                sleepFn(minOf(delay + jitter, config.retryMaxBackoffMillis))
            }

            // Every attempt -- including retries -- gets a freshly built (and,
            // for signed calls, freshly nonce'd + re-signed) body. Resending
            // the exact same nonce on a retry is unsafe: if an earlier attempt
            // actually reached Owner and was processed (its nonce consumed)
            // but the response was then lost client-side (a real failure mode
            // confirmed via physical Phase 7V-A testing over an unreliable
            // transport), Owner's replay protection correctly -- and
            // permanently -- rejects the identical nonce on retry as
            // NONCE_REUSED, which would otherwise strand every retry after a
            // single lost response.
            val body = bodyProvider?.invoke()
            val requestBuilder = Request.Builder().url(url)
            when {
                body != null -> requestBuilder.post(gson.toJson(body).toRequestBody("application/json".toMediaType()))
                method == "POST" -> requestBuilder.post("".toRequestBody(null))
                else -> requestBuilder.get()
            }

            val outcome: RequestOutcome = try {
                http.newCall(requestBuilder.build()).execute().use { response ->
                    if (!retry && response.code == 429) {
                        // A lockout is an ANSWER, not a hiccup: hand it back with its wait time.
                        val wait = response.header("Retry-After")?.toDoubleOrNull()?.toLong()
                        val answer = linkedMapOf<String, Any?>("reason_code" to "RATE_LIMITED")
                        if (wait != null) answer["retry_after_seconds"] = wait
                        RequestOutcome.Success(gson.toJson(answer))
                    } else if (response.code in RETRYABLE_STATUS_CODES) {
                        val retryAfterSeconds = response.header("Retry-After")?.toDoubleOrNull()
                        if (retryAfterSeconds != null) {
                            sleepFn(minOf((retryAfterSeconds * 1000).toLong(), config.retryMaxBackoffMillis))
                        }
                        val reasonCode = if (response.code == 429) "RATE_LIMITED" else "SERVICE_TEMPORARILY_UNAVAILABLE"
                        RequestOutcome.Retry(NetworkError(reasonCode, "HTTP ${response.code}"))
                    } else {
                        val bodyString = response.body?.string() ?: ""
                        try {
                            parseJson(bodyString) // validate only -- discard the parsed copy
                        } catch (exc: Exception) {
                            throw MalformedResponseError("MALFORMED_RESPONSE", "Response was not valid JSON: ${exc.message}")
                        }
                        RequestOutcome.Success(bodyString)
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
        data class Success(val value: String) : RequestOutcome()
        data class Retry(val error: NetworkError) : RequestOutcome()
    }

    private fun newNonce(): String {
        val bytes = ByteArray(24)
        SecureRandom().nextBytes(bytes)
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    }
}
