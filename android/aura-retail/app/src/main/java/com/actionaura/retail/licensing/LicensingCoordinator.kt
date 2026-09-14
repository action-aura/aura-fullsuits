package com.actionaura.retail.licensing

import android.content.Context
import com.actionaura.retail.server.ServerBootstrap
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * Orchestrates Phase 7 Part G/U activation on Android: this is the ONLY
 * place that ties [DeviceIdentity] (the real device key, Kotlin-owned) and
 * [OwnerClient] (the real Owner HTTP call, also Kotlin-owned) to the
 * embedded Python backend's /_internal/sync-* routes -- Compose screens
 * never talk to DeviceIdentity/OwnerClient directly, only to this class.
 *
 * Every method here follows the same two-step shape: (1) make the real
 * signed call to Owner via OwnerClient, (2) hand the raw response to Python
 * for INDEPENDENT re-verification and the only state mutation that counts
 * (see routes.py's /_internal/sync-* handlers and
 * docs/licensing/phase7/product-integration-architecture.md, Part U). This
 * class's own opinion of whether a call "succeeded" is never trusted by
 * itself -- callers must read the returned status, not just the absence of
 * a thrown exception.
 */
class LicensingCoordinator(context: Context) {

    private val gson = Gson()
    private val identity = DeviceIdentity(File(context.filesDir, "data"))
    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .build()

    private fun ownerClient(): OwnerClient {
        val baseUrl = com.actionaura.retail.BuildConfig.OWNER_LICENSING_BASE_URL
        return OwnerClient(OwnerClientConfig(baseUrl = baseUrl))
    }

    suspend fun status(): Map<String, Any?> = withContext(Dispatchers.IO) {
        val request = Request.Builder().url(localUrl("/api/licensing/status")).get().build()
        executeLocal(request)
    }

    suspend fun activate(licenseKey: String): Map<String, Any?> = withContext(Dispatchers.IO) {
        if (!identity.hasKey()) identity.generateNewKey()

        // The raw response body, forwarded VERBATIM (never parsed into a
        // Map and re-serialized) -- see OwnerClient.requestRaw()'s doc
        // comment: a Gson round-trip through Map<String, Any?> silently
        // turns integer fields like `30` into `30.0`, which changes the
        // canonical bytes and makes Python's independent signature
        // re-verification fail even though Owner genuinely approved the
        // request (confirmed via physical Phase 7V-A testing).
        val ownerResponseRaw: String = try {
            ownerClient().activate(
                productCode = com.actionaura.retail.BuildConfig.PRODUCT_CODE,
                platform = "ANDROID",
                appVersion = com.actionaura.retail.BuildConfig.VERSION_NAME,
                releaseChannel = "rc",
                installationId = UUID.randomUUID().toString(),
                devicePublicKeyB64 = identity.getPublicKeyB64(),
                licenseKey = licenseKey,
                idempotencyKey = UUID.randomUUID().toString(),
                identity = identity,
            )
        } catch (exc: OwnerClientError) {
            return@withContext mapOf("reason_code" to exc.reasonCode, "detail" to (exc.message ?: ""), "_local_error" to true)
        }

        val request = Request.Builder()
            .url(localUrl("/api/licensing/_internal/sync-activation"))
            .header("X-Aura-Internal-Secret", ServerBootstrap.internalSecret())
            .post(ownerResponseRaw.toRequestBody("application/json".toMediaType()))
            .build()
        executeLocal(request)
    }

    suspend fun checkIn(): Map<String, Any?> = withContext(Dispatchers.IO) {
        if (!identity.hasKey()) return@withContext mapOf("current_state" to "ACTIVATION_REQUIRED")
        val currentStatus = status()
        val installationId = currentStatus["installation_id"] as? String
            ?: return@withContext mapOf("current_state" to "ACTIVATION_REQUIRED")

        val ownerResponseRaw: String = try {
            ownerClient().checkIn(installationId = installationId, identity = identity)
        } catch (exc: OwnerClientError) {
            // Mirrors the Windows route's own behavior (Part AD): a failed
            // check-in attempt is not itself an error state -- report the
            // current status, flagged as not-reached, rather than
            // surfacing a raw exception to the UI. Unlike Windows'
            // run_once(), this process never calls into the scheduler
            // itself on a failed attempt, so a plain status() re-read
            // would never re-run the offline-policy evaluation and
            // current_state would stay stuck regardless of elapsed time
            // (Phase 7V-A gate I). Call /_internal/reevaluate instead --
            // it makes no Owner call, just re-evaluates against elapsed
            // trusted time and persists the result.
            val reevaluateRequest = Request.Builder()
                .url(localUrl("/api/licensing/_internal/reevaluate"))
                .header("X-Aura-Internal-Secret", ServerBootstrap.internalSecret())
                .post("{}".toRequestBody("application/json".toMediaType()))
                .build()
            val fallback = executeLocal(reevaluateRequest).toMutableMap()
            fallback["last_attempt_reached_owner"] = false
            return@withContext fallback
        }

        val request = Request.Builder()
            .url(localUrl("/api/licensing/_internal/sync-checkin"))
            .header("X-Aura-Internal-Secret", ServerBootstrap.internalSecret())
            .post(ownerResponseRaw.toRequestBody("application/json".toMediaType()))
            .build()
        val result = executeLocal(request).toMutableMap()
        result.putIfAbsent("last_attempt_reached_owner", true)
        result
    }

