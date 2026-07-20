@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.clinic.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.ArrowForward
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
import com.actionaura.clinic.ui.i18n.tr
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
    var showDayPicker by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val todayFmt = remember { SimpleDateFormat("yyyy-MM-dd", Locale.US) }
    val today = remember { todayFmt.format(Date()) }
    // Wave 1A follow-up (found alongside MOB-005): booking worked, but the
    // screen could only ever show *today's* schedule with no way to see
    // anything booked ahead -- a real appointment for tomorrow was
    // permanently invisible with no way to browse forward at all. Fixed by
    // switching the default view to "upcoming from a start date onward"
    // (backend `from_date`, unbounded except a 200-row safety cap) instead
    // of an exact single-day match. The date picker only moves *where the
    // window starts* -- it never narrows the view back down to one day.
    var fromDate by remember { mutableStateOf(today) }
    val fromCal = remember(fromDate) {
        java.util.Calendar.getInstance().apply { time = todayFmt.parse(fromDate) ?: Date() }
    }

    suspend fun load() {
        items = try { ApiClient.get().appointments(fromDate = fromDate).data } catch (e: Exception) { emptyList() }
    }
    LaunchedEffect(fromDate) { loading = true; load(); loading = false }

    if (showDayPicker) {
        val state = androidx.compose.material3.rememberDatePickerState(initialSelectedDateMillis = fromCal.timeInMillis)
        DatePickerDialog(
            onDismissRequest = { showDayPicker = false },
            confirmButton = {
                TextButton(onClick = {
                    state.selectedDateMillis?.let { ms ->
                        val cal = java.util.Calendar.getInstance(java.util.TimeZone.getTimeZone("UTC"))
                        cal.timeInMillis = ms
                        fromDate = todayFmt.format(cal.time)
                    }
                    showDayPicker = false
                }) { Text(tr("OK")) }
            },
            dismissButton = { TextButton(onClick = { showDayPicker = false }) { Text(tr("Cancel")) } },
        ) { DatePicker(state = state) }
    }

    Box(Modifier.fillMaxSize()) {
        androidx.compose.foundation.layout.Column(Modifier.fillMaxSize()) {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                TextButton(onClick = { showDayPicker = true }, modifier = Modifier.weight(1f)) {
                    Text(
                        if (fromDate == today) tr("Upcoming (from today)") else tr("Upcoming from") + " $fromDate",
                        style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold,
                    )
                }
                if (fromDate != today) TextButton(onClick = { fromDate = today }) { Text(tr("Today")) }
            }
        PullToRefreshBox(
            isRefreshing = refreshing,
            onRefresh = { scope.launch { refreshing = true; load(); refreshing = false } },
            modifier = Modifier.weight(1f),
        ) {
            if (loading && items.isEmpty()) {
                SkeletonList(count = 6, modifier = Modifier.fillMaxSize())
            } else if (items.isEmpty()) {
                EmptyState(
                    icon = Icons.Default.EventAvailable,
                    title = if (fromDate == today) tr("No upcoming appointments") else tr("No appointments from") + " $fromDate " + tr("onward"),
                    subtitle = tr("Booked appointments will show up here."),
                    ctaText = tr("Book"), onCta = { showBook = true },
                )
            } else {
                LazyColumn(contentPadding = PaddingValues(16.dp, 12.dp, 16.dp, 96.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    var lastDateHeader = ""
                    items.forEach { a ->
                        val dayPart = (a.appointment_dt ?: "").take(10)
                        if (dayPart.isNotEmpty() && dayPart != lastDateHeader) {
                            lastDateHeader = dayPart
                            item(key = "hdr-$dayPart") {
                                Text(
                                    if (dayPart == today) tr("Today") else dayPart,
                                    style = MaterialTheme.typography.labelLarge,
                                    color = MaterialTheme.colorScheme.primary,
                                    modifier = Modifier.padding(top = 4.dp, bottom = 2.dp),
                                )
                            }
                        }
                        item(key = a.id) {
                        ElevatedCard(Modifier.fillMaxWidth()) {
                            Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                                Avatar(a.patient_name)
                                Spacer(Modifier.width(14.dp))
                                Column(Modifier.weight(1f)) {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Text(a.patient_name ?: tr("Patient"), style = MaterialTheme.typography.titleMedium,
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
        }
        }
        ExtendedFloatingActionButton(
            onClick = { showBook = true },
            icon = { Icon(Icons.Default.Add, null) }, text = { Text(tr("Book")) },
            modifier = Modifier.align(Alignment.BottomEnd).padding(20.dp),
        )
    }

    if (showBook) {
        BookAppointmentSheet(
            onDismiss = { showBook = false },
            onBooked = { bookedDate ->
                showBook = false
                scope.launch {
                    snackbar.showSnackbar(tr("Appointment booked"))
                    if (bookedDate != null && bookedDate < fromDate) fromDate = bookedDate else load()
                }
            },
        )
    }
}

@Composable
private fun BookAppointmentSheet(onDismiss: () -> Unit, onBooked: (bookedDate: String?) -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var patients by remember { mutableStateOf<List<com.actionaura.clinic.net.Patient>>(emptyList()) }
    var doctors by remember { mutableStateOf<List<com.actionaura.clinic.net.Doctor>>(emptyList()) }
    var patientId by remember { mutableStateOf<Int?>(null) }
    var doctorId by remember { mutableStateOf<Int?>(null) }
    // MOB-005 (Wave 1A, found on a real device): this used to be a raw free-text
    // field ("YYYY-MM-DD HH:MM") with no validation. A real typed entry
    // ("2026-7-18", missing zero-padding and the time) saved successfully
    // (the backend only validates patient_id) but then never matched any
    // date(appointment_dt)=? schedule query again -- the appointment silently
    // vanished from the app while still sitting in the database. Fixed by
    // replacing free text with native date/time pickers so a malformed
    // string can never be constructed in the first place.
    var dateMillis by remember { mutableStateOf<Long?>(null) }
    var hour by remember { mutableStateOf<Int?>(null) }
    var minute by remember { mutableStateOf<Int?>(null) }
    var showDatePicker by remember { mutableStateOf(false) }
    var showTimePicker by remember { mutableStateOf(false) }
    var reason by remember { mutableStateOf("") }
    var saving by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) {
        patients = try { ApiClient.get().patients().data } catch (e: Exception) { emptyList() }
        doctors = try { ApiClient.get().doctors().data } catch (e: Exception) { emptyList() }
    }

    fun formattedDt(): String? {
        val ms = dateMillis ?: return null
        val h = hour ?: return null
        val m = minute ?: return null
        val cal = java.util.Calendar.getInstance(java.util.TimeZone.getTimeZone("UTC"))
        cal.timeInMillis = ms
        val datePart = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(cal.time)
        return "%s %02d:%02d".format(datePart, h, m)
    }

    if (showDatePicker) {
        val state = androidx.compose.material3.rememberDatePickerState(initialSelectedDateMillis = dateMillis)
        DatePickerDialog(
            onDismissRequest = { showDatePicker = false },
            confirmButton = {
                TextButton(onClick = { dateMillis = state.selectedDateMillis; showDatePicker = false }) { Text(tr("OK")) }
            },
            dismissButton = { TextButton(onClick = { showDatePicker = false }) { Text(tr("Cancel")) } },
        ) { DatePicker(state = state) }
    }
    if (showTimePicker) {
        val state = androidx.compose.material3.rememberTimePickerState(
            initialHour = hour ?: 9, initialMinute = minute ?: 0, is24Hour = true)
        AlertDialog(
            onDismissRequest = { showTimePicker = false },
            confirmButton = {
                TextButton(onClick = { hour = state.hour; minute = state.minute; showTimePicker = false }) { Text(tr("OK")) }
            },
            dismissButton = { TextButton(onClick = { showTimePicker = false }) { Text(tr("Cancel")) } },
            text = { TimePicker(state = state) },
        )
    }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        androidx.compose.foundation.layout.Column(
            Modifier.padding(20.dp).padding(bottom = 24.dp)) {
            Text(tr("Book Appointment"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(16.dp))
            LabeledDropdown(tr("Patient"), patients.map { it.id to (it.name ?: "#${it.id}") }, patientId) { patientId = it }
            Spacer(Modifier.height(12.dp))
            LabeledDropdown(tr("Doctor"), doctors.map { it.id to (it.name ?: "#${it.id}") }, doctorId) { doctorId = it }
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedButton(onClick = { showDatePicker = true }, modifier = Modifier.weight(1f)) {
                    Text(dateMillis?.let {
                        val cal = java.util.Calendar.getInstance(java.util.TimeZone.getTimeZone("UTC"))
                        cal.timeInMillis = it
                        SimpleDateFormat("yyyy-MM-dd", Locale.US).format(cal.time)
                    } ?: tr("Pick date"))
                }
                OutlinedButton(onClick = { showTimePicker = true }, modifier = Modifier.weight(1f)) {
                    Text(if (hour != null && minute != null) "%02d:%02d".format(hour, minute) else tr("Pick time"))
                }
            }
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(reason, { reason = it }, label = { Text(tr("Reason")) }, modifier = Modifier.fillMaxWidth())
            if (error != null) { Spacer(Modifier.height(10.dp)); Text(error!!, color = MaterialTheme.colorScheme.error) }
            Spacer(Modifier.height(20.dp))
            Button(onClick = {
                val dtValue = formattedDt()
                if (patientId == null || dtValue == null) { error = tr("Pick a patient, date, and time"); return@Button }
                saving = true; error = null
                scope.launch {
                    try {
                        val r = ApiClient.get().createAppointment(com.actionaura.clinic.net.CreateAppointmentRequest(
                            patient_id = patientId!!, doctor_id = doctorId, appointment_dt = dtValue, reason = reason.trim()))
                        if (r.status == "success") onBooked(dtValue.substring(0, 10)) else error = r.message ?: tr("Couldn't book")
                    } catch (e: Exception) { error = com.actionaura.clinic.net.appointmentErrorMessage(e) } finally { saving = false }
                }
            }, enabled = !saving, modifier = Modifier.fillMaxWidth().height(52.dp)) {
                if (saving) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.onPrimary)
                else Text(tr("Book"), style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}
