package com.actionaura.retail.ui.activation

import com.actionaura.retail.licensing.ActivationResult
import com.actionaura.retail.licensing.DeviceMetadata
import com.actionaura.retail.licensing.InstallationIdentity
import com.actionaura.retail.licensing.LicensingPlatform
import com.actionaura.retail.licensing.LicensingProductCode
import com.actionaura.retail.licensing.ResolvedDevicePolicy
import com.actionaura.retail.licensing.transport.ActivationIdempotencyCoordinator
import com.actionaura.retail.licensing.transport.ActivationResponseProcessor
import com.actionaura.retail.licensing.transport.ActivationState
import com.actionaura.retail.licensing.transport.ActivationAction
import com.actionaura.retail.licensing.transport.CustomerAuthenticationOrchestrator
import com.actionaura.retail.licensing.transport.CustomerAuthenticationState
import com.actionaura.retail.licensing.transport.DirectLicenseKeyActivationCommand
import com.actionaura.retail.licensing.transport.ExternalCustomerSessionId
import com.actionaura.retail.licensing.transport.ExternalLicensingTransport
import com.actionaura.retail.licensing.transport.ActivationProcessingResult
import com.actionaura.retail.licensing.transport.ActivationTransitionResult
import com.actionaura.retail.licensing.transport.LicenseClaimOrchestrator
import com.actionaura.retail.licensing.transport.LicenseClaimOutcome
import com.actionaura.retail.licensing.transport.NoSecureStorageAvailableSink
import com.actionaura.retail.licensing.transport.PresentationError
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
    /**
     * Still only ever real for `signIn`/`claimLicense` (M9's own
     * customer-session-gated flow -- Task 8's own KDoc: "shared client-
     * side contract shapes only, no real transport execution" for that
     * surface today). The one real, wire-verified activation call
     * ([HttpExternalLicensingTransport.activateWithLicenseKey]) is
     * deliberately NOT a member of [ExternalLicensingTransport] (see
     * that class's own KDoc for why `activateInstallation(ActivationCommand)`
     * stays an honest stub) -- [activateWithLicenseKey] below is the
     * separate, correctly-typed seam for it.
     */
    private val transport: ExternalLicensingTransport,
    private val productCode: LicensingProductCode,
    private val platform: LicensingPlatform,
    /**
     * Real Installation identity generation is ANDROID_ADAPTER/
     * IOS_ADAPTER work (M8 gap #8/#9), not implemented in M9. The
     * default `{ null }` is the real, honest current state -- no
     * fabricated placeholder identity is ever synthesized by this
     * class or by the Compose layer (`activation-compose-flow.md`).
     * Task 11a (multi-device-sync-foundation): real production wiring
     * (`AuraAppContainer.newActivationViewModel()`) now supplies a real
     * generator -- a fresh, locally-generated random seed
     * (`secureRandomHex`), no persistence dependency, matching exactly
     * how Tasks 8/9/10's own live tests already construct a real
     * `InstallationIdentity` (no chicken-and-egg problem: the identity
     * is generated BEFORE activation and only becomes persisted, inside
     * the committed activation bundle, as a RESULT of a successful
     * activation, never a precondition of one).
     */
    private val installationIdentityProvider: () -> InstallationIdentity? = { null },
    /**
     * Task 11a -- the one real, wire-verified activation call
     * (`HttpExternalLicensingTransport.activateWithLicenseKey`), kept
     * as a standalone suspend lambda rather than a second field on
     * [transport] or a new [ExternalLicensingTransport] interface
     * method -- adding it to the interface would force every other
     * implementer (`DisabledProductionTransport`, test fixtures) to
     * grow a matching override for a capability only one real class
     * has, for no real benefit. Honest `TransportNotConfigured` default,
     * matching every other not-yet-wired capability in this class.
     */
    private val activateWithLicenseKey: suspend (DirectLicenseKeyActivationCommand) -> TransportOutcome<ActivationResult> = { TransportOutcome.TransportNotConfigured },
    /**
     * Task 11a -- replaces the old eagerly-constructed
     * `ActivationResponseProcessor(credentialSink, leaseSink)` field.
     * The real production sink
     * ([com.actionaura.retail.securestorage.SecureMaterialStoreActivationSink])
     * cannot be constructed until the real `ownerInstallationId` is
     * known -- and that value only exists once Owner's real activation
     * response arrives (`ActivationResult.Approved.installationPublicId`,
     * the exact same value Task 10's own `SyncOrchestratorLiveTest`
     * already proved is the correct source). So this is a FACTORY,
     * invoked fresh on every real activation attempt with the response's
     * own `installationPublicId`, never a pre-built instance. Honest
     * no-op default -- matches [NoSecureStorageAvailableSink]'s own
     * "no platform secure storage exists yet" default this replaces.
     */
    private val activationResponseProcessorFactory: (ownerInstallationId: String, installationIdentity: InstallationIdentity) -> ActivationResponseProcessor =
        { _, _ -> ActivationResponseProcessor(NoSecureStorageAvailableSink(), NoSecureStorageAvailableSink()) },
    dispatcher: kotlinx.coroutines.CoroutineDispatcher = Dispatchers.Default,
) : AuraViewModel<ActivationUiState, ActivationEffect>(ActivationUiState(), dispatcher) {

    private val authOrchestrator = CustomerAuthenticationOrchestrator(transport)
    private val claimOrchestrator = LicenseClaimOrchestrator(transport)
    private val idempotency = ActivationIdempotencyCoordinator(keyGenerator = { randomIdempotencyKey() })

    private var customerSessionId: ExternalCustomerSessionId? = null
    private var licenseClaimReference: String? = null

    /**
     * Task 11a -- the real, raw license key the user actually typed,
     * retained here (never placed in [ActivationUiState]) because it,
     * not [licenseClaimReference] (a server-assigned public ID from the
     * still-unreal `claimLicense()` call), is what a real
     * [DirectLicenseKeyActivationCommand] needs -- see
     * `HttpExternalLicensingTransport`'s own KDoc for why the two must
     * never be conflated. Set only on a real [LicenseClaimOutcome.Claimed]
     * (mirroring [licenseClaimReference]'s own existing lifecycle), so it
     * is never available to [onActivate] before [onSubmitLicense] has
     * genuinely run once.
     */
    private var submittedLicenseKey: String? = null

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
        val rawLicenseKey = currentState.licenseSerialInput
        applyAction(ActivationAction.SUBMIT_LICENSE)
        setState { it.copy(submitting = true, presentationError = null) }
        launchOnDefault {
            when (val outcome = claimOrchestrator.claim(sessionId, rawLicenseKey, productCode)) {
                is LicenseClaimOutcome.Claimed -> {
                    licenseClaimReference = outcome.result.licensePublicId
                    // Task 11a -- retain the raw key itself; see this class's own
                    // `submittedLicenseKey` KDoc for why.
                    submittedLicenseKey = rawLicenseKey
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
        // Task 11a -- the real wire value; see `submittedLicenseKey`'s own KDoc.
        val rawLicenseKey = submittedLicenseKey ?: run {
            setState { it.copy(presentationError = PresentationError.Transport.NotConfigured) }
            return
        }
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
                val command = DirectLicenseKeyActivationCommand(
                    licenseKey = rawLicenseKey,
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
                // Task 11a -- the real, wire-verified activation call
                // (`HttpExternalLicensingTransport.activateWithLicenseKey`),
                // NOT `transport.activateInstallation()` -- see this class's own
                // KDoc on [activateWithLicenseKey] for why that stays a
                // deliberate, separate stub.
                val outcome = activateWithLicenseKey(command)
                when (outcome) {
                    is TransportOutcome.Success -> {
                        applyAction(ActivationAction.RESPONSE_RECEIVED)
                        // Task 11a -- the real production sink cannot be built until
                        // Owner's own `installationPublicId` is known; see
                        // `activationResponseProcessorFactory`'s own KDoc.
                        val ownerInstallationId = ownerInstallationIdOf(outcome.value)
                        val processor = if (ownerInstallationId != null) {
                            activationResponseProcessorFactory(ownerInstallationId, installationIdentity)
                        } else {
                            ActivationResponseProcessor(NoSecureStorageAvailableSink(), NoSecureStorageAvailableSink())
                        }
                        when (val processed = processor.process(outcome.value, productCode, platform)) {
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

    /** Task 11a -- the real `installationPublicId`, present on every non-Rejected [ActivationResult] Owner's real activation.py can return. */
    private fun ownerInstallationIdOf(result: ActivationResult): String? = when (result) {
        is ActivationResult.Approved -> result.installationPublicId
        is ActivationResult.AlreadyActive -> result.installationPublicId
        is ActivationResult.Pending -> result.installationPublicId
        is ActivationResult.Rejected -> null
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
        submittedLicenseKey = null
    }

    fun onRestartFlow() {
        applyAction(ActivationAction.RESTART)
        customerSessionId = null
        licenseClaimReference = null
        submittedLicenseKey = null
        setState { ActivationUiState() }
    }

    private fun commandFingerprint(sessionId: ExternalCustomerSessionId, claimReference: String): Int =
        (sessionId.value.hashCode() * 31 + claimReference.hashCode() * 31 + currentState.deviceLabelInput.hashCode())
}

/**
 * Real platform CSPRNG-backed key (`secureRandomBytes`, Android
 * `java.security.SecureRandom` / iOS `SecRandomCopyBytes`) --
 * previously `kotlin.random.Random`-based, changed after a security
 * review correctly flagged that as a weak cryptographic primitive.
 */
internal fun randomIdempotencyKey(): String = com.actionaura.retail.licensing.transport.secureRandomHex(16)
