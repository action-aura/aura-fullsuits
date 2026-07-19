package com.actionaura.clinic.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.actionaura.clinic.net.*
import kotlinx.coroutines.launch

@Composable
fun PatientDetailScreen(patientId: Int) {
    // Phase 4M: patient name/notes/visits/prescriptions/invoices are all
    // rendered on this one screen -- excluded from screenshots and the
    // recent-apps thumbnail while visible. See
    // docs/mobile/wave1a/clinic-screen-protection-decision.md.
    com.actionaura.clinic.ui.components.SecureScreen()
    var detail by remember { mutableStateOf<PatientDetail?>(null) }
    var prescriptions by remember { mutableStateOf<List<Prescription>>(emptyList()) }
    var invoices by remember { mutableStateOf<List<Invoice>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }

    LaunchedEffect(patientId) {
        loading = true
        try {
            detail = ApiClient.get().patientDetail(patientId).data
            prescriptions = try { ApiClient.get().prescriptions(patientId).data } catch (e: Exception) { emptyList() }
            invoices = try { ApiClient.get().invoices().data.filter { it.patient_id == patientId } } catch (e: Exception) { emptyList() }
        } catch (e: Exception) { /* keep nulls */ }
        loading = false
    }

    if (loading) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
        return
    }
    val p = detail?.patient
    if (p == null) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text("Patient not found") }
        return
    }

    val tabs = listOf("Overview", "Visits", "Prescriptions", "Invoices", "Notes")
    val pager = rememberPagerState(pageCount = { tabs.size })
    val scope = rememberCoroutineScope()

    Column(Modifier.fillMaxSize()) {
        // Header
        Column(Modifier.fillMaxWidth().padding(16.dp)) {
            Text(p.name ?: "—", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(4.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("${p.patient_code ?: ""}   ·   ${p.phone ?: "no phone"}",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
                StatusChip(p.status ?: "active")
            }
        }

        ScrollableTabRow(selectedTabIndex = pager.currentPage, edgePadding = 12.dp) {
            tabs.forEachIndexed { i, t ->
                Tab(selected = pager.currentPage == i,
                    onClick = { scope.launch { pager.animateScrollToPage(i) } },
                    text = { Text(t) })
            }
        }

        HorizontalPager(state = pager, modifier = Modifier.weight(1f)) { page ->
            when (page) {
                0 -> OverviewTab(p)
                1 -> VisitsTab(detail?.visits ?: emptyList())
                2 -> PrescriptionsTab(prescriptions)
                3 -> InvoicesTab(invoices)
                else -> NotesTab(p)
            }
        }
    }
}

@Composable private fun OverviewTab(p: Patient) {
    LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item { InfoRow("Gender", p.gender) }
        item { InfoRow("Date of birth", p.dob) }
        item { InfoRow("Blood type", p.blood_type) }
        item { InfoRow("Email", p.email) }
        item { InfoRow("Status", p.status) }
    }
}

@Composable private fun VisitsTab(visits: List<Visit>) {
    if (visits.isEmpty()) { EmptyTab("No visits yet"); return }
    LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        items(visits, key = { it.id }) { v ->
            ElevatedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(14.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("Visit #${v.id}", fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                        StatusChip(v.status ?: "")
                    }
                    Spacer(Modifier.height(4.dp))
                    Text(v.created_at?.take(10) ?: "—", style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (!v.diagnosis.isNullOrBlank()) Text("Dx: ${v.diagnosis}", style = MaterialTheme.typography.bodyMedium)
                }
            }
        }
    }
}

@Composable private fun PrescriptionsTab(rx: List<Prescription>) {
    if (rx.isEmpty()) { EmptyTab("No prescriptions"); return }
    LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        items(rx, key = { it.id }) { r ->
            ElevatedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(14.dp)) {
                    Text("Rx #${r.id}", fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(4.dp))
                    Text(r.notes ?: (r.items_json ?: "—"), style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(r.created_at?.take(10) ?: "", style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

@Composable private fun InvoicesTab(inv: List<Invoice>) {
    if (inv.isEmpty()) { EmptyTab("No invoices"); return }
    LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        items(inv, key = { it.id }) { i ->
            ElevatedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(14.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(i.invoice_number ?: "Invoice", fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                        StatusChip(i.status ?: "")
                    }
                    Spacer(Modifier.height(4.dp))
                    Text("Total $%.2f   ·   Paid $%.2f".format(i.total, i.amount_paid),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

@Composable private fun NotesTab(p: Patient) {
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        if (p.notes.isNullOrBlank()) EmptyTab("No notes")
        else ElevatedCard(Modifier.fillMaxWidth()) { Text(p.notes, Modifier.padding(16.dp)) }
    }
}

@Composable private fun InfoRow(label: String, value: String?) {
    Row(Modifier.fillMaxWidth()) {
        Text(label, modifier = Modifier.weight(1f), color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(if (value.isNullOrBlank()) "—" else value, fontWeight = FontWeight.Medium)
    }
}

@Composable private fun EmptyTab(msg: String) {
    Box(Modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) {
        Text(msg, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
