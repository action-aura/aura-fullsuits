package com.actionaura.retail.licensing.transport

import com.actionaura.retail.licensing.ActivationResult
import com.actionaura.retail.licensing.DeactivateInstallationRequest
import com.actionaura.retail.licensing.DeviceManagementOutcome
import com.actionaura.retail.licensing.InstallationDescriptor
import com.actionaura.retail.licensing.LicensingError
import com.actionaura.retail.licensing.ListInstallationsRequest
import com.actionaura.retail.licensing.ReleaseCheckRequest
import com.actionaura.retail.licensing.ReleaseCheckResult
import com.actionaura.retail.licensing.RequestDeviceReplacementRequest
import com.actionaura.retail.licensing.SignedAssertionEnvelope
import com.actionaura.retail.licensing.lease.LeaseCanonicalJson
import com.actionaura.retail.sync.DeviceSigner
import io.ktor.client.HttpClient
import io.ktor.client.plugins.HttpRequestTimeoutException
import io.ktor.client.plugins.HttpTimeout
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.client.statement.HttpResponse
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.HttpHeaders
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
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlin.io.encoding.Base64
import kotlin.io.encoding.ExperimentalEncodingApi

/**
 * Task 8 (multi-device-sync-foundation) -- the real, net-new HTTP-backed
 * [ExternalLicensingTransport] this module previously had zero of
 * (`DisabledProductionTransport` deterministically never makes a network
 * call, `production-transport-availability-rule.md`). Only
 * [activateWithLicenseKey] is real here -- the one operation this task
 * actually verified end to end against Owner's real, unmodified
 * `owner/app/licensing_service/activation.py`. Every
 * [ExternalLicensingTransport] interface method, INCLUDING
 * `activateInstallation(ActivationCommand)`, still deterministically
 * returns [TransportOutcome.TransportNotConfigured], the same honest
 * default [DisabledProductionTransport] already uses, because their real
 * Owner-side authority does not exist yet
 * (`ExternalCustomerSessionContracts.kt`'s own KDoc: the customer-session/
 * register/sign-in/claim-license surface is "shared client-side contract
 * shapes only, no real transport execution" as of this task) -- this class
 * never fabricates a fake success for those.
 *
 * Deliberate scope note: the real `owner/app/licensing_service/activation.py`
 * wire contract (`REQUIRED_FIELDS`) takes one flat `license_key` string and
 * has no concept of a customer session at all -- Owner's activation
 * endpoint authenticates purely via the license key + device signature,
 * exactly like desktop's own
 * `commercial_runtime/licensing_contracts/client.py::LicensingClient.activate()`.
 * `ActivationCommand` (the interface's own command type) is shaped for a
 * DIFFERENT, still-unreal customer-session-gated flow --
 * `ActivationCommand.licenseClaimReference` is a server-assigned public
 * license ID from a real `claimLicense()` call, never the raw key (see the
 * loud warning on that field itself, `ActivationCommandContracts.kt`).
 * Reusing it here as the wire `license_key` would have been a real, silent
 * landmine for whoever wires a real `claimLicense()` next -- every
 * activation through the actual customer-facing UI flow
 * (`ActivationViewModel.onActivate()`) would send a public ID where Owner
 * expects the plaintext secret and get rejected with `LICENSE_NOT_FOUND`
 * every time. So `activateInstallation(ActivationCommand)` stays an honest
 * `TransportNotConfigured` stub -- correctly reflecting that its real
 * dependency (`claimLicense`) isn't real yet either -- and
 * [activateWithLicenseKey] is a separate, additional, correctly-typed real
 * method for the one activation path Owner actually supports today
 * ([DirectLicenseKeyActivationCommand], whose `licenseKey` field is
 * unambiguously the raw secret).
 */
