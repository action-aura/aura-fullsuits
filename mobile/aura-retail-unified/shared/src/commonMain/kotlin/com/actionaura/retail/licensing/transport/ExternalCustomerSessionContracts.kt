package com.actionaura.retail.licensing.transport

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * M9.6 -- shared external Customer session contract, structurally
 * separate from Aura Owner employee sessions, local Retail user
 * sessions, Installation credentials, and signed License leases
 * (`owner-internal-vs-customer-boundary.md`, `external-customer-
 * session-contract.md`). Real, currently-unimplemented server-side
 * authority per `OWNER-EXTERNAL-CUSTOMER-IDENTITY-AND-LICENSING-
 * BOUNDARY-SPEC.md` -- these are shared client-side contract shapes
 * only, no real transport execution.
 */
@Serializable
enum class ExternalCustomerSessionStatus {
    NOT_CONFIGURED, SIGNED_OUT, AUTHENTICATING, VERIFICATION_REQUIRED, AUTHENTICATED,
    REFRESHING, EXPIRED, REVOKED, DISABLED, LOCKED, TRANSPORT_UNAVAILABLE, ERROR,
}

/** Opaque wrapper -- never redacted-away entirely (needed for scoping requests) but never exposed in a log/UI-state via toString(). */
data class ExternalCustomerAccountId(val value: String) {
    override fun toString(): String = "ExternalCustomerAccountId(<redacted>)"
}

data class ExternalCustomerSessionId(val value: String) {
    override fun toString(): String = "ExternalCustomerSessionId(<redacted>)"
}

/** Real secret material -- an opaque access token. Never logged, never in UiState, never in navigation arguments (M9.6's own requirement). */
class ExternalCustomerAccessCredential(private val raw: String) {
    fun expose(): String = raw
    override fun toString(): String = "ExternalCustomerAccessCredential(<redacted>)"
    override fun equals(other: Any?): Boolean = other is ExternalCustomerAccessCredential && other.raw == raw
    override fun hashCode(): Int = raw.hashCode()
}

class ExternalCustomerRefreshCredential(private val raw: String) {
    fun expose(): String = raw
    override fun toString(): String = "ExternalCustomerRefreshCredential(<redacted>)"
    override fun equals(other: Any?): Boolean = other is ExternalCustomerRefreshCredential && other.raw == raw
    override fun hashCode(): Int = raw.hashCode()
}

data class CustomerSessionExpiry(val expiresAtIso8601: String)

/** Real, immutable session snapshot -- redacts every credential field in toString(). */
data class ExternalCustomerSession(
    val accountId: ExternalCustomerAccountId,
    val sessionId: ExternalCustomerSessionId,
    val accessCredential: ExternalCustomerAccessCredential,
    val refreshCredential: ExternalCustomerRefreshCredential?,
    val expiry: CustomerSessionExpiry,
    val status: ExternalCustomerSessionStatus,
) {
    override fun toString(): String = "ExternalCustomerSession(accountId=$accountId, sessionId=$sessionId, status=$status, expiry=$expiry, accessCredential=<redacted>, refreshCredential=<redacted>)"
}

/**
 * Closed authentication-state model for presentation
 * (`external-customer-session-contract.md`). Distinct from
 * [ExternalCustomerSessionStatus] -- this is the orchestrator's own
 * higher-level state, which folds transport outcomes into it.
 */
sealed interface CustomerAuthenticationState {
    data object NotConfigured : CustomerAuthenticationState
    data object SignedOut : CustomerAuthenticationState
    data object Authenticating : CustomerAuthenticationState
    data class VerificationRequired(val accountId: ExternalCustomerAccountId) : CustomerAuthenticationState
    data class Authenticated(val session: ExternalCustomerSession) : CustomerAuthenticationState
    data object Refreshing : CustomerAuthenticationState
    data object Expired : CustomerAuthenticationState
    data object Revoked : CustomerAuthenticationState
    data object Disabled : CustomerAuthenticationState
    data object Locked : CustomerAuthenticationState
    data object TransportUnavailable : CustomerAuthenticationState
    data class Error(val error: com.actionaura.retail.licensing.LicensingError) : CustomerAuthenticationState
}

// ---- Real request/result shapes for the sign-in/register/refresh/sign-out transport operations ----

/** Real, bounded fields only -- no business data (owner-data-minimization-contract.md discipline extended to M9). */
@Serializable
data class CustomerRegisterRequest(
    @SerialName("email") val email: String,
    @SerialName("password") val password: String,
    @SerialName("customer_reference") val customerReference: String? = null,
) {
    override fun toString(): String = "CustomerRegisterRequest(email=$email, password=<redacted>, customerReference=$customerReference)"
}

@Serializable
data class CustomerVerifyAccountRequest(
    @SerialName("account_id") val accountId: String,
    @SerialName("verification_code") val verificationCode: String,
) {
    override fun toString(): String = "CustomerVerifyAccountRequest(accountId=$accountId, verificationCode=<redacted>)"
}

@Serializable
data class CustomerSignInRequest(
    @SerialName("email") val email: String,
    @SerialName("password") val password: String,
) {
    override fun toString(): String = "CustomerSignInRequest(email=$email, password=<redacted>)"
}

data class CustomerSignInResult(val session: ExternalCustomerSession)

data class CustomerRefreshSessionRequest(val refreshCredential: ExternalCustomerRefreshCredential) {
    override fun toString(): String = "CustomerRefreshSessionRequest(refreshCredential=<redacted>)"
}

data class CustomerSignOutRequest(val sessionId: ExternalCustomerSessionId)
