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

    private fun localUrl(path: String): String = ServerBootstrap.baseUrl().trimEnd('/') + path

    private fun executeLocal(request: Request): Map<String, Any?> {
        http.newCall(request).execute().use { response ->
            val bodyString = response.body?.string() ?: "{}"
            val type = object : TypeToken<Map<String, Any?>>() {}.type
            return gson.fromJson(bodyString, type) ?: emptyMap()
        }
    }
}
