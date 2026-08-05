package com.actionaura.retail.licensing.transport

import com.actionaura.retail.licensing.ActivationResult
import com.actionaura.retail.licensing.DeactivateInstallationRequest
import com.actionaura.retail.licensing.DeviceManagementOutcome
import com.actionaura.retail.licensing.InstallationDescriptor
import com.actionaura.retail.licensing.ReleaseCheckRequest
import com.actionaura.retail.licensing.ReleaseCheckResult
import com.actionaura.retail.licensing.RequestDeviceReplacementRequest
import com.actionaura.retail.licensing.ListInstallationsRequest

/**
 * M9.3 -- the real, only production-safe [ExternalLicensingTransport]
 * implementation today (`production-transport-availability-rule.md`).
 * Because Aura Owner's real external Customer/licensing API does not
 * exist yet (`OWNER-EXTERNAL-CUSTOMER-IDENTITY-AND-LICENSING-BOUNDARY-
 * SPEC.md`), every operation deterministically returns
 * [TransportOutcome.TransportNotConfigured] -- never a fake success,
 * never a fabricated credential, never a fabricated signed lease.
 * This is the one and only transport wired into release production
 * dependency injection.
 */
class DisabledProductionTransport : ExternalLicensingTransport {
    private fun <T> notConfigured(): TransportOutcome<T> = TransportOutcome.TransportNotConfigured

    override suspend fun register(request: CustomerRegisterRequest) = notConfigured<com.actionaura.retail.licensing.transport.ExternalCustomerAccountId>()
    override suspend fun verifyAccount(request: CustomerVerifyAccountRequest) = notConfigured<Unit>()
    override suspend fun signIn(request: CustomerSignInRequest) = notConfigured<CustomerSignInResult>()
    override suspend fun refreshCustomerSession(request: CustomerRefreshSessionRequest) = notConfigured<CustomerSignInResult>()
    override suspend fun signOut(request: CustomerSignOutRequest) = notConfigured<Unit>()
    override suspend fun claimLicense(request: LicenseClaimRequest) = notConfigured<LicenseClaimResult>()
    override suspend fun activateInstallation(command: ActivationCommand) = notConfigured<ActivationResult>()
    override suspend fun refreshLease(request: LeaseRefreshRequest) = notConfigured<LeaseRefreshResult>()
    override suspend fun listInstallations(request: ListInstallationsRequest) = notConfigured<List<InstallationDescriptor>>()
    override suspend fun deactivateInstallation(request: DeactivateInstallationRequest) = notConfigured<DeviceManagementOutcome>()
    override suspend fun requestReplacement(request: RequestDeviceReplacementRequest) = notConfigured<DeviceManagementOutcome>()
    override suspend fun checkRelease(request: ReleaseCheckRequest) = notConfigured<ReleaseCheckResult>()
}
