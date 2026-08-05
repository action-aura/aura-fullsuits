package com.actionaura.retail.licensing

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * M7.17 -- real activation contract (`remote-licensing-api-contract-map.md`,
 * `owner-data-minimization-contract.md`). Field set matches
 * `owner/contracts/activation-request-v1.schema.json` exactly, which is
 * itself `additionalProperties: false` -- this model must never gain a
 * field the real schema does not also allow.
 */
@Serializable
data class ActivationRequest(
    @SerialName("contract_version") val contractVersion: String = "v1",
    @SerialName("request_id") val requestId: String,
    @SerialName("correlation_id") val correlationId: String,
    val timestamp: String,
    val nonce: String,
    @SerialName("product_code") val productCode: LicensingProductCode,
    val platform: LicensingPlatform,
    @SerialName("app_version") val appVersion: String,
    @SerialName("release_channel") val releaseChannel: String? = null,
    @SerialName("installation_id") val installationId: String,
    @SerialName("device_public_key") val devicePublicKey: String,
    @SerialName("device_public_key_algorithm") val devicePublicKeyAlgorithm: String = "ed25519",
    @SerialName("license_key") val licenseKey: String,
    @SerialName("idempotency_key") val idempotencyKey: String,
    val signature: String,
) {
    /** `owner-data-minimization-contract.md`: the license key and signature must never appear in a log line via toString(). */
    override fun toString(): String = "ActivationRequest(requestId=$requestId, correlationId=$correlationId, productCode=$productCode, platform=$platform, installationId=$installationId, licenseKey=<redacted>, idempotencyKey=$idempotencyKey, signature=<redacted>)"
}

/**
 * Real activation outcomes (`activation-idempotency-contract.md`,
 * `installation-credential-contract.md`). `Pending` carries no assertion --
 * matching `_pending_review_response()`, `activation.py:352-408`, which
 * issues no signed assertion until staff approval.
 */
sealed interface ActivationResult {
    data class Approved(val assertion: SignedAssertionEnvelope, val installationPublicId: String) : ActivationResult
    data class AlreadyActive(val assertion: SignedAssertionEnvelope, val installationPublicId: String) : ActivationResult
    data class Pending(val installationPublicId: String, val correlationId: String) : ActivationResult
    data class Rejected(val error: LicensingError, val correlationId: String? = null) : ActivationResult
}
