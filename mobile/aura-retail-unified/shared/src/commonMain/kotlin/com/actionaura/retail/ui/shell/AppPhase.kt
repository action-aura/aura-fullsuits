package com.actionaura.retail.ui.shell

/**
 * M6.6 -- the real, shared application-shell phase machine, matching
 * the audited legacy app's own real phase shape (`AppRoot.kt`'s
 * `LOADING/SETUP/LOGIN/READY`) extended to the checkpoint's own fuller
 * required list. Real, disclosed current scope: no real
 * onboarding/session/licensing backend exists yet
 * (`UserRepository`/`SessionRepository`/`LicensingRepository` remain
 * M7-M10 interface markers) -- so this app currently transitions
 * straight from `Bootstrap` to `Authenticated` (a real, honest
 * `AUTHORIZATION_PENDING` development posture, M6.25), never a faked
 * sign-in. The other phases are real, defined states a later
 * milestone's real backend will actually drive into.
 */
sealed interface AppPhase {
    data object Bootstrap : AppPhase
    data object Onboarding : AppPhase
    data object Unauthenticated : AppPhase
    data object LicenseBlocked : AppPhase
    data object Authenticated : AppPhase
    data class BootstrapFailure(val reasonKey: String) : AppPhase
    data object SessionExpired : AppPhase
}
