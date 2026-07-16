@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.clinic.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.EventAvailable
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.actionaura.clinic.net.ApiClient
import com.actionaura.clinic.net.Appointment
import com.actionaura.clinic.ui.components.Avatar
import com.actionaura.clinic.ui.components.EmptyState
import com.actionaura.clinic.ui.components.SkeletonList
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.*

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppointmentsScreen(snackbar: SnackbarHostState) {
    var items by remember { mutableStateOf<List<Appointment>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var refreshing by remember { mutableStateOf(false) }
    var showBook by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val today = remember { SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date()) }

    suspend fun load() {
        items = try { ApiClient.get().appointments(date = today).data } catch (e: Exception) { emptyList() }
    }
    LaunchedEffect(Unit) { loading = true; load(); loading = false }

    Box(Modifier.fillMaxSize()) {
        PullToRefreshBox(
            isRefreshing = refreshing,
            onRefresh = { scope.launch { refreshing = true; load(); refreshing = false } },
            modifier = Modifier.fillMaxSize(),
        ) {
            if (loading && items.isEmpty()) {
                SkeletonList(count = 6, modifier = Modifier.fillMaxSize())
            } else if (items.isEmpty()) {
                EmptyState(
                    icon = Icons.Default.EventAvailable,
                    title = "No appointments today",
                    subtitle = "Booked appointments for today will show up here.",
                    ctaText = "Book", onCta = { showBook = true },
                )
            } else {
                LazyColumn(contentPadding = PaddingValues(16.dp, 12.dp, 16.dp, 96.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    items(items, key = { it.id }) { a ->
                        ElevatedCard(Modifier.fillMaxWidth()) {
                            Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                                Avatar(a.patient_name)
                                Spacer(Modifier.width(14.dp))
                                Column(Modifier.weight(1f)) {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Text(a.patient_name ?: "Patient", style = MaterialTheme.typography.titleMedium,
                                            fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                                        StatusChip(a.status ?: "scheduled")
                                    }
                                    Spacer(Modifier.height(4.dp))
                                    Text("${a.appointment_dt ?: ""}   ·   ${a.reason ?: ""}",
                                        style = MaterialTheme.typography.bodyMedium,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                        }
                    }
                }
            }
        }
        ExtendedFloatingActionButton(
            onClick = { showBook = true },
            icon = { Icon(Icons.Default.Add, null) }, text = { Text("Book") },
            modifier = Modifier.align(Alignment.BottomEnd).padding(20.dp),
        )
    }

    if (showBook) {
        BookAppointmentSheet(
            onDismiss = { showBook = false },
            onBooked = { showBook = false; scope.launch { snackbar.showSnackbar("Appointment booked"); load() } },
        )
    }
}

@Composable
private fun BookAppointmentSheet(onDismiss: () -> Unit, onBooked: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var patients by remember { mutableStateOf<List<com.actionaura.clinic.net.Patient>>(emptyList()) }
    var doctors by remember { mutableStateOf<List<com.actionaura.clinic.net.Doctor>>(emptyList()) }
    var patientId by remember { mutableStateOf<Int?>(null) }
    var doctorId by remember { mutableStateOf<Int?>(null) }
    var dt by remember { mutableStateOf("") }
    var reason by remember { mutableStateOf("") }
    var saving by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) {
        patients = try { ApiClient.get().patients().data } catch (e: Exception) { emptyList() }
        doctors = try { ApiClient.get().doctors().data } catch (e: Exception) { emptyList() }
    }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        androidx.compose.foundation.layout.Column(
            Modifier.padding(20.dp).padding(bottom = 24.dp)) {
            Text("Book Appointment", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(16.dp))
            LabeledDropdown("Patient", patients.map { it.id to (it.name ?: "#${it.id}") }, patientId) { patientId = it }
            Spacer(Modifier.height(12.dp))
            LabeledDropdown("Doctor", doctors.map { it.id to (it.name ?: "#${it.id}") }, doctorId) { doctorId = it }
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(dt, { dt = it }, label = { Text("Date & time (YYYY-MM-DD HH:MM)") },
                singleLine = true, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(reason, { reason = it }, label = { Text("Reason") }, modifier = Modifier.fillMaxWidth())
            if (error != null) { Spacer(Modifier.height(10.dp)); Text(error!!, color = MaterialTheme.colorScheme.error) }
            Spacer(Modifier.height(20.dp))
            Button(onClick = {
                if (patientId == null || dt.isBlank()) { error = "Pick a patient and enter date/time"; return@Button }
                saving = true; error = null
                scope.launch {
                    try {
                        val r = ApiClient.get().createAppointment(com.actionaura.clinic.net.CreateAppointmentRequest(
                            patient_id = patientId!!, doctor_id = doctorId, appointment_dt = dt.trim(), reason = reason.trim()))
                        if (r.status == "success") onBooked() else error = r.message ?: "Couldn't book"
                    } catch (e: Exception) { error = "Couldn't reach the server" } finally { saving = false }
                }
            }, enabled = !saving, modifier = Modifier.fillMaxWidth().height(52.dp)) {
                if (saving) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.onPrimary)
                else Text("Book", style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}
