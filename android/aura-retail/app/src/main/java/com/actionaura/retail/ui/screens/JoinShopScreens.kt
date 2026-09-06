package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.ApiClient
import com.actionaura.retail.ui.i18n.tr
import kotlinx.coroutines.delay

/**
 * Shown right after activation when this device might be JOINING a shop that
 * already exists on another device, rather than being its first. AppRoot can
 * only get this far when needs_setup is true and the licensing status carries
 * an Owner-issued installation_id in a non-pending state (FirstRunDecision) --
 * both a fresh install and a still-pending activation skip straight to
 * SetupScreen without ever asking. See docs/launch-readiness/
 * join-existing-shop-design.md and the desktop's `_openFirstRun()` /
 * `_isJoinedDevice()` twins in products/retail/frontend/app-shell.js, which
 * this mirrors for Android.
 */
@Composable
fun JoinChoiceScreen(onJoin: () -> Unit, onSetup: () -> Unit) {
    Surface(color = Color.Transparent) {
        Column(
            Modifier.fillMaxSize().padding(28.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(tr("Licence activated"), style = MaterialTheme.typography.headlineMedium,
                color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.ExtraBold)
            Spacer(Modifier.height(10.dp))
            Text(tr("Is your shop already set up on another device?"),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(24.dp))

            ElevatedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(20.dp)) {
                    Button(onClick = onJoin, modifier = Modifier.fillMaxWidth().height(52.dp)) {
                        Text(tr("Yes — connect to my shop"), style = MaterialTheme.typography.labelLarge)
                    }
                    Spacer(Modifier.height(12.dp))
                    OutlinedButton(onClick = onSetup, modifier = Modifier.fillMaxWidth().height(52.dp)) {
                        Text(tr("No — this is the shop's first device"), style = MaterialTheme.typography.labelLarge)
                    }
                    Spacer(Modifier.height(16.dp))
                    Text(
                        tr("Connecting brings your existing owner and staff accounts to this phone. No new account is created."),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

/**
 * Polls this device's own /api/onboarding/status until the owner's account --
 * synced down from the shop's other device -- makes needs_setup false. Never
 * calls createAdmin: that would manufacture a second, placeholder owner on a
 * device that is supposed to inherit the real one, exactly the bug this whole
 * feature exists to avoid (see the module doc comment above and the desktop's
 * `_joinSubmit()`, which carries the identical rule).
 */
@Composable
fun JoiningScreen(
    onConnected: () -> Unit,
    onSetupInstead: () -> Unit,
    pollMs: Long = 2000L,
    ceilingMs: Long = 120000L,
) {
    var attempt by remember { mutableIntStateOf(0) }
    var timedOut by remember { mutableStateOf(false) }

    LaunchedEffect(attempt) {
        timedOut = false
        var elapsed = 0L
        while (elapsed < ceilingMs) {
            val needsSetup = try {
                ApiClient.get().onboardingStatus().needs_setup
            } catch (e: Exception) {
                true // a failed call just keeps waiting
            }
            if (!needsSetup) { onConnected(); return@LaunchedEffect }
            delay(pollMs)
            elapsed += pollMs
        }
        timedOut = true
    }

    Surface(color = Color.Transparent) {
        Column(
            Modifier.fillMaxSize().padding(28.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(tr("Connecting to your shop…"), style = MaterialTheme.typography.headlineMedium,
                color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.ExtraBold)
            Spacer(Modifier.height(10.dp))
            Text(tr("Your account is on its way from your shop's other device."),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(24.dp))

            ElevatedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(20.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                    if (!timedOut) {
                        CircularProgressIndicator()
                    } else {
                        Text(
                            tr("Still waiting for your account. Check the connection, or set up a new shop instead."),
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Spacer(Modifier.height(16.dp))
                        Button(
                            onClick = { attempt++ },
                            modifier = Modifier.fillMaxWidth().height(52.dp),
                        ) { Text(tr("Keep waiting"), style = MaterialTheme.typography.labelLarge) }
                        Spacer(Modifier.height(12.dp))
                        OutlinedButton(
                            onClick = onSetupInstead,
                            modifier = Modifier.fillMaxWidth().height(52.dp),
                        ) { Text(tr("Set up a new shop instead"), style = MaterialTheme.typography.labelLarge) }
                    }
                }
            }

            if (!timedOut) {
                Spacer(Modifier.height(16.dp))
                // One tap to undo a mis-tap on the choice screen, without
                // waiting the full two minutes out.
                TextButton(onClick = onSetupInstead) {
                    Text(tr("Set up a new shop instead"))
                }
            }
        }
    }
}
