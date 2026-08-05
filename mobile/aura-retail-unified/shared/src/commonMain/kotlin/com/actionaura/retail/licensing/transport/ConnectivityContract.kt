package com.actionaura.retail.licensing.transport

import kotlinx.coroutines.flow.Flow

/**
 * M9.16 -- platform-neutral connectivity signal
 * (`mobile-connectivity-contract.md`). Helps UX only -- never proof
 * the server is reachable (a real distinct concept from
 * [TransportOutcome.NetworkFailure]/[TransportOutcome.ServerUnavailable]-
 * shaped results, which come from an actual attempted call).
 */
sealed interface ConnectivitySignal {
    data object ApparentlyOnline : ConnectivitySignal
    data object NoApparentNetwork : ConnectivitySignal
}

/** commonMain contract; Android/iOS supply the real adapter (M9 provides compile-safe skeletons only, per its own scope). */
interface ConnectivityObserver {
    val signal: Flow<ConnectivitySignal>
}

/** Real, honest default when no platform adapter is wired -- never claims to know real connectivity. */
class UnknownConnectivityObserver : ConnectivityObserver {
    override val signal: Flow<ConnectivitySignal> = kotlinx.coroutines.flow.flowOf(ConnectivitySignal.ApparentlyOnline)
}
