package com.actionaura.clinic.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.actionaura.clinic.net.ApiClient
import com.actionaura.clinic.net.LoginRequest
import com.actionaura.clinic.ui.components.auroraBrush
import com.actionaura.clinic.ui.i18n.tr
import com.actionaura.clinic.ui.components.pulseGlow
import com.actionaura.clinic.ui.theme.AuroraTeal
import kotlinx.coroutines.launch

@Composable
fun LoginScreen(onLoggedIn: () -> Unit) {
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    fun submit() {
        if (email.isBlank() || password.isBlank()) { error = tr("Enter email and password"); return }
        loading = true; error = null
        scope.launch {
            try {
                val r = ApiClient.get().login(LoginRequest(email.trim(), password))
                if (r.success) {
                    // Phase 4K: populate role-gating state at the moment of login,
                    // not just on cold-start session restore.
                    com.actionaura.clinic.ui.ClinicSession.update(r.user)
                    onLoggedIn()
                } else error = r.error ?: tr("Invalid credentials")
            } catch (e: Exception) {
                error = com.actionaura.clinic.net.loginErrorMessage(e)
            } finally { loading = false }
        }
    }

    Surface(color = Color.Transparent) {
        Column(
            modifier = Modifier.fillMaxSize().padding(28.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Surface(shape = CircleShape, color = MaterialTheme.colorScheme.surface,
                modifier = Modifier.size(72.dp).pulseGlow(AuroraTeal, CircleShape)) {
                Box(contentAlignment = Alignment.Center) {
                    Text("A", style = MaterialTheme.typography.headlineMedium.copy(brush = auroraBrush()),
                        fontWeight = FontWeight.ExtraBold)
                }
            }
            Spacer(Modifier.height(20.dp))
            Text("Action Aura", style = MaterialTheme.typography.headlineMedium.copy(brush = auroraBrush()),
                fontWeight = FontWeight.ExtraBold)
            Spacer(Modifier.height(6.dp))
            Text(tr("Sign in to your workspace"), style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(28.dp))

            ElevatedCard(modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(20.dp)) {
                    OutlinedTextField(
                        value = email, onValueChange = { email = it },
                        label = { Text(tr("Email")) }, singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Spacer(Modifier.height(14.dp))
                    OutlinedTextField(
                        value = password, onValueChange = { password = it },
                        label = { Text(tr("Password")) }, singleLine = true,
                        visualTransformation = PasswordVisualTransformation(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    if (error != null) {
                        Spacer(Modifier.height(12.dp))
                        Text(error!!, color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodyMedium)
                    }
                    Spacer(Modifier.height(20.dp))
                    Button(
                        onClick = { submit() }, enabled = !loading,
                        modifier = Modifier.fillMaxWidth().height(52.dp),
                    ) {
                        if (loading) CircularProgressIndicator(
                            modifier = Modifier.size(22.dp), strokeWidth = 2.dp,
                            color = MaterialTheme.colorScheme.onPrimary)
                        else Text(tr("Sign In"), style = MaterialTheme.typography.labelLarge)
                    }
                }
            }
        }
    }
}
