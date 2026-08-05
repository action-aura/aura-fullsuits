package com.actionaura.retail.licensing.transport

import com.actionaura.retail.licensing.SignedAssertionEnvelope

/**
 * M9.14 -- lease-refresh contract (`lease-refresh-orchestration.md`).
 * M9 orchestrates transport and states only -- it never verifies the
 * lease signature, decides offline grace, or enforces offline expiry
 * (all real M11 work); an unverified signed lease is never trusted as
 * commercial authority here.
 */
data class LeaseRefreshRequest(val installationId: String)

data class LeaseRefreshResult(val assertion: SignedAssertionEnvelope)

/** Real, closed orchestration states -- distinguishes every case the checkpoint's own M9.14 list requires. */
sealed interface LeaseRefreshState {
    data object NoStoredInstallationAuthority : LeaseRefreshState
    data object SecureStorageUnavailable : LeaseRefreshState
    data object ValidRefreshSchedulingInput : LeaseRefreshState
    data object RefreshInProgress : LeaseRefreshState
    data class RefreshSuccess(val assertion: SignedAssertionEnvelope) : LeaseRefreshState
    data object NetworkUnavailable : LeaseRefreshState
    data object ServerUnavailable : LeaseRefreshState
    data object AuthenticationRejected : LeaseRefreshState
    data object InstallationRevoked : LeaseRefreshState
    data object LicenseSuspended : LeaseRefreshState
    data object LicenseExpired : LeaseRefreshState
    data object SubscriptionInactive : LeaseRefreshState
    data object RequiredAppUpdate : LeaseRefreshState
    data class MalformedLeaseResponse(val reason: String) : LeaseRefreshState
    data object RetryScheduled : LeaseRefreshState
    data object TerminalBlock : LeaseRefreshState
}
