package com.actionaura.retail.licensing.transport

import com.actionaura.retail.licensing.ActivationResult
import com.actionaura.retail.licensing.AssertionPayload
import com.actionaura.retail.licensing.LicenseStatus
import com.actionaura.retail.licensing.LicensingError
import com.actionaura.retail.licensing.LicensingFixtures
import com.actionaura.retail.licensing.LicensingPlatform
import com.actionaura.retail.licensing.LicensingProductCode
import com.actionaura.retail.licensing.ResolvedDevicePolicy
import com.actionaura.retail.licensing.ServerReasonCode

/**
 * M9.28 -- sanitized, versioned fixtures for the M9 orchestration
 * layer, built on top of the real M7.18/M8.14 fixture sets rather than
 * duplicating them. No real Customer PII, passwords, License serials,
 * credentials, tokens, or signing keys -- every value below is
 * obviously fake (`FIXTURE`/`FAKE` markers, mirroring
 * `LicensingFixtures`'s own real discipline).
 */
object M9SanitizedFixtures {
    const val FIXTURE_SET_VERSION = "m9-orchestration-fixtures-v1"

    fun customerSession(status: ExternalCustomerSessionStatus = ExternalCustomerSessionStatus.AUTHENTICATED) = ExternalCustomerSession(
        accountId = ExternalCustomerAccountId("fixture-account-0001"),
        sessionId = ExternalCustomerSessionId("fixture-session-0001"),
        accessCredential = ExternalCustomerAccessCredential("FIXTURE_ACCESS_TOKEN_NOT_REAL"),
        refreshCredential = ExternalCustomerRefreshCredential("FIXTURE_REFRESH_TOKEN_NOT_REAL"),
        expiry = CustomerSessionExpiry("2026-09-05T00:00:00Z"),
        status = status,
    )

    fun devicePolicy(remaining: Int = 1, total: Int = 3, active: Int = total - remaining) = ResolvedDevicePolicy(
        policyVersion = 1,
        licensePublicId = "fixture-license-public-id",
        productCode = LicensingProductCode.AURA_RETAIL,
        allowedPlatforms = listOf("WINDOWS", "ANDROID"),
        totalActiveInstallationLimit = total,
        currentActiveInstallationCount = active,
        remainingInstallationSlots = remaining,
        voluntaryDeactivationAllowed = true,
        replacementAllowed = true,
        policyEffectiveAt = "2026-08-05T00:00:00Z",
    )

    fun licenseClaimResult(remaining: Int = 1) = LicenseClaimResult(
        licensePublicId = "fixture-license-public-id",
        devicePolicy = devicePolicy(remaining = remaining),
    )

    /** Reuses the real, already-audited M7.18 assertion fixture -- never duplicates fake signature material. */
    fun activationApproved(platform: LicensingPlatform = LicensingPlatform.ANDROID): ActivationResult.Approved =
        ActivationResult.Approved(assertion = LicensingFixtures.validAssertion(), installationPublicId = "fixture-installation-public-id")

    fun activationAlreadyActive(): ActivationResult.AlreadyActive =
        ActivationResult.AlreadyActive(assertion = LicensingFixtures.validAssertion(), installationPublicId = "fixture-installation-public-id")

    fun activationRejected(code: ServerReasonCode): ActivationResult.Rejected =
        ActivationResult.Rejected(error = LicensingError.FromServer(code))

    fun leaseRefreshResult() = LeaseRefreshResult(assertion = LicensingFixtures.validAssertion())
}
