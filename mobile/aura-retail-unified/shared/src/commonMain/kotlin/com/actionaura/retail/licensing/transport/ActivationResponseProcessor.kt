package com.actionaura.retail.licensing.transport

import com.actionaura.retail.licensing.ActivationResult
import com.actionaura.retail.licensing.LicensingPlatform
import com.actionaura.retail.licensing.LicensingProductCode

/**
 * M9.12 -- layered activation-response processing
 * (`activation-response-processing.md`). Structural decode already
 * happens via `kotlinx.serialization` into [ActivationResult] (M7.17)
 * before this layer runs; this processor performs the remaining six
 * real validation/handoff steps and never marks activation complete
 * until [InstallationCredentialSink]/[SignedLeaseSink] accept the
 * material.
 */
sealed interface ActivationProcessingResult {
    data class Complete(val installationId: String) : ActivationProcessingResult
    data object SecurePersistenceRequired : ActivationProcessingResult
    data class ContractViolation(val reason: String) : ActivationProcessingResult
    data class Rejected(val error: PresentationError) : ActivationProcessingResult
    data object Pending : ActivationProcessingResult
}

class ActivationResponseProcessor(
    private val credentialSink: InstallationCredentialSink,
    private val leaseSink: SignedLeaseSink,
) {
    suspend fun process(
        result: ActivationResult,
        expectedProduct: LicensingProductCode,
        expectedPlatform: LicensingPlatform,
    ): ActivationProcessingResult = when (result) {
        is ActivationResult.Rejected -> ActivationProcessingResult.Rejected(result.error.toPresentationError())
        is ActivationResult.Pending -> ActivationProcessingResult.Pending
        is ActivationResult.Approved -> finishApprovedOrAlreadyActive(
            result.installationPublicId, result.assertion.payload.productCode, result.assertion.payload.platform,
            expectedProduct, expectedPlatform, result,
        )
        is ActivationResult.AlreadyActive -> finishApprovedOrAlreadyActive(
            result.installationPublicId, result.assertion.payload.productCode, result.assertion.payload.platform,
            expectedProduct, expectedPlatform, result,
        )
    }

    private suspend fun finishApprovedOrAlreadyActive(
        installationPublicId: String,
        responseProduct: LicensingProductCode,
        responsePlatform: LicensingPlatform,
        expectedProduct: LicensingProductCode,
        expectedPlatform: LicensingPlatform,
        result: ActivationResult,
    ): ActivationProcessingResult {
        // Step 3/4: Product/Platform validation -- the response must describe the same command we sent.
        if (responseProduct != expectedProduct) return ActivationProcessingResult.ContractViolation("response productCode ($responseProduct) does not match the requested product ($expectedProduct)")
        if (responsePlatform != expectedPlatform) return ActivationProcessingResult.ContractViolation("response platform ($responsePlatform) does not match the requested platform ($expectedPlatform)")
        // Step 5: Installation identity validation.
        if (installationPublicId.isBlank()) return ActivationProcessingResult.ContractViolation("empty installationPublicId in a successful response")

        val assertion = when (result) {
            is ActivationResult.Approved -> result.assertion
            is ActivationResult.AlreadyActive -> result.assertion
            else -> error("unreachable")
        }

        // Step 8/9: credential + signed-lease handoff -- activation is never complete until both accept.
        val credentialCommit = credentialSink.commit(installationPublicId, InstallationCredentialMaterial(assertion.signature))
        val leaseCommit = leaseSink.commit(installationPublicId, assertion)

        return if (credentialCommit is SecureMaterialCommitResult.Committed && leaseCommit is SecureMaterialCommitResult.Committed) {
            ActivationProcessingResult.Complete(installationPublicId)
        } else {
            ActivationProcessingResult.SecurePersistenceRequired
        }
    }
}