class HttpExternalLicensingTransport(
    baseHttpClient: HttpClient,
    private val configuration: ExternalApiConfiguration,
    private val deviceSigner: DeviceSigner,
) : ExternalLicensingTransport {

    /**
     * A fresh derived client with THIS transport's own real timeout policy
     * installed (`ExternalApiConfiguration`'s own fields) -- never assumes
     * the caller-supplied [baseHttpClient] already carries [HttpTimeout],
     * so a caller-side omission can never silently turn every call
     * unbounded.
     */
    private val httpClient: HttpClient = baseHttpClient.config {
        install(HttpTimeout) {
            requestTimeoutMillis = configuration.requestTimeoutMillis
            connectTimeoutMillis = configuration.connectTimeoutMillis
            socketTimeoutMillis = configuration.responseTimeoutMillis
        }
    }

    private val wireJson = Json { ignoreUnknownKeys = true; isLenient = false }

    /**
     * The one real, wire-verified activation path -- direct, anonymous
     * (no customer session) activation using the raw plaintext license
     * key, exactly matching `activation.py`'s real contract. See this
     * class's own KDoc for why this is a separate method/command type
     * from the interface's `activateInstallation(ActivationCommand)`
     * rather than a reuse of it.
     */
    suspend fun activateWithLicenseKey(command: DirectLicenseKeyActivationCommand): TransportOutcome<ActivationResult> {
        val signablePayload = buildJsonObject {
            put("contract_version", command.clientContractVersion)
            put("request_id", secureRandomHex(16))
            put("correlation_id", secureRandomHex(16))
            put("timestamp", Clock.System.now().toString())
            put("nonce", secureRandomHex(24))
            put("product_code", command.productCode.name)
            put("platform", command.platform.name)
            put("app_version", command.deviceMetadata.appVersion)
            put("installation_id", command.installationIdentity.seed.value)
            put("device_public_key", encodeBase64(deviceSigner.publicKeyBytes()))
            put("device_public_key_algorithm", "ed25519")
            put("license_key", command.licenseKey)
            put("idempotency_key", command.idempotencyKey)
        }

        // The signature covers exactly the fields above -- everything the
        // request will carry except `signature` itself, matching
        // `device_identity.py::_signable_fields()`/`canonicalize_bytes()`
        // (`LeaseCanonicalJson` is a verified byte-for-byte Kotlin port of
        // the same canonical.py the server re-derives its own copy from).
        val canonicalBytes = LeaseCanonicalJson.canonicalizeBytes(signablePayload)
        val signatureB64 = encodeBase64(deviceSigner.sign(canonicalBytes))
        val fullPayload = JsonObject(signablePayload + ("signature" to JsonPrimitive(signatureB64)))

        return try {
            val response = httpClient.post(activationsUrl()) {
                contentType(ContentType.Application.Json)
                setBody(fullPayload.toString())
            }
            interpretActivationResponse(response, command.clientContractVersion)
        } catch (e: CancellationException) {
            throw e
        } catch (e: HttpRequestTimeoutException) {
            TransportOutcome.Timeout
        } catch (e: Exception) {
            classifyTransportException(e)
        }
    }

    private fun activationsUrl(): String = configuration.baseUrl.trimEnd('/') + "/activations"

    private suspend fun interpretActivationResponse(response: HttpResponse, expectedContractVersion: String): TransportOutcome<ActivationResult> {
        if (response.status == HttpStatusCode.TooManyRequests) {
            val retryAfter = response.headers[HttpHeaders.RetryAfter]?.toLongOrNull()
            return TransportOutcome.RateLimited(retryAfter)
        }

        val rawBody = try {
            response.bodyAsText()
        } catch (e: Exception) {
            return TransportOutcome.MalformedResponse("failed to read activation response body: ${e::class.simpleName}")
        }

        val decoded = try {
            wireJson.decodeFromString(ActivationWireResponse.serializer(), rawBody)
        } catch (e: SerializationException) {
            return TransportOutcome.MalformedResponse("failed to decode activation response JSON: ${e::class.simpleName}")
        } catch (e: IllegalArgumentException) {
            return TransportOutcome.MalformedResponse("failed to decode activation response JSON: ${e::class.simpleName}")
        }

        if (decoded.contractVersion != null && decoded.contractVersion != expectedContractVersion) {
            return TransportOutcome.UnsupportedContractVersion(decoded.contractVersion)
        }

        return when {
            response.status == HttpStatusCode.OK && decoded.result == "SUCCESS" -> {
                val assertion = decoded.signedAssertion
                val installationId = decoded.installationId
                when {
                    assertion == null -> TransportOutcome.MalformedResponse("SUCCESS activation response missing signed_assertion")
                    installationId.isNullOrBlank() -> TransportOutcome.MalformedResponse("SUCCESS activation response missing installation_id")
                    decoded.reasonCode == "ACTIVATION_ALREADY_ACTIVE" -> TransportOutcome.Success(ActivationResult.AlreadyActive(assertion, installationId))
                    else -> TransportOutcome.Success(ActivationResult.Approved(assertion, installationId))
                }
            }
            response.status == HttpStatusCode.OK && decoded.result == "PENDING" -> {
                val installationId = decoded.installationId
                if (installationId.isNullOrBlank()) {
                    TransportOutcome.MalformedResponse("PENDING activation response missing installation_id")
                } else {
                    TransportOutcome.Success(ActivationResult.Pending(installationId, decoded.correlationId ?: ""))
                }
            }
            decoded.result == "FAILURE" && !decoded.reasonCode.isNullOrBlank() -> {
                TransportOutcome.Success(ActivationResult.Rejected(LicensingError.fromServerCode(decoded.reasonCode), decoded.correlationId))
            }
            else -> TransportOutcome.MalformedResponse(
                "unrecognized activation response shape: httpStatus=${response.status.value}, result=${decoded.result}, decision=${decoded.decision}",
            )
        }
    }

    private fun classifyTransportException(e: Exception): TransportOutcome<Nothing> {
        val name = e::class.simpleName.orEmpty()
        val message = e.message.orEmpty()
        val looksLikeTls = listOf("SSL", "TLS", "Certificate", "Handshake").any { it in name || it in message }
        return if (looksLikeTls) {
            TransportOutcome.TlsFailure
        } else {
            TransportOutcome.NetworkFailure(message.ifBlank { name }.ifBlank { "unknown network failure" })
        }
    }

    // ---- Not yet real: no corresponding Owner-side authority exists today
    // (`ExternalCustomerSessionContracts.kt`'s own KDoc) -- never fabricates
    // a fake success, matching `DisabledProductionTransport`'s own honest
    // default for every operation outside this task's real, verified scope.

    /**
     * Deliberately still `TransportNotConfigured`, NOT wired to
     * [activateWithLicenseKey] -- see this class's own KDoc. This
     * interface method's [ActivationCommand] carries a customer-session-
     * flow public license ID (`licenseClaimReference`), not the raw
     * secret `activateWithLicenseKey` needs, and its real dependency
     * ([claimLicense]) isn't real either. Forwarding
     * `command.licenseClaimReference` to Owner as `license_key` here
     * would silently send the wrong value the moment `claimLicense`
     * starts returning real public IDs -- every real activation through
     * `ActivationViewModel`'s actual UI flow would then fail with
     * `LICENSE_NOT_FOUND`. Stays honest until a real `claimLicense` (or an
     * explicit design decision to retire the customer-session flow in
     * favor of direct-license-key activation everywhere) makes this
     * method's own contract real.
     */
    override suspend fun activateInstallation(command: ActivationCommand) = TransportOutcome.TransportNotConfigured

    override suspend fun register(request: CustomerRegisterRequest) = TransportOutcome.TransportNotConfigured
    override suspend fun verifyAccount(request: CustomerVerifyAccountRequest) = TransportOutcome.TransportNotConfigured
    override suspend fun signIn(request: CustomerSignInRequest) = TransportOutcome.TransportNotConfigured
    override suspend fun refreshCustomerSession(request: CustomerRefreshSessionRequest) = TransportOutcome.TransportNotConfigured
    override suspend fun signOut(request: CustomerSignOutRequest) = TransportOutcome.TransportNotConfigured
    override suspend fun claimLicense(request: LicenseClaimRequest) = TransportOutcome.TransportNotConfigured
    override suspend fun refreshLease(request: LeaseRefreshRequest) = TransportOutcome.TransportNotConfigured
    override suspend fun listInstallations(request: ListInstallationsRequest): TransportOutcome<List<InstallationDescriptor>> = TransportOutcome.TransportNotConfigured
    override suspend fun deactivateInstallation(request: DeactivateInstallationRequest): TransportOutcome<DeviceManagementOutcome> = TransportOutcome.TransportNotConfigured
    override suspend fun requestReplacement(request: RequestDeviceReplacementRequest): TransportOutcome<DeviceManagementOutcome> = TransportOutcome.TransportNotConfigured
    override suspend fun checkRelease(request: ReleaseCheckRequest): TransportOutcome<ReleaseCheckResult> = TransportOutcome.TransportNotConfigured
}

@OptIn(ExperimentalEncodingApi::class)
private fun encodeBase64(bytes: ByteArray): String = Base64.Default.encode(bytes)

/** Real, minimal decode shape for `activation.py`'s two real response bodies (`process_activation`'s success/pending returns, `_error_response`'s failure body, `owner/app/api_external/routes.py`). Unknown top-level fields (`signing_key_id`, `assertion_version`, `retry_guidance`, `server_timestamp`) are intentionally ignored here -- either redundant with `signed_assertion`'s own fields or not needed to build [ActivationResult]. */
@Serializable
private data class ActivationWireResponse(
    @SerialName("contract_version") val contractVersion: String? = null,
    @SerialName("correlation_id") val correlationId: String? = null,
    val result: String? = null,
    @SerialName("reason_code") val reasonCode: String? = null,
    val decision: String? = null,
    @SerialName("installation_id") val installationId: String? = null,
    @SerialName("signed_assertion") val signedAssertion: SignedAssertionEnvelope? = null,
)
