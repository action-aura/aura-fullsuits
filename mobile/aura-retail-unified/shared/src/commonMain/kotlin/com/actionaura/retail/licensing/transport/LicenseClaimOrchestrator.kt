package com.actionaura.retail.licensing.transport

import com.actionaura.retail.licensing.LicensingProductCode

/**
 * M9.8 -- shared License-claim orchestrator
 * (`mobile-license-claim-orchestration.md`). Local format sanity
 * check only -- the server remains authoritative for real ownership.
 */
sealed interface LicenseClaimOutcome {
    data class Claimed(val result: LicenseClaimResult) : LicenseClaimOutcome
    data object LocallyImplausible : LicenseClaimOutcome
    data class Rejected(val error: PresentationError) : LicenseClaimOutcome
}

class LicenseClaimOrchestrator(private val transport: ExternalLicensingTransport) {
    suspend fun claim(
        customerSessionId: ExternalCustomerSessionId,
        rawSerial: String,
        productCode: LicensingProductCode,
    ): LicenseClaimOutcome {
        val trimmed = rawSerial.trim()
        if (!LicenseSerialSanityCheck.isPlausible(trimmed)) return LicenseClaimOutcome.LocallyImplausible

        val outcome = transport.claimLicense(LicenseClaimRequest(customerSessionId, trimmed, productCode))
        return when (outcome) {
            is TransportOutcome.Success -> LicenseClaimOutcome.Claimed(outcome.value)
            else -> LicenseClaimOutcome.Rejected(outcome.toPresentationError() ?: PresentationError.Unknown("unexpected success-shaped non-success outcome"))
        }
    }
}
