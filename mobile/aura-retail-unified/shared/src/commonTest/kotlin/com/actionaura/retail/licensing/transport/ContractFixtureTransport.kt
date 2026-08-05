package com.actionaura.retail.licensing.transport

import com.actionaura.retail.licensing.ActivationResult
import com.actionaura.retail.licensing.DeactivateInstallationRequest
import com.actionaura.retail.licensing.DeviceManagementOutcome
import com.actionaura.retail.licensing.InstallationDescriptor
import com.actionaura.retail.licensing.ListInstallationsRequest
import com.actionaura.retail.licensing.ReleaseCheckRequest
import com.actionaura.retail.licensing.ReleaseCheckResult
import com.actionaura.retail.licensing.RequestDeviceReplacementRequest

/**
 * M9.28 -- test-only, deterministic, fixture-backed transport
 * (`fixture-transport-report.md`). Never referenced from any
 * `commonMain`/`androidMain` file -- lives exclusively in
 * `commonTest`, structurally invisible to any production build
 * (`production-transport-availability-rule.md`). No real credentials,
 * PII, License serials, tokens, or private keys anywhere in this file
 * or the scenario fixtures built from it.
 *
 * Configurable per-operation: each real scenario test configures only
 * the operations it needs; every unconfigured operation defaults to
 * [TransportOutcome.TransportNotConfigured] -- the same honest default
 * as production, so a test can never accidentally rely on an
 * un-configured fixture behaving like a real success.
 */
class ContractFixtureTransport(
    private val onRegister: suspend (CustomerRegisterRequest) -> TransportOutcome<ExternalCustomerAccountId> = { TransportOutcome.TransportNotConfigured },
    private val onVerifyAccount: suspend (CustomerVerifyAccountRequest) -> TransportOutcome<Unit> = { TransportOutcome.TransportNotConfigured },
    private val onSignIn: suspend (CustomerSignInRequest) -> TransportOutcome<CustomerSignInResult> = { TransportOutcome.TransportNotConfigured },
    private val onRefreshCustomerSession: suspend (CustomerRefreshSessionRequest) -> TransportOutcome<CustomerSignInResult> = { TransportOutcome.TransportNotConfigured },
    private val onSignOut: suspend (CustomerSignOutRequest) -> TransportOutcome<Unit> = { TransportOutcome.TransportNotConfigured },
    private val onClaimLicense: suspend (LicenseClaimRequest) -> TransportOutcome<LicenseClaimResult> = { TransportOutcome.TransportNotConfigured },
    private val onActivateInstallation: suspend (ActivationCommand) -> TransportOutcome<ActivationResult> = { TransportOutcome.TransportNotConfigured },
    private val onRefreshLease: suspend (LeaseRefreshRequest) -> TransportOutcome<LeaseRefreshResult> = { TransportOutcome.TransportNotConfigured },
    private val onListInstallations: suspend (ListInstallationsRequest) -> TransportOutcome<List<InstallationDescriptor>> = { TransportOutcome.TransportNotConfigured },
    private val onDeactivateInstallation: suspend (DeactivateInstallationRequest) -> TransportOutcome<DeviceManagementOutcome> = { TransportOutcome.TransportNotConfigured },
    private val onRequestReplacement: suspend (RequestDeviceReplacementRequest) -> TransportOutcome<DeviceManagementOutcome> = { TransportOutcome.TransportNotConfigured },
    private val onCheckRelease: suspend (ReleaseCheckRequest) -> TransportOutcome<ReleaseCheckResult> = { TransportOutcome.TransportNotConfigured },
) : ExternalLicensingTransport {
    var activationCallCount: Int = 0
        private set

    override suspend fun register(request: CustomerRegisterRequest) = onRegister(request)
    override suspend fun verifyAccount(request: CustomerVerifyAccountRequest) = onVerifyAccount(request)
    override suspend fun signIn(request: CustomerSignInRequest) = onSignIn(request)
    override suspend fun refreshCustomerSession(request: CustomerRefreshSessionRequest) = onRefreshCustomerSession(request)
    override suspend fun signOut(request: CustomerSignOutRequest) = onSignOut(request)
    override suspend fun claimLicense(request: LicenseClaimRequest) = onClaimLicense(request)
    override suspend fun activateInstallation(command: ActivationCommand): TransportOutcome<ActivationResult> {
        activationCallCount++
        return onActivateInstallation(command)
    }
    override suspend fun refreshLease(request: LeaseRefreshRequest) = onRefreshLease(request)
    override suspend fun listInstallations(request: ListInstallationsRequest) = onListInstallations(request)
    override suspend fun deactivateInstallation(request: DeactivateInstallationRequest) = onDeactivateInstallation(request)
    override suspend fun requestReplacement(request: RequestDeviceReplacementRequest) = onRequestReplacement(request)
    override suspend fun checkRelease(request: ReleaseCheckRequest) = onCheckRelease(request)
}
