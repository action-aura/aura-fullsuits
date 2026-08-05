package com.actionaura.retail.licensing

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * M7.17 -- real check-in/renewal contract (`installation-credential-contract.md`
 * credential type 1, `remote-licensing-api-contract-map.md`). No license key
 * field -- `process_checkin()` (`checkin.py:71-80`) authenticates purely via
 * the installation's device-key signature.
 */
@Serializable
data class CheckInRequest(
    @SerialName("contract_version") val contractVersion: String = "v1",
    @SerialName("request_id") val requestId: String,
    @SerialName("correlation_id") val correlationId: String,
    val timestamp: String,
    val nonce: String,
    @SerialName("installation_id") val installationId: String,
    val signature: String,
) {
    override fun toString(): String = "CheckInRequest(requestId=$requestId, correlationId=$correlationId, installationId=$installationId, signature=<redacted>)"
}

sealed interface CheckInResult {
    data class Accepted(val assertion: SignedAssertionEnvelope) : CheckInResult
    data class Rejected(val error: LicensingError, val correlationId: String? = null) : CheckInResult
}

/**
 * Real deactivation contract (`deactivation.py:37-115`) -- idempotent by
 * construction on the server; no license key required.
 */
@Serializable
data class DeactivationRequest(
    @SerialName("contract_version") val contractVersion: String = "v1",
    @SerialName("request_id") val requestId: String,
    @SerialName("correlation_id") val correlationId: String,
    val timestamp: String,
    val nonce: String,
    @SerialName("installation_id") val installationId: String,
    @SerialName("idempotency_key") val idempotencyKey: String,
    val signature: String,
) {
    override fun toString(): String = "DeactivationRequest(requestId=$requestId, correlationId=$correlationId, installationId=$installationId, idempotencyKey=$idempotencyKey, signature=<redacted>)"
}

sealed interface DeactivationResult {
    data class Accepted(val installationId: String) : DeactivationResult
    data class Rejected(val error: LicensingError, val correlationId: String? = null) : DeactivationResult
}
