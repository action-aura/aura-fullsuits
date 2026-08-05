package com.actionaura.retail.ui.activation

import com.actionaura.retail.licensing.DeviceMetadata
import com.actionaura.retail.licensing.InstallationIdentity
import com.actionaura.retail.licensing.LicensingPlatform
import com.actionaura.retail.licensing.LicensingProductCode
import com.actionaura.retail.licensing.ResolvedDevicePolicy
import com.actionaura.retail.licensing.transport.ActivationCommand
import com.actionaura.retail.licensing.transport.ActivationIdempotencyCoordinator
import com.actionaura.retail.licensing.transport.ActivationResponseProcessor
import com.actionaura.retail.licensing.transport.ActivationState
import com.actionaura.retail.licensing.transport.ActivationAction
import com.actionaura.retail.licensing.transport.CustomerAuthenticationOrchestrator
import com.actionaura.retail.licensing.transport.CustomerAuthenticationState
import com.actionaura.retail.licensing.transport.ExternalCustomerSessionId
import com.actionaura.retail.licensing.transport.ExternalLicensingTransport
import com.actionaura.retail.licensing.transport.ActivationProcessingResult
import com.actionaura.retail.licensing.transport.ActivationTransitionResult
import com.actionaura.retail.licensing.transport.InstallationCredentialSink
import com.actionaura.retail.licensing.transport.LicenseClaimOrchestrator
import com.actionaura.retail.licensing.transport.LicenseClaimOutcome
import com.actionaura.retail.licensing.transport.NoSecureStorageAvailableSink
import com.actionaura.retail.licensing.transport.PresentationError
import com.actionaura.retail.licensing.transport.SignedLeaseSink
import com.actionaura.retail.licensing.transport.TransportOutcome
import com.actionaura.retail.licensing.transport.transition
import com.actionaura.retail.licensing.transport.toPresentationError
import com.actionaura.retail.presentation.AuraViewModel
import com.actionaura.retail.presentation.UiEffect
import kotlinx.coroutines.Dispatchers

/**
 * M9.18 -- real, shared activation presentation contract
 * (`activation-presentation-contract.md`). Every field is
 * presentation-safe; no password, access/refresh credential, License
 * serial after submission, Installation credential, raw signed lease,
 * private key, or idempotency secret material is ever placed here.
 */
data class ActivationUiState(
    val activationState: ActivationState = ActivationState.NOT_STARTED,
    val emailInput: String = "",
    val licenseSerialInput: String = "",
    val deviceLabelInput: String = "",
    val devicePolicy: ResolvedDevicePolicy? = null,
    val presentationError: PresentationError? = null,
    val retryAvailable: Boolean = false,
    val submitting: Boolean = false,
)

sealed interface ActivationEffect : UiEffect {
    data object NavigateToActivationComplete : ActivationEffect
    data object NavigateToSupport : ActivationEffect
    data object ClearSecureInput : ActivationEffect
}