    suspend fun deactivate(): Map<String, Any?> = withContext(Dispatchers.IO) {
        val currentStatus = status()
        val installationId = currentStatus["installation_id"] as? String
        if (installationId == null || !identity.hasKey()) {
            return@withContext mapOf("result" to "SUCCESS", "state" to (currentStatus["current_state"] ?: "NOT_CONFIGURED"))
        }

        val ownerResponseRaw: String = try {
            ownerClient().deactivate(
                installationId = installationId,
                idempotencyKey = UUID.randomUUID().toString(),
                identity = identity,
            )
        } catch (exc: OwnerClientError) {
            return@withContext mapOf("reason_code" to exc.reasonCode, "detail" to (exc.message ?: ""), "_local_error" to true)
        }

        val request = Request.Builder()
            .url(localUrl("/api/licensing/_internal/sync-deactivation"))
            .header("X-Aura-Internal-Secret", ServerBootstrap.internalSecret())
            .post(ownerResponseRaw.toRequestBody("application/json".toMediaType()))
            .build()
        executeLocal(request)
    }

    /**
     * Reads this device's own verified `license_public_id` +
     * Owner-signed assertion envelope from `GET /api/licensing/
     * _internal/license-identity` -- the route `HubAutoJoinService.kt`
     * needs to close the gap its own header used to document (search that
     * file's git history for "THE GAP THIS TASK DID NOT CLOSE"). Kotlin
     * deliberately never opens `licensing.db` itself: this class's own doc
     * comment above already establishes that Python does "the only state
     * mutation that counts", and the identical reasoning applies to
     * READING the verified assertion, not just writing it -- a second,
     * independent parse of the raw file from Kotlin would have no
     * guarantee of matching the state machine's own verified copy.
     *
     * Same `executeLocal()`/gson-`Map` shape every other method here
     * uses, deliberately: the route answers 403 ("Unauthorized") or 400
     * (`NO_LOCAL_LICENSE`, for a missing/unparseable/incomplete stored
     * assertion) on failure and 200 on success, but [executeLocal] -- like
     * every other call in this class -- never inspects the status code,
     * only parses whatever JSON body came back. So a `{"reason_code",
     * "detail"}` failure body and a `{"license_public_id",
     * "assertion_envelope_json"}` success body both arrive here as a plain
     * `Map`, and a caller reads success or failure off ITS KEYS -- the
     * same way [status] and [checkIn]'s fallback path already do -- rather
     * than by branching on an HTTP status Kotlin would otherwise have to
     * special-case.
     *
     * `assertion_envelope_json` comes back out of [executeLocal]'s Gson
     * `Map<String, Any?>` parse as a plain `String` -- a JSON STRING
     * field's decoded value, never a nested object Gson had to walk and
     * that a caller might re-serialize -- so the exact bytes the route
     * read off disk reach the caller unchanged. Callers (only
     * [com.actionaura.retail.sync.HubAutoJoinService] has a legitimate
     * reason to call this) must keep it that way: read the `String` out
     * of the map directly and hand it onward verbatim, never round-trip
     * it through a JSON parser and re-stringify, or the hub's own
     * signature check over these exact bytes breaks silently.
     */
    suspend fun licenseIdentity(): Map<String, Any?> = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(localUrl("/api/licensing/_internal/license-identity"))
            .header("X-Aura-Internal-Secret", ServerBootstrap.internalSecret())
            .get()
            .build()
        executeLocal(request)
    }

    private fun localUrl(path: String): String = ServerBootstrap.baseUrl().trimEnd('/') + path

    private fun executeLocal(request: Request): Map<String, Any?> {
        http.newCall(request).execute().use { response ->
            val bodyString = response.body?.string() ?: "{}"
            val type = object : TypeToken<Map<String, Any?>>() {}.type
            return gson.fromJson(bodyString, type) ?: emptyMap()
        }
    }
}
