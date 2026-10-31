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
                registryDeviceId = registryDeviceId(),
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

    // ── Sign in with an Aura account (new customer flow, plan S6) ─────────────
    // Same two-step shape as activate(): Kotlin makes the signed Owner call, Python
    // re-reads Owner's RAW answer (the contract text is checked against its hash
    // there, by the same code the desktop uses). The typed password and the Owner
    // ticket stay in this process: the ticket in AccountFlowStore (memory only).

    private val productCode get() = com.actionaura.retail.BuildConfig.PRODUCT_CODE
    private val appVersion get() = com.actionaura.retail.BuildConfig.VERSION_NAME

    private fun refusal(code: String, detail: String = ""): Map<String, Any?> =
        mapOf("reason_code" to code, "detail" to detail, "_local_error" to true)

    /** Returns the screen's view of the sign-in (NO ticket) or `{reason_code: ...}`. */
    suspend fun accountSignIn(email: String, password: String): Map<String, Any?> = withContext(Dispatchers.IO) {
        if (!identity.hasKey()) identity.generateNewKey()
        val raw: String = try {
            ownerClient().accountSignIn(
                productCode, "ANDROID", appVersion, identity.getPublicKeyB64(),
                email.trim(), password, identity,
            )
        } catch (exc: OwnerClientError) {
            return@withContext refusal(exc.reasonCode, exc.message ?: "")
        }
        val ticket = (parseMap(raw)["ticket"] as? String)?.takeIf { it.isNotBlank() }
        val request = Request.Builder()
            .url(localUrl("/api/licensing/_internal/account-sign-in-response"))
            .header("X-Aura-Internal-Secret", ServerBootstrap.internalSecret())
            .post(raw.toRequestBody("application/json".toMediaType()))
            .build()
        val view = executeLocal(request)
        val parsed = parseAccountView(view)
        if (ticket == null || parsed == null) {
            AccountFlowStore.clear()
            // A refused sign-in carries Python's single public reason code; an
            // answer with no ticket and no code is the same single failure.
            return@withContext if (view["reason_code"] != null) view else refusal("ACCOUNT_SIGN_IN_FAILED")
        }
        AccountFlowStore.set(AccountFlowSession(parsed, ticket))
        view
    }

    suspend fun changePassword(newPassword: String): Map<String, Any?> = withContext(Dispatchers.IO) {
        val session = AccountFlowStore.get() ?: return@withContext refusal("ACCOUNT_FLOW_EXPIRED")
        if (!session.passwordPending) return@withContext refusal("PASSWORD_CHANGE_NOT_NEEDED")
        if (newPassword.length < MIN_NEW_PASSWORD_LENGTH) {
            return@withContext mapOf("reason_code" to "PASSWORD_POLICY_FAILED", "policy_code" to "too_short")
        }
        val ticket = session.ticket() ?: return@withContext refusal("ACCOUNT_FLOW_EXPIRED")
        val raw: String = try {
            ownerClient().changeAccountPassword(
                productCode, "ANDROID", appVersion, identity.getPublicKeyB64(), ticket, newPassword, identity,
            )
        } catch (exc: OwnerClientError) {
            return@withContext refusal(exc.reasonCode, exc.message ?: "")
        }
        val answer = parseMap(raw)
        val code = answer["reason_code"] as? String
        if (code != null) {
            if (code == "ACCOUNT_SIGN_IN_FAILED") AccountFlowStore.clear()   // ticket refused: start over
            return@withContext answer
        }
        session.replaceTicket(answer["ticket"] as? String)
        session.markPasswordChanged()
        mapOf("result" to "SUCCESS")
    }

    suspend fun acceptContract(): Map<String, Any?> = withContext(Dispatchers.IO) {
        val session = AccountFlowStore.get() ?: return@withContext refusal("ACCOUNT_FLOW_EXPIRED")
        if (session.passwordPending) return@withContext refusal("PASSWORD_CHANGE_REQUIRED")
        val c = session.view.contract
        val body = c.body
        val versionId = c.versionId
        val sha = c.sha256
        if (!c.required || body == null || versionId == null || sha == null) {
            return@withContext refusal("CONTRACT_HASH_MISMATCH", "There is no contract to accept.")
        }
        // Re-hash at the moment of acceptance: what is recorded is what was shown.
        if (contractSha256(body) != sha.lowercase()) return@withContext refusal("CONTRACT_HASH_MISMATCH")
        val ticket = session.ticket() ?: return@withContext refusal("ACCOUNT_FLOW_EXPIRED")
        val raw: String = try {
            ownerClient().acceptAccountContract(
                productCode, "ANDROID", appVersion, identity.getPublicKeyB64(),
                ticket, versionId, sha.lowercase(), c.language ?: "ar", identity,
            )
        } catch (exc: OwnerClientError) {
            return@withContext refusal(exc.reasonCode, exc.message ?: "")
        }
        val answer = parseMap(raw)
        val code = answer["reason_code"] as? String
        if (code != null) {
            if (code == "ACCOUNT_SIGN_IN_FAILED") AccountFlowStore.clear()
            return@withContext answer
        }
        session.markContractAccepted()
        mapOf("result" to "SUCCESS")
    }

    /** Activates this phone for one of the licences listed by the sign-in. */
    suspend fun activateAccount(licensePublicId: String?): Map<String, Any?> = withContext(Dispatchers.IO) {
        val session = AccountFlowStore.get() ?: return@withContext refusal("ACCOUNT_FLOW_EXPIRED")
        if (session.passwordPending) return@withContext refusal("PASSWORD_CHANGE_REQUIRED")
        if (session.contractPending) return@withContext refusal("CONTRACT_ACCEPTANCE_REQUIRED")
        val chosen = licensePublicId ?: session.view.licences.singleOrNull()?.licensePublicId
        if (chosen == null || !session.isListed(chosen)) return@withContext refusal("ACTIVATION_GRANT_INVALID")
        val ticket = session.ticket() ?: return@withContext refusal("ACCOUNT_FLOW_EXPIRED")
        if (!identity.hasKey()) identity.generateNewKey()
        val raw: String = try {
            ownerClient().activateWithAccount(
                productCode, "ANDROID", appVersion, "rc", UUID.randomUUID().toString(),
                identity.getPublicKeyB64(), ticket, chosen, UUID.randomUUID().toString(), identity,
                registryDeviceId(),
            )
        } catch (exc: OwnerClientError) {
            return@withContext refusal(exc.reasonCode, exc.message ?: "")
        }
        val request = Request.Builder()
            .url(localUrl("/api/licensing/_internal/sync-activation"))
            .header("X-Aura-Internal-Secret", ServerBootstrap.internalSecret())
            .post(raw.toRequestBody("application/json".toMediaType()))
            .build()
        val result = executeLocal(request)
        // The ticket is single-use: whatever the verdict, it has done its job.
        if (result["result"] == "SUCCESS" || result["result"] == "PENDING" ||
            result["reason_code"] == "ACTIVATION_GRANT_INVALID") AccountFlowStore.clear()
        result
    }

    private fun parseMap(body: String): Map<String, Any?> = try {
        val type = object : TypeToken<Map<String, Any?>>() {}.type
        gson.fromJson<Map<String, Any?>>(body, type) ?: emptyMap()
    } catch (e: Exception) { emptyMap() }

    suspend fun checkIn(): Map<String, Any?> = withContext(Dispatchers.IO) {
        if (!identity.hasKey()) return@withContext mapOf("current_state" to "ACTIVATION_REQUIRED")
        val currentStatus = status()
        val installationId = currentStatus["installation_id"] as? String
            ?: return@withContext mapOf("current_state" to "ACTIVATION_REQUIRED")

        val ownerResponseRaw: String = try {
            ownerClient().checkIn(
                installationId = installationId,
                identity = identity,
                registryDeviceId = registryDeviceId(),
            )
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

    /**
     * AUDIT-033 fix round (verifier V finding #1): this device's shop-registry
     * computer id, asked of Python, which owns it. Kotlin owns the Owner call
     * and Python owns the id, so the id travels Python -> here -> Owner (inside
     * the device-signed body) -> Owner's pin -> the Owner-signed lease.
     *
     * Never throws and never blocks licensing: any failure (backend not up,
     * 403, malformed body) yields null and the request simply goes out without
     * the field, exactly like a pre-fix client. The lease then stays
     * unattested and this phone cannot sign shop-authority changes until a
     * later check-in succeeds -- a degraded state, not a licensing failure.
     */
    private fun registryDeviceId(): String? = try {
        val request = Request.Builder()
            .url(localUrl("/api/licensing/_internal/registry-device-id"))
            .header("X-Aura-Internal-Secret", ServerBootstrap.internalSecret())
            .get()
            .build()
        parseRegistryDeviceId(executeLocal(request))
    } catch (exc: java.io.IOException) {
        null
    } catch (exc: RuntimeException) {
        // Gson parse failures are RuntimeExceptions; same "send without" outcome.
        null
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

/**
 * Pure parse of the `/_internal/registry-device-id` body: a non-blank String
 * is the id, anything else (missing key, null, blank, a number) is "no id".
 * Top-level and `internal` so a plain JVM test can pin it without a Context.
 */
internal fun parseRegistryDeviceId(map: Map<String, Any?>): String? =
    (map["registry_device_id"] as? String)?.takeIf { it.isNotBlank() }