class ActivationViewModel(
    private val transport: ExternalLicensingTransport,
    private val productCode: LicensingProductCode,
    private val platform: LicensingPlatform,
    /**
     * Real Installation identity generation is ANDROID_ADAPTER/
     * IOS_ADAPTER work (M8 gap #8/#9), not implemented in M9. The
     * default `{ null }` is the real, honest current state -- no
     * fabricated placeholder identity is ever synthesized by this
     * class or by the Compose layer (`activation-compose-flow.md`).
     */
    private val installationIdentityProvider: () -> InstallationIdentity? = { null },
    /** Real, honest production default -- no platform secure storage exists yet (M10 scope, secure-material-handoff-boundary.md). */
    credentialSink: InstallationCredentialSink = NoSecureStorageAvailableSink(),
    leaseSink: SignedLeaseSink = NoSecureStorageAvailableSink(),
    dispatcher: kotlinx.coroutines.CoroutineDispatcher = Dispatchers.Default,
) : AuraViewModel<ActivationUiState, ActivationEffect>(ActivationUiState(), dispatcher) {

    private val authOrchestrator = CustomerAuthenticationOrchestrator(transport)
    private val claimOrchestrator = LicenseClaimOrchestrator(transport)
    private val idempotency = ActivationIdempotencyCoordinator(keyGenerator = { randomIdempotencyKey() })
    private val responseProcessor = ActivationResponseProcessor(credentialSink, leaseSink)

    private var customerSessionId: ExternalCustomerSessionId? = null
    private var licenseClaimReference: String? = null

    private fun applyAction(action: ActivationAction) {
        when (val result = transition(currentState.activationState, action)) {
            is ActivationTransitionResult.Applied -> setState { it.copy(activationState = result.newState) }
            is ActivationTransitionResult.Rejected -> Unit // real regression guard: silently-ignored invalid transition, never crashes the UI
        }
    }

    fun onStart() = applyAction(ActivationAction.START)

    fun onEmailChange(value: String) = setState { it.copy(emailInput = value) }

    /** Real duplicate-submit guard: [submitting] gates a second concurrent tap before it ever reaches the orchestrator. */
    fun onSignIn(password: String) {
        if (currentState.submitting) return
        applyAction(ActivationAction.SIGN_IN)
        setState { it.copy(submitting = true, presentationError = null) }
        launchOnDefault {
            val outcome = authOrchestrator.signIn(currentState.emailInput, password)
            val authState = authOrchestrator.toAuthenticationState(outcome)
            when (authState) {
                is CustomerAuthenticationState.Authenticated -> {
                    customerSessionId = authState.session.sessionId
                    applyAction(ActivationAction.VERIFICATION_CONFIRMED)
                }
                is CustomerAuthenticationState.VerificationRequired -> applyAction(ActivationAction.RESPONSE_RECEIVED)
                else -> {
                    applyAction(ActivationAction.TRANSPORT_FAILURE)
                    setState { it.copy(presentationError = outcome.toPresentationError()) }
                }
            }
            setState { it.copy(submitting = false) }
            sendEffect(ActivationEffect.ClearSecureInput)
        }
    }

    fun onVerificationConfirmed() = applyAction(ActivationAction.VERIFICATION_CONFIRMED)

    fun onLicenseSerialChange(value: String) = setState { it.copy(licenseSerialInput = value) }

    fun onSubmitLicense() {
        val sessionId = customerSessionId ?: return
        if (currentState.submitting) return
        applyAction(ActivationAction.SUBMIT_LICENSE)
        setState { it.copy(submitting = true, presentationError = null) }
        launchOnDefault {
            when (val outcome = claimOrchestrator.claim(sessionId, currentState.licenseSerialInput, productCode)) {
                is LicenseClaimOutcome.Claimed -> {
                    licenseClaimReference = outcome.result.licensePublicId
                    applyAction(ActivationAction.RESPONSE_RECEIVED)
                    setState { it.copy(devicePolicy = outcome.result.devicePolicy, licenseSerialInput = "") }
                    applyAction(ActivationAction.DEVICE_POLICY_LOADED)
                }
                is LicenseClaimOutcome.LocallyImplausible -> {
                    applyAction(ActivationAction.TRANSPORT_FAILURE)
                    setState { it.copy(presentationError = PresentationError.Contract.UnknownRequiredField) }
                }
                is LicenseClaimOutcome.Rejected -> {
                    applyAction(ActivationAction.TRANSPORT_FAILURE)
                    setState { it.copy(presentationError = outcome.error) }
                }
            }
            setState { it.copy(submitting = false) }
            sendEffect(ActivationEffect.ClearSecureInput)
        }
    }

    fun onRetryLicenseClaim() = applyAction(ActivationAction.RETRY_LICENSE_CLAIM)

    fun onDeviceLabelChange(value: String) = setState { it.copy(deviceLabelInput = value) }

    fun onConfirmDevice() = applyAction(ActivationAction.CONFIRM_DEVICE)

    fun onActivate() {
        val sessionId = customerSessionId ?: return
        val claimReference = licenseClaimReference ?: return
        val installationIdentity = installationIdentityProvider() ?: run {
            setState { it.copy(presentationError = PresentationError.Transport.NotConfigured) }
            return
        }
        if (currentState.submitting) return
        applyAction(ActivationAction.ACTIVATE)
        setState { it.copy(submitting = true, presentationError = null) }
        launchOnDefault {
            if (!idempotency.tryBeginAttempt()) {
                setState { it.copy(submitting = false) }
                return@launchOnDefault
            }
            try {
                val command = ActivationCommand(
                    customerSessionId = sessionId,
                    licenseClaimReference = claimReference,
                    productCode = productCode,
                    platform = platform,
                    installationIdentity = installationIdentity,
                    deviceMetadata = DeviceMetadata(
                        deviceLabel = currentState.deviceLabelInput.ifBlank { null },
                        platform = platform,
                        appVersion = "1.0.0",
                    ),
                    idempotencyKey = idempotency.keyFor(commandFingerprint(sessionId, claimReference)),
                )
                val outcome = transport.activateInstallation(command)
                when (outcome) {
                    is TransportOutcome.Success -> {
                        applyAction(ActivationAction.RESPONSE_RECEIVED)
                        when (val processed = responseProcessor.process(outcome.value, productCode, platform)) {
                            is ActivationProcessingResult.Complete -> {
                                applyAction(ActivationAction.SECURE_PERSISTENCE_COMMITTED)
                                sendEffect(ActivationEffect.NavigateToActivationComplete)
                            }
                            is ActivationProcessingResult.SecurePersistenceRequired -> {
                                applyAction(ActivationAction.TRANSPORT_FAILURE)
                                setState { it.copy(presentationError = PresentationError.Transport.NotConfigured) }
                            }
                            is ActivationProcessingResult.ContractViolation -> {
                                applyAction(ActivationAction.TRANSPORT_FAILURE)
                                setState { it.copy(presentationError = PresentationError.Contract.MalformedResponse(processed.reason)) }
                            }
                            is ActivationProcessingResult.Rejected -> {
                                applyAction(ActivationAction.TRANSPORT_FAILURE)
                                setState { it.copy(presentationError = processed.error) }
                            }
                            is ActivationProcessingResult.Pending -> Unit // real, honest: manual-approval activation stays in ACTIVATION_RESPONSE_RECEIVED, awaiting staff approval
                        }
                    }
                    else -> {
                        applyAction(ActivationAction.TRANSPORT_FAILURE)
                        setState { it.copy(presentationError = outcome.toPresentationError(), retryAvailable = true) }
                    }
                }
            } finally {
                idempotency.completeAttempt()
                setState { it.copy(submitting = false) }
            }
        }
    }

    fun onRetryActivation() = applyAction(ActivationAction.RETRY_ACTIVATION)

    fun onCancel() {
        applyAction(ActivationAction.CANCEL)
        sendEffect(ActivationEffect.ClearSecureInput)
    }

    fun onOpenSupport() = sendEffect(ActivationEffect.NavigateToSupport)

    fun onSignOut(sessionId: ExternalCustomerSessionId) = launchOnDefault {
        authOrchestrator.signOut(sessionId)
        customerSessionId = null
        licenseClaimReference = null
    }

    fun onRestartFlow() {
        applyAction(ActivationAction.RESTART)
        customerSessionId = null
        licenseClaimReference = null
        setState { ActivationUiState() }
    }

    private fun commandFingerprint(sessionId: ExternalCustomerSessionId, claimReference: String): Int =
        (sessionId.value.hashCode() * 31 + claimReference.hashCode() * 31 + currentState.deviceLabelInput.hashCode())
}

/** Pure-Kotlin, KMP-safe random key -- no platform UUID API required (avoids introducing this codebase's first expect/actual pair for something this simple). */
internal fun randomIdempotencyKey(): String {
    val bytes = ByteArray(16)
    for (i in bytes.indices) bytes[i] = kotlin.random.Random.nextInt(0, 256).toByte()
    return bytes.joinToString("") { (it.toInt() and 0xFF).toString(16).padStart(2, '0') }
}
