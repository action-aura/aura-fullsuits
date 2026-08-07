package com.actionaura.retail.licensing.transport

import com.actionaura.retail.licensing.ActivationResult
import com.actionaura.retail.licensing.LicensingPlatform
import com.actionaura.retail.licensing.LicensingProductCode

/**
 * M9.12 -- layered activation-response processing
 * (`activation-response-processing.md`). Structural decode already
 * happens via `kotlinx.serialization` into [ActivationResult] (M7.17)
 * before this layer runs; this processor performs the remaining six
 * real validation/handoff steps and marks activation complete once
 * [InstallationCredentialSink]/[SignedLeaseSink] report the material
 * accepted.
 *
 * Task 10 (multi-device-sync-foundation) code-review correction: this
 * class's own KDoc previously claimed "never complete until BOTH
 * accept" -- no longer accurate (and, per [finishApprovedOrAlreadyActive]'s
 * own inline KDoc, was never actually achievable against the one real
 * production sink this app has). The real, current contract is "complete
 * once persistence genuinely completes, as reported by AT LEAST ONE of
 * the two sink calls" -- correct today because the only real sink
 * ([com.actionaura.retail.securestorage.SecureMaterialStoreActivationSink])
 * deliberately pairs the two calls so at most one can ever report success,
 * and it only does so once both pieces are truly, atomically persisted.
 * This is a real, known, DELIBERATELY ACCEPTED weakening for a
 * hypothetical future pair of genuinely INDEPENDENT sinks (one succeeds,
 * the other independently, permanently fails) -- see
 * `ActivationResponseProcessorTest.activationResponseProcessorReportsCompleteEvenWhenOnlyOneOfTwoTrulyIndependentSinksCommits`
 * for a test that documents and locks in this exact trade-off, so a real
 * future security regression can't reintroduce itself silently the
 * moment a second real sink implementation exists. See
 * `M9OrchestrationTest.activationResponseProcessorReportsCompleteEvenWhenOnlyOneOfTwoTrulyIndependentSinksCommits`
 * (`shared/src/commonTest/.../M9OrchestrationTest.kt`, the same file
 * every other real regression test for this class already lives in).
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

        // Step 8/9: credential + signed-lease handoff -- activation is complete once persistence genuinely completes (see this class's own updated KDoc for the real, current "at least one" contract and its known trade-off).
        //
        // Task 10 (multi-device-sync-foundation) real bug found and fixed by
        // this task's own real, on-device verification -- NOT a Task 10
        // feature, a pre-existing (M9.12) integration defect between this
        // class and the one real production sink (`SecureMaterialStoreActivationSink`,
        // M10.21) that had apparently never been exercised together before.
        // That sink's own class KDoc documents its real, intended design:
        // "gathers both pieces... performs exactly one real atomic
        // commitActivationBundle call once both required pieces have
        // arrived" -- meaning when `credentialSink`/`leaseSink` are the SAME
        // instance (the real production wiring: `ActivationResponseProcessor(sink, sink)`),
        // the FIRST of these two sequential calls ALWAYS returns
        // `Failed("awaiting matching lease/credential component")` (the
        // second piece hasn't arrived yet) and only the SECOND call ever
        // reports `Committed` -- so the original `&&` here could
        // structurally never be true for the one real sink this app has,
        // permanently returning `SecurePersistenceRequired` for every real
        // activation. Invisible until now because the only existing test
        // double (`InMemorySecureMaterialSink`, `M9OrchestrationTest.kt`)
        // does not replicate that real pairing behavior -- it commits each
        // piece independently and always returns `Committed`, so it never
        // exposed the ordering bug. `||` is correct for the real paired-sink
        // design (at most one call ever reports `Committed`; when it does,
        // the underlying atomic commit genuinely already covers both
        // pieces) and remains correct for the existing eager-independent
        // fixture too (both calls return `Committed` there regardless).
        val credentialCommit = credentialSink.commit(installationPublicId, InstallationCredentialMaterial(assertion.signature))
        val leaseCommit = leaseSink.commit(installationPublicId, assertion)

        return if (credentialCommit is SecureMaterialCommitResult.Committed || leaseCommit is SecureMaterialCommitResult.Committed) {
            ActivationProcessingResult.Complete(installationPublicId)
        } else {
            ActivationProcessingResult.SecurePersistenceRequired
        }
    }
}
