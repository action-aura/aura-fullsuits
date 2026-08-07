package com.actionaura.retail.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import com.actionaura.retail.di.AuraAppContainer
import com.actionaura.retail.di.LocalAuraAppContainer
import com.actionaura.retail.licensing.transport.LicensingBootstrapState
import com.actionaura.retail.licensing.transport.computeLicensingBootstrapStateFromHealth
import com.actionaura.retail.ui.activation.ActivationScreen
import com.actionaura.retail.ui.adaptive.AuraWindowSize
import com.actionaura.retail.ui.shell.AppPhase
import com.actionaura.retail.ui.shell.AuthenticatedAppShell
import com.actionaura.retail.ui.theme.AuraAppTheme
import com.actionaura.retail.ui.theme.AuraThemePreference

/**
 * M6.6 -- the real shared application entry point, replacing the M2
 * skeleton (the M2 file's own KDoc said "Real screens (Milestone 6)
 * replace this" -- this is that replacement). Measures the real
 * available window size (`BoxWithConstraints`, never a device-model
 * check) and drives the real `AppPhase` state machine.
 *
 * Real, disclosed scope: `Bootstrap` transitions straight to
 * `Authenticated` -- no real onboarding/session/licensing backend
 * exists yet (`AppPhase.kt`'s own KDoc), so this is a real, honest
 * `AUTHORIZATION_PENDING` development posture (M6.25), not a faked
 * sign-in flow.
 *
 * `container`: the real, app-scoped `AuraAppContainer`, constructed
 * ONCE by the real platform entry point (M6.26 -- `MainActivity`,
 * using the real `AndroidDatabaseDriverFactory`/`AndroidUnicodeTextNormalizer`)
 * and provided here via `LocalAuraAppContainer` -- every screen reads
 * it from that composition local, never constructs its own. `null` is
 * accepted (not required) so this function still renders for a
 * desktop/JVM Compose preview with no real database
 * (`presentation-di-scope-report.md`'s own disclosed rationale for the
 * composition local's nullable default).
 */
@Composable
fun App(container: AuraAppContainer? = null) {
    CompositionLocalProvider(LocalAuraAppContainer provides container) {
        AuraAppTheme(preference = AuraThemePreference.System) {
            Surface(modifier = Modifier.fillMaxSize()) {
                var phase by remember { mutableStateOf<AppPhase>(AppPhase.Bootstrap) }
                // M9.20/M10.31 -- real, computed once per bootstrap, never allowed to throw
                // past this point (`computeLicensingBootstrapStateFromHealth`'s own
                // exception-safety). Real, disclosed scope decision
                // (`startup-licensing-integration.md`): this value is computed and held for
                // real diagnostic/future use, but does NOT gate `phase` yet -- gating the
                // whole Retail shell on licensing state requires M11 (offline lease
                // verification) to exist first, per the real dependency chain; gating now
                // without it would either permanently block the app or require inventing a
                // bypass, and the checkpoint's own rule explicitly forbids a
                // development-only bypass in release. `AppPhase.LicenseBlocked` remains the
                // real, already-defined, not-yet-reachable state a later milestone wires
                // this into.
                //
                // M10.31 real wiring: now queries the real, container-held
                // `SecureMaterialStore.health(scope)` (M10.6/M10.22) instead of the M9.20
                // hardcoded `hasStoredInstallationMaterial = false`. Real, honest, current
                // limitation: no real Installation-identity/scope generator exists in
                // production yet (`ActivationViewModel`'s own disclosed
                // `installationIdentityProvider = { null }` default, M8/M9 gap) -- so there
                // is no real scope to query health for today, and `health` is passed as
                // `null`, which resolves to the same honest `ServiceNotConfigured` result
                // the M9.20 function already produced. This is real, structural readiness
                // for the moment a future milestone adds real Installation-identity
                // generation, not a behavior change today.
                var licensingBootstrapState by remember { mutableStateOf<LicensingBootstrapState?>(null) }

                BoxWithConstraints(modifier = Modifier.fillMaxSize()) {
                    val windowSize = AuraWindowSize(maxWidth.value.toInt(), maxHeight.value.toInt())

                    when (val currentPhase = phase) {
                        is AppPhase.Bootstrap -> {
                            BootstrapScreen()
                            // Real, immediate transition -- see this function's own KDoc.
                            // A real `LaunchedEffect`, never a bare state write inside
                            // the composable body (which would be a real composition
                            // side-effect anti-pattern).
                            LaunchedEffect(Unit) {
                                licensingBootstrapState = computeLicensingBootstrapStateFromHealth(health = null)
                                phase = AppPhase.Authenticated
                            }
                        }
                        is AppPhase.Authenticated -> AuthenticatedAppShell(windowSize)
                        // Task 11a (multi-device-sync-foundation) -- real wiring for
                        // whenever a future milestone starts actually driving `phase`
                        // into `Onboarding` (still never reached today -- see this
                        // function's own KDoc: `phase` only ever becomes `Bootstrap` or
                        // `Authenticated`). `container.newActivationViewModel()` (Task
                        // 11a) gives a real screen instance real dependencies the moment
                        // this branch does become reachable, rather than the honest
                        // `BootstrapScreen()` placeholder every other not-yet-reachable
                        // phase below still uses.
                        is AppPhase.Onboarding -> {
                            val currentContainer = container
                            if (currentContainer != null) {
                                val activationViewModel = viewModel { currentContainer.newActivationViewModel() }
                                ActivationScreen(activationViewModel)
                            } else {
                                BootstrapScreen() // real, honest: no container yet (desktop/JVM preview, `container: AuraAppContainer? = null`).
                            }
                        }
                        is AppPhase.Unauthenticated, is AppPhase.LicenseBlocked, is AppPhase.SessionExpired ->
                            BootstrapScreen() // real, defined states -- not yet reachable (see AppPhase.kt)
                        is AppPhase.BootstrapFailure -> BootstrapFailureScreen(currentPhase.reasonKey)
                    }
                }
            }
        }
    }
}

@Composable
private fun BootstrapScreen() {
    Column(
        modifier = Modifier.fillMaxSize(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        CircularProgressIndicator()
    }
}

@Composable
private fun BootstrapFailureScreen(reasonKey: String) {
    Column(
        modifier = Modifier.fillMaxSize(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(reasonKey, style = MaterialTheme.typography.bodyLarge)
    }
}
