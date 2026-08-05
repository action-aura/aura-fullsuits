package com.actionaura.retail.licensing.transport

/**
 * M9.14 -- shared lease-refresh coordinator
 * (`lease-refresh-orchestration.md`). Orchestrates transport and
 * state only -- never verifies the lease signature, decides offline
 * grace, or enforces offline expiry (real M11 work). An unverified
 * signed lease returned here is never treated as trusted commercial
 * authority by this class.
 */
class LeaseRefreshOrchestrator(
    private val transport: ExternalLicensingTransport,
    private val hasStoredInstallationAuthority: suspend (installationId: String) -> Boolean,
) {
    suspend fun refresh(installationId: String?): LeaseRefreshState {
        if (installationId == null) return LeaseRefreshState.NoStoredInstallationAuthority
        if (!hasStoredInstallationAuthority(installationId)) return LeaseRefreshState.SecureStorageUnavailable

        return when (val outcome = transport.refreshLease(LeaseRefreshRequest(installationId))) {
            is TransportOutcome.Success -> LeaseRefreshState.RefreshSuccess(outcome.value.assertion)
            is TransportOutcome.TransportNotConfigured -> LeaseRefreshState.ServerUnavailable
            is TransportOutcome.NetworkFailure -> LeaseRefreshState.NetworkUnavailable
            is TransportOutcome.Timeout -> LeaseRefreshState.NetworkUnavailable
            is TransportOutcome.RateLimited -> LeaseRefreshState.RetryScheduled
            is TransportOutcome.AuthenticationRejection -> LeaseRefreshState.AuthenticationRejected
            is TransportOutcome.MalformedResponse -> LeaseRefreshState.MalformedLeaseResponse(outcome.reason)
            is TransportOutcome.BusinessRejection -> businessRejectionToState(outcome)
            is TransportOutcome.TlsFailure -> LeaseRefreshState.TerminalBlock
            is TransportOutcome.UnsupportedContractVersion -> LeaseRefreshState.RequiredAppUpdate
            is TransportOutcome.Cancelled -> LeaseRefreshState.TerminalBlock
        }
    }

    private fun businessRejectionToState(outcome: TransportOutcome.BusinessRejection): LeaseRefreshState =
        when (val presentation = outcome.error.toPresentationError()) {
            is PresentationError.Installation.Revoked -> LeaseRefreshState.InstallationRevoked
            is PresentationError.License.Suspended -> LeaseRefreshState.LicenseSuspended
            is PresentationError.License.Expired -> LeaseRefreshState.LicenseExpired
            is PresentationError.License.SubscriptionInactive -> LeaseRefreshState.SubscriptionInactive
            else -> LeaseRefreshState.TerminalBlock
        }
}
