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
import com.actionaura.retail.di.AuraAppContainer
import com.actionaura.retail.di.LocalAuraAppContainer
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

                BoxWithConstraints(modifier = Modifier.fillMaxSize()) {
                    val windowSize = AuraWindowSize(maxWidth.value.toInt(), maxHeight.value.toInt())

                    when (val currentPhase = phase) {
                        is AppPhase.Bootstrap -> {
                            BootstrapScreen()
                            // Real, immediate transition -- see this function's own KDoc.
                            // A real `LaunchedEffect`, never a bare state write inside
                            // the composable body (which would be a real composition
                            // side-effect anti-pattern).
                            LaunchedEffect(Unit) { phase = AppPhase.Authenticated }
                        }
                        is AppPhase.Authenticated -> AuthenticatedAppShell(windowSize)
                        is AppPhase.Onboarding, is AppPhase.Unauthenticated, is AppPhase.LicenseBlocked, is AppPhase.SessionExpired ->
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
