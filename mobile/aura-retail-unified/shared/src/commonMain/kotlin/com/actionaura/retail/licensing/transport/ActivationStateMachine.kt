package com.actionaura.retail.licensing.transport

/**
 * M9.9 -- one canonical shared activation state machine
 * (`mobile-activation-state-machine.md`). No single generic loading
 * boolean -- every real, distinct step of the flow is its own state.
 * Invalid transitions fail deterministically via [transition].
 */
enum class ActivationState {
    NOT_STARTED,
    CUSTOMER_SESSION_REQUIRED,
    CUSTOMER_AUTHENTICATING,
    CUSTOMER_VERIFICATION_REQUIRED,
    LICENSE_INPUT_REQUIRED,
    LICENSE_CLAIMING,
    LICENSE_REJECTED,
    DEVICE_POLICY_LOADING,
    DEVICE_POLICY_REJECTED,
    INSTALLATION_IDENTITY_REQUIRED,
    READY_TO_ACTIVATE,
    ACTIVATION_REQUESTING,
    ACTIVATION_RETRY_AVAILABLE,
    ACTIVATION_REJECTED,
    DEVICE_LIMIT_REACHED,
    PLATFORM_NOT_ALLOWED,
    IOS_SERVER_NOT_READY,
    INSTALLATION_REVOKED,
    ACTIVATION_RESPONSE_RECEIVED,
    SECURE_PERSISTENCE_REQUIRED,
    ACTIVATION_COMPLETE,
    NETWORK_UNAVAILABLE,
    SERVER_UNAVAILABLE,
    TRANSPORT_NOT_CONFIGURED,
    CANCELLED,
    FATAL_CONTRACT_ERROR,
}

enum class ActivationAction {
    START, SIGN_IN, VERIFICATION_CONFIRMED, SUBMIT_LICENSE, RETRY_LICENSE_CLAIM,
    DEVICE_POLICY_LOADED, CONFIRM_DEVICE, ACTIVATE, RETRY_ACTIVATION,
    RESPONSE_RECEIVED, SECURE_PERSISTENCE_COMMITTED, CANCEL, TRANSPORT_FAILURE, RESTART,
}

/** Terminal states never accept a further transition except RESTART back to NOT_STARTED. */
val TERMINAL_STATES: Set<ActivationState> = setOf(
    ActivationState.ACTIVATION_COMPLETE, ActivationState.CANCELLED, ActivationState.FATAL_CONTRACT_ERROR,
)

