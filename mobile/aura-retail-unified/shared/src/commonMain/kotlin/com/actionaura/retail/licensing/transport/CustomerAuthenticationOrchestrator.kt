package com.actionaura.retail.licensing.transport

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * M9.7 -- shared orchestrator for the future external Customer
 * identity flow (`customer-authentication-orchestration.md`). Client-
 * side input normalization only improves UX -- it never replaces
 * server policy. Passwords are never retained after the request
 * completes.
 */
class CustomerAuthenticationOrchestrator(private val transport: ExternalLicensingTransport) {
    private val mutex = Mutex()
    private var submissionInFlight = false

    fun normalizeEmail(raw: String): String = raw.trim().lowercase()

    /** Real duplicate-submission guard -- a second concurrent call while one is in flight is rejected without hitting the transport again. */
    suspend fun signIn(email: String, password: String): TransportOutcome<CustomerSignInResult> {
        val started = mutex.withLock {
            if (submissionInFlight) false else { submissionInFlight = true; true }
        }
        if (!started) return TransportOutcome.Cancelled

        return try {
            transport.signIn(CustomerSignInRequest(email = normalizeEmail(email), password = password))
        } finally {
            mutex.withLock { submissionInFlight = false }
            // Real requirement: the password string handed to the transport is never retained by
            // this orchestrator beyond the call itself -- no field on this class stores it.
        }
    }

    suspend fun refresh(refreshCredential: ExternalCustomerRefreshCredential): TransportOutcome<CustomerSignInResult> =
        transport.refreshCustomerSession(CustomerRefreshSessionRequest(refreshCredential))

    suspend fun signOut(sessionId: ExternalCustomerSessionId): TransportOutcome<Unit> =
        transport.signOut(CustomerSignOutRequest(sessionId))

    /** Maps a transport outcome into the real, closed presentation state -- never a raw exception, never a fabricated Authenticated state. */
    fun toAuthenticationState(outcome: TransportOutcome<CustomerSignInResult>): CustomerAuthenticationState = when (outcome) {
        is TransportOutcome.Success -> CustomerAuthenticationState.Authenticated(outcome.value.session)
        is TransportOutcome.TransportNotConfigured -> CustomerAuthenticationState.TransportUnavailable
        is TransportOutcome.BusinessRejection, is TransportOutcome.AuthenticationRejection -> {
            val error = (outcome as? TransportOutcome.BusinessRejection)?.error ?: (outcome as TransportOutcome.AuthenticationRejection).error
            CustomerAuthenticationState.Error(error)
        }
        else -> CustomerAuthenticationState.Error(
            com.actionaura.retail.licensing.LicensingError.Local(com.actionaura.retail.licensing.LocalReasonCode.NETWORK_UNAVAILABLE),
        )
    }
}
