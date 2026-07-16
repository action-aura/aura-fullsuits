package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.ApiClient
import com.actionaura.retail.net.CreateAdminRequest
import com.actionaura.retail.ui.i18n.tr
import kotlinx.coroutines.launch

/** First-run: create the administrator account (fresh install has no users). */
@Composable
fun SetupScreen(onDone: () -> Unit) {
    var name by remember { mutableStateOf("") }
    var company by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    fun submit() {
        if (email.isBlank() || password.length < 6) { error = tr("Email and a 6+ char password required"); return }
        loading = true; error = null
        scope.launch {
            try {
                val r = ApiClient.get().createAdmin(
                    CreateAdminRequest(name.trim(), email.trim(), password, company.trim()))
                if (r.success) onDone() else error = r.error ?: tr("Couldn't create account")
            } catch (e: Exception) {
                error = tr("Couldn't reach the server. Try again.")
            } finally { loading = false }
        }
    }

    Surface(color = androidx.compose.ui.graphics.Color.Transparent) {
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(28.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Spacer(Modifier.height(40.dp))
            Text(tr("Welcome to Action Aura"), style = MaterialTheme.typography.headlineMedium,
                color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.ExtraBold)
            Spacer(Modifier.height(6.dp))
            Text(tr("Create your administrator account"), style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(24.dp))

            ElevatedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(20.dp)) {
                    OutlinedTextField(name, { name = it }, label = { Text(tr("Your name")) },
                        singleLine = true, modifier = Modifier.fillMaxWidth())
                    Spacer(Modifier.height(12.dp))
                    OutlinedTextField(company, { company = it },
                        label = { Text(tr("Business / store name")) },
                        singleLine = true, modifier = Modifier.fillMaxWidth())
                    Spacer(Modifier.height(12.dp))
                    OutlinedTextField(email, { email = it }, label = { Text(tr("Email")) }, singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
                        modifier = Modifier.fillMaxWidth())
                    Spacer(Modifier.height(12.dp))
                    OutlinedTextField(password, { password = it }, label = { Text(tr("Password")) }, singleLine = true,
                        visualTransformation = PasswordVisualTransformation(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                        modifier = Modifier.fillMaxWidth())
                    if (error != null) {
                        Spacer(Modifier.height(12.dp))
                        Text(error!!, color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodyMedium)
                    }
                    Spacer(Modifier.height(20.dp))
                    Button(onClick = { submit() }, enabled = !loading,
                        modifier = Modifier.fillMaxWidth().height(52.dp)) {
                        if (loading) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp,
                            color = MaterialTheme.colorScheme.onPrimary)
                        else Text(tr("Create Account"), style = MaterialTheme.typography.labelLarge)
                    }
                }
            }
        }
    }
}
