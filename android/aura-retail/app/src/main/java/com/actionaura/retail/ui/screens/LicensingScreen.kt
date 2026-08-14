@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.VerifiedUser
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.actionaura.retail.licensing.LicensingCoordinator
import com.actionaura.retail.ui.i18n.tr
import kotlinx.coroutines.launch

/**
 * Activation/status UI (Phase 7 Part G, Android). Talks only to
 * [LicensingCoordinator] -- never to DeviceIdentity/OwnerClient directly,
 * and never assumes an outcome beyond what the coordinator's return value
 * (itself only ever the embedded Python backend's own verified opinion)
 * says. Mirrors products/clinic/frontend/licensing.js's state labels and
 * reason-code messages so the activation experience reads the same on
 * Windows and Android.
 */

private val REASON_MESSAGES = mapOf(
    "INVALID_REQUEST" to "Please check the information entered.",
    "ACTIVATION_REJECTED" to "This license key could not be activated. Double-check the key and try again, or contact support.",
    "PRODUCT_MISMATCH" to "This license key is not valid for Aura Retail.",
    "PLATFORM_NOT_ALLOWED" to "This license key is not valid for an Android installation.",
    "DEVICE_LIMIT_REACHED" to "This license has reached its device limit. Deactivate another device or contact support to add capacity.",
    "RATE_LIMITED" to "Too many attempts. Please wait a moment and try again.",
    "NETWORK_UNAVAILABLE" to "Could not reach the licensing service. Check your internet connection and try again.",
    "REQUEST_TIMED_OUT" to "The request timed out. Please try again.",
    "TLS_VERIFICATION_FAILED" to "A secure connection to the licensing service could not be established.",
    "SERVICE_TEMPORARILY_UNAVAILABLE" to "The licensing service is temporarily unavailable. Please try again shortly.",
    "SIGNING_KEY_UNAVAILABLE" to "The licensing service is temporarily unavailable. Please try again shortly.",
    "MALFORMED_RESPONSE" to "Received an unexpected response from the licensing service. Please try again.",
    "DEVICE_KEY_UNAVAILABLE" to "This device is not yet set up for activation. Please try again.",
)

private fun stateLabel(state: String): Pair<String, Color> = when (state) {
    "NOT_CONFIGURED" -> "Not configured" to Color(0xFF666666)
    "ACTIVATION_REQUIRED" -> "Activation required" to Color(0xFF666666)
    "ACTIVATING" -> "Activating…" to Color(0xFF666666)
    "ACTIVE_ONLINE" -> "Active" to Color(0xFF1A7A3D)
    "ACTIVE_OFFLINE" -> "Active (offline)" to Color(0xFF1A7A3D)
    "WARNING" -> "Check-in needed soon" to Color(0xFF8A6100)
    "GRACE_PERIOD" -> "Offline grace period" to Color(0xFF8A6100)
    "RESTRICTED" -> "Restricted" to Color(0xFFA3231F)
    "SUSPENDED" -> "Suspended" to Color(0xFFA3231F)
    "REVOKED" -> "Revoked" to Color(0xFFA3231F)
    "EXPIRED" -> "Expired" to Color(0xFFA3231F)
    "DEVICE_DEACTIVATED" -> "Device deactivated" to Color(0xFF666666)
    "CLOCK_REVIEW_REQUIRED" -> "Clock review required" to Color(0xFF8A6100)
    "LOCAL_STATE_CORRUPT" -> "Local state needs reset" to Color(0xFFA3231F)
    else -> state to Color(0xFF666666)
}

