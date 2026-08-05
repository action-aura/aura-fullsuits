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
 * M9.2 -- the one commonMain external-licensing transport boundary
 * (`external-licensing-transport-contract.md`). Every operation
 * returns [TransportOutcome], never a raw exception. Only operations
 * justified by the real, already-accepted M7/M8 contracts are
 * included -- no invented endpoint, no fabricated URL, no fabricated
 * auth header.
 *
 * This interface has exactly three real implementations in this
 * codebase (`production-transport-availability-rule.md`):
 * [DisabledProductionTransport] (the only one ever wired into
 * production DI), a `commonTest`-only fixture transport
 * (`fixture-transport-report.md`), and this milestone's own narrow,
 * disabled future-HTTP placeholder -- none of the three ever executes
 * a real network call.
 */
interface ExternalLicensingTransport {
    suspend fun register(request: CustomerRegisterRequest): TransportOutcome<ExternalCustomerAccountId>
    suspend fun verifyAccount(request: CustomerVerifyAccountRequest): TransportOutcome<Unit>
    suspend fun signIn(request: CustomerSignInRequest): TransportOutcome<CustomerSignInResult>
    suspend fun refreshCustomerSession(request: CustomerRefreshSessionRequest): TransportOutcome<CustomerSignInResult>
    suspend fun signOut(request: CustomerSignOutRequest): TransportOutcome<Unit>
    suspend fun claimLicense(request: LicenseClaimRequest): TransportOutcome<LicenseClaimResult>
    suspend fun activateInstallation(command: ActivationCommand): TransportOutcome<ActivationResult>
    suspend fun refreshLease(request: LeaseRefreshRequest): TransportOutcome<LeaseRefreshResult>
    suspend fun listInstallations(request: ListInstallationsRequest): TransportOutcome<List<InstallationDescriptor>>
    suspend fun deactivateInstallation(request: DeactivateInstallationRequest): TransportOutcome<DeviceManagementOutcome>
    suspend fun requestReplacement(request: RequestDeviceReplacementRequest): TransportOutcome<DeviceManagementOutcome>
    suspend fun checkRelease(request: ReleaseCheckRequest): TransportOutcome<ReleaseCheckResult>
}
