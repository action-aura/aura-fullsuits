package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.actionaura.retail.ui.i18n.tr

/**
 * "Which computer is this?" -- asked ONCE, right after this phone was activated
 * (new customer flow, plan S6). The desktop twin is app-shell.js's _showRoleStep.
 *
 *   boss computer (Admin) -> the ordinary create-account screen, e-mail prefilled
 *                            with the Aura account's, and a SEPARATE local password
 *   cashier computer      -> the existing "join a shop" wait: this phone receives
 *                            the shop's staff accounts from the boss computer
 *
 * Whether a PHONE may be the boss computer is decided by the backend, not here:
 * only in a shop with no Windows computer (device_routes._platform_can_be_main).
 */
@Composable
fun RoleStepScreen(onAdmin: () -> Unit, onCashier: () -> Unit) {
    Surface(color = Color.Transparent) {
        Column(
            Modifier.fillMaxSize().padding(28.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(tr("Which computer is this?"), style = MaterialTheme.typography.headlineMedium,
                color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.ExtraBold)
            Spacer(Modifier.height(10.dp))
            Text(tr("Choose once. You can add more computers later."),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(24.dp))
            ElevatedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(20.dp)) {
                    Button(onClick = onAdmin, modifier = Modifier.fillMaxWidth().height(52.dp)) {
                        Text(tr("This is the boss computer (Admin)"), style = MaterialTheme.typography.labelLarge)
                    }
                    Spacer(Modifier.height(6.dp))
                    Text(tr("The boss computer sets up the shop, the staff and the settings. Every shop needs one."),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.height(16.dp))
                    OutlinedButton(onClick = onCashier, modifier = Modifier.fillMaxWidth().height(52.dp)) {
                        Text(tr("This is a cashier computer"), style = MaterialTheme.typography.labelLarge)
                    }
                    Spacer(Modifier.height(6.dp))
                    Text(tr("A cashier computer sells at the counter. It gets its staff from the boss computer."),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}