@Composable
fun LicensingScreen(onBack: () -> Unit, snackbar: SnackbarHostState, onActivated: (() -> Unit)? = null) {
    val ctx = LocalContext.current
    val coordinator = remember { LicensingCoordinator(ctx) }
    val scope = rememberCoroutineScope()

    var status by remember { mutableStateOf<Map<String, Any?>>(mapOf("current_state" to "NOT_CONFIGURED")) }
    var loading by remember { mutableStateOf(true) }
    var busy by remember { mutableStateOf(false) }
    var licenseKeyInput by remember { mutableStateOf("") }
    var errorMessage by remember { mutableStateOf<String?>(null) }
    var infoMessage by remember { mutableStateOf<String?>(null) }
    var confirmDeactivate by remember { mutableStateOf(false) }

    suspend fun refresh() {
        status = try { coordinator.status() } catch (e: Exception) { mapOf("current_state" to "NOT_CONFIGURED") }
    }
    LaunchedEffect(Unit) { loading = true; refresh(); loading = false }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(tr("Licensing")) },
                navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.Default.ArrowBack, null) } },
            )
        },
    ) { padding ->
        Column(
            Modifier.padding(padding).fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            if (loading) {
                Box(Modifier.fillMaxWidth().padding(32.dp), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
                return@Column
            }

            errorMessage?.let { MessageBanner(it, isError = true) }
            infoMessage?.let { MessageBanner(it, isError = false) }

            val state = status["current_state"] as? String ?: "NOT_CONFIGURED"
            val (label, color) = stateLabel(state)
            AssistChip(onClick = {}, enabled = false, label = { Text(label) },
                leadingIcon = { Icon(Icons.Default.VerifiedUser, null, tint = color) })

            if (state == "NOT_CONFIGURED" || state == "ACTIVATION_REQUIRED") {
                Text(
                    if (state == "NOT_CONFIGURED")
                        tr("This installation is not yet connected to a licensing server. Owner licensing is not configured for this build.")
                    else tr("Enter your Aura Retail license key to activate this installation."),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                OutlinedTextField(
                    value = licenseKeyInput, onValueChange = { licenseKeyInput = it },
                    label = { Text(tr("License key")) },
                    placeholder = { Text("AURA-RETAIL-XXXX-YYYY-ZZZZ") },
                    singleLine = true, enabled = !busy,
                    modifier = Modifier.fillMaxWidth(),
                )
                Button(
                    enabled = !busy && licenseKeyInput.isNotBlank(),
                    onClick = {
                        scope.launch {
                            errorMessage = null; infoMessage = null; busy = true
                            val key = licenseKeyInput.trim()
                            licenseKeyInput = ""
                            val result = try { coordinator.activate(key) } catch (e: Exception) {
                                mapOf("reason_code" to "NETWORK_UNAVAILABLE")
                            }
                            if (result["result"] == "SUCCESS") {
                                infoMessage = tr("Activation successful.")
                                // Pre-login gate usage (AppRoot's Phase.LICENSE) passes this
                                // to advance to the login screen; the settings-accessed path
                                // (already logged in) passes null and just stays here showing
                                // the now-active status, same as before this param existed.
                                onActivated?.invoke()
                            } else if (result["result"] == "PENDING") {
                                // Phase 8 Part O: Owner is holding this activation for
                                // manual approval, not rejecting it -- a distinct,
                                // non-error state. Not treated as failure.
                                infoMessage = tr("This activation is awaiting manual approval. We'll keep checking automatically -- no action needed right now.")
                            } else {
                                val reason = result["reason_code"] as? String ?: "ACTIVATION_REJECTED"
                                errorMessage = REASON_MESSAGES[reason]?.let { tr(it) } ?: tr(REASON_MESSAGES.getValue("ACTIVATION_REJECTED"))
                            }
                            refresh()
                            busy = false
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
                    else Text(tr("Activate"))
                }
                return@Column
            }

            // Active/restricted/etc. -- status + check-in/deactivate actions.
            ElevatedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    StatusRow(tr("Product"), status["product_code"] as? String ?: "—")
                    (status["installation_id"] as? String)?.let { StatusRow(tr("Installation"), it) }
                    (status["license_status"] as? String)?.let { StatusRow(tr("License status"), it) }
                    (status["last_successful_checkin_at"] as? String)?.let { StatusRow(tr("Last check-in"), it) }
                }
            }

            if (state in setOf("RESTRICTED", "GRACE_PERIOD", "WARNING")) {
                Text(
                    tr("Some features are limited in this state. Existing records remain fully viewable, and backup/restore/export remain available."),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (state in setOf("SUSPENDED", "REVOKED", "EXPIRED")) {
                Text(
                    tr("Commercial features are unavailable. Your existing data is safe and remains viewable; backup, restore, and export remain available."),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedButton(
                    enabled = !busy,
                    onClick = {
                        scope.launch {
                            errorMessage = null; infoMessage = null; busy = true
                            val result = try { coordinator.checkIn() } catch (e: Exception) {
                                mapOf<String, Any?>("last_attempt_reached_owner" to false)
                            }
                            infoMessage = if (result["last_attempt_reached_owner"] == true) tr("Check-in complete.") else null
                            if (result["last_attempt_reached_owner"] != true) {
                                errorMessage = tr("Could not reach the licensing service. Your current status is unchanged.")
                            }
                            refresh()
                            busy = false
                        }
                    },
                ) { Text(tr("Check Now")) }

                if (state != "DEVICE_DEACTIVATED") {
                    OutlinedButton(
                        enabled = !busy,
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
                        onClick = { confirmDeactivate = true },
                    ) { Text(tr("Deactivate This Device")) }
                }
            }
        }
    }

    if (confirmDeactivate) {
        AlertDialog(
            onDismissRequest = { confirmDeactivate = false },
            title = { Text(tr("Deactivate this device?")) },
            text = { Text(tr("You will need to reactivate with a license key to use commercial features again.")) },
            confirmButton = {
                TextButton(onClick = {
                    confirmDeactivate = false
                    scope.launch {
                        errorMessage = null; infoMessage = null; busy = true
                        val result = try { coordinator.deactivate() } catch (e: Exception) {
                            mapOf("reason_code" to "NETWORK_UNAVAILABLE")
                        }
                        if (result["result"] == "SUCCESS") infoMessage = tr("This device has been deactivated.")
                        else errorMessage = REASON_MESSAGES[result["reason_code"] as? String]?.let { tr(it) }
                            ?: tr(REASON_MESSAGES.getValue("ACTIVATION_REJECTED"))
                        refresh()
                        busy = false
                    }
                }) { Text(tr("Deactivate")) }
            },
            dismissButton = { TextButton(onClick = { confirmDeactivate = false }) { Text(tr("Cancel")) } },
        )
    }
}

@Composable
private fun StatusRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, fontWeight = FontWeight.Medium)
    }
}

@Composable
private fun MessageBanner(text: String, isError: Boolean) {
    val bg = if (isError) Color(0xFFFDE2E2) else Color(0xFFE0EDFF)
    val fg = if (isError) Color(0xFFA3231F) else Color(0xFF1E429F)
    Surface(color = bg, contentColor = fg, shape = MaterialTheme.shapes.small) {
        Text(text, Modifier.padding(12.dp))
    }
}
