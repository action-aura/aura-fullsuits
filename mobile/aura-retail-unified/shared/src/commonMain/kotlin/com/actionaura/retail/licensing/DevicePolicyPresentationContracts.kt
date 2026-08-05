package com.actionaura.retail.licensing

/**
 * M8.12 -- shared presentation state for future License/device-
 * management screens (`device-policy-presentation-contract.md`). No
 * real HTTP wiring here; production consumers must expose
 * TransportNotImplemented today, never a fabricated Loaded state.
 */
sealed interface DevicePolicyPresentationState {
    data object Loading : DevicePolicyPresentationState

    data class Loaded(
        val policy: ResolvedDevicePolicy,
        val installations: List<InstallationDescriptor>,
        val iosReadiness: IosPlatformReadinessState,
    ) : DevicePolicyPresentationState

    data object ServerUnavailable : DevicePolicyPresentationState

    data class PolicyMalformed(val problems: List<String>) : DevicePolicyPresentationState

    data object CustomerAuthPrerequisiteMissing : DevicePolicyPresentationState

    /** The real, current, honest state for every production consumer of this contract today -- M8 implements no HTTP execution. */
    data object TransportNotImplemented : DevicePolicyPresentationState
}
