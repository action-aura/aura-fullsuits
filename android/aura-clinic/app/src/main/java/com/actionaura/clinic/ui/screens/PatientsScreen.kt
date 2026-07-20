@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.clinic.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.PersonAdd
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.actionaura.clinic.net.ApiClient
import com.actionaura.clinic.net.CreatePatientRequest
import com.actionaura.clinic.net.Patient
import com.actionaura.clinic.ui.components.Avatar
import com.actionaura.clinic.ui.components.EmptyState
import com.actionaura.clinic.ui.components.GlowCard
import com.actionaura.clinic.ui.components.SkeletonList
import com.actionaura.clinic.ui.i18n.tr
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PatientsScreen(onOpenPatient: (Int) -> Unit, snackbar: SnackbarHostState) {
    var query by remember { mutableStateOf("") }
    var patients by remember { mutableStateOf<List<Patient>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var refreshing by remember { mutableStateOf(false) }
    var showAdd by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    suspend fun load() {
        patients = try { ApiClient.get().patients(query.ifBlank { null }).data } catch (e: Exception) { emptyList() }
    }
    // initial + debounced search
    LaunchedEffect(query) {
        if (query.isNotBlank()) delay(350)
        loading = true; load(); loading = false
    }

    Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize()) {
            // Sticky search
            OutlinedTextField(
                value = query, onValueChange = { query = it },
                placeholder = { Text(tr("Search name, phone, code")) },
                leadingIcon = { Icon(Icons.Default.Search, null) },
                singleLine = true, shape = RoundedCornerShape(28.dp),
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp),
            )

            PullToRefreshBox(
                isRefreshing = refreshing,
                onRefresh = { scope.launch { refreshing = true; load(); refreshing = false } },
                modifier = Modifier.weight(1f),
            ) {
                if (loading && patients.isEmpty()) {
                    SkeletonList(count = 7, modifier = Modifier.fillMaxSize())
                } else if (patients.isEmpty()) {
                    EmptyState(
                        icon = Icons.Default.PersonAdd,
                        title = if (query.isBlank()) tr("No patients yet") else tr("No matches"),
                        subtitle = if (query.isBlank()) tr("Start by adding your first patient.")
                                   else tr("Try a different name, phone or code."),
                        ctaText = if (query.isBlank()) tr("Add Patient") else null,
                        onCta = if (query.isBlank()) ({ showAdd = true }) else null,
                    )
                } else {
                    LazyColumn(
                        contentPadding = PaddingValues(16.dp, 4.dp, 16.dp, 96.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        items(patients, key = { it.id }) { p ->
                            PatientCard(p, onOpen = { onOpenPatient(p.id) })
                        }
                    }
                }
            }
        }

        ExtendedFloatingActionButton(
            onClick = { showAdd = true },
            icon = { Icon(Icons.Default.Add, null) }, text = { Text(tr("Add Patient")) },
            modifier = Modifier.align(Alignment.BottomEnd).padding(20.dp),
        )
    }

    if (showAdd) {
        AddPatientSheet(
            onDismiss = { showAdd = false },
            onCreated = {
                showAdd = false
                scope.launch { snackbar.showSnackbar(tr("Patient added")); loading = true; load(); loading = false }
            },
        )
    }
}

@Composable
private fun PatientCard(p: Patient, onOpen: () -> Unit) {
    GlowCard(modifier = Modifier.fillMaxWidth(), onClick = onOpen) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Avatar(p.name)
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(p.name ?: "—", style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                    StatusChip(p.status ?: "active")
                }
                Spacer(Modifier.height(4.dp))
                Text("${p.patient_code ?: ""}   ·   ${p.phone ?: tr("no phone")}",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (!p.blood_type.isNullOrBlank() && p.blood_type != "N/A") {
                    Spacer(Modifier.height(2.dp))
                    Text(tr("Blood type") + " ${p.blood_type}", style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Icon(Icons.Default.ChevronRight, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
fun StatusChip(status: String) {
    val color = when (status.lowercase()) {
        "active", "paid", "completed" -> MaterialTheme.colorScheme.primary
        "waiting", "partial", "scheduled" -> MaterialTheme.colorScheme.tertiary
        "archived", "cancelled", "unpaid" -> MaterialTheme.colorScheme.error
        else -> MaterialTheme.colorScheme.secondary
    }
    Surface(color = color.copy(alpha = 0.14f), shape = RoundedCornerShape(20.dp)) {
        Text(tr(status), color = color, style = MaterialTheme.typography.labelMedium,
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp))
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AddPatientSheet(onDismiss: () -> Unit, onCreated: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var name by remember { mutableStateOf("") }
    var phone by remember { mutableStateOf("") }
    var gender by remember { mutableStateOf("") }
    var blood by remember { mutableStateOf("") }
    var notes by remember { mutableStateOf("") }
    var saving by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp)) {
            Text(tr("Add Patient"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(16.dp))
            OutlinedTextField(name, { name = it }, label = { Text(tr("Full name *")) },
                singleLine = true, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(phone, { phone = it }, label = { Text(tr("Phone")) },
                singleLine = true, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(gender, { gender = it }, label = { Text(tr("Gender")) },
                    singleLine = true, modifier = Modifier.weight(1f))
                OutlinedTextField(blood, { blood = it }, label = { Text(tr("Blood type")) },
                    singleLine = true, modifier = Modifier.weight(1f))
            }
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(notes, { notes = it }, label = { Text(tr("Notes (allergies, conditions)")) },
                modifier = Modifier.fillMaxWidth(), minLines = 2)
            if (error != null) {
                Spacer(Modifier.height(10.dp)); Text(error!!, color = MaterialTheme.colorScheme.error)
            }
            Spacer(Modifier.height(20.dp))
            Button(
                onClick = {
                    if (name.isBlank()) { error = tr("Name is required"); return@Button }
                    saving = true; error = null
                    scope.launch {
                        try {
                            val r = ApiClient.get().createPatient(CreatePatientRequest(
                                name = name.trim(), phone = phone.trim(), gender = gender.trim(),
                                blood_type = blood.trim(), notes = notes.trim()))
                            if (r.status == "success") onCreated() else error = r.message ?: tr("Couldn't save")
                        } catch (e: Exception) { error = tr("Couldn't reach the server") } finally { saving = false }
                    }
                },
                enabled = !saving, modifier = Modifier.fillMaxWidth().height(52.dp),
            ) {
                if (saving) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.onPrimary)
                else Text(tr("Save Patient"), style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}
