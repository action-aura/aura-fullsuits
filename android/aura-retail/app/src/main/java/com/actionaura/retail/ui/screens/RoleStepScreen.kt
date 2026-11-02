package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.actionaura.retail.licensing.RoleLean
import com.actionaura.retail.net.RoleSuggestion
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
 *
 * Owner 2026-11-02: which button is FILLED comes from the backend's suggestion (a phone leans boss,
 * a Windows computer leans cashier, and any device defaults to cashier when the shop's registry
 * already names another boss device -- the screen then says which). A lean, never a lock.
 */
@Composable
fun RoleStepScreen(
    onAdmin: () -> Unit,
    onCashier: () -> Unit,
    /** The backend's lean (null on any failure = the neutral old look). Loaded once, before drawing. */
    loadSuggestion: suspend () -> RoleSuggestion? = { null },
) {
    var suggestion by remember { mutableStateOf<RoleSuggestion?>(null) }
    var loaded by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        suggestion = try { loadSuggestion() } catch (e: Exception) { null }
        loaded = true
    }
    // Nothing is drawn until the lean is known, so the filled button never jumps from one to the other.
    if (!loaded) return
    val bossLeads = RoleLean.leansBoss(suggestion)
    val known = suggestion?.takeIf { it.boss_exists }
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
            if (known != null) {
                Spacer(Modifier.height(12.dp))
                val who = RoleLean.bossName(known) ?: tr(RoleLean.bossKind(known))
                Text(tr(RoleLean.BOSS_KNOWN).replace("{name}", who),
                    style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurface)
            }
            Spacer(Modifier.height(24.dp))
            ElevatedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(20.dp)) {
                    // The suggested button is the filled one, the other is outlined -- both always work.
                    RoleChoiceButton(bossLeads, onAdmin, tr("This is the boss computer (Admin)"))
                    Spacer(Modifier.height(6.dp))
                    Text(
                        tr(if (known != null) RoleLean.BOSS_NOTE_WHEN_KNOWN
                           else "The boss computer sets up the shop, the staff and the settings. Every shop needs one."),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.height(16.dp))
                    RoleChoiceButton(!bossLeads, onCashier, tr("This is a cashier computer"))
                    Spacer(Modifier.height(6.dp))
                    Text(tr("A cashier computer sells at the counter. It gets its staff from the boss computer."),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

@Composable
private fun RoleChoiceButton(filled: Boolean, onClick: () -> Unit, label: String) {
    val mod = Modifier.fillMaxWidth().height(52.dp)
    if (filled) Button(onClick = onClick, modifier = mod) { Text(label, style = MaterialTheme.typography.labelLarge) }
    else OutlinedButton(onClick = onClick, modifier = mod) { Text(label, style = MaterialTheme.typography.labelLarge) }
}
