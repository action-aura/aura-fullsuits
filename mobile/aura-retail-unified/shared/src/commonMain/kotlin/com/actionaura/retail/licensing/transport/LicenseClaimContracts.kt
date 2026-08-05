package com.actionaura.retail.licensing.transport

import com.actionaura.retail.licensing.LicensingProductCode
import com.actionaura.retail.licensing.ResolvedDevicePolicy

/**
 * M9.8 -- shared License-claim contract
 * (`mobile-license-claim-orchestration.md`). The server remains
 * authoritative; the client performs only a local format sanity check
 * before submission, never a real ownership decision.
 */
data class LicenseClaimRequest(
    val customerSessionId: com.actionaura.retail.licensing.transport.ExternalCustomerSessionId,
    val licenseSerial: String,
    val productCode: LicensingProductCode,
) {
    /** The License serial must never appear in a log line (activation-command-contract.md's own rule, applied here too). */
    override fun toString(): String = "LicenseClaimRequest(customerSessionId=$customerSessionId, licenseSerial=<redacted>, productCode=$productCode)"
}

data class LicenseClaimResult(
    val licensePublicId: String,
    val devicePolicy: ResolvedDevicePolicy,
)

/** Real, local-only, structural sanity check -- never a substitute for server validation. */
object LicenseSerialSanityCheck {
    private val ALLOWED_CHARS = ('A'..'Z') + ('0'..'9') + '-'

    fun isPlausible(serial: String): Boolean =
        serial.isNotBlank() && serial.length in 8..64 && serial.all { it in ALLOWED_CHARS }
}
