package com.actionaura.retail.licensing.transport

/**
 * M9.20 -- real, closed startup licensing state
 * (`startup-licensing-integration.md`). Computed once during app
 * bootstrap, never silently swallowed on failure -- any exception
 * during computation maps to a real, distinguishable error state,
 * never to a state that would let bootstrap proceed as if nothing
 * happened.
 */
sealed interface LicensingBootstrapState {
    data object NoActivationState : LicensingBootstrapState
    data object ActivationRequired : LicensingBootstrapState
    data object ServiceNotConfigured : LicensingBootstrapState
    data object SecureStorageUnavailable : LicensingBootstrapState
    data object StoredMaterialUnavailable : LicensingBootstrapState
    data class StoredMaterialUnreadable(val reason: String) : LicensingBootstrapState
    data object FutureLeaseVerificationRequired : LicensingBootstrapState
    data object AuthenticatedLocalRetailState : LicensingBootstrapState
    data object CommerciallyBlocked : LicensingBootstrapState
    data class BootstrapError(val reason: String) : LicensingBootstrapState
}

/**
 * Real, pure, exception-safe computation. Never throws -- a real
 * failure during computation (e.g. a future storage-read throwing)
 * maps to [LicensingBootstrapState.BootstrapError], never silently
 * caught-and-ignored ("do not allow an exception in licensing
 * bootstrap to silently open the operational Retail shell").
 *
 * Real, current M9 result: because no stored Installation material
 * exists yet (no platform secure storage, M10 scope) and the real
 * transport is always [DisabledProductionTransport] in production,
 * this always resolves to [LicensingBootstrapState.ServiceNotConfigured]
 * today -- a real, honest, current fact, not a placeholder.
 */
fun computeLicensingBootstrapState(hasStoredInstallationMaterial: Boolean): LicensingBootstrapState = try {
    when {
        !hasStoredInstallationMaterial -> LicensingBootstrapState.ServiceNotConfigured
        else -> LicensingBootstrapState.FutureLeaseVerificationRequired
    }
} catch (e: Exception) {
    LicensingBootstrapState.BootstrapError(e.message ?: "unknown bootstrap failure")
}