/** Real, closed transition table -- allowed (state, action) pairs and their real destination. */
private val TRANSITIONS: Map<Pair<ActivationState, ActivationAction>, ActivationState> = buildMap {
    put(ActivationState.NOT_STARTED to ActivationAction.START, ActivationState.CUSTOMER_SESSION_REQUIRED)
    put(ActivationState.CUSTOMER_SESSION_REQUIRED to ActivationAction.SIGN_IN, ActivationState.CUSTOMER_AUTHENTICATING)
    put(ActivationState.CUSTOMER_AUTHENTICATING to ActivationAction.VERIFICATION_CONFIRMED, ActivationState.LICENSE_INPUT_REQUIRED)
    put(ActivationState.CUSTOMER_AUTHENTICATING to ActivationAction.RESPONSE_RECEIVED, ActivationState.CUSTOMER_VERIFICATION_REQUIRED)
    put(ActivationState.CUSTOMER_AUTHENTICATING to ActivationAction.TRANSPORT_FAILURE, ActivationState.NETWORK_UNAVAILABLE)
    put(ActivationState.CUSTOMER_VERIFICATION_REQUIRED to ActivationAction.VERIFICATION_CONFIRMED, ActivationState.LICENSE_INPUT_REQUIRED)
    put(ActivationState.LICENSE_INPUT_REQUIRED to ActivationAction.SUBMIT_LICENSE, ActivationState.LICENSE_CLAIMING)
    put(ActivationState.LICENSE_CLAIMING to ActivationAction.RESPONSE_RECEIVED, ActivationState.DEVICE_POLICY_LOADING)
    put(ActivationState.LICENSE_CLAIMING to ActivationAction.TRANSPORT_FAILURE, ActivationState.LICENSE_REJECTED)
    put(ActivationState.LICENSE_REJECTED to ActivationAction.RETRY_LICENSE_CLAIM, ActivationState.LICENSE_INPUT_REQUIRED)
    put(ActivationState.DEVICE_POLICY_LOADING to ActivationAction.DEVICE_POLICY_LOADED, ActivationState.INSTALLATION_IDENTITY_REQUIRED)
    put(ActivationState.DEVICE_POLICY_LOADING to ActivationAction.TRANSPORT_FAILURE, ActivationState.DEVICE_POLICY_REJECTED)
    put(ActivationState.DEVICE_POLICY_REJECTED to ActivationAction.RETRY_LICENSE_CLAIM, ActivationState.LICENSE_INPUT_REQUIRED)
    put(ActivationState.INSTALLATION_IDENTITY_REQUIRED to ActivationAction.CONFIRM_DEVICE, ActivationState.READY_TO_ACTIVATE)
    put(ActivationState.READY_TO_ACTIVATE to ActivationAction.ACTIVATE, ActivationState.ACTIVATION_REQUESTING)
    put(ActivationState.ACTIVATION_REQUESTING to ActivationAction.RESPONSE_RECEIVED, ActivationState.ACTIVATION_RESPONSE_RECEIVED)
    put(ActivationState.ACTIVATION_REQUESTING to ActivationAction.TRANSPORT_FAILURE, ActivationState.ACTIVATION_RETRY_AVAILABLE)
    put(ActivationState.ACTIVATION_RETRY_AVAILABLE to ActivationAction.RETRY_ACTIVATION, ActivationState.ACTIVATION_REQUESTING)
    put(ActivationState.ACTIVATION_RESPONSE_RECEIVED to ActivationAction.SECURE_PERSISTENCE_COMMITTED, ActivationState.ACTIVATION_COMPLETE)
    put(ActivationState.ACTIVATION_RESPONSE_RECEIVED to ActivationAction.TRANSPORT_FAILURE, ActivationState.SECURE_PERSISTENCE_REQUIRED)

    // Every non-terminal state may be cancelled.
    for (state in ActivationState.entries) {
        if (state !in TERMINAL_STATES && state != ActivationState.NOT_STARTED) {
            put(state to ActivationAction.CANCEL, ActivationState.CANCELLED)
        }
    }
    // Every terminal-adjacent rejection/blocked state may restart.
    for (state in listOf(
        ActivationState.LICENSE_REJECTED, ActivationState.DEVICE_POLICY_REJECTED, ActivationState.ACTIVATION_REJECTED,
        ActivationState.DEVICE_LIMIT_REACHED, ActivationState.PLATFORM_NOT_ALLOWED, ActivationState.IOS_SERVER_NOT_READY,
        ActivationState.INSTALLATION_REVOKED, ActivationState.NETWORK_UNAVAILABLE, ActivationState.SERVER_UNAVAILABLE,
        ActivationState.TRANSPORT_NOT_CONFIGURED, ActivationState.CANCELLED, ActivationState.SECURE_PERSISTENCE_REQUIRED,
    )) {
        put(state to ActivationAction.RESTART, ActivationState.NOT_STARTED)
    }
}

sealed interface ActivationTransitionResult {
    data class Applied(val newState: ActivationState) : ActivationTransitionResult
    data class Rejected(val fromState: ActivationState, val action: ActivationAction) : ActivationTransitionResult
}

/**
 * Pure, deterministic transition function -- no side effects, no
 * coroutine, no I/O. [ActivationOrchestrator] (M9's own future glue,
 * built on this) is what actually invokes transports/sinks.
 */
fun transition(from: ActivationState, action: ActivationAction): ActivationTransitionResult {
    val target = TRANSITIONS[from to action]
    return if (target != null) ActivationTransitionResult.Applied(target) else ActivationTransitionResult.Rejected(from, action)
}

fun ActivationState.isTerminal(): Boolean = this in TERMINAL_STATES
